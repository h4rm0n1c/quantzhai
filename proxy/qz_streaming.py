#!/usr/bin/env python3
import json


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
            })
            if isinstance(item.get("arguments"), str):
                call["arguments"] = item["arguments"]
            completed_call = dict(call)
            completed_call["status"] = "completed"
            completed.append(completed_call)
            self._calls.pop(key, None)

        return completed
