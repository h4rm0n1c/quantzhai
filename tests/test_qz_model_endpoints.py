"""Tests for /qz/model/* endpoints — Slice C.

Endpoints are added in proxy/qz_request_router.py and dispatch through:
  GET  /qz/model/status
  POST /qz/model/select
  POST /qz/model/reload
  POST /qz/model/select-and-restart

These tests exercise the RequestRouter handler methods directly with a
fake ProxyHandler — the HTTP layer itself is thin and is exercised by the
existing /qz/backend/* and /qz/control-plane integration tests.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from proxy.qz_model_state import (
    SCHEMA as MODEL_STATE_SCHEMA,
    ModelState,
    load_model_state,
    write_model_state,
)
from proxy.qz_model_status import QZ_MODEL_STATUS_SCHEMA
from proxy.qz_request_router import RequestRouter


# ---------------------------------------------------------------------------
# Fake handler scaffolding
# ---------------------------------------------------------------------------

class _FakeCatalog:
    def __init__(self, entries):
        self.entries = list(entries or [])
        self.selected = None

    def select(self, query=None):
        # Lightweight stub for catalog.select() — Slice C calls it to keep the
        # in-memory selected pointer aligned with the persisted selection.
        if query:
            from proxy.qz_model_catalog import match_model
            matched = match_model(self.entries, query)
            if matched is not None:
                self.selected = matched
        return self.selected, "test stub"


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


class _FakeHandler:
    """Minimum surface needed by the /qz/model/* handler methods."""

    def __init__(
        self,
        *,
        state_path: Path,
        entries=None,
        loaded_id: str = "",
        resolve_outcome=None,
        body: dict | None = None,
        path: str = "/qz/model/select",
        proxy_ready: bool = True,
        catalog_ready: bool = True,
    ):
        self.path = path
        self.model_state_path = str(state_path)
        self._catalog = _FakeCatalog(entries or [])
        self._backend = _FakeBackendModels(loaded_id)
        self._resolve_outcome = resolve_outcome
        self.backend_manager = None
        self.headers = _FakeHeaders(body)
        self.rfile = io.BytesIO(json.dumps(body or {}).encode("utf-8"))
        self.sent: list[tuple[int, dict]] = []
        self._proxy_ready = proxy_ready
        self._catalog_ready = catalog_ready
        class _Telemetry:
            def emit(self, *_args, **_kwargs):
                return None
        self.telemetry = _Telemetry()

    # --- proxy contract ---

    def _send_json(self, status_code, payload):
        self.sent.append((status_code, payload))

    def _model_catalog(self):
        return self._catalog

    def _model_router(self):
        return self._backend

    def _resolve_model_selection(self, requested):
        if self._resolve_outcome is None:
            # Default: succeed and return a stand-in selection dict
            return {"key": requested, "backend_id": requested}, "test reload"
        return self._resolve_outcome

    def _initialization_payload(self):
        return {
            "state": "ready" if self._proxy_ready else "initializing",
            "ready": self._proxy_ready,
            "catalog_ready": self._catalog_ready,
        }

    def _handle_ollama_post(self):
        return False

    def _mark_deprecated_endpoint(self, path):
        return None


class _FakeHeaders:
    """Minimal mapping-ish header object used by the handler."""

    def __init__(self, body: dict | None):
        self._body = body
        self._len = len(json.dumps(body or {}).encode("utf-8")) if body is not None else 0

    def get(self, key, default=None):
        if key == "Content-Length":
            return str(self._len)
        return default


@contextmanager
def _tmp_state_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _entry(key: str, backend_id: str = "", profile_valid: bool = True) -> dict:
    return {
        "key": key,
        "slug": key,
        "filename": key,
        "stem": key.split(".")[0],
        "backend_id": backend_id or key,
        "label": key,
        "profile_valid": profile_valid,
        "runtime_context_length": 131072,
    }


# ---------------------------------------------------------------------------
# POST /qz/model/select
# ---------------------------------------------------------------------------

class SelectEndpointTests(unittest.TestCase):

    def test_validates_unknown_model(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf")],
                body={"model": "does-not-exist"},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
        self.assertEqual(len(handler.sent), 1)
        status, body = handler.sent[0]
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertIn("not found", body["error"])

    def test_rejects_invalid_profile(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("bad.gguf", profile_valid=False)],
                body={"model": "bad.gguf"},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
        status, body = handler.sent[0]
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertIn("profile invalid", body["error"])

    def test_rejects_missing_model_field(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf")],
                body={},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
        status, body = handler.sent[0]
        self.assertEqual(status, 400)
        self.assertIn("missing", body["error"])

    def test_rejects_unknown_source(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf")],
                body={"model": "kuato.gguf", "source": "bogus"},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
        status, body = handler.sent[0]
        self.assertEqual(status, 400)
        self.assertIn("selected_source", body["error"])

    def test_writes_qz_model_state_v1_with_operator_source(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                path="/qz/model/select",
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], MODEL_STATE_SCHEMA)
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            self.assertEqual(payload["selected_backend_id"], "kuato")
            self.assertEqual(payload["selected_source"], "operator")
            self.assertIn("/qz/model/select", payload["selected_reason"])
            self.assertNotIn("/qz/model/select-and-restart", payload["selected_reason"])
            status, body = handler.sent[0]
            self.assertEqual(status, 200)
            self.assertEqual(body["schema"], QZ_MODEL_STATUS_SCHEMA)
            self.assertEqual(body["selected_key"], "kuato.gguf")

    def test_select_does_not_invoke_backend_reload(self):
        """POST /qz/model/select must not call _resolve_model_selection."""
        called = []

        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf")],
                body={"model": "kuato.gguf"},
            )

            def _resolve(requested):
                called.append(requested)
                return None, "should not be called"

            handler._resolve_model_selection = _resolve
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
        self.assertEqual(called, [])

    def test_select_preserves_observation_fields(self):
        """Selection write must not clobber last_load_* observation fields."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            # Seed with observation fields
            write_model_state(
                ModelState(
                    selected_key="old.gguf",
                    selected_backend_id="old",
                    last_load_result="failed",
                    last_load_error="cudaMalloc failed",
                    last_load_error_type="insufficient_vram",
                    last_loaded_model="old",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
            loaded = load_model_state(state_path).state
            self.assertEqual(loaded.selected_key, "kuato.gguf")
            self.assertEqual(loaded.last_load_result, "failed")
            self.assertEqual(loaded.last_load_error_type, "insufficient_vram")
            self.assertEqual(loaded.last_loaded_model, "old")

    def test_select_and_restart_preserves_operator_source(self):
        """Select-and-restart must not downgrade selected_source."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            resolve_called: list[str] = []

            def _resolve(requested):
                resolve_called.append(requested)
                return {"key": "kuato.gguf", "backend_id": "kuato"}, "operator path"

            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="kuato",
            )
            handler._resolve_model_selection = _resolve
            handler.backend_manager = _FakeBackendManager("update_slots: all slots are idle")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_source"], "operator")
            self.assertEqual(resolve_called, [])

    def test_select_accepts_qz_codex_source(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf")],
                body={"model": "kuato.gguf", "source": "qz_codex"},
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=False)
            loaded = load_model_state(state_path).state
            self.assertEqual(loaded.selected_source, "qz_codex")


# ---------------------------------------------------------------------------
# POST /qz/model/reload
# ---------------------------------------------------------------------------

class ReloadEndpointTests(unittest.TestCase):

    def test_409_when_no_selection(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(state_path=state_path, entries=[_entry("kuato.gguf")])
            RequestRouter(handler)._handle_model_reload_endpoint()
        status, body = handler.sent[0]
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])
        self.assertIn("no selected model", body["error"])

    def test_404_when_selection_not_in_catalog(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            write_model_state(ModelState(selected_key="gone.gguf"), state_path)
            handler = _FakeHandler(state_path=state_path, entries=[_entry("kuato.gguf")])
            RequestRouter(handler)._handle_model_reload_endpoint()
        status, body = handler.sent[0]
        self.assertEqual(status, 404)
        self.assertIn("not found", body["error"])

    def test_restarts_manager_with_selected_identity(self):
        called = []
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                loaded_id="kuato",
            )

            def _resolve(requested):
                called.append(requested)
                return {"key": requested, "backend_id": requested}, "test reload"

            handler._resolve_model_selection = _resolve
            handler.backend_manager = _FakeBackendManager("update_slots: all slots are idle")
            RequestRouter(handler)._handle_model_reload_endpoint()
        self.assertEqual(called, [])
        self.assertEqual(handler.backend_manager.restart_calls, 0)
        self.assertEqual(handler.backend_manager.load_model_http_calls, ["kuato.gguf"])
        self.assertEqual(handler.backend_manager.set_launch_model_calls[0]["path_basename"], "kuato.gguf")
        status, body = handler.sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], QZ_MODEL_STATUS_SCHEMA)


# ---------------------------------------------------------------------------
# POST /qz/model/select-and-restart
# ---------------------------------------------------------------------------

class SelectAndRestartEndpointTests(unittest.TestCase):

    def test_validates_then_restarts_manager(self):
        called = []
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                path="/qz/model/select-and-restart",
                loaded_id="kuato",
            )

            def _resolve(requested):
                called.append(requested)
                return {"key": requested, "backend_id": requested}, "select-and-restart"

            handler._resolve_model_selection = _resolve
            handler.backend_manager = _FakeBackendManager("update_slots: all slots are idle")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            self.assertEqual(called, [])
            self.assertEqual(handler.backend_manager.restart_calls, 0)
            self.assertEqual(handler.backend_manager.load_model_http_calls, ["kuato.gguf"])
            self.assertEqual(handler.backend_manager.set_launch_model_calls[0]["backend_id"], "kuato")
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            status, body = handler.sent[0]
            self.assertEqual(status, 200)

    def test_reload_failure_returns_409_with_classified_state(self):
        """Slice E: reload failure returns 409 Conflict and updates state.

        The request was valid (model exists in catalog) but the selected
        model cannot become the active backend model, so the response is
        409 Conflict with last_load_* observation fields populated.
        """
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
            )
            handler.backend_manager = _FakeBackendManager(
                "OOM during context allocation",
                post_restart_phase="failed",
                post_restart_error="OOM during context allocation",
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["ok"])
            self.assertIn("OOM", body["error"])
            # State preserved + observation updated (no backend_manager attached
            # so no log classification — falls through to "unknown" type).
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            self.assertEqual(payload["selected_backend_id"], "kuato")
            self.assertEqual(payload["last_load_result"], "failed")
            self.assertIn("OOM", payload["last_load_error"])
            self.assertEqual(payload["last_load_error_type"], "unknown")


# ---------------------------------------------------------------------------
# Slice E — load-failure classification integrated with endpoints
# ---------------------------------------------------------------------------

class _FakeBackendManager:
    """Minimal direct-mode stand-in returning canned recent container logs."""

    def __init__(
        self,
        logs: str | None,
        *,
        backend_model_mode: str = "direct",
        restart_result: dict | None = None,
        post_restart_phase: str = "healthy",
        post_restart_error: str = "",
    ):
        self._logs = logs
        self.backend_model_mode = backend_model_mode
        self._restart_result = restart_result if restart_result is not None else {"ok": True}
        self._post_phase = post_restart_phase
        self._post_error = post_restart_error
        self.set_launch_model_calls: list[dict] = []
        self.restart_calls: int = 0
        self.start_calls: int = 0
        self.load_model_http_calls: list[str] = []
        self._snapshot: dict = {
            "phase": "healthy",
            "backend_health_ok": True,
            "backend_model_mode": backend_model_mode,
            "launch_model_key": "",
            "launch_model_backend_id": "",
            "launch_model_path_basename": "",
            "launch_model_error": None,
            "gpu_offload_state": "gpu",
            "last_error": None,
        }

    @property
    def phase(self):
        return self._snapshot.get("phase")

    def fetch_recent_logs(self, tail=None):
        return self._logs

    def set_launch_model(self, *, key, backend_id, path_basename):
        self.set_launch_model_calls.append({
            "key": key,
            "backend_id": backend_id,
            "path_basename": path_basename,
        })
        self._snapshot["launch_model_key"] = key
        self._snapshot["launch_model_backend_id"] = backend_id
        self._snapshot["launch_model_path_basename"] = path_basename

    def start(self):
        self.start_calls += 1
        self._snapshot["phase"] = self._post_phase
        self._snapshot["backend_health_ok"] = self._post_phase == "healthy"
        if self._post_error:
            self._snapshot["last_error"] = self._post_error
        return self._restart_result

    def restart(self):
        self.restart_calls += 1
        # Simulate the lifecycle landing on the post-restart phase
        # immediately so the endpoint's wait-for-healthy loop sees it.
        self._snapshot["phase"] = self._post_phase
        if self._post_error:
            self._snapshot["last_error"] = self._post_error
        return self._restart_result

    def load_model_http(self, model_basename):
        self.load_model_http_calls.append(model_basename)
        self._snapshot["phase"] = self._post_phase
        self._snapshot["backend_health_ok"] = self._post_phase == "healthy"
        if self._post_error:
            self._snapshot["last_error"] = self._post_error
        return self._restart_result

    def get_models_status(self):
        basename = (self._snapshot.get("launch_model_path_basename") or "").strip()
        if self._snapshot.get("phase") != "healthy" or not basename:
            return {"data": []}
        return {"data": [{"id": f"/models/{basename}", "status": {"value": "loaded"}}]}

    def snapshot(self):
        return dict(self._snapshot)


class LoadFailureClassificationTests(unittest.TestCase):

    def test_insufficient_vram_logs_classify_select_and_restart(self):
        """Logs with cudaMalloc → 409 with last_load_error_type=insufficient_vram."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="",  # backend never finished loading
                # resolve "succeeds" but logs show VRAM failure
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager(
                "\n".join([
                    "load_tensors: offloaded 49/49 layers to GPU",
                    "cudaMalloc failed: out of memory",
                    "llama_init_from_model: failed to initialize the context",
                ])
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["ok"])
            self.assertEqual(body["last_load_result"], "failed")
            self.assertEqual(body["last_load_error_type"], "insufficient_vram")
            self.assertIn("cudaMalloc", body["last_load_error"])
            # recommended_action mentions smaller model / reduce QZ_CONTEXT
            self.assertIn("VRAM", body["recommended_action"])
            # selection_key/selection_backend_id preserved
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            self.assertEqual(payload["selected_backend_id"], "kuato")
            self.assertEqual(payload["last_load_error_type"], "insufficient_vram")

    def test_context_creation_failed_classification(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="",
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager(
                "common_init_from_params: failed to create context with model"
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertEqual(body["last_load_error_type"], "context_creation_failed")

    def test_reload_failure_preserves_selection_authority(self):
        """A failed reload must not clear selected_key/selected_backend_id."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager("cudaMalloc failed: OOM")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            self.assertEqual(payload["selected_backend_id"], "kuato")
            self.assertEqual(payload["selected_source"], "operator")

    def test_successful_reload_clears_previous_load_error(self):
        """After a successful reload, last_load_* observation fields are cleared."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            # Seed with a prior failure observation
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    last_load_result="failed",
                    last_load_error="cudaMalloc failed",
                    last_load_error_type="insufficient_vram",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="kuato",
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            # Logs are clean — no failure patterns
            handler.backend_manager = _FakeBackendManager(
                "INFO: server listening on 0.0.0.0:8080"
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 200)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["last_load_result"], "loaded")
            self.assertIsNone(payload["last_load_error"])
            self.assertIsNone(payload["last_load_error_type"])
            self.assertEqual(payload["last_loaded_model"], "kuato")

    def test_select_and_restart_rolls_back_to_last_good_on_failure(self):
        """Default rollback_on_failure=True: failure rolls selected_* back to last_good_*."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            # Seed state with a previously-confirmed last_good (kuato)
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_label="Kuato",
                    selected_source="operator",
                    last_good_key="kuato.gguf",
                    last_good_backend_id="kuato",
                    last_good_label="Kuato",
                    last_good_source="operator",
                    last_load_result="loaded",
                    last_loaded_model="kuato",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[
                    _entry("kuato.gguf", "kuato"),
                    _entry("too-large.gguf", "too-large"),
                ],
                body={"model": "too-large.gguf"},
                resolve_outcome=({"key": "too-large.gguf", "backend_id": "too-large"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager("cudaMalloc failed: out of memory")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["ok"])
            self.assertTrue(body["rollback_performed"])
            # selected_* rolled back
            self.assertEqual(body["selected_key"], "kuato.gguf")
            self.assertEqual(body["selected_backend_id"], "kuato")
            # failed_candidate_* records what failed
            self.assertEqual(body["failed_candidate_key"], "too-large.gguf")
            self.assertEqual(body["failed_candidate_backend_id"], "too-large")
            # last_good_* preserved
            self.assertEqual(body["last_good_key"], "kuato.gguf")
            self.assertTrue(body["recovery_available"])
            self.assertIn("rolled back", body["recommended_recovery_action"])
            # State on disk reflects same
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            self.assertEqual(payload["failed_candidate_key"], "too-large.gguf")

    def test_select_and_restart_no_rollback_when_explicitly_disabled(self):
        """rollback_on_failure=false in body keeps the failed candidate selected."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    last_good_key="kuato.gguf",
                    last_good_backend_id="kuato",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[
                    _entry("kuato.gguf", "kuato"),
                    _entry("too-large.gguf", "too-large"),
                ],
                body={"model": "too-large.gguf", "rollback_on_failure": False},
                resolve_outcome=({"key": "too-large.gguf", "backend_id": "too-large"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager("cudaMalloc failed")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["rollback_performed"])
            # selected_* still the failed candidate
            self.assertEqual(body["selected_key"], "too-large.gguf")
            self.assertEqual(body["selected_backend_id"], "too-large")
            self.assertEqual(body["failed_candidate_key"], "too-large.gguf")

    def test_failure_with_no_last_good_keeps_candidate_selected(self):
        """When no last_good exists, rollback can't happen — candidate stays selected."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("too-large.gguf", "too-large")],
                body={"model": "too-large.gguf"},
                resolve_outcome=({"key": "too-large.gguf", "backend_id": "too-large"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager("cudaMalloc failed")
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["rollback_performed"])
            self.assertFalse(body["recovery_available"])
            self.assertEqual(body["selected_key"], "too-large.gguf")
            self.assertIn("no last-good", body["recommended_recovery_action"].lower())

    def test_successful_load_records_last_good(self):
        """A confirmed-loaded selection records last_good_* and clears failed_candidate_*."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            # Seed with prior failure
            write_model_state(
                ModelState(
                    selected_key="too-large.gguf",
                    selected_backend_id="too-large",
                    failed_candidate_key="too-large.gguf",
                    failed_candidate_backend_id="too-large",
                    last_load_result="failed",
                    last_load_error="cudaMalloc",
                    last_load_error_type="insufficient_vram",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="kuato",
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            # Clean logs → no failure
            handler.backend_manager = _FakeBackendManager(
                "common_init_from_params: warming up the model\nupdate_slots: all slots are idle"
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["last_load_result"], "loaded")
            self.assertIsNone(payload["last_load_error"])
            self.assertEqual(payload["last_good_key"], "kuato.gguf")
            self.assertEqual(payload["last_good_backend_id"], "kuato")
            self.assertEqual(payload["last_good_source"], "operator")
            # Failed candidate cleared after recovery
            self.assertEqual(payload["failed_candidate_key"], "")
            self.assertEqual(payload["failed_candidate_backend_id"], "")

    def test_reload_endpoint_classifies_failures(self):
        """POST /qz/model/reload also runs the classifier."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            write_model_state(
                ModelState(selected_key="kuato.gguf", selected_backend_id="kuato"),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                loaded_id="",
                resolve_outcome=({"key": "kuato.gguf", "backend_id": "kuato"}, "test"),
            )
            handler.backend_manager = _FakeBackendManager(
                "failed to allocate buffer for kv cache"
            )
            RequestRouter(handler)._handle_model_reload_endpoint()
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertEqual(body["last_load_error_type"], "insufficient_vram")


# ---------------------------------------------------------------------------
# Direct-mode endpoint behaviour
# ---------------------------------------------------------------------------

class DirectModeEndpointTests(unittest.TestCase):
    """In direct mode, /qz/model/{reload,select-and-restart} load through
    BackendManager HTTP model management instead of the legacy resolve path."""

    def test_select_and_restart_in_direct_mode_calls_set_launch_model_and_load(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="kuato",
            )
            handler.backend_manager = _FakeBackendManager(
                logs="update_slots: all slots are idle",
                backend_model_mode="direct",
                post_restart_phase="healthy",
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            # set_launch_model was called with the resolved filename
            calls = handler.backend_manager.set_launch_model_calls
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["path_basename"], "kuato.gguf")
            self.assertEqual(calls[0]["backend_id"], "kuato")
            self.assertEqual(handler.backend_manager.restart_calls, 0)
            self.assertEqual(handler.backend_manager.load_model_http_calls, ["kuato.gguf"])
            # 200 OK with status payload
            status, body = handler.sent[0]
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            # Direct mode reflected in status
            self.assertEqual(body["backend_model_mode"], "direct")
            self.assertEqual(body["launch_model_path_basename"], "kuato.gguf")

    def test_direct_mode_restart_failure_returns_409_with_classified_state(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
            )
            handler.backend_manager = _FakeBackendManager(
                logs="cudaMalloc failed: out of memory",
                backend_model_mode="direct",
                post_restart_phase="failed",
                post_restart_error="backend failed to launch",
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 409)
            self.assertFalse(body["ok"])
            self.assertEqual(body["last_load_error_type"], "insufficient_vram")
            self.assertEqual(body["backend_model_mode"], "direct")

    def test_legacy_models_select_returns_410(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                path="/qz/models/select",
            )
            RequestRouter(handler).handle_post()
            status, body = handler.sent[0]
            self.assertEqual(status, 410)
            self.assertFalse(body["ok"])
            self.assertIn("/qz/model/select-and-restart", body["message"])

    def test_status_surface_exposes_mode_and_switch_state(self):
        """GET-shape status payload includes backend_model_mode, launch_model_*,
        model_switch_state, active_load_operation."""
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            write_model_state(
                ModelState(
                    selected_key="kuato.gguf",
                    selected_backend_id="kuato",
                    selected_source="operator",
                    last_load_result="loaded",
                    last_loaded_model="kuato",
                    last_good_key="kuato.gguf",
                    last_good_backend_id="kuato",
                ),
                state_path,
            )
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                loaded_id="kuato",
            )
            handler.backend_manager = _FakeBackendManager(
                logs="update_slots: all slots are idle",
                backend_model_mode="direct",
            )
            handler.backend_manager.set_launch_model(
                key="kuato.gguf", backend_id="kuato", path_basename="kuato.gguf",
            )
            from proxy.qz_model_status import build_model_status
            status = build_model_status(handler)
            self.assertEqual(status["backend_model_mode"], "direct")
            self.assertEqual(status["launch_model_key"], "kuato.gguf")
            self.assertEqual(status["launch_model_path_basename"], "kuato.gguf")
            self.assertIn(status["model_switch_state"], ("loaded", "idle"))
            self.assertIn("active_load_operation", status)


# ---------------------------------------------------------------------------
# Script-sprawl guard — no new model-selection scripts
# ---------------------------------------------------------------------------

class ScriptSprawlGuardTests(unittest.TestCase):
    """Slice C invariant: no scripts/qz-model* wrappers may be added."""

    FORBIDDEN = (
        "scripts/qz-model",
        "scripts/qz-select-model",
        "scripts/qz-model-status",
        "scripts/qz-load-model",
    )

    def test_no_model_selection_scripts_added(self):
        root = Path(__file__).resolve().parents[1]
        for relpath in self.FORBIDDEN:
            self.assertFalse(
                (root / relpath).exists(),
                f"{relpath} must not exist — model selection lives at /qz/model/* "
                f"(see docs/proxy-model-selection-authority.md §12)",
            )


if __name__ == "__main__":
    unittest.main()
