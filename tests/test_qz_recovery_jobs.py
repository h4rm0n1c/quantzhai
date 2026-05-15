"""Tests for in-memory recovery job store (proxy/qz_recovery_jobs.py)."""
import json
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_recovery_jobs import (
    RECOVERY_JOB_SCHEMA,
    RECOVERY_JOBS_SCHEMA,
    RecoveryJobStore,
)


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------

class InitialStateTests(unittest.TestCase):
    def setUp(self):
        self.store = RecoveryJobStore()

    def test_snapshot_schema(self):
        s = self.store.snapshot()
        self.assertEqual(s["schema"], RECOVERY_JOBS_SCHEMA)

    def test_snapshot_empty(self):
        s = self.store.snapshot()
        self.assertEqual(s["count"], 0)
        self.assertEqual(s["jobs"], [])

    def test_get_unknown_returns_none(self):
        self.assertIsNone(self.store.get("nonexistent"))

    def test_json_serialisable_empty(self):
        json.dumps(self.store.snapshot())


# ---------------------------------------------------------------------------
# 2. create
# ---------------------------------------------------------------------------

class CreateTests(unittest.TestCase):
    def setUp(self):
        self.store = RecoveryJobStore()
        self.t0 = 1_000_000.0
        self.job = self.store.create(
            "rec-abc123",
            "reload_selected_model",
            pre_status={"state": "none"},
            operator_warning="Watch out.",
            now=self.t0,
        )

    def test_schema(self):
        self.assertEqual(self.job["schema"], RECOVERY_JOB_SCHEMA)

    def test_request_id(self):
        self.assertEqual(self.job["request_id"], "rec-abc123")

    def test_action(self):
        self.assertEqual(self.job["action"], "reload_selected_model")

    def test_state_queued(self):
        self.assertEqual(self.job["state"], "queued")

    def test_accepted_true(self):
        self.assertTrue(self.job["accepted"])

    def test_async_true(self):
        self.assertTrue(self.job["async"])

    def test_started_at(self):
        self.assertAlmostEqual(self.job["started_at"], self.t0, places=3)

    def test_finished_at_none(self):
        self.assertIsNone(self.job["finished_at"])

    def test_pre_status_set(self):
        self.assertEqual(self.job["pre_status"], {"state": "none"})

    def test_post_status_none(self):
        self.assertIsNone(self.job["post_status"])

    def test_error_empty(self):
        self.assertEqual(self.job["error"], "")

    def test_operator_warning(self):
        self.assertEqual(self.job["operator_warning"], "Watch out.")

    def test_appears_in_snapshot(self):
        s = self.store.snapshot()
        self.assertEqual(s["count"], 1)

    def test_get_returns_same(self):
        got = self.store.get("rec-abc123")
        self.assertIsNotNone(got)
        self.assertEqual(got["request_id"], "rec-abc123")

    def test_json_serialisable(self):
        json.dumps(self.job)


# ---------------------------------------------------------------------------
# 3. mark_running
# ---------------------------------------------------------------------------

class MarkRunningTests(unittest.TestCase):
    def setUp(self):
        self.store = RecoveryJobStore()
        self.store.create("rec-r1", "reload_selected_model", now=1_000_000.0)
        self.store.mark_running("rec-r1")

    def test_state_running(self):
        self.assertEqual(self.store.get("rec-r1")["state"], "running")

    def test_unknown_safe(self):
        self.store.mark_running("does-not-exist")  # must not raise


# ---------------------------------------------------------------------------
# 4. mark_completed
# ---------------------------------------------------------------------------

class MarkCompletedTests(unittest.TestCase):
    def setUp(self):
        self.store = RecoveryJobStore()
        self.t0 = 1_000_000.0
        self.t1 = self.t0 + 5.0
        self.store.create("rec-c1", "reload_selected_model", now=self.t0)
        self.store.mark_running("rec-c1")
        self.post = {"state": "completed_status"}
        self.store.mark_completed("rec-c1", post_status=self.post, now=self.t1)

    def test_state_completed(self):
        self.assertEqual(self.store.get("rec-c1")["state"], "completed")

    def test_finished_at(self):
        self.assertAlmostEqual(self.store.get("rec-c1")["finished_at"], self.t1, places=3)

    def test_post_status(self):
        self.assertEqual(self.store.get("rec-c1")["post_status"], self.post)

    def test_error_cleared(self):
        self.assertEqual(self.store.get("rec-c1")["error"], "")

    def test_telemetry_event(self):
        self.assertEqual(self.store.get("rec-c1")["telemetry_event"], "recovery_action_completed")

    def test_json_serialisable(self):
        json.dumps(self.store.snapshot())


# ---------------------------------------------------------------------------
# 5. mark_failed
# ---------------------------------------------------------------------------

class MarkFailedTests(unittest.TestCase):
    def setUp(self):
        self.store = RecoveryJobStore()
        self.t0 = 1_000_000.0
        self.t1 = self.t0 + 2.0
        self.store.create("rec-f1", "reload_selected_model", now=self.t0)
        self.store.mark_running("rec-f1")
        self.store.mark_failed("rec-f1", error="load failed", now=self.t1)

    def test_state_failed(self):
        self.assertEqual(self.store.get("rec-f1")["state"], "failed")

    def test_finished_at(self):
        self.assertAlmostEqual(self.store.get("rec-f1")["finished_at"], self.t1, places=3)

    def test_error_stored(self):
        self.assertIn("failed", self.store.get("rec-f1")["error"].lower())

    def test_telemetry_event(self):
        self.assertEqual(self.store.get("rec-f1")["telemetry_event"], "recovery_action_failed")

    def test_json_serialisable(self):
        json.dumps(self.store.snapshot())


# ---------------------------------------------------------------------------
# 6. get unknown returns None
# ---------------------------------------------------------------------------

class GetUnknownTests(unittest.TestCase):
    def test_get_unknown(self):
        store = RecoveryJobStore()
        self.assertIsNone(store.get("nope"))

    def test_get_after_all_pruned(self):
        store = RecoveryJobStore(max_jobs=2)
        for i in range(3):
            store.create(f"rec-{i:03d}", "reload_selected_model")
        # rec-000 should be pruned
        self.assertIsNone(store.get("rec-000"))
        self.assertIsNotNone(store.get("rec-002"))


# ---------------------------------------------------------------------------
# 7. snapshot
# ---------------------------------------------------------------------------

class SnapshotTests(unittest.TestCase):
    def test_limit(self):
        store = RecoveryJobStore()
        for i in range(5):
            store.create(f"rec-{i:03d}", "reload_selected_model")
        s = store.snapshot(limit=3)
        self.assertEqual(s["count"], 3)
        self.assertEqual(len(s["jobs"]), 3)

    def test_most_recent_first(self):
        store = RecoveryJobStore()
        for i in range(3):
            store.create(f"rec-{i:03d}", "reload_selected_model")
        s = store.snapshot(limit=10)
        # Most recent job should be first
        self.assertEqual(s["jobs"][0]["request_id"], "rec-002")

    def test_schema(self):
        store = RecoveryJobStore()
        self.assertEqual(store.snapshot()["schema"], RECOVERY_JOBS_SCHEMA)

    def test_json_serialisable(self):
        store = RecoveryJobStore()
        store.create("rec-x", "reload_selected_model")
        store.mark_running("rec-x")
        store.mark_completed("rec-x", post_status={"ok": True})
        json.dumps(store.snapshot())


# ---------------------------------------------------------------------------
# 8. pruning
# ---------------------------------------------------------------------------

class PruningTests(unittest.TestCase):
    def test_max_jobs_enforced(self):
        store = RecoveryJobStore(max_jobs=3)
        for i in range(5):
            store.create(f"rec-{i:03d}", "reload_selected_model")
        s = store.snapshot(limit=10)
        self.assertLessEqual(s["count"], 3)

    def test_oldest_pruned(self):
        store = RecoveryJobStore(max_jobs=2)
        store.create("rec-old", "reload_selected_model")
        store.create("rec-new1", "reload_selected_model")
        store.create("rec-new2", "reload_selected_model")
        self.assertIsNone(store.get("rec-old"))
        self.assertIsNotNone(store.get("rec-new2"))


# ---------------------------------------------------------------------------
# 9. thread safety
# ---------------------------------------------------------------------------

class ThreadSafetyTests(unittest.TestCase):
    def test_concurrent_create_and_complete(self):
        store = RecoveryJobStore()
        errors = []

        def worker(i):
            try:
                rid = f"rec-{i:04d}"
                store.create(rid, "reload_selected_model")
                store.mark_running(rid)
                time.sleep(0.001)
                store.mark_completed(rid, post_status={"i": i})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# 10. Integration: recovery_status with jobs
# ---------------------------------------------------------------------------

class RecoveryStatusIntegrationTests(unittest.TestCase):
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

    def test_jobs_omitted_without_arg(self):
        from proxy.qz_recovery_status import build_recovery_status
        p = build_recovery_status(self._ss())
        self.assertNotIn("jobs", p)

    def test_jobs_included_when_supplied(self):
        from proxy.qz_recovery_status import build_recovery_status
        store = RecoveryJobStore()
        snap = store.snapshot()
        p = build_recovery_status(self._ss(), recovery_jobs=snap)
        self.assertIn("jobs", p)
        self.assertEqual(p["jobs"]["schema"], RECOVERY_JOBS_SCHEMA)

    def test_jobs_count_reflects_store(self):
        from proxy.qz_recovery_status import build_recovery_status
        store = RecoveryJobStore()
        store.create("rec-z", "reload_selected_model")
        p = build_recovery_status(self._ss(), recovery_jobs=store.snapshot())
        self.assertEqual(p["jobs"]["count"], 1)

    def test_json_serialisable_with_jobs(self):
        from proxy.qz_recovery_status import build_recovery_status
        store = RecoveryJobStore()
        store.create("rec-ser", "reload_selected_model")
        store.mark_running("rec-ser")
        p = build_recovery_status(self._ss(), recovery_jobs=store.snapshot())
        json.dumps(p)


# ---------------------------------------------------------------------------
# 11. ASYNC_SUPPORTED_ACTIONS constant
# ---------------------------------------------------------------------------

class AsyncSupportedActionsTests(unittest.TestCase):
    def test_reload_in_async_supported(self):
        from proxy.qz_request_router import ASYNC_SUPPORTED_ACTIONS
        self.assertIn("reload_selected_model", ASYNC_SUPPORTED_ACTIONS)

    def test_restart_not_in_async_supported(self):
        from proxy.qz_request_router import ASYNC_SUPPORTED_ACTIONS
        self.assertNotIn("restart_backend", ASYNC_SUPPORTED_ACTIONS)

    def test_safe_actions_not_async(self):
        from proxy.qz_request_router import ASYNC_SUPPORTED_ACTIONS, SAFE_TRIGGER_ACTIONS
        self.assertTrue(ASYNC_SUPPORTED_ACTIONS.isdisjoint(SAFE_TRIGGER_ACTIONS))


# ---------------------------------------------------------------------------
# 12. _recovery_error_payload for async_not_supported
# ---------------------------------------------------------------------------

class AsyncNotSupportedErrorTests(unittest.TestCase):
    def test_async_not_supported_shape(self):
        from proxy.qz_request_router import RequestRouter
        p = RequestRouter._recovery_error_payload(
            "async_not_supported",
            "async=true is supported only for reload_selected_model.",
            action="restart_backend",
            blocked_by="bad_request",
        )
        self.assertEqual(p["schema"], "qz.recovery.error.v1")
        self.assertEqual(p["error"], "async_not_supported")
        self.assertEqual(p["blocked_by"], "bad_request")
        self.assertFalse(p["ok"])


if __name__ == "__main__":
    unittest.main()
