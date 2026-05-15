import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from proxy.qz_braincase_db import (
    BrainCaseDB,
    QZ_BRAINCASE_DB_SCHEMA_VERSION,
    QZ_STATE_DB_ENABLED_ENV,
    QZ_STATE_DB_PATH_ENV,
)


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


if __name__ == "__main__":
    unittest.main()
