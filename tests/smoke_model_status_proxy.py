#!/usr/bin/env python3
import json
import os
import sys
import tempfile
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
            "context_length": 262144,
            "metadata": {},
            "overrides": {"system_prompt_file": "var/prompts/model-a.md"},
            "default": False,
        }, {
            "key": "model-b.gguf",
            "filename": "model-b.gguf",
            "stem": "model-b",
            "path": "/models/model-b.gguf",
            "label": "Model B",
            "name": "Model B",
            "context_length": 262144,
            "metadata": {},
            "overrides": {"system_prompt_file": "var/prompts/model-b.md"},
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
                        "status": {
                            "value": entry.get("status", "unknown"),
                            "args": entry.get("args", []),
                            "preset": entry.get("preset", ""),
                        },
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
    old_var_dir = os.environ.get("QZ_VAR_DIR")
    old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["QZ_VAR_DIR"] = tmpdir
            os.environ["QZ_CAPTURE_MODE"] = "latest"
            model_state_path = f"{tmpdir}/model-state.json"
            backend_state_path = f"{tmpdir}/backend-state.json"
            with open(model_state_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "selected_key": "stale-profile.gguf",
                    "selected_backend_id": "missing-old-model",
                    "selected_path": "/models/missing-old-model.gguf",
                }, handle)
            with open(backend_state_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "selected_key": "stale-profile.gguf",
                    "selected_backend_id": "missing-old-model",
                    "selected_context_length": 262144,
                    "backend_context_length": 262144,
                    "state": "loaded",
                    "loaded_model": "missing-old-model",
                }, handle)

            FakeBackendHandler.requests = []
            FakeBackendHandler.models = {
                "model-a.gguf": {
                    "status": "unloaded",
                    "path": "/models/model-a.gguf",
                    "args": ["llama-server", "--ctx-size", "262144"],
                },
                "model-b.gguf": {
                    "status": "loaded",
                    "path": "/models/model-b.gguf",
                    "args": ["llama-server", "--ctx-size", "262144"],
                },
            }
            ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
            ProxyHandler.reasoning_stream_format = "raw"
            ProxyHandler.runtime_state_enabled = True
            ProxyHandler.model_catalog = FakeCatalog()
            ProxyHandler.model_catalog_path = "/tmp/fake-model-catalog.json"
            ProxyHandler.model_state_path = model_state_path
            ProxyHandler.backend_state_path = backend_state_path
            ProxyHandler.model_load_state = "failed"
            ProxyHandler.model_load_model = "missing-old-model"
            ProxyHandler.model_load_started_at = 1
            ProxyHandler.model_load_finished_at = 2
            ProxyHandler.model_load_error = "load failed: HTTP 404"
            proxy = _free_server(ProxyHandler)

            status, content_type, ready = _request_json(f"http://127.0.0.1:{proxy.server_port}/ready")
            assert status == 200, ready
            assert "application/json" in content_type, content_type
            assert ready["ready"] is True, ready
            assert ready["load"]["state"] == "loaded", ready
            assert ready["load"]["error"] is None, ready
            assert ready["backend"]["backend_context_length"] == 262144, ready
            assert ready["backend"]["backend_context_length_state"] == "confirmed", ready
            assert ready["backend"]["backend_context_length_source"] == "backend_inventory", ready
            assert ready["backend"]["selected_context_length_state"] == "intended", ready
            assert ready["backend"]["restart_required_state"] == "confirmed", ready

            with open(model_state_path, encoding="utf-8") as handle:
                reconciled_model_state = json.load(handle)
            with open(backend_state_path, encoding="utf-8") as handle:
                reconciled_backend_state = json.load(handle)
            assert reconciled_model_state["selected_key"] == "model-b.gguf", reconciled_model_state
            assert reconciled_model_state["selected_backend_id"] == "model-b.gguf", reconciled_model_state
            assert reconciled_backend_state["selected_key"] == "model-b.gguf", reconciled_backend_state
            assert reconciled_backend_state["selected_backend_id"] == "model-b.gguf", reconciled_backend_state
            assert reconciled_backend_state["backend_context_length"] == 262144, reconciled_backend_state
            assert reconciled_backend_state["loaded_model"] == "model-b.gguf", reconciled_backend_state

            status, _, snapshot = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/status")
            assert status == 200, snapshot
            assert snapshot["schema"] == "qz.status.snapshot.v1", snapshot
            assert snapshot["router_mode"] is True, snapshot
            assert snapshot["selected"]["key"] == "model-b.gguf", snapshot
            assert snapshot["backend"]["selected_context_length"] == 262144, snapshot
            assert snapshot["backend"]["backend_context_length"] == 262144, snapshot
            assert snapshot["backend"]["backend_reasoning_budget"] == -1, snapshot
            assert snapshot["backend"]["backend_context_length_state"] == "confirmed", snapshot
            assert snapshot["prompt"]["schema"] == "qz.prompt.status.v1", snapshot
            assert snapshot["prompt"]["files_missing"], snapshot

            status, _, telemetry = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=5")
            assert status == 200, telemetry
            assert telemetry["schema"] == "qz.telemetry.recent.v1", telemetry
            assert telemetry["state"]["schema"] == "qz.telemetry.state.v1", telemetry
            assert telemetry["state"]["runtime"]["schema"] == "qz.status.summary.v1", telemetry
            assert telemetry["state"]["runtime"]["selected_context_length"] == 262144, telemetry
            assert telemetry["state"]["runtime"]["backend_context_length"] == 262144, telemetry
            assert telemetry["state"]["runtime"]["backend_reasoning_budget"] == -1, telemetry
            assert any(event.get("type") == "status_snapshot" for event in telemetry.get("events", [])), telemetry

            status, _, telemetry_state = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/state")
            assert status == 200, telemetry_state
            assert telemetry_state["schema"] == "qz.telemetry.state.v1", telemetry_state
            assert telemetry_state["runtime"]["schema"] == "qz.status.summary.v1", telemetry_state
            assert telemetry_state["runtime"]["selected_context_length"] == 262144, telemetry_state
            assert telemetry_state["runtime"]["backend_context_length"] == 262144, telemetry_state
            assert telemetry_state["runtime"]["backend_reasoning_budget"] == -1, telemetry_state
            assert telemetry_state["runtime"]["selected_reasoning_level"] == "medium", telemetry_state

            status, _, config_report = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/config/effective")
            assert status == 200, config_report
            assert config_report["schema"] == "qz.config.effective.v1", config_report
            config_paths = {item["name"]: item for item in config_report["paths"]}
            assert config_paths["model_state"]["path"] == model_state_path, config_report
            assert config_paths["backend_state"]["path"] == backend_state_path, config_report
            assert config_paths["capture_dir"]["path"] == f"{tmpdir}/captures", config_report
            assert config_paths["codex_model_catalog"]["source_layer"] == "generated", config_report

            status, _, out = _request_json(
                f"http://127.0.0.1:{proxy.server_port}/v1/responses",
                {
                    "model": "model-a.gguf",
                    "stream": False,
                    "reasoning": {"effort": "high"},
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
            assert "<QZSTATE v=1 ready=1 load=ready ctx=262144 prof=model-a.gguf sel=model-a.gguf>" in sent["instructions"], sent
            assert "Reasoning effort: high." in sent["instructions"], sent
            assert sent["temperature"] == 0.6, sent
            assert sent["top_p"] == 0.95, sent
            assert sent["top_k"] == 20, sent
            assert sent["min_p"] == 0, sent
            assert sent["presence_penalty"] == 1.5, sent
            assert sent["repeat_penalty"] == 1.0, sent
            assert "repeat_last_n" not in sent, sent
            assert "dry_multiplier" not in sent, sent
            assert "thinking_budget_tokens" not in sent, sent
            assert sent["metadata"]["qz_runtime"]["schema"] == "qz.runtime.state.v1", sent
            assert sent["metadata"]["qz_runtime"]["ready"] is True, sent
            assert sent["metadata"]["qz_runtime"]["load_state"] == "ready", sent
            assert sent["metadata"]["qz_runtime"]["selected_backend_id"] == "model-a.gguf", sent
            assert sent["metadata"]["qz_runtime"]["selected_state"] == "loaded", sent
            assert sent["metadata"]["qz_runtime"]["backend_context_length_state"] == "confirmed", sent
            assert sent["metadata"]["qz_runtime"]["restart_required_state"] == "confirmed", sent
            assert sent["metadata"]["qz_request_id"].startswith("qz_req_"), sent
            assert sent["metadata"]["qz_reasoning"]["level"] == "high", sent
            assert sent["metadata"]["qz_reasoning"]["policy"] == "prompt", sent
            assert sent["metadata"]["qz_prompt_policy"]["mode"], sent

            status, _, telemetry_recent = _request_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=50")
            assert status == 200, telemetry_recent
            prompt_contracts = [
                (event.get("payload") or {})
                for event in telemetry_recent.get("events", [])
                if event.get("type") == "prompt_contract"
            ]
            assert prompt_contracts, telemetry_recent
            prompt_contract = prompt_contracts[-1]
            assert prompt_contract["schema"] == "qz.prompt.contract.v1", prompt_contract
            assert prompt_contract["request_id"].startswith("qz_req_"), prompt_contract
            assert prompt_contract["profile"] == "Model A", prompt_contract
            assert prompt_contract["requested_model"] == "model-a.gguf", prompt_contract
            assert prompt_contract["selected_backend_id"] == "model-a.gguf", prompt_contract
            assert prompt_contract["reasoning_level"] == "high", prompt_contract
            assert prompt_contract["prompt_policy"]["mode"], prompt_contract
            assert prompt_contract["prompt_policy"]["replacement_files_missing"], prompt_contract
            assert any(
                event.get("type") == "request_completed"
                and event.get("request_id")
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("selected_context_length") == 262144
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("schema") == "qz.runtime.metrics.v1"
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("reasoning_level") == "high"
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("active_reasoning_level") == "high"
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("selected_reasoning_level") == "medium"
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("runtime_truth_source") == "prompt_contract"
                and (event.get("payload") or {}).get("runtime_metrics", {}).get("prompt_contract", {}).get("requested_model") == "model-a.gguf"
                for event in telemetry_recent.get("events", [])
            ), telemetry_recent
            contract_path = f"{tmpdir}/captures/latest-request-contract.json"
            with open(contract_path, encoding="utf-8") as handle:
                capture_contract = json.load(handle)
            assert capture_contract["schema"] == "qz.capture.contract.v1", capture_contract
            assert capture_contract["request_id"] == prompt_contract["request_id"], capture_contract
            assert capture_contract["selected_backend_id"] == "model-a.gguf", capture_contract
            assert capture_contract["runtime_metrics"]["backend_context_length_state"] == "confirmed", capture_contract
            assert capture_contract["runtime_metrics"]["reasoning_level"] == "high", capture_contract
            assert capture_contract["runtime_metrics"]["selected_reasoning_level"] == "medium", capture_contract

            status, _, ready = _request_json(f"http://127.0.0.1:{proxy.server_port}/ready")
            assert status == 200, ready
            assert ready["schema"] == "qz.status.snapshot.v1", ready
            assert ready["ready"] is True, ready
            assert ready["load"]["state"] == "ready", ready
            assert ready["selected"]["key"] == "model-a.gguf", ready
            assert ready["latest_request"]["latest_completed_request_id"] == prompt_contract["request_id"], ready
    finally:
        if proxy is not None:
            proxy.shutdown()
        upstream.shutdown()
        if old_var_dir is None:
            os.environ.pop("QZ_VAR_DIR", None)
        else:
            os.environ["QZ_VAR_DIR"] = old_var_dir
        if old_capture_mode is None:
            os.environ.pop("QZ_CAPTURE_MODE", None)
        else:
            os.environ["QZ_CAPTURE_MODE"] = old_capture_mode


if __name__ == "__main__":
    main()
