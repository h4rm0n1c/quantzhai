import unittest
import json
import base64
import os
import socket
import tempfile
import urllib.error
from unittest.mock import patch, MagicMock
from proxy.qz_responses import (
    _active_backend_base_url,
    _build_llm_compactor_payload,
    _DEFAULT_LLM_MAX_INPUT_CHARS,
    _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
    _DEFAULT_LLM_TIMEOUT_SEC,
    _build_local_compaction_response,
    _build_local_compaction_response_v3,
    _build_survival_weighted_compaction_prompt,
    _call_llm_compactor,
    _decode_local_compaction_blob,
    _expand_local_compaction_items,
    _extract_llm_compactor_content,
    _get_env_int,
    _is_probably_quantzhai_proxy_url,
    _normalize_compaction_mode,
    _resolve_llm_compaction_budget,
    _validate_anchored_summary,
    COMPACTION_CONFIG
)

VALID_SUMMARY = """## Goal
Complete Stage 4.1 compaction hardening.

## Active Constraints & Guardrails
- Keep localcmp:v3 opt-in.

## Current Status
### Done
- Runtime path hardened.

## Key Decisions
- Decision: use mocked backend tests only.

## Evidence Boundaries
- Confirmed by unit tests.

## Technical State
### Files / Paths
- proxy/qz_responses.py

## Next Actions
1. Run validation.
"""


class TestLLMCompaction(unittest.TestCase):

    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        # Set up a base config for testing
        COMPACTION_CONFIG["mode"] = "llm"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        COMPACTION_CONFIG["llm_model"] = "test-model"

    def tearDown(self):
        # Restore original config
        for k, v in self.original_config.items():
            COMPACTION_CONFIG[k] = v

    def _make_body(self, items):
        return {
            "input": items,
            "model": "qwen3.6",
            "context_management": {"compact_threshold": 10} # Trigger compaction
        }

    def _mock_backend_response(self, mock_urlopen, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

    def test_heuristic_mode_produces_v2(self):
        # Explicit heuristic mode must always produce v2, even with a backend URL.
        COMPACTION_CONFIG["mode"] = "heuristic"
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_llm_compaction_success_produces_v3(self, mock_urlopen):
        # Mock successful LLM response
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)
        
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v3:"))
        
        payload = _decode_local_compaction_blob(blob)
        self.assertEqual(payload["version"], 3)
        self.assertEqual(payload["engine"], "anchored-llm")
        self.assertIn("## Goal", payload["summary_text"])
        self.assertEqual(payload["schema_version"], "anchored-v0")
        self.assertEqual(payload["metadata"]["fallback"], False)
        self.assertEqual(payload["metadata"]["prompt"], "compact-v0")
        self.assertIn("survival_hint_count", payload["metadata"])

    @patch("urllib.request.urlopen")
    def test_llm_compaction_fallback_on_invalid_output(self, mock_urlopen):
        # Mock LLM response with missing headings
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": "Bad output without headings"}}]},
        )

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)

        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))
        payload = _decode_local_compaction_blob(blob)
        self.assertEqual(payload["metadata"]["v3_fallback_reason"], "invalid_summary")

    @patch("urllib.request.urlopen")
    def test_llm_compaction_fallback_on_error(self, mock_urlopen):
        # Mock LLM error
        mock_urlopen.side_effect = Exception("Connection error")

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)

        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))
        payload = _decode_local_compaction_blob(blob)
        self.assertEqual(payload["metadata"]["v3_fallback_reason"], "llm_failed")

    @patch("urllib.request.urlopen")
    def test_heuristic_mode_does_not_call_backend(self, mock_urlopen):
        COMPACTION_CONFIG["mode"] = "heuristic"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_llm_mode_without_base_url_falls_back_to_v2(self, mock_urlopen):
        COMPACTION_CONFIG["mode"] = "llm"
        COMPACTION_CONFIG["llm_base_url"] = ""
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_auto_mode_without_base_url_falls_back_to_v2(self, mock_urlopen):
        COMPACTION_CONFIG["mode"] = "auto"
        COMPACTION_CONFIG["llm_base_url"] = ""
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_auto_mode_with_valid_backend_produces_v3(self, mock_urlopen):
        COMPACTION_CONFIG["mode"] = "auto"
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v3:"))

    @patch("urllib.request.urlopen")
    def test_invalid_mode_falls_back_to_v2(self, mock_urlopen):
        COMPACTION_CONFIG["mode"] = "surprise"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_timeout_falls_back_to_v2(self, mock_urlopen):
        mock_urlopen.side_effect = socket.timeout("timed out")
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_url_error_falls_back_to_v2(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_invalid_backend_json_falls_back_to_v2(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"{not-json"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50

        result = _build_local_compaction_response(self._make_body(items))

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))

    def test_invalid_env_int_values_fall_back_safely(self):
        with patch.dict(os.environ, {"BAD_INT": "not-int", "ZERO_INT": "0", "NEG_INT": "-5"}):
            self.assertEqual(_get_env_int("BAD_INT", 30), 30)
            self.assertEqual(_get_env_int("ZERO_INT", 30), 30)
            self.assertEqual(_get_env_int("NEG_INT", 30), 30)

    def test_invalid_mode_normalizes_to_heuristic(self):
        self.assertEqual(_normalize_compaction_mode("llm"), "llm")
        self.assertEqual(_normalize_compaction_mode("AUTO"), "auto")
        self.assertEqual(_normalize_compaction_mode(""), "heuristic")
        self.assertEqual(_normalize_compaction_mode("invalid"), "heuristic")

    @patch("urllib.request.urlopen")
    def test_negative_llm_config_values_use_defaults(self, mock_urlopen):
        COMPACTION_CONFIG["llm_timeout_sec"] = -1
        COMPACTION_CONFIG["llm_max_output_tokens"] = 0
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )

        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")

        self.assertEqual(reason, "ok")
        self.assertEqual(content, VALID_SUMMARY.strip())
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], _DEFAULT_LLM_TIMEOUT_SEC)

    @patch("urllib.request.urlopen")
    def test_recursive_proxy_url_is_rejected_without_network_call(self, mock_urlopen):
        COMPACTION_CONFIG["llm_base_url"] = "http://127.0.0.1:18180"
        with patch.dict(os.environ, {"CODEX_OSS_BASE_URL": "http://127.0.0.1:18180/v1"}):
            content, reason = _call_llm_compactor("prompt")
        self.assertIsNone(content)
        self.assertEqual(reason, "proxy_url_rejected")
        mock_urlopen.assert_not_called()

    def test_proxy_url_guard_matches_known_proxy_env(self):
        env = {
            "CODEX_OSS_BASE_URL": "http://127.0.0.1:18180/v1",
            "QZ_PROXY_BASE_URL": "",
        }
        self.assertTrue(_is_probably_quantzhai_proxy_url("http://127.0.0.1:18180", env=env))
        self.assertFalse(_is_probably_quantzhai_proxy_url("http://127.0.0.1:8080", env=env))

    @patch("urllib.request.urlopen")
    def test_response_shape_chat_completions(self, mock_urlopen):
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": " chat content "}}]},
        )
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "chat content")

    @patch("urllib.request.urlopen")
    def test_response_shape_legacy_completions(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"choices": [{"text": " legacy text "}]})
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "legacy text")

    def test_response_shape_reasoning_content_alone_is_ignored(self):
        self.assertIsNone(
            _extract_llm_compactor_content(
                {"choices": [{"message": {"reasoning_content": "## Goal\nhidden"}}]}
            )
        )

    def test_response_shape_message_content_wins_over_reasoning_content(self):
        content = _extract_llm_compactor_content(
            {
                "choices": [
                    {
                        "message": {
                            "content": " visible summary ",
                            "reasoning_content": "hidden summary",
                        }
                    }
                ]
            }
        )

        self.assertEqual(content, "visible summary")

    @patch("urllib.request.urlopen")
    def test_response_shape_simple_content(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"content": " simple content "})
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "simple content")

    @patch("urllib.request.urlopen")
    def test_response_shape_simple_response(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"response": " simple response "})
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "simple response")

    @patch("urllib.request.urlopen")
    def test_response_shape_non_string_returns_none(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"choices": [{"message": {"content": ["no"]}}]})
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertIsNone(content)
        self.assertEqual(reason, "empty_content")

    def test_compactor_payload_requests_final_content_and_disables_reasoning(self):
        payload = _build_llm_compactor_payload("prompt")
        system_text = payload["messages"][0]["content"]

        self.assertIn("message.content", system_text)
        self.assertIn("reasoning_content", system_text)
        self.assertEqual(payload["thinking_budget_tokens"], 0)
        self.assertEqual(payload["reasoning_budget_tokens"], 0)

    def test_compactor_payload_can_leave_reasoning_budget_fields_out(self):
        COMPACTION_CONFIG["llm_disable_reasoning"] = False

        payload = _build_llm_compactor_payload("prompt")

        self.assertNotIn("thinking_budget_tokens", payload)
        self.assertNotIn("reasoning_budget_tokens", payload)

    def test_prompt_includes_previous_summary_new_text_and_survival_hints(self):
        COMPACTION_CONFIG["prompt_file"] = "/tmp/qz-no-such-compact-template.md"
        COMPACTION_CONFIG["llm_max_input_chars"] = 2000
        previous = "## Goal\nPrevious summary"
        items = [{"type": "message", "role": "user", "content": "Run bash -n scripts/qz-up and keep DOCKER_BUILDKIT=1."}]

        prompt = _build_survival_weighted_compaction_prompt(previous, items)

        self.assertIn(previous, prompt)
        self.assertIn("Run bash -n scripts/qz-up", prompt)
        self.assertIn("### Preservation Hints", prompt)
        self.assertIn("DOCKER_BUILDKIT=1", prompt)

    def test_prompt_caps_raw_conversation_but_keeps_hints(self):
        COMPACTION_CONFIG["prompt_file"] = "/tmp/qz-no-such-compact-template.md"
        COMPACTION_CONFIG["llm_max_input_chars"] = 500
        giant = "tool output " + ("RAW_TOOL_OUTPUT " * 1000) + " keep DOCKER_BUILDKIT=1"
        items = [{"type": "message", "role": "assistant", "content": giant}]

        prompt = _build_survival_weighted_compaction_prompt("", items)

        self.assertLess(len(prompt), 900)
        self.assertLess(prompt.count("RAW_TOOL_OUTPUT"), 40)
        self.assertIn("### Preservation Hints", prompt)
        self.assertIn("DOCKER_BUILDKIT=1", prompt)

    def test_missing_prompt_file_uses_safe_fallback_template(self):
        COMPACTION_CONFIG["prompt_file"] = "/tmp/qz-no-such-compact-template.md"
        items = [{"type": "message", "role": "user", "content": "new content"}]

        prompt = _build_survival_weighted_compaction_prompt("previous content", items)

        self.assertIn("Previous anchored summary", prompt)
        self.assertIn("previous content", prompt)
        self.assertIn("new content", prompt)

    def test_prompt_file_template_is_used_when_available(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as tmp:
            tmp.write("P={{PREVIOUS_ANCHORED_SUMMARY}}\nN={{NEW_CONVERSATION}}")
            tmp.flush()
            COMPACTION_CONFIG["prompt_file"] = tmp.name
            prompt = _build_survival_weighted_compaction_prompt("prev", [{"type": "message", "role": "user", "content": "new"}])
        self.assertIn("P=prev", prompt)
        self.assertIn("N=user: new", prompt)

    def test_validate_anchored_summary_accepts_required_headings(self):
        self.assertTrue(_validate_anchored_summary(VALID_SUMMARY))

    def test_validate_anchored_summary_rejects_empty_or_too_short(self):
        self.assertFalse(_validate_anchored_summary(""))
        self.assertFalse(_validate_anchored_summary("## Goal\nx"))

    def test_validate_anchored_summary_rejects_markdown_fence_wrapper(self):
        self.assertFalse(_validate_anchored_summary(f"```markdown\n{VALID_SUMMARY}\n```"))

    def test_validate_anchored_summary_rejects_raw_placeholders(self):
        self.assertFalse(_validate_anchored_summary(VALID_SUMMARY + "\n{{NEW_CONVERSATION}}"))
        self.assertFalse(_validate_anchored_summary(VALID_SUMMARY + "\n{{PREVIOUS_ANCHORED_SUMMARY}}"))

    def test_validate_anchored_summary_rejects_missing_required_heading(self):
        self.assertFalse(_validate_anchored_summary(VALID_SUMMARY.replace("## Evidence Boundaries", "## Evidence")))

    def test_validate_anchored_summary_rejects_partial_live_style_summary(self):
        partial = """## Goal
Complete Stage 6.1 live tuning.

## Active Constraints & Guardrails
- Do not accept reasoning_content.
"""
        self.assertFalse(_validate_anchored_summary(partial))

    def test_expand_v3_blob(self):
        payload = {
            "version": 3,
            "summary_text": "Anchored Summary Content",
            "metadata": {"engine": "anchored-llm"}
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        blob = "localcmp:v3:" + encoded
        
        items = [{"type": "compaction", "encrypted_content": blob}]
        expanded = _expand_local_compaction_items(items)
        
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["role"], "user")
        self.assertIn("Anchored Summary Content", expanded[0]["content"][0]["text"])

    def test_v1_v2_still_expand_correctly(self):
        # Test v2
        v2_payload = {"version": 2, "summary_text": "V2 Summary"}
        v2_blob = "localcmp:v2:" + base64.urlsafe_b64encode(json.dumps(v2_payload).encode()).decode().rstrip("=")
        
        # Test v1
        v1_payload = {"summary_text": "V1 Summary"}
        v1_blob = "localcmp:v1:" + base64.urlsafe_b64encode(json.dumps(v1_payload).encode()).decode().rstrip("=")
        
        items = [
            {"type": "compaction", "encrypted_content": v1_blob},
            {"type": "compaction", "encrypted_content": v2_blob}
        ]
        expanded = _expand_local_compaction_items(items)
        
        self.assertEqual(len(expanded), 2)
        self.assertIn("V1 Summary", expanded[0]["content"][0]["text"])
        self.assertIn("V2 Summary", expanded[1]["content"][0]["text"])

class TestCompactionBudget(unittest.TestCase):
    """Stage 6.9/6.10 — context-aligned budget resolver and v3 context threading."""

    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        COMPACTION_CONFIG["mode"] = "llm"
        COMPACTION_CONFIG["llm_base_url"] = "http://mock-llm:8080"
        COMPACTION_CONFIG["llm_model"] = "test-model"

    def tearDown(self):
        for k, v in self.original_config.items():
            COMPACTION_CONFIG[k] = v

    def _make_body(self, items):
        return {
            "input": items,
            "model": "qwen3.6",
            "context_management": {"compact_threshold": 10},
        }

    def _mock_backend_response(self, mock_urlopen, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

    # ── Budget resolver: fail-closed cases ────────────────────────────────────

    def test_budget_resolver_fail_v3_when_context_window_none(self):
        budget = _resolve_llm_compaction_budget(None)
        self.assertTrue(budget["fail_v3"])
        self.assertEqual(budget["budget_policy"], "missing_context_window")
        self.assertIsNone(budget["context_window_tokens"])
        self.assertIsNone(budget["compaction_budget_tokens"])
        self.assertIsNone(budget["effective_max_input_chars"])
        self.assertIsNone(budget["effective_max_output_tokens"])
        self.assertIsNone(budget["effective_timeout_sec"])

    def test_budget_resolver_fail_v3_when_context_window_zero(self):
        budget = _resolve_llm_compaction_budget(0)
        self.assertTrue(budget["fail_v3"])
        self.assertEqual(budget["budget_policy"], "missing_context_window")

    def test_budget_resolver_fail_v3_when_context_window_negative(self):
        budget = _resolve_llm_compaction_budget(-8192)
        self.assertTrue(budget["fail_v3"])
        self.assertEqual(budget["budget_policy"], "missing_context_window")

    def test_budget_resolver_fail_v3_when_context_window_string(self):
        budget = _resolve_llm_compaction_budget("262144")  # type: ignore[arg-type]
        self.assertTrue(budget["fail_v3"])

    # ── Budget resolver: correct values for 256k context ─────────────────────
    # Stage 6.10 policy: context_window_autocompact_buffer_v1
    #   autocompact_buffer_tokens = floor(262144 * 0.165) = 43253
    #   safe_compaction_budget_tokens = 262144 - 43253 = 218891

    def test_budget_resolver_correct_policy_for_256k(self):
        budget = _resolve_llm_compaction_budget(262144)
        self.assertFalse(budget["fail_v3"])
        self.assertEqual(budget["policy"], "context_window_autocompact_buffer_v1")
        self.assertEqual(budget["budget_policy"], "context_window_autocompact_buffer_v1")
        self.assertEqual(budget["context_source"], "selected_model.context_window")
        self.assertEqual(budget["context_window_tokens"], 262144)

    def test_budget_resolver_90_policy_not_used(self):
        # The old 90% direct-budget policy must not be active.
        budget = _resolve_llm_compaction_budget(262144)
        self.assertNotEqual(budget.get("policy"), "context_window_90")
        self.assertNotEqual(budget.get("budget_policy"), "context_window_90")

    def test_budget_resolver_autocompact_buffer_for_256k(self):
        budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["autocompact_buffer_ratio"], 0.165)
        # floor(262144 * 0.165) = 43253
        self.assertEqual(budget["autocompact_buffer_tokens"], 43253)
        self.assertEqual(budget["safe_compaction_budget_tokens"], 218891)

    def test_budget_resolver_correct_compaction_budget_for_256k(self):
        # effective_compaction_budget_tokens = safe_compaction_budget_tokens = 218891
        budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)
        self.assertEqual(budget["compaction_budget_tokens"], 218891)

    def test_budget_resolver_max_output_tokens_capped_at_8192_for_256k(self):
        budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_max_output_tokens"], 8192)

    def test_budget_resolver_timeout_is_between_30_and_120_for_256k(self):
        budget = _resolve_llm_compaction_budget(262144)
        self.assertGreaterEqual(budget["effective_timeout_sec"], 30)
        self.assertLessEqual(budget["effective_timeout_sec"], 120)

    def test_budget_resolver_max_input_chars_exceeds_static_default_for_256k(self):
        budget = _resolve_llm_compaction_budget(262144)
        self.assertGreater(budget["effective_max_input_chars"], _DEFAULT_LLM_MAX_INPUT_CHARS)

    def test_budget_resolver_degrades_for_small_context(self):
        # Tiny context (8k) — effective values must floor to static defaults.
        # safe = floor(8192 * (1-0.165)) = 8192 - 1351 = 6841
        budget = _resolve_llm_compaction_budget(8192)
        self.assertFalse(budget["fail_v3"])
        self.assertGreaterEqual(budget["effective_max_output_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)
        self.assertGreaterEqual(budget["effective_timeout_sec"], _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertGreaterEqual(budget["effective_max_input_chars"], _DEFAULT_LLM_MAX_INPUT_CHARS)

    def test_budget_resolver_metadata_fields_populated(self):
        budget = _resolve_llm_compaction_budget(262144, old_history_estimated_tokens=5000, survival_hint_count=12)
        self.assertEqual(budget["old_history_estimated_tokens"], 5000)
        self.assertEqual(budget["survival_hint_count"], 12)
        self.assertIn("env_overrides", budget)
        self.assertIn("autocompact_buffer_ratio", budget)
        self.assertIn("autocompact_buffer_tokens", budget)
        self.assertIn("safe_compaction_budget_tokens", budget)
        self.assertIn("client_compact_threshold_tokens", budget)
        self.assertIn("client_compact_threshold_role", budget)
        self.assertIn("client_compact_threshold_used", budget)
        self.assertIn("effective_compaction_budget_tokens", budget)

    # ── Budget resolver: compact_threshold handling ───────────────────────────

    def test_budget_resolver_no_threshold_uses_safe_budget(self):
        # No compact_threshold → effective = safe_compaction_budget_tokens.
        budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)
        self.assertIsNone(budget["client_compact_threshold_tokens"])
        self.assertIsNone(budget["client_compact_threshold_role"])
        self.assertFalse(budget["client_compact_threshold_used"])

    def test_budget_resolver_threshold_1_is_force_compaction_only(self):
        # compact_threshold=1 (dogfood runner) must not shrink the budget.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=1)
        self.assertEqual(budget["client_compact_threshold_tokens"], 1)
        self.assertEqual(budget["client_compact_threshold_role"], "force_compaction_only")
        self.assertFalse(budget["client_compact_threshold_used"])
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_threshold_below_1024_is_force_compaction_only(self):
        # Any threshold below 1024 is a force/test signal.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=500)
        self.assertEqual(budget["client_compact_threshold_role"], "force_compaction_only")
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_plausible_threshold_used_as_budget_cap(self):
        # A plausible threshold <= safe budget is used as budget_cap.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=150000)
        self.assertEqual(budget["client_compact_threshold_tokens"], 150000)
        self.assertEqual(budget["client_compact_threshold_role"], "budget_cap")
        self.assertTrue(budget["client_compact_threshold_used"])
        self.assertEqual(budget["effective_compaction_budget_tokens"], 150000)

    def test_budget_resolver_threshold_above_safe_budget_is_capped(self):
        # A threshold above safe budget is capped to safe_compaction_budget_tokens.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=250000)
        self.assertEqual(budget["client_compact_threshold_tokens"], 250000)
        self.assertEqual(budget["client_compact_threshold_role"], "capped_to_safe_budget")
        self.assertFalse(budget["client_compact_threshold_used"])
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_invalid_negative_threshold_ignored(self):
        # Non-positive threshold is ignored.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=-100)
        self.assertEqual(budget["client_compact_threshold_role"], "ignored_invalid")
        self.assertFalse(budget["client_compact_threshold_used"])
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_zero_threshold_ignored(self):
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=0)
        self.assertEqual(budget["client_compact_threshold_role"], "ignored_invalid")
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_string_threshold_ignored(self):
        # A string threshold (not coerced) must be ignored as invalid.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens="150000")  # type: ignore[arg-type]
        self.assertEqual(budget["client_compact_threshold_role"], "ignored_invalid")
        self.assertFalse(budget["client_compact_threshold_used"])
        self.assertEqual(budget["effective_compaction_budget_tokens"], 218891)

    def test_budget_resolver_threshold_raw_value_recorded(self):
        # Raw client_compact_threshold_tokens is recorded regardless of role.
        budget = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=1)
        self.assertEqual(budget["client_compact_threshold_tokens"], 1)
        budget2 = _resolve_llm_compaction_budget(262144, compact_threshold_tokens=150000)
        self.assertEqual(budget2["client_compact_threshold_tokens"], 150000)

    # ── Budget resolver: env overrides ────────────────────────────────────────

    def test_budget_resolver_env_timeout_wins(self):
        with patch.dict(os.environ, {"QZ_LLM_COMPACT_TIMEOUT_SEC": "45"}):
            budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_timeout_sec"], 45)
        self.assertTrue(budget["env_overrides"]["timeout_sec"])

    def test_budget_resolver_env_max_input_chars_wins(self):
        with patch.dict(os.environ, {"QZ_LLM_COMPACT_MAX_INPUT_CHARS": "55000"}):
            budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_max_input_chars"], 55000)
        self.assertTrue(budget["env_overrides"]["max_input_chars"])

    def test_budget_resolver_env_max_output_tokens_wins(self):
        with patch.dict(os.environ, {"QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS": "2048"}):
            budget = _resolve_llm_compaction_budget(262144)
        self.assertEqual(budget["effective_max_output_tokens"], 2048)
        self.assertTrue(budget["env_overrides"]["max_output_tokens"])

    def test_budget_resolver_env_override_flags_false_when_not_set(self):
        # Remove the env vars if present so we test the default path.
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("QZ_LLM_COMPACT_TIMEOUT_SEC", "QZ_LLM_COMPACT_MAX_INPUT_CHARS",
                              "QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS")}
        with patch.dict(os.environ, clean, clear=True):
            budget = _resolve_llm_compaction_budget(262144)
        self.assertFalse(budget["env_overrides"]["timeout_sec"])
        self.assertFalse(budget["env_overrides"]["max_input_chars"])
        self.assertFalse(budget["env_overrides"]["max_output_tokens"])

    # ── v3 context threading: fail-closed to v2 when no context ──────────────

    @patch("urllib.request.urlopen")
    def test_v3_fails_to_v2_when_context_window_missing(self, mock_urlopen):
        """Without selected_context_tokens, v3 must fail closed to v2."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        # No selected_context_tokens — v3 should not be attempted.
        result = _build_local_compaction_response(self._make_body(items))
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"),
                        f"Expected v2 fallback without context, got: {blob[:30]}")
        # urlopen must NOT have been called (fail closed before network).
        mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_v3_accepted_with_valid_context_window(self, mock_urlopen):
        """v3 is accepted when selected_context_tokens is provided."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v3:"))

    # ── v3 context threading: budget values propagated ────────────────────────

    @patch("urllib.request.urlopen")
    def test_v3_uses_budget_timeout_sec(self, mock_urlopen):
        """The effective_timeout_sec from the resolver is passed to urlopen."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)
        effective_timeout = mock_urlopen.call_args.kwargs["timeout"]
        # For 256k, derived timeout > 30s (the old static default).
        self.assertGreater(effective_timeout, _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertLessEqual(effective_timeout, 120)

    @patch("urllib.request.urlopen")
    def test_v3_uses_budget_max_output_tokens(self, mock_urlopen):
        """The effective_max_output_tokens from the resolver is in the request payload."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)
        payload = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        # For 256k, max_tokens in the request should be 8192 (budget cap).
        self.assertEqual(payload["max_tokens"], 8192)

    @patch("urllib.request.urlopen")
    def test_v3_metadata_records_budget(self, mock_urlopen):
        """The v3 blob's metadata.budget field records resolver output."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=262144)
        blob = result["output"][0]["encrypted_content"]
        payload = _decode_local_compaction_blob(blob)
        budget_meta = payload["metadata"]["budget"]
        # Stage 6.10 policy.
        self.assertEqual(budget_meta["policy"], "context_window_autocompact_buffer_v1")
        self.assertEqual(budget_meta["context_window_tokens"], 262144)
        self.assertEqual(budget_meta["autocompact_buffer_ratio"], 0.165)
        self.assertEqual(budget_meta["autocompact_buffer_tokens"], 43253)
        self.assertEqual(budget_meta["safe_compaction_budget_tokens"], 218891)
        self.assertEqual(budget_meta["effective_compaction_budget_tokens"], 218891)
        self.assertEqual(budget_meta["compaction_budget_tokens"], 218891)
        self.assertEqual(budget_meta["effective_max_output_tokens"], 8192)
        self.assertGreater(budget_meta["effective_timeout_sec"], _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertIn("env_overrides", budget_meta)
        self.assertIn("client_compact_threshold_tokens", budget_meta)
        self.assertIn("client_compact_threshold_role", budget_meta)
        self.assertIn("client_compact_threshold_used", budget_meta)

    @patch("urllib.request.urlopen")
    def test_v3_budget_for_128k_context_window(self, mock_urlopen):
        """Budget values scale correctly for a 128k context window."""
        # 128k: autocompact_buffer = floor(131072 * 0.165) = 21626
        #        safe = 131072 - 21626 = 109446
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": VALID_SUMMARY}}]},
        )
        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items), selected_context_tokens=131072)
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v3:"))
        payload = _decode_local_compaction_blob(blob)
        bm = payload["metadata"]["budget"]
        self.assertEqual(bm["context_window_tokens"], 131072)
        self.assertEqual(bm["autocompact_buffer_tokens"], 21626)
        self.assertEqual(bm["safe_compaction_budget_tokens"], 109446)
        # No threshold provided → effective = safe budget.
        self.assertEqual(bm["compaction_budget_tokens"], 109446)
        self.assertEqual(bm["effective_compaction_budget_tokens"], 109446)


class TestActiveBackendBaseUrl(unittest.TestCase):
    """Stage 6.10.2 — _active_backend_base_url() resolves from env vars."""

    def test_default_returns_loopback_18084(self):
        env = {}  # No QZ_SERVER_HOST / QZ_SERVER_PORT set
        self.assertEqual(_active_backend_base_url(env=env), "http://127.0.0.1:18084")

    def test_uses_qz_server_host_and_port(self):
        env = {"QZ_SERVER_HOST": "192.168.1.10", "QZ_SERVER_PORT": "11434"}
        self.assertEqual(_active_backend_base_url(env=env), "http://192.168.1.10:11434")

    def test_blank_host_returns_empty(self):
        env = {"QZ_SERVER_HOST": "", "QZ_SERVER_PORT": "18084"}
        self.assertEqual(_active_backend_base_url(env=env), "")

    def test_blank_port_returns_empty(self):
        env = {"QZ_SERVER_HOST": "127.0.0.1", "QZ_SERVER_PORT": ""}
        self.assertEqual(_active_backend_base_url(env=env), "")

    def test_non_integer_port_returns_empty(self):
        env = {"QZ_SERVER_HOST": "127.0.0.1", "QZ_SERVER_PORT": "notaport"}
        self.assertEqual(_active_backend_base_url(env=env), "")

    def test_whitespace_stripped_from_host_and_port(self):
        env = {"QZ_SERVER_HOST": "  127.0.0.1  ", "QZ_SERVER_PORT": "  18084  "}
        self.assertEqual(_active_backend_base_url(env=env), "http://127.0.0.1:18084")


class TestProxyRecursionGuardDefaultPorts(unittest.TestCase):
    """Stage 6.10.2 — recursion guard catches proxy ports 18180/18183 without env vars."""

    def _empty_env(self):
        # Env with no QZ_PROXY_BASE_URL, CODEX_OSS_BASE_URL, or QZ_PROXY_HOST/PORT set.
        return {}

    def test_port_18180_loopback_rejected_without_env(self):
        self.assertTrue(
            _is_probably_quantzhai_proxy_url("http://127.0.0.1:18180", env=self._empty_env())
        )

    def test_port_18183_loopback_rejected_without_env(self):
        self.assertTrue(
            _is_probably_quantzhai_proxy_url("http://127.0.0.1:18183", env=self._empty_env())
        )

    def test_port_18180_localhost_rejected_without_env(self):
        self.assertTrue(
            _is_probably_quantzhai_proxy_url("http://localhost:18180", env=self._empty_env())
        )

    def test_port_18183_localhost_rejected_without_env(self):
        self.assertTrue(
            _is_probably_quantzhai_proxy_url("http://localhost:18183", env=self._empty_env())
        )

    def test_backend_port_18084_not_rejected(self):
        # Port 18084 is the direct llama-server backend — must NOT be flagged as proxy.
        self.assertFalse(
            _is_probably_quantzhai_proxy_url("http://127.0.0.1:18084", env=self._empty_env())
        )

    def test_unrelated_port_not_rejected(self):
        self.assertFalse(
            _is_probably_quantzhai_proxy_url("http://127.0.0.1:8080", env=self._empty_env())
        )

    def test_env_proxy_url_still_rejected(self):
        env = {"CODEX_OSS_BASE_URL": "http://127.0.0.1:18180/v1"}
        self.assertTrue(
            _is_probably_quantzhai_proxy_url("http://127.0.0.1:18180", env=env)
        )

    def test_empty_url_returns_false(self):
        self.assertFalse(
            _is_probably_quantzhai_proxy_url("", env=self._empty_env())
        )


class TestCallLlmCompactorUrlResolution(unittest.TestCase):
    """Stage 6.11 — _call_llm_compactor() URL resolution: explicit param → config override → no_backend.

    _active_backend_base_url() must never be consulted by _call_llm_compactor().
    BackendManager provides the URL via the explicit llm_base_url parameter.
    """

    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        COMPACTION_CONFIG["llm_model"] = "test-model"
        COMPACTION_CONFIG["llm_disable_reasoning"] = True
        COMPACTION_CONFIG["llm_base_url"] = ""

    def tearDown(self):
        for k, v in self.original_config.items():
            COMPACTION_CONFIG[k] = v

    def _mock_backend_response(self, mock_urlopen, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

    # Test B: explicit llm_base_url parameter is used for the request
    @patch("urllib.request.urlopen")
    def test_explicit_llm_base_url_param_is_used(self, mock_urlopen):
        """Test B: explicit llm_base_url param routes the request to the correct backend."""
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": "summary"}}]},
        )
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "summary")
        mock_urlopen.assert_called_once()
        called_url = mock_urlopen.call_args.args[0].full_url
        self.assertIn(":19999", called_url)

    # Test B2: config override (COMPACTION_CONFIG["llm_base_url"]) used when param empty
    @patch("urllib.request.urlopen")
    def test_config_override_used_when_param_empty(self, mock_urlopen):
        """Config debug override is used when explicit param is empty."""
        COMPACTION_CONFIG["llm_base_url"] = "http://config-override:8888"
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": "summary"}}]},
        )
        content, reason = _call_llm_compactor("prompt", llm_base_url="")
        self.assertEqual(reason, "ok")
        self.assertEqual(content, "summary")
        mock_urlopen.assert_called_once()
        called_url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("config-override:8888", called_url)

    # Test B3: explicit param takes precedence over config override
    @patch("urllib.request.urlopen")
    def test_explicit_param_wins_over_config_override(self, mock_urlopen):
        """Explicit llm_base_url param wins over COMPACTION_CONFIG debug override."""
        COMPACTION_CONFIG["llm_base_url"] = "http://config-override:8888"
        self._mock_backend_response(
            mock_urlopen,
            {"choices": [{"message": {"content": "summary"}}]},
        )
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://backend-mgr:19999")
        self.assertEqual(reason, "ok")
        mock_urlopen.assert_called_once()
        called_url = mock_urlopen.call_args.args[0].full_url
        self.assertIn("backend-mgr:19999", called_url)
        self.assertNotIn("config-override", called_url)

    # Test A: no param and no config → "no_backend"; _active_backend_base_url never consulted
    @patch("proxy.qz_responses._active_backend_base_url")
    @patch("urllib.request.urlopen")
    def test_no_url_returns_no_backend_reason(self, mock_urlopen, mock_active):
        """Test A: when no URL available, returns (None, 'no_backend') without calling _active_backend_base_url."""
        COMPACTION_CONFIG["llm_base_url"] = ""
        content, reason = _call_llm_compactor("prompt", llm_base_url="")
        self.assertIsNone(content)
        self.assertEqual(reason, "no_backend")
        mock_urlopen.assert_not_called()
        mock_active.assert_not_called()

    # Test C: proxy URL is rejected → "proxy_url_rejected"
    @patch("urllib.request.urlopen")
    def test_proxy_url_param_is_rejected(self, mock_urlopen):
        """Test C: explicit llm_base_url matching a proxy port is rejected."""
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:18180")
        self.assertIsNone(content)
        self.assertEqual(reason, "proxy_url_rejected")
        mock_urlopen.assert_not_called()

    # Test C2: config override with proxy port also rejected
    @patch("urllib.request.urlopen")
    def test_config_proxy_url_is_rejected(self, mock_urlopen):
        """Config debug override matching a proxy port is rejected."""
        COMPACTION_CONFIG["llm_base_url"] = "http://127.0.0.1:18183"
        content, reason = _call_llm_compactor("prompt", llm_base_url="")
        self.assertIsNone(content)
        self.assertEqual(reason, "proxy_url_rejected")
        mock_urlopen.assert_not_called()

    # Reason code: http_error
    @patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
        url="http://127.0.0.1:19999/v1/chat/completions",
        code=503,
        msg="Service Unavailable",
        hdrs=None,
        fp=None,
    ))
    def test_http_error_returns_http_error_reason(self, _mock_urlopen):
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertIsNone(content)
        self.assertEqual(reason, "http_error:503")

    # Reason code: timeout
    @patch("urllib.request.urlopen", side_effect=TimeoutError("timed out"))
    def test_timeout_returns_timeout_reason(self, _mock_urlopen):
        content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertIsNone(content)
        self.assertEqual(reason, "timeout")

    # Reason code: invalid_json
    def test_invalid_json_returns_invalid_json_reason(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not json {"
        bad_resp.__enter__.return_value = bad_resp
        with patch("urllib.request.urlopen", return_value=bad_resp):
            content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertIsNone(content)
        self.assertEqual(reason, "invalid_json")

    # Reason code: empty_content
    def test_empty_content_returns_empty_content_reason(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": None}}]}).encode()
        mock_resp.__enter__.return_value = mock_resp
        with patch("urllib.request.urlopen", return_value=mock_resp):
            content, reason = _call_llm_compactor("prompt", llm_base_url="http://127.0.0.1:19999")
        self.assertIsNone(content)
        self.assertEqual(reason, "empty_content")


if __name__ == "__main__":
    unittest.main()
