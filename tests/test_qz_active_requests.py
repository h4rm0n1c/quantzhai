"""Tests for in-memory active request tracker (proxy/qz_active_requests.py)."""
import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_active_requests import ACTIVE_REQUESTS_SCHEMA, ActiveRequestTracker


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------

class InitialStateTests(unittest.TestCase):
    def setUp(self):
        self.ar = ActiveRequestTracker()

    def test_count_zero(self):
        self.assertEqual(self.ar.count(), 0)

    def test_snapshot_schema(self):
        s = self.ar.snapshot()
        self.assertEqual(s["schema"], ACTIVE_REQUESTS_SCHEMA)
        self.assertEqual(s["schema"], "qz.active_requests.v1")

    def test_snapshot_count_zero(self):
        s = self.ar.snapshot()
        self.assertEqual(s["count"], 0)

    def test_snapshot_requests_empty(self):
        s = self.ar.snapshot()
        self.assertEqual(s["requests"], [])

    def test_json_serialisable_empty(self):
        json.dumps(self.ar.snapshot())


# ---------------------------------------------------------------------------
# 2. begin increments count
# ---------------------------------------------------------------------------

class BeginTests(unittest.TestCase):
    def setUp(self):
        self.ar = ActiveRequestTracker()

    def test_begin_increments_count(self):
        self.ar.begin("req-001")
        self.assertEqual(self.ar.count(), 1)

    def test_begin_twice_different_ids(self):
        self.ar.begin("req-001")
        self.ar.begin("req-002")
        self.assertEqual(self.ar.count(), 2)

    def test_begin_same_id_overwrites(self):
        self.ar.begin("req-001", route="/v1/responses", model="m1")
        self.ar.begin("req-001", route="/v1/responses", model="m2")
        self.assertEqual(self.ar.count(), 1)

    def test_begin_empty_id_ignored(self):
        self.ar.begin("")
        self.assertEqual(self.ar.count(), 0)

    def test_snapshot_has_request(self):
        t0 = 1_000_000.0
        self.ar.begin("req-abc", route="/v1/responses", model="qwen3", started_at=t0)
        s = self.ar.snapshot(now=t0 + 2.5)
        self.assertEqual(len(s["requests"]), 1)
        r = s["requests"][0]
        self.assertEqual(r["request_id"], "req-abc")
        self.assertEqual(r["route"], "/v1/responses")
        self.assertEqual(r["model"], "qwen3")
        self.assertAlmostEqual(r["age_secs"], 2.5, delta=0.01)


# ---------------------------------------------------------------------------
# 3. finish decrements count
# ---------------------------------------------------------------------------

class FinishTests(unittest.TestCase):
    def setUp(self):
        self.ar = ActiveRequestTracker()

    def test_finish_decrements_count(self):
        self.ar.begin("req-001")
        self.ar.finish("req-001")
        self.assertEqual(self.ar.count(), 0)

    def test_finish_removes_from_snapshot(self):
        self.ar.begin("req-001")
        self.ar.finish("req-001")
        s = self.ar.snapshot()
        self.assertEqual(s["requests"], [])

    def test_finish_one_of_two(self):
        self.ar.begin("req-001")
        self.ar.begin("req-002")
        self.ar.finish("req-001")
        self.assertEqual(self.ar.count(), 1)
        s = self.ar.snapshot()
        ids = [r["request_id"] for r in s["requests"]]
        self.assertIn("req-002", ids)
        self.assertNotIn("req-001", ids)

    def test_finish_empty_id_safe(self):
        self.ar.finish("")  # must not raise


# ---------------------------------------------------------------------------
# 4. finish unknown request is safe
# ---------------------------------------------------------------------------

class FinishUnknownTests(unittest.TestCase):
    def test_finish_unknown_safe(self):
        ar = ActiveRequestTracker()
        ar.finish("does-not-exist")  # must not raise
        self.assertEqual(ar.count(), 0)

    def test_double_finish_safe(self):
        ar = ActiveRequestTracker()
        ar.begin("req-x")
        ar.finish("req-x")
        ar.finish("req-x")  # second finish must not raise
        self.assertEqual(ar.count(), 0)


# ---------------------------------------------------------------------------
# 5. age_secs computed correctly
# ---------------------------------------------------------------------------

class AgeSecs(unittest.TestCase):
    def test_age_secs_positive(self):
        ar = ActiveRequestTracker()
        t0 = 1_000_000.0
        ar.begin("req-age", started_at=t0)
        s = ar.snapshot(now=t0 + 7.5)
        r = s["requests"][0]
        self.assertAlmostEqual(r["age_secs"], 7.5, delta=0.01)

    def test_age_secs_none_if_no_started_at(self):
        ar = ActiveRequestTracker()
        # Manually inject a bad record — simulate edge case
        with ar._lock:
            ar._requests["bad"] = {"request_id": "bad", "started_at": None}
        s = ar.snapshot()
        r = next((x for x in s["requests"] if x["request_id"] == "bad"), None)
        self.assertIsNone(r["age_secs"])


# ---------------------------------------------------------------------------
# 6. JSON serialisable
# ---------------------------------------------------------------------------

class JsonSerialisableTests(unittest.TestCase):
    def test_snapshot_serialisable_with_requests(self):
        ar = ActiveRequestTracker()
        ar.begin("req-j1", route="/v1/responses", model="m1")
        ar.begin("req-j2", route="/v1/responses", model="m2")
        json.dumps(ar.snapshot())  # must not raise

    def test_snapshot_serialisable_after_finish(self):
        ar = ActiveRequestTracker()
        ar.begin("req-j3")
        ar.finish("req-j3")
        json.dumps(ar.snapshot())

    def test_schema_constant(self):
        self.assertEqual(ACTIVE_REQUESTS_SCHEMA, "qz.active_requests.v1")


# ---------------------------------------------------------------------------
# 7. Thread safety smoke
# ---------------------------------------------------------------------------

class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_begin_finish(self):
        ar = ActiveRequestTracker()
        errors = []

        def worker(i):
            try:
                req_id = f"req-{i:04d}"
                ar.begin(req_id, model=f"m{i}")
                time.sleep(0.001)
                ar.finish(req_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(ar.count(), 0)


# ---------------------------------------------------------------------------
# 8. Integration with recovery status
# ---------------------------------------------------------------------------

class IntegrationWithRecoveryStatusTests(unittest.TestCase):
    def _ss(self):
        return {
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

    def test_active_requests_omitted_when_none(self):
        from proxy.qz_recovery_status import build_recovery_status
        p = build_recovery_status(self._ss())
        self.assertNotIn("active_requests", p)

    def test_active_requests_included_when_supplied(self):
        from proxy.qz_recovery_status import build_recovery_status
        ar = ActiveRequestTracker()
        snap = ar.snapshot()
        p = build_recovery_status(self._ss(), active_requests=snap)
        self.assertIn("active_requests", p)
        self.assertEqual(p["active_requests"]["schema"], ACTIVE_REQUESTS_SCHEMA)

    def test_active_requests_count_in_status(self):
        from proxy.qz_recovery_status import build_recovery_status
        ar = ActiveRequestTracker()
        ar.begin("req-x")
        snap = ar.snapshot()
        p = build_recovery_status(self._ss(), active_requests=snap)
        self.assertEqual(p["active_requests"]["count"], 1)

    def test_json_serialisable_full_status(self):
        from proxy.qz_recovery_status import build_recovery_status
        ar = ActiveRequestTracker()
        ar.begin("req-ser", route="/v1/responses", model="qwen3")
        p = build_recovery_status(self._ss(), active_requests=ar.snapshot())
        json.dumps(p)  # must not raise


# ---------------------------------------------------------------------------
# 9. Recovery plan uses count
# ---------------------------------------------------------------------------

class RecoveryPlanWithCountTests(unittest.TestCase):
    def _ss_unhealthy(self):
        return {
            "schema": "qz.service.status.v1",
            "proxy_state": "ready",
            "catalog_state": "ready",
            "backend_state": "unhealthy",
            "model_state": "unknown",
            "request_admission": "rejected_backend_not_ready",
            "recovery_state": "available",
            "recoverable": True,
            "retryable": False,
            "fatal": False,
            "last_error": "",
            "operator_action": "restart_backend",
            "operator_hints": [],
        }

    def test_restart_blocked_when_active_requests_positive(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss_unhealthy(),
            "restart_backend",
            authority_enabled=True,
            local_request=True,
            active_requests=2,
            force=False,
        )
        self.assertTrue(p["blocked_by_active_requests"])
        self.assertFalse(p["feasible"])

    def test_restart_not_blocked_when_count_zero(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss_unhealthy(),
            "restart_backend",
            authority_enabled=True,
            local_request=True,
            active_requests=0,
            force=False,
        )
        self.assertFalse(p["blocked_by_active_requests"])

    def test_note_no_longer_says_unavailable_when_count_given(self):
        from proxy.qz_recovery_plan import build_recovery_plan
        p = build_recovery_plan(
            self._ss_unhealthy(),
            "restart_backend",
            authority_enabled=True,
            local_request=True,
            active_requests=0,
        )
        combined = " ".join(p["notes"]).lower()
        self.assertNotIn("unavailable", combined)


if __name__ == "__main__":
    unittest.main()
