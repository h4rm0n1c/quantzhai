import json
import unittest
from pathlib import Path

from proxy.qz_responses_stream import ResponsesStreamRuntime

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sse"


def _sse_block(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


class FakeStream:
    def __init__(self, chunks):
        self._lines = []
        for chunk in chunks:
            self._lines.extend(chunk.splitlines(keepends=True))
        self.closed = False

    def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def close(self):
        self.closed = True


def _fixture_chunks(name):
    return [FIXTURE_DIR.joinpath(name).read_bytes()]


def _parse_sse_events(stream_text):
    events = []
    event_type = None
    data_lines = []
    for line in stream_text.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
            continue
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                payload = "[DONE]" if data == "[DONE]" else json.loads(data)
                events.append((event_type or (payload.get("type") if isinstance(payload, dict) else None), payload))
            event_type = None
            data_lines = []
    return events


class FakeWebRuntime:
    def __init__(self):
        self.calls = []

    def execute_web_search_call(self, call_item, counters, seen_signatures):
        self.calls.append({
            "call_item": call_item,
            "counters": dict(counters),
            "seen_signatures": set(seen_signatures),
        })
        public_item = {
            "id": "wsc_1",
            "type": "web_search_call",
            "status": "completed",
            "call_id": call_item.get("call_id"),
            "action": {
                "type": "search",
                "queries": ["quantzhai"],
                "result_count": 1,
            },
        }
        output_item = {
            "type": "function_call_output",
            "call_id": call_item.get("call_id"),
            "output": json.dumps({"ok": True, "result": {"results": [{"title": "QuantZhai"}]}}),
        }
        return public_item, output_item, [{"url": "https://example.test", "title": "QuantZhai"}]


def _web_call_stream():
    arguments = json.dumps({"action": "search", "query": "quantzhai"})
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_web",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_web",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_web",
                "name": "web_search",
                "arguments": "",
            },
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_web",
            "output_index": 0,
            "delta": arguments,
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_web",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_web",
                "name": "web_search",
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _final_message_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_final",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "msg_final",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_final",
            "output_index": 0,
            "content_index": 0,
            "delta": "searched.",
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_final",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "searched.", "annotations": []}],
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _apply_patch_call_stream():
    arguments = json.dumps({
        "operation": {
            "type": "create_file",
            "path": "tmp/quantzhai-smoke.txt",
            "diff": "@@\n+quantzhai apply_patch smoke\n",
        }
    })
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_apply_patch",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_patch",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_patch",
                "name": "apply_patch",
                "arguments": "",
            },
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_patch",
            "output_index": 0,
            "delta": arguments,
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_patch",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_patch",
                "name": "apply_patch",
            },
        }),
        b"data: [DONE]\n\n",
    ]


class ResponsesStreamRuntimeTests(unittest.TestCase):
    def _run_runtime(self, opener, web_runtime=None):
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=web_runtime or FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
        )
        runtime.run({
            "model": "QwenZhai-high",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "test"}],
            }],
            "tools": [{"type": "web_search"}],
        }, "QwenZhai-high")
        return b"".join(chunks).decode("utf-8")

    def test_web_search_call_is_public_and_upstream_resumes_with_hidden_output(self):
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_web"
                for item in body.get("input") or []
            )
            return FakeStream(_final_message_stream() if has_tool_output else _web_call_stream())

        stream_text = self._run_runtime(opener, web_runtime=web_runtime)

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(web_runtime.calls), 1)
        self.assertIn('"type": "web_search_call"', stream_text)
        self.assertIn("searched.", stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(any(item.get("type") == "function_call_output" for item in requests[1]["input"]))

    def test_apply_patch_call_is_rewritten_as_public_tool_item(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_apply_patch_call_stream())

        stream_text = self._run_runtime(opener)

        self.assertEqual(len(requests), 1)
        self.assertIn('"type": "apply_patch_call"', stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertIn("response.completed", stream_text)

    def test_golden_basic_message_stream_replays_unchanged(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)

        self.assertEqual(len(requests), 1)
        self.assertEqual([event for event, _payload in events].count("response.created"), 1)
        self.assertIn("stream ok", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))

    def test_golden_web_search_stream_replays_with_continuation(self):
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            fixture = "web_search_final.raw" if has_tool_output else "web_search_call.raw"
            return FakeStream(_fixture_chunks(fixture))

        stream_text = self._run_runtime(opener, web_runtime=web_runtime)
        events = _parse_sse_events(stream_text)
        output_indexes = [
            payload.get("output_index")
            for _event, payload in events
            if isinstance(payload, dict) and isinstance(payload.get("output_index"), int)
        ]
        completed = next(
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(web_runtime.calls), 1)
        self.assertIn('"type": "web_search_call"', stream_text)
        self.assertIn("searched.", stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertIn(1, output_indexes)
        self.assertEqual(completed["model"], "QwenZhai-high")
        self.assertEqual(completed["output"][0]["type"], "web_search_call")


if __name__ == "__main__":
    unittest.main()
