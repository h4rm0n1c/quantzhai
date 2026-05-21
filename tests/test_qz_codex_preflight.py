"""Tests for qz-codex exec preflight — Slice D.

Two layers:
  1. Structural — assert the script content references the new endpoints
     and no longer relies on visibility-only preflight.
  2. Behavioural — run `qz_codex_exec_preflight` from qz-codex-common in a
     subprocess against a mock proxy that serves /qz/model/status and
     /qz/model/select-and-restart, and observe exit codes / stderr / POST
     bodies.
"""

import http.server
import json
import os
import subprocess
import threading
import unittest
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
QZ_CODEX = REPO_ROOT / "scripts" / "qz-codex"
QZ_CODEX_COMMON = REPO_ROOT / "scripts" / "qz-codex-common"


# ---------------------------------------------------------------------------
# Structural tests — content of the scripts themselves
# ---------------------------------------------------------------------------

class StructuralTests(unittest.TestCase):
    """Locks in the Slice D contract by inspecting the script source."""

    @classmethod
    def setUpClass(cls):
        cls.qz_codex = QZ_CODEX.read_text(encoding="utf-8")
        cls.qz_codex_common = QZ_CODEX_COMMON.read_text(encoding="utf-8")

    def test_qz_codex_calls_new_preflight_helper(self):
        self.assertIn("qz_codex_exec_preflight", self.qz_codex)

    def test_qz_codex_no_longer_calls_qz_wait_ready_for_model(self):
        # The old preflight passed --catalog --model to qz-wait-ready.
        self.assertNotIn("qz-wait-ready", self.qz_codex)

    def test_preflight_helper_references_qz_model_status(self):
        self.assertIn("/qz/model/status", self.qz_codex_common)

    def test_preflight_helper_references_select_and_restart(self):
        self.assertIn("/qz/model/select-and-restart", self.qz_codex_common)

    def test_preflight_helper_references_qz_codex_auto_select_model(self):
        self.assertIn("QZ_CODEX_AUTO_SELECT_MODEL", self.qz_codex_common)

    def test_mismatch_message_includes_literal_curl(self):
        self.assertIn("curl -sS -X POST", self.qz_codex_common)
        self.assertIn("Content-Type: application/json", self.qz_codex_common)

    def test_mismatch_message_mentions_auto_select_env(self):
        self.assertIn(
            "QZ_CODEX_AUTO_SELECT_MODEL=1 to let qz-codex select/restart",
            self.qz_codex_common,
        )

    def test_select_and_restart_post_uses_qz_codex_source(self):
        # The POST body must include source=qz_codex so SELECTED_SOURCES validates.
        self.assertIn("\"source\": \"qz_codex\"", self.qz_codex_common)

    def test_qz_codex_propagates_preflight_exit_code(self):
        """Slice F regression: ``! cmd; then exit $?`` swallows the exit code.

        scripts/qz-codex must propagate qz_codex_exec_preflight's non-zero
        exit so that an exec-with-mismatch actually halts before launching
        Codex.  Use ``|| exit $?`` (or equivalent) — never ``! cmd; then exit $?``.
        """
        self.assertNotRegex(
            self.qz_codex,
            r"if\s*!\s*qz_codex_exec_preflight[^\n]*\n\s*exit\s+\$\?",
            "qz-codex must not use `! qz_codex_exec_preflight; then exit $?` — "
            "the `!` inverts the exit code so $? is always 0 inside the then-branch",
        )
        self.assertRegex(
            self.qz_codex,
            r"qz_codex_exec_preflight[^\n]*\|\|\s*exit\s+\$\?",
            "qz-codex should use `qz_codex_exec_preflight ... || exit $?`",
        )

    def test_no_new_model_selection_scripts(self):
        for relpath in (
            "scripts/qz-model",
            "scripts/qz-select-model",
            "scripts/qz-model-status",
            "scripts/qz-load-model",
        ):
            self.assertFalse(
                (REPO_ROOT / relpath).exists(),
                f"{relpath} must not exist (script-sprawl invariant)",
            )


# ---------------------------------------------------------------------------
# Mock proxy + behavioural tests
# ---------------------------------------------------------------------------

class _MockProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # silence test output
        return

    def do_GET(self):
        srv = self.server
        parsed = urlparse(self.path)
        if parsed.path == "/qz/model/status":
            body = json.dumps(srv.status_response).encode("utf-8")
            self.send_response(srv.status_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        srv = self.server
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        srv.posts.append((parsed.path, body))
        if parsed.path == "/qz/model/select-and-restart":
            payload = json.dumps(srv.select_response).encode("utf-8")
            self.send_response(srv.select_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()


class _MockProxy:
    def __init__(
        self,
        *,
        status_response: dict,
        status_status: int = 200,
        select_response: dict | None = None,
        select_status: int = 200,
    ):
        self.status_response = status_response
        self.status_status = status_status
        self.select_response = select_response or {}
        self.select_status = select_status
        self.posts: list[tuple[str, dict]] = []
        self.server: http.server.HTTPServer | None = None
        self.port = 0

    def __enter__(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _MockProxyHandler)
        self.server.status_response = self.status_response
        self.server.status_status = self.status_status
        self.server.select_response = self.select_response
        self.server.select_status = self.select_status
        self.server.posts = self.posts
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_):
        if self.server is not None:
            self.server.shutdown()


def _run_preflight(port: int, requested: str, *, autoselect: bool = False, timeout: int = 10):
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
        timeout=timeout,
    )


def _status(
    *,
    selected_key: str = "",
    selected_backend_id: str = "",
    backend_loaded_model: str = "",
    selected_source: str = "operator",
    mismatch: bool = False,
) -> dict:
    return {
        "ok": True,
        "schema": "qz.model_status.v1",
        "configured_env_model": "",
        "selected_key": selected_key,
        "selected_backend_id": selected_backend_id,
        "selected_label": "",
        "selected_source": selected_source,
        "selected_at": "2026-05-21T10:00:00Z",
        "backend_loaded_model": backend_loaded_model,
        "selected_loaded_mismatch": mismatch,
        "model_visible": True,
        "profile_valid": True,
        "backend_phase": "healthy",
        "backend_gpu_state": "gpu",
        "last_load_result": "loaded",
        "last_load_error": None,
        "last_load_error_type": None,
        "recommended_action": None,
    }


class PreflightBehaviouralTests(unittest.TestCase):

    def test_active_match_exits_zero(self):
        status = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                         backend_loaded_model="kuato")
        with _MockProxy(status_response=status) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_match_via_backend_alias(self):
        """Requested 'kuato' matches when backend_loaded_model == 'kuato'."""
        status = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                         backend_loaded_model="kuato")
        with _MockProxy(status_response=status) as proxy:
            result = _run_preflight(proxy.port, "kuato")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mismatch_without_autoselect_exits_1_and_prints_curl(self):
        status = _status(selected_key="other.gguf", selected_backend_id="other",
                         backend_loaded_model="other")
        with _MockProxy(status_response=status) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("not the active backend model", result.stderr)
        self.assertIn("curl -sS -X POST", result.stderr)
        self.assertIn("/qz/model/select-and-restart", result.stderr)
        self.assertIn("QZ_CODEX_AUTO_SELECT_MODEL=1", result.stderr)
        # No backend mutation
        self.assertEqual(_pending_posts(proxy), [])

    def test_mismatch_with_autoselect_posts_select_and_restart(self):
        initial = _status(selected_key="other.gguf", selected_backend_id="other",
                          backend_loaded_model="other")
        # select-and-restart returns the *new* status with requested as active
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

    def test_autoselect_failure_exits_2(self):
        initial = _status(selected_key="other.gguf", selected_backend_id="other",
                          backend_loaded_model="other")
        with _MockProxy(
            status_response=initial,
            select_response={"ok": False, "error": "OOM: insufficient VRAM"},
            select_status=500,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("select-and-restart failed", result.stderr)

    def test_autoselect_still_mismatch_after_post_exits_2(self):
        initial = _status(selected_key="other.gguf", selected_backend_id="other",
                          backend_loaded_model="other")
        # select-and-restart returns 200 but new status still shows mismatch
        still_other = _status(selected_key="other.gguf", selected_backend_id="other",
                              backend_loaded_model="other")
        with _MockProxy(
            status_response=initial,
            select_response=still_other,
            select_status=200,
        ) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("still not the active model", result.stderr)

    def test_proxy_unreachable_exits_3(self):
        # Use a port no one is listening on
        result = _run_preflight(1, "kuato.gguf")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("GET /qz/model/status failed", result.stderr)

    def test_mismatch_when_selected_loaded_mismatch_true(self):
        """Selected matches request, but loaded doesn't — must fail (we go by loaded)."""
        status = _status(selected_key="kuato.gguf", selected_backend_id="kuato",
                         backend_loaded_model="other", mismatch=True)
        with _MockProxy(status_response=status) as proxy:
            result = _run_preflight(proxy.port, "kuato.gguf", autoselect=False)
        self.assertEqual(result.returncode, 1, result.stderr)


def _pending_posts(proxy: _MockProxy) -> list:
    return proxy.posts


if __name__ == "__main__":
    unittest.main()
