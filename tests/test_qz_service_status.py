"""Tests for qz.service.status.v1 builder (proxy/qz_service_status.py)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_service_status import (
    SERVICE_STATUS_SCHEMA,
    build_service_status,
)


# ---------------------------------------------------------------------------
# Payload builders for common scenarios
# ---------------------------------------------------------------------------

def _cp_ready(loaded_model="kuato"):
    """Fully ready control-plane payload."""
    return {
        "schema": "qz.control_plane.status.v1",
        "ok": True,
        "status": "ready",
        "readiness": {
            "proxy_ready": True,
            "catalog_ready": True,
            "models_visible": True,
            "backend_reachable": True,
            "backend_ready": True,
        },
        "proxy_initialization": {"state": "ready", "ready": True, "catalog_ready": True, "error": None},
        "models": {"count": 3, "ids": ["a", "b", loaded_model], "selected": loaded_model, "selected_backend_id": loaded_model},
        "backend": {"reachable": True, "ready": True, "health_status": 200, "loaded_model": loaded_model, "loaded_count": 1, "restart_required": False, "error": None},
        "operator_hints": [],
    }


def _cp_proxy_initializing():
    return {
        "ok": False, "status": "initializing",
        "readiness": {"proxy_ready": False, "catalog_ready": False, "models_visible": False, "backend_reachable": False, "backend_ready": False},
        "proxy_initialization": {"state": "starting", "ready": False, "catalog_ready": False, "error": None},
        "models": {"count": 0, "ids": [], "selected": "", "selected_backend_id": ""},
        "backend": {"reachable": False, "ready": False, "health_status": None, "loaded_model": "", "loaded_count": 0, "restart_required": False, "error": None},
        "operator_hints": [],
    }


def _cp_backend_unreachable():
    return {
        "ok": True, "status": "backend_unavailable",
        "readiness": {"proxy_ready": True, "catalog_ready": True, "models_visible": True, "backend_reachable": False, "backend_ready": False},
        "proxy_initialization": {"state": "ready", "ready": True, "catalog_ready": True, "error": None},
        "models": {"count": 2, "ids": ["a", "b"], "selected": "a", "selected_backend_id": "a"},
        "backend": {"reachable": False, "ready": False, "health_status": None, "loaded_model": "", "loaded_count": 0, "restart_required": False, "error": "Connection refused"},
        "operator_hints": ["The llama.cpp backend is unreachable."],
    }


def _cp_model_not_loaded():
    return {
        "ok": True, "status": "model_not_loaded",
        "readiness": {"proxy_ready": True, "catalog_ready": True, "models_visible": True, "backend_reachable": True, "backend_ready": False},
        "proxy_initialization": {"state": "ready", "ready": True, "catalog_ready": True, "error": None},
        "models": {"count": 1, "ids": ["kuato"], "selected": "kuato", "selected_backend_id": "kuato"},
        "backend": {"reachable": True, "ready": False, "health_status": 200, "loaded_model": "", "loaded_count": 0, "restart_required": False, "error": None},
        "operator_hints": [],
    }


def _cp_restart_required():
    return {
        "ok": True, "status": "ready",
        "readiness": {"proxy_ready": True, "catalog_ready": True, "models_visible": True, "backend_reachable": True, "backend_ready": True},
        "proxy_initialization": {"state": "ready", "ready": True, "catalog_ready": True, "error": None},
        "models": {"count": 1, "ids": ["kuato"], "selected": "kuato", "selected_backend_id": "kuato"},
        "backend": {"reachable": True, "ready": True, "health_status": 200, "loaded_model": "kuato", "loaded_count": 1, "restart_required": True, "error": None},
        "operator_hints": [],
    }


# ---------------------------------------------------------------------------
# Schema / structure
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    def test_schema_field(self):
        s = build_service_status(_cp_ready())
        self.assertEqual(s["schema"], SERVICE_STATUS_SCHEMA)
        self.assertEqual(s["schema"], "qz.service.status.v1")

    def test_all_required_fields_present(self):
        s = build_service_status(_cp_ready())
        for field in (
            "schema", "proxy_state", "catalog_state", "backend_state",
            "model_state", "request_admission", "recovery_state",
            "recoverable", "retryable", "fatal",
            "last_error", "operator_action", "operator_hints",
        ):
            self.assertIn(field, s, f"missing field: {field}")

    def test_json_serialisable(self):
        for cp in (_cp_ready(), _cp_proxy_initializing(), _cp_backend_unreachable(), _cp_model_not_loaded()):
            s = build_service_status(cp)
            rt = json.loads(json.dumps(s))
            self.assertEqual(rt["schema"], SERVICE_STATUS_SCHEMA)

    def test_empty_payload_safe(self):
        s = build_service_status({})
        self.assertEqual(s["schema"], SERVICE_STATUS_SCHEMA)
        self.assertIsInstance(s["proxy_state"], str)

    def test_none_payload_safe(self):
        s = build_service_status(None)  # type: ignore[arg-type]
        self.assertEqual(s["schema"], SERVICE_STATUS_SCHEMA)


# ---------------------------------------------------------------------------
# Scenario 1: fully ready
# ---------------------------------------------------------------------------

class ReadyScenarioTests(unittest.TestCase):
    def setUp(self):
        self.s = build_service_status(_cp_ready())

    def test_proxy_state_ready(self):
        self.assertEqual(self.s["proxy_state"], "ready")

    def test_catalog_state_ready(self):
        self.assertEqual(self.s["catalog_state"], "ready")

    def test_backend_state_healthy(self):
        self.assertEqual(self.s["backend_state"], "healthy")

    def test_model_state_loaded(self):
        self.assertEqual(self.s["model_state"], "loaded")

    def test_request_admission_accepted(self):
        self.assertEqual(self.s["request_admission"], "accepted")

    def test_recovery_state_none(self):
        self.assertEqual(self.s["recovery_state"], "none")

    def test_not_recoverable_not_fatal(self):
        self.assertFalse(self.s["recoverable"])
        self.assertFalse(self.s["retryable"])
        self.assertFalse(self.s["fatal"])

    def test_operator_action_empty(self):
        self.assertEqual(self.s["operator_action"], "")

    def test_no_last_error(self):
        self.assertEqual(self.s["last_error"], "")


# ---------------------------------------------------------------------------
# Scenario 2: proxy initializing
# ---------------------------------------------------------------------------

class ProxyInitializingTests(unittest.TestCase):
    def setUp(self):
        self.s = build_service_status(_cp_proxy_initializing())

    def test_proxy_state_initializing(self):
        self.assertEqual(self.s["proxy_state"], "initializing")

    def test_request_admission_rejected_proxy(self):
        self.assertEqual(self.s["request_admission"], "rejected_proxy_not_ready")

    def test_recovery_in_progress(self):
        self.assertEqual(self.s["recovery_state"], "in_progress")

    def test_recoverable_and_retryable(self):
        self.assertTrue(self.s["recoverable"])
        self.assertTrue(self.s["retryable"])
        self.assertFalse(self.s["fatal"])

    def test_operator_action_remote_wait(self):
        self.assertEqual(self.s["operator_action"], "remote_wait")


# ---------------------------------------------------------------------------
# Scenario 3: backend unreachable
# ---------------------------------------------------------------------------

class BackendUnreachableTests(unittest.TestCase):
    def setUp(self):
        self.s = build_service_status(_cp_backend_unreachable())

    def test_proxy_state_ready(self):
        self.assertEqual(self.s["proxy_state"], "ready")

    def test_backend_state_unreachable(self):
        self.assertEqual(self.s["backend_state"], "unreachable")

    def test_request_admission_rejected_backend_unavailable(self):
        self.assertEqual(self.s["request_admission"], "rejected_backend_unavailable")

    def test_recovery_available(self):
        self.assertEqual(self.s["recovery_state"], "available")

    def test_recoverable_not_retryable(self):
        self.assertTrue(self.s["recoverable"])
        self.assertFalse(self.s["retryable"])
        self.assertFalse(self.s["fatal"])

    def test_operator_action_start_backend(self):
        self.assertEqual(self.s["operator_action"], "start_backend")

    def test_last_error_from_backend(self):
        self.assertIn("refused", self.s["last_error"].lower())

    def test_operator_hints_carried_forward(self):
        self.assertTrue(len(self.s["operator_hints"]) >= 1)


# ---------------------------------------------------------------------------
# Scenario 4: model not loaded
# ---------------------------------------------------------------------------

class ModelNotLoadedTests(unittest.TestCase):
    def setUp(self):
        self.s = build_service_status(_cp_model_not_loaded())

    def test_backend_state_healthy(self):
        self.assertEqual(self.s["backend_state"], "healthy")

    def test_model_state_unloaded(self):
        self.assertEqual(self.s["model_state"], "unloaded")

    def test_request_admission_rejected_model_not_loaded(self):
        self.assertEqual(self.s["request_admission"], "rejected_model_not_loaded")

    def test_recovery_available(self):
        self.assertEqual(self.s["recovery_state"], "available")

    def test_recoverable(self):
        self.assertTrue(self.s["recoverable"])
        self.assertFalse(self.s["fatal"])


# ---------------------------------------------------------------------------
# Scenario 5: restart required (context/identity mismatch)
# ---------------------------------------------------------------------------

class RestartRequiredTests(unittest.TestCase):
    def setUp(self):
        self.s = build_service_status(_cp_restart_required())

    def test_model_state_mismatch(self):
        self.assertEqual(self.s["model_state"], "mismatch")

    def test_recovery_available(self):
        self.assertEqual(self.s["recovery_state"], "available")

    def test_operator_action_restart_backend(self):
        self.assertEqual(self.s["operator_action"], "restart_backend")


# ---------------------------------------------------------------------------
# Proxy failed scenario
# ---------------------------------------------------------------------------

class ProxyFailedTests(unittest.TestCase):
    def setUp(self):
        cp = _cp_proxy_initializing()
        cp["proxy_initialization"]["state"] = "failed"
        cp["proxy_initialization"]["error"] = "startup exception"
        self.s = build_service_status(cp)

    def test_proxy_state_failed(self):
        self.assertEqual(self.s["proxy_state"], "failed")

    def test_fatal_true(self):
        self.assertTrue(self.s["fatal"])

    def test_not_recoverable(self):
        self.assertFalse(self.s["recoverable"])

    def test_last_error_from_init(self):
        self.assertIn("startup", self.s["last_error"])


if __name__ == "__main__":
    unittest.main()
