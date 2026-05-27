import base64
import json
import sys
import unittest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy.qz_responses import (
    COMPACTION_CONFIG,
    LOCAL_COMPACTION_PREFIX,
    _build_local_compaction_response,
    _decode_local_compaction_blob,
    _encode_local_compaction_blob,
    _estimate_items_tokens,
    _expand_local_compaction_items,
    _make_input_text_message,
    _summarize_items_for_compaction,
)


def _msg(role, text):
    return _make_input_text_message(role, text)


def _make_v1_blob(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return "localcmp:v1:" + encoded


def _make_v3_blob(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return "localcmp:v3:" + encoded


def _make_compaction_item(blob):
    return {
        "type": "compaction",
        "id": "cmp_test_1",
        "created_by": "turboquant-local",
        "encrypted_content": blob,
    }


class EncodeDecodeTests(unittest.TestCase):
    def test_roundtrip_v2(self):
        payload = {"version": 2, "summary_text": "hello world", "depth": 1}
        blob = _encode_local_compaction_blob(payload)
        self.assertTrue(blob.startswith("localcmp:v2:"))
        decoded = _decode_local_compaction_blob(blob)
        self.assertEqual(decoded, payload)

    def test_decode_v1_backward_compat(self):
        payload = {"version": 1, "summary_text": "old summary", "depth": 1}
        blob = _make_v1_blob(payload)
        decoded = _decode_local_compaction_blob(blob)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["summary_text"], "old summary")

    def test_decode_returns_none_for_unknown_prefix(self):
        self.assertIsNone(_decode_local_compaction_blob("localcmp:v3:abc123"))

    def test_decode_returns_none_for_non_string(self):
        self.assertIsNone(_decode_local_compaction_blob(None))
        self.assertIsNone(_decode_local_compaction_blob(42))

    def test_decode_returns_none_for_corrupt_blob(self):
        self.assertIsNone(_decode_local_compaction_blob("localcmp:v2:!!!notbase64!!!"))

    def test_decode_returns_none_for_corrupt_v3_blob(self):
        self.assertIsNone(_decode_local_compaction_blob("localcmp:v3:!!!notbase64!!!"))

    def test_decode_returns_none_for_non_dict_payload(self):
        raw = json.dumps(["list", "not", "dict"]).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        blob = "localcmp:v2:" + encoded
        self.assertIsNone(_decode_local_compaction_blob(blob))


class EstimateTokensTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_estimate_items_tokens([]), 0)

    def test_single_message(self):
        items = [_msg("user", "hello world")]
        tokens = _estimate_items_tokens(items)
        self.assertGreater(tokens, 0)

    def test_multiple_messages_additive(self):
        one = _estimate_items_tokens([_msg("user", "hello")])
        two = _estimate_items_tokens([_msg("user", "hello"), _msg("assistant", "world")])
        self.assertGreater(two, one)

    def test_none_items(self):
        self.assertEqual(_estimate_items_tokens(None), 0)


class ExpandCompactionItemsTests(unittest.TestCase):
    def test_local_compaction_expands_to_text_message(self):
        payload = {"version": 2, "summary_text": "earlier work", "depth": 1}
        blob = _encode_local_compaction_blob(payload)
        items = [_make_compaction_item(blob), _msg("user", "follow-up")]
        expanded = _expand_local_compaction_items(items)
        types = [i.get("type") for i in expanded]
        self.assertNotIn("compaction", types)
        texts = [
            part.get("text", "")
            for item in expanded
            for part in item.get("content", [])
            if part.get("type") == "input_text"
        ]
        self.assertTrue(any("earlier work" in t for t in texts))
        self.assertEqual(expanded[-1], _msg("user", "follow-up"))

    def test_v1_compaction_expands(self):
        payload = {"version": 1, "summary_text": "v1 summary", "depth": 1}
        blob = _make_v1_blob(payload)
        items = [_make_compaction_item(blob)]
        expanded = _expand_local_compaction_items(items)
        types = [i.get("type") for i in expanded]
        self.assertNotIn("compaction", types)

    def test_v3_compaction_expands(self):
        payload = {"version": 3, "summary_text": "v3 anchored summary", "depth": 1}
        blob = _make_v3_blob(payload)
        items = [_make_compaction_item(blob)]
        expanded = _expand_local_compaction_items(items)
        self.assertEqual(expanded[0]["type"], "message")
        self.assertIn("v3 anchored summary", expanded[0]["content"][0]["text"])

    def test_local_compaction_without_summary_text_passes_through(self):
        payload = {"version": 3, "metadata": {"engine": "anchored-llm"}}
        item = _make_compaction_item(_make_v3_blob(payload))
        expanded = _expand_local_compaction_items([item])
        self.assertEqual(expanded, [item])

    def test_malformed_local_compaction_passes_through(self):
        item = _make_compaction_item("localcmp:v3:!!!notbase64!!!")
        expanded = _expand_local_compaction_items([item])
        self.assertEqual(expanded, [item])

    def test_native_compaction_passthrough(self):
        native = {
            "type": "compaction",
            "id": "cmp_native_1",
            "encrypted_content": "some-opaque-token-not-localcmp",
        }
        items = [native, _msg("user", "hi")]
        expanded = _expand_local_compaction_items(items)
        self.assertIn(native, expanded)

    def test_non_compaction_items_pass_through(self):
        items = [_msg("user", "a"), _msg("assistant", "b")]
        expanded = _expand_local_compaction_items(items)
        self.assertEqual(expanded, items)

    def test_empty_list(self):
        self.assertEqual(_expand_local_compaction_items([]), [])


class BuildCompactionResponseTests(unittest.TestCase):
    def _make_body(self, messages):
        return {"input": messages, "model": "qwen3.6"}

    def test_returns_response_compaction_object(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        self.assertEqual(result["object"], "response.compaction")
        self.assertIn("output", result)
        self.assertIn("usage", result)

    def test_output_starts_with_compaction_item(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        self.assertEqual(result["output"][0]["type"], "compaction")

    def test_compaction_blob_is_v2(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    def test_payload_has_metadata(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        blob = result["output"][0]["encrypted_content"]
        payload = _decode_local_compaction_blob(blob)
        self.assertIn("metadata", payload)
        self.assertEqual(payload["metadata"]["engine"], "qwen3.6-bridge")

    def test_depth_increments(self):
        # First compaction — depth should be 1
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        payload = _decode_local_compaction_blob(result["output"][0]["encrypted_content"])
        self.assertEqual(payload["depth"], 1)

    def test_depth_increments_on_recompaction(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result1 = _build_local_compaction_response(self._make_body(items))
        # Feed the compacted output back as the next request's input
        result2 = _build_local_compaction_response(self._make_body(result1["output"]))
        payload = _decode_local_compaction_blob(result2["output"][0]["encrypted_content"])
        self.assertEqual(payload["depth"], 2)

    def test_depth_capped_at_max(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        body = self._make_body(items)
        output = items
        for _ in range(COMPACTION_CONFIG["max_compaction_depth"] + 3):
            result = _build_local_compaction_response({"input": output, "model": "qwen3.6"})
            output = result["output"]
        payload = _decode_local_compaction_blob(output[0]["encrypted_content"])
        self.assertLessEqual(payload["depth"], COMPACTION_CONFIG["max_compaction_depth"])

    def test_preserves_recent_items(self):
        items = [_msg("user", f"turn {i}") for i in range(16)]
        result = _build_local_compaction_response(self._make_body(items))
        self.assertGreater(len(result["output"]), 1)

    def test_usage_keys_present(self):
        items = [_msg("user", f"turn {i}") for i in range(12)]
        result = _build_local_compaction_response(self._make_body(items))
        usage = result["usage"]
        self.assertIn("input_tokens", usage)
        self.assertIn("output_tokens", usage)
        self.assertIn("total_tokens", usage)
        self.assertEqual(usage["total_tokens"], usage["input_tokens"] + usage["output_tokens"])

    def test_string_input_handled(self):
        result = _build_local_compaction_response({"input": "plain text input", "model": "qwen3.6"})
        self.assertEqual(result["object"], "response.compaction")

    def test_empty_input_handled(self):
        result = _build_local_compaction_response({"input": [], "model": "qwen3.6"})
        self.assertEqual(result["object"], "response.compaction")


class SummarizeItemsTests(unittest.TestCase):
    def test_returns_string(self):
        items = [_msg("user", "question"), _msg("assistant", "answer")]
        summary = _summarize_items_for_compaction(items)
        self.assertIsInstance(summary, str)

    def test_uses_structured_markers(self):
        items = [_msg("user", "some content")]
        summary = _summarize_items_for_compaction(items)
        self.assertIn("<|history_summary|>", summary)
        self.assertIn("<|end_history_summary|>", summary)

    def test_empty_items_returns_empty(self):
        summary = _summarize_items_for_compaction([])
        self.assertEqual(summary, "")


if __name__ == "__main__":
    unittest.main()
