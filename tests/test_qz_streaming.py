import json
import unittest

from proxy.qz_streaming import (
    StreamedFunctionCallAssembler,
    is_function_call_stream_event,
    is_terminal_stream_event,
    parse_sse_event_lines,
    public_tool_item_events,
    rewrite_sse_payload,
)


def _event(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return [
        f"event: {event_type}\n".encode("utf-8"),
        f"data: {json.dumps(payload)}\n".encode("utf-8"),
        b"\n",
    ]


class StreamingStateTests(unittest.TestCase):
    def test_parse_sse_event_lines_reads_type_and_payload(self):
        event_type, payload = parse_sse_event_lines(_event("response.output_text.delta", {"delta": "ok"}))

        self.assertEqual(event_type, "response.output_text.delta")
        self.assertEqual(payload["delta"], "ok")

    def test_parse_sse_event_lines_handles_done_marker(self):
        event_type, payload = parse_sse_event_lines([b"data: [DONE]\n", b"\n"])

        self.assertEqual(event_type, "done")
        self.assertEqual(payload, "[DONE]")

    def test_function_call_assembler_joins_argument_deltas(self):
        assembler = StreamedFunctionCallAssembler()

        self.assertEqual(assembler.observe("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_1",
                "name": "web_search",
                "arguments": "",
            },
        }), [])
        assembler.observe("response.function_call_arguments.delta", {
            "item_id": "fc_1",
            "output_index": 0,
            "delta": "{\"action\":\"search\",",
        })
        assembler.observe("response.function_call_arguments.delta", {
            "item_id": "fc_1",
            "output_index": 0,
            "delta": "\"query\":\"quantzhai\"}",
        })
        completed = assembler.observe("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "web_search",
            },
        })

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["call_id"], "call_1")
        self.assertEqual(completed[0]["name"], "web_search")
        self.assertEqual(completed[0]["arguments"], "{\"action\":\"search\",\"query\":\"quantzhai\"}")
        self.assertEqual(completed[0]["output_index"], 0)

    def test_function_call_done_arguments_override_deltas(self):
        assembler = StreamedFunctionCallAssembler()
        assembler.observe("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "apply_patch",
            },
        })
        assembler.observe("response.function_call_arguments.delta", {
            "item_id": "fc_1",
            "delta": "partial",
        })
        assembler.observe("response.function_call_arguments.done", {
            "item_id": "fc_1",
            "name": "apply_patch",
            "arguments": "{\"cmd\":\"ok\"}",
        })
        completed = assembler.observe("response.output_item.done", {
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "apply_patch",
            },
        })

        self.assertEqual(completed[0]["arguments"], "{\"cmd\":\"ok\"}")

    def test_function_call_event_detection(self):
        self.assertTrue(is_function_call_stream_event("response.function_call_arguments.delta", {"delta": "{}"}))
        self.assertTrue(is_function_call_stream_event("response.output_item.done", {
            "item": {"type": "function_call"},
        }))
        self.assertFalse(is_function_call_stream_event("response.output_item.done", {
            "item": {"type": "message"},
        }))

    def test_terminal_event_detection(self):
        self.assertTrue(is_terminal_stream_event("response.completed", {"type": "response.completed"}))
        self.assertTrue(is_terminal_stream_event("done", "[DONE]"))
        self.assertFalse(is_terminal_stream_event("response.output_text.delta", {"delta": "ok"}))

    def test_public_tool_item_events_emit_added_and_done(self):
        chunks, sequence = public_tool_item_events({
            "id": "wsc_1",
            "type": "web_search_call",
            "status": "completed",
            "action": {"type": "search", "queries": ["quantzhai"]},
        }, output_index=2, sequence_start=10)
        stream = b"".join(chunks).decode("utf-8")

        self.assertEqual(sequence, 12)
        self.assertIn("response.output_item.added", stream)
        self.assertIn("response.output_item.done", stream)
        self.assertIn('"type": "web_search_call"', stream)

    def test_rewrite_sse_payload_offsets_output_index_and_prepends_output(self):
        event_type, payload = rewrite_sse_payload(
            "response.completed",
            {
                "type": "response.completed",
                "output_index": 0,
                "response": {
                    "model": "old",
                    "output": [{"type": "message", "id": "msg_1"}],
                },
            },
            output_index_offset=2,
            prepend_output=[{"type": "web_search_call", "id": "wsc_1"}],
            model="Qwen3.6Turbo",
        )

        self.assertEqual(event_type, "response.completed")
        self.assertEqual(payload["output_index"], 2)
        self.assertEqual(payload["response"]["model"], "Qwen3.6Turbo")
        self.assertEqual(payload["response"]["output"][0]["type"], "web_search_call")


if __name__ == "__main__":
    unittest.main()
