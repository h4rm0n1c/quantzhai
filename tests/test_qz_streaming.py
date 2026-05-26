import json
import unittest

from proxy.qz_streaming import (
    StreamedFunctionCallAssembler,
    custom_tool_call_input_events,
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

    def test_web_search_call_no_fake_lifecycle_events(self):
        """web_search_call_lifecycle_event was removed (issue #66).

        Codex source does not parse response.web_search_call.* subevents.
        web_search is exposed via output_item.added / output_item.done only.
        See docs/codex-source-tool-contract.md.
        """
        import proxy.qz_streaming as streaming_mod
        self.assertFalse(
            hasattr(streaming_mod, "web_search_call_lifecycle_event"),
            "web_search_call_lifecycle_event must not exist — removed in issue #66",
        )
        self.assertFalse(
            hasattr(streaming_mod, "public_tool_lifecycle_event"),
            "public_tool_lifecycle_event must not exist — removed in issue #66",
        )

    def test_custom_tool_call_input_events_emit_delta_only(self):
        """custom_tool_call_input.delta event for apply_patch streaming.

        Codex parses response.custom_tool_call_input.delta → ToolCallInputDelta.
        Current Codex does not parse response.custom_tool_call_input.done.
        Source: codex-rs/codex-api/src/sse/responses.rs:314.
        See docs/codex-source-tool-contract.md.
        """
        patch_text = "*** Begin Patch\n*** Add File: test.md\n+hello\n*** End Patch\n"
        item = {
            "id": "ctc_1",
            "type": "custom_tool_call",
            "call_id": "call_ap_1",
            "name": "apply_patch",
            "input": patch_text,
        }
        chunks, sequence = custom_tool_call_input_events(item, output_index=1, sequence_start=5)
        stream = b"".join(chunks).decode("utf-8")

        self.assertEqual(sequence, 6)  # 5 + 1 (delta) = 6
        self.assertIn("response.custom_tool_call_input.delta", stream)
        self.assertNotIn("response.custom_tool_call_input.done", stream)
        # patch text is JSON-encoded in SSE stream — newlines become \\n
        self.assertIn("Begin Patch", stream)

        # Parse individual events
        lines = stream.strip().split("\n\n")
        self.assertEqual(len(lines), 1)

        delta_lines = [l.encode("utf-8") + b"\n" for l in lines[0].split("\n")]
        et_delta, pd_delta = parse_sse_event_lines(delta_lines)

        self.assertEqual(et_delta, "response.custom_tool_call_input.delta")
        self.assertEqual(pd_delta["delta"], patch_text)
        self.assertEqual(pd_delta["call_id"], "call_ap_1")

    def test_custom_tool_call_input_events_preserves_call_id(self):
        item = {
            "id": "ctc_2",
            "type": "custom_tool_call",
            "call_id": "call_ap_2",
            "name": "apply_patch",
            "input": "*** Begin Patch\n*** Delete File: old.md\n*** End Patch\n",
        }
        chunks, _ = custom_tool_call_input_events(item, output_index=0, sequence_start=0)
        stream = b"".join(chunks).decode("utf-8")
        self.assertIn('"call_id": "call_ap_2"', stream)

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
            model="test-model.gguf",
        )

        self.assertEqual(event_type, "response.completed")
        self.assertEqual(payload["output_index"], 2)
        self.assertEqual(payload["response"]["model"], "test-model.gguf")
        self.assertEqual(payload["response"]["output"][0]["type"], "web_search_call")


if __name__ == "__main__":
    unittest.main()
