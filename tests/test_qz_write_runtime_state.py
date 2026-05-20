"""Tests for scripts/qz-write-runtime-state — after #46 close-out.

The script no longer writes var/run/qz-runtime-state.json.
It now writes solely to OperationalStore (when QZ_OPERATIONAL_DB_ENABLED=1).
Disabled mode is a complete no-op.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Import the script as a module (no .py extension).
_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "qz-write-runtime-state"
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "qz_write_runtime_state", _SCRIPT_PATH, submodule_search_locations=[],
    )
    if _spec and _spec.loader:
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
    else:
        raise AttributeError
except (AttributeError, Exception):
    _mod = types.ModuleType("qz_write_runtime_state")
    _mod.__file__ = str(_SCRIPT_PATH)
    exec(compile(_SCRIPT_PATH.read_text(), str(_SCRIPT_PATH), "exec"), _mod.__dict__)

base_state = _mod.base_state
_write_to_store = _mod._write_to_store
_get_previous_model_from_store = _mod._get_previous_model_from_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proxy.qz_operational_store import OperationalStore


# -------------------------------------------------------------------------
# No JSON file written anymore
# -------------------------------------------------------------------------


class NoJsonWriteTests(unittest.TestCase):
    """Script no longer writes qz-runtime-state.json."""

    def test_disabled_store_creates_no_json_and_no_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "run" / "qz-runtime-state.json"
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "0",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                _write_to_store(state)
            self.assertFalse(json_path.exists(), "JSON must not be written")
            self.assertFalse(db_path.exists(), "DB must not be created when disabled")

    def test_enabled_store_creates_no_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "run" / "qz-runtime-state.json"
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("proxy-started", "launcher")
                _write_to_store(state)
            self.assertFalse(json_path.exists(), "JSON must not be written")
            self.assertTrue(db_path.exists(), "DB must be created when enabled")

    def test_no_json_even_with_qz_runtime_state_path_set(self):
        """QZ_RUNTIME_STATE_PATH is now a dead env var — script ignores it."""
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "custom-runtime-state.json"
            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "0",
                "QZ_RUNTIME_STATE_PATH": str(json_path),
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                state = base_state("requested", "env")
                _write_to_store(state)
            self.assertFalse(json_path.exists(), "JSON must not be written even with QZ_RUNTIME_STATE_PATH set")


# -------------------------------------------------------------------------
# OperationalStore writes (enabled mode)
# -------------------------------------------------------------------------


class EnabledStoreWriteTests(unittest.TestCase):
    """Enabled mode writes events and facts."""

    def _store(self, db_path: Path) -> OperationalStore:
        store = OperationalStore(path=db_path, enabled=True)
        store.init()
        return store

    def test_event_recorded_for_phase(self):
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
                _write_to_store(state)

            store = self._store(db_path)
            events = store.recent_events(event_type="backend-healthy")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["source"], "launcher")
            store.close()

    def test_all_standard_facts_written(self):
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
                _write_to_store(state)

            store = self._store(db_path)
            for key in ("current_phase", "requested_model", "effective_model",
                        "backend_status", "proxy_status"):
                self.assertIsNotNone(store.get_runtime_fact(key), f"missing: {key}")
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
                    _write_to_store(base_state(phase, "launcher"))

            store = self._store(db_path)
            events = store.recent_events(limit=10)
            phases = {e["event_type"] for e in events}
            self.assertIn("requested", phases)
            self.assertIn("backend-started", phases)
            self.assertIn("proxy-started", phases)
            store.close()


# -------------------------------------------------------------------------
# Model carryover via OperationalStore
# -------------------------------------------------------------------------


class ModelCarryoverTests(unittest.TestCase):
    """Effective model is carried forward from OperationalStore."""

    def test_previous_model_carried_from_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            # Seed a previous effective_model fact.
            store = OperationalStore(path=db_path, enabled=True)
            store.init()
            store.record_runtime_fact("effective_model", {"model": "prev-qwen"})
            store.close()

            env_patch = {
                "QZ_VAR_DIR": tmp,
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
                "QZ_MODEL_KEY": "",   # env has no model
            }
            with patch.dict(os.environ, env_patch, clear=False):
                model = _get_previous_model_from_store()
            self.assertEqual(model, "prev-qwen")

    def test_no_previous_model_when_store_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_patch = {
                "QZ_OPERATIONAL_DB_ENABLED": "0",
                "QZ_MODEL_KEY": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                model = _get_previous_model_from_store()
            self.assertEqual(model, "")


# -------------------------------------------------------------------------
# Non-fatal failure
# -------------------------------------------------------------------------


class NonFatalTests(unittest.TestCase):
    """Store failures must never raise."""

    def test_broken_store_cls_does_not_raise(self):
        state = base_state("requested", "env")
        with patch.object(_mod, "_STORE_CLS", side_effect=RuntimeError("boom")):
            try:
                _write_to_store(state)
            except Exception:
                self.fail("_write_to_store must not raise")

    def test_init_failure_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state" / "operational.sqlite3"
            env_patch = {
                "QZ_OPERATIONAL_DB_ENABLED": "1",
                "QZ_OPERATIONAL_DB_PATH": str(db_path),
            }
            state = base_state("requested", "env")
            with patch.dict(os.environ, env_patch, clear=False):
                with patch.object(OperationalStore, "init", return_value=False):
                    try:
                        _write_to_store(state)
                    except Exception:
                        self.fail("must not raise when init returns False")


if __name__ == "__main__":
    unittest.main()
