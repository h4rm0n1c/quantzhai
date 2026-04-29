#!/usr/bin/env python3
import json


def _token_count(value, fallback: int = 0) -> int:
    try:
        count = int(value)
    except Exception:
        return fallback
    return max(0, count)


def _normalize_response_usage(usage) -> dict:
    source = usage if isinstance(usage, dict) else {}
    normalized = dict(source)

    input_tokens = _token_count(source.get("input_tokens"), _token_count(source.get("prompt_tokens")))
    output_tokens = _token_count(source.get("output_tokens"), _token_count(source.get("completion_tokens")))
    total_tokens = _token_count(source.get("total_tokens"), input_tokens + output_tokens)
    if total_tokens < input_tokens + output_tokens:
        total_tokens = input_tokens + output_tokens

    normalized["input_tokens"] = input_tokens
    normalized["output_tokens"] = output_tokens
    normalized["total_tokens"] = total_tokens
    if not isinstance(normalized.get("input_tokens_details"), dict):
        normalized["input_tokens_details"] = {"cached_tokens": 0}
    if not isinstance(normalized.get("output_tokens_details"), dict):
        normalized["output_tokens_details"] = {"reasoning_tokens": 0}
    return normalized


def make_sse_block(event_type, payload):
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


def _strip_reasoning_from_payload(obj):
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


def _convert_reasoning_item_to_summary(item):
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


def transform_sse_event(event_lines, summary_started, mode):
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
        stripped = _strip_reasoning_from_payload(obj)
        if stripped is None:
            return []
        return [make_sse_block(stripped.get("type", event_type), stripped)]

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
            out.append(make_sse_block("response.reasoning_summary_part.added", part))

        obj["type"] = "response.reasoning_summary_text.delta"
        obj["summary_index"] = obj.pop("content_index", 0)
        out.append(make_sse_block("response.reasoning_summary_text.delta", obj))
        return out

    if event_type == "response.reasoning_text.done":
        obj["type"] = "response.reasoning_summary_text.done"
        obj["summary_index"] = obj.pop("content_index", 0)
        out.append(make_sse_block("response.reasoning_summary_text.done", obj))
        part = {
            "type": "response.reasoning_summary_part.done",
            "item_id": obj.get("item_id", "rs_local"),
            "output_index": obj.get("output_index", 0),
            "summary_index": obj.get("summary_index", 0),
            "part": {"type": "summary_text", "text": obj.get("text", "")},
        }
        out.append(make_sse_block("response.reasoning_summary_part.done", part))
        return out

    item = obj.get("item")
    if isinstance(item, dict) and item.get("type") == "reasoning":
        _convert_reasoning_item_to_summary(item)

    response = obj.get("response")
    if isinstance(response, dict) and isinstance(response.get("output"), list):
        for item in response["output"]:
            _convert_reasoning_item_to_summary(item)

    return [make_sse_block(obj.get("type", event_type), obj)]


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
        return make_sse_block(event_type, payload)

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

        if item_type == "apply_patch_call":
            item_id = item.get("id") or f"apc_local_{output_index}"
            call_id = item.get("call_id") or f"call_apply_patch_{output_index}"
            done_item = {
                "id": item_id,
                "type": "apply_patch_call",
                "status": item.get("status", "completed"),
                "call_id": call_id,
                "operation": item.get("operation") or {},
            }

            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": dict(done_item, status="in_progress"),
            })
            yield ev("response.output_item.done", {
                "output_index": output_index,
                "item": done_item,
            })

            completed_output.append(done_item)
            continue

        if item_type == "custom_tool_call":
            call_id = item.get("call_id") or f"call_custom_{output_index}"
            done_item = {
                "type": "custom_tool_call",
                "status": item.get("status", "completed"),
                "call_id": call_id,
                "name": item.get("name"),
                "input": item.get("input") or "",
            }

            yield ev("response.output_item.added", {
                "output_index": output_index,
                "item": dict(done_item, status="in_progress"),
            })
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

        done_item = dict(item)
        done_item.setdefault("id", f"item_local_{output_index}")
        done_item.setdefault("status", "completed")
        added_item = dict(done_item)
        added_item["status"] = "in_progress"

        yield ev("response.output_item.added", {
            "output_index": output_index,
            "item": added_item,
        })
        yield ev("response.output_item.done", {
            "output_index": output_index,
            "item": done_item,
        })
        completed_output.append(done_item)

    completed = dict(created)
    completed["status"] = "completed"
    completed["output"] = completed_output
    completed["usage"] = _normalize_response_usage(out.get("usage"))

    yield ev("response.completed", {"response": completed})
    yield b"data: [DONE]\n\n"
