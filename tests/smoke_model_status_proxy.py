#!/usr/bin/env python3
import json
import sys
import threading
import urllib.request
import urllib.error
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
            "default": False,
        }, {
            "key": "model-b.gguf",
            "filename": "model-b.gguf",
            "stem": "model-b",
            "path": "/models/model-b.gguf",
            "label": "Model B",
            "name": "Model B",
            "context_length": 131072,
            "metadata": {},
            "default": True,
        }]
        self.selected = self.entries[1]

    def to_payload(self):
        return {"selected": self.selected, "entries": self.entries}

    def resolve(self, query):
        query = (query or "").strip()
        for entry in self.entries:
            if query in (entry["key"], entry["stem"], entry["label"], entry["name"]):
                return entry, f"matched {query}"
        return self.selected, f"matched {query or 'default'}"

    def select(self, requested):
        selected, reason = self.resolve(requested)
        self.selected = selected
        return selected, reason

    def to_v1_models(self, backend_models=None):
        return {"data": [{"id": entry["key"], "object": "model"} for entry in self.entries]}

    def to_ollama_models(self, backend_models=None):
        return [{"name": entry["key"], "modified_at": 4102444800} for entry in self.entries]


def _json_response(handler, status, payload):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class FakeBackendHandler(BaseHTTPRequestHandler):
    requests = []
    models = {}

    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):
        if self.path == "/health":
            _json_response(self, 200, {"status": "ok"})
            return
        if self.path == "/models":
            _json_response(self, 200, {
                "data": [
                    {
                        "id": model_id,
                        "status": {"value": entry.get("status", "unknown")},
                        "path": entry.get("path"),
                    }
                    for model_id, entry in self.__class__.models.items()
                ],
            })
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        self.__class__.requests.append({"path": self.path, "body": body})
        if self.path == "/models/load":
            model_id = body.get("model") or ""
            if model_id in self.__class__.models:
                self.__class__.models[model_id]["status"] = "loaded"
            _json_response(self, 200, {"success": True})
            return
        if self.path == "/models/unload":
            model_id = body.get("model") or ""
            if model_id in self.__class__.models:
                self.__class__.models[model_id]["status"] = "unloaded"
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
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        payload = json.loads(body) if body else {}
        return exc.code, exc.headers.get("Content-Type", ""), payload


def main():
    upstream = _free_server(FakeBackendHandler)
    proxy = None
    try:
        FakeBackendHandler.requests = []
        FakeBackendHandler.models = {
            "model-a.gguf": {
                "status": "unloaded",
                "path": "/models/model-a.gguf",
            },
            "model-b.gguf": {
                "status": "loaded",
                "path": "/models/model-b.gguf",
            },
        }
        ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
        ProxyHandler.reasoning_stream_format = "raw"
        ProxyHandler.runtime_state_enabled = True
        ProxyHandler.model_catalog = FakeCatalog()
        ProxyHandler.model_catalog_path = "/tmp/fake-model-catalog.json"
        ProxyHandler.model_load_state = "idle"
        ProxyHandler.model_load_model = None
        ProxyHandler.model_load_started_at = None
        ProxyHandler.model_load_finished_at = None
        ProxyHandler.model_load_error = None
        proxy = _free_server(ProxyHandler)

        status, content_type, ready = _request_json(f"http://127.0.0.1:{proxy.server_port}/ready")
        assert status == 200, ready
        assert "application/json" in content_type, content_type
        assert ready["ready"] is True, ready
        assert ready["load"]["state"] == "loaded", ready

        status, _, snapshot = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/status")
        assert status == 200, snapshot
        assert snapshot["router_mode"] is True, snapshot
        assert snapshot["selected"]["key"] == "model-b.gguf", snapshot

        status, _, telemetry = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=5")
        assert status == 200, telemetry
        assert any(event.get("type") == "status_snapshot" for event in telemetry.get("events", [])), telemetry

        status, _, out = _request_json(
            f"http://127.0.0.1:{proxy.server_port}/v1/responses",
            {
                "model": "model-a.gguf",
                "stream": False,
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }],
            },
        )
        assert status == 200, out
        assert out["model"] == "model-a.gguf", out
        assert len(FakeBackendHandler.requests) >= 1, FakeBackendHandler.requests
        assert [req["path"] for req in FakeBackendHandler.requests[:3]] == ["/models/unload", "/models/load", "/v1/responses"], FakeBackendHandler.requests
        assert FakeBackendHandler.requests[0]["body"].get("model") == "model-b.gguf", FakeBackendHandler.requests
        assert FakeBackendHandler.requests[1]["body"].get("model") == "model-a.gguf", FakeBackendHandler.requests
        assert FakeBackendHandler.requests[-1]["path"] == "/v1/responses", FakeBackendHandler.requests
        sent = FakeBackendHandler.requests[-1]["body"]
        assert sent["model"] == "model-a.gguf", sent
        assert sent["instructions"].startswith("<QZSTATE"), sent
        assert sent["metadata"]["qz_runtime"]["ready"] is True, sent
        assert sent["metadata"]["qz_runtime"]["load_state"] == "ready", sent

        status, _, ready = _request_json(f"http://127.0.0.1:{proxy.server_port}/ready")
        assert status == 200, ready
        assert ready["ready"] is True, ready
        assert ready["load"]["state"] == "ready", ready
        assert ready["selected"]["key"] == "model-a.gguf", ready
    finally:
        if proxy is not None:
            proxy.shutdown()
        upstream.shutdown()


if __name__ == "__main__":
    main()
