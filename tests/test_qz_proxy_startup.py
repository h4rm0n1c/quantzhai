#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.quantzhai_proxy import ProxyHandler  # noqa: E402
from proxy.qz_telemetry import TelemetryBus  # noqa: E402


class InitializingProxyHandler(ProxyHandler):
    initialization_lock = threading.Lock()
    initialization_state = "initializing"
    initialization_error = None
    initialization_started_at = 123.0
    initialization_finished_at = None
    model_catalog = None
    model_catalog_path = None
    telemetry = TelemetryBus()
    root = ""
    model_state_path = ""
    backend_state_path = ""
    model_catalog_calls = 0
    backend_calls = 0

    def _model_catalog(self):
        self.__class__.model_catalog_calls += 1
        raise AssertionError("early control-plane routes must not load the model catalog")

    def _backend(self, authorization=None):
        self.__class__.backend_calls += 1
        raise AssertionError("early control-plane routes must not touch the backend")


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _request_json_with_headers(url, payload=None):
    if payload is None:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else "{}"
        return exc.code, dict(exc.headers), json.loads(body or "{}")


def _request_json(url, payload=None):
    status, _headers, body = _request_json_with_headers(url, payload)
    return status, body


class ProxyStartupTest(unittest.TestCase):
    def test_control_plane_routes_answer_while_proxy_initializes(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"QZ_VAR_DIR": tmpdir}, clear=False):
            InitializingProxyHandler.root = str(Path(tmpdir))
            InitializingProxyHandler.model_state_path = str(Path(tmpdir) / "model-state.json")
            InitializingProxyHandler.backend_state_path = str(Path(tmpdir) / "backend-state.json")
            InitializingProxyHandler.telemetry = TelemetryBus()
            InitializingProxyHandler.model_catalog_calls = 0
            InitializingProxyHandler.backend_calls = 0
            InitializingProxyHandler._set_initialization_state("initializing")
            server = _serve(InitializingProxyHandler)
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                status, health = _request_json(f"{base}/health")
                self.assertEqual(status, 200)
                self.assertEqual(health["status"], "initializing")
                self.assertEqual(health["proxy_initialization"]["state"], "initializing")
                self.assertEqual(health["catalog"]["status"], "initializing")

                status, telemetry = _request_json(f"{base}/qz/telemetry/recent?limit=5")
                self.assertEqual(status, 200)
                self.assertEqual(telemetry["state"]["runtime"]["schema"], "qz.status.summary.v1")
                self.assertEqual(telemetry["state"]["runtime"]["load_state"], "initializing")

                status, config = _request_json(f"{base}/qz/config/effective")
                self.assertEqual(status, 200)
                self.assertEqual(config["proxy_initialization"]["state"], "initializing")

                InitializingProxyHandler.model_catalog_calls = 0
                InitializingProxyHandler.backend_calls = 0
                status, headers, model_status = _request_json_with_headers(f"{base}/qz/model/status")
                self.assertEqual(status, 200)
                self.assertEqual(model_status["schema"], "qz.model_status.v1")
                self.assertFalse(model_status["selected_model_ready"])
                self.assertFalse(model_status["ready"])
                self.assertEqual(model_status["request_admission_state"], "starting")
                self.assertFalse(model_status["proxy_initialization"]["ready"])
                self.assertEqual(model_status["proxy_initialization"]["state"], "initializing")
                self.assertNotIn("Retry-After", headers)
                self.assertNotIn("retry_suggested", model_status)
                self.assertEqual(InitializingProxyHandler.model_catalog_calls, 0)
                self.assertEqual(InitializingProxyHandler.backend_calls, 0)

                status, headers, models = _request_json_with_headers(f"{base}/qz/models")
                self.assertEqual(status, 503)
                self.assertEqual(models["error"], "proxy initializing")
                self.assertNotIn("Retry-After", headers)
                self.assertNotIn("retry_suggested", models)

                status, headers, response = _request_json_with_headers(
                    f"{base}/v1/responses",
                    {"model": "anything", "input": "hi"},
                )
                self.assertEqual(status, 503)
                self.assertEqual(headers.get("Retry-After"), "5")
                # New schema: qz.responses.error.v1
                self.assertEqual(response.get("schema"), "qz.responses.error.v1")
                self.assertEqual(response["error"], "proxy not ready")
                self.assertIs(response["retry_suggested"], True)
                self.assertFalse(response["readiness"]["proxy_ready"])
                self.assertIn("proxy_initialization", response)
                self.assertEqual(response["proxy_initialization"]["state"], "initializing")
            finally:
                server.shutdown()
                server.server_close()

    def test_model_status_short_circuits_while_proxy_initializes(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"QZ_VAR_DIR": tmpdir}, clear=False):
            InitializingProxyHandler.root = str(Path(tmpdir))
            InitializingProxyHandler.model_state_path = str(Path(tmpdir) / "model-state.json")
            InitializingProxyHandler.backend_state_path = str(Path(tmpdir) / "backend-state.json")
            InitializingProxyHandler.telemetry = TelemetryBus()
            InitializingProxyHandler.model_catalog_calls = 0
            InitializingProxyHandler.backend_calls = 0
            InitializingProxyHandler._set_initialization_state("initializing")
            server = _serve(InitializingProxyHandler)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with patch("proxy.qz_model_status.build_model_status") as build_status:
                    status, model_status = _request_json(f"{base}/qz/model/status")

                self.assertEqual(status, 200)
                build_status.assert_not_called()
                self.assertEqual(model_status["schema"], "qz.model_status.v1")
                self.assertFalse(model_status["ready"])
                self.assertEqual(model_status["request_admission_state"], "starting")
                self.assertEqual(InitializingProxyHandler.model_catalog_calls, 0)
                self.assertEqual(InitializingProxyHandler.backend_calls, 0)
            finally:
                server.shutdown()
                server.server_close()

    def test_model_status_returns_normal_payload_when_proxy_ready(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"QZ_VAR_DIR": tmpdir}, clear=False):
            InitializingProxyHandler.root = str(Path(tmpdir))
            InitializingProxyHandler.model_state_path = str(Path(tmpdir) / "model-state.json")
            InitializingProxyHandler.backend_state_path = str(Path(tmpdir) / "backend-state.json")
            InitializingProxyHandler.telemetry = TelemetryBus()
            InitializingProxyHandler.model_catalog_calls = 0
            InitializingProxyHandler.backend_calls = 0
            InitializingProxyHandler._set_initialization_state("ready")
            server = _serve(InitializingProxyHandler)
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                status, model_status = _request_json(f"{base}/qz/model/status")
                self.assertEqual(status, 200)
                self.assertEqual(model_status["schema"], "qz.model_status.v1")
                self.assertTrue(model_status["ok"])
                self.assertFalse(model_status["selected_model_ready"])
                self.assertEqual(model_status["request_admission_state"], "unavailable")
                self.assertNotIn("proxy_initialization", model_status)
            finally:
                server.shutdown()
                server.server_close()

    def test_model_status_builder_failure_still_returns_200(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"QZ_VAR_DIR": tmpdir}, clear=False):
            InitializingProxyHandler.root = str(Path(tmpdir))
            InitializingProxyHandler.model_state_path = str(Path(tmpdir) / "model-state.json")
            InitializingProxyHandler.backend_state_path = str(Path(tmpdir) / "backend-state.json")
            InitializingProxyHandler.telemetry = TelemetryBus()
            InitializingProxyHandler.model_catalog_calls = 0
            InitializingProxyHandler.backend_calls = 0
            InitializingProxyHandler._set_initialization_state("ready")
            server = _serve(InitializingProxyHandler)
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with patch("proxy.qz_model_status.build_model_status", side_effect=RuntimeError("boom")):
                    status, model_status = _request_json(f"{base}/qz/model/status")

                self.assertEqual(status, 200)
                self.assertEqual(model_status["schema"], "qz.model_status.v1")
                self.assertFalse(model_status["ok"])
                self.assertEqual(model_status["error"], "boom")
                self.assertFalse(model_status["selected_model_ready"])
                self.assertEqual(model_status["request_admission_state"], "unavailable")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
