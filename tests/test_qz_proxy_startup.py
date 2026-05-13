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

    def _model_catalog(self):
        raise AssertionError("early control-plane routes must not load the model catalog")

    def _backend(self, authorization=None):
        raise AssertionError("early control-plane routes must not touch the backend")


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _request_json(url, payload=None):
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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else "{}"
        return exc.code, json.loads(body or "{}")


class ProxyStartupTest(unittest.TestCase):
    def test_control_plane_routes_answer_while_proxy_initializes(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"QZ_VAR_DIR": tmpdir}, clear=False):
            InitializingProxyHandler.root = str(Path(tmpdir))
            InitializingProxyHandler.model_state_path = str(Path(tmpdir) / "model-state.json")
            InitializingProxyHandler.backend_state_path = str(Path(tmpdir) / "backend-state.json")
            InitializingProxyHandler.telemetry = TelemetryBus()
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

                status, response = _request_json(f"{base}/v1/responses", {"model": "anything", "input": "hi"})
                self.assertEqual(status, 503)
                self.assertEqual(response["error"], "proxy initializing")
                self.assertEqual(response["initialization"]["state"], "initializing")
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
