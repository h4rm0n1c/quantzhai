"""Tests for scripts/qz-write-runtime-state — Slice C dual-write.

Tests that:
- JSON file is always written (existing behaviour unchanged)
- Disabled OperationalStore creates no DB file
- Enabled OperationalStore receives runtime_events and runtime_facts rows
- DB failure does not prevent JSON write
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Import the script as a module (no .py extension requires explicit loader).
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "qz-write-runtime-state"
_spec = importlib.util.spec_from_file_location(
    "qz_write_runtime_state",
    _SCRIPT_PATH,
    submodule_search_locations=[],
)
if _spec is None or _spec.loader is None:
    # Fallback: read and exec directly
    import types as _types
    _mod = _types.ModuleType("qz_write_runtime_state")
    _mod.__file__ = str(_SCRIPT_PATH)
    exec(compile(_SCRIPT_PATH.read_text(), str(_SCRIPT_PATH), "exec"), _mod.__dict__)
else:
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

base_state = _mod.base_state
atomic_write_json = _mod.atomic_write_json
_dual_write_to_store = _mod._dual_write_to_store
runtime_path = _mod.runtime_path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proxy.qz_operational_store import OperationalStore


# -------------------------------------------------------------------------
# JSON write compatibility (existing behaviour)
# -------------------------------------------------------------------------


class JsonWriteTests(unittest.TestCase):
    """JSON is written regardless of OperationalStore state."""

    def test_json_written_when_store_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "run" / "qz-runtime-state.json"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "0",
                "QZ_MODEL_KEY": "",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                atomic_write_json(json_path, state)
                _dual_write_to_store(state)
            self.assertTrue(json_path.exists())
            data = json.loads(json_path.read_text())
            self.assertEqual(data["phase"], "requested")

    def test_json_written_when_store_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "run" / "qz-runtime-state.json"
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("proxy_started", "launcher")
                atomic_write_json(json_path, state)
                _dual_write_to_store(state)
            self.assertTrue(json_path.exists())
            data = json.loads(json_path.read_text())
            self.assertEqual(data["phase"], "proxy_started")

    def test_json_written_even_when_db_raises(self):
        """DB failure must never prevent JSON write."""
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "run" / "qz-runtime-state.json"
            state = base_state("backend-started", "env")
            # Write JSON first (as main() does)
            atomic_write_json(json_path, state)
            # Now simulate a crashing dual-write
            with patch.object(_mod, "_STORE_CLS", side_effect=Exception("db exploded")):
                try:
                    _dual_write_to_store(state)
                except Exception:
                    self.fail("_dual_write_to_store must not raise")
            # JSON must still be there
            self.assertTrue(json_path.exists())


# -------------------------------------------------------------------------
# Disabled mode
# -------------------------------------------------------------------------


class DisabledStoreTests(unittest.TestCase):
    """OperationalStore disabled → no DB file created."""

    def test_disabled_no_db_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "0",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                _dual_write_to_store(state)
            self.assertFalse(db_path.exists())

    def test_disabled_noop_when_env_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            # Remove the enabled var entirely
            cleaned = {k: v for k, v in os.environ.items()
                       if k != "QZ_OPERATIONAL_DB_ENABLED"}
            cleaned.update(env_patch)
            with patch.dict(os.environ, cleaned, clear=True):
                state = base_state("requested", "env")
                _dual_write_to_store(state)
            self.assertFalse(db_path.exists())


# -------------------------------------------------------------------------
# Enabled mode — events
# -------------------------------------------------------------------------


class EnabledStoreEventTests(unittest.TestCase):
    """Enabled mode writes runtime_events."""

    def _store_from_env(self, tmp: str, db_path: Path) -> OperationalStore:
        env = {
            "QZ_OPERATIONAL_DB_ENABLED": "1",
            "QZ_OPERATIONAL_DB_PATH": str(db_path),
        }
        store = OperationalStore.from_env(env)
        store.init()
        return store

    def test_event_written_for_requested_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                _dual_write_to_store(state)

            store = self._store_from_env(tmp, db_path)
            events = store.recent_events(event_type="requested")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "requested")
            self.assertEqual(events[0]["source"], "launcher")
            self.assertEqual(events[0]["payload"]["phase"], "requested")
            store.close()

    def test_event_written_for_proxy_started_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "qwen",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("proxy-started", "launcher")
                _dual_write_to_store(state)

            store = self._store_from_env(tmp, db_path)
            events = store.recent_events(event_type="proxy-started")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["payload"]["phase"], "proxy-started")
            store.close()

    def test_multiple_phases_accumulate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                for phase in ("requested", "backend-started", "proxy-started"):
                    _dual_write_to_store(base_state(phase, "launcher"))

            store = self._store_from_env(tmp, db_path)
            events = store.recent_events(limit=10)
            phases = {e["event_type"] for e in events}
            self.assertIn("requested", phases)
            self.assertIn("backend-started", phases)
            self.assertIn("proxy-started", phases)
            store.close()


# -------------------------------------------------------------------------
# Enabled mode — facts
# -------------------------------------------------------------------------


class EnabledStoreFactTests(unittest.TestCase):
    """Enabled mode writes runtime_facts."""

    def _store_from_env(self, db_path: Path) -> OperationalStore:
        store = OperationalStore(path=db_path, enabled=True)
        store.init()
        return store

    def test_current_phase_fact_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "qwen",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("backend-healthy", "launcher")
                _dual_write_to_store(state)

            store = self._store_from_env(db_path)
            fact = store.get_runtime_fact("current_phase")
            self.assertIsNotNone(fact)
            self.assertEqual(fact["phase"], "backend-healthy")
            store.close()

    def test_effective_model_fact_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "apex-qwen",
                "QZ_PROFILE": "apex",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("proxy-started", "launcher")
                _dual_write_to_store(state)

            store = self._store_from_env(db_path)
            fact = store.get_runtime_fact("effective_model")
            self.assertIsNotNone(fact)
            self.assertIn("model", fact)
            store.close()

    def test_backend_status_fact_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("backend-healthy", "launcher")
                state["backend"]["healthy"] = True
                state["backend"]["loaded_model"] = "qwen.gguf"
                _dual_write_to_store(state)

            store = self._store_from_env(db_path)
            fact = store.get_runtime_fact("backend_status")
            self.assertIsNotNone(fact)
            self.assertIn("healthy", fact)
            self.assertTrue(fact["healthy"])
            store.close()

    def test_all_standard_facts_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
                "QZ_PROFILE": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                _dual_write_to_store(state)

            store = self._store_from_env(db_path)
            for key in ("current_phase", "requested_model", "effective_model",
                        "backend_status", "proxy_status"):
                self.assertIsNotNone(store.get_runtime_fact(key), f"missing fact: {key}")
            store.close()


# -------------------------------------------------------------------------
# Non-fatal failure
# -------------------------------------------------------------------------


class NonFatalDualWriteTests(unittest.TestCase):
    """DB failure must not raise and must not affect JSON write."""

    def test_broken_store_cls_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = base_state("requested", "env")
            with patch.object(_mod, "_STORE_CLS", side_effect=RuntimeError("boom")):
                try:
                    _dual_write_to_store(state)
                except Exception:
                    self.fail("must not raise")

    def test_store_init_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
            }
            state = base_state("requested", "env")
            # Give an unwritable path
            with patch.dict(os.environ, env_patch, clear=False):
                with patch.object(OperationalStore, "init", return_value=False):
                    try:
                        _dual_write_to_store(state)
                    except Exception:
                        self.fail("must not raise when init returns False")


if __name__ == "__main__":
    unittest.main()
