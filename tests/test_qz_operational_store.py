"""Tests for proxy/qz_operational_store.py — OperationalStore Slice B."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_operational_store import (
    SCHEMA_VERSION,
    OperationalStore,
    _default_db_path,
    _resolve_db_path,
)

# -------------------------------------------------------------------------
# Path resolution tests
# -------------------------------------------------------------------------


class PathResolutionTests(unittest.TestCase):
    """Tests that path helpers obey QZ_VAR_DIR and QZ_OPERATIONAL_DB_PATH."""

    def test_default_path_uses_qz_var_dir(self):
        env = {"QZ_VAR_DIR": "/custom/var"}
        path = _default_db_path(env)
        self.assertEqual(path, Path("/custom/var/state/operational.sqlite3"))

    def test_default_path_falls_back_to_qz_root(self):
        env = {"QZ_ROOT": "/custom/root"}
        path = _default_db_path(env)
        self.assertEqual(path, Path("/custom/root/var/state/operational.sqlite3"))

    def test_resolve_path_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = str(Path(tmp) / "my.sqlite3")
            env = {
                "QZ_OPERATIONAL_DB_PATH": override,
                "QZ_VAR_DIR": "/should/be/ignored",
            }
            path = _resolve_db_path(env)
            self.assertEqual(path, Path(override))

    def test_resolve_path_uses_var_dir_when_no_override(self):
        env = {"QZ_VAR_DIR": "/data/var"}
        path = _resolve_db_path(env)
        self.assertEqual(path, Path("/data/var/state/operational.sqlite3"))


# -------------------------------------------------------------------------
# Disabled no-op tests
# -------------------------------------------------------------------------


class DisabledNoOpTests(unittest.TestCase):
    """Disabled OperationalStore must be a complete no-op."""

    def test_disabled_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            store = OperationalStore(path=db_path, enabled=False)
            store.init()
            self.assertFalse(db_path.exists(), "DB file must not be created when disabled")

    def test_disabled_from_env_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            env = {"QZ_OPERATIONAL_DB_ENABLED": "0", "QZ_OPERATIONAL_DB_PATH": str(db_path)}
            store = OperationalStore.from_env(env)
            self.assertFalse(store.enabled)
            store.init()
            self.assertFalse(store.available)
            self.assertFalse(db_path.exists())

    def test_disabled_from_env_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            env = {"QZ_OPERATIONAL_DB_PATH": str(db_path)}
            store = OperationalStore.from_env(env)
            self.assertFalse(store.enabled)

    def test_disabled_record_startup_event_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            store = OperationalStore(path=db_path, enabled=False)
            store.init()
            store.record_startup_event("proxy_started", {"pid": 1})
            self.assertFalse(db_path.exists())

    def test_disabled_get_runtime_fact_returns_none(self):
        store = OperationalStore(path=Path("/nonexistent/op.sqlite3"), enabled=False)
        self.assertIsNone(store.get_runtime_fact("anything"))

    def test_disabled_recent_events_returns_empty(self):
        store = OperationalStore(path=Path("/nonexistent/op.sqlite3"), enabled=False)
        self.assertEqual(store.recent_events(), [])

    def test_enabled_from_env_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            env = {"QZ_OPERATIONAL_DB_ENABLED": "1", "QZ_OPERATIONAL_DB_PATH": str(db_path)}
            store = OperationalStore.from_env(env)
            self.assertTrue(store.enabled)


# -------------------------------------------------------------------------
# Schema tests
# -------------------------------------------------------------------------


class SchemaTests(unittest.TestCase):
    """Tests for schema creation and idempotence."""

    def _enabled_store(self, tmp: str) -> OperationalStore:
        db_path = Path(tmp) / "op.sqlite3"
        store = OperationalStore(path=db_path, enabled=True)
        self.assertTrue(store.init())
        return store

    def test_schema_created_on_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._enabled_store(tmp)
            self.assertTrue(store.available)
            h = store.health()
            self.assertEqual(h["schema_version"], SCHEMA_VERSION)
            store.close()

    def test_schema_created_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            s1 = OperationalStore(path=db_path, enabled=True)
            self.assertTrue(s1.init())
            s1.close()
            s2 = OperationalStore(path=db_path, enabled=True)
            self.assertTrue(s2.init(), "second open must succeed")
            h = s2.health()
            self.assertEqual(h["schema_version"], SCHEMA_VERSION)
            s2.close()

    def test_db_file_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            self.assertTrue(db_path.exists())
            store.close()

    def test_parent_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "dirs" / "op.sqlite3"
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            self.assertTrue(db_path.parent.is_dir())
            store.close()

    def test_health_returns_expected_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._enabled_store(tmp)
            h = store.health()
            for key in ("enabled", "path", "available", "schema_version", "last_error"):
                self.assertIn(key, h)
            store.close()


# -------------------------------------------------------------------------
# runtime_events tests
# -------------------------------------------------------------------------


class RuntimeEventsTests(unittest.TestCase):
    """Tests for record_startup_event and recent_events."""

    def _store(self, tmp: str) -> OperationalStore:
        db_path = Path(tmp) / "op.sqlite3"
        store = OperationalStore(path=db_path, enabled=True)
        store.init()
        return store

    def test_event_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_startup_event("proxy_started", {"pid": 42}, source="launcher")
            events = store.recent_events()
            self.assertEqual(len(events), 1)
            evt = events[0]
            self.assertEqual(evt["event_type"], "proxy_started")
            self.assertEqual(evt["source"], "launcher")
            self.assertEqual(evt["payload"]["pid"], 42)
            self.assertIn("ts_ms", evt)
            store.close()

    def test_multiple_events_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_startup_event("requested", {})
            store.record_startup_event("backend_started", {})
            store.record_startup_event("proxy_started", {})
            events = store.recent_events()
            self.assertEqual(len(events), 3)
            self.assertEqual(events[0]["event_type"], "proxy_started")
            self.assertEqual(events[2]["event_type"], "requested")
            store.close()

    def test_filter_by_event_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_startup_event("requested", {})
            store.record_startup_event("proxy_started", {})
            store.record_startup_event("proxy_started", {"second": True})
            proxy_events = store.recent_events(event_type="proxy_started")
            self.assertEqual(len(proxy_events), 2)
            for e in proxy_events:
                self.assertEqual(e["event_type"], "proxy_started")
            requested = store.recent_events(event_type="requested")
            self.assertEqual(len(requested), 1)
            store.close()

    def test_limit_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(10):
                store.record_startup_event("tick", {"i": i})
            events = store.recent_events(limit=3)
            self.assertEqual(len(events), 3)
            store.close()

    def test_empty_payload_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_startup_event("backend_healthy")
            events = store.recent_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"], {})
            store.close()

    def test_recent_events_empty_when_none_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(store.recent_events(), [])
            store.close()


# -------------------------------------------------------------------------
# runtime_facts tests
# -------------------------------------------------------------------------


class RuntimeFactsTests(unittest.TestCase):
    """Tests for record_runtime_fact and get_runtime_fact."""

    def _store(self, tmp: str) -> OperationalStore:
        db_path = Path(tmp) / "op.sqlite3"
        store = OperationalStore(path=db_path, enabled=True)
        store.init()
        return store

    def test_fact_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_runtime_fact("last_model", {"slug": "qwen"})
            fact = store.get_runtime_fact("last_model")
            self.assertIsNotNone(fact)
            self.assertEqual(fact["slug"], "qwen")
            store.close()

    def test_fact_upsert_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_runtime_fact("last_model", {"slug": "qwen"})
            store.record_runtime_fact("last_model", {"slug": "apex"})
            fact = store.get_runtime_fact("last_model")
            self.assertEqual(fact["slug"], "apex")
            store.close()

    def test_missing_fact_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertIsNone(store.get_runtime_fact("nonexistent_key"))
            store.close()

    def test_provenance_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            store.record_runtime_fact("proxy_pid", {"pid": 999}, provenance="proxy")
            assert store._conn is not None
            row = store._conn.execute(
                "SELECT provenance FROM runtime_facts WHERE key = ?", ("proxy_pid",)
            ).fetchone()
            self.assertEqual(row["provenance"], "proxy")
            store.close()

    def test_facts_independent_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.record_runtime_fact("a", {"v": 1})
            store.record_runtime_fact("b", {"v": 2})
            self.assertEqual(store.get_runtime_fact("a"), {"v": 1})
            self.assertEqual(store.get_runtime_fact("b"), {"v": 2})
            store.close()


# -------------------------------------------------------------------------
# Non-fatal failure test
# -------------------------------------------------------------------------


class NonFatalFailureTests(unittest.TestCase):
    """DB errors must not propagate to callers."""

    def test_write_to_closed_store_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            store.close()
            # Writing after close: available=False, _conn=None -> noop
            store.record_startup_event("proxy_started", {})
            store.record_runtime_fact("k", {"v": 1})
            # No exception raised

    def test_get_from_closed_store_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "op.sqlite3"
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            store.record_runtime_fact("k", {"v": 1})
            store.close()
            self.assertIsNone(store.get_runtime_fact("k"))


if __name__ == "__main__":
    unittest.main()
