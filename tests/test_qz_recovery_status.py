"""Tests for qz.recovery.status.v1 builder (proxy/qz_recovery_status.py)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_recovery_status import RECOVERY_STATUS_SCHEMA, build_recovery_status


# ---------------------------------------------------------------------------
# Service status payloads for common scenarios
# ---------------------------------------------------------------------------

def _ss(recovery_state="none", operator_action="", recoverable=False, retryable=False,
        fatal=False, last_error="", hints=None):
    return {
        "schema": "qz.service.status.v1",
        "proxy_state": "ready",
        "catalog_state": "ready",
        "backend_state": "healthy",
        "model_state": "loaded",
        "request_admission": "accepted",
        "recovery_state": recovery_state,
        "recoverable": recoverable,
        "retryable": retryable,
        "fatal": fatal,
        "last_error": last_error,
        "operator_action": operator_action,
        "operator_hints": hints or [],
    }


# ---------------------------------------------------------------------------
# Schema / structure
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    def test_schema_field(self):
        p = build_recovery_status(_ss())
        self.assertEqual(p["schema"], RECOVERY_STATUS_SCHEMA)
        self.assertEqual(p["schema"], "qz.recovery.status.v1")

    def test_required_fields_present(self):
        p = build_recovery_status(_ss())
        for field in (
            "schema", "ok", "state", "recoverable", "retryable", "fatal",
            "operator_action", "last_error",
            "remote_client_action", "local_operator_action",
            "summary", "service_status", "operator_hints",
        ):
            self.assertIn(field, p, f"missing field: {field}")

    def test_json_serialisable(self):
        for ss in (
            _ss(),
            _ss("in_progress", "remote_wait", True, True),
            _ss("available", "start_backend", True, False),
        ):
            rt = json.loads(json.dumps(build_recovery_status(ss)))
            self.assertEqual(rt["schema"], RECOVERY_STATUS_SCHEMA)

    def test_none_input_safe(self):
        p = build_recovery_status(None)  # type: ignore[arg-type]
        self.assertEqual(p["schema"], RECOVERY_STATUS_SCHEMA)
        self.assertFalse(p["ok"])

    def test_empty_dict_safe(self):
        p = build_recovery_status({})
        self.assertEqual(p["schema"], RECOVERY_STATUS_SCHEMA)


# ---------------------------------------------------------------------------
# Scenario 1: no recovery needed (all ready)
# ---------------------------------------------------------------------------

class NoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(_ss("none", "", False, False, False))

    def test_ok_true(self):
        self.assertTrue(self.p["ok"])

    def test_state_none(self):
        self.assertEqual(self.p["state"], "none")

    def test_not_recoverable(self):
        self.assertFalse(self.p["recoverable"])
        self.assertFalse(self.p["retryable"])
        self.assertFalse(self.p["fatal"])

    def test_no_action(self):
        self.assertEqual(self.p["remote_client_action"], "")
        self.assertEqual(self.p["local_operator_action"], "")
        self.assertEqual(self.p["operator_action"], "")

    def test_summary_no_action(self):
        self.assertIn("No recovery", self.p["summary"])

    def test_service_status_embedded(self):
        self.assertIsNotNone(self.p["service_status"])
        self.assertEqual(self.p["service_status"]["recovery_state"], "none")


# ---------------------------------------------------------------------------
# Scenario 2: in progress / initializing
# ---------------------------------------------------------------------------

class InProgressTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(_ss("in_progress", "remote_wait", True, True))

    def test_ok_true(self):
        self.assertTrue(self.p["ok"])

    def test_state_in_progress(self):
        self.assertEqual(self.p["state"], "in_progress")

    def test_remote_client_wait(self):
        self.assertEqual(self.p["remote_client_action"], "wait")

    def test_local_operator_monitor(self):
        self.assertEqual(self.p["local_operator_action"], "monitor")

    def test_retryable(self):
        self.assertTrue(self.p["retryable"])

    def test_summary_in_progress(self):
        self.assertIn("progress", self.p["summary"].lower())


# ---------------------------------------------------------------------------
# Scenario 3: backend unreachable
# ---------------------------------------------------------------------------

class BackendUnreachableTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(
            _ss("available", "start_backend", True, False, False,
                "Connection refused", ["Contact operator."])
        )

    def test_ok_false(self):
        self.assertFalse(self.p["ok"])

    def test_state_available(self):
        self.assertEqual(self.p["state"], "available")

    def test_remote_contact_operator(self):
        self.assertEqual(self.p["remote_client_action"], "contact_operator")

    def test_local_start_backend(self):
        self.assertEqual(self.p["local_operator_action"], "start_backend")

    def test_operator_action(self):
        self.assertEqual(self.p["operator_action"], "start_backend")

    def test_last_error_carried(self):
        self.assertIn("refused", self.p["last_error"].lower())

    def test_hints_carried(self):
        self.assertIn("Contact operator.", self.p["operator_hints"])

    def test_summary_backend(self):
        self.assertIn("backend", self.p["summary"].lower())


# ---------------------------------------------------------------------------
# Scenario 4: restart required
# ---------------------------------------------------------------------------

class RestartRequiredTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(_ss("available", "restart_backend", True, False))

    def test_local_restart_backend(self):
        self.assertEqual(self.p["local_operator_action"], "restart_backend")

    def test_remote_contact_operator(self):
        self.assertEqual(self.p["remote_client_action"], "contact_operator")

    def test_summary_restart(self):
        self.assertIn("restart", self.p["summary"].lower())


# ---------------------------------------------------------------------------
# Scenario 5: model selection problem
# ---------------------------------------------------------------------------

class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(_ss("available", "select_model", True, False))

    def test_remote_choose_valid_model(self):
        self.assertEqual(self.p["remote_client_action"], "choose_valid_model")

    def test_local_select_model(self):
        self.assertEqual(self.p["local_operator_action"], "select_model")

    def test_summary_model(self):
        self.assertIn("model", self.p["summary"].lower())


# ---------------------------------------------------------------------------
# Scenario 6: inspect logs / manual
# ---------------------------------------------------------------------------

class InspectLogsTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(_ss("available", "inspect_logs", True, False))

    def test_remote_contact_operator(self):
        self.assertEqual(self.p["remote_client_action"], "contact_operator")

    def test_local_inspect_logs(self):
        self.assertEqual(self.p["local_operator_action"], "inspect_logs")


# ---------------------------------------------------------------------------
# Scenario 7: fatal
# ---------------------------------------------------------------------------

class FatalTests(unittest.TestCase):
    def setUp(self):
        self.p = build_recovery_status(
            _ss("failed", "inspect_logs", False, False, True, "startup exception")
        )

    def test_ok_false(self):
        self.assertFalse(self.p["ok"])

    def test_fatal_true(self):
        self.assertTrue(self.p["fatal"])

    def test_summary_mentions_manual(self):
        self.assertIn("manual", self.p["summary"].lower())


# ---------------------------------------------------------------------------
# Control-plane embedding
# ---------------------------------------------------------------------------

class ControlPlaneEmbeddingTests(unittest.TestCase):
    def test_recovery_embedded_in_control_plane(self):
        from proxy.qz_control_plane import build_control_plane_status
        from tests.test_qz_control_plane import _make_handler
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertIn("recovery", p)
        r = p["recovery"]
        self.assertEqual(r.get("schema"), RECOVERY_STATUS_SCHEMA)

    def test_recovery_has_state_field(self):
        from proxy.qz_control_plane import build_control_plane_status
        from tests.test_qz_control_plane import _make_handler
        h = _make_handler()
        p = build_control_plane_status(h)
        r = p["recovery"]
        self.assertIn("state", r)

    def test_existing_cp_fields_still_present(self):
        from proxy.qz_control_plane import build_control_plane_status
        from tests.test_qz_control_plane import _make_handler
        h = _make_handler()
        p = build_control_plane_status(h)
        for field in ("schema", "ok", "status", "readiness", "proxy_initialization",
                      "models", "backend", "codex_catalog", "operator_hints",
                      "service_status"):
            self.assertIn(field, p, f"existing field removed: {field}")


if __name__ == "__main__":
    unittest.main()
