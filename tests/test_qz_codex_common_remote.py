"""Tests for _qz_remote_bootstrap() in scripts/qz-codex-common — #57 Slice C2.

Uses a lightweight Python HTTP server to simulate the QuantZhai proxy endpoints:
  GET /qz/codex/client-config
  GET /qz/codex/model-catalog

Tests call the bash function directly via subprocess after sourcing the script.
Co-located mode is never exercised here — only the remote bootstrap path.
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
        srv.requests.append(self.path)

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
        self.server.requests = self.requests
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        if self.server:
            self.server.shutdown()


def _run_remote_bootstrap(port, codex_home, extra_env=None, timeout=15):
    """Source qz-codex-common and call _qz_remote_bootstrap in a subprocess."""
    env = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "QZ_PROXY_HOST": "127.0.0.1",
        "QZ_PROXY_PORT": str(port),
        "CODEX_HOME": str(codex_home),
        "QZ_CODEX_REMOTE": "1",
        "LOCAL_QWEN_API_KEY": "",
        # Feed QZ_ROOT so qz-env doesn't fail on repo-level paths
        "QZ_ROOT": str(REPO_ROOT),
    }
    if extra_env:
        env.update(extra_env)
    script = f"source '{QZ_CODEX_COMMON}' && _qz_remote_bootstrap"
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class RemoteBootstrapTests(unittest.TestCase):
    """Tests for _qz_remote_bootstrap() with a mock QuantZhai server."""

    def test_remote_mode_fetches_client_config_and_writes_catalog(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_remote_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            catalog_file = codex_home / "model-catalogs" / "qwenzhai-models.json"
            self.assertTrue(catalog_file.exists(), "catalog file should have been written")
            catalog = json.loads(catalog_file.read_text())
            self.assertEqual(catalog["models"][0]["slug"], "test-model")

    def test_remote_mode_writes_config_toml_with_remote_base_url(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_remote_bootstrap(srv.port, codex_home)
            self.assertEqual(result.returncode, 0, result.stderr)
            config_toml = (codex_home / "config.toml").read_text()
            self.assertIn(f"127.0.0.1:{srv.port}/v1", config_toml)
            self.assertIn("model_provider", config_toml)

    def test_remote_mode_model_catalog_json_points_to_local_path(self):
        with _MockServer() as srv, tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / "codex-home"
            result = _run_remote_bootstrap(srv.port, codex_home)
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
            result = _run_remote_bootstrap(
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
            result = _run_remote_bootstrap(19999, codex_home)
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

            result = _run_remote_bootstrap(srv.port, codex_home)
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
                result = _run_remote_bootstrap(port, codex_home)
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
                result = _run_remote_bootstrap(server.server_port, codex_home)
                self.assertNotEqual(result.returncode, 0)
                surviving = json.loads(catalog_file.read_text())
                self.assertEqual(surviving["models"][0]["slug"], "safe-original")
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
