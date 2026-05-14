"""Tests for in-memory recovery state tracker (proxy/qz_recovery_state.py)."""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_recovery_state import (
    DEFAULT_BACKOFF_SECS,
    DEFAULT_MAX_ATTEMPTS,
    RECOVERY_STATE_SCHEMA,
    RecoveryRuntimeState,
    parse_backoff_schedule,
)


# ---------------------------------------------------------------------------
# 1. Initial snapshot
# ---------------------------------------------------------------------------

class InitialSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()

    def test_schema(self):
        s = self.rs.snapshot()
        self.assertEqual(s["schema"], RECOVERY_STATE_SCHEMA)
        self.assertEqual(s["schema"], "qz.recovery.runtime_state.v1")

    def test_not_in_progress(self):
        s = self.rs.snapshot()
        self.assertFalse(s["in_progress"])

    def test_empty_current(self):
        s = self.rs.snapshot()
        self.assertEqual(s["current_action"], "")
        self.assertEqual(s["current_request_id"], "")

    def test_empty_history(self):
        s = self.rs.snapshot()
        self.assertEqual(s["last_recovery_action"], "")
        self.assertIsNone(s["last_recovery_started_at"])
        self.assertIsNone(s["last_recovery_finished_at"])
        self.assertEqual(s["last_recovery_error"], "")

    def test_empty_attempts(self):
        s = self.rs.snapshot()
        self.assertEqual(s["attempts"], {})

    def test_json_serialisable(self):
        s = self.rs.snapshot()
        json.dumps(s)  # must not raise


# ---------------------------------------------------------------------------
# 2. mark_started
# ---------------------------------------------------------------------------

class MarkStartedTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        self.t0 = time.time()
        self.rs.mark_started("restart_backend", request_id="req-001", now=self.t0)

    def test_in_progress_true(self):
        self.assertTrue(self.rs.is_recovery_in_progress())

    def test_current_action(self):
        s = self.rs.snapshot()
        self.assertEqual(s["current_action"], "restart_backend")

    def test_current_request_id(self):
        s = self.rs.snapshot()
        self.assertEqual(s["current_request_id"], "req-001")

    def test_last_action_set(self):
        s = self.rs.snapshot()
        self.assertEqual(s["last_recovery_action"], "restart_backend")

    def test_started_at_set(self):
        s = self.rs.snapshot()
        self.assertAlmostEqual(s["last_recovery_started_at"], self.t0, places=3)

    def test_finished_at_none(self):
        s = self.rs.snapshot()
        self.assertIsNone(s["last_recovery_finished_at"])


# ---------------------------------------------------------------------------
# 3. mark_completed
# ---------------------------------------------------------------------------

class MarkCompletedTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        t0 = time.time()
        self.rs.mark_started("refresh_catalog", now=t0)
        self.t1 = t0 + 1.0
        self.rs.mark_completed("refresh_catalog", now=self.t1)

    def test_not_in_progress(self):
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_current_action_cleared(self):
        s = self.rs.snapshot()
        self.assertEqual(s["current_action"], "")

    def test_finished_at_set(self):
        s = self.rs.snapshot()
        self.assertAlmostEqual(s["last_recovery_finished_at"], self.t1, places=3)

    def test_no_error(self):
        s = self.rs.snapshot()
        self.assertEqual(s["last_recovery_error"], "")

    def test_attempt_count_reset(self):
        s = self.rs.snapshot()
        att = s["attempts"].get("refresh_catalog", {})
        self.assertEqual(att.get("count", 0), 0)

    def test_backoff_not_active_after_success(self):
        self.assertFalse(self.rs.is_backoff_active("refresh_catalog"))


# ---------------------------------------------------------------------------
# 4. First mark_failed — backoff 30 s
# ---------------------------------------------------------------------------

class FirstFailureTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        self.t0 = 1_000_000.0
        self.rs.mark_failed("restart_backend", "Connection refused", now=self.t0)

    def test_not_in_progress(self):
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_count_one(self):
        s = self.rs.snapshot(now=self.t0)
        self.assertEqual(s["attempts"]["restart_backend"]["count"], 1)

    def test_backoff_active_immediately(self):
        self.assertTrue(self.rs.is_backoff_active("restart_backend", now=self.t0 + 1))

    def test_backoff_until_approx_30s(self):
        s = self.rs.snapshot(now=self.t0)
        bu = s["attempts"]["restart_backend"]["backoff_until"]
        self.assertIsNotNone(bu)
        self.assertAlmostEqual(bu - self.t0, DEFAULT_BACKOFF_SECS[0], delta=1)

    def test_backoff_expires(self):
        self.assertFalse(
            self.rs.is_backoff_active("restart_backend", now=self.t0 + DEFAULT_BACKOFF_SECS[0] + 1)
        )

    def test_manual_required_false(self):
        s = self.rs.snapshot(now=self.t0)
        self.assertFalse(s["attempts"]["restart_backend"]["manual_required"])

    def test_last_error_stored(self):
        s = self.rs.snapshot(now=self.t0)
        self.assertIn("refused", s["attempts"]["restart_backend"]["last_error"].lower())


# ---------------------------------------------------------------------------
# 5. Second failure — backoff 120 s
# ---------------------------------------------------------------------------

class SecondFailureTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        self.t0 = 1_000_000.0
        self.rs.mark_failed("restart_backend", "err1", now=self.t0)
        self.t1 = self.t0 + DEFAULT_BACKOFF_SECS[0] + 1
        self.rs.mark_failed("restart_backend", "err2", now=self.t1)

    def test_count_two(self):
        s = self.rs.snapshot(now=self.t1)
        self.assertEqual(s["attempts"]["restart_backend"]["count"], 2)

    def test_backoff_approx_120s(self):
        s = self.rs.snapshot(now=self.t1)
        bu = s["attempts"]["restart_backend"]["backoff_until"]
        self.assertAlmostEqual(bu - self.t1, DEFAULT_BACKOFF_SECS[1], delta=1)


# ---------------------------------------------------------------------------
# 6. Third failure — backoff 300 s
# ---------------------------------------------------------------------------

class ThirdFailureTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        t = 1_000_000.0
        for err in ("e1", "e2", "e3"):
            self.rs.mark_failed("restart_backend", err, now=t)
            t += 1
        self.snap_time = t
        self.snap = self.rs.snapshot(now=self.snap_time)

    def test_count_three(self):
        self.assertEqual(self.snap["attempts"]["restart_backend"]["count"], 3)

    def test_backoff_approx_300s(self):
        bu = self.snap["attempts"]["restart_backend"]["backoff_until"]
        # backoff_until set at time of 3rd failure (~2s after t0); check ~300s gap
        self.assertIsNotNone(bu)
        self.assertTrue(bu > 0)

    def test_manual_required_false_at_3(self):
        self.assertFalse(self.snap["attempts"]["restart_backend"]["manual_required"])


# ---------------------------------------------------------------------------
# 7. Fourth failure — manual_required
# ---------------------------------------------------------------------------

class FourthFailureTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        t = 1_000_000.0
        for err in ("e1", "e2", "e3", "e4"):
            self.rs.mark_failed("restart_backend", err, now=t)
            t += 1
        self.snap = self.rs.snapshot(now=t)

    def test_manual_required_true(self):
        self.assertTrue(self.snap["attempts"]["restart_backend"]["manual_required"])

    def test_backoff_active_true(self):
        self.assertTrue(self.snap["attempts"]["restart_backend"]["backoff_active"])

    def test_is_backoff_active_true(self):
        self.assertTrue(self.rs.is_backoff_active("restart_backend", now=1_100_000.0))

    def test_backoff_until_none_when_manual(self):
        # No time-based backoff_until when manual_required
        self.assertIsNone(self.snap["attempts"]["restart_backend"]["backoff_until"])

    def test_count_four(self):
        self.assertEqual(self.snap["attempts"]["restart_backend"]["count"], 4)


# ---------------------------------------------------------------------------
# 8. clear(action)
# ---------------------------------------------------------------------------

class ClearActionTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        t = 1_000_000.0
        self.rs.mark_failed("restart_backend", "err", now=t)
        self.rs.mark_failed("refresh_catalog", "err2", now=t)
        self.rs.clear("restart_backend")

    def test_cleared_action_gone(self):
        s = self.rs.snapshot()
        self.assertNotIn("restart_backend", s["attempts"])

    def test_other_action_intact(self):
        s = self.rs.snapshot()
        self.assertIn("refresh_catalog", s["attempts"])

    def test_is_backoff_inactive_after_clear(self):
        self.assertFalse(self.rs.is_backoff_active("restart_backend"))


# ---------------------------------------------------------------------------
# 9. clear() — clears all
# ---------------------------------------------------------------------------

class ClearAllTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()
        t = 1_000_000.0
        self.rs.mark_started("restart_backend", now=t)
        self.rs.mark_failed("restart_backend", "boom", now=t + 1)
        self.rs.mark_failed("refresh_catalog", "nope", now=t + 1)
        self.rs.clear()

    def test_attempts_empty(self):
        s = self.rs.snapshot()
        self.assertEqual(s["attempts"], {})

    def test_not_in_progress(self):
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_no_backoff(self):
        self.assertFalse(self.rs.is_backoff_active("restart_backend"))

    def test_snapshot_serialisable(self):
        json.dumps(self.rs.snapshot())


# ---------------------------------------------------------------------------
# 10. parse_backoff_schedule
# ---------------------------------------------------------------------------

class ParseBackoffScheduleTests(unittest.TestCase):
    def test_valid_three(self):
        self.assertEqual(parse_backoff_schedule("30,120,300"), [30, 120, 300])

    def test_valid_single(self):
        self.assertEqual(parse_backoff_schedule("60"), [60])

    def test_with_spaces(self):
        self.assertEqual(parse_backoff_schedule(" 10 , 20 , 30 "), [10, 20, 30])

    def test_empty_string(self):
        self.assertEqual(parse_backoff_schedule(""), DEFAULT_BACKOFF_SECS)

    def test_bad_string(self):
        self.assertEqual(parse_backoff_schedule("bad"), DEFAULT_BACKOFF_SECS)

    def test_zero_value(self):
        self.assertEqual(parse_backoff_schedule("0,120,300"), DEFAULT_BACKOFF_SECS)

    def test_negative_value(self):
        self.assertEqual(parse_backoff_schedule("30,-1,300"), DEFAULT_BACKOFF_SECS)

    def test_partial_bad(self):
        self.assertEqual(parse_backoff_schedule("30,abc,300"), DEFAULT_BACKOFF_SECS)

    def test_returns_list(self):
        self.assertIsInstance(parse_backoff_schedule("10,20"), list)

    def test_single_large(self):
        self.assertEqual(parse_backoff_schedule("3600"), [3600])


# ---------------------------------------------------------------------------
# 11. is_backoff_active respects time
# ---------------------------------------------------------------------------

class BackoffTimeTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState(backoff_schedule=[100])
        self.t0 = 1_000_000.0
        self.rs.mark_failed("start_backend", "err", now=self.t0)

    def test_active_during_window(self):
        self.assertTrue(self.rs.is_backoff_active("start_backend", now=self.t0 + 50))

    def test_active_at_boundary(self):
        # t0 + 100 is the exact expiry; not yet expired
        self.assertTrue(self.rs.is_backoff_active("start_backend", now=self.t0 + 99.9))

    def test_inactive_after_expiry(self):
        self.assertFalse(self.rs.is_backoff_active("start_backend", now=self.t0 + 101))

    def test_inactive_for_other_action(self):
        self.assertFalse(self.rs.is_backoff_active("refresh_catalog", now=self.t0 + 1))


# ---------------------------------------------------------------------------
# 12. is_recovery_in_progress
# ---------------------------------------------------------------------------

class InProgressTests(unittest.TestCase):
    def setUp(self):
        self.rs = RecoveryRuntimeState()

    def test_initially_false(self):
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_true_after_started(self):
        self.rs.mark_started("clear_failure")
        self.assertTrue(self.rs.is_recovery_in_progress())

    def test_false_after_completed(self):
        self.rs.mark_started("clear_failure")
        self.rs.mark_completed("clear_failure")
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_false_after_failed(self):
        self.rs.mark_started("restart_backend")
        self.rs.mark_failed("restart_backend", "boom")
        self.assertFalse(self.rs.is_recovery_in_progress())

    def test_false_after_clear(self):
        self.rs.mark_started("restart_backend")
        self.rs.clear()
        self.assertFalse(self.rs.is_recovery_in_progress())


# ---------------------------------------------------------------------------
# 13. build_recovery_status with runtime_state (integration)
# ---------------------------------------------------------------------------

class BuildRecoveryStatusWithRuntimeTests(unittest.TestCase):
    def _ss(self, **kwargs):
        base = {
            "schema": "qz.service.status.v1",
            "proxy_state": "ready",
            "catalog_state": "ready",
            "backend_state": "healthy",
            "model_state": "loaded",
            "request_admission": "accepted",
            "recovery_state": "none",
            "recoverable": False,
            "retryable": False,
            "fatal": False,
            "last_error": "",
            "operator_action": "",
            "operator_hints": [],
        }
        base.update(kwargs)
        return base

    def test_without_runtime_state_no_extra_fields(self):
        from proxy.qz_recovery_status import build_recovery_status
        p = build_recovery_status(self._ss())
        self.assertNotIn("runtime_state", p)
        self.assertNotIn("backoff", p)

    def test_with_runtime_state_fields_present(self):
        from proxy.qz_recovery_status import build_recovery_status
        rs = RecoveryRuntimeState()
        snap = rs.snapshot()
        p = build_recovery_status(self._ss(), runtime_state=snap)
        self.assertIn("runtime_state", p)
        self.assertIn("backoff", p)

    def test_backoff_none_when_no_operator_action(self):
        from proxy.qz_recovery_status import build_recovery_status
        rs = RecoveryRuntimeState()
        p = build_recovery_status(self._ss(operator_action=""), runtime_state=rs.snapshot())
        self.assertIsNone(p["backoff"])

    def test_backoff_active_reflects_state(self):
        from proxy.qz_recovery_status import build_recovery_status
        rs = RecoveryRuntimeState(backoff_schedule=[100])
        t0 = 1_000_000.0
        rs.mark_failed("start_backend", "err", now=t0)
        snap = rs.snapshot(now=t0 + 1)
        ss = self._ss(
            backend_state="unreachable",
            recovery_state="available",
            operator_action="start_backend",
        )
        p = build_recovery_status(ss, runtime_state=snap)
        self.assertTrue(p["backoff"]["active"])
        self.assertEqual(p["backoff"]["action"], "start_backend")

    def test_invalid_service_status_includes_runtime(self):
        from proxy.qz_recovery_status import build_recovery_status
        rs = RecoveryRuntimeState()
        p = build_recovery_status(None, runtime_state=rs.snapshot())  # type: ignore
        self.assertIn("runtime_state", p)
        self.assertIn("backoff", p)

    def test_json_serialisable_with_runtime(self):
        from proxy.qz_recovery_status import build_recovery_status
        rs = RecoveryRuntimeState()
        t = 1_000_000.0
        rs.mark_failed("restart_backend", "err", now=t)
        p = build_recovery_status(
            self._ss(operator_action="restart_backend", recovery_state="available"),
            runtime_state=rs.snapshot(now=t + 1),
        )
        json.dumps(p)  # must not raise


# ---------------------------------------------------------------------------
# 14. Custom backoff schedule and max_attempts
# ---------------------------------------------------------------------------

class CustomScheduleTests(unittest.TestCase):
    def test_custom_single_backoff(self):
        rs = RecoveryRuntimeState(backoff_schedule=[10], max_attempts=1)
        t = 1_000_000.0
        rs.mark_failed("clear_failure", "err", now=t)
        # attempt 1 == max_attempts, so next call is attempt 2 > max_attempts
        rs.mark_failed("clear_failure", "err2", now=t + 11)
        s = rs.snapshot(now=t + 12)
        self.assertTrue(s["attempts"]["clear_failure"]["manual_required"])

    def test_custom_backoff_values(self):
        rs = RecoveryRuntimeState(backoff_schedule=[5, 10, 15])
        t = 1_000_000.0
        rs.mark_failed("reload_selected_model", "err", now=t)
        s = rs.snapshot(now=t)
        bu = s["attempts"]["reload_selected_model"]["backoff_until"]
        self.assertAlmostEqual(bu, t + 5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
