import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from proxy.qz_responses import (
    COMPACTION_CONFIG,
    _DEFAULT_LLM_DISABLE_REASONING,
    _DEFAULT_LLM_MAX_INPUT_CHARS,
    _DEFAULT_LLM_MAX_OUTPUT_TOKENS,
    _DEFAULT_LLM_TIMEOUT_SEC,
    _DEFAULT_SURVIVAL_PROFILE,
    _build_local_compaction_response,
    _call_llm_compactor,
    _decode_local_compaction_blob,
    _get_effective_compaction_config,
    _normalize_survival_profile,
)


VALID_SUMMARY = """## Goal
Complete Stage 5 compaction config plumbing.

## Active Constraints & Guardrails
- Keep localcmp:v3 opt-in.

## Current Status
### Done
- Profile config is under test.

## Key Decisions
- Decision: env overrides compaction.json.

## Evidence Boundaries
- Confirmed by mocked unit tests.

## Technical State
### Files / Paths
- proxy/qz_responses.py

## Next Actions
1. Run validation.
"""


class TestCompactionConfig(unittest.TestCase):
    def setUp(self):
        self.original_config = COMPACTION_CONFIG.copy()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        COMPACTION_CONFIG.clear()
        COMPACTION_CONFIG.update(self.original_config)
        self.tmp.cleanup()

    def _write_config(self, data):
        path = self.root / "compaction.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def _config(self, data, env=None, profile_name=None):
        return _get_effective_compaction_config(
            env={} if env is None else env,
            root=str(self.root),
            config_path=self._write_config(data),
            profile_name=profile_name,
        )

    def _mock_backend_response(self, mock_urlopen, payload):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

    def _make_body(self):
        return {
            "input": [{"type": "message", "role": "user", "content": "hello"}] * 50,
            "model": "qwen3.6",
            "context_management": {"compact_threshold": 10},
        }

    def test_no_compaction_json_no_env_uses_heuristic_defaults(self):
        cfg = _get_effective_compaction_config(env={}, root=str(self.root))

        self.assertEqual(cfg["mode"], "heuristic")
        self.assertEqual(cfg["llm_base_url"], "")
        self.assertEqual(cfg["survival_profile"], _DEFAULT_SURVIVAL_PROFILE)

    def test_default_compaction_json_profile_is_heuristic(self):
        cfg = self._config({
            "version": 1,
            "profiles": {"default": {"mode": "heuristic", "survival_profile": "coding"}},
        })

        self.assertEqual(cfg["mode"], "heuristic")
        self.assertEqual(cfg["survival_profile"], "coding")

    def test_profile_mode_llm_is_read(self):
        cfg = self._config({
            "profiles": {
                "default": {"mode": "heuristic"},
                "coding-llm": {"mode": "llm"},
            },
        }, profile_name="coding-llm")

        self.assertEqual(cfg["mode"], "llm")

    def test_env_qzcompact_overrides_profile_mode(self):
        cfg = self._config(
            {"profiles": {"default": {"mode": "llm"}}},
            env={"QZCOMPACT": "heuristic"},
        )

        self.assertEqual(cfg["mode"], "heuristic")

    def test_invalid_profile_mode_falls_back_to_heuristic(self):
        cfg = self._config({"profiles": {"default": {"mode": "surprise"}}})

        self.assertEqual(cfg["mode"], "heuristic")

    def test_invalid_env_mode_falls_back_to_heuristic(self):
        cfg = self._config(
            {"profiles": {"default": {"mode": "llm"}}},
            env={"QZCOMPACT": "surprise"},
        )

        self.assertEqual(cfg["mode"], "heuristic")

    def test_profile_llm_base_url_is_used_when_env_absent(self):
        cfg = self._config({"profiles": {"default": {"llm_base_url": "http://backend:8080"}}})

        self.assertEqual(cfg["llm_base_url"], "http://backend:8080")

    def test_env_llm_base_url_overrides_profile(self):
        cfg = self._config(
            {"profiles": {"default": {"llm_base_url": "http://profile:8080"}}},
            env={"QZ_LLM_COMPACT_BASE_URL": "http://env:8080"},
        )

        self.assertEqual(cfg["llm_base_url"], "http://env:8080")

    def test_profile_prompt_file_is_used_when_env_absent(self):
        cfg = self._config({"profiles": {"default": {"prompt_file": "config/custom.md"}}})

        self.assertEqual(cfg["prompt_file"], "config/custom.md")

    def test_env_prompt_file_overrides_profile(self):
        cfg = self._config(
            {"profiles": {"default": {"prompt_file": "config/profile.md"}}},
            env={"QZ_LLM_COMPACT_PROMPT_FILE": "config/env.md"},
        )

        self.assertEqual(cfg["prompt_file"], "config/env.md")

    def test_profile_integer_values_are_parsed_only_when_explicit(self):
        implicit = self._config({"profiles": {"default": {"mode": "heuristic"}}})
        explicit = self._config({
            "profiles": {
                "default": {
                    "llm_timeout_sec": "7",
                    "llm_max_input_chars": "1234",
                    "llm_max_output_tokens": "321",
                }
            }
        })

        self.assertEqual(implicit["llm_timeout_sec"], _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertEqual(implicit["llm_max_input_chars"], _DEFAULT_LLM_MAX_INPUT_CHARS)
        self.assertEqual(implicit["llm_max_output_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)
        self.assertEqual(explicit["llm_timeout_sec"], 7)
        self.assertEqual(explicit["llm_max_input_chars"], 1234)
        self.assertEqual(explicit["llm_max_output_tokens"], 321)

    def test_disable_reasoning_defaults_for_compactor_only(self):
        cfg = self._config({"profiles": {"default": {"mode": "heuristic"}}})

        self.assertEqual(cfg["llm_disable_reasoning"], _DEFAULT_LLM_DISABLE_REASONING)

    def test_profile_disable_reasoning_is_ignored(self):
        cfg = self._config({"profiles": {"default": {"llm_disable_reasoning": False}}})

        self.assertEqual(cfg["llm_disable_reasoning"], _DEFAULT_LLM_DISABLE_REASONING)

    def test_env_disable_reasoning_overrides_default(self):
        cfg = self._config(
            {"profiles": {"default": {"mode": "heuristic"}}},
            env={"QZ_LLM_COMPACT_DISABLE_REASONING": "0"},
        )

        self.assertFalse(cfg["llm_disable_reasoning"])

    def test_invalid_disable_reasoning_env_falls_back_safely(self):
        cfg = self._config(
            {"profiles": {"default": {"llm_disable_reasoning": False}}},
            env={"QZ_LLM_COMPACT_DISABLE_REASONING": "maybe"},
        )

        self.assertEqual(cfg["llm_disable_reasoning"], _DEFAULT_LLM_DISABLE_REASONING)

    def test_invalid_profile_integer_values_fall_back_to_defaults(self):
        cfg = self._config({
            "profiles": {
                "default": {
                    "llm_timeout_sec": "bad",
                    "llm_max_input_chars": 0,
                    "llm_max_output_tokens": -1,
                }
            }
        })

        self.assertEqual(cfg["llm_timeout_sec"], _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertEqual(cfg["llm_max_input_chars"], _DEFAULT_LLM_MAX_INPUT_CHARS)
        self.assertEqual(cfg["llm_max_output_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)

    def test_invalid_env_integer_values_fall_back_to_defaults(self):
        cfg = self._config(
            {"profiles": {"default": {}}},
            env={
                "QZ_LLM_COMPACT_TIMEOUT_SEC": "bad",
                "QZ_LLM_COMPACT_MAX_INPUT_CHARS": "0",
                "QZ_LLM_COMPACT_MAX_OUTPUT_TOKENS": "-1",
            },
        )

        self.assertEqual(cfg["llm_timeout_sec"], _DEFAULT_LLM_TIMEOUT_SEC)
        self.assertEqual(cfg["llm_max_input_chars"], _DEFAULT_LLM_MAX_INPUT_CHARS)
        self.assertEqual(cfg["llm_max_output_tokens"], _DEFAULT_LLM_MAX_OUTPUT_TOKENS)

    def test_survival_profile_defaults_to_coding(self):
        cfg = self._config({"profiles": {"default": {}}})

        self.assertEqual(cfg["survival_profile"], "coding")

    def test_survival_profile_coding_is_accepted(self):
        cfg = self._config({"profiles": {"default": {"survival_profile": "coding"}}})

        self.assertEqual(cfg["survival_profile"], "coding")

    def test_unknown_survival_profile_falls_back_to_coding(self):
        cfg = self._config({"profiles": {"default": {"survival_profile": "research"}}})

        self.assertEqual(cfg["survival_profile"], "coding")
        self.assertEqual(_normalize_survival_profile("legal"), "coding")

    def test_env_survival_profile_overrides_profile_safely(self):
        cfg = self._config(
            {"profiles": {"default": {"survival_profile": "research"}}},
            env={"QZ_COMPACTION_SURVIVAL_PROFILE": "coding"},
        )

        self.assertEqual(cfg["survival_profile"], "coding")

    @patch("urllib.request.urlopen")
    def test_recursive_proxy_url_guard_rejects_profile_url(self, mock_urlopen):
        cfg = self._config({
            "profiles": {
                "default": {
                    "mode": "llm",
                    "llm_base_url": "http://127.0.0.1:18180",
                }
            }
        })
        COMPACTION_CONFIG.clear()
        COMPACTION_CONFIG.update(cfg)

        with patch.dict(os.environ, {"CODEX_OSS_BASE_URL": "http://127.0.0.1:18180/v1"}):
            self.assertIsNone(_call_llm_compactor("prompt"))
        mock_urlopen.assert_not_called()

    def test_config_absent_still_produces_localcmp_v2(self):
        cfg = _get_effective_compaction_config(env={}, root=str(self.root))
        COMPACTION_CONFIG.clear()
        COMPACTION_CONFIG.update(cfg)

        result = _build_local_compaction_response(self._make_body())

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))

    @patch("urllib.request.urlopen")
    def test_profile_mode_llm_with_mocked_backend_produces_localcmp_v3(self, mock_urlopen):
        cfg = self._config({
            "profiles": {
                "coding-llm": {
                    "mode": "llm",
                    "llm_base_url": "http://mock-llm:8080",
                    "llm_model": "test-model",
                }
            }
        }, profile_name="coding-llm")
        COMPACTION_CONFIG.clear()
        COMPACTION_CONFIG.update(cfg)
        self._mock_backend_response(mock_urlopen, {"choices": [{"message": {"content": VALID_SUMMARY}}]})

        result = _build_local_compaction_response(self._make_body())
        blob = result["output"][0]["encrypted_content"]

        self.assertTrue(blob.startswith("localcmp:v3:"))
        payload = _decode_local_compaction_blob(blob)
        self.assertEqual(payload["version"], 3)

    @patch("urllib.request.urlopen")
    def test_profile_mode_llm_without_backend_falls_back_to_localcmp_v2(self, mock_urlopen):
        cfg = self._config({"profiles": {"default": {"mode": "llm"}}})
        COMPACTION_CONFIG.clear()
        COMPACTION_CONFIG.update(cfg)

        result = _build_local_compaction_response(self._make_body())

        self.assertTrue(result["output"][0]["encrypted_content"].startswith("localcmp:v2:"))
        mock_urlopen.assert_not_called()

    def test_malformed_compaction_json_uses_safe_defaults(self):
        path = self.root / "bad.json"
        path.write_text("{not-json", encoding="utf-8")

        cfg = _get_effective_compaction_config(env={}, root=str(self.root), config_path=str(path))

        self.assertEqual(cfg["mode"], "heuristic")
        self.assertEqual(cfg["llm_base_url"], "")
        self.assertEqual(cfg["survival_profile"], "coding")

    def test_missing_selected_profile_falls_back_to_default(self):
        cfg = self._config(
            {"profiles": {"default": {"mode": "heuristic", "llm_model": "fallback-model"}}},
            profile_name="missing",
        )

        self.assertEqual(cfg["mode"], "heuristic")
        self.assertEqual(cfg["llm_model"], "fallback-model")

    def test_qz_compaction_config_env_path_is_used(self):
        path = self._write_config({"profiles": {"default": {"mode": "llm"}}})

        cfg = _get_effective_compaction_config(
            env={"QZ_COMPACTION_CONFIG": path},
            root=str(self.root),
        )

        self.assertEqual(cfg["mode"], "llm")

    def test_qz_compaction_profile_env_selects_profile(self):
        path = self._write_config({
            "profiles": {
                "default": {"mode": "heuristic"},
                "coding-llm": {"mode": "llm"},
            }
        })

        cfg = _get_effective_compaction_config(
            env={"QZ_COMPACTION_CONFIG": path, "QZ_COMPACTION_PROFILE": "coding-llm"},
            root=str(self.root),
        )

        self.assertEqual(cfg["mode"], "llm")

    def test_existing_v3_blob_decode_still_works(self):
        payload = {"version": 3, "summary_text": "v3 summary"}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")

        decoded = _decode_local_compaction_blob("localcmp:v3:" + encoded)

        self.assertEqual(decoded["summary_text"], "v3 summary")


if __name__ == "__main__":
    unittest.main()
