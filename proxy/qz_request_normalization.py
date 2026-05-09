#!/usr/bin/env python3
import re

try:
    from .qz_prompt_policy import assemble_instruction_stack
    from .qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from .qz_tool_lifecycle import ToolHistoryReplayFilter
except ImportError:
    from qz_prompt_policy import assemble_instruction_stack
    from qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from qz_tool_lifecycle import ToolHistoryReplayFilter


CHECKPOINT_MARKER = "CONTEXT CHECKPOINT COMPACTION"

HARNESS_TEXT_MARKERS = (
    "<permissions instructions>",
    "<collaboration_mode>",
    "<skills_instructions>",
    "<environment_context>",
    "you are qwen3.6turbo running locally through the codex cli",
)

META_USER_TEXT_MARKERS = (
    "can you show me your system prompt",
)

META_ASSISTANT_TEXT_MARKERS = (
    "system prompt",
    "the proxy's source code",
    "the recursion is indeed funny",
)


def clean_content(text: str) -> str:
    if not isinstance(text, str):
        return text

    text = text.replace("\r\n", "\n")

    text = re.sub(r"^\s*</think>\s*", "", text)
    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    text = text.replace("<think>", "").replace("</think>", "")

    scratch_markers = (
        "Self-Correction",
        "Verification during thought",
        "Output Generation",
        "Final Output Generation",
        "matches the draft",
        "Check constraint",
        "All constraints met",
        "Output matches",
        "Proceed",
        "Ready.",
        "✅",
    )

    has_scratch = any(m in text for m in scratch_markers)
    numbered_starts = list(re.finditer(r"(?m)^\s*1\.\s+", text))

    if numbered_starts:
        if has_scratch or len(numbered_starts) >= 2:
            text = text[numbered_starts[-1].start():]

    if any(m in text for m in scratch_markers):
        useful = re.search(r"(?m)^\s*(?:1\.|- |\* |### |## )", text)
        if useful:
            text = text[useful.start():]

    text = re.sub(r"(?im)^\s*\*\(Done\.\)\*\s*$", "", text)
    text = re.sub(r"(?im)^\s*\(Done\.\)\s*$", "", text)

    return text.strip()


def recursive_clean(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("content", "text", "output_text") and isinstance(v, str):
                out[k] = clean_content(v)
            else:
                out[k] = recursive_clean(v)
        return out
    if isinstance(obj, list):
        return [recursive_clean(x) for x in obj]
    return obj


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("text", "content", "output", "arguments", "result"):
            value = item.get(key)
            if isinstance(value, str):
                parts.append(value)
                break
    return "\n".join(parts).strip()


def is_local_checkpoint_prompt(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") != "message":
        return False
    text = content_to_text(item.get("content"))
    return CHECKPOINT_MARKER in (text or "")


def _extract_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                elif isinstance(item.get("refusal"), str):
                    parts.append(item["refusal"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _looks_like_meta(role, text):
    lower = (text or "").strip().lower()
    if not lower:
        return False
    if any(marker in lower for marker in HARNESS_TEXT_MARKERS):
        return True
    if role == "user" and any(marker in lower for marker in META_USER_TEXT_MARKERS):
        return True
    if role == "assistant" and any(marker in lower for marker in META_ASSISTANT_TEXT_MARKERS):
        return True
    return False


def _canonicalize_message(item):
    role = item.get("role") or "user"
    content = item.get("content")
    content_items = content if isinstance(content, list) else [content]
    parts = []

    if role == "assistant":
        for part in content_items:
            if isinstance(part, str):
                text = part.strip()
                if text:
                    parts.append({"type": "output_text", "text": text, "annotations": []})
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "refusal":
                refusal = part.get("refusal") or part.get("text") or part.get("content")
                if isinstance(refusal, str) and refusal.strip():
                    parts.append({"type": "refusal", "refusal": refusal.strip()})
                continue
            text = part.get("text")
            if not isinstance(text, str):
                text = part.get("content") if isinstance(part.get("content"), str) else None
            if isinstance(text, str) and text.strip():
                parts.append({"type": "output_text", "text": text.strip(), "annotations": []})
        if not parts:
            text = _extract_text(content)
            if text.strip():
                parts.append({"type": "output_text", "text": text.strip(), "annotations": []})
        return {"type": "message", "role": "assistant", "content": parts}

    for part in content_items:
        if isinstance(part, str):
            text = part.strip()
            if text:
                parts.append({"type": "input_text", "text": text})
            continue
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if not isinstance(text, str):
            if isinstance(part.get("content"), str):
                text = part.get("content")
            elif isinstance(part.get("refusal"), str):
                text = part.get("refusal")
        if isinstance(text, str) and text.strip():
            parts.append({"type": "input_text", "text": text.strip()})
    if not parts:
        text = _extract_text(content)
        if text.strip():
            parts.append({"type": "input_text", "text": text.strip()})
    return {"type": "message", "role": role, "content": parts}


def normalize_responses_input_for_qwen(body: dict, selected_model: dict | None = None) -> dict:
    """
    Canonicalize replayed Codex Responses history for the local llama.cpp/Qwen bridge.

    Key rules:
    - assistant messages must use output_text/refusal parts
    - user/developer/system messages use input_text parts
    - replayed reasoning items are dropped instead of being merged into instructions
    - old harness/meta blocks are discarded because the current request already carries
      the active Codex harness in body["instructions"]
    """
    input_items = body.get("input")
    if not isinstance(input_items, list):
        return body

    clean_input = []
    tool_history_filter = ToolHistoryReplayFilter()
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    fallback_instructions = []

    for item in input_items:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                clean_input.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
            continue

        item_type = item.get("type")
        role = item.get("role")
        item_text = _extract_text(item.get("content"))

        if item_type in ("reasoning", "web_search_call"):
            continue

        adapted_tool_item = DEFAULT_TOOL_REGISTRY.input_to_upstream(item)
        if adapted_tool_item is not None:
            clean_input.append(adapted_tool_item)
            continue

        if tool_history_filter.should_drop(item):
            continue

        if is_local_checkpoint_prompt(item):
            continue

        if role in ("system", "developer"):
            if item_text.strip():
                fallback_instructions.append(item_text.strip())
            continue

        if _looks_like_meta(role, item_text):
            continue

        if item_type == "message" or role in ("user", "assistant", "tool"):
            clean_input.append(_canonicalize_message(item))
            continue

        clean_input.append(item)

    assembled_instructions, prompt_policy_report = assemble_instruction_stack(
        existing_instructions=body.get("instructions"),
        client_blocks=fallback_instructions,
        selected_model=selected_model,
    )
    if assembled_instructions:
        body["instructions"] = assembled_instructions

    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["qz_prompt_policy"] = prompt_policy_report
    body["metadata"] = metadata

    body["input"] = clean_input
    return body
