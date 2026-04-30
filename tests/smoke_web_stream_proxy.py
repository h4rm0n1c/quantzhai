#!/usr/bin/env python3
import json
import sys
import time
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.quantzhai_proxy import ProxyHandler  # noqa: E402


def _sse_block(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


class FakeWebUpstreamHandler(BaseHTTPRequestHandler):
    requests = []
    models = {}

    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/models":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({
                "data": [
                    {
                        "id": model_id,
                        "status": {"value": entry.get("status", "unknown")},
                        "path": entry.get("path"),
                    }
                    for model_id, entry in self.__class__.models.items()
                ],
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        payload = json.dumps({"error": "not found"}).encode("utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append({"path": self.path, "body": body})
        if self.path == "/models/unload":
            model_id = body.get("model") or ""
            self.__class__.models.setdefault(model_id, {"path": f"/models/{model_id}"})
            self.__class__.models[model_id]["status"] = "unloaded"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"success": True}).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/models/load":
            model_id = body.get("model") or ""
            self.__class__.models.setdefault(model_id, {"path": f"/models/{model_id}"})
            self.__class__.models[model_id]["status"] = "loaded"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            payload = json.dumps({"success": True}).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        has_tool_output = any(
            isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == "call_web"
            for item in body.get("input") or []
        )

        if has_tool_output:
            chunks = [
                _sse_block("response.created", {
                    "response": {
                        "id": "resp_fake_final",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "in_progress",
                        "model": body.get("model", "fake"),
                        "output": [],
                    },
                }),
                _sse_block("response.output_item.added", {
                    "output_index": 0,
                    "item": {
                        "id": "msg_final",
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }),
                _sse_block("response.content_part.added", {
                    "item_id": "msg_final",
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }),
                _sse_block("response.output_text.delta", {
                    "item_id": "msg_final",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "searched.",
                }),
                _sse_block("response.output_item.done", {
                    "output_index": 0,
                    "item": {
                        "id": "msg_final",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "searched.", "annotations": []}],
                    },
                }),
                _sse_block("response.completed", {
                    "response": {
                        "id": "resp_fake_final",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "completed",
                        "model": body.get("model", "fake"),
                        "output": [{
                            "id": "msg_final",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "searched.", "annotations": []}],
                        }],
                        "usage": {
                            "input_tokens": 24,
                            "output_tokens": 8,
                            "total_tokens": 32,
                        },
                    },
                }),
                b"data: [DONE]\n\n",
            ]
        else:
            arguments = json.dumps({"action": "search", "query": "quantzhai"})
            chunks = [
                _sse_block("response.created", {
                    "response": {
                        "id": "resp_fake_web",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "in_progress",
                        "model": body.get("model", "fake"),
                        "output": [],
                    },
                }),
                _sse_block("response.output_item.added", {
                    "output_index": 0,
                    "item": {
                        "id": "fc_web",
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": "call_web",
                        "name": "web_search",
                        "arguments": "",
                    },
                }),
                _sse_block("response.function_call_arguments.delta", {
                    "item_id": "fc_web",
                    "output_index": 0,
                    "delta": arguments,
                }),
                _sse_block("response.output_item.done", {
                    "output_index": 0,
                    "item": {
                        "id": "fc_web",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_web",
                        "name": "web_search",
                    },
                }),
                b"data: [DONE]\n\n",
            ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()
            if chunk != b"data: [DONE]\n\n":
                time.sleep(0.01)


def _free_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _post_json_stream(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": "Bearer local",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        chunks = []
        while True:
            chunk = resp.readline()
            if not chunk:
                break
            chunks.append(chunk)
            if b"data: [DONE]" in b"".join(chunks):
                break
        return resp.status, resp.headers.get("Content-Type", ""), b"".join(chunks)


def _get_json(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    upstream = _free_server(FakeWebUpstreamHandler)
    proxy = None
    try:
        FakeWebUpstreamHandler.requests = []
        FakeWebUpstreamHandler.models = {
            "Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M": {
                "status": "loaded",
                "path": "/models/Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M",
            },
            "Qwen3.6-35B-A3B-uncensored-heretic-APEX-I-Compact": {
                "status": "unloaded",
                "path": "/models/Qwen3.6-35B-A3B-uncensored-heretic-APEX-I-Compact",
            },
        }
        ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
        ProxyHandler.reasoning_stream_format = "raw"
        ProxyHandler.searxng_policy = {}
        ProxyHandler.searxng_capabilities = {}
        ProxyHandler.searxng_base_url = None
        ProxyHandler.searxng_timeout = 1
        proxy = _free_server(ProxyHandler)

        payload = {
            "model": "QwenZhai-high",
            "stream": True,
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Search for QuantZhai."}],
            }],
            "tools": [{"type": "web_search"}],
        }

        status, content_type, raw = _post_json_stream(f"http://127.0.0.1:{proxy.server_port}/v1/responses", payload)
        stream_text = raw.decode("utf-8")
        assert status == 200, status
        assert "text/event-stream" in content_type, content_type
        assert "web_search_call" in stream_text, stream_text
        assert "function_call" not in stream_text, stream_text
        assert "searched." in stream_text, stream_text
        assert len(FakeWebUpstreamHandler.requests) == 4, FakeWebUpstreamHandler.requests
        assert FakeWebUpstreamHandler.requests[0]["path"] == "/models/unload", FakeWebUpstreamHandler.requests
        assert FakeWebUpstreamHandler.requests[1]["path"] == "/models/load", FakeWebUpstreamHandler.requests
        assert FakeWebUpstreamHandler.requests[2]["path"] == "/v1/responses", FakeWebUpstreamHandler.requests
        assert FakeWebUpstreamHandler.requests[3]["path"] == "/v1/responses", FakeWebUpstreamHandler.requests
        second_body = FakeWebUpstreamHandler.requests[3]["body"]
        assert any(item.get("type") == "function_call_output" for item in second_body.get("input") or []), second_body

        telemetry = _get_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=100")
        request_completed = next(
            event
            for event in telemetry["events"]
            if event.get("type") == "request_completed" and event.get("payload", {}).get("path") == "/v1/responses"
        )
        payload = request_completed.get("payload") or {}
        usage = payload.get("usage") or {}
        assert int(usage.get("total_tokens") or 0) > 0, request_completed
        assert int(usage.get("input_tokens") or 0) > 0, request_completed
        assert int(usage.get("output_tokens") or 0) > 0, request_completed
        assert float(payload.get("prompt_ms") or 0) > 0.0, request_completed
        assert float(payload.get("gen_ms") or 0) > 0.0, request_completed

        print("ok web stream proxy smoke")
        print(f"proxy_port={proxy.server_port}")
        print(f"upstream_port={upstream.server_port}")
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main()
