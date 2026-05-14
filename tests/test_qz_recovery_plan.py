"""Tests for qz.recovery.plan.v1 builder (proxy/qz_recovery_plan.py)."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_recovery_plan import (
    ALLOWED_RECOVERY_ACTIONS,
    RECOVERY_PLAN_SCHEMA,
    build_recovery_plan,
)


# ---------------------------------------------------------------------------
# Helpers — service status payloads
# ---------------------------------------------------------------------------

def _ss(
    proxy_state="ready",
    catalog_state="ready",
    backend_state="healthy",
    model_state="loaded",
    recovery_state="none",
    model_count=1,
):
    return {
        "schema": "qz.service.status.v1",
        "proxy_state": proxy_state,
        "catalog_state": catalog_state,
        "backend_state": backend_state,
        "model_state": model_state,
        "request_admission": "accepted",
        "recovery_state": recovery_state,
        "recoverable": False,
        "retryable": False,
        "fatal": False,
        "last_error": "",
        "operator_action": "",
        "operator_hints": [],
    }


_READY = _ss()
_INITIALIZING = _ss(proxy_state="initializing", catalog_state="loading",
                    backend_state="unreachable", model_state="unknown",
                    recovery_state="in_progress")
_BACKEND_DOWN = _ss(backend_state="unreachable", model_state="unknown",
                    recovery_state="available")
_BACKEND_UNHEALTHY = _ss(backend_state="unhealthy", model_state="unknown",
                         recovery_state="available")
_MODEL_FAILED = _ss(model_state="failed", recovery_state="available")
_MODEL_MISMATCH = _ss(model_state="mismatch", recovery_state="available")
_PROXY_FAILED = _ss(proxy_state="failed", catalog_state="failed",
                    backend_state="unreachable", model_state="unknown",
                    recovery_state="failed")


# ---------------------------------------------------------------------------
# 1. Schema / structure
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    REQUIRED_FIELDS = (
        "schema", "action", "feasible",
        "blocked_by_active_requests", "blocked_by_backoff", "blocked_by_authority",
        "blocked_by_state", "blocked_by_locality", "blocked_by_in_progress",
        "blocked_by_missing_model",
        "would_interrupt_requests", "requires_authority", "requires_confirmation",
        "requires_force",
        "operator_warning", "pre_status", "notes",
    )

    def test_schema_field(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertEqual(p["schema"], RECOVERY_PLAN_SCHEMA)
        self.assertEqual(p["schema"], "qz.recovery.plan.v1")

    def test_required_fields_present(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        for f in self.REQUIRED_FIELDS:
            self.assertIn(f, p, f"missing field: {f}")

    def test_all_actions_json_serialisable(self):
        for action in ALLOWED_RECOVERY_ACTIONS:
            p = build_recovery_plan(_READY, action, model="test-model",
                                    authority_enabled=True, local_request=True)
            rt = json.loads(json.dumps(p))
            self.assertEqual(rt["schema"], RECOVERY_PLAN_SCHEMA)

    def test_none_service_status_safe(self):
        p = build_recovery_plan(None, "refresh_catalog")  # type: ignore[arg-type]
        self.assertEqual(p["schema"], RECOVERY_PLAN_SCHEMA)
        self.assertIsInstance(p["notes"], list)

    def test_empty_service_status_safe(self):
        p = build_recovery_plan({}, "refresh_catalog")
        self.assertEqual(p["schema"], RECOVERY_PLAN_SCHEMA)

    def test_notes_is_list(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertIsInstance(p["notes"], list)

    def test_pre_status_is_dict(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertIsInstance(p["pre_status"], dict)


# ---------------------------------------------------------------------------
# 2. refresh_catalog — feasible
# ---------------------------------------------------------------------------

class RefreshCatalogFeasibleTests(unittest.TestCase):
    def test_feasible_when_proxy_ready(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertTrue(p["feasible"])

    def test_feasible_catalog_failed(self):
        ss = _ss(catalog_state="failed")
        p = build_recovery_plan(ss, "refresh_catalog")
        self.assertTrue(p["feasible"])

    def test_no_authority_required(self):
        p = build_recovery_plan(_READY, "refresh_catalog", authority_enabled=False)
        self.assertFalse(p["blocked_by_authority"])
        self.assertTrue(p["feasible"])

    def test_no_interrupt(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertFalse(p["would_interrupt_requests"])

    def test_no_confirmation_required(self):
        p = build_recovery_plan(_READY, "refresh_catalog")
        self.assertFalse(p["requires_confirmation"])

    def test_not_blocked_by_active_requests(self):
        p = build_recovery_plan(_READY, "refresh_catalog", active_requests=5)
        self.assertFalse(p["blocked_by_active_requests"])
        self.assertTrue(p["feasible"])

    def test_not_blocked_by_recovery_in_progress(self):
        p = build_recovery_plan(_READY, "refresh_catalog", recovery_in_progress=True)
        self.assertFalse(p["blocked_by_in_progress"])
        self.assertTrue(p["feasible"])


# ---------------------------------------------------------------------------
# 3. refresh_catalog — blocked states
# ---------------------------------------------------------------------------

class RefreshCatalogBlockedTests(unittest.TestCase):
    def test_blocked_while_proxy_initializing(self):
        p = build_recovery_plan(_INITIALIZING, "refresh_catalog")
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_blocked_while_proxy_failed(self):
        p = build_recovery_plan(_PROXY_FAILED, "refresh_catalog")
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_blocked_while_catalog_loading(self):
        ss = _ss(catalog_state="loading")
        p = build_recovery_plan(ss, "refresh_catalog")
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])


# ---------------------------------------------------------------------------
# 4. select_model — blocked when model empty
# ---------------------------------------------------------------------------

class SelectModelMissingModelTests(unittest.TestCase):
    def test_blocked_by_missing_model_when_empty(self):
        p = build_recovery_plan(_READY, "select_model", model="")
        self.assertTrue(p["blocked_by_missing_model"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_missing_model_default(self):
        p = build_recovery_plan(_READY, "select_model")
        self.assertTrue(p["blocked_by_missing_model"])
        self.assertFalse(p["feasible"])

    def test_notes_mention_model(self):
        p = build_recovery_plan(_READY, "select_model", model="")
        self.assertTrue(any("model" in n.lower() for n in p["notes"]))


# ---------------------------------------------------------------------------
# 5. select_model — feasible with model when proxy+catalog ready
# ---------------------------------------------------------------------------

class SelectModelFeasibleTests(unittest.TestCase):
    def test_feasible_with_model(self):
        p = build_recovery_plan(_READY, "select_model", model="qwen3-6b")
        self.assertTrue(p["feasible"])
        self.assertFalse(p["blocked_by_missing_model"])

    def test_not_blocked_by_authority(self):
        p = build_recovery_plan(_READY, "select_model", model="qwen3-6b",
                                authority_enabled=False)
        self.assertFalse(p["blocked_by_authority"])

    def test_no_interrupt(self):
        p = build_recovery_plan(_READY, "select_model", model="qwen3-6b")
        self.assertFalse(p["would_interrupt_requests"])

    def test_blocked_when_catalog_not_ready(self):
        ss = _ss(catalog_state="loading")
        p = build_recovery_plan(ss, "select_model", model="qwen3-6b")
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_blocked_when_proxy_not_ready(self):
        ss = _ss(proxy_state="initializing")
        p = build_recovery_plan(ss, "select_model", model="qwen3-6b")
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_notes_mention_load_side_effect(self):
        p = build_recovery_plan(_READY, "select_model", model="qwen3-6b")
        combined = " ".join(p["notes"]).lower()
        self.assertIn("load", combined)


# ---------------------------------------------------------------------------
# 6. start_backend
# ---------------------------------------------------------------------------

class StartBackendTests(unittest.TestCase):
    def _feasible_ss(self):
        return _BACKEND_DOWN

    def test_feasible_when_unreachable_with_authority(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["feasible"])

    def test_blocked_by_authority_when_disabled(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=False, local_request=True)
        self.assertTrue(p["blocked_by_authority"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_locality_when_remote(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=False)
        self.assertTrue(p["blocked_by_locality"])
        self.assertFalse(p["feasible"])

    def test_blocked_when_backend_already_healthy(self):
        p = build_recovery_plan(_READY, "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_blocked_when_backend_unhealthy_suggests_restart(self):
        p = build_recovery_plan(_BACKEND_UNHEALTHY, "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])
        combined = " ".join(p["notes"]).lower()
        self.assertIn("restart", combined)

    def test_requires_authority(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["requires_authority"])

    def test_requires_confirmation(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["requires_confirmation"])

    def test_no_interrupt(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=True)
        self.assertFalse(p["would_interrupt_requests"])

    def test_blocked_by_backoff(self):
        p = build_recovery_plan(self._feasible_ss(), "start_backend",
                                authority_enabled=True, local_request=True,
                                backoff_active=True)
        self.assertTrue(p["blocked_by_backoff"])
        self.assertFalse(p["feasible"])


# ---------------------------------------------------------------------------
# 7. restart_backend
# ---------------------------------------------------------------------------

class RestartBackendTests(unittest.TestCase):
    def _feasible_ss(self):
        return _BACKEND_UNHEALTHY

    def test_feasible_when_unhealthy_with_authority(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True,
                                active_requests=0)
        self.assertTrue(p["feasible"])

    def test_feasible_when_model_mismatch(self):
        p = build_recovery_plan(_MODEL_MISMATCH, "restart_backend",
                                authority_enabled=True, local_request=True,
                                active_requests=0)
        self.assertTrue(p["feasible"])

    def test_blocked_by_active_requests_no_force(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True,
                                active_requests=2, force=False)
        self.assertTrue(p["blocked_by_active_requests"])
        self.assertFalse(p["feasible"])

    def test_force_overrides_active_requests(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True,
                                active_requests=2, force=True)
        self.assertFalse(p["blocked_by_active_requests"])
        self.assertTrue(p["feasible"])
        self.assertTrue(p["requires_force"])

    def test_active_requests_none_adds_note(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True,
                                active_requests=None)
        combined = " ".join(p["notes"]).lower()
        self.assertIn("unavailable", combined)

    def test_blocked_by_authority_when_disabled(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=False, local_request=True)
        self.assertTrue(p["blocked_by_authority"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_locality_when_remote(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=False)
        self.assertTrue(p["blocked_by_locality"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_backoff(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True,
                                backoff_active=True)
        self.assertTrue(p["blocked_by_backoff"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_state_when_all_healthy(self):
        p = build_recovery_plan(_READY, "restart_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_force_overrides_state(self):
        p = build_recovery_plan(_READY, "restart_backend",
                                authority_enabled=True, local_request=True,
                                force=True, active_requests=0)
        self.assertFalse(p["blocked_by_state"])
        self.assertTrue(p["feasible"])

    def test_would_interrupt_requests(self):
        p = build_recovery_plan(self._feasible_ss(), "restart_backend",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["would_interrupt_requests"])


# ---------------------------------------------------------------------------
# 8. restart_backend — blocked by authority
# ---------------------------------------------------------------------------

class RestartBackendAuthorityTests(unittest.TestCase):
    def test_blocked_authority_with_notes(self):
        p = build_recovery_plan(_BACKEND_UNHEALTHY, "restart_backend",
                                authority_enabled=False, local_request=True)
        self.assertTrue(p["blocked_by_authority"])
        combined = " ".join(p["notes"]).lower()
        self.assertIn("qz_recovery_actions", combined)


# ---------------------------------------------------------------------------
# 9. restart_backend — blocked by locality
# ---------------------------------------------------------------------------

class RestartBackendLocalityTests(unittest.TestCase):
    def test_blocked_locality_with_notes(self):
        p = build_recovery_plan(_BACKEND_UNHEALTHY, "restart_backend",
                                authority_enabled=True, local_request=False)
        self.assertTrue(p["blocked_by_locality"])
        combined = " ".join(p["notes"]).lower()
        self.assertIn("local", combined)


# ---------------------------------------------------------------------------
# 10. restart_backend — blocked by backoff
# ---------------------------------------------------------------------------

class RestartBackendBackoffTests(unittest.TestCase):
    def test_blocked_by_backoff_with_notes(self):
        p = build_recovery_plan(_BACKEND_UNHEALTHY, "restart_backend",
                                authority_enabled=True, local_request=True,
                                backoff_active=True)
        self.assertTrue(p["blocked_by_backoff"])
        combined = " ".join(p["notes"]).lower()
        self.assertIn("backoff", combined)


# ---------------------------------------------------------------------------
# 11. reload_selected_model — blocked when backend unreachable
# ---------------------------------------------------------------------------

class ReloadSelectedModelTests(unittest.TestCase):
    def test_blocked_when_backend_unreachable(self):
        p = build_recovery_plan(_BACKEND_DOWN, "reload_selected_model",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_feasible_when_backend_healthy(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True,
                                active_requests=0)
        self.assertTrue(p["feasible"])

    def test_would_interrupt(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["would_interrupt_requests"])

    def test_requires_authority(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["requires_authority"])

    def test_blocked_by_in_progress(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True,
                                recovery_in_progress=True)
        self.assertTrue(p["blocked_by_in_progress"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_backoff(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True,
                                backoff_active=True, active_requests=0)
        self.assertTrue(p["blocked_by_backoff"])
        self.assertFalse(p["feasible"])

    def test_blocked_by_active_requests_no_force(self):
        p = build_recovery_plan(_READY, "reload_selected_model",
                                authority_enabled=True, local_request=True,
                                active_requests=3, force=False)
        self.assertTrue(p["blocked_by_active_requests"])
        self.assertFalse(p["feasible"])


# ---------------------------------------------------------------------------
# 12. clear_failure — feasible when model_state failed
# ---------------------------------------------------------------------------

class ClearFailureFeasibleTests(unittest.TestCase):
    def test_feasible_when_model_failed(self):
        p = build_recovery_plan(_MODEL_FAILED, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["feasible"])

    def test_feasible_when_recovery_failed(self):
        ss = _ss(recovery_state="failed")
        p = build_recovery_plan(ss, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["feasible"])

    def test_feasible_when_backend_failed(self):
        ss = _ss(backend_state="failed")
        p = build_recovery_plan(ss, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["feasible"])

    def test_feasible_when_manual_required(self):
        ss = _ss(recovery_state="manual_required")
        p = build_recovery_plan(ss, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["feasible"])

    def test_no_interrupt(self):
        p = build_recovery_plan(_MODEL_FAILED, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertFalse(p["would_interrupt_requests"])

    def test_no_confirmation_required(self):
        p = build_recovery_plan(_MODEL_FAILED, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertFalse(p["requires_confirmation"])


# ---------------------------------------------------------------------------
# 13. clear_failure — blocked when no failure
# ---------------------------------------------------------------------------

class ClearFailureBlockedTests(unittest.TestCase):
    def test_blocked_when_all_ready(self):
        p = build_recovery_plan(_READY, "clear_failure",
                                authority_enabled=True, local_request=True)
        self.assertTrue(p["blocked_by_state"])
        self.assertFalse(p["feasible"])

    def test_notes_mention_no_failure(self):
        p = build_recovery_plan(_READY, "clear_failure",
                                authority_enabled=True, local_request=True)
        combined = " ".join(p["notes"]).lower()
        self.assertIn("failure state", combined)


# ---------------------------------------------------------------------------
# 14. recovery_in_progress — blocks state-changing actions
# ---------------------------------------------------------------------------

class RecoveryInProgressTests(unittest.TestCase):
    BLOCKED_ACTIONS = (
        "reload_selected_model", "start_backend", "restart_backend", "clear_failure"
    )
    UNBLOCKED_ACTIONS = ("refresh_catalog",)

    def test_in_progress_blocks_state_changing(self):
        ss = _ss(backend_state="unreachable", recovery_state="in_progress")
        for action in self.BLOCKED_ACTIONS:
            with self.subTest(action=action):
                p = build_recovery_plan(ss, action,
                                        model="m", authority_enabled=True,
                                        local_request=True, recovery_in_progress=True)
                self.assertTrue(p["blocked_by_in_progress"], f"{action} should be blocked")
                self.assertFalse(p["feasible"])

    def test_in_progress_does_not_block_refresh_catalog(self):
        p = build_recovery_plan(_READY, "refresh_catalog", recovery_in_progress=True)
        self.assertFalse(p["blocked_by_in_progress"])

    def test_in_progress_does_not_block_select_model(self):
        p = build_recovery_plan(_READY, "select_model", model="qwen3-6b",
                                recovery_in_progress=True)
        self.assertFalse(p["blocked_by_in_progress"])


# ---------------------------------------------------------------------------
# 15. Unknown action
# ---------------------------------------------------------------------------

class UnknownActionTests(unittest.TestCase):
    def test_unknown_action_not_feasible(self):
        p = build_recovery_plan(_READY, "nuke_everything")
        self.assertFalse(p["feasible"])

    def test_unknown_action_blocked_by_state(self):
        p = build_recovery_plan(_READY, "nuke_everything")
        self.assertTrue(p["blocked_by_state"])

    def test_unknown_action_no_other_blocks(self):
        p = build_recovery_plan(_READY, "nuke_everything")
        self.assertFalse(p["blocked_by_authority"])
        self.assertFalse(p["blocked_by_locality"])
        self.assertFalse(p["blocked_by_in_progress"])

    def test_unknown_action_note_mentions_allowed(self):
        p = build_recovery_plan(_READY, "nuke_everything")
        combined = " ".join(p["notes"]).lower()
        self.assertIn("unknown", combined)
        self.assertIn("allowed", combined)

    def test_unknown_action_json_serialisable(self):
        p = build_recovery_plan(_READY, "")
        json.dumps(p)  # must not raise

    def test_empty_string_action(self):
        p = build_recovery_plan(_READY, "")
        self.assertFalse(p["feasible"])
        self.assertTrue(p["blocked_by_state"])


# ---------------------------------------------------------------------------
# 16. allowed_recovery_actions constant
# ---------------------------------------------------------------------------

class AllowedActionsTests(unittest.TestCase):
    def test_all_expected_actions_present(self):
        expected = {
            "refresh_catalog", "select_model", "reload_selected_model",
            "start_backend", "restart_backend", "clear_failure",
        }
        self.assertEqual(ALLOWED_RECOVERY_ACTIONS, expected)

    def test_is_frozenset(self):
        self.assertIsInstance(ALLOWED_RECOVERY_ACTIONS, frozenset)


# ---------------------------------------------------------------------------
# 17. _recovery_error_payload helper (from RequestRouter)
# ---------------------------------------------------------------------------

class RecoveryErrorPayloadTests(unittest.TestCase):
    def _make(self, **kwargs):
        from proxy.qz_request_router import RequestRouter
        return RequestRouter._recovery_error_payload(**kwargs)

    def test_schema(self):
        p = self._make(error="invalid_json", message="bad body")
        self.assertEqual(p["schema"], "qz.recovery.error.v1")

    def test_ok_false(self):
        p = self._make(error="missing_action", message="no action")
        self.assertFalse(p["ok"])

    def test_error_field(self):
        p = self._make(error="unknown_action", message="unknown", action="kaboom",
                       blocked_by="unknown_action")
        self.assertEqual(p["error"], "unknown_action")
        self.assertEqual(p["action"], "kaboom")
        self.assertEqual(p["blocked_by"], "unknown_action")

    def test_defaults(self):
        p = self._make(error="err", message="msg")
        self.assertEqual(p["action"], "")
        self.assertEqual(p["blocked_by"], "bad_request")
        self.assertIsNone(p["retry_after_secs"])
        self.assertIsNone(p["recovery_status"])

    def test_json_serialisable(self):
        p = self._make(error="invalid_json", message="oops")
        json.dumps(p)  # must not raise


# ---------------------------------------------------------------------------
# 18. _is_local_request helper (from RequestRouter)
# ---------------------------------------------------------------------------

class IsLocalRequestTests(unittest.TestCase):
    def _is_local(self, addr):
        from proxy.qz_request_router import RequestRouter

        class _FakeHandler:
            client_address = addr

        return RequestRouter._is_local_request(_FakeHandler())

    def test_loopback_ipv4(self):
        self.assertTrue(self._is_local(("127.0.0.1", 54321)))

    def test_loopback_ipv6(self):
        self.assertTrue(self._is_local(("::1", 54321)))

    def test_localhost_string(self):
        self.assertTrue(self._is_local(("localhost", 54321)))

    def test_remote_ip_false(self):
        self.assertFalse(self._is_local(("10.0.0.5", 54321)))

    def test_missing_client_address(self):
        from proxy.qz_request_router import RequestRouter

        class _NoAddr:
            pass

        self.assertFalse(RequestRouter._is_local_request(_NoAddr()))


if __name__ == "__main__":
    unittest.main()
