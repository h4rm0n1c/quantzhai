import json
import unittest

from proxy.qz_sse import _normalize_response_usage, make_response_stream_events, transform_sse_event


def _event(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return [
        f"event: {event_type}\n".encode("utf-8"),
        f"data: {json.dumps(payload)}\n".encode("utf-8"),
        b"\n",
    ]


class SseTests(unittest.TestCase):
    def test_usage_normalizer_maps_legacy_and_detail_fields(self):
        usage = _normalize_response_usage({
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 9},
            "completion_tokens_details": {"reasoning_tokens": 3},
        })

        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 4)
        self.assertEqual(usage["total_tokens"], 16)
        self.assertEqual(usage["input_tokens_details"]["cached_tokens"], 9)
        self.assertEqual(usage["output_tokens_details"]["reasoning_tokens"], 3)

    def test_usage_normalizer_maps_llamacpp_token_fields(self):
        usage = _normalize_response_usage({
            "prompt_eval_count": 7,
            "eval_count": 5,
            "cached_tokens": 6,
            "reasoning_tokens": 2,
        })

        self.assertEqual(usage["input_tokens"], 7)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertEqual(usage["total_tokens"], 12)
        self.assertEqual(usage["input_tokens_details"]["cached_tokens"], 6)
        self.assertEqual(usage["output_tokens_details"]["reasoning_tokens"], 2)

    def test_completed_event_usage_is_normalized_for_status_clients(self):
        chunks = transform_sse_event(
            _event("response.completed", {
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "prompt_tokens_details": {"cached_tokens": 8},
                        "completion_tokens_details": {"reasoning_tokens": 2},
                    },
                },
            }),
            set(),
            "summary",
        )

        payload = json.loads(b"".join(chunks).decode("utf-8").split("data: ", 1)[1])
        usage = payload["response"]["usage"]
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 3)
        self.assertEqual(usage["total_tokens"], 13)
        self.assertEqual(usage["input_tokens_details"]["cached_tokens"], 8)
        self.assertEqual(usage["output_tokens_details"]["reasoning_tokens"], 2)

    def test_response_stream_synthesizes_message_events(self):
        out = {
            "id": "resp_1",
            "model": "test-model.gguf",
            "output": [{
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            }],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3},
        }

        stream = b"".join(make_response_stream_events(out)).decode("utf-8")

        self.assertIn("event: response.created", stream)
        self.assertIn("event: response.output_text.delta", stream)
        self.assertIn('"delta": "done"', stream)
        self.assertIn('"input_tokens": 2', stream)
        self.assertIn('"output_tokens": 3', stream)
        self.assertTrue(stream.endswith("data: [DONE]\n\n"))

    def test_summary_mode_maps_reasoning_text_delta(self):
        chunks = transform_sse_event(
            _event("response.reasoning_text.delta", {
                "item_id": "rs_1",
                "output_index": 0,
                "content_index": 0,
                "delta": "thinking",
            }),
            set(),
            "summary",
        )
        stream = b"".join(chunks).decode("utf-8")

        self.assertIn("response.reasoning_summary_part.added", stream)
        self.assertIn("response.reasoning_summary_text.delta", stream)
        self.assertIn('"delta": "thinking"', stream)

    def test_hidden_mode_drops_reasoning_items(self):
        chunks = transform_sse_event(
            _event("response.output_item.added", {
                "output_index": 0,
                "item": {"id": "rs_1", "type": "reasoning"},
            }),
            set(),
            "hidden",
        )

        self.assertEqual(chunks, [])


if __name__ == "__main__":
    unittest.main()
