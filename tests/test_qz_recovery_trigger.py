"""Tests for POST /qz/recovery/trigger helpers (proxy/qz_request_router.py)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_request_router import (
    ALLOWED_RECOVERY_ACTIONS,
    DANGEROUS_TRIGGER_ACTIONS,
    IMPLEMENTED_TRIGGER_ACTIONS,
    RECOVERY_TRIGGER_SCHEMA,
    SAFE_TRIGGER_ACTIONS,
    UNIMPLEMENTED_TRIGGER_ACTIONS,
    RequestRouter,
)
from proxy.qz_recovery_state import RecoveryRuntimeState
import unittest.mock


# ---------------------------------------------------------------------------
# 1. Module-level constants
# ---------------------------------------------------------------------------

class TriggerConstantsTests(unittest.TestCase):
    def test_safe_actions(self):
        self.assertEqual(SAFE_TRIGGER_ACTIONS, frozenset({"refresh_catalog", "clear_failure"}))

    def test_unimplemented_actions(self):
        from proxy.qz_request_router import IMPLEMENTED_TRIGGER_ACTIONS
        expected = ALLOWED_RECOVERY_ACTIONS - IMPLEMENTED_TRIGGER_ACTIONS
        self.assertEqual(UNIMPLEMENTED_TRIGGER_ACTIONS, expected)

    def test_restart_backend_now_implemented(self):
        self.assertNotIn("restart_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_restart_backend_dangerous(self):
        self.assertIn("restart_backend", DANGEROUS_TRIGGER_ACTIONS)

    def test_start_backend_unimplemented(self):
        self.assertIn("start_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_reload_now_implemented(self):
        self.assertNotIn("reload_selected_model", UNIMPLEMENTED_TRIGGER_ACTIONS)

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
    def test_implemented_plus_unimplemented_equals_all(self):
        self.assertEqual(
            IMPLEMENTED_TRIGGER_ACTIONS | UNIMPLEMENTED_TRIGGER_ACTIONS,
            ALLOWED_RECOVERY_ACTIONS,
        )

    def test_no_overlap_safe_dangerous(self):
        self.assertEqual(
            SAFE_TRIGGER_ACTIONS & DANGEROUS_TRIGGER_ACTIONS,
            frozenset(),
        )

    def test_no_overlap_implemented_unimplemented(self):
        self.assertEqual(
            IMPLEMENTED_TRIGGER_ACTIONS & UNIMPLEMENTED_TRIGGER_ACTIONS,
            frozenset(),
        )


# ---------------------------------------------------------------------------
# 8. _recovery_authority_enabled / _recovery_local_only_enabled
# ---------------------------------------------------------------------------

class EnvFlagHelpersTests(unittest.TestCase):
    def test_authority_disabled_by_default(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os_env_backup = os.environ.pop("QZ_RECOVERY_ACTIONS", None)
            try:
                self.assertFalse(RequestRouter._recovery_authority_enabled())
            finally:
                if os_env_backup is not None:
                    os.environ["QZ_RECOVERY_ACTIONS"] = os_env_backup

    def test_authority_enabled_when_1(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_ACTIONS": "1"}):
            self.assertTrue(RequestRouter._recovery_authority_enabled())

    def test_authority_disabled_when_0(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_ACTIONS": "0"}):
            self.assertFalse(RequestRouter._recovery_authority_enabled())

    def test_local_only_enabled_by_default(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            backup = os.environ.pop("QZ_RECOVERY_BIND_LOCAL_ONLY", None)
            try:
                self.assertTrue(RequestRouter._recovery_local_only_enabled())
            finally:
                if backup is not None:
                    os.environ["QZ_RECOVERY_BIND_LOCAL_ONLY"] = backup

    def test_local_only_disabled_when_0(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_BIND_LOCAL_ONLY": "0"}):
            self.assertFalse(RequestRouter._recovery_local_only_enabled())

    def test_local_only_enabled_when_1(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_BIND_LOCAL_ONLY": "1"}):
            self.assertTrue(RequestRouter._recovery_local_only_enabled())


# ---------------------------------------------------------------------------
# 9. _confirm_phrase_required
# ---------------------------------------------------------------------------

class ConfirmPhraseRequiredTests(unittest.TestCase):
    def test_not_required_when_env_unset_for_safe_action(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            self.assertFalse(RequestRouter._confirm_phrase_required("refresh_catalog"))

    def test_always_required_for_dangerous_action_even_env_unset(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            self.assertTrue(RequestRouter._confirm_phrase_required("restart_backend"))

    def test_not_required_for_safe_action_even_when_env_set(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            self.assertFalse(RequestRouter._confirm_phrase_required("refresh_catalog"))
            self.assertFalse(RequestRouter._confirm_phrase_required("clear_failure"))

    def test_required_for_dangerous_action_when_env_set(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            self.assertTrue(RequestRouter._confirm_phrase_required("restart_backend"))
            self.assertTrue(RequestRouter._confirm_phrase_required("start_backend"))
            self.assertTrue(RequestRouter._confirm_phrase_required("reload_selected_model"))


# ---------------------------------------------------------------------------
# 10. _confirm_phrase_matches
# ---------------------------------------------------------------------------

class ConfirmPhraseMatchesTests(unittest.TestCase):
    def test_ok_when_env_unset_for_safe_action(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            ok, msg = RequestRouter._confirm_phrase_matches({}, "refresh_catalog")
            self.assertTrue(ok)
            self.assertEqual(msg, "")

    def test_rejected_for_restart_backend_when_env_unset(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            ok, msg = RequestRouter._confirm_phrase_matches({}, "restart_backend")
            self.assertFalse(ok)
            self.assertIn("QZ_RECOVERY_CONFIRM_PHRASE", msg)

    def test_ok_for_safe_action_even_with_env(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            ok, msg = RequestRouter._confirm_phrase_matches({}, "refresh_catalog")
            self.assertTrue(ok)
            ok2, _ = RequestRouter._confirm_phrase_matches({}, "clear_failure")
            self.assertTrue(ok2)

    def test_ok_when_phrase_matches(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "let me restart"}):
            body = {"confirm": "let me restart"}
            ok, msg = RequestRouter._confirm_phrase_matches(body, "restart_backend")
            self.assertTrue(ok)
            self.assertEqual(msg, "")

    def test_fail_when_phrase_missing(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            ok, msg = RequestRouter._confirm_phrase_matches({}, "restart_backend")
            self.assertFalse(ok)
            self.assertIn("confirm", msg.lower())

    def test_fail_when_phrase_wrong(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            body = {"confirm": "wrong phrase"}
            ok, msg = RequestRouter._confirm_phrase_matches(body, "restart_backend")
            self.assertFalse(ok)
            self.assertIn("mismatch", msg.lower())

    def test_match_is_exact(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            body = {"confirm": "Secret"}  # different case
            ok, _ = RequestRouter._confirm_phrase_matches(body, "restart_backend")
            self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 11. _validate_recovery_trigger_body
# ---------------------------------------------------------------------------

class ValidateTriggerBodyTests(unittest.TestCase):
    def _good_body(self):
        return {"action": "clear_failure", "reason": "test"}

    def test_valid_body_returns_none(self):
        self.assertIsNone(RequestRouter._validate_recovery_trigger_body(self._good_body()))

    def test_missing_action(self):
        result = RequestRouter._validate_recovery_trigger_body({"reason": "x"})
        self.assertIsNotNone(result)
        status, error, blocked_by, msg = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "missing_action")
        self.assertEqual(blocked_by, "bad_request")

    def test_missing_reason(self):
        result = RequestRouter._validate_recovery_trigger_body({"action": "clear_failure"})
        self.assertIsNotNone(result)
        status, error, blocked_by, msg = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "missing_reason")

    def test_unknown_action(self):
        result = RequestRouter._validate_recovery_trigger_body({"action": "kaboom", "reason": "x"})
        self.assertIsNotNone(result)
        status, error, blocked_by, msg = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "unknown_action")

    def test_restart_backend_now_valid(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "restart_backend", "reason": "x"}
        )
        # restart_backend is now implemented — body validation passes
        self.assertIsNone(result)

    def test_unimplemented_action_start_backend(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "start_backend", "reason": "x"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 409)
        self.assertEqual(error, "action_not_implemented")

    def test_force_true_on_safe_action(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "clear_failure", "reason": "x", "force": True}
        )
        self.assertIsNotNone(result)
        status, error, blocked_by, msg = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "force_not_allowed")
        self.assertEqual(blocked_by, "bad_request")

    def test_force_false_on_safe_action_ok(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "refresh_catalog", "reason": "x", "force": False}
        )
        self.assertIsNone(result)

    def test_unimplemented_action_message_mentions_implemented(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "start_backend", "reason": "x"}
        )
        _, _, _, msg = result
        self.assertIn("refresh_catalog", msg)
        self.assertIn("clear_failure", msg)


# ---------------------------------------------------------------------------
# 12. Rejection payloads include recovery_status for 409
# ---------------------------------------------------------------------------

class RejectionPayloadWithStatusTests(unittest.TestCase):
    def test_recovery_error_payload_includes_recovery_status(self):
        status_snap = {"schema": "qz.recovery.status.v1", "state": "none"}
        p = RequestRouter._recovery_error_payload(
            "action_not_implemented",
            "not available",
            action="restart_backend",
            blocked_by="state",
            recovery_status=status_snap,
        )
        self.assertEqual(p["recovery_status"], status_snap)
        self.assertEqual(p["schema"], "qz.recovery.error.v1")

    def test_recovery_error_payload_recovery_status_none_by_default(self):
        p = RequestRouter._recovery_error_payload("bad_confirm", "bad phrase")
        self.assertIsNone(p["recovery_status"])


# ---------------------------------------------------------------------------
# 13. Updated constants — restart_backend now implemented
# ---------------------------------------------------------------------------

class UpdatedConstantsTests(unittest.TestCase):
    def test_restart_backend_in_dangerous(self):
        self.assertIn("restart_backend", DANGEROUS_TRIGGER_ACTIONS)

    def test_restart_backend_in_implemented(self):
        self.assertIn("restart_backend", IMPLEMENTED_TRIGGER_ACTIONS)

    def test_restart_backend_not_in_unimplemented(self):
        self.assertNotIn("restart_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_start_backend_still_unimplemented(self):
        self.assertIn("start_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_reload_selected_model_now_implemented(self):
        self.assertNotIn("reload_selected_model", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_select_model_still_unimplemented(self):
        self.assertIn("select_model", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_implemented_subset_of_all(self):
        self.assertTrue(IMPLEMENTED_TRIGGER_ACTIONS <= ALLOWED_RECOVERY_ACTIONS)

    def test_dangerous_subset_of_implemented(self):
        self.assertTrue(DANGEROUS_TRIGGER_ACTIONS <= IMPLEMENTED_TRIGGER_ACTIONS)

    def test_safe_and_dangerous_disjoint(self):
        self.assertEqual(SAFE_TRIGGER_ACTIONS & DANGEROUS_TRIGGER_ACTIONS, frozenset())


# ---------------------------------------------------------------------------
# 14. force=true handling for restart_backend vs safe actions
# ---------------------------------------------------------------------------

class ForceValidationTests(unittest.TestCase):
    def test_force_allowed_for_restart_backend(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "restart_backend", "reason": "x", "force": True}
        )
        self.assertIsNone(result)

    def test_force_still_rejected_for_refresh_catalog(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "refresh_catalog", "reason": "x", "force": True}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "force_not_allowed")

    def test_force_still_rejected_for_clear_failure(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "clear_failure", "reason": "x", "force": True}
        )
        self.assertIsNotNone(result)
        _, error, _, _ = result
        self.assertEqual(error, "force_not_allowed")


# ---------------------------------------------------------------------------
# 15. _confirm_phrase_required for dangerous actions
# ---------------------------------------------------------------------------

class DangerousConfirmPhraseRequiredTests(unittest.TestCase):
    def test_always_required_for_restart_backend(self):
        import unittest.mock as mock
        # Even when env unset, dangerous action requires phrase
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            self.assertTrue(RequestRouter._confirm_phrase_required("restart_backend"))

    def test_always_required_even_with_phrase_set(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "secret"}):
            self.assertTrue(RequestRouter._confirm_phrase_required("restart_backend"))


# ---------------------------------------------------------------------------
# 16. _confirm_phrase_matches for restart_backend (dangerous action)
# ---------------------------------------------------------------------------

class DangerousConfirmPhraseMatchesTests(unittest.TestCase):
    def test_rejected_when_env_unset(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            ok, msg = RequestRouter._confirm_phrase_matches({}, "restart_backend")
            self.assertFalse(ok)
            self.assertIn("QZ_RECOVERY_CONFIRM_PHRASE", msg)

    def test_rejected_when_confirm_missing(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "go go go"}):
            ok, msg = RequestRouter._confirm_phrase_matches({"reason": "x"}, "restart_backend")
            self.assertFalse(ok)
            self.assertIn("confirm", msg.lower())

    def test_rejected_when_phrase_wrong(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "go go go"}):
            ok, msg = RequestRouter._confirm_phrase_matches(
                {"confirm": "stop stop stop"}, "restart_backend"
            )
            self.assertFalse(ok)
            self.assertIn("mismatch", msg.lower())

    def test_accepted_when_exact_match(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "go go go"}):
            ok, msg = RequestRouter._confirm_phrase_matches(
                {"confirm": "go go go"}, "restart_backend"
            )
            self.assertTrue(ok)
            self.assertEqual(msg, "")

    def test_case_sensitive_rejection(self):
        import unittest.mock as mock
        with mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "go go go"}):
            ok, _ = RequestRouter._confirm_phrase_matches(
                {"confirm": "Go Go Go"}, "restart_backend"
            )
            self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 17. _validate_recovery_trigger_body for restart_backend
# ---------------------------------------------------------------------------

class RestartBackendBodyValidationTests(unittest.TestCase):
    def test_restart_backend_valid_no_force(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "restart_backend", "reason": "test"}
        )
        self.assertIsNone(result)

    def test_restart_backend_valid_with_force(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "restart_backend", "reason": "test", "force": True}
        )
        self.assertIsNone(result)

    def test_restart_backend_missing_reason_rejected(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "restart_backend"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "missing_reason")

    def test_start_backend_still_409(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "start_backend", "reason": "x"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 409)
        self.assertEqual(error, "action_not_implemented")

    def test_unimplemented_message_updated(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "select_model", "reason": "x"}
        )
        _, _, _, msg = result
        self.assertIn("restart_backend", msg)


# ---------------------------------------------------------------------------
# 18. _do_restart_backend with fake handler
# ---------------------------------------------------------------------------

class DoRestartBackendTests(unittest.TestCase):
    def _make_router(self, backend_ok=True, context_length=131072):
        class _FakeBackend:
            def __init__(self, ok):
                self._ok = ok
                self.called_with = None

            def restart_container(self, ctx):
                self.called_with = ctx
                if not self._ok:
                    raise RuntimeError("Docker failed")
                return {"health_status": 200}

        class _FakeRouter:
            def __init__(self, ctx):
                self._ctx = ctx

            def backend_context_length(self):
                return self._ctx

        class _FakeHandlerClass:
            model_load_state = "failed"
            model_load_error = "prev error"
            model_load_finished_at = None

        class _FakeHandler:
            __class__ = _FakeHandlerClass

            def __init__(self, ok, ctx):
                self._backend_obj = _FakeBackend(ok)
                self._router_obj = _FakeRouter(ctx)

            def _backend(self, *a, **kw):
                return self._backend_obj

            def _model_router(self):
                return self._router_obj

        router = RequestRouter.__new__(RequestRouter)
        router.handler = _FakeHandler(backend_ok, context_length)
        return router, router.handler._backend_obj

    def test_success_returns_true(self):
        router, _ = self._make_router(backend_ok=True)
        ok, error = router._do_restart_backend()
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_success_calls_restart_container(self):
        router, backend = self._make_router(backend_ok=True, context_length=65536)
        router._do_restart_backend()
        self.assertEqual(backend.called_with, 65536)

    def test_failure_returns_false_with_message(self):
        router, _ = self._make_router(backend_ok=False)
        ok, error = router._do_restart_backend()
        self.assertFalse(ok)
        self.assertIn("Docker failed", error)

    def test_no_restart_method_safe(self):
        class _NoBackendHandler:
            class __class__:
                model_load_state = "idle"
                model_load_error = None
                model_load_finished_at = None

            def _model_router(self):
                class _R:
                    def backend_context_length(self):
                        return 0
                return _R()

            def _backend(self, *a, **kw):
                raise AttributeError("no backend")

        router = RequestRouter.__new__(RequestRouter)
        router.handler = _NoBackendHandler()
        ok, error = router._do_restart_backend()
        self.assertFalse(ok)
        self.assertGreater(len(error), 0)


# ---------------------------------------------------------------------------
# 19. reload_selected_model — constants
# ---------------------------------------------------------------------------

class ReloadConstantsTests(unittest.TestCase):
    def test_reload_in_dangerous(self):
        self.assertIn("reload_selected_model", DANGEROUS_TRIGGER_ACTIONS)

    def test_reload_in_implemented(self):
        self.assertIn("reload_selected_model", IMPLEMENTED_TRIGGER_ACTIONS)

    def test_reload_not_in_unimplemented(self):
        self.assertNotIn("reload_selected_model", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_start_backend_still_unimplemented(self):
        self.assertIn("start_backend", UNIMPLEMENTED_TRIGGER_ACTIONS)

    def test_select_model_still_unimplemented(self):
        self.assertIn("select_model", UNIMPLEMENTED_TRIGGER_ACTIONS)


# ---------------------------------------------------------------------------
# 20. force=true for reload_selected_model
# ---------------------------------------------------------------------------

class ReloadForceTests(unittest.TestCase):
    def test_force_allowed_for_reload(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "reload_selected_model", "reason": "x", "force": True}
        )
        self.assertIsNone(result)

    def test_force_still_rejected_for_safe_actions(self):
        for action in ("refresh_catalog", "clear_failure"):
            with self.subTest(action=action):
                result = RequestRouter._validate_recovery_trigger_body(
                    {"action": action, "reason": "x", "force": True}
                )
                self.assertIsNotNone(result)
                _, error, _, _ = result
                self.assertEqual(error, "force_not_allowed")


# ---------------------------------------------------------------------------
# 21. Confirmation phrase for reload_selected_model
# ---------------------------------------------------------------------------

class ReloadConfirmPhraseTests(unittest.TestCase):
    def test_phrase_required(self):
        self.assertTrue(RequestRouter._confirm_phrase_required("reload_selected_model"))

    def test_rejected_when_env_unset(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_RECOVERY_CONFIRM_PHRASE", None)
            ok, msg = RequestRouter._confirm_phrase_matches({}, "reload_selected_model")
            self.assertFalse(ok)
            self.assertIn("QZ_RECOVERY_CONFIRM_PHRASE", msg)

    def test_rejected_missing_confirm(self):
        with unittest.mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "abc"}):
            ok, msg = RequestRouter._confirm_phrase_matches({"reason": "x"}, "reload_selected_model")
            self.assertFalse(ok)

    def test_rejected_wrong_confirm(self):
        with unittest.mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "abc"}):
            ok, msg = RequestRouter._confirm_phrase_matches(
                {"confirm": "xyz"}, "reload_selected_model"
            )
            self.assertFalse(ok)
            self.assertIn("mismatch", msg.lower())

    def test_accepted_exact_match(self):
        with unittest.mock.patch.dict("os.environ", {"QZ_RECOVERY_CONFIRM_PHRASE": "abc"}):
            ok, msg = RequestRouter._confirm_phrase_matches(
                {"confirm": "abc"}, "reload_selected_model"
            )
            self.assertTrue(ok)
            self.assertEqual(msg, "")


# ---------------------------------------------------------------------------
# 22. _validate_recovery_trigger_body for reload_selected_model
# ---------------------------------------------------------------------------

class ReloadBodyValidationTests(unittest.TestCase):
    def test_valid_no_force(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "reload_selected_model", "reason": "x"}
        )
        self.assertIsNone(result)

    def test_valid_with_force(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "reload_selected_model", "reason": "x", "force": True}
        )
        self.assertIsNone(result)

    def test_missing_reason_rejected(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "reload_selected_model"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 400)
        self.assertEqual(error, "missing_reason")

    def test_start_backend_still_409(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "start_backend", "reason": "x"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 409)
        self.assertEqual(error, "action_not_implemented")

    def test_select_model_still_409(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "select_model", "reason": "x"}
        )
        self.assertIsNotNone(result)
        status, error, _, _ = result
        self.assertEqual(status, 409)

    def test_unimplemented_message_updated(self):
        result = RequestRouter._validate_recovery_trigger_body(
            {"action": "start_backend", "reason": "x"}
        )
        _, _, _, msg = result
        self.assertIn("reload_selected_model", msg)


# ---------------------------------------------------------------------------
# 23. _selected_model_for_reload
# ---------------------------------------------------------------------------

class SelectedModelForReloadTests(unittest.TestCase):
    def _make_router(self, backend_id="", entry_key=""):
        class _FakeRouter:
            def __init__(self, bid, ek):
                self._bid = bid
                self._ek = ek

            def selected_backend_id(self):
                return self._bid

            def selected_model_entry(self):
                if self._ek:
                    return {"backend_id": self._bid or self._ek, "key": self._ek}
                return None

        class _FakeHandler:
            def _model_router(self):
                return _FakeRouter(backend_id, entry_key)

        router = RequestRouter.__new__(RequestRouter)
        router.handler = _FakeHandler()
        return router

    def test_returns_backend_id(self):
        router = self._make_router(backend_id="qwen3-6b")
        model_id, err = router._selected_model_for_reload()
        self.assertEqual(model_id, "qwen3-6b")
        self.assertEqual(err, "")

    def test_falls_back_to_entry_key(self):
        router = self._make_router(backend_id="", entry_key="qwen3")
        model_id, err = router._selected_model_for_reload()
        self.assertEqual(model_id, "qwen3")
        self.assertEqual(err, "")

    def test_empty_when_no_model(self):
        router = self._make_router(backend_id="", entry_key="")
        model_id, err = router._selected_model_for_reload()
        self.assertEqual(model_id, "")
        self.assertGreater(len(err), 0)

    def test_error_message_mentions_select(self):
        router = self._make_router(backend_id="", entry_key="")
        _, err = router._selected_model_for_reload()
        self.assertIn("select", err.lower())


# ---------------------------------------------------------------------------
# 24. _do_reload_selected_model
# ---------------------------------------------------------------------------

class DoReloadSelectedModelTests(unittest.TestCase):
    """Tests for _do_reload_selected_model helper.

    Uses a proper class hierarchy (not __class__ assignment) so that
    getattr(handler.__class__, 'model_load_state', '') works correctly.
    """

    def _make_router(self, backend_id="qwen3-6b", load_succeeds=True):
        """Returns (router, inner_fake_router)."""
        inner_router_ref: list = [None]

        class FH:
            model_load_state = "idle"
            model_load_error = None
            model_load_timeout = 30.0
            restart_called = False

            def _model_router(self):
                return inner_router_ref[0]

        class _FakeRouter:
            def __init__(self, bid, hcls, succeeds):
                self._bid = bid
                self._hcls = hcls
                self._succeeds = succeeds
                self.load_called_with = None

            def selected_backend_id(self):
                return self._bid

            def selected_model_entry(self):
                return {"backend_id": self._bid} if self._bid else None

            def load_backend_model(self, model_id, wait=False, timeout=None):
                self.load_called_with = model_id
                if self._succeeds:
                    self._hcls.model_load_state = "ready"
                    self._hcls.model_load_error = None
                else:
                    self._hcls.model_load_state = "failed"
                    self._hcls.model_load_error = "load HTTP 422"

            def restart_backend_for_context(self, *a, **kw):
                FH.restart_called = True

        inner = _FakeRouter(backend_id, FH, load_succeeds)
        inner_router_ref[0] = inner

        router = RequestRouter.__new__(RequestRouter)
        router.handler = FH()
        return router, inner

    def test_success(self):
        router, _ = self._make_router(backend_id="qwen3-6b", load_succeeds=True)
        ok, error = router._do_reload_selected_model()
        self.assertTrue(ok)
        self.assertEqual(error, "")

    def test_calls_load_with_model_id(self):
        router, inner = self._make_router(backend_id="qwen3-6b", load_succeeds=True)
        router._do_reload_selected_model()
        self.assertEqual(inner.load_called_with, "qwen3-6b")

    def test_failure(self):
        router, _ = self._make_router(backend_id="qwen3-6b", load_succeeds=False)
        ok, error = router._do_reload_selected_model()
        self.assertFalse(ok)
        self.assertIn("422", error)

    def test_no_model_returns_false(self):
        router, _ = self._make_router(backend_id="", load_succeeds=True)
        ok, error = router._do_reload_selected_model()
        self.assertFalse(ok)
        self.assertGreater(len(error), 0)

    def test_does_not_call_restart(self):
        router, _ = self._make_router(backend_id="qwen3", load_succeeds=True)
        router._do_reload_selected_model()
        self.assertFalse(router.handler.__class__.restart_called)


# ---------------------------------------------------------------------------
# 25. reload_selected_model planner
# ---------------------------------------------------------------------------

class ReloadPlannerTests(unittest.TestCase):
    def _ss(self, backend_state="healthy", model_state="unloaded", recovery_state="available"):
        return {
            "schema": "qz.service.status.v1",
            "proxy_state": "ready",
            "catalog_state": "ready",
            "backend_state": backend_state,
            "model_state": model_state,
            "request_admission": "accepted",
            "recovery_state": recovery_state,
            "recoverable": True,
            "retryable": False,
            "fatal": False,
            "last_error": "",
            "operator_action": "",
            "operator_hints": [],
        }

    def test_feasible_when_backend_healthy_no_active(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss(),
            "reload_selected_model",
            authority_enabled=True,
            local_request=True,
            active_requests=0,
        )
        self.assertTrue(p["feasible"])

    def test_blocked_by_active_requests_no_force(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss(),
            "reload_selected_model",
            authority_enabled=True,
            local_request=True,
            active_requests=3,
            force=False,
        )
        self.assertTrue(p["blocked_by_active_requests"])
        self.assertFalse(p["feasible"])

    def test_force_overrides_active_requests(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss(),
            "reload_selected_model",
            authority_enabled=True,
            local_request=True,
            active_requests=3,
            force=True,
        )
        self.assertFalse(p["blocked_by_active_requests"])
        self.assertTrue(p["feasible"])

    def test_blocked_by_backend_unreachable(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss(backend_state="unreachable"),
            "reload_selected_model",
            authority_enabled=True,
            local_request=True,
            active_requests=0,
        )
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_backoff(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss(),
            "reload_selected_model",
            authority_enabled=True,
            local_request=True,
            active_requests=0,
            backoff_active=True,
        )
        self.assertTrue(p["blocked_by_backoff"])
        self.assertFalse(p["feasible"])


if __name__ == "__main__":
    unittest.main()
