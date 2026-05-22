"""Tests for qz_model_router._persist_model_state status_snapshot non-authority rule."""

import json
import tempfile
import unittest
from pathlib import Path

from proxy.qz_model_router import ModelRouter
from proxy.qz_model_state import (
    ModelState,
    load_model_state,
    write_model_state,
)


class _FakeHandler:
    """Minimal handler that ModelRouter can use to locate model_state_path."""
    model_state_path: str = ""


def _make_router(state_path: str) -> ModelRouter:
    """Return a minimal ModelRouter pointing to state_path."""
    h = _FakeHandler()
    h.__class__ = type("FakeHandlerCls", (_FakeHandler,), {"model_state_path": state_path})
    return ModelRouter(h)


class PersistModelStateSourceTests(unittest.TestCase):
    """Verify _persist_model_state never overwrites canonical source with status_snapshot."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = str(Path(self._tmp.name) / "model-state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_state(self, **kwargs):
        state = ModelState(**kwargs)
        write_model_state(state, Path(self._state_path))

    def _read_source(self) -> str:
        return load_model_state(Path(self._state_path)).state.selected_source

    def test_status_snapshot_does_not_overwrite_operator_source(self):
        """status_snapshot write must preserve operator source (same backend_id)."""
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="operator",
            selected_reason="user explicitly chose this",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato", "label": "Kuato"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        self.assertEqual(self._read_source(), "operator",
                         "operator source must survive a status_snapshot write")

    def test_status_snapshot_does_not_overwrite_fallback_source(self):
        """status_snapshot write must preserve fallback source."""
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="fallback",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato", "label": "Kuato"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        self.assertEqual(self._read_source(), "fallback",
                         "fallback source must survive a status_snapshot write")

    def test_status_snapshot_does_not_overwrite_qz_codex_source(self):
        """status_snapshot write must preserve qz_codex source."""
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="qz_codex",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        self.assertEqual(self._read_source(), "qz_codex")

    def test_operator_write_overrides_status_snapshot(self):
        """An explicit operator write must overwrite status_snapshot source."""
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="status_snapshot",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato"},
            reason="user selected",
            source="operator",
        )
        self.assertEqual(self._read_source(), "operator",
                         "operator write must overwrite status_snapshot")

    def test_status_snapshot_write_when_no_existing_canonical_source(self):
        """status_snapshot write on empty/non-canonical state writes status_snapshot."""
        # State has status_snapshot existing source — should still write (no canonical to preserve)
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="status_snapshot",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        # status_snapshot can persist when existing is also non-canonical
        self.assertEqual(self._read_source(), "status_snapshot")

    def test_persisted_operator_selection_remains_after_status_snapshot_different_backend_id(self):
        """status_snapshot must preserve operator source even when backend_id differs.

        In direct mode the router may observe a different backend representation
        than what was originally selected. The operator's choice must survive.
        """
        self._write_state(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            selected_source="operator",
        )
        router = _make_router(self._state_path)
        # Simulate router seeing a different backend_id string for same model
        router._persist_model_state(
            {"key": "kuato.gguf", "backend_id": "kuato-alias"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        self.assertEqual(self._read_source(), "operator",
                         "operator source must survive even when backend_id differs in status_snapshot write")


if __name__ == "__main__":
    unittest.main()
