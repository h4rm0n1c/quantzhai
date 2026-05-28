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

    # ── Identity fields must also be preserved, not just source ──────────────

    def test_status_snapshot_does_not_overwrite_selection_identity(self):
        """status_snapshot must not overwrite selected_key/backend_id/label when canonical source exists.

        Root cause of poisoned state: the guard preserved selected_source but
        still overwrote selected_key and selected_backend_id with snapshot values.
        """
        self._write_state(
            selected_key="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_label="Qwen3.6 27B NEO",
            selected_source="operator",
            selected_reason="operator selected the real model",
        )
        router = _make_router(self._state_path)
        # Simulate a status probe that sees "default" as the catalog selection
        router._persist_model_state(
            {"key": "default.gguf", "backend_id": "default", "label": "Default"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        state = load_model_state(Path(self._state_path)).state
        self.assertEqual(state.selected_key,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
                         "status_snapshot must not overwrite selected_key with snapshot value")
        self.assertEqual(state.selected_backend_id,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
                         "status_snapshot must not overwrite selected_backend_id")
        self.assertEqual(state.selected_label, "Qwen3.6 27B NEO",
                         "status_snapshot must not overwrite selected_label")

    def test_status_snapshot_with_fallback_source_preserves_identity(self):
        """status_snapshot does not overwrite identity when existing source is 'fallback'."""
        self._write_state(
            selected_key="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_source="fallback",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "default.gguf", "backend_id": "default"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        state = load_model_state(Path(self._state_path)).state
        self.assertEqual(state.selected_key,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf")
        self.assertEqual(state.selected_backend_id,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS")

    def test_status_snapshot_on_non_canonical_state_still_writes_snapshot_identity(self):
        """status_snapshot may write identity when existing source is also non-canonical."""
        self._write_state(
            selected_key="default.gguf",
            selected_backend_id="default",
            selected_source="status_snapshot",  # non-canonical: guard does not fire
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "other.gguf", "backend_id": "other"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        state = load_model_state(Path(self._state_path)).state
        # No canonical source → identity is overwritten (expected behavior)
        self.assertEqual(state.selected_key, "other.gguf")


class ReconcileStatusStateLastGoodTests(unittest.TestCase):
    """Verify _reconcile_status_state records last_good_backend_id on confirmed healthy load."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = str(Path(self._tmp.name) / "model-state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_state(self, **kwargs):
        state = ModelState(**kwargs)
        write_model_state(state, Path(self._state_path))

    def _read_state(self) -> ModelState:
        return load_model_state(Path(self._state_path)).state

    def test_last_good_backend_id_recorded_when_backend_healthy(self):
        """_reconcile_status_state records last_good_backend_id when backend is confirmed loaded."""
        self._write_state(
            selected_key="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_source="operator",
            last_good_backend_id="",  # empty — no recovery point yet
        )
        router = _make_router(self._state_path)
        # Simulate confirmed-healthy backend state
        router._reconcile_status_state(
            selected={"key": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
                      "backend_id": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"},
            health_status=200,
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_context_length=262144,
            backend_context_length=262144,
            backend_state="loaded",
            loaded_model="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
        )
        state = self._read_state()
        self.assertEqual(state.last_good_backend_id,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
                         "last_good_backend_id must be recorded after confirmed healthy load")

    def test_last_good_not_overwritten_when_already_set(self):
        """_reconcile_status_state does not overwrite last_good_backend_id if already set."""
        self._write_state(
            selected_key="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_source="operator",
            last_good_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",  # already set
        )
        router = _make_router(self._state_path)
        router._reconcile_status_state(
            selected={"key": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
                      "backend_id": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"},
            health_status=200,
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_context_length=262144,
            backend_context_length=262144,
            backend_state="loaded",
            loaded_model="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
        )
        state = self._read_state()
        # Should remain unchanged (doesn't need to reload from scratch)
        self.assertEqual(state.last_good_backend_id,
                         "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS")

    def test_last_good_not_recorded_when_backend_not_loaded(self):
        """_reconcile_status_state does not record last_good when backend is not confirmed loaded."""
        self._write_state(
            selected_key="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_source="operator",
            last_good_backend_id="",
        )
        router = _make_router(self._state_path)
        router._reconcile_status_state(
            selected={"key": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf",
                      "backend_id": "Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS"},
            health_status=503,   # backend not healthy
            selected_backend_id="Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS",
            selected_context_length=262144,
            backend_context_length=262144,
            backend_state="starting",  # not loaded
            loaded_model="",
        )
        state = self._read_state()
        self.assertEqual(state.last_good_backend_id, "",
                         "last_good_backend_id must remain empty when backend is not loaded")


class PersistModelStateRecoveryMemoryTests(unittest.TestCase):
    """_persist_model_state must carry last_good_* and failed_candidate_* through ordinary writes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = str(Path(self._tmp.name) / "model-state.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _write_state(self, **kwargs):
        write_model_state(ModelState(**kwargs), Path(self._state_path))

    def _read_state(self) -> ModelState:
        return load_model_state(Path(self._state_path)).state

    def test_last_good_fields_preserved_across_normal_write(self):
        """An ordinary _persist_model_state call must not erase last_good_* fields."""
        self._write_state(
            selected_key="Qwen3.6.gguf",
            selected_backend_id="Qwen3.6",
            selected_source="operator",
            last_good_key="Qwen3.6.gguf",
            last_good_backend_id="Qwen3.6",
            last_good_label="Qwen3.6 27B",
            last_good_source="operator",
            last_good_loaded_at="2026-05-28T00:00:00Z",
        )
        router = _make_router(self._state_path)
        # Ordinary write — same model, new reason
        router._persist_model_state(
            {"key": "Qwen3.6.gguf", "backend_id": "Qwen3.6", "label": "Qwen3.6 27B"},
            reason="reloaded",
            source="operator",
        )
        state = self._read_state()
        self.assertEqual(state.last_good_backend_id, "Qwen3.6",
                         "last_good_backend_id must survive ordinary write")
        self.assertEqual(state.last_good_key, "Qwen3.6.gguf",
                         "last_good_key must survive ordinary write")
        self.assertEqual(state.last_good_label, "Qwen3.6 27B",
                         "last_good_label must survive ordinary write")
        self.assertEqual(state.last_good_source, "operator",
                         "last_good_source must survive ordinary write")
        self.assertEqual(state.last_good_loaded_at, "2026-05-28T00:00:00Z",
                         "last_good_loaded_at must survive ordinary write")

    def test_failed_candidate_fields_preserved_across_normal_write(self):
        """An ordinary _persist_model_state call must not erase failed_candidate_* fields."""
        self._write_state(
            selected_key="Qwen3.6.gguf",
            selected_backend_id="Qwen3.6",
            selected_source="fallback",
            last_good_backend_id="Qwen3.6",
            failed_candidate_key="BigModel.gguf",
            failed_candidate_backend_id="BigModel",
            failed_candidate_label="Big Model",
            failed_candidate_at="2026-05-28T00:01:00Z",
        )
        router = _make_router(self._state_path)
        router._persist_model_state(
            {"key": "Qwen3.6.gguf", "backend_id": "Qwen3.6"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        state = self._read_state()
        self.assertEqual(state.failed_candidate_backend_id, "BigModel",
                         "failed_candidate_backend_id must survive status_snapshot write")
        self.assertEqual(state.failed_candidate_key, "BigModel.gguf",
                         "failed_candidate_key must survive status_snapshot write")
        self.assertEqual(state.failed_candidate_label, "Big Model",
                         "failed_candidate_label must survive ordinary write")
        self.assertEqual(state.failed_candidate_at, "2026-05-28T00:01:00Z",
                         "failed_candidate_at must survive ordinary write")

    def test_status_snapshot_after_recovery_does_not_erase_last_good(self):
        """A status_snapshot write after self-heal recovery must not erase last_good_backend_id."""
        # Simulate state after self-heal: canonical source, last_good populated
        self._write_state(
            selected_key="Qwen3.6.gguf",
            selected_backend_id="Qwen3.6",
            selected_source="fallback",
            selected_reason="startup self-heal: last_loaded_model salvage",
            last_good_backend_id="Qwen3.6",
            last_good_key="Qwen3.6.gguf",
        )
        router = _make_router(self._state_path)
        # A status probe arrives and calls _persist_model_state with status_snapshot
        router._persist_model_state(
            {"key": "Qwen3.6.gguf", "backend_id": "Qwen3.6"},
            reason="status reconciliation",
            source="status_snapshot",
        )
        state = self._read_state()
        self.assertEqual(state.last_good_backend_id, "Qwen3.6",
                         "last_good_backend_id must not be erased by a post-recovery status_snapshot write")


if __name__ == "__main__":
    unittest.main()
