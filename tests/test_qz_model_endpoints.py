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

    def test_invokes_resolve_with_selected_identity(self):
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
            RequestRouter(handler)._handle_model_reload_endpoint()
        self.assertEqual(called, ["kuato.gguf"])
        status, body = handler.sent[0]
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], QZ_MODEL_STATUS_SCHEMA)


# ---------------------------------------------------------------------------
# POST /qz/model/select-and-restart
# ---------------------------------------------------------------------------

class SelectAndRestartEndpointTests(unittest.TestCase):

    def test_validates_then_invokes_resolve(self):
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
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            self.assertEqual(called, ["kuato.gguf"])
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")
            status, body = handler.sent[0]
            self.assertEqual(status, 200)

    def test_reload_failure_returns_500_with_status_payload(self):
        with _tmp_state_dir() as tmp:
            state_path = tmp / "model-state.json"
            handler = _FakeHandler(
                state_path=state_path,
                entries=[_entry("kuato.gguf", "kuato")],
                body={"model": "kuato.gguf"},
                resolve_outcome=(None, "OOM during context allocation"),
            )
            RequestRouter(handler)._handle_model_select_endpoint(restart=True)
            status, body = handler.sent[0]
            self.assertEqual(status, 500)
            self.assertFalse(body["ok"])
            self.assertIn("OOM", body["error"])
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["selected_key"], "kuato.gguf")


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
