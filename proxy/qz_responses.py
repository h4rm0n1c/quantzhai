#!/usr/bin/env python3
import base64
import json
import re

try:
    from .qz_tool_apply_patch import (
        APPLY_PATCH_TOOL_ADAPTER,
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from .qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from .qz_runtime_io import capture_enabled, capture_path, write_capture
    from .qz_tools import ToolRegistry
except ImportError:
    from qz_tool_apply_patch import (
        APPLY_PATCH_TOOL_ADAPTER,
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from qz_runtime_io import capture_enabled, capture_path, write_capture
    from qz_tools import ToolRegistry

LOCAL_COMPACTION_PREFIX = "localcmp:v1:"
COMPACTION_CONFIG = {
    "keep_recent_items": 8,
    "min_preserve_items": 4,
    "max_summary_chars": 12000,
    "max_tool_output_chars": 600,
    "max_item_summary_chars": 500,
    "max_compaction_depth": 6,
    "target_output_tokens": 10000,
}

FUNCTION_CALL_TYPES = {"function_call", "computer_call", "code_interpreter_call", "apply_patch_call", "custom_tool_call"}
FUNCTION_OUTPUT_TYPES = {
    "function_call_output",
    "computer_call_output",
    "apply_patch_call_output",
    "custom_tool_call_output",
    "tool_result",
    "tool_output",
}
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

TOOL_REGISTRY = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER, WEB_SEARCH_TOOL_ADAPTER))

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


def normalize_responses_input_for_qwen(body: dict) -> dict:
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
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    have_base_instructions = bool(metadata.get("qz_upstream_instructions_present"))
    fallback_instructions = []

    def extract_text(content):
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

    def looks_like_meta(role, text):
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

    def canonicalize_message(item):
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
                text = extract_text(content)
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
            text = extract_text(content)
            if text.strip():
                parts.append({"type": "input_text", "text": text.strip()})
        return {"type": "message", "role": role, "content": parts}

    for item in input_items:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                clean_input.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
            continue

        item_type = item.get("type")
        role = item.get("role")
        item_text = extract_text(item.get("content"))

        if item_type in ("reasoning", "web_search_call"):
            continue

        adapted_tool_item = APPLY_PATCH_TOOL_ADAPTER.input_to_upstream(item)
        if adapted_tool_item is not None:
            clean_input.append(adapted_tool_item)
            continue

        if _is_local_checkpoint_prompt(item):
            continue

        if role in ("system", "developer"):
            if item_text.strip():
                fallback_instructions.append(item_text.strip())
            continue

        if looks_like_meta(role, item_text):
            continue

        if item_type == "message" or role in ("user", "assistant", "tool"):
            clean_input.append(canonicalize_message(item))
            continue

        clean_input.append(item)

    if fallback_instructions:
        existing = body.get("instructions")
        merged = []
        if isinstance(existing, str) and existing.strip():
            merged.append(existing.strip())
        merged.extend(fallback_instructions)
        body["instructions"] = "\n\n".join(merged)

    body["input"] = clean_input
    return body

def normalize_tools_for_llamacpp(body: dict) -> dict:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body

    clean = []
    dropped = []
    translated = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        tool_type = tool.get("type")
        tool_name = tool.get("name") or tool.get("server_label") or tool_type

        if tool_type == "function":
            clean.append(tool)
            continue

        tool_adapter = TOOL_REGISTRY.adapter_for_tool(tool)
        if tool_adapter:
            clean.append(tool_adapter.to_upstream_tool(tool))
            translated.append(tool_adapter.upstream_name)
            continue

        dropped.append(str(tool_name))

    body["tools"] = clean

    if capture_enabled():
        try:
            notes = []
            if translated:
                notes.append("translated: " + ", ".join(translated))
            if dropped:
                notes.append("dropped: " + ", ".join(dropped))
            capture_path("latest-dropped-tools.txt").write_text(
                "\n".join(notes) + ("\n" if notes else ""),
                encoding="utf-8"
            )
            write_capture("latest-forwarded.json", body)
        except Exception:
            pass

    if isinstance(body.get("tool_choice"), dict):
        tool_choice_type = body["tool_choice"].get("type")
        adapted_choice = TOOL_REGISTRY.normalize_tool_choice(body["tool_choice"])
        if adapted_choice is not None:
            body["tool_choice"] = adapted_choice
        elif tool_choice_type not in (None, "function"):
            body["tool_choice"] = "auto"

    return body


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


def extract_response_output_text(out: dict) -> str:
    texts = []
    for item in out.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts).strip()


def _now_ts() -> int:
    import time
    return int(time.time())


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _approx_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _content_to_text(content) -> str:
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


def _decode_local_compaction_blob(blob: str):
    if not isinstance(blob, str) or not blob.startswith(LOCAL_COMPACTION_PREFIX):
        return None
    raw = blob[len(LOCAL_COMPACTION_PREFIX):]
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _encode_local_compaction_blob(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return LOCAL_COMPACTION_PREFIX + encoded


def _make_input_text_message(role: str, text: str) -> dict:
    part_type = "output_text" if role == "assistant" else "input_text"
    part = {"type": part_type, "text": text}
    if part_type == "output_text":
        part["annotations"] = []
    return {
        "type": "message",
        "role": role,
        "content": [part],
    }


def _is_local_checkpoint_prompt(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") != "message":
        return False
    text = _content_to_text(item.get("content"))
    return CHECKPOINT_MARKER in (text or "")


def _item_text(item: dict) -> str:
    if not isinstance(item, dict):
        return _normalize_ws(str(item))

    item_type = item.get("type")
    if item_type == "message":
        text = _normalize_ws(_content_to_text(item.get("content")))
        if not text:
            return ""
        lower = text.lower()
        if CHECKPOINT_MARKER in text or any(marker in lower for marker in HARNESS_TEXT_MARKERS):
            return ""
        if item.get("role") == "user" and any(marker in lower for marker in META_USER_TEXT_MARKERS):
            return ""
        if item.get("role") == "assistant" and any(marker in lower for marker in META_ASSISTANT_TEXT_MARKERS):
            return ""
        role = item.get("role", "unknown")
        return f"{role}: {text}"

    if item_type in ("reasoning", "web_search_call"):
        return ""

    if item_type in FUNCTION_CALL_TYPES:
        name = item.get("name") or item.get("call_id") or "function"
        arguments = _truncate(_normalize_ws(item.get("arguments") or ""), COMPACTION_CONFIG["max_item_summary_chars"])
        return f"tool call {name}: {arguments}" if arguments else f"tool call {name}"

    if item_type in FUNCTION_OUTPUT_TYPES:
        name = item.get("name") or item.get("call_id") or "tool output"
        output = _truncate(_normalize_ws(_content_to_text(item.get("content")) or item.get("output") or item.get("result") or ""), COMPACTION_CONFIG["max_tool_output_chars"])
        return f"tool result {name}: {output}" if output else f"tool result {name}"

    if item_type == "compaction":
        payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
        if payload:
            return _normalize_ws(payload.get("summary_text", ""))
        return "compacted earlier context"

    text = _normalize_ws(_content_to_text(item.get("content")))
    if text:
        return text
    return _normalize_ws(json.dumps(item, sort_keys=True))


def _is_tool_like(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type in FUNCTION_CALL_TYPES or item_type in FUNCTION_OUTPUT_TYPES:
        return True
    return item.get("role") == "tool"


def _tail_start_for_compaction(items):
    keep_recent = COMPACTION_CONFIG["keep_recent_items"]
    if len(items) <= keep_recent:
        return 0
    start = max(0, len(items) - keep_recent)
    while start > 0 and _is_tool_like(items[start]):
        start -= 1
    if start > 0 and items[start - 1].get("type") in FUNCTION_CALL_TYPES and _is_tool_like(items[start]):
        start -= 1
    return start


def _microcompact_old_tool_results(items):
    if not isinstance(items, list):
        return items
    start = _tail_start_for_compaction(items)
    compacted = []
    for idx, item in enumerate(items):
        if idx >= start or not isinstance(item, dict):
            compacted.append(item)
            continue
        item_type = item.get("type")
        if item_type in FUNCTION_OUTPUT_TYPES or item.get("role") == "tool":
            name = item.get("name") or item.get("call_id") or "tool"
            placeholder = _make_input_text_message(
                "assistant",
                f"Older tool output compacted locally for {name}. The detailed payload was dropped to save context.",
            )
            compacted.append(placeholder)
            continue
        compacted.append(item)
    return compacted


def _expand_local_compaction_items(items):
    if not isinstance(items, list):
        return items
    expanded = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "compaction":
            payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
            if payload:
                summary_text = _normalize_ws(payload.get("summary_text", ""))
                if summary_text:
                    # Keep local compaction summaries alive. normalize_responses_input_for_qwen()
                    # drops replayed developer/system messages when the active Codex harness
                    # is present, so carry this as ordinary user-visible context to llama.cpp.
                    expanded.append(_make_input_text_message(
                        "user",
                        "Context carried forward from local compaction:\n" + summary_text,
                    ))
                continue
        expanded.append(item)
    return expanded


def _summarize_items_for_compaction(items):
    lines = []
    for item in items:
        text = _item_text(item)
        text = _normalize_ws(text)
        if not text:
            continue
        text = _truncate(text, COMPACTION_CONFIG["max_item_summary_chars"])
        if not lines or lines[-1] != text:
            lines.append(text)
    if not lines:
        return ""
    summary = "Earlier conversation summary:\n" + "\n".join(f"- {line}" for line in lines)
    return _truncate(summary, COMPACTION_CONFIG["max_summary_chars"])


def _estimate_items_tokens(items):
    total = 0
    for item in items or []:
        total += _approx_tokens(_item_text(item))
    return total


def _build_local_compaction_response(body: dict) -> dict:
    input_items = body.get("input")
    if isinstance(input_items, str):
        input_items = [_make_input_text_message("user", input_items)]
    elif not isinstance(input_items, list):
        input_items = []

    working_items = []
    for item in input_items:
        if _is_local_checkpoint_prompt(item):
            continue
        if isinstance(item, dict) and item.get("type") in ("reasoning", "web_search_call"):
            continue
        if isinstance(item, dict) and item.get("type") == "message":
            text = _normalize_ws(_content_to_text(item.get("content")))
            lower = text.lower()
            role = item.get("role")
            if any(marker in lower for marker in HARNESS_TEXT_MARKERS):
                continue
            if role == "user" and any(marker in lower for marker in META_USER_TEXT_MARKERS):
                continue
            if role == "assistant" and any(marker in lower for marker in META_ASSISTANT_TEXT_MARKERS):
                continue
        working_items.append(item)
    working_items = _microcompact_old_tool_results(working_items)

    existing_depth = 0
    for item in working_items:
        if isinstance(item, dict) and item.get("type") == "compaction":
            payload = _decode_local_compaction_blob(item.get("encrypted_content", ""))
            if payload:
                existing_depth = max(existing_depth, int(payload.get("depth", 1)))

    tail_start = _tail_start_for_compaction(working_items)
    older = working_items[:tail_start]
    recent = working_items[tail_start:]
    if len(recent) < COMPACTION_CONFIG["min_preserve_items"]:
        recent = working_items[-COMPACTION_CONFIG["min_preserve_items"]:]
        older = working_items[:-len(recent)] if recent else working_items

    summary_text = _summarize_items_for_compaction(older)
    if not summary_text:
        summary_text = "No older turns required compaction."

    depth = min(existing_depth + 1, COMPACTION_CONFIG["max_compaction_depth"])

    payload = {
        "version": 1,
        "source": "turboquant-local",
        "depth": depth,
        "created_at": _now_ts(),
        "summary_text": summary_text,
        "preserved_items": len(recent),
    }
    encrypted = _encode_local_compaction_blob(payload)

    recent = [
        item for item in recent
        if not (isinstance(item, dict) and item.get("type") == "compaction")
        and not _is_local_checkpoint_prompt(item)
    ]

    output_items = [
        {
            "type": "compaction",
            "id": f"cmp_local_{_now_ts()}",
            "created_by": "turboquant-local",
            "encrypted_content": encrypted,
        },
    ]
    output_items.extend(recent)

    while _estimate_items_tokens(output_items) > COMPACTION_CONFIG["target_output_tokens"] and len(recent) > COMPACTION_CONFIG["min_preserve_items"]:
        recent = recent[1:]
        output_items = output_items[:1] + recent

    summary_text = _truncate(summary_text, COMPACTION_CONFIG["max_summary_chars"])
    payload["summary_text"] = summary_text
    payload["preserved_items"] = len(recent)
    output_items[0]["encrypted_content"] = _encode_local_compaction_blob(payload)

    return {
        "id": f"resp_cmp_local_{_now_ts()}",
        "object": "response.compaction",
        "created_at": _now_ts(),
        "output": output_items,
        "usage": {
            "input_tokens": _estimate_items_tokens(working_items),
            "output_tokens": _estimate_items_tokens(output_items),
            "total_tokens": _estimate_items_tokens(working_items) + _estimate_items_tokens(output_items),
        },
    }
