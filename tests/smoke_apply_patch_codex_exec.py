#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.quantzhai_proxy import ProxyHandler  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MODEL = "Qwen3.6Turbo-high"


def _sse_block(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


def _usage():
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }


class FakeCodexUpstreamHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, _fmt, *_args):
        return

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            payload = {
                "object": "list",
                "data": [{"id": MODEL, "object": "model", "owned_by": "quantzhai-smoke"}],
            }
            self._send_json(payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append({"path": self.path, "body": body})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if _has_tool_output(body.get("input")):
            chunks = [
                _sse_block("response.created", {
                    "response": {
                        "id": "resp_fake_final",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "in_progress",
                        "model": body.get("model", MODEL),
                        "output": [],
                    },
                }),
                _sse_block("response.output_item.added", {
                    "output_index": 0,
                    "item": {
                        "id": "msg_fake_final",
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }),
                _sse_block("response.content_part.added", {
                    "item_id": "msg_fake_final",
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }),
                _sse_block("response.output_text.delta", {
                    "item_id": "msg_fake_final",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "Patch smoke complete.",
                }),
                _sse_block("response.output_item.done", {
                    "output_index": 0,
                    "item": {
                        "id": "msg_fake_final",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Patch smoke complete.", "annotations": []}],
                    },
                }),
                _sse_block("response.completed", {
                    "response": {
                        "id": "resp_fake_final",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "completed",
                        "model": body.get("model", MODEL),
                        "output": [{
                            "id": "msg_fake_final",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Patch smoke complete.", "annotations": []}],
                        }],
                        "usage": _usage(),
                    },
                }),
                b"data: [DONE]\n\n",
            ]
        else:
            arguments = json.dumps({
                "operation": {
                    "type": "create_file",
                    "path": "codex-patch-smoke.txt",
                    "diff": "@@\n+quantzhai codex apply_patch smoke\n",
                }
            })
            chunks = [
                _sse_block("response.created", {
                    "response": {
                        "id": "resp_fake_apply_patch",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "in_progress",
                        "model": body.get("model", MODEL),
                        "output": [],
                    },
                }),
                _sse_block("response.output_item.added", {
                    "output_index": 0,
                    "item": {
                        "id": "fc_fake_apply_patch",
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": "call_fake_apply_patch",
                        "name": "apply_patch",
                        "arguments": "",
                    },
                }),
                _sse_block("response.function_call_arguments.delta", {
                    "item_id": "fc_fake_apply_patch",
                    "output_index": 0,
                    "delta": arguments,
                }),
                _sse_block("response.output_item.done", {
                    "output_index": 0,
                    "item": {
                        "id": "fc_fake_apply_patch",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_fake_apply_patch",
                        "name": "apply_patch",
                    },
                }),
                b"data: [DONE]\n\n",
            ]

        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()


def _has_tool_output(value):
    if isinstance(value, list):
        return any(_has_tool_output(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") in ("function_call_output", "apply_patch_call_output"):
        return True
    return any(_has_tool_output(item) for item in value.values())


def _free_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _write_codex_config(codex_home: Path, proxy_port: int):
    catalog_dir = codex_home / "model-catalogs"
    sqlite_dir = codex_home / "sqlite"
    catalog_dir.mkdir(parents=True)
    sqlite_dir.mkdir(parents=True)

    catalog_path = catalog_dir / "qwenzhai-models.json"
    catalog_path.write_text((ROOT / "config" / "qwenzhai-models.example.json").read_text(), encoding="utf-8")

    config = f"""
model_catalog_json = "{catalog_path}"
model = "{MODEL}"
model_provider = "quantzhai"
approval_policy = "never"
sandbox_mode = "workspace-write"
model_context_window = 131072
model_max_output_tokens = 4096

[model_providers.quantzhai]
name = "QuantZhai Smoke"
base_url = "http://127.0.0.1:{proxy_port}/v1"
wire_api = "responses"
env_key = "LOCAL_QWEN_API_KEY"
request_max_retries = 0
stream_max_retries = 0
stream_idle_timeout_ms = 120000
"""
    (codex_home / "config.toml").write_text(config.lstrip(), encoding="utf-8")


def main():
    upstream = _free_server(FakeCodexUpstreamHandler)
    proxy = None
    try:
        ProxyHandler.upstream = f"http://127.0.0.1:{upstream.server_port}"
        ProxyHandler.reasoning_stream_format = "raw"
        ProxyHandler.searxng_policy = {}
        ProxyHandler.searxng_capabilities = {}
        ProxyHandler.searxng_base_url = None
        ProxyHandler.searxng_timeout = 1
        proxy = _free_server(ProxyHandler)

        codex_home = Path(tempfile.mkdtemp(prefix="quantzhai-codex-home-"))
        workspace = Path(tempfile.mkdtemp(prefix="quantzhai-codex-patch-"))
        _write_codex_config(codex_home, proxy.server_port)

        env = os.environ.copy()
        env["CODEX_HOME"] = str(codex_home)
        env["CODEX_SQLITE_HOME"] = str(codex_home / "sqlite")
        env["LOCAL_QWEN_API_KEY"] = "local"

        command = [
            "codex",
            "exec",
            "-m",
            MODEL,
            "-C",
            str(workspace),
            "--skip-git-repo-check",
            "--full-auto",
            "--json",
            "--color",
            "never",
            "-",
        ]
        prompt = "Create codex-patch-smoke.txt with the exact text provided by the tool call."
        target = workspace / "codex-patch-smoke.txt"
        expected = "quantzhai codex apply_patch smoke\n"

        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        stdout_lines = []
        stderr_lines = []
        stdout_thread = threading.Thread(target=_read_lines, args=(process.stdout, stdout_lines), daemon=True)
        stderr_thread = threading.Thread(target=_read_lines, args=(process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        assert process.stdin is not None
        process.stdin.write(prompt)
        process.stdin.close()

        created = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if target.exists() and target.read_text(encoding="utf-8") == expected:
                created = True
            if process.poll() is not None:
                break
            time.sleep(0.1)

        if process.poll() is None:
            print("codex stdout:")
            print("".join(stdout_lines))
            print("codex stderr:")
            print("".join(stderr_lines))
            print(f"workspace={workspace}")
            print(f"codex_home={codex_home}")
            print(f"requests={json.dumps(FakeCodexUpstreamHandler.requests, indent=2)}")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise AssertionError("Codex CLI did not finish after apply_patch smoke")
        else:
            process.wait(timeout=5)

        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)

        if not created:
            print("codex stdout:")
            print("".join(stdout_lines))
            print("codex stderr:")
            print("".join(stderr_lines))
            print(f"workspace={workspace}")
            print(f"codex_home={codex_home}")
            print(f"requests={json.dumps(FakeCodexUpstreamHandler.requests, indent=2)}")
            raise AssertionError("Codex CLI did not create the apply_patch smoke file")

        if process.returncode != 0:
            print("codex stdout:")
            print("".join(stdout_lines))
            print("codex stderr:")
            print("".join(stderr_lines))
            print(f"workspace={workspace}")
            print(f"codex_home={codex_home}")
            print(f"requests={json.dumps(FakeCodexUpstreamHandler.requests, indent=2)}")
            raise AssertionError(f"Codex CLI exited nonzero: {process.returncode}")

        assert len(FakeCodexUpstreamHandler.requests) >= 2, FakeCodexUpstreamHandler.requests

        upstream_body = next(
            request["body"]
            for request in FakeCodexUpstreamHandler.requests
            if request["path"] == "/v1/responses"
        )
        upstream_tools = upstream_body.get("tools") or []
        assert any(tool.get("type") == "function" and tool.get("name") == "apply_patch" for tool in upstream_tools), upstream_tools

        followup_body = FakeCodexUpstreamHandler.requests[-1]["body"]
        assert _has_tool_output(followup_body.get("input")), followup_body.get("input")

        print("ok apply_patch codex exec smoke")
        print(f"workspace={workspace}")
        print(f"codex_home={codex_home}")
        print(f"requests={len(FakeCodexUpstreamHandler.requests)}")
        print(f"created={target}")
        print(f"codex_exit={process.returncode}")
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        upstream.shutdown()
        upstream.server_close()


def _read_lines(pipe, lines):
    if pipe is None:
        return
    try:
        for line in iter(pipe.readline, ""):
            lines.append(line)
    finally:
        pipe.close()


if __name__ == "__main__":
    main()
