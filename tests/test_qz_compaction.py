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
    _resolve_compact_context_window,
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


class RemoteCompactionEndpointShapeTests(unittest.TestCase):
    """Unit tests for the /v1/responses/compact response shape (Stage 6.10.1).

    These tests validate that _build_local_compaction_response() produces output
    compatible with Codex's CompactHistoryResponse { output: Vec<ResponseItem> }
    parser, which only reads the "output" field and ignores extra top-level keys.
    """

    def _make_history(self):
        return [
            _msg("user", "hello world"),
            _msg("assistant", "I can help with that."),
            _msg("user", "please summarise the earlier discussion"),
            _msg("assistant", "Earlier we discussed X, Y, Z."),
        ] * 3  # enough history to trigger real compaction

    def test_response_has_output_list(self):
        """Response must have an 'output' key containing a list."""
        body = {"input": self._make_history(), "model": "test-model"}
        result = _build_local_compaction_response(body)
        self.assertIn("output", result)
        self.assertIsInstance(result["output"], list)

    def test_output_not_empty(self):
        """Output list must not be empty — at least the compaction blob item."""
        body = {"input": self._make_history(), "model": "test-model"}
        result = _build_local_compaction_response(body)
        self.assertGreater(len(result["output"]), 0)

    def test_output_contains_compaction_item(self):
        """Output must include exactly one compaction item with encrypted_content."""
        body = {"input": self._make_history(), "model": "test-model"}
        result = _build_local_compaction_response(body)
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        self.assertEqual(len(cmp_items), 1)
        self.assertIn("encrypted_content", cmp_items[0])
        self.assertTrue(cmp_items[0]["encrypted_content"].startswith("localcmp:"))

    def test_compaction_item_blob_is_decodable(self):
        """The compaction blob must decode to a valid payload."""
        body = {"input": self._make_history(), "model": "test-model"}
        result = _build_local_compaction_response(body)
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertIsNotNone(payload)
        self.assertIn("summary_text", payload)
        self.assertIn("version", payload)

    def test_extra_top_level_keys_present_and_output_key_survives(self):
        """Response may include extra keys (id, object, usage) — Codex ignores them."""
        body = {"input": self._make_history(), "model": "test-model"}
        result = _build_local_compaction_response(body)
        # Codex only reads "output" — extra fields must not replace it
        self.assertIn("output", result)
        # These extra fields are expected and should not cause parse errors on the Codex side
        for extra_key in ("id", "object", "created_at", "usage"):
            self.assertIn(extra_key, result)

    def test_compact_input_with_compaction_instruction_field(self):
        """CompactionInput includes 'instructions' field — must not crash."""
        body = {
            "model": "test-model",
            "input": self._make_history(),
            "instructions": "Summarise the conversation, preserving key facts.",
            "tools": [],
            "parallel_tool_calls": False,
        }
        result = _build_local_compaction_response(body)
        self.assertIn("output", result)
        self.assertIsInstance(result["output"], list)

    def test_compact_input_empty_history_returns_output(self):
        """Empty input list must return valid output shape, not crash."""
        body = {"model": "test-model", "input": [], "tools": []}
        result = _build_local_compaction_response(body)
        self.assertIn("output", result)
        self.assertIsInstance(result["output"], list)

    def test_compact_input_string_model_field_accepted(self):
        """The 'model' field in CompactionInput is a string — must be accepted."""
        body = {
            "model": "Qwen3.6Turbo-27B",
            "input": self._make_history(),
        }
        result = _build_local_compaction_response(body)
        self.assertIn("output", result)

    def test_selected_context_tokens_passed_to_budget_resolver(self):
        """selected_context_tokens=256k passes through without error (v3 fallback to v2 ok)."""
        body = {"input": self._make_history(), "model": "test-model"}
        # Will fail_v3 (no LLM backend in unit test) but must not raise
        result = _build_local_compaction_response(body, selected_context_tokens=262144)
        self.assertIn("output", result)
        self.assertIsInstance(result["output"], list)


class RemoteCompactionSourceSelectionTests(unittest.TestCase):
    """Stage 6.10.3 — remote_compaction=True uses all working_items as LLM source."""

    VALID_SUMMARY = """## Goal
Complete Stage 6.10.3.

## Active Constraints & Guardrails
- remote_compaction uses all items as source.

## Current Status
### Done
- Source selection fixed.

## Key Decisions
- Decision: use working_items in remote mode.

## Evidence Boundaries
- Confirmed by unit tests.

## Technical State
### Files / Paths
- proxy/qz_responses.py

## Next Actions
1. Verify smoke returns v3.
"""

    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        COMPACTION_CONFIG["mode"] = "llm"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        COMPACTION_CONFIG["llm_model"] = "test-model"

    def tearDown(self):
        for k, v in self.original_config.items():
            COMPACTION_CONFIG[k] = v

    def _make_small_history(self):
        """Only 2 messages — well below keep_recent_items (20), so older is empty
        under normal compaction but all items are used under remote_compaction."""
        return [
            _msg("user", "Describe Paris."),
            _msg("assistant", "Paris is the capital of France."),
        ]

    def _mock_llm(self, mock_urlopen):
        mock_resp = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": self.VALID_SUMMARY}}]}
        ).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

    # ── Remote compact source selection ──────────────────────────────────────

    @unittest.mock.patch("urllib.request.urlopen")
    def test_remote_compact_with_2_messages_calls_llm(self, mock_urlopen):
        """remote_compaction=True must not skip LLM even with only 2 messages."""
        self._mock_llm(mock_urlopen)
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=True
        )
        # LLM should have been called — remote mode passes all items as source
        mock_urlopen.assert_called_once()

    @unittest.mock.patch("urllib.request.urlopen")
    def test_remote_compact_with_2_messages_returns_v3(self, mock_urlopen):
        """remote_compaction=True with a valid LLM response must produce localcmp:v3."""
        self._mock_llm(mock_urlopen)
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=True
        )
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        self.assertEqual(len(cmp_items), 1)
        self.assertTrue(
            cmp_items[0]["encrypted_content"].startswith("localcmp:v3:"),
            f"Expected localcmp:v3, got: {cmp_items[0]['encrypted_content'][:30]}",
        )

    @unittest.mock.patch("urllib.request.urlopen")
    def test_normal_compact_with_2_messages_falls_back_to_v2(self, mock_urlopen):
        """Normal compaction with 2 messages must not call LLM (older is empty).

        This preserves the existing behaviour: for small histories under
        keep_recent_items, the normal path keeps all items in 'recent' and
        has nothing to summarise in 'older', so v3 falls back to v2.
        """
        self._mock_llm(mock_urlopen)
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=False
        )
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        self.assertEqual(len(cmp_items), 1)
        blob = cmp_items[0]["encrypted_content"]
        self.assertTrue(
            blob.startswith("localcmp:v2:"),
            f"Expected v2 fallback for small normal history, got: {blob[:30]}",
        )
        # LLM must NOT have been called
        mock_urlopen.assert_not_called()

    # ── v3 fallback reason recorded in v2 metadata ───────────────────────────

    def test_normal_compact_v3_fallback_reason_is_empty_llm_source_items(self):
        """v2 blob for normal compact with 2-message history records the v3 reason."""
        COMPACTION_CONFIG["mode"] = "auto"
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=False
        )
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertEqual(
            payload["metadata"].get("v3_fallback_reason"),
            "empty_llm_source_items",
        )

    def test_v2_fallback_reason_is_missing_context_window_when_no_tokens(self):
        """v2 blob records 'missing_context_window' when selected_context_tokens=None."""
        COMPACTION_CONFIG["mode"] = "auto"
        big_history = [_msg("user", "hello " * 50), _msg("assistant", "world " * 50)] * 15
        body = {"input": big_history, "model": "test-model"}
        result = _build_local_compaction_response(body, selected_context_tokens=None)
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertEqual(
            payload["metadata"].get("v3_fallback_reason"),
            "missing_context_window",
        )

    def test_v2_fallback_reason_is_mode_not_llm_or_auto_for_heuristic_mode(self):
        """Heuristic mode produces v2 with reason 'mode_not_llm_or_auto'."""
        COMPACTION_CONFIG["mode"] = "heuristic"
        big_history = [_msg("user", "hello " * 50), _msg("assistant", "world " * 50)] * 15
        body = {"input": big_history, "model": "test-model"}
        result = _build_local_compaction_response(body, selected_context_tokens=262144)
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertEqual(
            payload["metadata"].get("v3_fallback_reason"),
            "mode_not_llm_or_auto",
        )

    @unittest.mock.patch("urllib.request.urlopen")
    def test_remote_compact_v3_metadata_records_remote_compaction_true(self, mock_urlopen):
        """v3 blob from remote_compaction=True records remote_compaction: True in metadata."""
        self._mock_llm(mock_urlopen)
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=True
        )
        cmp_items = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertEqual(payload["version"], 3)
        self.assertTrue(payload["metadata"].get("remote_compaction"))

    # ── remote compact output shape ───────────────────────────────────────────

    @unittest.mock.patch("urllib.request.urlopen")
    def test_remote_compact_output_is_only_compaction_blob(self, mock_urlopen):
        """Remote compact output must be just the compaction blob (no recent tail)."""
        self._mock_llm(mock_urlopen)
        body = {"input": self._make_small_history(), "model": "test-model"}
        result = _build_local_compaction_response(
            body, selected_context_tokens=262144, remote_compaction=True
        )
        # In remote mode all items are summarised; no recent tail preserved.
        non_cmp = [
            item for item in result["output"]
            if isinstance(item, dict) and item.get("type") != "compaction"
        ]
        self.assertEqual(non_cmp, [], "Remote compact output must not contain non-compaction items")


import unittest.mock  # noqa: E402 — needed for @unittest.mock.patch decorators above


class _MockCatalog:
    """Minimal mock for ModelCatalog sufficient to test _resolve_compact_context_window."""

    def __init__(self, entries=None, selected=None, resolve_result=None):
        self.entries = entries or []
        self.selected = selected
        self._resolve_result = resolve_result  # (entry_or_None, reason_str) tuple

    def resolve(self, query=None):
        if self._resolve_result is not None:
            return self._resolve_result
        # Default: try to find by stem/label match
        for e in self.entries:
            if isinstance(e, dict) and (
                e.get("stem") == query or e.get("label") == query or e.get("key") == query
            ):
                return e, f"matched {query}"
        return None, f"no match for {query}"


def _make_entry(stem, context_length=None, runtime_context_length=None):
    entry = {
        "key": f"{stem}.gguf",
        "filename": f"{stem}.gguf",
        "stem": stem,
        "label": stem,
        "name": stem,
        "context_length": context_length,
        "runtime_context_length": runtime_context_length,
    }
    return entry


class ContextWindowResolutionTests(unittest.TestCase):
    """Stage 6.10.4 — _resolve_compact_context_window() uses correct catalog fields."""

    # ── Field name correctness ────────────────────────────────────────────────

    def test_uses_runtime_context_length_first(self):
        """runtime_context_length takes priority over context_length."""
        entry = _make_entry("Qwen3.6-27B", context_length=131072, runtime_context_length=262144)
        catalog = _MockCatalog(entries=[entry], selected=entry)
        ctx, reason, label = _resolve_compact_context_window(catalog, "")
        self.assertEqual(ctx, 262144)

    def test_falls_back_to_context_length(self):
        """context_length used when runtime_context_length is absent/zero."""
        entry = _make_entry("Qwen3.6-27B", context_length=131072, runtime_context_length=None)
        catalog = _MockCatalog(entries=[entry], selected=entry)
        ctx, reason, label = _resolve_compact_context_window(catalog, "")
        self.assertEqual(ctx, 131072)

    def test_does_not_use_context_window_field(self):
        """context_window field (Codex catalog only) must NOT be used — catalog entries don't have it."""
        entry = _make_entry("Qwen3.6-27B", context_length=None, runtime_context_length=None)
        entry["context_window"] = 999999  # wrong field — should be ignored
        catalog = _MockCatalog(entries=[entry], selected=entry)
        ctx, reason, _ = _resolve_compact_context_window(catalog, "")
        self.assertIsNone(ctx)
        self.assertEqual(reason, "selected_model_no_context_window")

    # ── Resolution order ─────────────────────────────────────────────────────

    def test_query_slug_resolves_by_query(self):
        """When client_model matches a catalog entry, reason is 'resolved_by_query'."""
        slug = "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"
        entry = _make_entry(slug, runtime_context_length=262144)
        catalog = _MockCatalog(
            entries=[entry],
            selected=None,
            resolve_result=(entry, f"matched {slug}"),
        )
        ctx, reason, label = _resolve_compact_context_window(catalog, slug)
        self.assertEqual(ctx, 262144)
        self.assertEqual(reason, "resolved_by_query")

    def test_falls_back_to_selected_when_query_misses(self):
        """Falls back to catalog.selected when query does not match any entry."""
        selected_entry = _make_entry("default-model", runtime_context_length=131072)
        catalog = _MockCatalog(
            entries=[selected_entry],
            selected=selected_entry,
            resolve_result=(None, "no match for wrong-slug"),
        )
        ctx, reason, label = _resolve_compact_context_window(catalog, "wrong-slug")
        self.assertEqual(ctx, 131072)
        self.assertEqual(reason, "resolved_from_selected")

    def test_falls_back_to_first_entry_when_no_selected(self):
        """Falls back to entries[0] when neither query nor selected resolves."""
        entry = _make_entry("only-model", context_length=65536)
        catalog = _MockCatalog(
            entries=[entry],
            selected=None,
            resolve_result=(None, "no match"),
        )
        ctx, reason, label = _resolve_compact_context_window(catalog, "unknown")
        self.assertEqual(ctx, 65536)
        self.assertEqual(reason, "resolved_from_first_entry")

    # ── Failure paths ─────────────────────────────────────────────────────────

    def test_catalog_empty_returns_catalog_empty_reason(self):
        catalog = _MockCatalog(entries=[], selected=None)
        ctx, reason, label = _resolve_compact_context_window(catalog, "any")
        self.assertIsNone(ctx)
        self.assertEqual(reason, "catalog_empty")
        self.assertIsNone(label)

    def test_entry_with_no_context_length_returns_no_context_window_reason(self):
        entry = _make_entry("bare-entry", context_length=None, runtime_context_length=None)
        catalog = _MockCatalog(entries=[entry], selected=entry)
        ctx, reason, label = _resolve_compact_context_window(catalog, "")
        self.assertIsNone(ctx)
        self.assertEqual(reason, "selected_model_no_context_window")
        self.assertEqual(label, "bare-entry")

    def test_catalog_exception_returns_catalog_exception_reason(self):
        class BrokenCatalog:
            @property
            def entries(self):
                raise RuntimeError("disk failure")
        ctx, reason, label = _resolve_compact_context_window(BrokenCatalog(), "any")
        self.assertIsNone(ctx)
        self.assertTrue(reason.startswith("catalog_exception:"), reason)
        self.assertIsNone(label)

    def test_zero_context_length_treated_as_missing(self):
        """Zero context_length is not a valid window — treated as missing."""
        entry = _make_entry("zero-ctx", context_length=0, runtime_context_length=0)
        catalog = _MockCatalog(entries=[entry], selected=entry)
        ctx, reason, _ = _resolve_compact_context_window(catalog, "")
        self.assertIsNone(ctx)
        self.assertEqual(reason, "selected_model_no_context_window")

    # ── Integration: context_resolution_reason in v2 metadata ────────────────

    def test_context_resolution_reason_recorded_in_v2_metadata(self):
        """context_resolution_reason is stored in v2 blob metadata for diagnostics."""
        items = [_msg("user", "hello"), _msg("assistant", "world")]
        body = {"input": items, "model": "test"}
        result = _build_local_compaction_response(
            body,
            selected_context_tokens=None,  # forces missing_context_window
            remote_compaction=True,
            context_resolution_reason="model_not_found",
        )
        cmp_items = [
            i for i in result["output"]
            if isinstance(i, dict) and i.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertEqual(payload["metadata"].get("context_resolution_reason"), "model_not_found")

    def test_context_resolution_reason_none_when_not_set(self):
        """context_resolution_reason is None (not set) when not provided."""
        items = [_msg("user", "hello"), _msg("assistant", "world")]
        body = {"input": items, "model": "test"}
        result = _build_local_compaction_response(body, selected_context_tokens=None)
        cmp_items = [
            i for i in result["output"]
            if isinstance(i, dict) and i.get("type") == "compaction"
        ]
        payload = _decode_local_compaction_blob(cmp_items[0]["encrypted_content"])
        self.assertIsNone(payload["metadata"].get("context_resolution_reason"))


if __name__ == "__main__":
    unittest.main()
