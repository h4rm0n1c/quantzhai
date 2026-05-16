"""Tests for proxy/qz_braincase_review.py and scripts/qz-braincase-review — Slice I.

Coverage:
  List (1-5), Inspect (6-9), Promote (10-23), Reject (24-29),
  CLI (30-36), Exposure (37-40).
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_review import (
    inspect_candidate_record,
    list_candidate_records,
    promote_candidate_record,
    reject_candidate_record,
)
from proxy.qz_braincase_tools import braincase_render_tool, braincase_recall_tool

_REPO = Path(__file__).resolve().parents[1]
_REVIEW_SCRIPT = _REPO / "scripts" / "qz-braincase-review"


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


def _make_record(
    record_id: str,
    memory_domain: str = "coding",
    tier: str = "project_state",
    record_type: str = "constraint",
    claim: str = "Test claim.",
    summary: str = "Test summary.",
    status: str = "candidate",
    visibility: str = "internal",
    importance: float = 0.7,
) -> dict:
    ts = int(time.time() * 1000)
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": record_type,
        "claim": claim,
        "summary": summary,
        "status": status,
        "visibility": visibility,
        "confidence": 0.9,
        "importance": importance,
        "retention": "project",
        "created_at_ms": ts,
        "updated_at_ms": ts,
        "source_refs": [],
        "tags": ["test"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": {"review_note": "test", "why_it_matters": "test"},
    }


def _seed_candidate(db: BrainCaseDB, record_id: str = "rec_cand_test_001",
                    memory_domain: str = "coding", **kwargs) -> str:
    rec = _make_record(record_id, memory_domain=memory_domain, **kwargs)
    assert db.put_state_record(rec), f"Failed to seed: {db.last_error}"
    return record_id


def _seed_active(db: BrainCaseDB, record_id: str = "rec_active_test_001",
                 memory_domain: str = "coding") -> str:
    rec = _make_record(record_id, memory_domain=memory_domain,
                       status="active", visibility="renderable")
    assert db.put_state_record(rec)
    return record_id


# =============================================================================
# 1-5: list_candidate_records
# =============================================================================

class ListCandidateTests(unittest.TestCase):

    def test_returns_only_candidate_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_cand_1")
            _seed_active(db, "rec_active_1")
            results = list_candidate_records(db)
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_cand_1", ids)
            self.assertNotIn("rec_active_1", ids)

    def test_filters_by_memory_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_coding", memory_domain="coding")
            _seed_candidate(db, "rec_hsm", memory_domain="hsm")
            results = list_candidate_records(db, memory_domain="coding")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_coding", ids)
            self.assertNotIn("rec_hsm", ids)

    def test_does_not_return_active_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_active(db, "rec_active_x")
            results = list_candidate_records(db)
            ids = [r["record_id"] for r in results]
            self.assertNotIn("rec_active_x", ids)

    def test_does_not_return_retired_records(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_retire_test")
            db.retire_state_record(rid, "retire for test")
            results = list_candidate_records(db)
            ids = [r["record_id"] for r in results]
            self.assertNotIn("rec_retire_test", ids)

    def test_returns_bounded_summaries_no_raw_blobs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_summary_check")
            results = list_candidate_records(db)
            for r in results:
                self.assertNotIn("raw_prompt", r)
                self.assertNotIn("raw_request_body", r)
                # claim is present but bounded
                claim = r.get("claim", "")
                self.assertLessEqual(len(claim), 201)


# =============================================================================
# 6-9: inspect_candidate_record
# =============================================================================

class InspectCandidateTests(unittest.TestCase):

    def test_returns_candidate_details(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_inspect_001")
            result = inspect_candidate_record(db, "rec_inspect_001")
            self.assertTrue(result["ok"])
            self.assertEqual(result["record"]["record_id"], "rec_inspect_001")
            self.assertEqual(result["record"]["status"], "candidate")

    def test_unknown_record_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = inspect_candidate_record(db, "rec_nonexistent")
            self.assertFalse(result["ok"])
            self.assertIn("not_found", result.get("error", ""))

    def test_can_inspect_active_record(self):
        """inspect allows any status, not just candidate."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_active(db, "rec_active_inspect")
            result = inspect_candidate_record(db, "rec_active_inspect")
            self.assertTrue(result["ok"])

    def test_inspect_has_no_raw_forbidden_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_inspect_safe")
            result = inspect_candidate_record(db, "rec_inspect_safe")
            rec = result.get("record", {})
            for forbidden in ("raw_prompt", "raw_request_body", "request_body", "full_log"):
                self.assertNotIn(forbidden, rec)


# =============================================================================
# 10-23: promote_candidate_record
# =============================================================================

class PromoteCandidateTests(unittest.TestCase):

    def test_promote_returns_ok(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_promote_ok")
            result = promote_candidate_record(db, "rec_promote_ok")
            self.assertTrue(result["ok"])
            self.assertTrue(result["promoted"])

    def test_promoted_status_is_active(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_promote_status")
            promote_candidate_record(db, "rec_promote_status")
            rec = db.get_state_record("rec_promote_status")
            self.assertEqual(rec["status"], "active")

    def test_promoted_visibility_renderable_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_promote_vis")
            promote_candidate_record(db, "rec_promote_vis")
            rec = db.get_state_record("rec_promote_vis")
            self.assertEqual(rec["visibility"], "renderable")

    def test_promote_with_visibility_internal(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_promote_int")
            promote_candidate_record(db, "rec_promote_int", visibility="internal")
            rec = db.get_state_record("rec_promote_int")
            self.assertEqual(rec["status"], "active")
            self.assertEqual(rec["visibility"], "internal")

    def test_promoted_renderable_appears_in_render(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_promote_render")
            promote_candidate_record(db, rid)
            result = braincase_render_tool(db, {"purpose": "test", "memory_domain": "coding"})
            self.assertIn(rid, result.get("source_record_ids", []))

    def test_promoted_renderable_appears_in_recall(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_promote_recall")
            promote_candidate_record(db, rid)
            result = braincase_recall_tool(db, {
                "purpose": "test", "memory_domain": "coding", "recall_mode": "task"
            })
            self.assertIn(rid, result.get("source_record_ids", []))

    def test_promoted_internal_not_in_render(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_promote_int_render")
            promote_candidate_record(db, rid, visibility="internal")
            result = braincase_render_tool(db, {"purpose": "test", "memory_domain": "coding"})
            self.assertNotIn(rid, result.get("source_record_ids", []))

    def test_promoted_internal_not_in_recall(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_promote_int_recall")
            promote_candidate_record(db, rid, visibility="internal")
            result = braincase_recall_tool(db, {
                "purpose": "test", "memory_domain": "coding", "recall_mode": "task"
            })
            self.assertNotIn(rid, result.get("source_record_ids", []))

    def test_non_candidate_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_active(db, "rec_already_active")
            result = promote_candidate_record(db, "rec_already_active")
            self.assertFalse(result["ok"])
            self.assertFalse(result["promoted"])
            self.assertTrue(any("not_candidate" in e for e in result["errors"]))

    def test_memory_domain_guard_blocks_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_domain_guard", memory_domain="coding")
            result = promote_candidate_record(
                db, "rec_domain_guard",
                allowed_memory_domains=["hsm"],
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("memory_domain_mismatch" in e for e in result["errors"]))

    def test_unknown_record_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = promote_candidate_record(db, "rec_nonexistent_promote")
            self.assertFalse(result["ok"])
            self.assertTrue(any("record_not_found" in e for e in result["errors"]))

    def test_dedup_conflict_warnings_do_not_block(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            # Seed two candidates with the same claim → dedup hint expected
            rec1 = _make_record("rec_dedup_1", claim="must use typed returns")
            rec2 = _make_record("rec_dedup_2", claim="must use typed returns")
            db.put_state_record(rec1)
            db.put_state_record(rec2)
            result = promote_candidate_record(db, "rec_dedup_2")
            # Should still succeed despite dedup hint
            self.assertTrue(result["ok"])

    def test_promotion_records_revision(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_revision_check")
            promote_candidate_record(db, "rec_revision_check", reason="test promotion")
            # Verify revision exists
            if db._conn:
                rows = db._conn.execute(
                    "SELECT operation, reason FROM qz_braincase_record_revisions "
                    "WHERE record_id = ?",
                    ("rec_revision_check",),
                ).fetchall()
                ops = [r[0] for r in rows]
                reasons = [r[1] for r in rows]
                self.assertIn("promote", ops)
                self.assertTrue(any("test promotion" in (r or "") for r in reasons))

    def test_dry_run_does_not_change_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_dry_run_promote")
            result = promote_candidate_record(db, "rec_dry_run_promote", dry_run=True)
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["promoted"])
            rec = db.get_state_record("rec_dry_run_promote")
            self.assertEqual(rec["status"], "candidate")  # unchanged


# =============================================================================
# 24-29: reject_candidate_record
# =============================================================================

class RejectCandidateTests(unittest.TestCase):

    def test_reject_returns_ok(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_reject_ok")
            result = reject_candidate_record(db, "rec_reject_ok")
            self.assertTrue(result["ok"])
            self.assertTrue(result["rejected"])

    def test_rejected_status_is_retired(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_reject_status")
            reject_candidate_record(db, "rec_reject_status")
            rec = db.get_state_record("rec_reject_status")
            self.assertEqual(rec["status"], "retired")

    def test_rejected_not_in_render_or_recall(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_reject_render")
            reject_candidate_record(db, rid)
            render_result = braincase_render_tool(db, {"purpose": "t", "memory_domain": "coding"})
            recall_result = braincase_recall_tool(db, {
                "purpose": "t", "memory_domain": "coding", "recall_mode": "task"
            })
            self.assertNotIn(rid, render_result.get("source_record_ids", []))
            self.assertNotIn(rid, recall_result.get("source_record_ids", []))

    def test_non_candidate_cannot_be_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_active(db, "rec_active_reject")
            result = reject_candidate_record(db, "rec_active_reject")
            self.assertFalse(result["ok"])
            self.assertTrue(any("not_candidate" in e for e in result["errors"]))

    def test_dry_run_does_not_change_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_dry_run_reject")
            result = reject_candidate_record(db, "rec_dry_run_reject", dry_run=True)
            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["rejected"])
            rec = db.get_state_record("rec_dry_run_reject")
            self.assertEqual(rec["status"], "candidate")  # unchanged

    def test_reject_records_revision_through_retire_path(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_reject_revision")
            reject_candidate_record(db, "rec_reject_revision", reason="op reject test")
            if db._conn:
                rows = db._conn.execute(
                    "SELECT operation FROM qz_braincase_record_revisions WHERE record_id = ?",
                    ("rec_reject_revision",),
                ).fetchall()
                ops = [r[0] for r in rows]
                self.assertIn("retire", ops)


# =============================================================================
# 30-36: CLI
# =============================================================================

class ReviewCLITests(unittest.TestCase):

    def _run(self, *args, db_path: str | None = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(_REVIEW_SCRIPT)]
        if db_path:
            cmd_list = list(cmd) + list(args)
            # Inject --db-path before the subcommand args if not already there
            if "--db-path" not in args:
                sub = cmd_list[2]
                cmd_list = [cmd_list[0], cmd_list[1], sub, "--db-path", db_path] + list(cmd_list[3:])
            cmd = cmd_list
        else:
            cmd = cmd + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO))

    def test_script_exists_and_executable(self):
        import os
        self.assertTrue(_REVIEW_SCRIPT.exists())
        self.assertTrue(os.access(_REVIEW_SCRIPT, os.X_OK))

    def test_list_exits_zero_with_temp_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("list", "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"list failed:\n{result.stderr}")

    def test_inspect_exits_zero_for_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_cli_inspect")
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("inspect", rid, "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"inspect failed:\n{result.stderr}")

    def test_promote_exits_zero_for_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_cli_promote")
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("promote", rid, "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"promote failed:\n{result.stderr}")

    def test_reject_exits_zero_for_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_cli_reject")
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("reject", rid, "--db-path", db_path)
            self.assertEqual(result.returncode, 0,
                             f"reject failed:\n{result.stderr}")

    def test_list_json_emits_parseable_json(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            _seed_candidate(db, "rec_json_test")
            db.close()
            db_path = str(Path(td) / "state.sqlite3")
            result = self._run("list", "--db-path", db_path, "--json")
            self.assertEqual(result.returncode, 0)
            parsed = json.loads(result.stdout)
            self.assertIsInstance(parsed, list)

    def test_cli_uses_db_path_not_var(self):
        """CLI with --db-path must not touch the real var/ DB."""
        import os
        real_db = Path(_REPO) / "var" / "qz-state.sqlite3"
        before_exists = real_db.exists()
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "cli_test.sqlite3")
            self._run("list", "--db-path", db_path)
        # Real DB existence must not have changed
        self.assertEqual(real_db.exists(), before_exists)


# =============================================================================
# 37-40: model-tool exposure unchanged
# =============================================================================

class ReviewExposureTests(unittest.TestCase):

    def test_promote_candidate_not_in_tool_definitions(self):
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
        self.assertNotIn("braincase.promote_candidate", names)

    def test_write_remains_unexposed(self):
        from proxy.qz_braincase_tools import get_braincase_tool_definitions
        for env in ({}, {"proxy.qz_braincase_tools.QZ_BRAINCASE_TOOLS_ENABLED_ENV": "true"}):
            names = [d["name"] for d in get_braincase_tool_definitions(env={})]
            self.assertNotIn("braincase.write", names)

    def test_update_search_inspect_remain_unexposed(self):
        from proxy.qz_braincase_tools import (
            get_braincase_tool_definitions,
            QZ_BRAINCASE_TOOLS_ENABLED_ENV,
        )
        names = [d["name"] for d in get_braincase_tool_definitions(
            env={QZ_BRAINCASE_TOOLS_ENABLED_ENV: "true"}
        )]
        for forbidden in ("braincase.update", "braincase.search", "braincase.inspect"):
            self.assertNotIn(forbidden, names)

    def test_no_automatic_ingestion_from_review_operations(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rid = _seed_candidate(db, "rec_ingest_check")
            before = len(db.list_state_records(memory_domain="coding", limit=200))
            # list, inspect, promote, reject must not create extra records
            list_candidate_records(db)
            inspect_candidate_record(db, rid)
            # promote creates no extra records
            promote_candidate_record(db, rid, dry_run=True)
            after = len(db.list_state_records(memory_domain="coding", limit=200))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
