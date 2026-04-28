#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _quantzhai_var_dir() -> Path:
    return Path(os.environ.get("QZ_VAR_DIR") or Path(__file__).resolve().parents[1] / "var")


def _capture_dir() -> Path:
    path = _quantzhai_var_dir() / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _capture_path(name: str) -> Path:
    return _capture_dir() / name


def _write_capture(name: str, payload, mode: str = "text"):
    path = _capture_path(name)
    if mode == "bytes":
        path.write_bytes(payload)
    elif isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def _append_capture(name: str, text: str):
    with _capture_path(name).open("a", encoding="utf-8") as handle:
        handle.write(text)

MODEL_BUDGETS = {
    "QwenZhai-low": 0,
    "QwenZhai-medium": 256,
    "QwenZhai-high": 1024,
    "QwenZhai-max": -1,
    "QwenZhai": 256,
    "Qwen3.6Turbo-low": 0,
    "Qwen3.6Turbo-medium": 256,
    "Qwen3.6Turbo-high": 1024,
    "Qwen3.6Turbo-max": -1,
    "Qwen3.6Turbo": 256,
}

LOCAL_CODEX_RATE_LIMITS = {
    "limit_id": "codex",
    "limit_name": "local",
    "primary": {
        "used_percent": 0.0,
        "window_minutes": 300,
        "resets_in_seconds": 300 * 60,
        "resets_at": 4102444800,
    },
    "secondary": {
        "used_percent": 0.0,
        "window_minutes": 10080,
        "resets_in_seconds": 10080 * 60,
        "resets_at": 4102444800,
    },
    "credits": {
        "has_credits": True,
        "unlimited": True,
        "balance": None,
    },
    "plan_type": "local",
    "rate_limit_reached_type": None,
}


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

FUNCTION_CALL_TYPES = {"function_call", "computer_call", "code_interpreter_call"}
FUNCTION_OUTPUT_TYPES = {"function_call_output", "computer_call_output", "tool_result", "tool_output"}
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
    have_base_instructions = isinstance(body.get("instructions"), str) and body["instructions"].strip()
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

        if _is_local_checkpoint_prompt(item):
            continue

        if looks_like_meta(role, item_text):
            continue

        if role in ("system", "developer"):
            if not have_base_instructions and item_text.strip():
                fallback_instructions.append(item_text.strip())
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

    def function_tool(name: str, description: str, parameters: dict) -> dict:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters,
        }

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

        if tool_type == "custom" and tool_name == "apply_patch":
            clean.append(function_tool(
                "apply_patch",
                tool.get("description") or "Apply a structured patch to files.",
                {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": "The complete apply_patch envelope to execute.",
                        }
                    },
                    "required": ["patch"],
                    "additionalProperties": False,
                },
            ))
            translated.append("apply_patch")
            continue

        if tool_type == "web_search":
            clean.append(function_tool(
                "web_search",
                "Search the web, open a page, or find text in an opened page using the local web runtime.",
                {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "open_page", "find_in_page"],
                            "description": "The web action to perform.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Search query for search, or needle text for find_in_page.",
                        },
                        "url": {
                            "type": "string",
                            "description": "Page URL for open_page or find_in_page.",
                        },
                        "page_id": {
                            "type": "string",
                            "description": "Previously opened page identifier for find_in_page.",
                        },
                        "categories": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional SearXNG categories to use for search.",
                        },
                        "engines": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional SearXNG engines to use for search.",
                        },
                        "top_k": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "description": "Optional maximum number of search results to return.",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            ))
            translated.append("web_search")
            continue

        dropped.append(str(tool_name))

    body["tools"] = clean

    try:
        notes = []
        if translated:
            notes.append("translated: " + ", ".join(translated))
        if dropped:
            notes.append("dropped: " + ", ".join(dropped))
        _capture_path("latest-dropped-tools.txt").write_text(
            "\n".join(notes) + ("\n" if notes else ""),
            encoding="utf-8"
        )
        _write_capture("latest-forwarded.json", body)
    except Exception:
        pass

    if isinstance(body.get("tool_choice"), dict):
        tool_choice_type = body["tool_choice"].get("type")
        tool_name = body["tool_choice"].get("name")
        if tool_choice_type == "custom" and tool_name == "apply_patch":
            body["tool_choice"] = {"type": "function", "name": "apply_patch"}
        elif tool_choice_type == "web_search":
            body["tool_choice"] = {"type": "function", "name": "web_search"}
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

def make_response_stream_events(out: dict):
    response_id = out.get("id", "resp_local")
    model = out.get("model", "Qwen3.6Turbo")
    output_items = out.get("output", [])

    seq = 0

    def ev(event_type, payload):
        nonlocal seq
        seq += 1
        payload["type"] = event_type
        payload["sequence_number"] = seq
        return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")

    created = {
        "id": response_id,
        "object": "response",
        "created_at": out.get("created_at"),
        "status": "in_progress",
        "model": model,
        "output": [],
    }

    yield ev("response.created", {"response": created})
    yield ev("response.in_progress", {"response": created})

    completed_output = []

    for output_index, item in enumerate(output_items):
        item_type = item.get("type")

        if item_type == "reasoning":
            rs_id = item.get("id") or f"rs_local_{output_index}"
            reasoning_texts = []
            for part in item.get("content", []) or []:
                if part.get("type") == "reasoning_text" and isinstance(part.get("text"), str):
                    reasoning_texts.append(part["text"])
            reasoning_text = "\n".join(reasoning_texts).strip()

            summary_parts = item.get("summary") or []
            summary_texts = []
            for part in summary_parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    summary_texts.append(part["text"])
            summary_text = "\n".join(summary_texts).strip()

            added_item = {
                "id": rs_id,
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
                "encrypted_content": item.get("encrypted_content", ""),
            }

            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": added_item,
            })

            if summary_text:
                yield ev("response.reasoning_summary_part.added", {
                    "item_id": rs_id,
                    "output_index": output_index,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                })
                yield ev("response.reasoning_summary_text.delta", {
                    "item_id": rs_id,
                    "output_index": output_index,
                    "summary_index": 0,
                    "delta": summary_text,
                })
                yield ev("response.reasoning_summary_text.done", {
                    "item_id": rs_id,
                    "output_index": output_index,
                    "summary_index": 0,
                    "text": summary_text,
                })

            if reasoning_text:
                yield ev("response.reasoning_text.delta", {
                    "item_id": rs_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "delta": reasoning_text,
                })
                yield ev("response.reasoning_text.done", {
                    "item_id": rs_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "text": reasoning_text,
                })

            done_item = {
                "id": rs_id,
                "type": "reasoning",
                "status": "completed",
                "summary": [{"type": "summary_text", "text": summary_text}] if summary_text else [],
                "content": [{"type": "reasoning_text", "text": reasoning_text}] if reasoning_text else [],
                "encrypted_content": item.get("encrypted_content", ""),
            }

            yield ev("response.output_item.done", {
                "output_index": output_index,
                "item": done_item,
            })

            completed_output.append(done_item)
            continue

        if item_type == "message":
            msg_id = item.get("id") or f"msg_local_{output_index}"

            part_index = 0
            built_parts = []
            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": {
                    "id": msg_id,
                    "type": "message",
                    "status": "in_progress",
                    "role": item.get("role", "assistant"),
                    "content": [],
                },
            })

            for original_part in item.get("content", []):
                if not isinstance(original_part, dict) or original_part.get("type") != "output_text":
                    continue
                text = original_part.get("text") or ""
                annotations = original_part.get("annotations") or []
                part_stub = {
                    "type": "output_text",
                    "text": "",
                    "annotations": annotations,
                }
                yield ev("response.content_part.added", {
                    "item_id": msg_id,
                    "output_index": output_index,
                    "content_index": part_index,
                    "part": part_stub,
                })
                if text:
                    yield ev("response.output_text.delta", {
                        "item_id": msg_id,
                        "output_index": output_index,
                        "content_index": part_index,
                        "delta": text,
                    })
                yield ev("response.output_text.done", {
                    "item_id": msg_id,
                    "output_index": output_index,
                    "content_index": part_index,
                    "text": text,
                    "logprobs": [],
                })
                final_part = {
                    "type": "output_text",
                    "text": text,
                    "annotations": annotations,
                }
                yield ev("response.content_part.done", {
                    "item_id": msg_id,
                    "output_index": output_index,
                    "content_index": part_index,
                    "part": final_part,
                })
                built_parts.append(final_part)
                part_index += 1

            if not built_parts:
                built_parts.append({
                    "type": "output_text",
                    "text": "",
                    "annotations": [],
                })

            done_item = {
                "id": msg_id,
                "type": "message",
                "status": "completed",
                "role": item.get("role", "assistant"),
                "content": built_parts,
            }

            yield ev("response.output_item.done", {
                "output_index": output_index,
                "item": done_item,
            })

            completed_output.append(done_item)
            continue

        if item_type == "function_call":
            call_id = item.get("call_id") or f"fc_local_{output_index}"
            item_id = item.get("id") or call_id
            name = item.get("name") or "unknown_function"
            arguments = item.get("arguments") or "{}"

            added_item = {
                "id": item_id,
                "type": "function_call",
                "status": "in_progress",
                "call_id": call_id,
                "name": name,
                "arguments": "",
            }

            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": added_item,
            })

            if arguments:
                yield ev("response.function_call_arguments.delta", {
                    "item_id": item_id,
                    "output_index": output_index,
                    "delta": arguments,
                })

            yield ev("response.function_call_arguments.done", {
                "item_id": item_id,
                "output_index": output_index,
                "arguments": arguments,
                "name": name,
            })

            done_item = {
                "id": item_id,
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            }

            yield ev("response.output_item.done", {
                "output_index": output_index,
                "item": done_item,
            })

            completed_output.append(done_item)
            continue

        if item_type == "web_search_call":
            item_id = item.get("id") or f"wsc_local_{output_index}"
            added_item = {
                "id": item_id,
                "type": "web_search_call",
                "status": "in_progress",
                "action": item.get("action", {}),
            }

            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": added_item,
            })

            done_item = {
                "id": item_id,
                "type": "web_search_call",
                "status": item.get("status", "completed"),
                "action": item.get("action", {}),
            }
            if item.get("call_id"):
                done_item["call_id"] = item.get("call_id")

            yield ev("response.output_item.done", {
                "output_index": output_index,
                "item": done_item,
            })

            completed_output.append(done_item)
            continue

    completed = dict(created)
    completed["status"] = "completed"
    completed["output"] = completed_output
    completed["usage"] = out.get("usage")

    yield ev("response.completed", {"response": completed})
    yield b"data: [DONE]\n\n"


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
                    expanded.append(_make_input_text_message("developer", summary_text))
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


WEB_SEARCH_SEARCH_CACHE_TTL = 300
WEB_SEARCH_PAGE_CACHE_TTL = 900
WEB_SEARCH_MAX_RESULTS = 8
WEB_SEARCH_MAX_HOPS = 6
WEB_SEARCH_MAX_SEARCHES = 2
WEB_SEARCH_MAX_OPENS = 3
WEB_SEARCH_USER_AGENT = "qwen36turbo-web-runtime/1.0"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0
        self.in_title = False
        self.title_chunks = []

    def handle_starttag(self, tag, attrs):
        tag = (tag or "").lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in {"p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol", "br", "tr", "table", "pre", "code", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        tag = (tag or "").lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            return
        if tag in {"p", "div", "section", "article", "main", "header", "footer", "aside", "li", "ul", "ol", "br", "tr", "table", "pre", "code", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth or not data:
            return
        if self.in_title:
            self.title_chunks.append(data)
        self._chunks.append(data)

    def get_text(self):
        return _normalize_ws(html_unescape(" ".join(self._chunks)).replace("\xa0", " "))

    def get_title(self):
        return _normalize_ws(html_unescape(" ".join(self.title_chunks)).replace("\xa0", " "))


def _safe_json_file(path: Path):
    try:
        if path and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _canonicalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except Exception:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    clean_path = parts.path or "/"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), clean_path, parts.query, ""))


def _unique_sources(sources):
    out = []
    seen = set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = _canonicalize_url(source.get("url") or "")
        title = _normalize_ws(source.get("title") or "")
        if not url:
            continue
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "url": url,
            "title": title or url,
        })
    return out


def _now_float():
    import time
    return time.time()


def _http_fetch(url: str, timeout: float, accept: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": WEB_SEARCH_USER_AGENT,
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
        final_url = resp.geturl()
        return raw, ctype, final_url


def _extract_page_text(raw: bytes, content_type: str):
    text = ""
    title = ""
    ctype = (content_type or "").lower()
    decoded = raw.decode("utf-8", errors="replace")
    if "html" in ctype or decoded.lstrip().startswith("<"):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(decoded)
        except Exception:
            pass
        title = parser.get_title()
        text = parser.get_text()
    elif "json" in ctype or "xml" in ctype or ctype.startswith("text/"):
        text = _normalize_ws(decoded)
    else:
        text = _normalize_ws(decoded)
    return title, text




class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:18084"
    reasoning_stream_format = "raw"
    searxng_base_url = None
    searxng_timeout = 15.0
    searxng_policy_path = None
    searxng_capabilities_path = None
    searxng_policy = {}
    searxng_capabilities = {}
    web_search_cache = {}
    opened_page_cache = {}

    def log_message(self, fmt, *args):
        return

    def _send_json(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


    def _send_codex_rate_limit_headers(self):
        primary = LOCAL_CODEX_RATE_LIMITS["primary"]
        secondary = LOCAL_CODEX_RATE_LIMITS["secondary"]
        credits = LOCAL_CODEX_RATE_LIMITS["credits"]

        self.send_header("x-codex-limit-id", LOCAL_CODEX_RATE_LIMITS["limit_id"])
        self.send_header("x-codex-limit-name", LOCAL_CODEX_RATE_LIMITS["limit_name"])
        self.send_header("x-codex-plan-type", LOCAL_CODEX_RATE_LIMITS["plan_type"])
        self.send_header("x-codex-primary-used-percent", str(primary["used_percent"]))
        self.send_header("x-codex-primary-window-minutes", str(primary["window_minutes"]))
        self.send_header("x-codex-primary-resets-in-seconds", str(primary["resets_in_seconds"]))
        self.send_header("x-codex-primary-resets-at", str(primary["resets_at"]))
        self.send_header("x-codex-secondary-used-percent", str(secondary["used_percent"]))
        self.send_header("x-codex-secondary-window-minutes", str(secondary["window_minutes"]))
        self.send_header("x-codex-secondary-resets-in-seconds", str(secondary["resets_in_seconds"]))
        self.send_header("x-codex-secondary-resets-at", str(secondary["resets_at"]))
        self.send_header("x-codex-credits-has-credits", "true" if credits["has_credits"] else "false")
        self.send_header("x-codex-credits-unlimited", "true" if credits["unlimited"] else "false")

    def _codex_rate_limits_payload(self):
        payload = {
            "type": "codex.rate_limits",
            "rate_limits": LOCAL_CODEX_RATE_LIMITS,
            "metered_limit_name": "local",
        }
        payload.update(LOCAL_CODEX_RATE_LIMITS)
        return payload

    def _write_codex_rate_limits_event(self):
        self.wfile.write(self._sse_block("codex.rate_limits", self._codex_rate_limits_payload()))
        self.wfile.flush()


    def _handle_responses_compact(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        try:
            _write_capture("latest-compact-request.json", body)
        except Exception:
            pass

        out = _build_local_compaction_response(body)

        try:
            import pathlib
            cmp_item = next((item for item in out.get("output", []) if isinstance(item, dict) and item.get("type") == "compaction"), None)
            payload = _decode_local_compaction_blob(cmp_item.get("encrypted_content", "")) if cmp_item else None
            if payload:
                _capture_path("latest-compact-summary.txt").write_text(
                    payload.get("summary_text", ""),
                    encoding="utf-8",
                )
        except Exception:
            pass

        self._send_json(200, out)


    def _sse_block(self, event_type, payload):
        return (
            f"event: {event_type}\n"
            f"data: {json.dumps(payload)}\n\n"
        ).encode("utf-8")

    def _strip_reasoning_from_payload(self, obj):
        item = obj.get("item")
        if isinstance(item, dict) and item.get("type") == "reasoning":
            return None

        response = obj.get("response")
        if isinstance(response, dict) and isinstance(response.get("output"), list):
            response["output"] = [
                item for item in response["output"]
                if not (isinstance(item, dict) and item.get("type") == "reasoning")
            ]

        return obj

    def _convert_reasoning_item_to_summary(self, item):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            return item

        texts = []
        for part in item.get("content", []) or []:
            if isinstance(part, dict) and part.get("type") == "reasoning_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])

        if texts and not item.get("summary"):
            item["summary"] = [{"type": "summary_text", "text": "\n".join(texts)}]

        item["content"] = []
        return item

    def _transform_sse_event(self, event_lines, summary_started):
        mode = self.reasoning_stream_format
        if mode == "raw":
            return [b"".join(event_lines)]

        event_type = None
        data_parts = []

        for line in event_lines:
            if line.startswith(b"event:"):
                event_type = line.split(b":", 1)[1].strip().decode("utf-8", "replace")
            elif line.startswith(b"data:"):
                data_parts.append(line.split(b":", 1)[1].lstrip().rstrip(b"\r\n"))

        data = b"\n".join(data_parts).strip()
        if not data or data == b"[DONE]":
            return [b"".join(event_lines)]

        try:
            obj = json.loads(data.decode("utf-8"))
        except Exception:
            return [b"".join(event_lines)]

        event_type = event_type or obj.get("type")
        if not isinstance(event_type, str):
            return [b"".join(event_lines)]

        if mode == "hidden":
            if event_type.startswith("response.reasoning_"):
                return []
            stripped = self._strip_reasoning_from_payload(obj)
            if stripped is None:
                return []
            return [self._sse_block(stripped.get("type", event_type), stripped)]

        if mode != "summary":
            return [b"".join(event_lines)]

        out = []

        if event_type == "response.reasoning_text.delta":
            item_id = obj.get("item_id", "rs_local")
            if item_id not in summary_started:
                summary_started.add(item_id)
                part = {
                    "type": "response.reasoning_summary_part.added",
                    "item_id": item_id,
                    "output_index": obj.get("output_index", 0),
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                }
                out.append(self._sse_block("response.reasoning_summary_part.added", part))

            obj["type"] = "response.reasoning_summary_text.delta"
            obj["summary_index"] = obj.pop("content_index", 0)
            out.append(self._sse_block("response.reasoning_summary_text.delta", obj))
            return out

        if event_type == "response.reasoning_text.done":
            obj["type"] = "response.reasoning_summary_text.done"
            obj["summary_index"] = obj.pop("content_index", 0)
            out.append(self._sse_block("response.reasoning_summary_text.done", obj))
            part = {
                "type": "response.reasoning_summary_part.done",
                "item_id": obj.get("item_id", "rs_local"),
                "output_index": obj.get("output_index", 0),
                "summary_index": obj.get("summary_index", 0),
                "part": {"type": "summary_text", "text": obj.get("text", "")},
            }
            out.append(self._sse_block("response.reasoning_summary_part.done", part))
            return out

        item = obj.get("item")
        if isinstance(item, dict) and item.get("type") == "reasoning":
            self._convert_reasoning_item_to_summary(item)

        response = obj.get("response")
        if isinstance(response, dict) and isinstance(response.get("output"), list):
            for item in response["output"]:
                self._convert_reasoning_item_to_summary(item)

        return [self._sse_block(obj.get("type", event_type), obj)]

    def _write_transformed_sse_stream(self, resp, raw_log=None):
        summary_started = set()
        event_lines = []

        while True:
            chunk = resp.readline()
            if not chunk:
                if event_lines:
                    for out_chunk in self._transform_sse_event(event_lines, summary_started):
                        self.wfile.write(out_chunk)
                        self.wfile.flush()
                break

            if raw_log is not None:
                raw_log.write(chunk)
                raw_log.flush()

            event_lines.append(chunk)
            if chunk in (b"\n", b"\r\n"):
                for out_chunk in self._transform_sse_event(event_lines, summary_started):
                    self.wfile.write(out_chunk)
                    self.wfile.flush()
                event_lines = []


    def _ollama_models(self):
        now = "2026-04-27T00:00:00Z"
        models = []
        for name in MODEL_BUDGETS:
            models.append({
                "name": name,
                "model": name,
                "modified_at": now,
                "size": 1,
                "digest": "local-qwen36turbo",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                }
            })
        return models

    def _handle_ollama_get(self):
        # Codex --oss may probe Ollama-compatible endpoints before using /v1.
        if self.path in ("/api/tags", "/v1/api/tags"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        if self.path in ("/api/version", "/v1/api/version"):
            self._send_json(200, {"version": "0.13.4"})
            return True

        if self.path in ("/api/ps", "/v1/api/ps"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        return False

    def _handle_ollama_post(self):
        if self.path not in ("/api/pull", "/v1/api/pull", "/api/show", "/v1/api/show"):
            return False

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        model = body.get("model") or body.get("name") or "Qwen3.6Turbo-medium"

        if self.path in ("/api/pull", "/v1/api/pull"):
            # Pretend the model is already installed.
            # Ollama permits non-stream response {"status":"success"} when stream=false;
            # Codex only needs pull to not fail.
            self._send_json(200, {"status": "success"})
            return True

        if self.path in ("/api/show", "/v1/api/show"):
            self._send_json(200, {
                "modelfile": f"FROM {model}\n",
                "parameters": "",
                "template": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                },
                "model_info": {
                    "general.architecture": "qwen",
                    "general.name": model,
                    "qwen36turbo.context_length": 131072
                },
                "capabilities": ["completion", "tools", "thinking"]
            })
            return True

        return False


    def _log_request_path(self, method):
        try:
            import time
            _append_capture("latest-paths.log", f"{time.time():.3f} {method} {self.path} accept={self.headers.get('Accept','')} content_type={self.headers.get('Content-Type','')}\n")
        except Exception:
            pass

    def do_GET(self):
        self._log_request_path("GET")
        if self._handle_ollama_get():
            return

        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "proxy": "Qwen3.6Turbo",
                "upstream": self.upstream,
                "models": MODEL_BUDGETS,
                "supports": ["/v1/chat/completions", "/v1/responses", "/v1/responses/compact"],
            })
            return

        if self.path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "local"}
                    for name in MODEL_BUDGETS
                ],
            })
            return

        self._proxy_raw("GET")

    def do_POST(self):
        self._log_request_path("POST")

        if self._handle_ollama_post():
            return

        if self.path in ("/chat/completions", "/v1/chat/completions"):
            self._proxy_json_api("/v1/chat/completions")
            return

        if self.path in ("/responses/compact", "/v1/responses/compact"):
            self._handle_responses_compact()
            return

        if self.path in ("/responses", "/v1/responses"):
            self._proxy_json_api("/v1/responses")
            return

        self._proxy_raw("POST")

    def _cache_get(self, cache: dict, key: str, ttl: int):
        now = _now_float()
        item = cache.get(key)
        if not item:
            return None
        if now - item.get("ts", 0) > ttl:
            cache.pop(key, None)
            return None
        return item.get("value")

    def _cache_put(self, cache: dict, key: str, value):
        cache[key] = {"ts": _now_float(), "value": value}

    def _proxy_dir(self) -> Path:
        return _capture_dir()

    def _runtime_log(self, name: str, payload):
        try:
            path = self._proxy_dir() / name
            if isinstance(payload, (dict, list)):
                path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                path.write_text(str(payload), encoding="utf-8")
        except Exception:
            pass

    def _allowed_engine_names(self):
        caps = self.searxng_capabilities or {}
        ok = set()
        for item in caps.get("recommended_for_coding_agent") or []:
            if isinstance(item, dict) and item.get("name"):
                ok.add(item["name"])
        if not ok:
            for name, meta in (caps.get("engine_probe") or {}).items():
                if isinstance(meta, dict) and meta.get("status") == "ok":
                    ok.add(name)
        return ok

    def _coding_profile(self):
        policy = self.searxng_policy or {}
        caps = self.searxng_capabilities or {}
        safe_categories = set(caps.get("safe_categories") or [])
        disallowed = set(policy.get("disabled_even_if_configured") or [])
        disallowed |= set(policy.get("never_for_coding_agent") or [])
        ok_engines = self._allowed_engine_names()

        categories = list((policy.get("agent_coding") or {}).get("categories") or ["it", "repos", "q&a", "packages", "software wikis"])
        if safe_categories:
            categories = [c for c in categories if c in safe_categories]
        if not categories:
            categories = ["it", "repos", "q&a", "packages", "software wikis"]

        engines = list((policy.get("agent_coding") or {}).get("engines") or [])
        engines = [e for e in engines if e not in disallowed and (not ok_engines or e in ok_engines)]

        fallback_engines = list((policy.get("agent_default") or {}).get("engines") or [])
        fallback_engines = [e for e in fallback_engines if e not in disallowed and (not ok_engines or e in ok_engines)]

        if not engines:
            engines = fallback_engines[:8]

        fallback_categories = list((policy.get("agent_default") or {}).get("categories") or ["web", "general"])
        if safe_categories:
            fallback_categories = [c for c in fallback_categories if c in safe_categories]
        if not fallback_categories:
            fallback_categories = ["web", "general"]

        return {
            "categories": categories,
            "engines": engines,
            "fallback_categories": fallback_categories,
            "fallback_engines": fallback_engines,
        }

    def _query_searxng(self, query: str, categories=None, engines=None, top_k: int = WEB_SEARCH_MAX_RESULTS):
        if not self.searxng_base_url:
            return {"error": "SearXNG is not configured.", "results": []}

        categories = [c for c in (categories or []) if isinstance(c, str) and c.strip()]
        engines = [e for e in (engines or []) if isinstance(e, str) and e.strip()]
        key = json.dumps({
            "q": query,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
        }, sort_keys=True)
        cached = self._cache_get(self.web_search_cache, key, WEB_SEARCH_SEARCH_CACHE_TTL)
        if cached is not None:
            return cached

        params = {
            "q": query,
            "format": "json",
            "pageno": "1",
        }
        if categories:
            params["categories"] = ",".join(categories)
        if engines:
            params["engines"] = ",".join(engines)

        url = self.searxng_base_url.rstrip("/") + "/search?" + urllib.parse.urlencode(params)
        try:
            raw, _content_type, _final_url = _http_fetch(url, self.searxng_timeout, "application/json")
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:
            result = {"error": str(e), "results": []}
            self._cache_put(self.web_search_cache, key, result)
            return result

        results = []
        seen = set()
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            item_url = _canonicalize_url(item.get("url") or "")
            if not item_url or item_url in seen:
                continue
            seen.add(item_url)
            results.append({
                "title": _normalize_ws(item.get("title") or "") or item_url,
                "url": item_url,
                "snippet": _truncate(_normalize_ws(item.get("content") or ""), 400),
                "engine": item.get("engine"),
                "engines": item.get("engines") or [],
                "published_date": item.get("publishedDate") or item.get("pubdate"),
            })
            if len(results) >= max(1, min(int(top_k or WEB_SEARCH_MAX_RESULTS), WEB_SEARCH_MAX_RESULTS)):
                break

        result = {
            "query": query,
            "results": results,
            "categories": categories,
            "engines": engines,
            "unresponsive_engines": payload.get("unresponsive_engines") or [],
            "answers": payload.get("answers") or [],
        }
        self._cache_put(self.web_search_cache, key, result)
        return result

    def _search_web(self, query: str, categories=None, engines=None, top_k: int = WEB_SEARCH_MAX_RESULTS):
        profile = self._coding_profile()
        primary_categories = categories or profile["categories"]
        primary_engines = engines or profile["engines"]
        result = self._query_searxng(query, primary_categories, primary_engines, top_k=top_k)
        if result.get("results"):
            result["profile"] = "coding"
            return result

        fallback = self._query_searxng(
            query,
            profile["fallback_categories"],
            profile["fallback_engines"],
            top_k=top_k,
        )
        fallback["profile"] = "fallback"
        return fallback

    def _open_page(self, url: str):
        canonical_url = _canonicalize_url(url)
        if not canonical_url:
            return {"error": f"Unsupported URL: {url}"}

        cached = self._cache_get(self.opened_page_cache, canonical_url, WEB_SEARCH_PAGE_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            raw, content_type, final_url = _http_fetch(
                canonical_url,
                self.searxng_timeout,
                "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.1",
            )
        except Exception as e:
            result = {
                "url": canonical_url,
                "page_id": "page_" + base64.urlsafe_b64encode(canonical_url.encode("utf-8")).decode("ascii").rstrip("="),
                "title": canonical_url,
                "content": "",
                "content_type": "fetch_error",
                "status": "error",
                "error": str(e),
            }
            self._cache_put(self.opened_page_cache, canonical_url, result)
            return result

        title, text = _extract_page_text(raw, content_type)
        final_url = _canonicalize_url(final_url) or canonical_url
        result = {
            "url": final_url,
            "page_id": "page_" + base64.urlsafe_b64encode(final_url.encode("utf-8")).decode("ascii").rstrip("="),
            "title": title or final_url,
            "content": _truncate(text, 12000),
            "content_type": content_type,
            "status": "ok",
        }
        self._cache_put(self.opened_page_cache, canonical_url, result)
        if final_url != canonical_url:
            self._cache_put(self.opened_page_cache, final_url, result)
        return result

    def _find_in_page(self, query: str, url: str = None, page_id: str = None):
        page = None
        if page_id:
            for item in self.opened_page_cache.values():
                value = item.get("value") if isinstance(item, dict) else None
                if isinstance(value, dict) and value.get("page_id") == page_id:
                    page = value
                    break
        if page is None and url:
            page = self._open_page(url)

        if not isinstance(page, dict) or not page.get("content"):
            return {
                "page_id": page.get("page_id") if isinstance(page, dict) else page_id,
                "url": page.get("url") if isinstance(page, dict) else url,
                "title": page.get("title") if isinstance(page, dict) else (url or ""),
                "query": query,
                "matches": [],
                "status": "empty",
            }

        haystack = page.get("content", "")
        needle = (query or "").strip()
        if not needle:
            return {
                "page_id": page.get("page_id"),
                "url": page.get("url"),
                "title": page.get("title"),
                "query": query,
                "matches": [],
                "status": "empty",
            }

        lower_haystack = haystack.lower()
        lower_needle = needle.lower()
        matches = []
        start = 0
        while len(matches) < 5:
            idx = lower_haystack.find(lower_needle, start)
            if idx < 0:
                break
            snippet_start = max(0, idx - 140)
            snippet_end = min(len(haystack), idx + len(needle) + 220)
            snippet = _normalize_ws(haystack[snippet_start:snippet_end])
            matches.append({
                "start_index": idx,
                "end_index": idx + len(needle) - 1,
                "snippet": snippet,
            })
            start = idx + len(needle)

        return {
            "page_id": page.get("page_id"),
            "url": page.get("url"),
            "title": page.get("title"),
            "query": query,
            "matches": matches,
            "status": "ok" if matches else "empty",
        }

    def _parse_web_search_arguments(self, arguments: str):
        try:
            data = json.loads(arguments or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        action = str(data.get("action") or "").strip() or "search"
        query = data.get("query")
        url = data.get("url")
        page_id = data.get("page_id")
        categories = data.get("categories") if isinstance(data.get("categories"), list) else None
        engines = data.get("engines") if isinstance(data.get("engines"), list) else None
        top_k = data.get("top_k")
        try:
            top_k = int(top_k) if top_k is not None else WEB_SEARCH_MAX_RESULTS
        except Exception:
            top_k = WEB_SEARCH_MAX_RESULTS
        top_k = max(1, min(top_k, WEB_SEARCH_MAX_RESULTS))
        return {
            "action": action,
            "query": query,
            "url": url,
            "page_id": page_id,
            "categories": categories,
            "engines": engines,
            "top_k": top_k,
        }

    def _execute_web_search_call(self, call_item: dict, counters: dict, seen_signatures: set):
        args = self._parse_web_search_arguments(call_item.get("arguments") or "{}")
        action = args["action"]
        query = args.get("query")
        url = args.get("url")
        page_id = args.get("page_id")
        signature = (
            action,
            _normalize_ws(query or "").lower(),
            _canonicalize_url(url or ""),
            page_id or "",
        )

        if signature in seen_signatures:
            repeated = True
        else:
            repeated = False
            seen_signatures.add(signature)

        error = None
        payload = {}
        sources = []

        if action == "search":
            if counters["search"] >= WEB_SEARCH_MAX_SEARCHES:
                error = f"Refusing search: reached per-turn limit of {WEB_SEARCH_MAX_SEARCHES} search calls."
            elif repeated:
                error = "Refusing repeated search request; use the cached result or open a page instead."
            elif not isinstance(query, str) or not query.strip():
                error = "Missing query for search."
            else:
                counters["search"] += 1
                payload = self._search_web(
                    query=query.strip(),
                    categories=args.get("categories"),
                    engines=args.get("engines"),
                    top_k=args.get("top_k") or WEB_SEARCH_MAX_RESULTS,
                )
                sources = [{"url": r.get("url"), "title": r.get("title")} for r in payload.get("results") or []]

        elif action == "open_page":
            if counters["open_page"] >= WEB_SEARCH_MAX_OPENS:
                error = f"Refusing open_page: reached per-turn limit of {WEB_SEARCH_MAX_OPENS} page opens."
            elif repeated:
                error = "Refusing repeated open_page request for the same page."
            elif not isinstance(url, str) or not url.strip():
                error = "Missing url for open_page."
            else:
                counters["open_page"] += 1
                payload = self._open_page(url.strip())
                sources = [{"url": payload.get("url"), "title": payload.get("title")}]

        elif action == "find_in_page":
            if repeated:
                error = "Refusing repeated find_in_page request with the same arguments."
            elif not isinstance(query, str) or not query.strip():
                error = "Missing query for find_in_page."
            elif not page_id and not url:
                error = "find_in_page requires page_id or url."
            else:
                payload = self._find_in_page(query=query.strip(), url=url, page_id=page_id)
                sources = [{"url": payload.get("url"), "title": payload.get("title")}]

        else:
            error = f"Unsupported web_search action: {action}"

        result_payload = {
            "ok": error is None,
            "action": action,
            "result": payload if error is None else {},
            "error": error,
        }

        web_call_item = {
            "id": call_item.get("id") or call_item.get("call_id") or f"wsc_local_{_now_ts()}",
            "type": "web_search_call",
            "status": "completed",
            "call_id": call_item.get("call_id"),
            "action": {
                "type": action,
            },
        }

        if action == "search" and isinstance(query, str):
            web_call_item["action"]["queries"] = [query]
            web_call_item["action"]["result_count"] = len((payload or {}).get("results") or [])
        elif action == "open_page" and isinstance(url, str):
            web_call_item["action"]["url"] = payload.get("url") if isinstance(payload, dict) else url
            if isinstance(payload, dict) and payload.get("page_id"):
                web_call_item["action"]["page_id"] = payload.get("page_id")
        elif action == "find_in_page":
            web_call_item["action"]["query"] = query
            if isinstance(payload, dict):
                web_call_item["action"]["url"] = payload.get("url")
                web_call_item["action"]["page_id"] = payload.get("page_id")
                web_call_item["action"]["match_count"] = len(payload.get("matches") or [])

        if error:
            web_call_item["status"] = "failed"
            web_call_item["error"] = error

        tool_output_item = {
            "type": "function_call_output",
            "call_id": call_item.get("call_id") or call_item.get("id") or f"fc_local_{_now_ts()}",
            "output": json.dumps(result_payload, ensure_ascii=False),
        }

        return web_call_item, tool_output_item, _unique_sources(sources)

    def _annotate_output_with_url_citations(self, out: dict, sources):
        unique_sources = _unique_sources(sources)[:4]
        if not unique_sources:
            return out

        output_items = out.get("output") or []
        for item in reversed(output_items):
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content") or []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                annotations = list(part.get("annotations") or [])
                for idx, source in enumerate(unique_sources, start=1):
                    marker = f" [{idx}]"
                    start_index = len(text)
                    text += marker
                    end_index = len(text) - 1
                    annotations.append({
                        "type": "url_citation",
                        "start_index": start_index,
                        "end_index": end_index,
                        "title": source.get("title") or source.get("url"),
                        "url": source.get("url"),
                    })
                part["text"] = text
                part["annotations"] = annotations
                return out
        return out

    def _call_upstream_json(self, url: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            resp_data = resp.read()
            status = resp.status
            content_type = resp.headers.get("Content-Type", "application/json")
        return status, content_type, resp_data

    def _run_responses_locally(self, body: dict, requested_model: str):
        url = self.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            status, content_type, resp_data = self._call_upstream_json(url, working_body)
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model

            output_items = out.get("output") or []
            web_calls = [
                item for item in output_items
                if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") == "web_search"
            ]

            if not web_calls:
                final_out = dict(out)
                final_out["output"] = public_trace + output_items
                self._annotate_output_with_url_citations(final_out, gathered_sources)
                self._runtime_log("latest-web-runtime-final.json", final_out)
                return status, content_type, final_out

            next_input = list(working_body.get("input") or [])
            next_input.extend(output_items)

            for call in web_calls:
                public_item, tool_output_item, sources = self._execute_web_search_call(call, counters, seen_signatures)
                public_trace.append(public_item)
                gathered_sources.extend(sources)
                next_input.append(tool_output_item)

            working_body["input"] = next_input

        fallback_out = {
            "id": f"resp_local_{_now_ts()}",
            "object": "response",
            "created_at": _now_ts(),
            "model": requested_model,
            "output": public_trace + [{
                "id": f"msg_local_{_now_ts()}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": "I stopped the web tool loop after hitting the safety limit for repeated search/open actions.",
                    "annotations": [],
                }],
            }],
            "usage": {},
        }
        self._annotate_output_with_url_citations(fallback_out, gathered_sources)
        self._runtime_log("latest-web-runtime-final.json", fallback_out)
        return 200, "application/json", fallback_out



    def _proxy_json_api(self, upstream_path):
        try:
            import time
            _append_capture("latest-json-api.log", f"{time.time():.3f} ENTER path={self.path} upstream_path={upstream_path} accept={self.headers.get('Accept','')}\n")
        except Exception:
            pass

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Debug: dump latest request so we can see Codex tool schema.
        try:
            _write_capture("latest-request.json", body)
        except Exception:
            pass

        client_wants_stream = (
            body.get("stream") is True
            or "text/event-stream" in self.headers.get("Accept", "")
        )

        requested_model = body.get("model") or "Qwen3.6Turbo-medium"
        budget = MODEL_BUDGETS.get(requested_model, MODEL_BUDGETS["Qwen3.6Turbo"])

        body["model"] = requested_model
        body["thinking_budget_tokens"] = budget
        body.setdefault("temperature", 0.1)

        if upstream_path == "/v1/responses":
            input_items = body.get("input")
            if isinstance(input_items, list):
                body["input"] = _microcompact_old_tool_results(_expand_local_compaction_items(input_items))
            body = normalize_responses_input_for_qwen(body)
            body = normalize_tools_for_llamacpp(body)

            try:
                status, content_type, out = self._run_responses_locally(body, requested_model)
            except urllib.error.HTTPError as e:
                resp_data = e.read()
                try:
                    self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
                except Exception:
                    self.send_response(e.code)
                    self.send_header("Content-Type", "text/plain")
                    self._send_codex_rate_limit_headers()
                    self.end_headers()
                    self.wfile.write(resp_data)
                return
            except Exception as e:
                try:
                    import traceback
                    self._runtime_log("latest-web-runtime-error.txt", traceback.format_exc())
                except Exception:
                    pass
                self._send_json(502, {"error": f"local web runtime error: {e}"})
                return

            if client_wants_stream:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                for chunk in make_response_stream_events(out):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return

            self._send_json(status, out)
            return

        data = json.dumps(body).encode("utf-8")
        url = self.upstream + upstream_path

        try:
            import time
            _append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM url={url} bytes={len(data)} stream={body.get('stream')}\n")
        except Exception:
            pass

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": self.headers.get("Accept", "application/json"),
            },
        )

        try:
            resp = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            try:
                self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
            except Exception:
                self.send_response(e.code)
                self.send_header("Content-Type", "text/plain")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self.wfile.write(resp_data)
            return
        except Exception as e:
            try:
                import time, traceback
                _append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM_EXCEPTION {type(e).__name__}: {e}\n")
                _append_capture("latest-json-api.log", traceback.format_exc() + "\n")
            except Exception:
                pass
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        content_type = resp.headers.get("Content-Type", "application/json")
        status = resp.status

        if upstream_path == "/v1/responses" and client_wants_stream and "text/event-stream" in content_type:
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_codex_rate_limit_headers()
            self.end_headers()
            self._write_codex_rate_limits_event()

            try:
                raw_log_path = _capture_path("latest-upstream-response.raw")
                status_path = _capture_path("latest-upstream-status.txt")
                status_path.write_text(
                    f"status={status}\ncontent_type={content_type}\nstream=passthrough\nreasoning_stream_format={self.reasoning_stream_format}\nrate_limits=local\n",
                    encoding="utf-8"
                )
                raw_log = raw_log_path.open("wb")
            except Exception:
                raw_log = None

            try:
                self._write_transformed_sse_stream(resp, raw_log)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if raw_log is not None:
                    raw_log.close()
                resp.close()
            return

        resp_data = resp.read()
        resp.close()

        try:
            _write_capture("latest-upstream-response.raw", resp_data, mode="bytes")
            _capture_path("latest-upstream-status.txt").write_text(
                f"status={status}\ncontent_type={content_type}\n",
                encoding="utf-8"
            )
        except Exception:
            pass

        try:
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model
            # Do not recursively clean response text here.
            # Reasoning format conversion belongs in the SSE transform layer.
            # Final output_text must pass through unchanged.

            if upstream_path == "/v1/responses" and client_wants_stream:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                for chunk in make_response_stream_events(out):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return

            self._send_json(status, out)
        except Exception:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._send_codex_rate_limit_headers()
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)

    def _proxy_raw(self, method):
        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length) if length else None
        url = self.upstream + self.path

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Authorization": self.headers.get("Authorization", "Bearer local"),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                resp_data = resp.read()
                status = resp.status
                content_type = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            status = e.code
            content_type = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18180)
    ap.add_argument("--upstream", default="http://127.0.0.1:18084")
    ap.add_argument("--reasoning-stream-format", choices=("raw", "summary", "hidden"), default="raw")
    ap.add_argument("--searxng-base-url", default=os.environ.get("SEARXNG_BASE_URL"))
    ap.add_argument("--searxng-timeout", type=float, default=float(os.environ.get("SEARXNG_TIMEOUT", "15")))
    ap.add_argument("--searxng-policy", default=os.environ.get("SEARXNG_POLICY"))
    ap.add_argument("--searxng-capabilities", default=os.environ.get("SEARXNG_CAPABILITIES"))
    args = ap.parse_args()

    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.reasoning_stream_format = args.reasoning_stream_format

    script_dir = Path(__file__).resolve().parent
    policy_path = Path(args.searxng_policy) if args.searxng_policy else script_dir / "searxng-agent-policy.json"
    capabilities_path = Path(args.searxng_capabilities) if args.searxng_capabilities else script_dir / "searxng-capabilities.json"
    policy = _safe_json_file(policy_path)
    capabilities = _safe_json_file(capabilities_path)

    ProxyHandler.searxng_policy_path = str(policy_path)
    ProxyHandler.searxng_capabilities_path = str(capabilities_path)
    ProxyHandler.searxng_policy = policy
    ProxyHandler.searxng_capabilities = capabilities
    ProxyHandler.searxng_base_url = args.searxng_base_url or policy.get("searxng_base") or capabilities.get("base")
    ProxyHandler.searxng_timeout = args.searxng_timeout

    server = ThreadingHTTPServer((args.listen, args.port), ProxyHandler)
    print(
        f"Qwen3.6Turbo proxy listening on {args.listen}:{args.port} -> {ProxyHandler.upstream}, reasoning_stream_format={ProxyHandler.reasoning_stream_format}, searxng_base={ProxyHandler.searxng_base_url}",
        flush=True,
    )
    server.serve_forever()

if __name__ == "__main__":
    main()
