"""Tests for proxy/qz_model_status.py — Slice C status builder."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proxy.qz_model_state import (
    SCHEMA as MODEL_STATE_SCHEMA,
    ModelState,
    write_model_state,
)
from proxy.qz_model_status import (
    QZ_MODEL_STATUS_SCHEMA,
    build_model_status,
    identity_matches_loaded,
    model_selection_hints,
)


# ---------------------------------------------------------------------------
# Fake handler helpers — minimal surface needed by build_model_status
# ---------------------------------------------------------------------------

class _FakeCatalog:
    def __init__(self, entries):
        self.entries = list(entries or [])


class _FakeBackendModels:
    def __init__(self, loaded_id: str = ""):
        self._loaded_id = loaded_id

    def backend_models(self):
        if not self._loaded_id:
            return {}
        return {self._loaded_id: {"state": "loaded"}}

    def loaded_backend_models(self, backend_models):
        if not self._loaded_id:
            return []
        return [{"id": self._loaded_id, "state": "loaded"}]


class _FakeBackendManager:
    def __init__(self, phase: str = "healthy", gpu_state: str = "gpu"):
        self._snap = {"phase": phase, "gpu_offload_state": gpu_state}

    def snapshot(self):
        return self._snap


class _FakeHandler:
    def __init__(
        self,
        *,
        state_path: Path,
        entries=None,
        loaded_id: str = "",
        manager: _FakeBackendManager | None = None,
    ):
        self.model_state_path = str(state_path)
        self._catalog = _FakeCatalog(entries or [])
        self._backend = _FakeBackendModels(loaded_id)
        self.backend_manager = manager

    def _model_catalog(self):
        return self._catalog

    def _model_router(self):
        return self._backend


def _entry(key: str, backend_id: str = "", label: str = "", profile_valid: bool = True) -> dict:
    return {
        "key": key,
        "slug": key,
        "filename": key,
        "stem": key.split(".")[0],
        "backend_id": backend_id or key,
        "label": label or key,
        "profile_valid": profile_valid,
    }


# ---------------------------------------------------------------------------
# build_model_status
# ---------------------------------------------------------------------------

class BuildModelStatusTests(unittest.TestCase):

    def _tmp(self) -> tempfile.TemporaryDirectory:
        return tempfile.TemporaryDirectory()

    def test_schema_and_ok_true(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            handler = _FakeHandler(state_path=state_path)
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["schema"], QZ_MODEL_STATUS_SCHEMA)
        self.assertTrue(payload["ok"])

    def test_no_selection_recommends_select(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            handler = _FakeHandler(state_path=state_path)
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["selected_key"], "")
        self.assertEqual(payload["selected_backend_id"], "")
        self.assertIn("POST /qz/model/select", payload["recommended_action"])

    def test_selected_and_loaded_match_no_mismatch(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_label="Kuato",
                    selected_source="operator",
                    selected_at="2026-05-21T10:00:00Z",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato", "Kuato")],
                loaded_id="kuato",
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["selected_key"], "kuato.gguf")
        self.assertEqual(payload["selected_backend_id"], "kuato")
        self.assertEqual(payload["backend_loaded_model"], "kuato")
        self.assertFalse(payload["selected_loaded_mismatch"])
        self.assertTrue(payload["model_visible"])
        self.assertTrue(payload["profile_valid"])
        self.assertIsNone(payload["recommended_action"])

    def test_selected_loaded_mismatch(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_source="operator",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato"), _entry("other.gguf", "other")],
                loaded_id="other",
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertTrue(payload["selected_loaded_mismatch"])
        self.assertIn("differs", payload["recommended_action"])
        self.assertIn("/qz/model/reload", payload["recommended_action"])

    def test_configured_env_model_independent_of_selected(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_source="operator",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato"), _entry("envseed.gguf", "envseed")],
                loaded_id="kuato",
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": "envseed.gguf"}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["configured_env_model"], "envseed.gguf")
        self.assertEqual(payload["selected_key"], "kuato.gguf")

    def test_invalid_profile_reported(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="bad.gguf", selected_backend_id="bad"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("bad.gguf", "bad", profile_valid=False)],
                loaded_id="",
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertTrue(payload["model_visible"])
        self.assertFalse(payload["profile_valid"])

    def test_insufficient_vram_recommendation(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_source="operator",
                    last_load_result="failed",
                    last_load_error="cudaMalloc failed",
                    last_load_error_type="insufficient_vram",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                loaded_id="",
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["last_load_error_type"], "insufficient_vram")
        self.assertIn("VRAM", payload["recommended_action"])

    def test_backend_phase_and_gpu_state_surface(self):
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                manager=_FakeBackendManager(phase="healthy", gpu_state="gpu"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["backend_phase"], "healthy")
        self.assertEqual(payload["backend_gpu_state"], "gpu")


# ---------------------------------------------------------------------------
# identity_matches_loaded
# ---------------------------------------------------------------------------

class IdentityMatchesLoadedTests(unittest.TestCase):

    def test_matches_selected_key(self):
        self.assertTrue(identity_matches_loaded("kuato.gguf", "kuato", "kuato.gguf"))

    def test_matches_selected_backend_id(self):
        self.assertTrue(identity_matches_loaded("kuato.gguf", "kuato", "kuato"))

    def test_no_match(self):
        self.assertFalse(identity_matches_loaded("kuato.gguf", "kuato", "other"))

    def test_empty_loaded_returns_false(self):
        self.assertFalse(identity_matches_loaded("kuato.gguf", "kuato", ""))


# ---------------------------------------------------------------------------
# model_selection_hints
# ---------------------------------------------------------------------------

class ModelSelectionHintsTests(unittest.TestCase):

    def test_env_key_differs_from_selected(self):
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        hints = model_selection_hints(state, configured_env_model="envseed", backend_loaded_model="")
        self.assertTrue(any("QZ_MODEL_KEY" in h for h in hints))

    def test_env_key_matches_selected_no_hint(self):
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        hints = model_selection_hints(state, configured_env_model="kuato.gguf", backend_loaded_model="kuato.gguf")
        self.assertFalse(any("QZ_MODEL_KEY" in h for h in hints))

    def test_env_key_matches_backend_alias_no_hint(self):
        """env model that matches the backend_id alias should not produce a hint."""
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        hints = model_selection_hints(state, configured_env_model="kuato", backend_loaded_model="kuato")
        self.assertFalse(any("QZ_MODEL_KEY" in h for h in hints))

    def test_mismatch_emits_hint(self):
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        hints = model_selection_hints(state, configured_env_model="", backend_loaded_model="other")
        self.assertTrue(any("differs from loaded" in h for h in hints))

    def test_no_mismatch_no_hint(self):
        state = ModelState(selected_key="kuato.gguf", selected_backend_id="kuato")
        hints = model_selection_hints(state, configured_env_model="", backend_loaded_model="kuato")
        self.assertFalse(any("differs from loaded" in h for h in hints))

    def test_insufficient_vram_hint(self):
        state = ModelState(
            selected_key="kuato.gguf",
            selected_backend_id="kuato",
            last_load_error_type="insufficient_vram",
        )
        hints = model_selection_hints(state, configured_env_model="", backend_loaded_model="")
        self.assertTrue(any("VRAM" in h for h in hints))


if __name__ == "__main__":
    unittest.main()
