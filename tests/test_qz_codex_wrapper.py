"""Tests for qz-codex wrapper effective-model resolution and loading wait/poll.

Eight behavioral requirements from the qz-codex wrapper contract:

  1. no explicit model arg + selected model exists
       → qz_resolve_effective_model returns that model (no mismatch failure)
  2. no selected model exists (fresh install)
       → qz_resolve_effective_model POSTs select-and-restart with source=qz_codex
         and returns the resulting selected model
  3. selected model matches but request_admission_state=starting
       → qz_codex_exec_preflight waits and succeeds when ready
  4. selected model matches but request_admission_state=loading
       → qz_codex_exec_preflight waits and succeeds when ready
  5. request_admission_state=failed
       → qz_codex_exec_preflight fails immediately; no wait loop
  6. explicit -m mismatch still fails unless QZ_CODEX_AUTO_SELECT_MODEL=1
       (existing behavior preserved)
  7. explicit -m mismatch with auto-select enabled triggers select-and-restart
       (existing behavior preserved)
  8. timeout during loading produces a clear failure message
"""

import http.server
import json
import os
import shlex
import subprocess
import threading
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
QZ_CODEX_COMMON = REPO_ROOT / "scripts" / "qz-codex-common"

# ---------------------------------------------------------------------------
# Shared mock proxy infrastructure
# ---------------------------------------------------------------------------

class _MockProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        srv = self.server
        if parsed.path == "/qz/model/status":
            # If server has a call-count-based status sequence, use it
            if hasattr(srv, "status_sequence"):
                idx = min(srv.call_count, len(srv.status_sequence) - 1)
                body_obj = srv.status_sequence[idx]
                srv.call_count += 1
            else:
                body_obj = srv.status_response
            body = json.dumps(body_obj).encode("utf-8")
            self.send_response(getattr(srv, "status_status", 200))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        srv = self.server
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        srv.posts.append((parsed.path, body))
        if parsed.path == "/qz/model/select-and-restart":
            resp_obj = getattr(srv, "select_response", {})
            resp_code = getattr(srv, "select_status", 200)
            payload = json.dumps(resp_obj).encode("utf-8")
            self.send_response(resp_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


class _MockProxy:
    """Context-manager wrapper around a mock HTTP proxy."""

    def __init__(
        self,
        *,
        status_response: dict | None = None,
        status_sequence: list[dict] | None = None,
        status_status: int = 200,
        select_response: dict | None = None,
        select_status: int = 200,
    ):
        self._status_response = status_response or {}
        self._status_sequence = status_sequence
        self._status_status = status_status
        self._select_response = select_response or {}
        self._select_status = select_status
        self.posts: list[tuple[str, dict]] = []
        self._server: http.server.HTTPServer | None = None
        self.port = 0

    def __enter__(self):
        self._server = http.server.HTTPServer(("127.0.0.1", 0), _MockProxyHandler)
        self._server.status_response = self._status_response
        if self._status_sequence is not None:
            self._server.status_sequence = self._status_sequence
            self._server.call_count = 0
        self._server.status_status = self._status_status
        self._server.select_response = self._select_response
        self._server.select_status = self._select_status
        self._server.posts = self.posts
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_):
        if self._server is not None:
            self._server.shutdown()


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _status(
    *,
    selected_key: str = "",
    selected_backend_id: str = "",
    backend_loaded_model: str = "",
    selected_source: str = "operator",
    mismatch: bool = False,
    admission_state: str | None = None,
) -> dict:
    ready = bool(backend_loaded_model and not mismatch)
    if admission_state is None:
        if ready:
            admission_state = "ready"
        elif backend_loaded_model and mismatch:
            admission_state = "unavailable"
        elif selected_key or selected_backend_id:
            admission_state = "loading"
        else:
            admission_state = "unavailable"
    return {
        "ok": True,
        "schema": "qz.model_status.v1",
        "selected_key": selected_key,
        "selected_backend_id": selected_backend_id,
        "selected_label": "",
        "selected_source": selected_source,
        "backend_loaded_model": backend_loaded_model,
        "selected_loaded_mismatch": mismatch,
        "selected_model_ready": ready,
        "request_admission_state": admission_state,
        "backend_phase": "running" if (selected_key or selected_backend_id) else "idle",
        "backend_gpu_state": "gpu",
        "last_load_result": "loaded" if ready else "loading",
        "last_load_error": None,
        "last_load_error_type": None,
        "recommended_action": None,
    }


def _run_resolve(port: int) -> subprocess.CompletedProcess:
    """Call qz_resolve_effective_model directly from bash."""
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_PROXY_HOST": "127.0.0.1",
        "QZ_PROXY_PORT": str(port),
        "QZ_ROOT": str(REPO_ROOT),
    }
    script = f"source '{QZ_CODEX_COMMON}' && qz_resolve_effective_model"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _run_model_from_args(*args: str) -> subprocess.CompletedProcess:
    """Call qz_exec_model_from_args directly from bash and capture stdout."""
    args_str = " ".join(shlex.quote(a) for a in args)
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_ROOT": str(REPO_ROOT),
    }
    script = f"source '{QZ_CODEX_COMMON}' && qz_exec_model_from_args {args_str}"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _run_preflight(port: int, requested: str, *, autoselect: bool = False) -> subprocess.CompletedProcess:
    """Call qz_codex_exec_preflight directly from bash."""
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_PROXY_HOST": "127.0.0.1",
        "QZ_PROXY_PORT": str(port),
        "QZ_ROOT": str(REPO_ROOT),
        "QZ_CODEX_READY_TIMEOUT": "5",
    }
    if autoselect:
        env["QZ_CODEX_AUTO_SELECT_MODEL"] = "1"
    script = f"source '{QZ_CODEX_COMMON}' && qz_codex_exec_preflight '{requested}'"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Test 1: no explicit model arg + selected model exists
# ---------------------------------------------------------------------------

class EffectiveModelResolutionTests(unittest.TestCase):
    """qz_resolve_effective_model returns the already-selected model."""

    def test_selected_backend_id_returned(self):
        st = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                     backend_loaded_model="kuato")
        with _MockProxy(status_response=st) as proxy:
            result = _run_resolve(proxy.port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "kuato")

    def test_selected_key_used_when_no_backend_id(self):
        st = _status(selected_key="kuato.gguf", selected_backend_id="",
                     backend_loaded_model="kuato.gguf")
        with _MockProxy(status_response=st) as proxy:
            result = _run_resolve(proxy.port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "kuato.gguf")

    def test_selected_model_returned_even_if_not_yet_ready(self):
        """Loading state: model is selected but backend not ready yet.
        qz_resolve_effective_model should still return the selected model name
        so that qz_codex_exec_preflight can run the wait/poll loop."""
        st = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                     backend_loaded_model="", admission_state="starting")
        with _MockProxy(status_response=st) as proxy:
            result = _run_resolve(proxy.port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "kuato")

    def test_proxy_unreachable_exits_3(self):
        result = _run_resolve(1)
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_no_mismatch_failure_for_selected_model(self):
        """End-to-end: resolve returns selected model, preflight passes — no spurious
        mismatch failure caused by a hardcoded wrapper default."""
        st = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                     backend_loaded_model="kuato")
        with _MockProxy(status_response=st) as proxy:
            # Step 1: resolve the effective model
            r_resolve = _run_resolve(proxy.port)
            self.assertEqual(r_resolve.returncode, 0, r_resolve.stderr)
            effective = r_resolve.stdout.strip()
            # Step 2: preflight with that effective model passes
            r_preflight = _run_preflight(proxy.port, effective)
        self.assertEqual(r_preflight.returncode, 0, r_preflight.stderr)


# ---------------------------------------------------------------------------
# Test 2: no selected model exists → bootstrap via select-and-restart
# ---------------------------------------------------------------------------

class BootstrapNoSelectedModelTests(unittest.TestCase):

    def _bootstrap_response(self, backend_id: str) -> dict:
        """What /qz/model/select-and-restart returns for a bootstrapped model."""
        return _status(
            selected_key=backend_id + ".gguf",
            selected_backend_id=backend_id,
            backend_loaded_model="",
            admission_state="starting",
        )

    def test_bootstrap_posts_select_and_restart_without_model(self):
        """When no model is selected, we POST select-and-restart with no model field
        so the proxy picks its catalog default.  Only source=qz_codex is sent."""
        empty_st = _status()  # no selected model
        bootstrap_resp = self._bootstrap_response("firstmodel")
        with _MockProxy(status_response=empty_st, select_response=bootstrap_resp) as proxy:
            result = _run_resolve(proxy.port)
        self.assertEqual(result.returncode, 0, result.stderr)
        # One POST to select-and-restart
        self.assertEqual(len(proxy.posts), 1)
        path, body = proxy.posts[0]
        self.assertEqual(path, "/qz/model/select-and-restart")
        # No explicit "model" field — let proxy pick default
        self.assertNotIn("model", body)
        # source must be qz_codex for SELECTED_SOURCES validation
        self.assertEqual(body.get("source"), "qz_codex")

    def test_bootstrap_returns_selected_model_from_response(self):
        empty_st = _status()
        bootstrap_resp = self._bootstrap_response("firstmodel")
        with _MockProxy(status_response=empty_st, select_response=bootstrap_resp) as proxy:
            result = _run_resolve(proxy.port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "firstmodel")

    def test_bootstrap_failure_exits_1(self):
        empty_st = _status()
        with _MockProxy(
            status_response=empty_st,
            select_response={"ok": False, "error": "no models available"},
            select_status=500,
        ) as proxy:
            result = _run_resolve(proxy.port)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed", result.stderr)

    def test_bootstrap_prints_informational_message(self):
        empty_st = _status()
        bootstrap_resp = self._bootstrap_response("firstmodel")
        with _MockProxy(status_response=empty_st, select_response=bootstrap_resp) as proxy:
            result = _run_resolve(proxy.port)
        self.assertIn("bootstrap", result.stderr)
        self.assertIn("firstmodel", result.stderr)


# ---------------------------------------------------------------------------
# Tests 3 + 4: selected model matches but still loading → wait and succeed
# ---------------------------------------------------------------------------

class LoadingWaitPollTests(unittest.TestCase):
    """qz_codex_exec_preflight polls until the backend becomes ready."""

    def _loading_then_ready(self, admission_state: str) -> tuple[dict, dict]:
        """(loading_status, ready_status) for a given initial admission state."""
        loading = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state=admission_state,
        )
        ready = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="kuato",
        )
        return loading, ready

    def test_starting_state_waits_and_succeeds(self):
        loading, ready = self._loading_then_ready("starting")
        # First call → loading; second call → ready
        seq = [loading, ready]
        with _MockProxy(status_sequence=seq) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting", result.stderr)

    def test_loading_state_waits_and_succeeds(self):
        loading, ready = self._loading_then_ready("loading")
        seq = [loading, ready]
        with _MockProxy(status_sequence=seq) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting", result.stderr)

    def test_wait_message_includes_model_name_and_timeout(self):
        loading, ready = self._loading_then_ready("loading")
        seq = [loading, ready]
        with _MockProxy(status_sequence=seq) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertIn("kuato", result.stderr)
        self.assertIn("5", result.stderr)  # QZ_CODEX_READY_TIMEOUT=5

    def test_loading_does_not_require_auto_select(self):
        """The wait-for-loading path must work without QZ_CODEX_AUTO_SELECT_MODEL=1."""
        loading, ready = self._loading_then_ready("loading")
        seq = [loading, ready]
        with _MockProxy(status_sequence=seq) as proxy:
            result = _run_preflight(proxy.port, "kuato", autoselect=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_during_poll_aborts_immediately(self):
        """If the model fails to load during the wait loop, abort immediately."""
        loading = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="loading",
        )
        failed = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="failed",
        )
        failed["last_load_result"] = "failed"
        seq = [loading, failed]
        with _MockProxy(status_sequence=seq) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed", result.stderr)


# ---------------------------------------------------------------------------
# Test 5: hard failure → immediate failure, no wait
# ---------------------------------------------------------------------------

class HardFailureTests(unittest.TestCase):

    def test_failed_state_does_not_wait(self):
        st = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="failed",
        )
        st["last_load_result"] = "failed"
        with _MockProxy(status_response=st) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("failed", result.stderr)
        # The wait-loop message starts with "waiting up to"; must not appear.
        self.assertNotIn("waiting up to", result.stderr)

    def test_failed_gpu_not_available_does_not_wait(self):
        st = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="failed_gpu_not_available",
        )
        with _MockProxy(status_response=st) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("waiting up to", result.stderr)

    def test_failed_state_prints_useful_detail(self):
        st = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="failed",
        )
        st["last_load_result"] = "failed"
        st["last_load_error_type"] = "insufficient_vram"
        st["last_load_error"] = "cudaMalloc failed"
        with _MockProxy(status_response=st) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertIn("failed", result.stderr)


# ---------------------------------------------------------------------------
# Tests 6 + 7: explicit -m mismatch behavior (existing contract preserved)
# ---------------------------------------------------------------------------

class ExplicitModelMismatchTests(unittest.TestCase):

    def test_explicit_mismatch_fails_without_auto_select(self):
        """Test 6: explicit -m mismatch → fail, print curl hint, no POST."""
        st = _status(selected_key="other.gguf", selected_backend_id="other",
                     backend_loaded_model="other")
        with _MockProxy(status_response=st) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not the active backend model", result.stderr)
        self.assertIn("curl -sS -X POST", result.stderr)
        self.assertEqual(proxy.posts, [])

    def test_explicit_mismatch_with_auto_select_triggers_select_and_restart(self):
        """Test 7: explicit -m mismatch + auto-select → POST select-and-restart."""
        initial = _status(selected_key="other.gguf", selected_backend_id="other",
                          backend_loaded_model="other")
        new_status = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                             backend_loaded_model="kuato", selected_source="qz_codex")
        with _MockProxy(status_response=initial, select_response=new_status) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(proxy.posts), 1)
        path, body = proxy.posts[0]
        self.assertEqual(path, "/qz/model/select-and-restart")
        self.assertEqual(body.get("model"), "kuato.gguf")
        self.assertEqual(body.get("source"), "qz_codex")


# ---------------------------------------------------------------------------
# Test 8: timeout during loading
# ---------------------------------------------------------------------------

class TimeoutTests(unittest.TestCase):

    def test_timeout_produces_clear_failure_message(self):
        """Mock proxy always returns loading state; should time out and print message."""
        forever_loading = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="loading",
        )
        with _MockProxy(status_response=forever_loading) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertIn("kuato", result.stderr)

    def test_timeout_message_includes_admission_state(self):
        forever_loading = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="loading",
        )
        with _MockProxy(status_response=forever_loading) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertIn("loading", result.stderr)

    def test_timeout_does_not_post_select_and_restart(self):
        """A load timeout is not a mismatch; must not trigger auto-select."""
        forever_loading = _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="", admission_state="loading",
        )
        with _MockProxy(status_response=forever_loading) as proxy:
            result = _run_preflight(proxy.port, "kuato", autoselect=True)
        self.assertNotEqual(result.returncode, 0)
        # Should not have hit select-and-restart (this is a wait timeout, not a mismatch)
        self.assertEqual(proxy.posts, [])


# ---------------------------------------------------------------------------
# Bug 1: profile args must not be parsed as model args
# ---------------------------------------------------------------------------

class ModelFromArgsParsingTests(unittest.TestCase):
    """qz_exec_model_from_args must only extract -m/--model values.
    Profile flags (-p / --profile / --profile=...) must produce empty output."""

    def test_explicit_dash_m_returns_model(self):
        r = _run_model_from_args("exec", "-m", "kuato.gguf")
        self.assertEqual(r.stdout.strip(), "kuato.gguf")

    def test_explicit_dash_dash_model_returns_model(self):
        r = _run_model_from_args("exec", "--model", "kuato.gguf")
        self.assertEqual(r.stdout.strip(), "kuato.gguf")

    def test_explicit_model_equals_form(self):
        r = _run_model_from_args("exec", "--model=kuato.gguf")
        self.assertEqual(r.stdout.strip(), "kuato.gguf")

    def test_dash_p_profile_does_not_produce_model(self):
        """exec -p high must NOT be treated as an explicit model 'high'."""
        r = _run_model_from_args("exec", "-p", "high")
        self.assertEqual(r.stdout.strip(), "")

    def test_dash_dash_profile_does_not_produce_model(self):
        """exec --profile high must NOT be treated as an explicit model 'high'."""
        r = _run_model_from_args("exec", "--profile", "high")
        self.assertEqual(r.stdout.strip(), "")

    def test_dash_dash_profile_equals_does_not_produce_model(self):
        """exec --profile=high must NOT be treated as an explicit model 'high'."""
        r = _run_model_from_args("exec", "--profile=high")
        self.assertEqual(r.stdout.strip(), "")

    def test_model_plus_profile_returns_model_only(self):
        """exec -m kuato.gguf -p high — model is extracted; profile is ignored."""
        r = _run_model_from_args("exec", "-m", "kuato.gguf", "-p", "high")
        self.assertEqual(r.stdout.strip(), "kuato.gguf")

    def test_profile_then_model_returns_model(self):
        """exec -p high -m kuato.gguf — profile precedes model; model still extracted."""
        # -p is skipped (not model), -m kuato.gguf is extracted
        r = _run_model_from_args("exec", "-p", "high", "-m", "kuato.gguf")
        self.assertEqual(r.stdout.strip(), "kuato.gguf")

    def test_no_args_returns_empty(self):
        r = _run_model_from_args("exec")
        self.assertEqual(r.stdout.strip(), "")

    def test_double_dash_stops_parsing(self):
        """-- terminates arg scanning; model after -- is not extracted."""
        r = _run_model_from_args("exec", "--", "-m", "kuato.gguf")
        self.assertEqual(r.stdout.strip(), "")


# ---------------------------------------------------------------------------
# Bug 2: auto-select path waits after POST instead of failing immediately
# ---------------------------------------------------------------------------

class AutoSelectPostWaitTests(unittest.TestCase):
    """After POST /qz/model/select-and-restart, if the backend is loading,
    qz_codex_exec_preflight must wait/poll rather than fail immediately."""

    def _other_model_status(self) -> dict:
        """A status showing a different model loaded (produces mismatch for kuato)."""
        return _status(
            selected_key="other.gguf", selected_backend_id="other",
            backend_loaded_model="other",
        )

    def _post_response(self, admission_state: str) -> dict:
        """What select-and-restart returns when kuato is selected but loading."""
        return _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="",
            admission_state=admission_state,
        )

    def _ready_status(self) -> dict:
        return _status(
            selected_key="kuato.gguf", selected_backend_id="kuato",
            backend_loaded_model="kuato",
        )

    def test_starting_after_autoselect_waits_and_succeeds(self):
        """POST returns 'starting' → preflight waits and succeeds on next poll."""
        initial = self._other_model_status()
        post_resp = self._post_response("starting")
        ready = self._ready_status()
        # GET seq: [initial, ready] (second GET in the poll loop)
        with _MockProxy(
            status_sequence=[initial, ready],
            select_response=post_resp,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting", result.stderr)
        # POST was made
        self.assertEqual(len(proxy.posts), 1)

    def test_loading_after_autoselect_waits_and_succeeds(self):
        """POST returns 'loading' → preflight waits and succeeds on next poll."""
        initial = self._other_model_status()
        post_resp = self._post_response("loading")
        ready = self._ready_status()
        with _MockProxy(
            status_sequence=[initial, ready],
            select_response=post_resp,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting", result.stderr)

    def test_failed_after_autoselect_fails_immediately(self):
        """POST returns 'failed' → preflight fails immediately (no wait)."""
        initial = self._other_model_status()
        post_resp = self._post_response("failed")
        post_resp["last_load_result"] = "failed"
        with _MockProxy(status_response=initial, select_response=post_resp) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("waiting up to", result.stderr)
        self.assertIn("failed", result.stderr)

    def test_loading_timeout_after_autoselect_gives_clear_message(self):
        """POST returns 'loading'; backend never becomes ready → timeout message."""
        initial = self._other_model_status()
        post_resp = self._post_response("loading")
        forever_loading = self._post_response("loading")  # GET poll always returns loading
        # Sequence: [initial, forever_loading, forever_loading, ...]
        with _MockProxy(
            status_sequence=[initial, forever_loading],
            select_response=post_resp,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertIn("kuato.gguf", result.stderr)

    def test_still_mismatched_after_autoselect_exits_2(self):
        """POST returns a status where kuato is NOT selected → still-not-active error."""
        initial = self._other_model_status()
        still_other = self._other_model_status()  # POST didn't change anything
        with _MockProxy(status_response=initial, select_response=still_other) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("still not the active model", result.stderr)

    def test_autoselect_wait_posts_select_and_restart_once(self):
        """Even if the wait loop runs multiple polls, only one POST is made."""
        initial = self._other_model_status()
        post_resp = self._post_response("loading")
        ready = self._ready_status()
        with _MockProxy(
            status_sequence=[initial, post_resp, ready],  # two poll rounds
            select_response=post_resp,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Exactly one POST
        self.assertEqual(len([p for p in proxy.posts if p[0] == "/qz/model/select-and-restart"]), 1)


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------

class StructuralInvariantTests(unittest.TestCase):
    """Ensure the new wrapper functions are present in the script."""

    @classmethod
    def setUpClass(cls):
        cls.common = QZ_CODEX_COMMON.read_text(encoding="utf-8")

    def test_qz_resolve_effective_model_defined(self):
        self.assertIn("qz_resolve_effective_model", self.common)

    def test_selected_identity_match_defined(self):
        self.assertIn("selected_identity_match", self.common)

    def test_poll_until_ready_helper_defined(self):
        self.assertIn("poll_until_ready", self.common)

    def test_loading_wait_references_starting_and_loading(self):
        # The wait branch must check both "starting" and "loading" admission states.
        self.assertIn('"starting"', self.common)
        self.assertIn('"loading"', self.common)

    def test_hard_failure_states_referenced(self):
        self.assertIn('"failed"', self.common)
        self.assertIn('"failed_gpu_not_available"', self.common)

    def test_bootstrap_uses_qz_codex_source(self):
        self.assertIn('"source": "qz_codex"', self.common)

    def test_timed_out_message_present(self):
        self.assertIn("timed out", self.common)

    def test_profile_flags_not_in_model_from_args(self):
        """qz_exec_model_from_args must not parse -p/--profile as model flags."""
        # Find the function body and verify -p is absent from the model extraction logic.
        # The fix removes -p, --profile, --profile=* from the case statement.
        import re
        fn_match = re.search(
            r'qz_exec_model_from_args\(\).*?^}',
            self.common,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(fn_match, "qz_exec_model_from_args not found")
        fn_body = fn_match.group(0)
        # Profile flags must not appear as model-extracting patterns
        self.assertNotIn("--profile=*)", fn_body)
        self.assertNotIn("-p|--profile)", fn_body)
        # Model flags must still be present
        self.assertIn("-m|--model)", fn_body)
        self.assertIn("--model=*)", fn_body)


if __name__ == "__main__":
    unittest.main()
