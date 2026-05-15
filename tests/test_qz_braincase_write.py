"""Tests for proxy/qz_braincase_write.py — Slice D write/update helpers.

Covers: scope_resolve, redaction_check, dedup_check, conflict_check, source_link,
braincase_write_state_record, braincase_update_state_record.

Uses Slice A fixture data where possible.
All helpers are internal plumbing; no model-facing tools are tested here.
"""
import copy
import json
import pathlib
import tempfile
import unittest
from pathlib import Path

from proxy.qz_braincase_db import BrainCaseDB
from proxy.qz_braincase_write import (
    braincase_update_state_record,
    braincase_write_state_record,
    conflict_check,
    dedup_check,
    redaction_check,
    scope_resolve,
    source_link,
)

# ---------------------------------------------------------------------------
# Fixture helpers (same pattern as test_qz_braincase_db.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BC_FIXTURES = _REPO_ROOT / "docs" / "fixtures" / "braincase"


def _load_source_refs() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((_BC_FIXTURES / "source-refs").glob("*.json"))
    ]


def _load_state_records() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((_BC_FIXTURES / "state-records").glob("*.json"))
    ]


def _fixture_by_id(records: list[dict], record_id: str) -> dict:
    return next(r for r in records if r["record_id"] == record_id)


def _make_record(
    record_id: str = "test_rec_001",
    memory_domain: str = "coding",
    tier: str = "project_state",
    record_type: str = "constraint",
    claim: str = "Test claim for write helper tests.",
    tags: list | None = None,
    **kwargs,
) -> dict:
    """Build a minimal valid StateRecord dict for test use."""
    return {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": record_type,
        "claim": claim,
        "summary": "Test summary.",
        "status": "active",
        "visibility": "internal",
        "confidence": 1.0,
        "importance": 0.8,
        "retention": "project",
        "created_at_ms": 1778803200000,
        "updated_at_ms": 1778803200000,
        "source_refs": [],
        "tags": tags if tags is not None else ["test", "write-helper"],
        "supersedes": None,
        "superseded_by": None,
        "metadata": None,
        **kwargs,
    }


def _make_source_ref(
    source_ref_id: str = "sref_test_001",
    source_type: str = "manual_note",
    summary: str = "Test source ref.",
    locator: str = "test://locator",
) -> dict:
    return {
        "source_ref_id": source_ref_id,
        "source_type": source_type,
        "title": "Test source ref",
        "summary": summary,
        "locator": locator,
        "content_hash": None,
        "captured_at_ms": None,
        "metadata": None,
    }


def _fresh_db(td: str) -> BrainCaseDB:
    db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
    assert db.init()
    return db


def _disabled_db(td: str) -> BrainCaseDB:
    return BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)


# ---------------------------------------------------------------------------
# scope_resolve tests
# ---------------------------------------------------------------------------

class ScopeResolveTests(unittest.TestCase):

    # 1. accepts a configured memory_domain
    def test_accepts_configured_domain(self):
        rec = _make_record(memory_domain="coding")
        result = scope_resolve(rec)
        self.assertTrue(result["ok"])
        self.assertEqual(result["memory_domain"], "coding")
        self.assertEqual(result["errors"], [])

    def test_accepts_any_domain_without_allowed_list(self):
        """No allowed_memory_domains means any non-empty domain is accepted."""
        for domain in ("coding", "hsm", "roleplay", "personal", "custom_domain_xyz"):
            with self.subTest(domain=domain):
                rec = _make_record(memory_domain=domain)
                result = scope_resolve(rec)
                self.assertTrue(result["ok"])
                self.assertEqual(result["memory_domain"], domain)

    # 2. rejects missing memory_domain
    def test_rejects_missing_domain(self):
        rec = _make_record()
        del rec["memory_domain"]
        result = scope_resolve(rec)
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["errors"]), 0)

    # 3. rejects empty memory_domain
    def test_rejects_empty_domain(self):
        rec = _make_record(memory_domain="")
        result = scope_resolve(rec)
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["errors"]), 0)

    # 3. rejects domain outside allowed list
    def test_rejects_domain_outside_allowed_list(self):
        rec = _make_record(memory_domain="hsm")
        result = scope_resolve(rec, allowed_memory_domains=["coding", "personal"])
        self.assertFalse(result["ok"])
        self.assertGreater(len(result["errors"]), 0)

    def test_accepts_domain_within_allowed_list(self):
        rec = _make_record(memory_domain="coding")
        result = scope_resolve(rec, allowed_memory_domains=["coding", "hsm"])
        self.assertTrue(result["ok"])

    # 4. does not infer / normalize / grant domains
    def test_does_not_normalize_domain(self):
        """memory_domain is stored exactly as supplied — never modified."""
        rec = _make_record(memory_domain="  coding  ")  # with spaces
        result = scope_resolve(rec)
        # The domain is returned exactly as-is (the caller is responsible for normalization)
        self.assertEqual(result["memory_domain"], "  coding  ")

    def test_does_not_grant_cross_domain_access(self):
        """Supplying a different current_memory_domain produces a warning, not access."""
        rec = _make_record(memory_domain="hsm")
        result = scope_resolve(rec, current_memory_domain="coding")
        # Still ok (no cross-domain enforcement here, just a warning)
        self.assertTrue(result["ok"])
        # But a warning is issued
        self.assertTrue(any("differs" in w for w in result["warnings"]))


# ---------------------------------------------------------------------------
# redaction_check tests
# ---------------------------------------------------------------------------

class RedactionCheckTests(unittest.TestCase):

    # 5. rejects forbidden fields
    def test_rejects_raw_prompt(self):
        rec = _make_record()
        rec["raw_prompt"] = "forbidden content"
        result = redaction_check(rec)
        self.assertFalse(result["ok"])
        self.assertIn("raw_prompt", result["forbidden_fields"])

    def test_rejects_raw_request_body(self):
        rec = _make_record()
        rec["raw_request_body"] = "forbidden"
        result = redaction_check(rec)
        self.assertFalse(result["ok"])
        self.assertIn("raw_request_body", result["forbidden_fields"])

    def test_rejects_all_forbidden_field_names(self):
        forbidden_names = [
            "raw_prompt", "raw_request_body", "request_body",
            "full_log", "telemetry_event", "stream_event",
        ]
        for field in forbidden_names:
            with self.subTest(field=field):
                rec = _make_record()
                rec[field] = "forbidden"
                result = redaction_check(rec)
                self.assertFalse(result["ok"])
                self.assertIn(field, result["forbidden_fields"])

    def test_accepts_clean_record(self):
        rec = _make_record()
        result = redaction_check(rec)
        self.assertTrue(result["ok"])
        self.assertEqual(result["forbidden_fields"], [])
        self.assertEqual(result["errors"], [])


# ---------------------------------------------------------------------------
# dedup_check tests
# ---------------------------------------------------------------------------

class DedupCheckTests(unittest.TestCase):

    # 6. finds same normalized claim in same domain
    def test_finds_same_claim_in_same_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            original = _fixture_by_id(records, "rec_bc_001")
            db.put_state_record(original)

            # New record with identical claim, different ID
            dup = _make_record(
                record_id="rec_dedup_test",
                memory_domain=original["memory_domain"],
                tier=original["tier"],
                record_type=original["record_type"],
                claim=original["claim"],
                tags=original["tags"],
            )
            result = dedup_check(db, dup)
            self.assertIsNotNone(result["warning"])
            self.assertEqual(result["warning"], "possible_duplicate")
            dup_ids = [d["record_id"] for d in result["duplicates"]]
            self.assertIn("rec_bc_001", dup_ids)
            db.close()

    # 7. does not cross memory_domain
    def test_does_not_cross_memory_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            original = _fixture_by_id(records, "rec_bc_001")  # domain=coding
            db.put_state_record(original)

            # Same claim but different domain — should NOT find duplicate
            dup = _make_record(
                record_id="rec_dedup_cross_domain",
                memory_domain="hsm",  # different domain
                tier=original["tier"],
                record_type=original["record_type"],
                claim=original["claim"],
            )
            result = dedup_check(db, dup)
            self.assertIsNone(result["warning"])
            self.assertEqual(result["duplicates"], [])
            db.close()

    def test_no_duplicate_when_record_id_matches(self):
        """A record should not be flagged as duplicate of itself."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            rec = _fixture_by_id(records, "rec_bc_001")
            db.put_state_record(rec)
            result = dedup_check(db, rec)
            self.assertIsNone(result["warning"])
            db.close()


# ---------------------------------------------------------------------------
# conflict_check tests
# ---------------------------------------------------------------------------

class ConflictCheckTests(unittest.TestCase):

    def _make_conflicting_record(self, claim: str, tags: list | None = None) -> dict:
        """Make a constraint record that opposes rec_bc_001's 'must not' claim."""
        return _make_record(
            record_id="rec_conflict_test",
            memory_domain="coding",
            tier="project_state",
            record_type="constraint",
            claim=claim,
            tags=tags or ["braincase", "constraint"],
        )

    # 8. surfaces simple opposing constraint marker
    def test_surfaces_opposing_must_marker(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            # rec_bc_001 claim contains "must not" — store it
            stored = _fixture_by_id(records, "rec_bc_001")
            db.put_state_record(stored)

            # New record with "must" (opposing "must not") and overlapping tags
            new_rec = self._make_conflicting_record(
                claim="BrainCaseDB must always store every request automatically."
            )
            result = conflict_check(db, new_rec)
            self.assertIsNotNone(result["warning"])
            self.assertEqual(result["warning"], "possible_conflict")
            conflict_ids = [c["record_id"] for c in result["conflicts"]]
            self.assertIn("rec_bc_001", conflict_ids)
            db.close()

    def test_no_conflict_without_tag_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            stored = _fixture_by_id(records, "rec_bc_001")
            db.put_state_record(stored)

            # No overlapping tags → no conflict
            new_rec = self._make_conflicting_record(
                claim="BrainCaseDB must always store every request.",
                tags=["unrelated", "other-domain"],
            )
            result = conflict_check(db, new_rec)
            self.assertIsNone(result["warning"])
            self.assertEqual(result["conflicts"], [])
            db.close()

    def test_no_conflict_across_record_types(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            stored = _fixture_by_id(records, "rec_bc_001")  # constraint
            db.put_state_record(stored)

            # Different record_type → no conflict
            new_rec = _make_record(
                record_id="rec_conflict_type_test",
                memory_domain="coding",
                tier="project_state",
                record_type="episode",  # different type
                claim="BrainCaseDB must always store every request.",
                tags=["braincase", "constraint"],
            )
            result = conflict_check(db, new_rec)
            self.assertIsNone(result["warning"])
            db.close()

    # 9. does not block write by itself
    def test_conflict_hint_does_not_block_write(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            records = _load_state_records()
            stored = _fixture_by_id(records, "rec_bc_001")
            db.put_state_record(stored)

            conflicting = self._make_conflicting_record(
                claim="BrainCaseDB must always store every request automatically."
            )
            write_result = braincase_write_state_record(db, conflicting)

            # Write succeeds despite conflict hint
            self.assertTrue(write_result["ok"])
            self.assertTrue(write_result["stored"])
            self.assertIsNotNone(write_result["conflicts"])
            self.assertEqual(write_result["conflicts"]["warning"], "possible_conflict")
            db.close()


# ---------------------------------------------------------------------------
# source_link tests
# ---------------------------------------------------------------------------

class SourceLinkTests(unittest.TestCase):

    # 10. stores supplied SourceRefs
    def test_stores_supplied_source_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            sref = _make_source_ref("sref_link_test_001")
            rec = _make_record(source_refs=["sref_link_test_001"])

            result = source_link(db, rec, source_refs=[sref])

            self.assertTrue(result["ok"])
            self.assertIn("sref_link_test_001", result["linked_source_refs"])
            # Verify it was stored in DB
            stored = db.get_source_ref("sref_link_test_001")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["locator"], sref["locator"])
            db.close()

    # 11. reports missing source_refs as warnings
    def test_reports_missing_source_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record(source_refs=["sref_nonexistent_xyz"])

            # No source_refs supplied, not pre-stored
            result = source_link(db, rec, source_refs=None)

            self.assertTrue(result["ok"])  # missing refs warn, not error
            self.assertIn("sref_nonexistent_xyz", result["missing_source_refs"])
            self.assertTrue(any("sref_nonexistent_xyz" in w for w in result["warnings"]))
            db.close()

    def test_ok_when_source_ref_already_in_db(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            sref = _make_source_ref("sref_pre_stored")
            db.put_source_ref(sref)

            rec = _make_record(source_refs=["sref_pre_stored"])
            result = source_link(db, rec, source_refs=None)

            self.assertTrue(result["ok"])
            self.assertIn("sref_pre_stored", result["linked_source_refs"])
            self.assertEqual(result["missing_source_refs"], [])
            db.close()

    def test_does_not_mutate_source_refs_input(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            sref = _make_source_ref("sref_mutate_test")
            original_sref = copy.deepcopy(sref)
            rec = _make_record(source_refs=["sref_mutate_test"])
            source_link(db, rec, source_refs=[sref])
            self.assertEqual(sref, original_sref)
            db.close()


# ---------------------------------------------------------------------------
# braincase_write_state_record tests
# ---------------------------------------------------------------------------

class WriteStateRecordTests(unittest.TestCase):

    # 12. stores valid fixture-shaped record
    def test_write_stores_valid_fixture_record(self):
        records = _load_state_records()
        refs = _load_source_refs()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _fixture_by_id(records, "rec_bc_004")  # coding domain, no source refs that need DB
            result = braincase_write_state_record(db, rec)
            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            self.assertEqual(result["record_id"], "rec_bc_004")
            # Verify it's in DB
            stored = db.get_state_record("rec_bc_004")
            self.assertIsNotNone(stored)
            db.close()

    # 13. write result includes dedup / conflict / source_link sections
    def test_write_result_includes_all_sections(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            result = braincase_write_state_record(db, rec)
            self.assertIn("dedup", result)
            self.assertIn("conflicts", result)
            self.assertIn("source_link", result)
            self.assertIsNotNone(result["dedup"])
            self.assertIsNotNone(result["conflicts"])
            self.assertIsNotNone(result["source_link"])
            db.close()

    # 14. does not mutate input record or source_refs
    def test_write_does_not_mutate_input_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            original = copy.deepcopy(rec)
            braincase_write_state_record(db, rec)
            self.assertEqual(rec, original)
            db.close()

    def test_write_does_not_mutate_source_refs_input(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record(source_refs=["sref_no_mutate_test"])
            sref = _make_source_ref("sref_no_mutate_test")
            original_sref = copy.deepcopy(sref)
            braincase_write_state_record(db, rec, source_refs=[sref])
            self.assertEqual(sref, original_sref)
            db.close()

    # 15. preserves memory_domain exactly
    def test_write_preserves_memory_domain_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record(memory_domain="coding")
            braincase_write_state_record(db, rec)
            stored = db.get_state_record(rec["record_id"])
            self.assertEqual(stored["memory_domain"], "coding")
            db.close()

    def test_write_preserves_hsm_domain_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record(memory_domain="hsm")
            braincase_write_state_record(db, rec, allowed_memory_domains=["hsm", "coding"])
            stored = db.get_state_record(rec["record_id"])
            self.assertIsNotNone(stored)
            self.assertEqual(stored["memory_domain"], "hsm")
            db.close()

    # 16. rejects forbidden raw fields
    def test_write_rejects_forbidden_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            rec["raw_prompt"] = "should be blocked"
            result = braincase_write_state_record(db, rec)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("raw_prompt" in e for e in result["errors"]))
            db.close()

    # 17. rejects missing memory_domain
    def test_write_rejects_missing_memory_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            del rec["memory_domain"]
            result = braincase_write_state_record(db, rec)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertGreater(len(result["errors"]), 0)
            db.close()

    def test_write_rejects_empty_memory_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record(memory_domain="")
            result = braincase_write_state_record(db, rec)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            db.close()

    # 18. returns structured error when DB disabled
    def test_write_returns_error_when_db_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            rec = _make_record()
            result = braincase_write_state_record(db, rec)
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            self.assertTrue(any("disabled" in e.lower() for e in result["errors"]))

    # 19. creates no record when validation fails
    def test_write_creates_no_record_when_validation_fails(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            rec["raw_prompt"] = "blocked"
            braincase_write_state_record(db, rec)
            stored = db.get_state_record(rec["record_id"])
            self.assertIsNone(stored)
            db.close()

    # 20. never creates RenderPacket
    def test_write_never_creates_render_packet(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = _make_record()
            result = braincase_write_state_record(db, rec)
            self.assertNotIn("rendered_text", result)
            self.assertNotIn("packet_id", result)
            self.assertNotIn("budget_tokens", result)
            db.close()

    def test_write_with_source_refs_supplied(self):
        """write should store supplied SourceRefs and link them."""
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            sref = _make_source_ref("sref_write_supplied_001")
            rec = _make_record(
                record_id="rec_write_with_srefs",
                source_refs=["sref_write_supplied_001"],
            )
            result = braincase_write_state_record(db, rec, source_refs=[sref])
            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            # SourceRef should be in DB
            stored_sref = db.get_source_ref("sref_write_supplied_001")
            self.assertIsNotNone(stored_sref)
            # source_link section should show it linked
            self.assertIn("sref_write_supplied_001", result["source_link"]["linked_source_refs"])
            db.close()


# ---------------------------------------------------------------------------
# braincase_update_state_record tests
# ---------------------------------------------------------------------------

class UpdateStateRecordTests(unittest.TestCase):

    def _stored_record(self, db: BrainCaseDB, **kwargs) -> dict:
        rec = _make_record(**kwargs)
        db.put_state_record(rec)
        return rec

    # 21. retire update marks record retired
    def test_retire_update_marks_record_retired(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            rec = self._stored_record(db, record_id="rec_retire_test")
            result = braincase_update_state_record(
                db, "rec_retire_test", "retire", reason="no longer needed"
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            stored = db.get_state_record("rec_retire_test")
            self.assertEqual(stored["status"], "retired")
            db.close()

    def test_retire_nonexistent_record_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_update_state_record(
                db, "rec_does_not_exist", "retire", reason="test"
            )
            self.assertFalse(result["ok"])
            self.assertGreater(len(result["errors"]), 0)
            db.close()

    # 22. supersede stores new record and marks old superseded
    def test_supersede_stores_new_and_marks_old_superseded(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            old_rec = self._stored_record(db, record_id="rec_old_supersede_test")
            new_rec = _make_record(
                record_id="rec_new_supersede_test",
                memory_domain="coding",
                claim="Updated claim replacing old record.",
            )

            result = braincase_update_state_record(
                db,
                "rec_old_supersede_test",
                "supersede",
                new_record=new_rec,
                reason="updated",
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            self.assertEqual(result.get("new_record_id"), "rec_new_supersede_test")

            old_got = db.get_state_record("rec_old_supersede_test")
            self.assertEqual(old_got["status"], "superseded")
            self.assertEqual(old_got["superseded_by"], "rec_new_supersede_test")

            new_got = db.get_state_record("rec_new_supersede_test")
            self.assertIsNotNone(new_got)
            self.assertEqual(new_got["supersedes"], "rec_old_supersede_test")
            db.close()

    # 23. supersede does not mutate new_record input
    def test_supersede_does_not_mutate_new_record_input(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            self._stored_record(db, record_id="rec_supersede_mutate_old")
            new_rec = _make_record(
                record_id="rec_supersede_mutate_new",
                memory_domain="coding",
            )
            original = copy.deepcopy(new_rec)
            braincase_update_state_record(
                db, "rec_supersede_mutate_old", "supersede",
                new_record=new_rec, reason="mutation test"
            )
            self.assertEqual(new_rec, original)
            db.close()

    # 24. update returns structured error for unsupported op
    def test_update_returns_error_for_correct_op(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_update_state_record(
                db, "rec_any", "correct", patch={"claim": "new"}
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("not yet implemented" in e for e in result["errors"]))

    def test_update_returns_error_for_link_op(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_update_state_record(db, "rec_any", "link")
            self.assertFalse(result["ok"])
            self.assertTrue(any("not yet implemented" in e for e in result["errors"]))

    def test_update_returns_error_for_unknown_op(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_update_state_record(db, "rec_any", "invent_new_op")
            self.assertFalse(result["ok"])
            self.assertTrue(any("Unknown" in e for e in result["errors"]))

    def test_update_returns_error_when_db_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            db = _disabled_db(td)
            result = braincase_update_state_record(db, "rec_any", "retire")
            self.assertFalse(result["ok"])
            self.assertTrue(any("disabled" in e.lower() for e in result["errors"]))

    def test_supersede_requires_new_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            result = braincase_update_state_record(
                db, "rec_any", "supersede", new_record=None, reason="missing"
            )
            self.assertFalse(result["ok"])
            self.assertTrue(any("new_record" in e for e in result["errors"]))
            db.close()

    def test_supersede_rejects_forbidden_fields_in_new_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            self._stored_record(db, record_id="rec_supersede_forbidden_old")
            new_rec = _make_record(record_id="rec_supersede_forbidden_new", memory_domain="coding")
            new_rec["raw_prompt"] = "forbidden"
            result = braincase_update_state_record(
                db, "rec_supersede_forbidden_old", "supersede",
                new_record=new_rec, reason="test"
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            db.close()


# ---------------------------------------------------------------------------
# HSM domain tests (tests 25–26)
# ---------------------------------------------------------------------------

class HSMDomainTests(unittest.TestCase):
    """HSM is a configured memory_domain example, not a special case."""

    # 25. HSM fixture writes when allowed_memory_domains includes "hsm"
    def test_hsm_write_succeeds_with_hsm_in_allowed_list(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            hsm_rec = _fixture_by_id(records, "rec_bc_007")  # memory_domain="hsm"
            result = braincase_write_state_record(
                db, hsm_rec, allowed_memory_domains=["hsm", "coding"]
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["stored"])
            stored = db.get_state_record("rec_bc_007")
            self.assertIsNotNone(stored)
            self.assertEqual(stored["memory_domain"], "hsm")
            db.close()

    def test_hsm_write_blocked_when_allowed_list_excludes_hsm(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            hsm_rec = _fixture_by_id(records, "rec_bc_007")
            result = braincase_write_state_record(
                db, hsm_rec, allowed_memory_domains=["coding"]  # hsm not allowed
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["stored"])
            # No record stored
            self.assertIsNone(db.get_state_record("rec_bc_007"))
            db.close()

    def test_hsm_write_succeeds_without_allowed_list(self):
        """Without an allowed list, any domain is accepted (not just built-in domains)."""
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            hsm_rec = _fixture_by_id(records, "rec_bc_007")
            result = braincase_write_state_record(db, hsm_rec)
            self.assertTrue(result["ok"])
            db.close()

    # 26. HSM fixture is not special-cased
    def test_hsm_record_goes_through_same_path_as_coding(self):
        """HSM domain record uses identical write path as any other domain."""
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            hsm_rec = _fixture_by_id(records, "rec_bc_007")
            coding_rec = _fixture_by_id(records, "rec_bc_001")

            r_hsm = braincase_write_state_record(db, hsm_rec)
            r_coding = braincase_write_state_record(db, coding_rec)

            # Both have same result structure
            self.assertEqual(set(r_hsm.keys()), set(r_coding.keys()))
            # Both stored successfully
            self.assertTrue(r_hsm["stored"])
            self.assertTrue(r_coding["stored"])
            # Both have dedup/conflict/source_link
            for key in ("dedup", "conflicts", "source_link"):
                self.assertIn(key, r_hsm)
                self.assertIn(key, r_coding)
            db.close()

    def test_hsm_metadata_note_preserved_after_write(self):
        """HSM fixture metadata (with hsm_domain_note) must survive round-trip."""
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = _fresh_db(td)
            hsm_rec = _fixture_by_id(records, "rec_bc_007")
            braincase_write_state_record(db, hsm_rec, allowed_memory_domains=["hsm"])
            stored = db.get_state_record("rec_bc_007")
            self.assertIsNotNone(stored)
            meta = stored.get("metadata") or {}
            self.assertIn("hsm_domain_note", meta)
            db.close()


if __name__ == "__main__":
    unittest.main()
