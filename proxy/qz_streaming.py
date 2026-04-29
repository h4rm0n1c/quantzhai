#!/usr/bin/env python3
import json


FUNCTION_CALL_EVENT_TYPES = {
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
}


def parse_sse_event_lines(event_lines):
    """Return (event_type, payload) for one SSE event block, or (event_type, None)."""
    event_type = None
    data_parts = []

    for line in event_lines or []:
        if isinstance(line, str):
            line = line.encode("utf-8")
        line = line.rstrip(b"\r\n")
        if not line or line.startswith(b":"):
            continue
        if line.startswith(b"event:"):
            event_type = line.split(b":", 1)[1].strip().decode("utf-8", "replace")
            continue
        if line.startswith(b"data:"):
            data_parts.append(line.split(b":", 1)[1].lstrip())

    if not data_parts:
        return event_type, None

    data = b"\n".join(data_parts).strip()
    if data == b"[DONE]":
        return event_type or "done", "[DONE]"

    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception:
        return event_type, None

    if not event_type and isinstance(payload, dict):
        event_type = payload.get("type")

    return event_type, payload


def is_function_call_stream_event(event_type, payload) -> bool:
    if event_type in FUNCTION_CALL_EVENT_TYPES:
        return True
    if event_type in {"response.output_item.added", "response.output_item.done"} and isinstance(payload, dict):
        item = payload.get("item")
        return isinstance(item, dict) and item.get("type") == "function_call"
    return False


def is_terminal_stream_event(event_type, payload) -> bool:
    if event_type == "done" or payload == "[DONE]":
        return True
    return event_type in {
        "response.completed",
        "response.failed",
        "response.cancelled",
        "response.incomplete",
    }


def public_tool_item_events(item: dict, output_index: int, sequence_start: int = 0):
    seq = sequence_start

    def block(event_type, payload):
        nonlocal seq
        seq += 1
        payload = dict(payload)
        payload["type"] = event_type
        payload["sequence_number"] = seq
        return (
            f"event: {event_type}\n"
            f"data: {json.dumps(payload)}\n\n"
        ).encode("utf-8")

    item_id = item.get("id") or item.get("call_id") or f"tool_local_{output_index}"
    added_item = dict(item)
    added_item["id"] = item_id
    added_item["status"] = "in_progress"

    done_item = dict(item)
    done_item["id"] = item_id
    done_item.setdefault("status", "completed")

    return [
        block("response.output_item.added", {
            "output_index": output_index,
            "item": added_item,
        }),
        block("response.output_item.done", {
            "output_index": output_index,
            "item": done_item,
        }),
    ], seq


def rewrite_sse_payload(event_type, payload, output_index_offset: int = 0, prepend_output=None, model=None):
    if not isinstance(payload, dict):
        return event_type, payload

    rewritten = json.loads(json.dumps(payload))

    if output_index_offset and isinstance(rewritten.get("output_index"), int):
        rewritten["output_index"] += output_index_offset

    response = rewritten.get("response")
    if isinstance(response, dict):
        if model:
            response["model"] = model
        if prepend_output and isinstance(response.get("output"), list):
            response["output"] = list(prepend_output) + response["output"]

    return event_type, rewritten


class StreamedFunctionCallAssembler:
    """Tracks Responses SSE function-call deltas until a call is complete."""

    def __init__(self):
        self._calls = {}

    def _key(self, payload):
        item_id = payload.get("item_id")
        if item_id:
            return item_id
        item = payload.get("item")
        if isinstance(item, dict):
            return item.get("id") or item.get("call_id")
        output_index = payload.get("output_index")
        if output_index is not None:
            return f"output:{output_index}"
        return None

    def _ensure_call(self, key):
        call = self._calls.get(key)
        if call is None:
            call = {
                "id": key,
                "type": "function_call",
                "status": "in_progress",
                "call_id": key,
                "name": None,
                "arguments": "",
            }
            self._calls[key] = call
        return call

    def observe(self, event_type, payload):
        if not isinstance(event_type, str) or not isinstance(payload, dict):
            return []

        completed = []

        if event_type == "response.output_item.added":
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                key = item.get("id") or item.get("call_id") or self._key(payload)
                if key:
                    call = self._ensure_call(key)
                    call.update({
                        "id": item.get("id") or call.get("id"),
                        "type": "function_call",
                        "status": item.get("status", "in_progress"),
                        "call_id": item.get("call_id") or call.get("call_id"),
                        "name": item.get("name") or call.get("name"),
                        "output_index": payload.get("output_index"),
                    })
                    if isinstance(item.get("arguments"), str) and item.get("arguments"):
                        call["arguments"] = item["arguments"]
            return completed

        if event_type == "response.function_call_arguments.delta":
            key = self._key(payload)
            if key:
                call = self._ensure_call(key)
                delta = payload.get("delta")
                if isinstance(delta, str):
                    call["arguments"] = (call.get("arguments") or "") + delta
            return completed

        if event_type == "response.function_call_arguments.done":
            key = self._key(payload)
            if key:
                call = self._ensure_call(key)
                if isinstance(payload.get("arguments"), str):
                    call["arguments"] = payload["arguments"]
                if isinstance(payload.get("name"), str):
                    call["name"] = payload["name"]
            return completed

        if event_type == "response.output_item.done":
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "function_call":
                return completed
            key = item.get("id") or item.get("call_id") or self._key(payload)
            if not key:
                return completed
            call = self._ensure_call(key)
            call.update({
                "id": item.get("id") or call.get("id"),
                "type": "function_call",
                "status": item.get("status", "completed"),
                "call_id": item.get("call_id") or call.get("call_id"),
                "name": item.get("name") or call.get("name"),
                "output_index": payload.get("output_index", call.get("output_index")),
            })
            if isinstance(item.get("arguments"), str):
                call["arguments"] = item["arguments"]
            completed_call = dict(call)
            completed_call["status"] = "completed"
            completed.append(completed_call)
            self._calls.pop(key, None)

        return completed
