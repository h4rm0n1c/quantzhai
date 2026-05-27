import unittest
import json
import base64
import os
import socket
import tempfile
import urllib.error
from unittest.mock import patch, MagicMock
from proxy.qz_responses import (
    _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
    _DEFAULT_LLM_TIMEOUT_SEC,
    _build_local_compaction_response,
    _build_survival_weighted_compaction_prompt,
    _call_llm_compactor,
    _decode_local_compaction_blob,
    _expand_local_compaction_items,
    _get_env_int,
    _is_probably_quantzhai_proxy_url,
    _normalize_compaction_mode,
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

    def test_default_is_heuristic_v2(self):
        # Reset mode to default
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
        result = _build_local_compaction_response(self._make_body(items))
        
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
        result = _build_local_compaction_response(self._make_body(items))
        
        # Should fallback to v2
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_llm_compaction_fallback_on_error(self, mock_urlopen):
        # Mock LLM error
        mock_urlopen.side_effect = Exception("Connection error")

        items = [{"type": "message", "role": "user", "content": "hello"}] * 50
        result = _build_local_compaction_response(self._make_body(items))
        
        # Should fallback to v2
        blob = result["output"][0]["encrypted_content"]
        self.assertTrue(blob.startswith("localcmp:v2:"))

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

        result = _build_local_compaction_response(self._make_body(items))

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

        result = _call_llm_compactor("prompt")

        self.assertEqual(result, VALID_SUMMARY.strip())
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], _DEFAULT_LLM_TIMEOUT_SEC)

    @patch("urllib.request.urlopen")
    def test_recursive_proxy_url_is_rejected_without_network_call(self, mock_urlopen):
        COMPACTION_CONFIG["llm_base_url"] = "http://127.0.0.1:18180"
        with patch.dict(os.environ, {"CODEX_OSS_BASE_URL": "http://127.0.0.1:18180/v1"}):
            self.assertIsNone(_call_llm_compactor("prompt"))
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
        self.assertEqual(_call_llm_compactor("prompt"), "chat content")

    @patch("urllib.request.urlopen")
    def test_response_shape_legacy_completions(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"choices": [{"text": " legacy text "}]})
        self.assertEqual(_call_llm_compactor("prompt"), "legacy text")

    @patch("urllib.request.urlopen")
    def test_response_shape_simple_content(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"content": " simple content "})
        self.assertEqual(_call_llm_compactor("prompt"), "simple content")

    @patch("urllib.request.urlopen")
    def test_response_shape_simple_response(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"response": " simple response "})
        self.assertEqual(_call_llm_compactor("prompt"), "simple response")

    @patch("urllib.request.urlopen")
    def test_response_shape_non_string_returns_none(self, mock_urlopen):
        self._mock_backend_response(mock_urlopen, {"choices": [{"message": {"content": ["no"]}}]})
        self.assertIsNone(_call_llm_compactor("prompt"))

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

if __name__ == "__main__":
    unittest.main()
