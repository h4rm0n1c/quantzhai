#!/usr/bin/env python3
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.quantzhai_proxy import ProxyHandler  # noqa: E402


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, _fmt, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append({"path": self.path, "body": body})

        response = {
            "id": "resp_fake_apply_patch",
            "object": "response",
            "created_at": 4102444800,
            "model": body.get("model", "fake"),
            "output": [{
                "id": "fc_fake_apply_patch",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_fake_apply_patch",
                "name": "apply_patch",
                "arguments": json.dumps({
                    "operation": {
                        "type": "create_file",
                        "path": "tmp/quantzhai-smoke.txt",
                        "diff": "@@\n+quantzhai apply_patch smoke\n",
                    }
                }),
            }],
            "usage": {},
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _free_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _post_json(url, payload, accept="application/json"):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": accept,
            "Authorization": "Bearer local",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


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
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), json.loads(resp.read().decode("utf-8"))


def main():
    upstream = _free_server(FakeUpstreamHandler)
    proxy = None
    try:
        ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
        ProxyHandler.reasoning_stream_format = "raw"
        ProxyHandler.searxng_policy = {}
        ProxyHandler.searxng_capabilities = {}
        ProxyHandler.searxng_base_url = None
        ProxyHandler.searxng_timeout = 1
        proxy = _free_server(ProxyHandler)

        payload = {
            "model": "QwenZhai-high",
            "stream": False,
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Create a tiny smoke-test file."}],
            }],
            "tools": [{"type": "apply_patch"}],
            "tool_choice": {"type": "apply_patch"},
        }

        status, content_type, raw = _post_json(f"http://127.0.0.1:{proxy.server_port}/v1/responses", payload)
        assert status == 200, status
        assert "application/json" in content_type, content_type
        response = json.loads(raw.decode("utf-8"))
        output = response.get("output") or []
        patch_calls = [item for item in output if item.get("type") == "apply_patch_call"]
        assert len(patch_calls) == 1, response
        assert patch_calls[0]["operation"]["type"] == "create_file", patch_calls[0]
        assert patch_calls[0]["operation"]["path"] == "tmp/quantzhai-smoke.txt", patch_calls[0]

        upstream_body = FakeUpstreamHandler.requests[-1]["body"]
        assert upstream_body["tools"][0]["type"] == "function", upstream_body["tools"]
        assert upstream_body["tools"][0]["name"] == "apply_patch", upstream_body["tools"]
        assert upstream_body["tool_choice"] == {"type": "function", "name": "apply_patch"}, upstream_body["tool_choice"]

        stream_payload = dict(payload)
        stream_payload["stream"] = True
        status, content_type, raw = _post_json_stream(
            f"http://127.0.0.1:{proxy.server_port}/v1/responses",
            stream_payload,
        )
        stream_text = raw.decode("utf-8")
        assert status == 200, status
        assert "text/event-stream" in content_type, content_type
        assert "apply_patch_call" in stream_text, stream_text
        assert "response.output_item.done" in stream_text, stream_text

        followup_payload = {
            "model": "QwenZhai-high",
            "input": [{
                "type": "apply_patch_call_output",
                "call_id": "call_fake_apply_patch",
                "status": "completed",
                "output": "Created tmp/quantzhai-smoke.txt",
            }],
            "tools": [{"type": "apply_patch"}],
        }
        _post_json(f"http://127.0.0.1:{proxy.server_port}/v1/responses", followup_payload)
        followup_body = FakeUpstreamHandler.requests[-1]["body"]
        assert followup_body["input"][0]["type"] == "function_call_output", followup_body["input"]
        assert followup_body["input"][0]["call_id"] == "call_fake_apply_patch", followup_body["input"]

        custom_payload = {
            "model": "QwenZhai-high",
            "stream": False,
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Create a tiny smoke-test file."}],
            }],
            "tools": [{"type": "custom", "name": "apply_patch"}],
            "tool_choice": {"type": "custom", "name": "apply_patch"},
        }
        status, content_type, raw = _post_json(f"http://127.0.0.1:{proxy.server_port}/v1/responses", custom_payload)
        assert status == 200, status
        assert "application/json" in content_type, content_type
        custom_response = json.loads(raw.decode("utf-8"))
        custom_calls = [item for item in custom_response.get("output") or [] if item.get("type") == "custom_tool_call"]
        assert len(custom_calls) == 1, custom_response
        assert custom_calls[0]["name"] == "apply_patch", custom_calls[0]
        assert "*** Add File: tmp/quantzhai-smoke.txt" in custom_calls[0]["input"], custom_calls[0]

        status, content_type, telemetry_state = _get_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/state")
        assert status == 200, status
        assert "application/json" in content_type, content_type
        assert telemetry_state["counters"]["request_started"] >= 1, telemetry_state

        status, content_type, telemetry_recent = _get_json(f"http://127.0.0.1:{proxy.server_port}/qz/telemetry/recent?limit=5")
        assert status == 200, status
        assert "application/json" in content_type, content_type
        assert telemetry_recent["events"], telemetry_recent

        print("ok apply_patch proxy smoke")
        print(f"proxy_port={proxy.server_port}")
        print(f"upstream_port={upstream.server_port}")
        print(f"patch_call={json.dumps(patch_calls[0], sort_keys=True)}")
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main()
