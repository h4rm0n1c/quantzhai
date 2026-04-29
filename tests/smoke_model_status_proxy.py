#!/usr/bin/env python3
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy.quantzhai_proxy import ProxyHandler  # noqa: E402


class FakeCatalog:
    def __init__(self):
        self.entries = [{
            "key": "model-a.gguf",
            "filename": "model-a.gguf",
            "stem": "model-a",
            "path": "/models/model-a.gguf",
            "label": "Model A",
            "name": "Model A",
            "context_length": 131072,
            "metadata": {},
            "default": True,
        }]
        self.selected = self.entries[0]

    def to_payload(self):
        return {"selected": self.selected, "entries": self.entries}

    def resolve(self, query):
        return self.selected, f"matched {query}"

    def select(self, requested):
        return self.selected, f"matched {requested or 'default'}"

    def to_v1_models(self, backend_models=None):
        return {"data": [{"id": self.selected["key"], "object": "model"}]}

    def to_ollama_models(self, backend_models=None):
        return [{"name": self.selected["key"], "modified_at": 4102444800}]


def _json_response(handler, status, payload):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class FakeBackendHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return
        if self.path == "/models":
            _json_response(self, 200, {
                "data": [{
                    "id": "model-a.gguf",
                    "status": {"value": "loaded"},
                    "path": "/models/model-a.gguf",
                }],
            })
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        self.__class__.requests.append({"path": self.path, "body": body})
        if self.path == "/models/load":
            _json_response(self, 200, {"success": True})
            return
        if self.path == "/v1/responses":
            _json_response(self, 200, {
                "id": "resp_ok",
                "object": "response",
                "created_at": 4102444800,
                "model": body.get("model", "model-a.gguf"),
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }],
                "usage": {},
            })
            return
        _json_response(self, 404, {"error": "not found"})


def _free_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _request_json(url, payload=None):
    if payload is None:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
    else:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer local",
            },
        )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), json.loads(resp.read().decode("utf-8"))


def main():
    upstream = _free_server(FakeBackendHandler)
    proxy = None
    try:
        FakeBackendHandler.requests = []
        ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
        ProxyHandler.reasoning_stream_format = "raw"
        ProxyHandler.runtime_state_enabled = True
        ProxyHandler.model_catalog = FakeCatalog()
        ProxyHandler.model_catalog_path = "/tmp/fake-model-catalog.json"
        proxy = _free_server(ProxyHandler)

        status, content_type, ready = _request_json(f"http://127.0.0.1:{proxy.server_port}/ready")
        assert status == 200, ready
        assert "application/json" in content_type, content_type
        assert ready["ready"] is True, ready
        assert ready["load"]["state"] == "loaded", ready

        status, _, snapshot = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/status")
        assert status == 200, snapshot
        assert snapshot["router_mode"] is True, snapshot
        assert snapshot["selected"]["key"] == "model-a.gguf", snapshot

        status, _, telemetry = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=5")
        assert status == 200, telemetry
        assert any(event.get("type") == "status_snapshot" for event in telemetry.get("events", [])), telemetry

        status, _, out = _request_json(
            f"http://127.0.0.1:{proxy.server_port}/v1/responses",
            {
                "model": "QwenZhai-high",
                "stream": False,
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }],
            },
        )
        assert status == 200, out
        assert out["model"] == "QwenZhai-high", out
        assert len(FakeBackendHandler.requests) >= 1, FakeBackendHandler.requests
        assert FakeBackendHandler.requests[-1]["path"] == "/v1/responses", FakeBackendHandler.requests
        sent = FakeBackendHandler.requests[-1]["body"]
        assert sent["instructions"].startswith("<QZSTATE"), sent
        assert sent["metadata"]["qz_runtime"]["ready"] is True, sent
        assert sent["metadata"]["qz_runtime"]["load_state"] == "ready", sent
    finally:
        if proxy is not None:
            proxy.shutdown()
        upstream.shutdown()


if __name__ == "__main__":
    main()
