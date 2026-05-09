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
MODEL = "test-model.gguf"


class SmokeModelCatalog:
    def __init__(self):
        self.entries = [{
            "slug": MODEL,
            "key": MODEL,
            "backend_id": MODEL,
            "label": MODEL,
            "profile_valid": True,
            "runtime_context_length": 131072,
            "context_length": 131072,
        }]
        self.selected = self.entries[0]
        self.reason = "smoke"
        self.cache_path = Path("/tmp/quantzhai-smoke-model-catalog.json")

    def resolve(self, query=""):
        if not query or query == MODEL:
            return self.entries[0], "smoke"
        return None, f"no match for {query}"


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

    def _send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            prompt_text = _input_text(body.get("input")).lower()
            if "rename" in prompt_text or "move" in prompt_text:
                arguments = json.dumps({
                    "operation": {
                        "type": "move_file",
                        "path": "codex-patch-move-old.txt",
                        "destination": "codex-patch-move-new.txt",
                        "diff": "@@\n quantzhai codex apply_patch move smoke\n",
                    }
                })
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


def _input_text(value):
    if isinstance(value, list):
        return "\n".join(_input_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    if isinstance(text, str):
        return text
    return "\n".join(_input_text(item) for item in value.values())


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
    catalog_path = catalog_dir / "smoke-models.json"
    catalog = {
        "models": [
            {
                "slug": MODEL,
                "display_name": "QuantZhai apply_patch smoke",
                "description": "Hermetic QuantZhai apply_patch Codex smoke model",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [],
                "priority": 1000,
                "label": "QuantZhai apply_patch smoke",
                "default": True,
                "shell_type": "shell_command",
                "visibility": "list",
                "supported_in_api": True,
                "additional_speed_tiers": [],
                "availability_nux": None,
                "upgrade": None,
                "base_instructions": "",
                "model_messages": {
                    "instructions_template": "",
                    "instructions_variables": {
                        "personality_default": "",
                        "personality_friendly": "",
                    },
                },
                "supports_reasoning_summaries": True,
                "default_reasoning_summary": "none",
                "support_verbosity": True,
                "default_verbosity": "low",
                "apply_patch_tool_type": "freeform",
                "supports_parallel_tool_calls": True,
                "supports_image_detail_original": False,
                "effective_context_window_percent": 95,
                "experimental_supported_tools": [],
                "input_modalities": ["text"],
                "web_search_tool_type": "text",
                "supports_search_tool": False,
                "truncation_policy": {
                    "mode": "tokens",
                    "limit": 10000,
                },
            },
        ],
    }
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

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
        ProxyHandler.model_catalog = SmokeModelCatalog()
        ProxyHandler.model_catalog_path = str(ProxyHandler.model_catalog.cache_path)
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
        create_run = _run_codex_patch_command(
            command,
            env,
            prompt,
            workspace,
            target,
            lambda: target.exists() and target.read_text(encoding="utf-8") == expected,
        )

        old_target = workspace / "codex-patch-move-old.txt"
        new_target = workspace / "codex-patch-move-new.txt"
        old_target.write_text("quantzhai codex apply_patch move smoke\n", encoding="utf-8")
        move_run = _run_codex_patch_command(
            command,
            env,
            "Rename codex-patch-move-old.txt to codex-patch-move-new.txt using the patch tool.",
            workspace,
            new_target,
            lambda: (
                not old_target.exists()
                and new_target.exists()
                and new_target.read_text(encoding="utf-8") == "quantzhai codex apply_patch move smoke\n"
            ),
        )

        _assert_codex_lifecycle(create_run["events"], [target], workspace, codex_home, create_run)
        _assert_codex_lifecycle(move_run["events"], [old_target, new_target], workspace, codex_home, move_run)

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
        print(f"moved={old_target} -> {new_target}")
        print(f"codex_exit=create:{create_run['returncode']} move:{move_run['returncode']}")
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


def _run_codex_patch_command(command, env, prompt, workspace, target, success_check):
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

    succeeded = False
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if success_check():
            succeeded = True
        if process.poll() is not None:
            break
        time.sleep(0.1)

    if process.poll() is None:
        _dump_codex_run(stdout_lines, stderr_lines, workspace)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise AssertionError(f"Codex CLI did not finish after apply_patch smoke for {target}")
    process.wait(timeout=5)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    if not succeeded:
        _dump_codex_run(stdout_lines, stderr_lines, workspace)
        raise AssertionError(f"Codex CLI did not apply patch for {target}")

    if process.returncode != 0:
        _dump_codex_run(stdout_lines, stderr_lines, workspace)
        raise AssertionError(f"Codex CLI exited nonzero for {target}: {process.returncode}")

    return {
        "stdout": stdout_lines,
        "stderr": stderr_lines,
        "events": _parse_codex_jsonl(stdout_lines),
        "returncode": process.returncode,
    }


def _dump_codex_run(stdout_lines, stderr_lines, workspace):
    print("codex stdout:")
    print("".join(stdout_lines))
    print("codex stderr:")
    print("".join(stderr_lines))
    print(f"workspace={workspace}")
    print(f"requests={json.dumps(FakeCodexUpstreamHandler.requests, indent=2)}")


def _assert_codex_lifecycle(codex_events, targets, workspace, codex_home, run):
    started_patch_items = _codex_patch_items(codex_events, "item.started", targets)
    completed_patch_items = _codex_patch_items(codex_events, "item.completed", targets)
    turn_completed = next((event for event in codex_events if event.get("type") == "turn.completed"), {})
    usage = turn_completed.get("usage") if isinstance(turn_completed, dict) else None
    lifecycle_ok = (
        started_patch_items
        and completed_patch_items
        and started_patch_items[0].get("status") == "in_progress"
        and completed_patch_items[0].get("status") == "completed"
        and isinstance(usage, dict)
        and {"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"}.issubset(usage)
    )
    if not lifecycle_ok:
        print("codex stdout:")
        print("".join(run["stdout"]))
        print("codex stderr:")
        print("".join(run["stderr"]))
        print(f"workspace={workspace}")
        print(f"codex_home={codex_home}")
        print(f"requests={json.dumps(FakeCodexUpstreamHandler.requests, indent=2)}")
        print(f"codex_events={json.dumps(codex_events, indent=2)}")
        raise AssertionError(f"Codex JSONL did not expose apply_patch lifecycle and terminal usage for {targets}")


def _parse_codex_jsonl(lines):
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _codex_patch_items(events, event_type, targets):
    target_paths = {str(target) for target in targets}
    items = []
    for event in events:
        if event.get("type") != event_type:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"apply_patch_call", "custom_tool_call"} and item.get("name") in {None, "apply_patch"}:
            items.append(item)
            continue
        if item.get("type") == "file_change":
            changes = item.get("changes") or []
            if any(change.get("path") in target_paths for change in changes if isinstance(change, dict)):
                items.append(item)
    return items


if __name__ == "__main__":
    main()
