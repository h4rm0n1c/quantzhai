"""Tests for POST /qz/recovery/trigger helpers (proxy/qz_request_router.py)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_request_router import (
    ALLOWED_RECOVERY_ACTIONS,
    RECOVERY_TRIGGER_SCHEMA,
    SAFE_TRIGGER_ACTIONS,
    UNIMPLEMENTED_TRIGGER_ACTIONS,
    RequestRouter,
)
from proxy.qz_recovery_state import RecoveryRuntimeState


# ---------------------------------------------------------------------------
# 1. Module-level constants
# ---------------------------------------------------------------------------

class TriggerConstantsTests(unittest.TestCase):
    def test_safe_actions(self):
        self.assertEqual(SAFE_TRIGGER_ACTIONS, frozenset({"refresh_catalog", "clear_failure"}))

    def test_unimplemented_actions(self):
        expected = ALLOWED_RECOVERY_ACTIONS - SAFE_TRIGGER_ACTIONS
        self.assertEqual(UNIMPLEMENTED_TRIGGER_ACTIONS, expected)

    def test_restart_backend_unimplemented(self):
        self.assertIn("restart_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_start_backend_unimplemented(self):
        self.assertIn("start_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_reload_unimplemented(self):
        self.assertIn("reload_selected_model", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_refresh_catalog_safe(self):
        self.assertIn("refresh_catalog", SAFE_TRIGGER_ACTIONS)

    def test_clear_failure_safe(self):
        self.assertIn("clear_failure", SAFE_TRIGGER_ACTIONS)

    def test_trigger_schema(self):
        self.assertEqual(RECOVERY_TRIGGER_SCHEMA, "qz.recovery.trigger.v1")


# ---------------------------------------------------------------------------
# 2. _build_trigger_response
# ---------------------------------------------------------------------------

class BuildTriggerResponseTests(unittest.TestCase):
    def _make(self, **kwargs):
        defaults = dict(
            action="clear_failure",
            request_id="rec-abc123",
            accepted=True,
            pre_status=None,
            post_status=None,
        )
        defaults.update(kwargs)
        return RequestRouter._build_trigger_response(**defaults)

    def test_schema(self):
        p = self._make()
        self.assertEqual(p["schema"], RECOVERY_TRIGGER_SCHEMA)

    def test_accepted_true(self):
        p = self._make(accepted=True)
        self.assertTrue(p["accepted"])

    def test_action_field(self):
        p = self._make(action="refresh_catalog")
        self.assertEqual(p["action"], "refresh_catalog")

    def test_dry_run_false(self):
        p = self._make()
        self.assertFalse(p["dry_run"])

    def test_request_id(self):
        p = self._make(request_id="rec-xyz")
        self.assertEqual(p["request_id"], "rec-xyz")

    def test_pre_post_status(self):
        pre = {"state": "pre"}
        post = {"state": "post"}
        p = self._make(pre_status=pre, post_status=post)
        self.assertEqual(p["pre_status"], pre)
        self.assertEqual(p["post_status"], post)

    def test_operator_warning(self):
        p = self._make(operator_warning="Watch out!")
        self.assertEqual(p["operator_warning"], "Watch out!")

    def test_telemetry_event(self):
        p = self._make(telemetry_event="recovery_action_completed")
        self.assertEqual(p["telemetry_event"], "recovery_action_completed")

    def test_json_serialisable(self):
        p = self._make(pre_status={"ok": True}, post_status={"ok": True})
        json.dumps(p)  # must not raise

    def test_required_fields_present(self):
        p = self._make()
        for f in ("schema", "accepted", "action", "dry_run", "request_id",
                  "pre_status", "post_status", "operator_warning", "telemetry_event"):
            self.assertIn(f, p, f"missing field: {f}")


# ---------------------------------------------------------------------------
# 3. _recovery_error_payload — trigger-specific shapes
# ---------------------------------------------------------------------------

class TriggerErrorPayloadTests(unittest.TestCase):
    def _make(self, **kwargs):
        return RequestRouter._recovery_error_payload(**kwargs)

    def test_authority_disabled_shape(self):
        p = self._make(
            error="authority_disabled",
            message="QZ_RECOVERY_ACTIONS is not set to 1.",
            blocked_by="authority",
        )
        self.assertEqual(p["schema"], "qz.recovery.error.v1")
        self.assertFalse(p["ok"])
        self.assertEqual(p["error"], "authority_disabled")
        self.assertEqual(p["blocked_by"], "authority")

    def test_non_local_shape(self):
        p = self._make(
            error="non_local_request",
            message="Loopback only.",
            blocked_by="authority",
        )
        self.assertEqual(p["blocked_by"], "authority")

    def test_action_not_implemented_shape(self):
        p = self._make(
            error="action_not_implemented",
            message="restart_backend not implemented.",
            action="restart_backend",
            blocked_by="state",
        )
        self.assertEqual(p["action"], "restart_backend")
        self.assertEqual(p["error"], "action_not_implemented")

    def test_force_not_allowed_shape(self):
        p = self._make(
            error="force_not_allowed",
            message="force=true not valid for clear_failure.",
            action="clear_failure",
            blocked_by="bad_request",
        )
        self.assertEqual(p["blocked_by"], "bad_request")

    def test_missing_reason_shape(self):
        p = self._make(
            error="missing_reason",
            message="reason field required.",
            action="refresh_catalog",
            blocked_by="bad_request",
        )
        self.assertEqual(p["error"], "missing_reason")


# ---------------------------------------------------------------------------
# 4. _emit_recovery_event — no-ops safely
# ---------------------------------------------------------------------------

class EmitRecoveryEventTests(unittest.TestCase):
    def _make_router_with_telemetry(self, telemetry):
        class _FakeTelemetry:
            def __init__(self, impl):
                self._impl = impl
                self.events = []

            def emit(self, event_type, payload):
                if self._impl == "raise":
                    raise RuntimeError("telemetry broken")
                self.events.append((event_type, payload))

        class _FakeHandler:
            pass

        handler = _FakeHandler()
        handler.telemetry = _FakeTelemetry(telemetry)

        router = RequestRouter.__new__(RequestRouter)
        router.handler = handler
        return router, handler.telemetry

    def test_emits_event(self):
        router, tel = self._make_router_with_telemetry("ok")
        router._emit_recovery_event("recovery_action_started", {"action": "clear_failure"})
        self.assertEqual(len(tel.events), 1)
        self.assertEqual(tel.events[0][0], "recovery_action_started")

    def test_no_ops_on_exception(self):
        router, _ = self._make_router_with_telemetry("raise")
        # must not raise
        router._emit_recovery_event("recovery_action_started", {"action": "clear_failure"})

    def test_missing_telemetry_no_ops(self):
        class _FakeHandler:
            pass  # no telemetry attribute

        router = RequestRouter.__new__(RequestRouter)
        router.handler = _FakeHandler()
        # must not raise
        router._emit_recovery_event("recovery_trigger_requested", {})


# ---------------------------------------------------------------------------
# 5. _do_clear_failure
# ---------------------------------------------------------------------------

class DoClearFailureTests(unittest.TestCase):
    def _make_router(self):
        router = RequestRouter.__new__(RequestRouter)
        return router

    def test_clears_recovery_state(self):
        router = self._make_router()
        rs = RecoveryRuntimeState()
        t = 1_000_000.0
        rs.mark_failed("restart_backend", "err", now=t)
        self.assertTrue(rs.is_backoff_active("restart_backend", now=t + 1))
        ok, error = router._do_clear_failure(rs)
        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertFalse(rs.is_backoff_active("restart_backend", now=t + 1))

    def test_clears_in_progress(self):
        router = self._make_router()
        rs = RecoveryRuntimeState()
        rs.mark_started("clear_failure")
        ok, _ = router._do_clear_failure(rs)
        self.assertTrue(ok)
        self.assertFalse(rs.is_recovery_in_progress())

    def test_returns_true_ok_on_empty_state(self):
        router = self._make_router()
        rs = RecoveryRuntimeState()
        ok, error = router._do_clear_failure(rs)
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_returns_false_on_exception(self):
        router = self._make_router()

        class _BrokenRS:
            def clear(self):
                raise RuntimeError("storage full")

        ok, error = router._do_clear_failure(_BrokenRS())
        self.assertFalse(ok)
        self.assertIn("storage full", error)


# ---------------------------------------------------------------------------
# 6. request_id format
# ---------------------------------------------------------------------------

class RequestIdTests(unittest.TestCase):
    def test_request_id_starts_with_rec(self):
        import uuid
        req_id = f"rec-{uuid.uuid4().hex[:12]}"
        self.assertTrue(req_id.startswith("rec-"))
        self.assertEqual(len(req_id), 16)  # "rec-" + 12 hex chars

    def test_request_ids_unique(self):
        import uuid
        ids = {f"rec-{uuid.uuid4().hex[:12]}" for _ in range(20)}
        self.assertEqual(len(ids), 20)


# ---------------------------------------------------------------------------
# 7. Integration: trigger constants consistency
# ---------------------------------------------------------------------------

class TriggerConstantsConsistencyTests(unittest.TestCase):
    def test_safe_plus_unimplemented_equals_all(self):
        self.assertEqual(
            SAFE_TRIGGER_ACTIONS | UNIMPLEMENTED_TRIGGER_ACTIONS,
            ALLOWED_RECOVERY_ACTIONS,
        )

    def test_no_overlap(self):
        self.assertEqual(
            SAFE_TRIGGER_ACTIONS & UNIMPLEMENTED_TRIGGER_ACTIONS,
            frozenset(),
        )


if __name__ == "__main__":
    unittest.main()
