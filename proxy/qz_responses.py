#!/usr/bin/env python3
import base64
import json
import re

try:
    from .qz_request_normalization import (
        CHECKPOINT_MARKER,
        HARNESS_TEXT_MARKERS,
        META_ASSISTANT_TEXT_MARKERS,
        META_USER_TEXT_MARKERS,
        clean_content,
        content_to_text as _content_to_text,
        is_local_checkpoint_prompt as _is_local_checkpoint_prompt,
        normalize_responses_input_for_qwen,
        recursive_clean,
    )
    from .qz_tool_apply_patch import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        ensure_apply_patch_tool_policy,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from .qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from .qz_tool_request import normalize_tools_for_llamacpp
except ImportError:
    from qz_request_normalization import (
        CHECKPOINT_MARKER,
        HARNESS_TEXT_MARKERS,
        META_ASSISTANT_TEXT_MARKERS,
        META_USER_TEXT_MARKERS,
        clean_content,
        content_to_text as _content_to_text,
        is_local_checkpoint_prompt as _is_local_checkpoint_prompt,
        normalize_responses_input_for_qwen,
        recursive_clean,
    )
    from qz_tool_apply_patch import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _parse_apply_patch_arguments,
        ensure_apply_patch_tool_policy,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
    )
    from qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from qz_tool_request import normalize_tools_for_llamacpp

# Item types that are proxy-generated and should be excluded from old-history
# compaction summaries and microcompaction replays. Proxy-local tool types are
# derived from the registry so new tools don't require edits here.
_PROXY_LOCAL_ITEM_TYPES: frozenset[str] = frozenset(
    spec.public_item_type
    for spec in DEFAULT_TOOL_REGISTRY.specs()
    if spec.execution == "proxy_local" and spec.public_item_type
)
# Reasoning items come from upstream and are also excluded; they are not
# managed by the tool registry.
_UPSTREAM_TRANSIENT_ITEM_TYPES: frozenset[str] = frozenset({"reasoning"})
_COMPACTION_DROP_TYPES: frozenset[str] = _PROXY_LOCAL_ITEM_TYPES | _UPSTREAM_TRANSIENT_ITEM_TYPES

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


def normalize_tool_output_for_codex(output_items, output_style: str = "native"):
    return DEFAULT_TOOL_REGISTRY.output_items_to_codex(output_items, output_style)


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

    if item_type in _COMPACTION_DROP_TYPES:
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
        if isinstance(item, dict) and item.get("type") in _COMPACTION_DROP_TYPES:
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
