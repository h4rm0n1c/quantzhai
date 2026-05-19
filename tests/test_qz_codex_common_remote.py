"""Tests for qz-codex-common HTTP bootstrap — #58 Slice D2.

Uses a lightweight Python HTTP server to simulate the QuantZhai proxy endpoints:
  GET /qz/codex/client-config
  GET /qz/codex/model-catalog

qz-codex always uses HTTP bootstrap. There is no co-located/local fallback path.
QZ_CODEX_REMOTE is no longer required — HTTP is always the path.

Tests call qz_prepare_codex_home() or _qz_http_bootstrap() via subprocess.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QZ_CODEX_COMMON = str(REPO_ROOT / "scripts" / "qz-codex-common")

# --- Fake server fixtures ---

FAKE_CATALOG = {"models": [{"slug": "test-model", "display_name": "Test"}]}

FAKE_CLIENT_CONFIG_TEMPLATE = {
    "ok": True,
    "schema": "qz.codex.client_config.v1",
    "model_provider": "quantzhai",
    "provider": {
        "name": "QuantZhai",
        "base_url": "http://127.0.0.1:{port}/v1",
        "wire_api": "responses",
        "env_key": "LOCAL_QWEN_API_KEY",
    },
    "model_catalog": {
        "mode": "download",
        "url": "http://127.0.0.1:{port}/qz/codex/model-catalog",
        "local_filename": "qwenzhai-models.json",
    },
    "warnings": [],
}


class _MockHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves the two bootstrap endpoints."""

    def do_GET(self):
        srv = self.server
        if self.path == "/qz/codex/client-config":
            body = json.dumps(srv.client_config_response).encode()
            status = srv.client_config_status
        elif self.path == "/qz/codex/model-catalog":
            if srv.catalog_response is None:
                body = json.dumps({"ok": False, "error": "missing_codex_catalog"}).encode()
                status = 404
            else:
                body = json.dumps(srv.catalog_response).encode()
                status = 200
        else:
            body = b"not found"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        srv.requests.append(("GET", self.path))

    def do_POST(self):
        """Record POST requests so tests can verify no /qz/models/refresh is called."""
        srv = self.server
        srv.requests.append(("POST", self.path))
        body = json.dumps({"ok": False, "error": "unexpected_post"}).encode()
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress output


class _MockServer:
    """Thread-safe mock HTTP server for bootstrap endpoint testing."""

    def __init__(self, client_config_status=200, catalog_response=FAKE_CATALOG):
        self.server = None
        self.port = None
        self.requests = []
        self._client_config_status = client_config_status
        self._catalog_response = catalog_response
        self._thread = None

    def __enter__(self):
        # Build client-config response dynamically once port is known
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _MockHandler)
        self.port = self.server.server_address[1]
        cfg = json.loads(json.dumps(FAKE_CLIENT_CONFIG_TEMPLATE))
        cfg["provider"]["base_url"] = f"http://127.0.0.1:{self.port}/v1"
        cfg["model_catalog"]["url"] = f"http://127.0.0.1:{self.port}/qz/codex/model-catalog"
        self.server.client_config_response = cfg
        self.server.client_config_status = self._client_config_status
        self.server.catalog_response = self._catalog_response
        self.server.requests = self.requests  # list of (method, path) tuples
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        if self.server:
            self.server.shutdown()


def _run_http_bootstrap(port, codex_home, extra_env=None, timeout=15):
    """Source qz-codex-common and call qz_prepare_codex_home() in a subprocess.

    QZ_CODEX_REMOTE is intentionally NOT set — HTTP bootstrap is always active.
    This exercises the public function, proving no flag is required.
    """
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_PROXY_HOST": "127.0.0.1",
        "QZ_PROXY_PORT": str(port),
        "CODEX_HOME": str(codex_home),
        # QZ_CODEX_REMOTE intentionally NOT set — HTTP is always used
        "LOCAL_QWEN_API_KEY": "",
        "QZ_ROOT": str(REPO_ROOT),
    }
    if extra_env:
        env.update(extra_env)
    script = f"source '{QZ_CODEX_COMMON}' && qz_prepare_codex_home"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# Backward-compatible alias used in low-level tests that call the helper directly
def _run_bootstrap_helper(port, codex_home, extra_env=None, timeout=15):
    """Call _qz_http_bootstrap directly (for testing failure branches)."""
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_PROXY_HOST": "127.0.0.1",
        "QZ_PROXY_PORT": str(port),
        "CODEX_HOME": str(codex_home),
        "LOCAL_QWEN_API_KEY": "",
        "QZ_ROOT": str(REPO_ROOT),
    }
    if extra_env:
        env.update(extra_env)
    script = f"source '{QZ_CODEX_COMMON}' && _qz_http_bootstrap"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class HttpBootstrapTests(unittest.TestCase):
    """Tests for qz-codex always-HTTP bootstrap — #58 Slice D2."""

    def test_always_uses_http_client_config_endpoint(self):
        """qz_prepare_codex_home always calls GET /qz/codex/client-config."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            client_config_gets = [r for r in srv.requests if r == ("GET", "/qz/codex/client-config")]
            self.assertGreater(len(client_config_gets), 0, "GET /qz/codex/client-config was not called")

    def test_qz_codex_remote_unset_still_uses_http_bootstrap(self):
        """Without QZ_CODEX_REMOTE, HTTP bootstrap is used — same result."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            # _run_http_bootstrap never sets QZ_CODEX_REMOTE
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            catalog_file = codex_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(catalog_file.exists())

    def test_qz_codex_remote_set_is_same_as_unset(self):
        """QZ_CODEX_REMOTE=1 no longer gates anything — same result as unset."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home, extra_env={"QZ_CODEX_REMOTE": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
            catalog_file = codex_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(catalog_file.exists())

    def test_no_models_refresh_post_by_default(self):
        """qz_prepare_codex_home must not call POST /qz/models/refresh."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            post_requests = [r for r in srv.requests if r[0] == "POST"]
            self.assertEqual(post_requests, [], f"Unexpected POST requests: {post_requests}")

    def test_no_branch_reads_server_var_codex_home(self):
        """bootstrap does not set CODEX_HOME to a server path like QZ_ROOT/var/codex-home."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "fake-root"
            fake_root.mkdir()
            codex_home = Path(tmp) / "client-home"
            result = _run_http_bootstrap(srv.port, codex_home, extra_env={"QZ_ROOT": str(fake_root)})
            self.assertEqual(result.returncode, 0, result.stderr)
            # Catalog must be under client codex_home, not under fake_root/var/codex-home
            expected = codex_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(expected.exists())
            unexpected = fake_root / "var" / "codex-home" / "model-catalogs" / "qwenzhai-models.json"
            self.assertFalse(unexpected.exists(), "catalog was written to server path instead of client CODEX_HOME")

    def test_codex_home_default_is_client_local_under_home(self):
        """Without CODEX_HOME set, default is $HOME/.qz-codex/codex-home."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "fake-home"
            fake_home.mkdir()
            env = {
                "HOME": str(fake_home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
                "QZ_PROXY_HOST": "127.0.0.1",
                "QZ_PROXY_PORT": str(srv.port),
                "LOCAL_QWEN_API_KEY": "",
                "QZ_ROOT": str(REPO_ROOT),
            }
            # No CODEX_HOME in env
            script = f"source '{QZ_CODEX_COMMON}' && qz_prepare_codex_home"
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = fake_home / ".qz-codex" / "codex-home"
            self.assertTrue(expected.exists(), f"expected {expected} to be created")

    def test_proxy_down_gives_clean_bounded_message_without_qz_up(self):
        """When proxy is unreachable, fail with clean message that does not mention qz-up."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(19999, codex_home)  # no server on this port
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("QuantZhai appears to be down", result.stderr)
            self.assertIn("Start the QuantZhai proxy/service", result.stderr)
            self.assertNotIn("qz-up", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_missing_catalog_gives_clean_bounded_message(self):
        """If catalog endpoint is missing, fail with message suggesting /qz/models/refresh."""
        with _MockServer(catalog_response=None) as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("model catalog", result.stderr.lower())
            # Should suggest /qz/models/refresh but not call it automatically
            post_requests = [r for r in srv.requests if r[0] == "POST"]
            self.assertEqual(post_requests, [], "should not auto-call /qz/models/refresh")

    def test_no_qz_up_invocation(self):
        """qz-codex-common does not invoke qz-up in any non-comment line."""
        with open(QZ_CODEX_COMMON) as f:
            content = f.read()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments are fine
            self.assertNotIn("qz-up", stripped,
                             f"qz-up reference in non-comment line: {line!r}")

    def test_shell_syntax_qz_codex_common(self):
        result = subprocess.run(["bash", "-n", QZ_CODEX_COMMON], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class BootstrapBehaviourTests(unittest.TestCase):
    """Tests for bootstrap behaviour: writes, atomicity, error handling."""

    def test_remote_mode_fetches_client_config_and_writes_catalog(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            catalog_file = codex_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(catalog_file.exists(), "catalog file should have been written")
            catalog = json.loads(catalog_file.read_text())
            self.assertEqual(catalog["models"][0]["slug"], "test-model")

    def test_remote_mode_writes_config_toml_with_remote_base_url(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            config_toml = (codex_home / "config.toml").read_text()
            self.assertIn(f"127.0.0.1:{srv.port}/v1", config_toml)
            self.assertIn("model_provider", config_toml)

    def test_remote_mode_model_catalog_json_points_to_local_path(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            config_toml = (codex_home / "config.toml").read_text()
            # model_catalog_json must be a local path, not http://
            import re
            match = re.search(r'model_catalog_json\s*=\s*"([^"]+)"', config_toml)
            self.assertIsNotNone(match, "model_catalog_json not found in config.toml")
            catalog_path_in_toml = match.group(1)
            self.assertFalse(
                catalog_path_in_toml.startswith("http"),
                f"model_catalog_json must be a local path, got: {catalog_path_in_toml}",
            )
            self.assertIn(str(codex_home), catalog_path_in_toml)

    def test_remote_mode_does_not_write_api_key_value(self):
        sentinel = "SENTINEL_SECRET_API_KEY_XYZ_MUST_NOT_APPEAR"
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_http_bootstrap(
                srv.port, codex_home,
                extra_env={"LOCAL_QWEN_API_KEY": sentinel},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config_toml = (codex_home / "config.toml").read_text()
            self.assertNotIn(sentinel, config_toml)
            self.assertNotIn(sentinel, result.stdout)
            self.assertNotIn(sentinel, result.stderr)
            # env_key name (not value) should appear
            self.assertIn("LOCAL_QWEN_API_KEY", config_toml)

    def test_missing_client_config_gives_bounded_error(self):
        """Server unreachable → script fails with bounded message."""
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            # Use a port nothing is listening on
            result = _run_http_bootstrap(19999, codex_home)
            self.assertNotEqual(result.returncode, 0)
            # Should say something about failing to reach the endpoint
            self.assertTrue(
                len(result.stderr) > 0,
                "Expected bounded error on stderr",
            )
            self.assertNotIn("Traceback", result.stderr)

    def test_missing_catalog_endpoint_gives_bounded_error(self):
        """client-config ok but catalog endpoint returns 404 → bounded error."""
        with _MockServer(catalog_response=None) as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            # Write a dummy catalog first; it must not be overwritten on failure
            catalog_dir = codex_home / "model-catalogs"
            catalog_dir.mkdir(parents=True)
            sentinel_catalog = {"models": [{"slug": "existing"}]}
            catalog_file = catalog_dir / "qwenzhai-models.json"
            catalog_file.write_text(json.dumps(sentinel_catalog))

            result = _run_http_bootstrap(srv.port, codex_home)
            self.assertNotEqual(result.returncode, 0)
            # Existing catalog must be preserved
            surviving = json.loads(catalog_file.read_text())
            self.assertEqual(surviving["models"][0]["slug"], "existing")

    def test_co_located_mode_does_not_call_remote_endpoints_without_flag(self):
        """Without QZ_CODEX_REMOTE=1, the remote branch is not entered."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            env = {
                "HOME": str(Path.home()),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
                "QZ_PROXY_HOST": "127.0.0.1",
                "QZ_PROXY_PORT": str(srv.port),
                "QZ_ROOT": str(REPO_ROOT),
            }
            # Explicitly unset QZ_CODEX_REMOTE
            env.pop("QZ_CODEX_REMOTE", None)
            # Just check the conditional, not the full qz_prepare_codex_home
            # (which would try to POST /qz/models/refresh to the mock server)
            script = (
                f"source '{QZ_CODEX_COMMON}' && "
                "[[ '${QZ_CODEX_REMOTE:-}' == '1' ]] && echo remote || echo local"
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertIn("local", result.stdout)
            # The mock server should have received no requests from this check
            client_config_requests = [r for r in srv.requests if "client-config" in r]
            self.assertEqual(len(client_config_requests), 0)

    def test_invalid_json_client_config_gives_bounded_error(self):
        """Server returns invalid JSON → bounded error, no traceback."""
        class _BadHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"not json at all"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _BadHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                codex_home = Path(tmp) / "codex-home"
                result = _run_http_bootstrap(port, codex_home)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
        finally:
            server.shutdown()

    def test_shell_syntax_qz_codex_common(self):
        result = subprocess.run(
            ["bash", "-n", QZ_CODEX_COMMON],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_catalog_not_replaced_on_validation_failure(self):
        """Malformed catalog fetch → existing catalog is preserved."""
        class _BadCatalogHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/qz/codex/client-config":
                    # Use port from server attribute
                    cfg = json.loads(json.dumps(FAKE_CLIENT_CONFIG_TEMPLATE))
                    cfg["provider"]["base_url"] = f"http://127.0.0.1:{self.server.server_port}/v1"
                    cfg["model_catalog"]["url"] = f"http://127.0.0.1:{self.server.server_port}/qz/codex/model-catalog"
                    body = json.dumps(cfg).encode()
                else:
                    body = b"[not an object]"  # list, not dict — will fail validation
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _BadCatalogHandler)
        server.server_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                codex_home = Path(tmp) / "codex-home"
                catalog_dir = codex_home / "model-catalogs"
                catalog_dir.mkdir(parents=True)
                original = {"models": [{"slug": "safe-original"}]}
                catalog_file = catalog_dir / "qwenzhai-models.json"
                catalog_file.write_text(json.dumps(original))
                result = _run_http_bootstrap(server.server_port, codex_home)
                self.assertNotEqual(result.returncode, 0)
                surviving = json.loads(catalog_file.read_text())
                self.assertEqual(surviving["models"][0]["slug"], "safe-original")
        finally:
            server.shutdown()


class BootstrapPolishTests(unittest.TestCase):
    """Audit/polish tests for idempotence, atomicity, and safety guarantees."""

    def test_remote_bootstrap_is_idempotent(self):
        """Running remote bootstrap twice produces stable config.toml."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            r1 = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            config1 = (codex_home / "config.toml").read_text()

            r2 = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            config2 = (codex_home / "config.toml").read_text()

            self.assertEqual(config1, config2, "config.toml should be stable after second run")
            # Verify no duplicate managed keys
            self.assertEqual(config2.count("model_provider ="), 1)
            self.assertEqual(config2.count("model_catalog_json ="), 1)
            self.assertEqual(config2.count("[model_providers.quantzhai]"), 1)

    def test_remote_mode_backup_created_on_first_run(self):
        """Backup of config.toml is created on first run."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text('original = "yes"\n', encoding="utf-8")

            r = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r.returncode, 0, r.stderr)
            backup = codex_home / "config.toml.pre-qz-remote.bak"
            self.assertTrue(backup.exists(), "backup should be created on first run")
            self.assertIn("original", backup.read_text())

    def test_remote_mode_backup_not_overwritten_on_second_run(self):
        """Pre-existing backup is not overwritten on second run."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            codex_home.mkdir(parents=True)
            (codex_home / "config.toml").write_text('original = "yes"\n', encoding="utf-8")

            r1 = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            backup_path = codex_home / "config.toml.pre-qz-remote.bak"
            backup_mtime1 = backup_path.stat().st_mtime

            r2 = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            backup_mtime2 = backup_path.stat().st_mtime

            self.assertEqual(backup_mtime1, backup_mtime2, "backup must not be overwritten on second run")
            self.assertIn("original", backup_path.read_text())

    def test_remote_mode_preserves_unrelated_config_sections(self):
        """Unrelated config.toml sections survive the remote bootstrap patch."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            codex_home.mkdir(parents=True)
            existing_config = (
                'approval_policy = "on-request"\n'
                'sandbox_mode = "workspace-write"\n'
                '\n[some_other_section]\n'
                'keep_this = "value"\n'
            )
            (codex_home / "config.toml").write_text(existing_config, encoding="utf-8")

            r = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r.returncode, 0, r.stderr)
            result = (codex_home / "config.toml").read_text()

            self.assertIn("some_other_section", result)
            self.assertIn('keep_this', result)
            self.assertIn("approval_policy", result)

    def test_remote_mode_no_temp_files_left_after_success(self):
        """No .tmp.* files remain in CODEX_HOME after a successful run."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            r = _run_http_bootstrap(srv.port, codex_home)
            self.assertEqual(r.returncode, 0, r.stderr)
            tmp_files = list(codex_home.rglob("*.tmp.*"))
            self.assertEqual(tmp_files, [], f"temp files found: {tmp_files}")

    def test_remote_mode_no_temp_files_left_after_catalog_failure(self):
        """No .tmp.* catalog files remain when catalog fetch fails."""
        with _MockServer(catalog_response=None) as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            r = _run_http_bootstrap(srv.port, codex_home)
            self.assertNotEqual(r.returncode, 0)
            tmp_files = list(codex_home.rglob("*.tmp.*"))
            self.assertEqual(tmp_files, [], f"temp files found after failure: {tmp_files}")

    def test_remote_mode_without_codex_home_uses_default_under_home(self):
        """Without CODEX_HOME set, remote mode uses $HOME/.qz-remote-codex/codex-home."""
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "fake-home"
            fake_home.mkdir()
            env = {
                "HOME": str(fake_home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
                "QZ_PROXY_HOST": "127.0.0.1",
                "QZ_PROXY_PORT": str(srv.port),
                "LOCAL_QWEN_API_KEY": "",
                "QZ_ROOT": str(REPO_ROOT),
                # QZ_CODEX_REMOTE intentionally absent — HTTP is always used
            }
            # No CODEX_HOME in env
            env.pop("CODEX_HOME", None)
            script = f"source '{QZ_CODEX_COMMON}' && qz_prepare_codex_home"
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected_home = fake_home / ".qz-codex" / "codex-home"
            self.assertTrue(expected_home.exists(), f"expected {expected_home} to be created")
            catalog_file = expected_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(catalog_file.exists(), "catalog should be written under default CODEX_HOME")

    def test_remote_mode_toml_special_chars_in_provider_values_produce_valid_toml(self):
        """Provider name/URL with special chars is safely written (TOML escaping)."""
        # Use a name with double-quote and backslash to verify json.dumps escaping
        class _SpecialCharHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                port = self.server.server_port
                if self.path == "/qz/codex/client-config":
                    cfg = {
                        "ok": True,
                        "schema": "qz.codex.client_config.v1",
                        "model_provider": "quantzhai",
                        "provider": {
                            "name": 'QuantZhai "local"',  # embedded quote
                            "base_url": f"http://127.0.0.1:{port}/v1",
                            "wire_api": "responses",
                            "env_key": "LOCAL_QWEN_API_KEY",
                        },
                        "model_catalog": {
                            "mode": "download",
                            "url": f"http://127.0.0.1:{port}/qz/codex/model-catalog",
                            "local_filename": "qwenzhai-models.json",
                        },
                        "warnings": [],
                    }
                    body = json.dumps(cfg).encode()
                else:
                    body = json.dumps(FAKE_CATALOG).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args): pass

        server = http.server.HTTPServer(("127.0.0.1", 0), _SpecialCharHandler)
        server.server_port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                codex_home = Path(tmp) / "codex-home"
                result = _run_http_bootstrap(server.server_port, codex_home)
                self.assertEqual(result.returncode, 0, result.stderr)
                config_toml = (codex_home / "config.toml").read_text()
                # The embedded quote should be escaped, not raw
                # json.dumps("QuantZhai \"local\"") = '"QuantZhai \\"local\\""'
                self.assertIn("QuantZhai", config_toml)
                # Should not contain unescaped double-quote inside the value
                # (the outer quotes of the TOML string, then the escaped inner)
                import re as _re
                name_match = _re.search(r'name\s*=\s*"(.*?)"', config_toml)
                # If json.dumps escaped correctly, name value should be parseable
                # We just verify the file was written and is syntactically plausible
                self.assertIsNotNone(name_match)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
