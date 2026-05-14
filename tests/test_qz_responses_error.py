"""Tests for qz.responses.error.v1 structured error payloads."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_responses_error import (
    DEPRECATED_MODEL_ALIASES,
    QZ_RESPONSES_ERROR_SCHEMA,
    build_responses_error_payload,
    is_deprecated_alias,
)
from proxy.qz_telemetry import REQUEST_LIFECYCLE_EVENT_TYPES


class SchemaTests(unittest.TestCase):
    def test_schema_constant(self):
        self.assertEqual(QZ_RESPONSES_ERROR_SCHEMA, "qz.responses.error.v1")

    def test_payload_contains_schema(self):
        p = build_responses_error_payload("test error")
        self.assertEqual(p["schema"], QZ_RESPONSES_ERROR_SCHEMA)

    def test_error_field_present(self):
        p = build_responses_error_payload("backend unavailable")
        self.assertEqual(p["error"], "backend unavailable")

    def test_payload_json_serialisable(self):
        p = build_responses_error_payload(
            "model not found",
            reason="no match for 'foo'",
            requested_model="foo",
            available_models=["a.gguf", "b.gguf"],
        )
        serialised = json.dumps(p)
        rt = json.loads(serialised)
        self.assertEqual(rt["schema"], QZ_RESPONSES_ERROR_SCHEMA)


class ProxyNotReadyPayloadTests(unittest.TestCase):
    def _make(self, ready=False, catalog_ready=False):
        initialization = {
            "schema": "qz.proxy.initialization.v1",
            "state": "initializing" if not ready else "ready",
            "ready": ready,
            "catalog_ready": catalog_ready,
        }
        return build_responses_error_payload(
            error="proxy not ready",
            reason="model catalog and startup policy are still loading",
            proxy_initialization=initialization,
            readiness={
                "proxy_ready": ready,
                "catalog_ready": catalog_ready,
                "model_visible": False,
                "backend_ready": False,
            },
            operator_hint="Check /qz/status.",
        )

    def test_contains_proxy_initialization(self):
        p = self._make()
        self.assertIn("proxy_initialization", p)
        self.assertFalse(p["proxy_initialization"]["ready"])

    def test_readiness_all_false(self):
        p = self._make()
        r = p["readiness"]
        self.assertFalse(r["proxy_ready"])
        self.assertFalse(r["catalog_ready"])
        self.assertFalse(r["model_visible"])
        self.assertFalse(r["backend_ready"])

    def test_has_operator_hint(self):
        p = self._make()
        self.assertIn("operator_hint", p)
        self.assertIn("/qz/status", p["operator_hint"])


class ModelMissingPayloadTests(unittest.TestCase):
    def _make(self, model="missing-model", available=None):
        return build_responses_error_payload(
            error="model not found",
            reason=f"no model matching '{model}'",
            requested_model=model,
            available_models=available or ["kuato.gguf", "qwen-blank.gguf"],
            readiness={
                "proxy_ready": True,
                "catalog_ready": True,
                "model_visible": False,
                "backend_ready": False,
            },
            operator_hint="Use a real model ID from /v1/models.",
        )

    def test_contains_requested_model(self):
        p = self._make("missing-model")
        self.assertEqual(p["requested_model"], "missing-model")

    def test_contains_available_models(self):
        p = self._make(available=["a.gguf", "b.gguf"])
        self.assertIn("available_models", p)
        self.assertIn("a.gguf", p["available_models"])

    def test_available_models_sorted(self):
        p = self._make(available=["z.gguf", "a.gguf"])
        self.assertEqual(p["available_models"], ["a.gguf", "z.gguf"])

    def test_model_visible_false(self):
        p = self._make()
        self.assertFalse(p["readiness"]["model_visible"])

    def test_deprecated_alias_gets_hint(self):
        p = self._make(model="high")
        self.assertIn("alias_hint", p)
        self.assertIn("deprecated", p["alias_hint"])

    def test_non_alias_no_alias_hint(self):
        p = self._make(model="kuato.gguf")
        self.assertNotIn("alias_hint", p)


class BackendUnavailablePayloadTests(unittest.TestCase):
    def _make(self, error="Connection refused"):
        initialization = {
            "schema": "qz.proxy.initialization.v1",
            "state": "ready",
            "ready": True,
            "catalog_ready": True,
        }
        return build_responses_error_payload(
            error="backend unavailable",
            reason=error,
            requested_model="kuato.gguf",
            proxy_initialization=initialization,
            readiness={
                "proxy_ready": True,
                "catalog_ready": True,
                "model_visible": True,
                "backend_ready": False,
            },
            operator_hint="Check /qz/status. qz-codex does not need local Docker access.",
        )

    def test_backend_ready_false(self):
        p = self._make()
        self.assertFalse(p["readiness"]["backend_ready"])

    def test_proxy_ready_true(self):
        p = self._make()
        self.assertTrue(p["readiness"]["proxy_ready"])

    def test_model_visible_true(self):
        """Backend unavailable: model was found, only backend is down."""
        p = self._make()
        self.assertTrue(p["readiness"]["model_visible"])

    def test_operator_hint_no_docker_requirement(self):
        """Hint must not imply Docker is required."""
        p = self._make()
        self.assertIn("operator_hint", p)
        self.assertNotIn("Docker is required", p["operator_hint"])
        self.assertNotIn("requires Docker", p["operator_hint"])

    def test_reason_included(self):
        p = self._make("Connection refused")
        self.assertIn("Connection refused", p.get("reason", ""))


class DeprecatedAliasTests(unittest.TestCase):
    def test_high_is_deprecated(self):
        self.assertTrue(is_deprecated_alias("high"))

    def test_low_is_deprecated(self):
        self.assertTrue(is_deprecated_alias("low"))

    def test_medium_is_deprecated(self):
        self.assertTrue(is_deprecated_alias("medium"))

    def test_caveman_is_deprecated(self):
        self.assertTrue(is_deprecated_alias("caveman"))

    def test_max_is_deprecated(self):
        self.assertTrue(is_deprecated_alias("max"))

    def test_real_model_not_deprecated(self):
        self.assertFalse(is_deprecated_alias("kuato.gguf"))

    def test_partial_alias_not_deprecated(self):
        self.assertFalse(is_deprecated_alias("highquality"))

    def test_none_not_deprecated(self):
        self.assertFalse(is_deprecated_alias(None))

    def test_empty_not_deprecated(self):
        self.assertFalse(is_deprecated_alias(""))

    def test_case_insensitive(self):
        self.assertTrue(is_deprecated_alias("HIGH"))
        self.assertTrue(is_deprecated_alias("Caveman"))


class TelemetryRegistrationTests(unittest.TestCase):
    def test_responses_rejected_proxy_not_ready_registered(self):
        self.assertIn("responses_rejected_proxy_not_ready", REQUEST_LIFECYCLE_EVENT_TYPES)

    def test_responses_rejected_model_missing_registered(self):
        self.assertIn("responses_rejected_model_missing", REQUEST_LIFECYCLE_EVENT_TYPES)

    def test_responses_rejected_backend_unavailable_registered(self):
        self.assertIn("responses_rejected_backend_unavailable", REQUEST_LIFECYCLE_EVENT_TYPES)


if __name__ == "__main__":
    unittest.main()
