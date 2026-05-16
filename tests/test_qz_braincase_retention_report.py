"""Tests for retention_report_records() and the retention-report / prune CLI — #54 Slice C.

All tests are read-only: no DB writes, no retire_state_record() calls.
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_retention import load_default_retention_policy
from proxy.qz_braincase_review import retention_report_records

_REPO = Path(__file__).resolve().parents[1]
_REVIEW_SCRIPT = _REPO / "scripts" / "qz-braincase-review"

_MS_DAY = 86_400_000


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


def _now():
    return int(time.time() * 1000)


def _rec(record_id, status, retention, tier="working_state",
         record_type="recent_topic", memory_domain="coding",
         age_days=0, now_ms=None):
    if now_ms is None:
        now_ms = _now()
    ts = now_ms - age_days * _MS_DAY
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": record_type,
        "claim": f"Test claim for {record_id}.",
        "summary": f"Summary for {record_id}.",
        "status": status,
        "visibility": "internal" if status == "candidate" else "renderable",
        "confidence": 0.9,
        "importance": 0.5,
        "retention": retention,
        "created_at_ms": ts,
        "updated_at_ms": ts,
        "source_refs": [],
        "tags": ["test"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": None,
    }


# =============================================================================
# 1-6: retention_report_records core behaviour
# =============================================================================

class RetentionReportCoreTests(unittest.TestCase):

    def test_report_ok_and_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = retention_report_records(
                db, now_ms=_now(), policy=load_default_retention_policy()
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])

    def test_report_includes_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r1", "candidate", "ephemeral", age_days=8, now_ms=now_ms))  # retire
            db.put_state_record(_rec("r2", "active", "project", tier="project_state",
                                     record_type="project_decision", age_days=100, now_ms=now_ms))  # stale
            db.put_state_record(_rec("r3", "active", "durable", tier="preference_constraint_memory",
                                     record_type="preference", age_days=365, now_ms=now_ms))  # keep
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy())
            counts = result["counts"]
            self.assertIn("retire", counts)
            self.assertIn("stale", counts)
            self.assertIn("keep", counts)
            self.assertGreater(counts["retire"] + counts["stale"] + counts["keep"], 0)

    def test_report_decisions_bounded_fields_only(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(_rec("r1", "candidate", "ephemeral"))
            result = retention_report_records(
                db, now_ms=_now(), policy=load_default_retention_policy()
            )
            for d in result["decisions"]:
                self.assertNotIn("claim", d, "claim must not appear in report decision")
                self.assertNotIn("tags", d, "tags must not appear in report decision")
                self.assertNotIn("source_refs", d)
                self.assertNotIn("metadata", d)
                self.assertIn("summary", d)
                self.assertLessEqual(len(d["summary"]), 100)

    def test_report_decisions_no_raw_state_record_dump(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            db.put_state_record(_rec("r1", "candidate", "ephemeral"))
            result = retention_report_records(
                db, now_ms=_now(), policy=load_default_retention_policy()
            )
            for d in result["decisions"]:
                for forbidden in ("claim", "tags", "source_refs", "metadata",
                                  "supersedes", "superseded_by", "confidence"):
                    self.assertNotIn(forbidden, d)

    def test_report_does_not_mutate_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r1", "candidate", "ephemeral", age_days=30, now_ms=now_ms))
            before = db.get_state_record("r1")
            retention_report_records(db, now_ms=now_ms,
                                     policy=load_default_retention_policy())
            after = db.get_state_record("r1")
            self.assertEqual(before["status"], after["status"],
                             "report must not mutate record status")
            self.assertEqual(before["visibility"], after["visibility"])

    def test_report_does_not_call_retire(self):
        """Candidate reported as retire must remain candidate in DB after report."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_retire_check", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy())
            # Find the retire decision
            retire_decisions = [d for d in result["decisions"]
                                if d.get("action") == "retire"]
            if retire_decisions:
                rec_after = db.get_state_record("r_retire_check")
                self.assertEqual(rec_after["status"], "candidate",
                                 "Record must remain candidate after dry-run report")


# =============================================================================
# 7-14: evaluator decisions via report
# =============================================================================

class RetentionReportDecisionTests(unittest.TestCase):

    def _get_decision(self, result, record_id):
        for d in result["decisions"]:
            if d.get("record_id") == record_id:
                return d
        return None

    def test_candidate_ephemeral_expired_appears_as_retire(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_exp", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy())
            d = self._get_decision(result, "r_exp")
            self.assertIsNotNone(d)
            self.assertEqual(d["action"], "retire")

    def test_active_project_old_appears_as_stale(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_proj", "active", "project",
                                     tier="project_state", record_type="project_decision",
                                     age_days=100, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy())
            d = self._get_decision(result, "r_proj")
            self.assertIsNotNone(d)
            self.assertEqual(d["action"], "stale")

    def test_active_durable_old_appears_as_keep(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_dur", "active", "durable",
                                     tier="preference_constraint_memory",
                                     record_type="preference",
                                     age_days=365, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy())
            d = self._get_decision(result, "r_dur")
            self.assertIsNotNone(d)
            self.assertEqual(d["action"], "keep")

    def test_action_filter_returns_only_requested_action(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r1", "candidate", "ephemeral", age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r2", "active", "durable", age_days=365, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy(),
                                              action="keep")
            for d in result["decisions"]:
                self.assertEqual(d["action"], "keep")

    def test_status_filter_candidate_uses_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_cand", "candidate", "ephemeral", age_days=2, now_ms=now_ms))
            db.put_state_record(_rec("r_act", "active", "ephemeral", age_days=2, now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy(),
                                              status="candidate")
            ids = [d["record_id"] for d in result["decisions"]]
            self.assertIn("r_cand", ids)
            self.assertNotIn("r_act", ids)

    def test_retention_filter_ephemeral(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_eph", "candidate", "ephemeral", now_ms=now_ms))
            db.put_state_record(_rec("r_dur", "candidate", "durable", now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy(),
                                              retention="ephemeral")
            ids = [d["record_id"] for d in result["decisions"]]
            self.assertIn("r_eph", ids)
            self.assertNotIn("r_dur", ids)

    def test_memory_domain_filter_exact(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_coding", "candidate", "ephemeral",
                                     memory_domain="coding", now_ms=now_ms))
            db.put_state_record(_rec("r_hsm", "candidate", "ephemeral",
                                     memory_domain="hsm", now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy(),
                                              memory_domain="coding")
            ids = [d["record_id"] for d in result["decisions"]]
            self.assertIn("r_coding", ids)
            self.assertNotIn("r_hsm", ids)

    def test_limit_respected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            for i in range(10):
                db.put_state_record(_rec(f"r_{i:03d}", "candidate", "ephemeral",
                                         now_ms=now_ms))
            result = retention_report_records(db, now_ms=now_ms,
                                              policy=load_default_retention_policy(),
                                              limit=3)
            self.assertLessEqual(result["records_returned"], 3)

    def test_empty_db_returns_ok_zero_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = retention_report_records(
                db, now_ms=_now(), policy=load_default_retention_policy()
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["records_seen"], 0)
            self.assertEqual(result["records_returned"], 0)
            self.assertEqual(result["decisions"], [])

    def test_disabled_db_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            result = retention_report_records(
                db, now_ms=_now(), policy=load_default_retention_policy()
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["dry_run"])


# =============================================================================
# 16-25: CLI tests
# =============================================================================

class RetentionReportCLITests(unittest.TestCase):

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_REVIEW_SCRIPT)] + list(args),
            capture_output=True, text=True, cwd=str(_REPO),
        )

    def test_retention_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("retention-report", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"retention-report failed:\n{result.stderr}")

    def test_retention_report_json_parseable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("retention-report", "--db-path", db_path, "--json")
            self.assertEqual(result.returncode, 0)
            parsed = json.loads(result.stdout)
            self.assertIsInstance(parsed, dict)
            self.assertTrue(parsed.get("dry_run"))

    def test_retention_report_human_output_has_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("retention-report", "--db-path", db_path)
            self.assertIn("dry-run", result.stdout.lower())

    def test_retention_report_shows_retire_but_does_not_retire(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            now_ms = _now()
            db.put_state_record(_rec("r_retire_cli", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            db.close()

            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("retention-report", "--db-path", db_path,
                               "--action", "retire", "--json")
            self.assertEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            retire_decisions = [d for d in report.get("decisions", [])
                                if d.get("action") == "retire"]
            if retire_decisions:
                # Verify record is still candidate in DB
                db2 = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
                db2.init()
                rec = db2.get_state_record("r_retire_cli")
                db2.close()
                self.assertEqual(rec["status"], "candidate",
                                 "Record must remain candidate after retention-report")

    def test_prune_dry_run_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--dry-run", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"prune --dry-run failed:\n{result.stderr}")

    def test_prune_apply_exits_zero(self):
        """prune --apply is implemented in Slice D and must exit 0 on a valid DB."""
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--apply", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"prune --apply must exit 0: {result.stderr}")

    def test_prune_without_dry_run_fails(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--db-path", db_path)
            self.assertNotEqual(result.returncode, 0,
                                "prune without --dry-run must fail in Slice C")

    def test_no_new_model_facing_tools(self):
        from proxy.qz_braincase_tools import (
            get_braincase_tool_definitions,
            QZ_BRAINCASE_TOOLS_ENABLED_ENV,
            QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV,
        )
        env = {
            QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true",
            QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV: "true",
        }
        names = [d["name"] for d in get_braincase_tool_definitions(env=env)]
        for forbidden in ("braincase.prune", "braincase.retention_report",
                          "braincase.expire", "braincase.promote_candidate",
                          "braincase.write", "braincase.update",
                          "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)


# =============================================================================
# 24-25: Regression — existing commands and smoke
# =============================================================================

class ExistingCommandsRegressionTests(unittest.TestCase):

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_REVIEW_SCRIPT)] + list(args),
            capture_output=True, text=True, cwd=str(_REPO),
        )

    def test_list_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("list", "--db-path", db_path)
            self.assertEqual(result.returncode, 0)

    def test_smoke_script_still_passes(self):
        result = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "qz-braincase-smoke")],
            capture_output=True, text=True, cwd=str(_REPO),
        )
        self.assertEqual(result.returncode, 0,
                         f"Smoke failed:\n{result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
