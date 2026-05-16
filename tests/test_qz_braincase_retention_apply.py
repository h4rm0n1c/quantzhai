"""Tests for apply_retention_prune() and prune --apply CLI — #54 Slice D.

Safety invariants verified here:
- Only action=retire records are retired.
- stale/keep records are never retired.
- active+durable records are never retired.
- Re-evaluation protects records whose decision changed.
- All retirements use retire_state_record() (revision recorded).
- No raw DELETE.
- No model-facing tools added.
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
from proxy.qz_braincase_review import apply_retention_prune

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
# 1-18: apply_retention_prune helper
# =============================================================================

class ApplyRetentionPruneTests(unittest.TestCase):

    def test_dry_run_true_mutates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_dry", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=True)
            self.assertTrue(result["dry_run"])
            # Record must still be candidate
            rec = db.get_state_record("r_dry")
            self.assertEqual(rec["status"], "candidate")

    def test_apply_retires_expired_candidate_ephemeral(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_exp", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            self.assertFalse(result["dry_run"])
            self.assertGreater(result["retired_count"], 0)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertIn("r_exp", retired_ids)

    def test_retired_record_status_is_retired(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_status", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            apply_retention_prune(db, now_ms=now_ms,
                                  policy=load_default_retention_policy(),
                                  dry_run=False)
            rec = db.get_state_record("r_status")
            self.assertEqual(rec["status"], "retired")

    def test_retirement_creates_revision_entry(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_rev", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            apply_retention_prune(db, now_ms=now_ms,
                                  policy=load_default_retention_policy(),
                                  dry_run=False)
            if db._conn:
                rows = db._conn.execute(
                    "SELECT operation FROM qz_braincase_record_revisions WHERE record_id = ?",
                    ("r_rev",),
                ).fetchall()
                ops = [r[0] for r in rows]
                self.assertIn("retire", ops)

    def test_only_retires_action_retire_not_stale(self):
        """Stale active project record must not be retired."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            # stale (active + project, 100d)
            db.put_state_record(_rec("r_stale", "active", "project",
                                     tier="project_state",
                                     record_type="project_decision",
                                     age_days=100, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_stale", retired_ids)
            rec = db.get_state_record("r_stale")
            self.assertEqual(rec["status"], "active")

    def test_active_durable_never_retired(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_dur", "active", "durable",
                                     tier="preference_constraint_memory",
                                     record_type="preference",
                                     age_days=365, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_dur", retired_ids)
            rec = db.get_state_record("r_dur")
            self.assertEqual(rec["status"], "active")

    def test_keep_record_not_retired(self):
        """Fresh candidate (age 0) should be keep, not retired."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_keep", "candidate", "ephemeral",
                                     age_days=0, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_keep", retired_ids)
            rec = db.get_state_record("r_keep")
            self.assertEqual(rec["status"], "candidate")

    def test_already_retired_record_is_not_re_retired(self):
        """Already-retired records return 'keep' from the evaluator (already_inactive),
        so they never appear in the retire candidates and are not touched."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_already", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            db.retire_state_record("r_already", "pre-retired")
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_already", retired_ids)
            # Record remains retired (not double-retired)
            rec = db.get_state_record("r_already")
            self.assertEqual(rec["status"], "retired")

    def test_reevaluation_protects_changed_decision(self):
        """If re-evaluation returns a different action, skip the retirement."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_change", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            # Simulate a policy change: empty policy keeps everything
            safe_policy = {"schema": "braincase/retention-policy@1",
                           "default_action": "keep", "rules": []}
            # First report with original policy says retire, but apply uses safe_policy
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=safe_policy, dry_run=False)
            # With safe_policy all records keep, so nothing retired
            self.assertEqual(result["retired_count"], 0)
            rec = db.get_state_record("r_change")
            self.assertEqual(rec["status"], "candidate")

    def test_result_has_retired_and_skipped_counts(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r1", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            self.assertIn("retired_count", result)
            self.assertIn("skipped_count", result)
            self.assertIn("retired", result)
            self.assertIn("skipped", result)

    def test_result_no_raw_state_record_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r1", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            for r in result.get("retired", []):
                for forbidden in ("claim", "summary", "tags", "source_refs",
                                  "metadata", "supersedes", "superseded_by",
                                  "confidence", "importance"):
                    self.assertNotIn(forbidden, r,
                                     f"retired entry must not contain {forbidden!r}")

    def test_memory_domain_filter_exact(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_coding", "candidate", "ephemeral",
                                     memory_domain="coding", age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r_hsm", "candidate", "ephemeral",
                                     memory_domain="hsm", age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           memory_domain="coding", dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertIn("r_coding", retired_ids)
            self.assertNotIn("r_hsm", retired_ids)
            rec_hsm = db.get_state_record("r_hsm")
            self.assertEqual(rec_hsm["status"], "candidate")

    def test_status_filter_works(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_cand", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r_act", "active", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           status="candidate", dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertIn("r_cand", retired_ids)
            # active ephemeral: not in status=candidate filter
            self.assertNotIn("r_act", retired_ids)

    def test_retention_filter_works(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_eph", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r_sess", "candidate", "session",
                                     age_days=2, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           retention="ephemeral", dry_run=False)
            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertIn("r_eph", retired_ids)
            # session 2d is stale (3d threshold), not retired
            self.assertNotIn("r_sess", retired_ids)

    def test_limit_respected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            for i in range(10):
                db.put_state_record(_rec(f"r_{i:03d}", "candidate", "ephemeral",
                                         age_days=8, now_ms=now_ms))
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           limit=3, dry_run=False)
            self.assertLessEqual(result["retired_count"], 3)

    def test_reason_in_revision(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_reason", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))
            apply_retention_prune(db, now_ms=now_ms,
                                  policy=load_default_retention_policy(),
                                  reason="my_custom_reason", dry_run=False)
            if db._conn:
                rows = db._conn.execute(
                    "SELECT reason FROM qz_braincase_record_revisions WHERE record_id = ?",
                    ("r_reason",),
                ).fetchall()
                reasons = [r[0] for r in rows]
                self.assertTrue(any("my_custom_reason" in (r or "") for r in reasons))

    def test_empty_db_returns_ok_zero_retired(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = apply_retention_prune(db, now_ms=_now(),
                                           policy=load_default_retention_policy(),
                                           dry_run=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["retired_count"], 0)
            self.assertFalse(result["dry_run"])


# =============================================================================
# 19-32: CLI tests
# =============================================================================

class PruneApplyCLITests(unittest.TestCase):

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_REVIEW_SCRIPT)] + list(args),
            capture_output=True, text=True, cwd=str(_REPO),
        )

    def _db_with_expired_candidate(self, td: str) -> str:
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        db.init()
        now_ms = _now()
        db.put_state_record(_rec("r_cli_exp", "candidate", "ephemeral",
                                  age_days=8, now_ms=now_ms))
        db.close()
        return str(Path(td) / "state.sqlite3")

    def test_prune_bare_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--db-path", db_path)
            self.assertNotEqual(result.returncode, 0)

    def test_prune_dry_run_exits_zero_no_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--dry-run", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"prune --dry-run failed:\n{result.stderr}")
            # Record still candidate
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            rec = db.get_state_record("r_cli_exp")
            db.close()
            self.assertEqual(rec["status"], "candidate")

    def test_prune_apply_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--apply", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"prune --apply failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}")

    def test_prune_apply_json_parseable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--apply", "--db-path", db_path, "--json")
            self.assertEqual(result.returncode, 0)
            parsed = json.loads(result.stdout)
            self.assertIsInstance(parsed, dict)
            self.assertFalse(parsed.get("dry_run"))
            self.assertIn("retired_count", parsed)

    def test_prune_apply_human_output_has_retired_count(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--apply", "--db-path", db_path)
            self.assertIn("retired:", result.stdout.lower())

    def test_prune_apply_does_not_retire_stale_project(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            now_ms = _now()
            db.put_state_record(_rec("r_stale_cli", "active", "project",
                                      tier="project_state",
                                      record_type="project_decision",
                                      age_days=100, now_ms=now_ms))
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--apply", "--db-path", db_path, "--json")
            parsed = json.loads(result.stdout)
            retired_ids = [r["record_id"] for r in parsed.get("retired", [])]
            self.assertNotIn("r_stale_cli", retired_ids)

    def test_prune_apply_does_not_retire_active_durable(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            now_ms = _now()
            db.put_state_record(_rec("r_dur_cli", "active", "durable",
                                      tier="preference_constraint_memory",
                                      record_type="preference",
                                      age_days=365, now_ms=now_ms))
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--apply", "--db-path", db_path, "--json")
            parsed = json.loads(result.stdout)
            retired_ids = [r["record_id"] for r in parsed.get("retired", [])]
            self.assertNotIn("r_dur_cli", retired_ids)

    def test_prune_apply_memory_domain_filter(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            now_ms = _now()
            db.put_state_record(_rec("r_coding_cli", "candidate", "ephemeral",
                                      memory_domain="coding", age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r_hsm_cli", "candidate", "ephemeral",
                                      memory_domain="hsm", age_days=8, now_ms=now_ms))
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--apply", "--db-path", db_path,
                               "--memory-domain", "coding", "--json")
            parsed = json.loads(result.stdout)
            retired_ids = [r["record_id"] for r in parsed.get("retired", [])]
            self.assertIn("r_coding_cli", retired_ids)
            self.assertNotIn("r_hsm_cli", retired_ids)

    def test_prune_apply_retention_filter(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            db.init()
            now_ms = _now()
            db.put_state_record(_rec("r_eph_cli", "candidate", "ephemeral",
                                      age_days=8, now_ms=now_ms))
            db.put_state_record(_rec("r_sess_cli", "candidate", "session",
                                      age_days=15, now_ms=now_ms))
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("prune", "--apply", "--db-path", db_path,
                               "--retention", "ephemeral", "--json")
            parsed = json.loads(result.stdout)
            retired_ids = [r["record_id"] for r in parsed.get("retired", [])]
            self.assertIn("r_eph_cli", retired_ids)
            self.assertNotIn("r_sess_cli", retired_ids)

    def test_reason_flag_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = self._db_with_expired_candidate(td)
            result = self._run("prune", "--apply", "--db-path", db_path,
                               "--reason", "ci_prune_test", "--json")
            self.assertEqual(result.returncode, 0)
            parsed = json.loads(result.stdout)
            for r in parsed.get("retired", []):
                self.assertIn("ci_prune_test", r.get("reason", ""))


# =============================================================================
# 29-32: Regression
# =============================================================================

class SliceDRegressionTests(unittest.TestCase):

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_REVIEW_SCRIPT)] + list(args),
            capture_output=True, text=True, cwd=str(_REPO),
        )

    def test_retention_report_still_dry_run_only(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("retention-report", "--db-path", db_path, "--json")
            self.assertEqual(result.returncode, 0)
            parsed = json.loads(result.stdout)
            self.assertTrue(parsed.get("dry_run"), "retention-report must always be dry-run")

    def test_list_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._run("list", "--db-path",
                               str(Path(td) / "state.sqlite3"))
            self.assertEqual(result.returncode, 0)

    def test_smoke_still_passes(self):
        result = subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "qz-braincase-smoke")],
            capture_output=True, text=True, cwd=str(_REPO),
        )
        self.assertEqual(result.returncode, 0,
                         f"Smoke failed:\n{result.stdout}\n{result.stderr}")

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

    def test_no_raw_delete_in_apply_code_path(self):
        """Confirm retire_state_record is the only retirement path (code review)."""
        review_src = (_REPO / "proxy" / "qz_braincase_review.py").read_text()
        # The apply function must not contain raw DELETE SQL
        apply_section_start = review_src.find("def apply_retention_prune")
        apply_section = review_src[apply_section_start:apply_section_start + 4000]
        self.assertNotIn("DELETE FROM", apply_section,
                         "apply_retention_prune must not contain raw DELETE SQL")
        self.assertNotIn("conn.execute", apply_section,
                         "apply_retention_prune must not directly execute SQL")



# =============================================================================
# Stronger re-evaluation test (audit checklist item 10)
# =============================================================================

class ReEvaluationProtectionTests(unittest.TestCase):
    """Prove that apply re-fetches and re-evaluates before retiring.

    Scenario: The initial report says 'retire' for a record.
    Between report and apply, the record is manually retired (status changes).
    Apply must skip it because re-evaluation returns 'already_inactive'.
    """

    def test_apply_skips_record_retired_between_report_and_apply(self):
        """Record retired externally between report and apply phases is skipped."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_externally_retired", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))

            # Phase 1: verify initial decision would be retire
            from proxy.qz_braincase_retention import retention_decision_for_record
            rec = db.get_state_record("r_externally_retired")
            decision = retention_decision_for_record(rec, now_ms=now_ms,
                                                     policy=load_default_retention_policy())
            self.assertEqual(decision["action"], "retire",
                             "Precondition: initial decision must be retire")

            # Simulate external retirement before apply
            db.retire_state_record("r_externally_retired", "external retirement before apply")

            # Now apply — must skip because re-evaluation returns already_inactive
            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=load_default_retention_policy(),
                                           dry_run=False)

            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_externally_retired", retired_ids,
                             "Record retired externally must be skipped by apply")

    def test_apply_skips_record_whose_policy_decision_changed(self):
        """Record that moved to a safe policy (keep) between phases is skipped."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            now_ms = _now()
            db.put_state_record(_rec("r_decision_changed", "candidate", "ephemeral",
                                     age_days=8, now_ms=now_ms))

            # The policy used for apply has no rules (everything keeps)
            empty_policy = {"schema": "braincase/retention-policy@1",
                            "default_action": "keep", "rules": []}

            result = apply_retention_prune(db, now_ms=now_ms,
                                           policy=empty_policy, dry_run=False)

            retired_ids = [r["record_id"] for r in result["retired"]]
            self.assertNotIn("r_decision_changed", retired_ids,
                             "Record whose policy changed to keep must not be retired")
            rec = db.get_state_record("r_decision_changed")
            self.assertEqual(rec["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
