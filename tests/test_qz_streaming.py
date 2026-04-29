import json
import unittest

from proxy.qz_streaming import StreamedFunctionCallAssembler, parse_sse_event_lines


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


if __name__ == "__main__":
    unittest.main()
