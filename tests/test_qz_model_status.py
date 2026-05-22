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
    def __init__(
        self,
        phase: str = "healthy",
        gpu_state: str = "gpu",
        loaded_id: str = "",
        backend_health_ok: bool | None = None,
        gpu_required: bool = True,
    ):
        # Default backend_health_ok to True for "healthy" phase, False otherwise
        if backend_health_ok is None:
            backend_health_ok = (phase == "healthy")
        self._snap = {
            "phase": phase,
            "backend_health_ok": backend_health_ok,
            "gpu_offload_state": gpu_state,
            "gpu_required": gpu_required,
            "launch_model_key": f"{loaded_id}.gguf" if loaded_id and not loaded_id.endswith(".gguf") else loaded_id,
            "launch_model_backend_id": loaded_id.replace(".gguf", "") if loaded_id else "",
            "launch_model_path_basename": f"{loaded_id}.gguf" if loaded_id and not loaded_id.endswith(".gguf") else loaded_id,
            "launch_model_error": None,
            "container_running": phase in ("running", "healthy"),
        }

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
        self.backend_manager = manager or _FakeBackendManager(loaded_id=loaded_id)

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
        self.assertTrue(payload["selected_model_ready"])
        self.assertEqual(payload["request_admission_state"], "ready")
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
        self.assertFalse(payload["selected_model_ready"])
        self.assertEqual(payload["request_admission_state"], "unavailable")
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
        self.assertFalse(payload["selected_model_ready"])

    # --- direct-mode effective loaded model ---

    def test_direct_healthy_selected_model_ready_true(self):
        """Direct healthy + launch matches selected + gpu=gpu => selected_model_ready."""
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
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="healthy", gpu_state="gpu", loaded_id="kuato"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertTrue(payload["selected_model_ready"])
        self.assertEqual(payload["request_admission_state"], "ready")
        self.assertEqual(payload["model_switch_state"], "loaded")
        self.assertIsNone(payload["recommended_action"])
        self.assertEqual(payload["backend_loaded_model_source"], "direct_launch")

    def test_direct_healthy_stale_failed_result_still_ready(self):
        """Direct healthy backend overrides stale last_load_result='failed'.

        In direct-m mode BackendManager health IS the load confirmation.  A
        historical failure record must not block a currently-healthy backend.
        """
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_source="operator",
                    last_load_result="failed",
                    last_load_error="previous start failed",
                    last_load_error_type="unknown",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="healthy", gpu_state="gpu", loaded_id="kuato"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertTrue(payload["selected_model_ready"],
                        "Healthy direct backend must override stale last_load_result=failed")
        self.assertEqual(payload["request_admission_state"], "ready")
        self.assertEqual(payload["model_switch_state"], "loaded")

    def test_direct_healthy_backend_loaded_model_populated(self):
        """Direct healthy backend populates backend_loaded_model from launch model."""
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato",
                           selected_source="operator"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="healthy", gpu_state="gpu", loaded_id="kuato"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertEqual(payload["backend_loaded_model"], "kuato")
        self.assertEqual(payload["backend_loaded_model_source"], "direct_launch")

    def test_direct_running_request_admission_loading(self):
        """Direct backend in running phase => request_admission_state loading."""
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato",
                           selected_source="operator"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="running", gpu_state="unknown",
                                            loaded_id="kuato", backend_health_ok=False),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertFalse(payload["selected_model_ready"])
        self.assertEqual(payload["request_admission_state"], "loading")

    def test_direct_running_recommended_action_says_loading_not_reload(self):
        """During backend loading, recommended_action must not say POST reload."""
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato",
                           selected_source="operator"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="running", gpu_state="unknown",
                                            loaded_id="kuato", backend_health_ok=False),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        action = payload["recommended_action"] or ""
        # The action must not positively instruct the operator to POST reload.
        # It may mention the endpoint name in a "do not" context.
        self.assertNotIn("POST /qz/model/reload to load", action,
                         "Must not positively instruct POST reload while backend is loading")
        self.assertIn("loading", action.lower(),
                      "Should mention loading state during active backend start")

    def test_direct_healthy_selected_differs_from_launch_mismatch(self):
        """Direct healthy + selected key differs from launch model => mismatch."""
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="other.gguf", selected_backend_id="other",
                           selected_source="operator"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato"), _entry("other.gguf", "other")],
                manager=_FakeBackendManager(phase="healthy", gpu_state="gpu", loaded_id="kuato"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertTrue(payload["selected_loaded_mismatch"])
        self.assertFalse(payload["selected_model_ready"])

    def test_direct_healthy_cpu_fallback_not_ready(self):
        """Direct healthy + gpu_required=True + cpu_fallback => not ready."""
        with self._tmp() as tmp:
            state_path = Path(tmp) / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato",
                           selected_source="operator"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                manager=_FakeBackendManager(phase="healthy", gpu_state="cpu_fallback",
                                            loaded_id="kuato"),
            )
            with patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
                payload = build_model_status(handler)
        self.assertFalse(payload["selected_model_ready"])
        self.assertEqual(payload["request_admission_state"], "failed_gpu_not_available")


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
