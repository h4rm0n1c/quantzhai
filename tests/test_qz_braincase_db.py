import copy
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from proxy.qz_braincase_db import (
    BrainCaseDB,
    QZ_BRAINCASE_DB_SCHEMA_VERSION,
    QZ_STATE_DB_ENABLED_ENV,
    QZ_STATE_DB_PATH_ENV,
    _FORBIDDEN_RECORD_FIELDS,
)

# ---------------------------------------------------------------------------
# Fixture helpers
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


def _load_render_packets() -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((_BC_FIXTURES / "render-packets").glob("*.json"))
    ]


# ---------------------------------------------------------------------------
# Slice 1 tests (original — preserved unchanged)
# ---------------------------------------------------------------------------

class BrainCaseDBTests(unittest.TestCase):
    def test_disabled_mode_does_not_open_or_create_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=False)

            self.assertFalse(db.init())

            self.assertFalse(db.available)
            self.assertFalse(db_path.exists())
            self.assertEqual(db.health()["schema_version"], None)

    def test_from_env_defaults_disabled_even_with_default_path(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "QZ_ROOT": td,
            }
            db = BrainCaseDB.from_env(env)

            self.assertFalse(db.enabled)
            self.assertEqual(db.path, Path(td) / "var" / "qz-state.sqlite3")
            self.assertFalse(db.path.exists())

    def test_enabled_tmpdir_path_creates_db(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "nested" / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=True)

            self.assertTrue(db.init())

            self.assertTrue(db.available)
            self.assertTrue(db_path.is_file())
            db.close()

    def test_from_env_uses_explicit_enabled_and_path(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            env = {
                QZ_STATE_DB_ENABLED_ENV: "true",
                QZ_STATE_DB_PATH_ENV: str(db_path),
            }

            db = BrainCaseDB.from_env(env)

            self.assertTrue(db.enabled)
            self.assertEqual(db.path, db_path)

    def test_init_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)

            self.assertTrue(db.init())
            self.assertTrue(db.init())

            self.assertTrue(db.available)
            self.assertIsNone(db.last_error)
            db.close()

    def test_schema_version_and_metadata_are_set(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=True)

            self.assertTrue(db.init())
            db.close()

            conn = sqlite3.connect(db_path)
            try:
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                metadata_value = conn.execute(
                    "SELECT value FROM qz_schema_metadata WHERE key = 'schema_version'"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(user_version, QZ_BRAINCASE_DB_SCHEMA_VERSION)
            self.assertEqual(metadata_value, str(QZ_BRAINCASE_DB_SCHEMA_VERSION))

    def test_health_reports_state(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=True)

            self.assertTrue(db.init())
            health = db.health()

            self.assertEqual(
                health,
                {
                    "enabled": True,
                    "path": str(db_path),
                    "available": True,
                    "schema_version": QZ_BRAINCASE_DB_SCHEMA_VERSION,
                    "fts_available": db.fts_available,
                    "last_error": None,
                },
            )
            db.close()

    def test_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            self.assertTrue(db.init())

            db.close()
            db.close()

            self.assertFalse(db.available)
            self.assertEqual(db.health()["schema_version"], None)

    def test_bad_path_open_failure_degrades_to_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            os.mkdir(db_path)
            db = BrainCaseDB(path=db_path, enabled=True)

            self.assertFalse(db.init())

            self.assertFalse(db.available)
            self.assertIn("OperationalError", db.last_error or "")

    def test_write_wrapper_catches_sqlite_errors(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
            self.assertTrue(db.init())

            self.assertFalse(db.execute_write("INSERT INTO missing_table VALUES (?)", ("x",)))

            self.assertIn("OperationalError", db.last_error or "")
            db.close()

    def test_write_wrapper_noops_when_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=False)

            self.assertFalse(db.execute_write("CREATE TABLE should_not_exist(value TEXT)"))

            self.assertFalse(db_path.exists())


# ---------------------------------------------------------------------------
# Slice 2 tests
# ---------------------------------------------------------------------------

class BrainCaseDBSliceBTests(unittest.TestCase):
    """Round-trip and doctrine tests for Slice B storage methods."""

    def _fresh_db(self, td: str) -> BrainCaseDB:
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        self.assertTrue(db.init())
        return db

    # --- 1. init creates Slice B tables ---

    def test_init_creates_slice_b_tables(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.close()

            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()

            expected = {
                "qz_schema_metadata",
                "qz_braincase_source_refs",
                "qz_braincase_state_records",
                "qz_braincase_record_sources",
                "qz_braincase_record_revisions",
                "qz_braincase_record_links",
            }
            self.assertTrue(expected.issubset(tables), f"Missing tables: {expected - tables}")

    # --- 2. schema version / user_version updated to 3 ---

    def test_schema_version_is_3(self):
        self.assertEqual(QZ_BRAINCASE_DB_SCHEMA_VERSION, 3)

    def test_user_version_is_3(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.close()
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                user_version = conn.execute("PRAGMA user_version").fetchone()[0]
                meta_version = conn.execute(
                    "SELECT value FROM qz_schema_metadata WHERE key='schema_version'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(user_version, 3)
            self.assertEqual(meta_version, "3")

    # --- 3. put/get source ref round-trips fixture ---

    def test_put_get_source_ref_round_trip(self):
        refs = _load_source_refs()
        self.assertGreater(len(refs), 0)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for ref in refs:
                with self.subTest(source_ref_id=ref["source_ref_id"]):
                    self.assertTrue(db.put_source_ref(ref))
                    got = db.get_source_ref(ref["source_ref_id"])
                    self.assertIsNotNone(got)
                    self.assertEqual(got["source_ref_id"], ref["source_ref_id"])
                    self.assertEqual(got["source_type"], ref["source_type"])
                    self.assertEqual(got["summary"], ref["summary"])
                    self.assertEqual(got["locator"], ref["locator"])
                    self.assertEqual(got["title"], ref.get("title"))
                    self.assertEqual(got["content_hash"], ref.get("content_hash"))
                    self.assertEqual(got["captured_at_ms"], ref.get("captured_at_ms"))
                    self.assertEqual(got["metadata"], ref.get("metadata"))
            db.close()

    def test_get_source_ref_not_found_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertIsNone(db.get_source_ref("nonexistent_id"))
            db.close()

    # --- 4. put/get state record round-trips fixture ---

    def test_put_get_state_record_round_trip(self):
        records = _load_state_records()
        self.assertGreater(len(records), 0)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                with self.subTest(record_id=rec["record_id"]):
                    self.assertTrue(db.put_state_record(rec))
                    got = db.get_state_record(rec["record_id"])
                    self.assertIsNotNone(got)
                    # Core identity fields
                    self.assertEqual(got["record_id"], rec["record_id"])
                    self.assertEqual(got["schema"], rec["schema"])
                    self.assertEqual(got["memory_domain"], rec["memory_domain"])
                    self.assertEqual(got["tier"], rec["tier"])
                    self.assertEqual(got["record_type"], rec["record_type"])
                    # Content fields
                    self.assertEqual(got["claim"], rec["claim"])
                    self.assertEqual(got["summary"], rec["summary"])
                    self.assertEqual(got["status"], rec["status"])
                    self.assertEqual(got["visibility"], rec["visibility"])
                    self.assertAlmostEqual(got["confidence"], rec["confidence"], places=6)
                    self.assertAlmostEqual(got["importance"], rec["importance"], places=6)
                    self.assertEqual(got["retention"], rec["retention"])
                    # Timestamps
                    self.assertEqual(got["created_at_ms"], rec["created_at_ms"])
                    self.assertEqual(got["updated_at_ms"], rec["updated_at_ms"])
                    # Collections
                    self.assertEqual(sorted(got["tags"]), sorted(rec.get("tags") or []))
                    self.assertEqual(got["supersedes"], rec.get("supersedes"))
                    self.assertEqual(got["superseded_by"], rec.get("superseded_by"))
                    self.assertEqual(got["metadata"], rec.get("metadata"))
            db.close()

    def test_get_state_record_not_found_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertIsNone(db.get_state_record("nonexistent_id"))
            db.close()

    # --- 5. record_sources links stored from source_refs ---

    def test_record_sources_links_stored(self):
        records = _load_state_records()
        rec = next(r for r in records if r.get("source_refs"))
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(rec))
            got = db.get_state_record(rec["record_id"])
            self.assertIsNotNone(got)
            self.assertEqual(sorted(got["source_refs"]), sorted(rec["source_refs"]))
            # Verify via raw SQL
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                rows = conn.execute(
                    "SELECT source_ref_id FROM qz_braincase_record_sources WHERE record_id = ?",
                    (rec["record_id"],),
                ).fetchall()
                stored_ids = {r[0] for r in rows}
            finally:
                conn.close()
            self.assertEqual(stored_ids, set(rec["source_refs"]))
            db.close()

    def test_put_state_record_is_idempotent(self):
        records = _load_state_records()
        rec = records[0]
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(rec))
            self.assertTrue(db.put_state_record(rec))  # second put is OR REPLACE
            got = db.get_state_record(rec["record_id"])
            self.assertEqual(got["record_id"], rec["record_id"])
            db.close()

    # --- 6. list_state_records filters by memory_domain ---

    def test_list_state_records_filter_by_memory_domain(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                db.put_state_record(rec)

            coding_records = db.list_state_records(memory_domain="coding")
            self.assertGreater(len(coding_records), 0)
            for r in coding_records:
                self.assertEqual(r["memory_domain"], "coding")

            hsm_records = db.list_state_records(memory_domain="hsm")
            self.assertGreater(len(hsm_records), 0)
            for r in hsm_records:
                self.assertEqual(r["memory_domain"], "hsm")

            # Domain that was never stored returns empty list
            empty = db.list_state_records(memory_domain="nonexistent_domain")
            self.assertEqual(empty, [])
            db.close()

    # --- 7. list_state_records filters by tier ---

    def test_list_state_records_filter_by_tier(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                db.put_state_record(rec)

            project_state_recs = db.list_state_records(tier="project_state")
            self.assertGreater(len(project_state_recs), 0)
            for r in project_state_recs:
                self.assertEqual(r["tier"], "project_state")

            working_state_recs = db.list_state_records(tier="working_state")
            self.assertGreater(len(working_state_recs), 0)
            for r in working_state_recs:
                self.assertEqual(r["tier"], "working_state")
            db.close()

    def test_list_state_records_combined_filter(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                db.put_state_record(rec)
            results = db.list_state_records(memory_domain="coding", tier="episodic_memory")
            for r in results:
                self.assertEqual(r["memory_domain"], "coding")
                self.assertEqual(r["tier"], "episodic_memory")
            db.close()

    def test_list_state_records_no_filter_returns_all(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                db.put_state_record(rec)
            results = db.list_state_records()
            self.assertEqual(len(results), len(records))
            db.close()

    # --- 8. memory_domain preserved exactly as supplied ---

    def test_memory_domain_preserved_exactly(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for rec in records:
                db.put_state_record(rec)
            for rec in records:
                got = db.get_state_record(rec["record_id"])
                self.assertEqual(
                    got["memory_domain"],
                    rec["memory_domain"],
                    f"memory_domain must be stored as-is for {rec['record_id']}",
                )
            db.close()

    def test_memory_domain_hsm_stored_as_string(self):
        """HSM fixture uses memory_domain='hsm' as a configured example; stored as plain string."""
        records = _load_state_records()
        hsm_fixture = next(r for r in records if r.get("memory_domain") == "hsm")
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(hsm_fixture))
            got = db.get_state_record(hsm_fixture["record_id"])
            self.assertIsNotNone(got)
            self.assertEqual(got["memory_domain"], "hsm")
            self.assertIsInstance(got["memory_domain"], str)
            db.close()

    # --- 9. no memory_domain enum/registry table ---

    def test_no_memory_domain_registry_table(self):
        """BrainCaseDB must not create a memory_domain registry table."""
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.close()
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()
            for table_name in tables:
                self.assertNotIn(
                    "domain_registry",
                    table_name.lower(),
                    f"Found unexpected domain registry table: {table_name}",
                )
                self.assertNotIn(
                    "memory_domains",
                    table_name.lower(),
                    f"Found unexpected memory_domains table: {table_name}",
                )

    # --- 10. disabled DB returns False/None/[] and creates no file ---

    def test_disabled_db_put_returns_false(self):
        refs = _load_source_refs()
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=False)
            self.assertFalse(db.put_source_ref(refs[0]))
            self.assertFalse(db.put_state_record(records[0]))
            self.assertFalse(db_path.exists())

    def test_disabled_db_read_returns_none_or_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "state.sqlite3"
            db = BrainCaseDB(path=db_path, enabled=False)
            self.assertIsNone(db.get_source_ref("x"))
            self.assertIsNone(db.get_state_record("x"))
            self.assertEqual(db.list_state_records(), [])
            self.assertFalse(db_path.exists())

    def test_disabled_db_retire_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            self.assertFalse(db.retire_state_record("x", "reason"))

    def test_disabled_db_supersede_returns_false(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            self.assertFalse(
                db.supersede_state_record("old_id", records[0], "reason")
            )

    # --- 11. bad DB write failure returns False and sets last_error ---

    def test_bad_write_sets_last_error(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            # Force an error by inserting into a non-existent table via execute_write
            result = db.execute_write("INSERT INTO nonexistent_table VALUES (?)", ("x",))
            self.assertFalse(result)
            self.assertIsNotNone(db.last_error)
            self.assertIn("OperationalError", db.last_error)
            db.close()

    # --- 12. input dicts not mutated ---

    def test_put_source_ref_does_not_mutate_input(self):
        ref = copy.deepcopy(_load_source_refs()[0])
        original = copy.deepcopy(ref)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.put_source_ref(ref)
            self.assertEqual(ref, original)
            db.close()

    def test_put_state_record_does_not_mutate_input(self):
        rec = copy.deepcopy(_load_state_records()[0])
        original = copy.deepcopy(rec)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.put_state_record(rec)
            self.assertEqual(rec, original)
            db.close()

    def test_supersede_does_not_mutate_new_record_input(self):
        records = _load_state_records()
        old_rec = copy.deepcopy(records[0])
        new_rec_base = copy.deepcopy(records[1])
        new_rec_base["record_id"] = "superseding_rec_test"
        original_new = copy.deepcopy(new_rec_base)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.put_state_record(old_rec)
            db.supersede_state_record(old_rec["record_id"], new_rec_base, "test supersession")
            # new_rec_base must not have been mutated
            self.assertEqual(new_rec_base, original_new)
            db.close()

    # --- 13. forbidden fields are rejected ---

    def test_put_source_ref_rejects_forbidden_fields(self):
        for field in _FORBIDDEN_RECORD_FIELDS:
            with self.subTest(field=field):
                refs = _load_source_refs()
                bad_ref = dict(refs[0])
                bad_ref[field] = "forbidden_content"
                with tempfile.TemporaryDirectory() as td:
                    db = self._fresh_db(td)
                    result = db.put_source_ref(bad_ref)
                    self.assertFalse(result)
                    self.assertIsNotNone(db.last_error)
                    self.assertIn(field, db.last_error)
                    db.close()

    def test_put_state_record_rejects_forbidden_fields(self):
        for field in _FORBIDDEN_RECORD_FIELDS:
            with self.subTest(field=field):
                records = _load_state_records()
                bad_rec = dict(records[0])
                bad_rec[field] = "forbidden_content"
                with tempfile.TemporaryDirectory() as td:
                    db = self._fresh_db(td)
                    result = db.put_state_record(bad_rec)
                    self.assertFalse(result)
                    self.assertIsNotNone(db.last_error)
                    self.assertIn(field, db.last_error)
                    db.close()

    def test_supersede_rejects_forbidden_fields_in_new_record(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.put_state_record(records[0])
            bad_new = dict(records[1])
            bad_new["record_id"] = "new_rec_forbidden"
            bad_new["raw_prompt"] = "should be rejected"
            result = db.supersede_state_record(records[0]["record_id"], bad_new, "test")
            self.assertFalse(result)
            self.assertIsNotNone(db.last_error)
            db.close()

    # --- 14. retire marks status retired and records revision ---

    def test_retire_state_record(self):
        records = _load_state_records()
        rec = copy.deepcopy(records[0])
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(rec))
            self.assertTrue(db.retire_state_record(rec["record_id"], "no longer needed", now_ms=9999))

            got = db.get_state_record(rec["record_id"])
            self.assertIsNotNone(got)
            self.assertEqual(got["status"], "retired")
            self.assertEqual(got["updated_at_ms"], 9999)

            # Verify revision was recorded
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                revisions = conn.execute(
                    "SELECT operation, reason FROM qz_braincase_record_revisions WHERE record_id = ?",
                    (rec["record_id"],),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0][0], "retire")
            self.assertEqual(revisions[0][1], "no longer needed")
            db.close()

    def test_retire_nonexistent_record_returns_false(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = db.retire_state_record("does_not_exist", "reason")
            self.assertFalse(result)
            self.assertIsNotNone(db.last_error)
            db.close()

    # --- 15. supersede links old/new and records revision ---

    def test_supersede_state_record(self):
        records = _load_state_records()
        old_rec = copy.deepcopy(records[0])
        new_rec = copy.deepcopy(records[1])
        new_rec["record_id"] = "superseding_test_record"

        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(old_rec))
            self.assertTrue(
                db.supersede_state_record(
                    old_rec["record_id"], new_rec, "architecture update", now_ms=12345
                )
            )

            # Old record should be superseded
            old_got = db.get_state_record(old_rec["record_id"])
            self.assertEqual(old_got["status"], "superseded")
            self.assertEqual(old_got["superseded_by"], "superseding_test_record")

            # New record should exist and link back
            new_got = db.get_state_record("superseding_test_record")
            self.assertIsNotNone(new_got)
            self.assertEqual(new_got["supersedes"], old_rec["record_id"])

            # Revision should be recorded for old record
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                revisions = conn.execute(
                    "SELECT operation, reason FROM qz_braincase_record_revisions WHERE record_id = ?",
                    (old_rec["record_id"],),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(len(revisions), 1)
            self.assertEqual(revisions[0][0], "supersede")
            self.assertEqual(revisions[0][1], "architecture update")
            db.close()

    def test_supersede_nonexistent_old_record_returns_false(self):
        records = _load_state_records()
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            result = db.supersede_state_record(
                "does_not_exist", records[0], "reason"
            )
            self.assertFalse(result)
            self.assertIsNotNone(db.last_error)
            db.close()

    # --- 16. render packet fixtures are not stored as StateRecords ---

    def test_render_packets_are_not_state_records(self):
        """RenderPacket fixtures must not be put_state_record'd successfully.

        RenderPackets have different required fields; put_state_record should
        fail (KeyError or missing required field) rather than store corrupt data.
        """
        packets = _load_render_packets()
        self.assertGreater(len(packets), 0)
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            for pkt in packets:
                with self.subTest(packet_id=pkt.get("packet_id")):
                    # RenderPackets don't have 'tier', 'claim', etc.
                    # put_state_record should fail (return False) for them.
                    result = db.put_state_record(pkt)
                    self.assertFalse(
                        result,
                        f"RenderPacket {pkt.get('packet_id')} should not be storable as StateRecord",
                    )
            db.close()

    # --- 17. HSM fixture memory_domain stored as configured example, no special treatment ---

    def test_hsm_fixture_stored_as_plain_configured_string(self):
        """HSM memory_domain is stored as a plain string with no special DB treatment."""
        records = _load_state_records()
        hsm_fixture = next(r for r in records if r.get("memory_domain") == "hsm")

        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertTrue(db.put_state_record(hsm_fixture))

            got = db.get_state_record(hsm_fixture["record_id"])
            self.assertIsNotNone(got)
            self.assertEqual(got["memory_domain"], "hsm")

            # metadata note is preserved exactly
            meta = got.get("metadata") or {}
            self.assertIn("hsm_domain_note", meta)

            # List with hsm domain filter works the same as any other domain
            hsm_list = db.list_state_records(memory_domain="hsm")
            ids = [r["record_id"] for r in hsm_list]
            self.assertIn(hsm_fixture["record_id"], ids)

            # coding domain filter does NOT return hsm records
            coding_list = db.list_state_records(memory_domain="coding")
            coding_ids = [r["record_id"] for r in coding_list]
            self.assertNotIn(hsm_fixture["record_id"], coding_ids)

            # No special HSM table or column exists
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
            finally:
                conn.close()
            for t in tables:
                self.assertNotIn("hsm", t.lower(), f"Unexpected HSM-specific table: {t}")
            db.close()


# ---------------------------------------------------------------------------
# Slice C tests: search + inspect helpers
# ---------------------------------------------------------------------------

class BrainCaseDBSliceCTests(unittest.TestCase):
    """Tests for search_state_records, inspect_state_records, and query_plan."""

    def _fresh_db(self, td: str) -> BrainCaseDB:
        db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=True)
        self.assertTrue(db.init())
        return db

    def _populated_db(self, td: str) -> BrainCaseDB:
        db = self._fresh_db(td)
        for ref in _load_source_refs():
            db.put_source_ref(ref)
        for rec in _load_state_records():
            db.put_state_record(rec)
        return db

    # --- 1. query_plan: exact for quoted / path-like / issue-like ---

    def test_query_plan_exact_for_quoted_string(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan('"automatically ingest"')
            self.assertEqual(plan["mode"], "exact")
            self.assertEqual(plan["query"], "automatically ingest")
            db.close()

    def test_query_plan_exact_for_path_like(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("docs/braincase-memory-tool-api.md")
            self.assertEqual(plan["mode"], "exact")
            db.close()

    def test_query_plan_exact_for_issue_reference(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("#53 Slice A")
            self.assertEqual(plan["mode"], "exact")
            db.close()

    # --- 2. query_plan: tag for tag:<name> ---

    def test_query_plan_tag_for_tag_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("tag:braincase")
            self.assertEqual(plan["mode"], "tag")
            self.assertEqual(plan["query"], "braincase")
            db.close()

    def test_query_plan_tag_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("tag: hsm ")
            self.assertEqual(plan["mode"], "tag")
            self.assertEqual(plan["query"], "hsm")
            db.close()

    # --- 3. query_plan: fts/default for normal text ---

    def test_query_plan_fts_or_exact_for_normal_text(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("architecture correction")
            # Either fts (if available) or exact (fallback) — both are valid
            self.assertIn(plan["mode"], ("fts", "exact"))
            self.assertEqual(plan["query"], "architecture correction")
            db.close()

    def test_query_plan_fts_available_flag(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            plan = db.query_plan("doctrine")
            if db.fts_available:
                self.assertEqual(plan["mode"], "fts")
            else:
                self.assertEqual(plan["mode"], "exact")
            db.close()

    def test_query_plan_explicit_mode_overrides_auto(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertEqual(db.query_plan("docs/foo", mode="fts")["mode"],
                             "fts" if db.fts_available else "exact")
            self.assertEqual(db.query_plan("tag:anything", mode="exact")["mode"], "exact")
            self.assertEqual(db.query_plan("anything", mode="tag")["mode"], "tag")
            db.close()

    # --- 4. search exact finds fixture by phrase in claim ---

    def test_search_exact_finds_by_claim_phrase(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            # rec_bc_001 claim contains "automatically ingest"
            results = db.search_state_records("automatically ingest", mode="exact")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_001", ids)
            db.close()

    # --- 5. search exact finds fixture by summary phrase ---

    def test_search_exact_finds_by_summary_phrase(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            # rec_bc_002 summary contains "Search -> inspect -> reason"
            results = db.search_state_records("Search -> inspect -> reason", mode="exact")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_002", ids)
            db.close()

    # --- 6. search tag finds fixture by tag ---

    def test_search_tag_finds_by_tag(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            # All coding fixtures have "braincase" tag
            results = db.search_state_records("tag:braincase", limit=20)
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_001", ids)
            self.assertIn("rec_bc_002", ids)
            self.assertIn("rec_bc_003", ids)
            db.close()

    def test_search_tag_mode_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("hsm", mode="tag")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_007", ids)
            db.close()

    # --- 7. search filters by memory_domain exactly ---

    def test_search_filters_memory_domain(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("braincase", mode="tag", memory_domain="coding")
            for r in results:
                self.assertEqual(r["memory_domain"], "coding")
            db.close()

    # --- 8. search filters by tier exactly ---

    def test_search_filters_tier(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("", mode="exact", tier="project_state", limit=20)
            # Empty exact search with tier filter returns all project_state records
            # (LIKE '%' matches everything)
            for r in results:
                self.assertEqual(r["tier"], "project_state")
            # rec_bc_001 is project_state
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_001", ids)
            db.close()

    # --- 9. search does not return hsm records when memory_domain=coding ---

    def test_search_no_hsm_when_domain_coding(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("braincase", mode="tag", memory_domain="coding")
            for r in results:
                self.assertNotEqual(r["memory_domain"], "hsm")
                self.assertNotEqual(r["record_id"], "rec_bc_007")
            db.close()

    # --- 10. search returns hsm record only when memory_domain=hsm or no filter ---

    def test_search_finds_hsm_with_hsm_domain_filter(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("configured", mode="exact", memory_domain="hsm")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_007", ids)
            for r in results:
                self.assertEqual(r["memory_domain"], "hsm")
            db.close()

    def test_search_finds_hsm_with_no_domain_filter(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("tag:hsm", limit=20)
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_007", ids)
            db.close()

    # --- 11. fts search works if FTS5 available, else exact fallback ---

    def test_fts_search_or_exact_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            # mode=fts: if FTS5 not available, query_plan falls back to exact
            results = db.search_state_records("doctrine", mode="fts")
            # Should return at least one record containing "doctrine" in claim/summary/tags
            self.assertIsInstance(results, list)
            # Every result should contain "doctrine" somewhere
            for r in results:
                text = r["claim"] + " " + r["summary"] + " " + " ".join(r["tags"])
                self.assertIn("doctrine", text.lower())
            db.close()

    def test_fts_available_attribute_is_bool(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            self.assertIsInstance(db.fts_available, bool)
            db.close()

    def test_health_exposes_fts_available(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            health = db.health()
            self.assertIn("fts_available", health)
            self.assertEqual(health["fts_available"], db.fts_available)
            db.close()

    # --- 12. put_state_record updates FTS/index data on replace ---

    def test_put_state_record_updates_fts_on_replace(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            records = _load_state_records()
            # Use rec_bc_001 which has "automatically ingest" in its claim
            rec = next(r for r in records if "automatically ingest" in r["claim"])
            rec = dict(rec)  # copy so we can mutate
            db.put_state_record(rec)

            # Confirm original phrase is found
            results_before = db.search_state_records(
                "automatically ingest", mode="exact"
            )
            ids_before = [r["record_id"] for r in results_before]
            self.assertIn(rec["record_id"], ids_before)

            # Replace with updated claim
            updated = dict(rec)
            updated["claim"] = "Updated claim: explicit write paths only after Slice C."
            db.put_state_record(updated)

            # Original phrase no longer matches
            results_after = db.search_state_records(
                "automatically ingest", mode="exact"
            )
            ids_after = [r["record_id"] for r in results_after]
            self.assertNotIn(rec["record_id"], ids_after)

            # New phrase now matches
            results_new = db.search_state_records(
                "explicit write paths only after Slice C", mode="exact"
            )
            ids_new = [r["record_id"] for r in results_new]
            self.assertIn(rec["record_id"], ids_new)
            db.close()

    # --- 13. inspect returns record plus source_refs ---

    def test_inspect_returns_record_plus_source_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            # Pick a record that has source_refs
            rec = next(r for r in records if r.get("source_refs"))
            results = db.inspect_state_records([rec["record_id"]], include_source_refs=True)
            self.assertEqual(len(results), 1)
            entry = results[0]
            self.assertEqual(entry["record_id"], rec["record_id"])
            self.assertIsNotNone(entry["record"])
            self.assertIsNone(entry["error"])
            # source_refs should be resolved SourceRef dicts
            self.assertIsInstance(entry["source_refs"], list)
            # All resolved source refs should have source_ref_id
            for sref in entry["source_refs"]:
                self.assertIn("source_ref_id", sref)
                self.assertIn("locator", sref)
            db.close()

    def test_inspect_without_source_refs(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            results = db.inspect_state_records(
                [records[0]["record_id"]], include_source_refs=False
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["source_refs"], [])
            db.close()

    # --- 14. inspect preserves requested order ---

    def test_inspect_preserves_requested_order(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            # Request in reverse order
            id1 = records[0]["record_id"]
            id2 = records[1]["record_id"]
            results = db.inspect_state_records([id2, id1])
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["record_id"], id2)
            self.assertEqual(results[1]["record_id"], id1)
            db.close()

    def test_inspect_single_record_returns_one_entry(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            results = db.inspect_state_records([records[2]["record_id"]])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["record"]["record_id"], records[2]["record_id"])
            db.close()

    # --- 15. inspect on unknown ID handles gracefully ---

    def test_inspect_unknown_id_returns_not_found_entry(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.inspect_state_records(["does_not_exist"])
            self.assertEqual(len(results), 1)
            entry = results[0]
            self.assertEqual(entry["record_id"], "does_not_exist")
            self.assertIsNone(entry["record"])
            self.assertEqual(entry["error"], "not found")
            self.assertEqual(entry["source_refs"], [])
            db.close()

    def test_inspect_mixed_known_unknown_ids(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            results = db.inspect_state_records([records[0]["record_id"], "missing_id"])
            self.assertEqual(len(results), 2)
            self.assertIsNotNone(results[0]["record"])
            self.assertIsNone(results[1]["record"])
            self.assertEqual(results[1]["error"], "not found")
            db.close()

    # --- 16. disabled DB search/inspect returns [] ---

    def test_disabled_db_search_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            self.assertEqual(db.search_state_records("anything"), [])
            self.assertFalse(Path(td, "state.sqlite3").exists())

    def test_disabled_db_inspect_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = BrainCaseDB(path=Path(td) / "state.sqlite3", enabled=False)
            self.assertEqual(db.inspect_state_records(["rec_bc_001"]), [])

    # --- 17. bad DB / search failure returns [] and sets last_error ---

    def test_search_on_closed_db_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            db.close()
            # After close, _conn is None; search should return [] gracefully
            results = db.search_state_records("braincase")
            # Either [] (can't connect) or degrades — must not raise
            self.assertIsInstance(results, list)

    # --- 18. no forbidden fields in search/inspect results ---

    def test_search_results_contain_no_forbidden_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("braincase", mode="tag", limit=20)
            for rec in results:
                for field in _FORBIDDEN_RECORD_FIELDS:
                    self.assertNotIn(field, rec,
                                     f"Forbidden field '{field}' found in search result")
            db.close()

    def test_inspect_results_contain_no_forbidden_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            results = db.inspect_state_records(
                [r["record_id"] for r in records[:3]], include_source_refs=True
            )
            for entry in results:
                if entry["record"] is not None:
                    for field in _FORBIDDEN_RECORD_FIELDS:
                        self.assertNotIn(field, entry["record"])
                for sref in entry.get("source_refs") or []:
                    for field in _FORBIDDEN_RECORD_FIELDS:
                        self.assertNotIn(field, sref)
            db.close()

    # --- 19. no model-facing RenderPacket creation ---

    def test_inspect_result_has_no_rendered_text(self):
        """inspect_state_records must not produce RenderPackets or rendered_text."""
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            records = _load_state_records()
            results = db.inspect_state_records(
                [r["record_id"] for r in records], include_source_refs=True
            )
            for entry in results:
                self.assertNotIn("rendered_text", entry)
                self.assertNotIn("packet_id", entry)
                self.assertNotIn("budget_tokens", entry)
                if entry["record"] is not None:
                    self.assertNotIn("rendered_text", entry["record"])
            db.close()

    def test_search_result_has_no_rendered_text(self):
        """search_state_records must not produce model-facing output."""
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            results = db.search_state_records("braincase", mode="tag", limit=20)
            for rec in results:
                self.assertNotIn("rendered_text", rec)
                self.assertNotIn("packet_id", rec)
            db.close()

    # --- FTS5 specific: init creates FTS table ---

    def test_init_creates_fts_table_if_available(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._fresh_db(td)
            db.close()
            if not db.fts_available:
                self.skipTest("FTS5 not available in this SQLite build")
            conn = sqlite3.connect(Path(td) / "state.sqlite3")
            try:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','shadow')"
                    ).fetchall()
                }
            finally:
                conn.close()
            self.assertIn("qz_braincase_state_records_fts", tables)

    def test_fts_search_finds_correct_record(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._populated_db(td)
            if not db.fts_available:
                self.skipTest("FTS5 not available in this SQLite build")
            # "gatekeeper" appears in rec_bc_005 claim (architecture correction episode)
            results = db.search_state_records("gatekeeper", mode="fts")
            ids = [r["record_id"] for r in results]
            self.assertIn("rec_bc_005", ids)
            db.close()


if __name__ == "__main__":
    unittest.main()
