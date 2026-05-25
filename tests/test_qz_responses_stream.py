import json
import unittest
from pathlib import Path

from proxy.qz_proxy_tools import ProxyLocalToolExecutor, ProxyLocalToolRegistry
from proxy.qz_responses_stream import (
    ClientStreamDisconnected,
    ResponsesStreamRuntime,
    StreamDecision,
    StreamHopState,
    StreamRunState,
    _reasoning_only_abort_reason,
    _should_inject_context_pressure_signal,
    _should_inject_hop_budget_signal,
    _should_suppress_duplicate_response_start,
    _should_suppress_proxy_local_terminal,
)
from proxy.qz_telemetry import TelemetryBus
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import ToolLifecycleSpec

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "sse"


def _sse_block(event_type, payload):
    payload = dict(payload)
    payload.setdefault("type", event_type)
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(payload)}\n\n"
    ).encode("utf-8")


class FakeStream:
    def __init__(self, chunks):
        self._lines = []
        for chunk in chunks:
            self._lines.extend(chunk.splitlines(keepends=True))
        self.closed = False

    def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def close(self):
        self.closed = True


class TimeoutAfterChunksStream(FakeStream):
    def readline(self):
        if not self._lines:
            raise TimeoutError("simulated upstream read timeout")
        return self._lines.pop(0)


def _fixture_chunks(name):
    return [FIXTURE_DIR.joinpath(name).read_bytes()]


def _parse_sse_events(stream_text):
    events = []
    event_type = None
    data_lines = []
    for line in stream_text.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
            continue
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                payload = "[DONE]" if data == "[DONE]" else json.loads(data)
                events.append((event_type or (payload.get("type") if isinstance(payload, dict) else None), payload))
            event_type = None
            data_lines = []
    return events


class FakeWebRuntime:
    def __init__(self):
        self.calls = []

    def execute_web_search_call(self, call_item, counters, seen_signatures, request_id=""):
        self.calls.append({
            "call_item": call_item,
            "counters": dict(counters),
            "seen_signatures": set(seen_signatures),
            "request_id": request_id,
        })
        public_item = {
            "id": "wsc_1",
            "type": "web_search_call",
            "status": "completed",
            "call_id": call_item.get("call_id"),
            "action": {
                "type": "search",
                "queries": ["quantzhai"],
                "result_count": 1,
            },
        }
        output_item = {
            "type": "function_call_output",
            "call_id": call_item.get("call_id"),
            "output": json.dumps({"ok": True, "result": {"results": [{"title": "QuantZhai"}]}}),
        }
        return public_item, output_item, [{"url": "https://example.test", "title": "QuantZhai"}]


class ProbeProxyToolExecutor(ProxyLocalToolExecutor):
    function_name = "qz_probe"
    lifecycle = ToolLifecycleSpec(
        name="qz_probe",
        execution="proxy_local",
        public_item_type="qz_probe_call",
        telemetry_name="qz_probe",
        continuation_hops=2,
        lifecycle_event_prefix="response.qz_probe_call",
        lifecycle_start_stages=("in_progress", "working"),
        lifecycle_done_stages=("completed",),
    )

    def execute(self, call, context):
        return ToolContinuationResult(
            public_item={
                "id": "qz_probe_public",
                "type": "qz_probe_call",
                "status": "completed",
                "call_id": call.get("call_id"),
            },
            upstream_items=(
                call,
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": "{\"probe\":\"ok\"}",
                },
            ),
        )

    def started_public_item(self, call, public_index):
        return {
            "id": call.get("id") or f"qz_probe_{public_index}",
            "type": "qz_probe_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }


def _web_call_stream():
    arguments = json.dumps({"action": "search", "query": "quantzhai"})
    return _named_web_call_stream("fc_web", "call_web", arguments)


def _probe_call_stream():
    return _named_function_call_stream("fc_probe", "call_probe", "qz_probe", json.dumps({"value": 1}))


def _named_web_call_stream(item_id, call_id, arguments):
    return _named_function_call_stream(item_id, call_id, "web_search", arguments)


def _named_function_call_stream(item_id, call_id, name, arguments):
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_web",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "function_call",
                "status": "in_progress",
                "call_id": call_id,
                "name": name,
                "arguments": "",
            },
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": item_id,
            "output_index": 0,
            "delta": arguments,
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _final_message_stream(usage=None, text="searched."):
    usage = usage if isinstance(usage, dict) else {}
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_final",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
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
        _sse_block("response.output_text.delta", {
            "item_id": "msg_final",
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_final",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }],
                "usage": usage,
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _failed_without_answer_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_failed",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.failed", {
            "response": {
                "id": "resp_fake_failed",
                "object": "response",
                "created_at": 4102444800,
                "status": "failed",
                "model": "fake",
                "output": [],
                "error": {"message": "upstream failed"},
            },
        }),
    ]


def _visible_output_without_terminal_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_terminal_timeout",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "msg_terminal_timeout",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_terminal_timeout",
            "output_index": 0,
            "content_index": 0,
            "delta": "hello",
        }),
    ]


def _reasoning_message_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_reasoning",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "rs_fake",
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
            },
        }),
        _sse_block("response.reasoning_text.delta", {
            "item_id": "rs_fake",
            "output_index": 0,
            "content_index": 0,
            "delta": "thinking",
        }),
        _sse_block("response.output_item.added", {
            "output_index": 1,
            "item": {
                "id": "msg_fake",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_fake",
            "output_index": 1,
            "content_index": 0,
            "delta": "stream ok",
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_reasoning",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "rs_fake",
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "thinking"}],
                }, {
                    "id": "msg_fake",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "stream ok", "annotations": []}],
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _multi_delta_message_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_multi_delta",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "msg_multi",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_multi",
            "output_index": 0,
            "content_index": 0,
            "delta": "alpha ",
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_multi",
            "output_index": 0,
            "content_index": 0,
            "delta": "beta",
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_multi_delta",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "msg_multi",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "alpha beta", "annotations": []}],
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _long_reasoning_then_message_stream(delta_count=20):
    chunks = [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_long_reasoning",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "rs_long",
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
            },
        }),
    ]
    for index in range(delta_count):
        chunks.append(_sse_block("response.reasoning_text.delta", {
            "item_id": "rs_long",
            "output_index": 0,
            "content_index": 0,
            "delta": f"generated-artifact-body-{index}-",
        }))
    chunks.extend([
        _sse_block("response.output_item.added", {
            "output_index": 1,
            "item": {
                "id": "msg_long",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_long",
            "output_index": 1,
            "content_index": 0,
            "delta": "final answer",
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_long_reasoning",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "rs_long",
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "generated artifact body"}],
                }, {
                    "id": "msg_long",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "final answer", "annotations": []}],
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ])
    return chunks


def _stuck_reasoning_only_stream(delta_count=5):
    chunks = [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_stuck_reasoning",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "rs_stuck",
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
            },
        }),
    ]
    for index in range(delta_count):
        chunks.append(_sse_block("response.reasoning_text.delta", {
            "item_id": "rs_stuck",
            "output_index": 0,
            "content_index": 0,
            "delta": f"draft-{index}-",
        }))
    return chunks


def _reasoning_only_completed_stream(delta="answer-shaped reasoning"):
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_reasoning_only_completed",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "rs_done",
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
            },
        }),
        _sse_block("response.reasoning_text.delta", {
            "item_id": "rs_done",
            "output_index": 0,
            "content_index": 0,
            "delta": delta,
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_reasoning_only_completed",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "rs_done",
                    "type": "reasoning",
                    "status": "completed",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": delta}],
                }, {
                    "id": "msg_empty",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "", "annotations": []}],
                }],
                "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _empty_completed_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_empty",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_empty",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _whitespace_output_text_completed_stream(delta=" \n\t"):
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_whitespace",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "msg_ws",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_ws",
            "output_index": 0,
            "content_index": 0,
            "delta": delta,
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_whitespace",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "msg_ws",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "", "annotations": []}],
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _public_item_completed_stream():
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_public_item",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "public_1",
                "type": "custom_tool_call",
                "status": "in_progress",
                "call_id": "call_public",
                "name": "external_tool",
                "input": "{}",
            },
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "public_1",
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": "call_public",
                "name": "external_tool",
                "input": "{}",
            },
        }),
        _sse_block("response.completed", {
            "response": {
                "id": "resp_fake_public_item",
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "public_1",
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": "call_public",
                    "name": "external_tool",
                    "input": "{}",
                }],
                "usage": {},
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _reasoning_tool_artifact_stream():
    artifact = (
        'json\n'
        '{\n'
        '  "operation": {\n'
        '    "type": "update_file",\n'
        '    "path": "sample_v4.md",\n'
        '    "diff": "--- a/sample_v4.md\\n+++ b/sample_v4.md\\n@@ -1,1 +1,2 @@\\n old\\n+new\\n"\n'
        '  }\n'
        '}\n'
    )
    chunks = [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_reasoning_artifact",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "rs_artifact",
                "type": "reasoning",
                "status": "in_progress",
                "summary": [],
                "content": [],
            },
        }),
    ]
    for start in range(0, len(artifact), 16):
        chunks.append(_sse_block("response.reasoning_text.delta", {
            "item_id": "rs_artifact",
            "output_index": 0,
            "content_index": 0,
            "delta": artifact[start:start + 16],
        }))
    return chunks


def _apply_patch_call_stream():
    arguments = json.dumps({
        "operation": {
            "type": "create_file",
            "path": "tmp/quantzhai-smoke.txt",
            "diff": "@@\n+quantzhai apply_patch smoke\n",
        }
    })
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_apply_patch",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_patch",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_patch",
                "name": "apply_patch",
                "arguments": "",
            },
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_patch",
            "output_index": 0,
            "delta": arguments,
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_patch",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_patch",
                "name": "apply_patch",
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _exec_command_call_stream():
    arguments = json.dumps({"cmd": "cat > sample_v2.md", "yield_time_ms": 1000})
    midpoint = len(arguments) // 2
    return [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_exec",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_exec",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_exec",
                "name": "exec_command",
                "arguments": "",
            },
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_exec",
            "output_index": 0,
            "delta": arguments[:midpoint],
        }),
        _sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_exec",
            "output_index": 0,
            "delta": arguments[midpoint:],
        }),
        _sse_block("response.function_call_arguments.done", {
            "item_id": "fc_exec",
            "output_index": 0,
            "arguments": arguments,
            "name": "exec_command",
        }),
        _sse_block("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_exec",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_exec",
                "name": "exec_command",
            },
        }),
        b"data: [DONE]\n\n",
    ]


def _stuck_function_call_stream(delta_count=5):
    chunks = [
        _sse_block("response.created", {
            "response": {
                "id": "resp_fake_stuck_tool",
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_stuck",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_stuck",
                "name": "apply_patch",
                "arguments": "",
            },
        }),
    ]
    for index in range(delta_count):
        chunks.append(_sse_block("response.function_call_arguments.delta", {
            "item_id": "fc_stuck",
            "output_index": 0,
            "delta": json.dumps({"chunk": index}),
        }))
    return chunks


class ResponsesStreamRuntimeTests(unittest.TestCase):
    def _run_runtime(
        self,
        opener,
        web_runtime=None,
        telemetry=None,
        reasoning_stream_format="raw",
        request_id="",
        private_function_call_timeout_s=None,
        private_function_call_delta_limit=None,
        reasoning_only_timeout_s=None,
        reasoning_only_char_limit=None,
        apply_patch_output_style="native",
        metadata=None,
        proxy_tool_registry=None,
        selected_model=None,
        hop_budget_signal_threshold=None,
        context_pressure_signal_threshold=None,
        empty_answer_repair_hops=None,
        empty_answer_repair_disable_tools=None,
        stream_terminal_timeout_s=None,
        return_result=False,
    ):
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format=reasoning_stream_format,
            web_runtime=web_runtime or FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id=request_id,
            private_function_call_timeout_s=private_function_call_timeout_s,
            private_function_call_delta_limit=private_function_call_delta_limit,
            reasoning_only_timeout_s=reasoning_only_timeout_s,
            reasoning_only_char_limit=reasoning_only_char_limit,
            proxy_tool_registry=proxy_tool_registry,
            selected_model=selected_model,
            hop_budget_signal_threshold=hop_budget_signal_threshold,
            context_pressure_signal_threshold=context_pressure_signal_threshold,
            empty_answer_repair_hops=empty_answer_repair_hops,
            empty_answer_repair_disable_tools=empty_answer_repair_disable_tools,
            stream_terminal_timeout_s=stream_terminal_timeout_s,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "test"}],
            }],
            "tools": [{"type": "web_search"}],
        }
        if metadata is not None:
            body["metadata"] = metadata
        result = runtime.run(body, "test-model.gguf", apply_patch_output_style)
        stream_text = b"".join(chunks).decode("utf-8")
        if return_result:
            return result, stream_text
        return stream_text

    def test_streaming_renormalization_preserves_disabled_system_prompt(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            selected_model={"overrides": {"disable_system_prompt": True}},
        )

        self.assertEqual(len(requests), 1)
        self.assertNotIn("instructions", requests[0])
        self.assertTrue(requests[0]["metadata"]["qz_prompt_policy"]["disable_system_prompt"])

    def test_web_search_call_is_public_and_upstream_resumes_with_hidden_output(self):
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_web"
                for item in body.get("input") or []
            )
            return FakeStream(_final_message_stream() if has_tool_output else _web_call_stream())

        stream_text = self._run_runtime(opener, web_runtime=web_runtime)

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(web_runtime.calls), 1)
        self.assertIn('"type": "web_search_call"', stream_text)
        self.assertIn("searched.", stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(any(item.get("type") == "function_call_output" for item in requests[1]["input"]))

    def test_proxy_local_streaming_lifecycle_is_not_web_search_specific(self):
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_probe"
                for item in body.get("input") or []
            )
            return FakeStream(_final_message_stream() if has_tool_output else _probe_call_stream())

        telemetry = TelemetryBus()
        stream_text = self._run_runtime(opener, telemetry=telemetry, proxy_tool_registry=registry)

        self.assertEqual(len(requests), 2)
        self.assertIn('"type": "qz_probe_call"', stream_text)
        self.assertIn("response.qz_probe_call.in_progress", stream_text)
        self.assertIn("response.qz_probe_call.working", stream_text)
        self.assertIn("response.qz_probe_call.completed", stream_text)
        self.assertIn("searched.", stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(any(item.get("type") == "function_call_output" for item in requests[1]["input"]))

        telemetry_events = telemetry.recent()
        started = [event for event in telemetry_events if event["type"] == "tool_call_started"][0]["payload"]
        completed = [event for event in telemetry_events if event["type"] == "tool_call_completed"][0]["payload"]
        self.assertEqual(started["tool"], "qz_probe")
        self.assertEqual(started["public_item_type"], "qz_probe_call")
        self.assertEqual(completed["tool"], "qz_probe")
        self.assertEqual(completed["upstream_items"], 2)

    def test_apply_patch_call_is_rewritten_as_public_tool_item(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_apply_patch_call_stream())

        stream_text = self._run_runtime(opener)

        self.assertEqual(len(requests), 1)
        self.assertIn('"type": "apply_patch_call"', stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertIn("response.completed", stream_text)

    def test_public_function_call_is_buffered_until_arguments_are_complete(self):
        def opener(body):
            return FakeStream(_exec_command_call_stream())

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        names = [event_type for event_type, _payload in events]

        added_index = names.index("response.output_item.added")
        item_done_index = names.index("response.output_item.done")
        self.assertLess(added_index, item_done_index)
        self.assertNotIn("response.function_call_arguments.delta", names)
        self.assertNotIn("response.function_call_arguments.done", names)

        added_payload = events[added_index][1]
        self.assertEqual(added_payload["item"]["type"], "function_call")
        self.assertEqual(added_payload["item"]["status"], "in_progress")
        self.assertEqual(added_payload["item"]["name"], "exec_command")
        self.assertIn('"cmd": "cat > sample_v2.md"', added_payload["item"]["arguments"])
        self.assertIn("cat > sample_v2.md", stream_text)

    def test_golden_public_function_call_buffers_until_arguments_done(self):
        def opener(body):
            return FakeStream(_fixture_chunks("public_function_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        names = [event_type for event_type, _payload in events]
        public_call_events = [
            (event_type, payload)
            for event_type, payload in events
            if isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "function_call"
        ]

        self.assertIn("response.completed", names)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertNotIn("response.function_call_arguments.delta", names)
        self.assertNotIn("response.function_call_arguments.done", names)
        self.assertEqual([event_type for event_type, _payload in public_call_events], [
            "response.output_item.added",
            "response.output_item.done",
        ])
        self.assertEqual(public_call_events[0][1]["item"]["name"], "exec_command")
        self.assertIn("cat > sample_v2.md", public_call_events[0][1]["item"]["arguments"])

    def test_golden_public_function_call_without_done_still_completes_once(self):
        def opener(body):
            return FakeStream(_fixture_chunks("public_function_call_without_done.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        names = [event_type for event_type, _payload in events]
        public_call_events = [
            (event_type, payload)
            for event_type, payload in events
            if isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "function_call"
        ]

        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertNotIn("response.function_call_arguments.delta", names)
        self.assertNotIn("response.function_call_arguments.done", names)
        self.assertEqual([event_type for event_type, _payload in public_call_events], [
            "response.output_item.added",
            "response.output_item.done",
        ])

    def test_stuck_function_call_aborts_instead_of_silent_dead_air(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_stuck_function_call_stream(delta_count=4))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-stuck-tool",
            private_function_call_delta_limit=2,
        )
        events = telemetry.recent()

        self.assertIn("private tool-call loop", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertTrue(any(event.get("type") == "private_tool_call_aborted" for event in events))
        self.assertTrue(any(
            event.get("type") == "stream_event_timing"
            and (event.get("payload") or {}).get("suppressed") == "function_call_aborted"
            for event in events
        ))

    def test_stuck_function_call_timeout_aborts_without_leaking_private_call(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_stuck_function_call_stream(delta_count=1))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-stuck-tool-timeout",
            private_function_call_timeout_s=0,
            private_function_call_delta_limit=99,
        )
        events = telemetry.recent()

        self.assertIn("private tool-call loop", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(any(
            event.get("type") == "private_tool_call_aborted"
            and (event.get("payload") or {}).get("reason") == "timeout"
            for event in events
        ))
        self.assertTrue(any(
            event.get("type") == "stream_event_timing"
            and (event.get("payload") or {}).get("suppressed") == "function_call_aborted"
            for event in events
        ))

    def test_reasoning_only_stream_aborts_instead_of_never_answering(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_stuck_reasoning_only_stream(delta_count=4))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-stuck-reasoning",
            reasoning_stream_format="summary",
            reasoning_only_char_limit=12,
        )
        events = telemetry.recent()

        self.assertIn("reasoning-only stream", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertTrue(any(event.get("type") == "reasoning_only_aborted" for event in events))
        self.assertTrue(any(
            event.get("type") == "stream_event_timing"
            and (event.get("payload") or {}).get("suppressed") == "reasoning_only_aborted"
            for event in events
        ))

    def test_reasoning_only_completed_triggers_exactly_one_repair_hop(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_reasoning_only_completed_stream())
            return FakeStream(_final_message_stream(text="repaired answer"))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-empty-answer-repair",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertEqual(len(requests), 2)
        self.assertIn("repaired answer", stream_text)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        repair_msgs = [
            item for item in requests[1].get("input") or []
            if isinstance(item, dict)
            and "Protocol repair: your previous completion ended" in json.dumps(item.get("content") or "")
        ]
        self.assertEqual(len(repair_msgs), 1)
        self.assertNotIn("tools", requests[1])
        self.assertNotIn("tool_choice", requests[1])
        started = [event for event in events if event.get("type") == "empty_answer_repair_started"]
        completed = [event for event in events if event.get("type") == "empty_answer_repair_completed"]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed), 1)
        self.assertEqual(started[0]["payload"]["repair_hop_index"], 0)
        self.assertEqual(started[0]["payload"]["input_tokens"], 11)
        self.assertEqual(started[0]["payload"]["output_tokens"], 7)

    def test_successful_empty_answer_repair_streams_repaired_answer(self):
        def opener(body):
            if not getattr(opener, "_called", False):
                opener._called = True
                return FakeStream(_reasoning_only_completed_stream())
            return FakeStream(_final_message_stream(text="final from repair"))

        stream_text = self._run_runtime(opener, reasoning_stream_format="summary")
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertIn("final from repair", stream_text)
        self.assertIn("response.reasoning_summary_text.delta", stream_text)
        self.assertNotIn("response.reasoning_text.delta", stream_text)
        self.assertEqual(names.count("response.created"), 1)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)

    def test_failed_empty_answer_repair_emits_visible_fallback_without_looping(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_reasoning_only_completed_stream())

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-empty-answer-repair-failed",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertEqual(len(requests), 2)
        self.assertIn("reasoning-only response", stream_text)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_started"]), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_failed"]), 1)
        self.assertTrue(any(
            event.get("type") == "reasoning_only_completed_without_answer"
            for event in events
        ))

    def test_empty_repair_completion_without_reasoning_fails_repair_without_looping(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_reasoning_only_completed_stream())
            return FakeStream(_empty_completed_stream())

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-empty-repair-empty-completed",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertEqual(len(requests), 2)
        self.assertIn("reasoning-only response", stream_text)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_started"]), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_failed"]), 1)
        self.assertFalse(any(event.get("type") == "empty_answer_repair_completed" for event in events))
        self.assertTrue(any(
            event.get("type") == "reasoning_only_completed_without_answer"
            for event in events
        ))

    def test_whitespace_only_repair_output_delta_fails_repair_without_completed_telemetry(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_reasoning_only_completed_stream())
            return FakeStream(_whitespace_output_text_completed_stream())

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-empty-repair-whitespace-output",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertEqual(len(requests), 2)
        self.assertIn("reasoning-only response", stream_text)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_started"]), 1)
        self.assertEqual(len([event for event in events if event.get("type") == "empty_answer_repair_failed"]), 1)
        self.assertFalse(any(event.get("type") == "empty_answer_repair_completed" for event in events))
        self.assertTrue(any(
            event.get("type") == "reasoning_only_completed_without_answer"
            for event in events
        ))

    def test_valid_public_item_completion_does_not_trigger_empty_answer_repair(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_public_item_completed_stream())

        stream_text = self._run_runtime(opener, telemetry=telemetry)
        events = telemetry.recent()
        names = [event for event, _payload in _parse_sse_events(stream_text)]

        self.assertEqual(len(requests), 1)
        self.assertIn('"type": "custom_tool_call"', stream_text)
        self.assertEqual(names.count("response.completed"), 1)
        self.assertFalse(any(event.get("type", "").startswith("empty_answer_repair") for event in events))

    def test_empty_completion_without_reasoning_does_not_trigger_empty_answer_repair(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_empty_completed_stream())

        stream_text = self._run_runtime(opener, telemetry=telemetry)
        events = telemetry.recent()

        self.assertEqual(len(requests), 1)
        self.assertIn("response.completed", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertFalse(any(event.get("type", "").startswith("empty_answer_repair") for event in events))

    def test_default_reasoning_only_char_limit_does_not_abort_long_active_output(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_long_reasoning_then_message_stream(delta_count=100))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-long-reasoning",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()

        self.assertIn("final answer", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertFalse(any(event.get("type") == "reasoning_only_aborted" for event in events))

    def test_golden_long_active_reasoning_reaches_answer_without_default_char_abort(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("long_active_reasoning.raw"))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-golden-long-reasoning",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event_type for event_type, _payload in _parse_sse_events(stream_text)]

        self.assertIn("final answer", stream_text)
        self.assertIn("response.completed", names)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertIn("response.reasoning_summary_text.delta", stream_text)
        self.assertNotIn("response.reasoning_text.delta", stream_text)
        self.assertFalse(any(event.get("type") == "reasoning_only_aborted" for event in events))

    def test_reasoning_tool_artifact_aborts_without_length_limit(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_reasoning_tool_artifact_stream())

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-reasoning-artifact",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()

        self.assertIn("sample_v4.md", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertTrue(any(
            event.get("type") == "reasoning_only_aborted"
            and (event.get("payload") or {}).get("reason") == "artifact_tool_payload"
            for event in events
        ))
        self.assertTrue(any(
            event.get("type") == "stream_event_timing"
            and (event.get("payload") or {}).get("suppressed") == "reasoning_artifact_aborted"
            for event in events
        ))

    def test_golden_reasoning_only_abort_replays_fallback(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("reasoning_only.raw"))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-golden-reasoning-only",
            reasoning_stream_format="summary",
            reasoning_only_char_limit=12,
        )
        events = telemetry.recent()

        self.assertIn("reasoning-only stream", stream_text)
        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertIn("response.reasoning_summary_text.delta", stream_text)
        self.assertNotIn("response.reasoning_text.delta", stream_text)
        self.assertTrue(any(
            event.get("type") == "reasoning_only_aborted"
            and event.get("request_id") == "req-golden-reasoning-only"
            for event in events
        ))

    def test_golden_reasoning_artifact_aborts_without_executing_tool(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("reasoning_artifact.raw"))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-golden-artifact",
            reasoning_stream_format="summary",
        )
        events = telemetry.recent()
        names = [event_type for event_type, _payload in _parse_sse_events(stream_text)]

        self.assertIn("sample_v4.md", stream_text)
        self.assertIn("response.completed", names)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertTrue(any(
            event.get("type") == "reasoning_only_aborted"
            and (event.get("payload") or {}).get("reason") == "artifact_tool_payload"
            for event in events
        ))
        # Fallback message is now streamed incrementally before response.completed:
        # the client receives output_item.added → content_part.added →
        # output_text.delta → output_text.done → content_part.done → output_item.done.
        self.assertIn("response.output_item.added", names)
        self.assertIn("response.output_text.delta", names)
        self.assertIn("response.output_item.done", names)

    def test_golden_basic_message_stream_replays_unchanged(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)

        self.assertEqual(len(requests), 1)
        self.assertEqual([event for event, _payload in events].count("response.created"), 1)
        self.assertIn("stream ok", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))

    def test_answer_deltas_are_written_before_terminal_completion(self):
        written = []

        def opener(body):
            return FakeStream(_multi_delta_message_stream())

        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=written.append,
            stream_opener=opener,
            capture_enabled=False,
        )

        runtime.run({
            "model": "test-model.gguf",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "test"}],
            }],
        }, "test-model.gguf")

        written_text = [chunk.decode("utf-8", "replace") for chunk in written]
        first_delta = next(index for index, chunk in enumerate(written_text) if "alpha " in chunk)
        second_delta = next(index for index, chunk in enumerate(written_text) if "beta" in chunk)
        completed = next(index for index, chunk in enumerate(written_text) if "response.completed" in chunk)

        self.assertLess(first_delta, completed)
        self.assertLess(second_delta, completed)
        self.assertNotIn('"type": "function_call"', "".join(written_text))

    def test_client_disconnect_closes_upstream_and_emits_cancel_telemetry(self):
        telemetry = TelemetryBus()
        upstream_stream = FakeStream(_fixture_chunks("basic_message.raw"))
        written = []

        def writer(chunk):
            if written:
                raise BrokenPipeError("client closed")
            written.append(chunk)

        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=writer,
            stream_opener=lambda body: upstream_stream,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="req-disconnect",
        )
        body = {
            "model": "test-model.gguf",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "test"}],
            }],
        }

        with self.assertRaises(ClientStreamDisconnected):
            runtime.run(body, "test-model.gguf")

        events = telemetry.recent()
        self.assertTrue(upstream_stream.closed)
        self.assertTrue(any(
            event.get("type") == "client_disconnected"
            and event.get("request_id") == "req-disconnect"
            and (event.get("payload") or {}).get("phase") == "stream_write"
            for event in events
        ))
        self.assertFalse(any(event.get("type") == "stream_failed" for event in events))
        self.assertNotIn(b"response.completed", b"".join(written))

    def test_golden_apply_patch_stream_rewrites_to_apply_patch_call(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_fixture_chunks("apply_patch_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        names = [event_type for event_type, _payload in events]
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertEqual(len(requests), 1)
        self.assertIn("response.completed", names)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["status"], "in_progress")
        self.assertEqual(patch_items[1]["status"], "completed")
        self.assertEqual(patch_items[0]["call_id"], "call_fixture_patch")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-smoke.txt")

    def test_golden_custom_apply_patch_stream_rewrites_to_custom_tool_call(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(_fixture_chunks("custom_apply_patch_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        names = [event_type for event_type, _payload in events]
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertEqual(len(requests), 1)
        self.assertIn("response.completed", names)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(len(custom_items), 2)
        self.assertEqual(custom_items[0]["status"], "in_progress")
        self.assertEqual(custom_items[1]["status"], "completed")
        self.assertEqual(custom_items[0]["call_id"], "call_fixture_custom_patch")
        self.assertEqual(custom_items[0]["name"], "apply_patch")
        self.assertIn("*** Begin Patch", custom_items[0]["input"])
        self.assertIn("*** Add File: tmp/quantzhai-custom-smoke.txt", custom_items[0]["input"])
        self.assertIn("+quantzhai custom apply_patch smoke", custom_items[0]["input"])

    def test_apply_patch_stream_uses_request_tool_policy_metadata(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_call.raw"))

        stream_text = self._run_runtime(
            opener,
            apply_patch_output_style="native",
            metadata={
                "qz_tool_policy": {
                    "schema": "qz.tool_policy.v1",
                    "apply_patch_declared": True,
                    "apply_patch_client_tool_type": "custom",
                    "apply_patch_output_style": "custom",
                }
            },
        )

        self.assertIn('"type": "custom_tool_call"', stream_text)
        self.assertNotIn('"type": "apply_patch_call"', stream_text)

    def test_golden_apply_patch_update_stream_rewrites_to_apply_patch_call(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_update_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "update_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-update.txt")
        self.assertIn("+new line", patch_items[0]["operation"]["diff"])

    def test_golden_custom_apply_patch_update_stream_rewrites_to_patch_envelope(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_update_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Update File: tmp/quantzhai-update.txt", custom_items[0]["input"])
        self.assertIn("-old line", custom_items[0]["input"])
        self.assertIn("+new line", custom_items[0]["input"])

    def test_golden_apply_patch_multihunk_update_stream_rewrites_to_apply_patch_call(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_multihunk_update_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "update_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-multihunk.txt")
        self.assertIn("-old alpha", patch_items[0]["operation"]["diff"])
        self.assertIn("+new alpha", patch_items[0]["operation"]["diff"])
        self.assertIn("-old beta", patch_items[0]["operation"]["diff"])
        self.assertIn("+new beta", patch_items[0]["operation"]["diff"])

    def test_golden_apply_patch_unified_diff_update_stream_strips_metadata(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_unified_diff_update_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "update_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-unified.txt")
        diff = patch_items[0]["operation"]["diff"]
        self.assertIn("@@\n-old alpha\n+new alpha", diff)
        self.assertIn("@@\n-old beta\n+new beta", diff)
        self.assertIn("+new tail", diff)
        self.assertNotIn("diff --git", diff)
        self.assertNotIn("index 1111111", diff)
        self.assertNotIn("--- a/tmp/quantzhai-unified.txt", diff)
        self.assertNotIn("+++ b/tmp/quantzhai-unified.txt", diff)
        self.assertNotIn("@@ -1,4 +1,4 @@", diff)
        self.assertNotIn("@@ -8,3 +8,4 @@", diff)

    def test_golden_apply_patch_large_multihunk_update_stream_strips_metadata(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_large_multihunk_update_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "update_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-large-multihunk.txt")
        diff = patch_items[0]["operation"]["diff"]
        self.assertIn("@@\n context one\n-old alpha\n+new alpha", diff)
        self.assertIn("@@\n context three\n-old beta\n+new beta\n+inserted beta detail", diff)
        self.assertIn("@@\n-old gamma\n+new gamma", diff)
        self.assertIn("@@\n context six\n-old delta\n+new delta\n+new epsilon tail", diff)
        self.assertNotIn("diff --git", diff)
        self.assertNotIn("index 1111111", diff)
        self.assertNotIn("--- a/tmp/quantzhai-large-multihunk.txt", diff)
        self.assertNotIn("+++ b/tmp/quantzhai-large-multihunk.txt", diff)
        self.assertNotIn("@@ -14,6 +14,7 @@", diff)

    def test_golden_custom_apply_patch_multihunk_update_stream_rewrites_to_patch_envelope(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_multihunk_update_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Update File: tmp/quantzhai-multihunk.txt", custom_items[0]["input"])
        self.assertIn("-old alpha", custom_items[0]["input"])
        self.assertIn("+new alpha", custom_items[0]["input"])
        self.assertIn("-old beta", custom_items[0]["input"])
        self.assertIn("+new beta", custom_items[0]["input"])

    def test_golden_custom_apply_patch_unified_diff_update_stream_strips_metadata(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_unified_diff_update_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        patch = custom_items[0]["input"]
        self.assertIn("*** Update File: tmp/quantzhai-unified.txt", patch)
        self.assertIn("@@\n-old alpha\n+new alpha", patch)
        self.assertIn("@@\n-old beta\n+new beta", patch)
        self.assertIn("+new tail", patch)
        self.assertNotIn("diff --git", patch)
        self.assertNotIn("index 1111111", patch)
        self.assertNotIn("--- a/tmp/quantzhai-unified.txt", patch)
        self.assertNotIn("+++ b/tmp/quantzhai-unified.txt", patch)
        self.assertNotIn("@@ -1,4 +1,4 @@", patch)
        self.assertNotIn("@@ -8,3 +8,4 @@", patch)

    def test_golden_custom_apply_patch_large_multihunk_update_stream_strips_metadata(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_large_multihunk_update_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        patch = custom_items[0]["input"]
        self.assertIn("*** Update File: tmp/quantzhai-large-multihunk.txt", patch)
        self.assertIn("@@\n context one\n-old alpha\n+new alpha", patch)
        self.assertIn("@@\n context three\n-old beta\n+new beta\n+inserted beta detail", patch)
        self.assertIn("@@\n-old gamma\n+new gamma", patch)
        self.assertIn("@@\n context six\n-old delta\n+new delta\n+new epsilon tail", patch)
        self.assertNotIn("diff --git", patch)
        self.assertNotIn("index 1111111", patch)
        self.assertNotIn("--- a/tmp/quantzhai-large-multihunk.txt", patch)
        self.assertNotIn("+++ b/tmp/quantzhai-large-multihunk.txt", patch)
        self.assertNotIn("@@ -14,6 +14,7 @@", patch)

    def test_golden_apply_patch_delete_stream_rewrites_to_apply_patch_call(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_delete_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "delete_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/quantzhai-delete.txt")

    def test_golden_custom_apply_patch_delete_stream_rewrites_to_patch_envelope(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_delete_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Delete File: tmp/quantzhai-delete.txt", custom_items[0]["input"])

    def test_golden_apply_patch_move_stream_rewrites_to_apply_patch_call(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_move_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"], {
            "type": "move_file",
            "path": "tmp/quantzhai-old-name.txt",
            "destination": "tmp/quantzhai-new-name.txt",
        })

    def test_golden_apply_patch_rename_alias_stream_rewrites_to_move_call(self):
        def opener(body):
            return FakeStream(_fixture_chunks("apply_patch_rename_alias_move_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        operation = patch_items[0]["operation"]
        self.assertEqual(operation["type"], "move_file")
        self.assertEqual(operation["path"], "tmp/quantzhai-rename-old.txt")
        self.assertEqual(operation["destination"], "tmp/quantzhai-rename-new.txt")
        self.assertIn("@@\n stable heading\n-old rename body\n+new rename body", operation["diff"])
        self.assertNotIn("similarity index", operation["diff"])
        self.assertNotIn("rename from", operation["diff"])
        self.assertNotIn("rename to", operation["diff"])
        self.assertNotIn("--- a/tmp/quantzhai-rename-old.txt", operation["diff"])
        self.assertNotIn("+++ b/tmp/quantzhai-rename-new.txt", operation["diff"])
        self.assertNotIn("@@ -1,3 +1,3 @@", operation["diff"])

    def test_golden_custom_apply_patch_move_stream_rewrites_to_patch_envelope(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_move_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Update File: tmp/quantzhai-old-name.txt", custom_items[0]["input"])
        self.assertIn("*** Move to: tmp/quantzhai-new-name.txt", custom_items[0]["input"])

    def test_golden_custom_apply_patch_rename_alias_stream_rewrites_to_move_envelope(self):
        def opener(body):
            return FakeStream(_fixture_chunks("custom_apply_patch_rename_alias_move_call.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(custom_items), 2)
        patch = custom_items[0]["input"]
        self.assertIn("*** Update File: tmp/quantzhai-rename-old.txt", patch)
        self.assertIn("*** Move to: tmp/quantzhai-rename-new.txt", patch)
        self.assertIn("@@\n stable heading\n-old rename body\n+new rename body", patch)
        self.assertNotIn("similarity index", patch)
        self.assertNotIn("rename from", patch)
        self.assertNotIn("rename to", patch)
        self.assertNotIn("--- a/tmp/quantzhai-rename-old.txt", patch)
        self.assertNotIn("+++ b/tmp/quantzhai-rename-new.txt", patch)
        self.assertNotIn("@@ -1,3 +1,3 @@", patch)

    def test_golden_qwen_create_file_sibling_patch_stream_is_coerced(self):
        """Qwen-observed shape: operation lacks diff, sibling top-level patch carries the content."""
        def opener(body):
            return FakeStream(_fixture_chunks("qwen_create_file_sibling_patch.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertEqual(len(patch_items), 2)
        self.assertEqual(patch_items[0]["operation"]["type"], "create_file")
        self.assertEqual(patch_items[0]["operation"]["path"], "tmp/qwen-sibling-patch.txt")
        self.assertEqual(patch_items[0]["operation"]["diff"], "qwen sibling patch content\n")

    def test_golden_qwen_create_file_sibling_patch_custom_stream_is_coerced(self):
        """Same shape, custom output style — should produce a custom_tool_call envelope."""
        def opener(body):
            return FakeStream(_fixture_chunks("qwen_create_file_sibling_patch.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Add File: tmp/qwen-sibling-patch.txt", custom_items[0]["input"])
        self.assertIn("+qwen sibling patch content", custom_items[0]["input"])

    def test_golden_qwen_update_file_sibling_patch_with_unified_headers_stream_is_coerced(self):
        """Sibling patch carrying a unified diff with file-level headers should be coerced and stripped."""
        def opener(body):
            return FakeStream(_fixture_chunks("qwen_update_file_sibling_patch_with_unified_headers.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(len(custom_items), 2)
        envelope = custom_items[0]["input"]
        self.assertIn("*** Update File: config/app.json", envelope)
        self.assertIn("+  \"debug\": false,", envelope)
        # File-level unified diff headers should be stripped.
        self.assertNotIn("--- a/config/app.json", envelope)
        self.assertNotIn("+++ b/config/app.json", envelope)
        # Hunk header normalised to bare @@.
        self.assertNotIn("@@ -1,4 +1,5 @@", envelope)

    def test_golden_qwen_legacy_patch_missing_path_extracts_path_from_envelope(self):
        """Qwen-observed shape: full Codex patch envelope as top-level 'patch'
        string with no separate 'path'. Proxy extracts type+path from the
        envelope's header line and routes through normal coercion."""
        def opener(body):
            return FakeStream(_fixture_chunks("qwen_legacy_patch_missing_path.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(len(custom_items), 2)
        envelope = custom_items[0]["input"]
        self.assertIn("*** Update File: quote.py", envelope)
        self.assertIn("-MESSAGE = 'plain'", envelope)
        self.assertIn("+MESSAGE = 'escaped'", envelope)

    def test_golden_qwen_rename_no_hunk_emits_move_envelope(self):
        """rename_file without a content hunk used to silently route to the
        broken assistant-message path. Now the proxy emits a partial
        *** Update File + *** Move to envelope so Codex's verifier surfaces
        a specific error (or applies the rename if the verifier accepts
        hunkless moves)."""
        def opener(body):
            return FakeStream(_fixture_chunks("qwen_rename_no_hunk.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")
        events = _parse_sse_events(stream_text)
        custom_items = [
            payload["item"]
            for event_type, payload in events
            if event_type in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
        ]

        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(len(custom_items), 2)
        self.assertIn("*** Update File: src/old.py", custom_items[0]["input"])
        self.assertIn("*** Move to: src/new.py", custom_items[0]["input"])
        self.assertIn("*** End Patch", custom_items[0]["input"])

    def test_golden_qwen_create_file_bare_operation_injects_error_upstream(self):
        """Qwen Shape B: create_file with no diff and no sibling patch.
        coerce() cannot recover content so the registry injects an error result
        upstream (into next_input for the model). No custom_tool_call is emitted
        to Codex — the tool call error is model-facing, not Codex-facing.
        The second hop returns a valid message so the stream ends cleanly."""
        telemetry = TelemetryBus()
        calls = []

        def opener(body):
            calls.append(body)
            if len(calls) == 1:
                return FakeStream(_fixture_chunks("qwen_create_file_bare_operation.raw"))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom", telemetry=telemetry)
        events_list = [et for et, _ in _parse_sse_events(stream_text)]

        # Error is injected upstream, not forwarded as a Codex-facing tool call.
        self.assertNotIn("custom_tool_call", stream_text)
        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        # Second hop carries the error result in its input.
        self.assertGreater(len(calls), 1)
        second_input = calls[1].get("input") or []
        error_results = [i for i in second_input if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(any("apply_patch" in str(i.get("output", "")) for i in error_results), second_input)
        # tool_call_error telemetry emitted.
        telem_events = telemetry.recent()
        self.assertTrue(any(e.get("type") == "tool_call_error" for e in telem_events), telem_events)

    def test_golden_qwen_update_file_bare_operation_injects_error_upstream(self):
        """Same as above but for update_file."""
        calls = []

        def opener(body):
            calls.append(body)
            if len(calls) == 1:
                return FakeStream(_fixture_chunks("qwen_update_file_bare_operation.raw"))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")

        self.assertNotIn("custom_tool_call", stream_text)
        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertGreater(len(calls), 1)

    def test_golden_invalid_apply_patch_stream_injects_error_upstream(self):
        """Bare-operation create_file: coerce() fails, error injected upstream.
        No Codex-facing custom_tool_call emitted. The model gets the error
        result in next_input and can retry on the second hop."""
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            if len(calls) == 1:
                return FakeStream(_fixture_chunks("invalid_apply_patch_call.raw"))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")

        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertNotIn("custom_tool_call", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertGreater(len(calls), 1)
        second_input = calls[1].get("input") or []
        self.assertTrue(
            any(
                isinstance(i, dict) and i.get("type") == "function_call_output"
                for i in second_input
            ),
            second_input,
        )

    def test_golden_invalid_apply_patch_move_without_destination_injects_error_upstream(self):
        """move_file with no destination: coerce() fails with a specific reason.
        The error is injected upstream as a function_call_output, not shown to
        Codex as an assistant message."""
        calls = []

        def opener(body):
            calls.append(body)
            if len(calls) == 1:
                return FakeStream(_fixture_chunks("invalid_apply_patch_move_call.raw"))
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener, apply_patch_output_style="custom")

        # No proxy-generated error assistant message (old behavior).
        self.assertNotIn("invalid patch arguments", stream_text)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertGreater(len(calls), 1)
        second_input = calls[1].get("input") or []
        error_outputs = [
            i for i in second_input
            if isinstance(i, dict) and i.get("type") == "function_call_output"
        ]
        self.assertTrue(error_outputs, second_input)
        # Error message should mention destination.
        self.assertTrue(
            any("destination" in str(i.get("output", "")) for i in error_outputs),
            error_outputs,
        )

    def test_golden_completed_without_done_appends_done_once(self):
        def opener(body):
            return FakeStream(_fixture_chunks("completed_without_done.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)

        self.assertEqual([event for event, _payload in events].count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))

    def test_stream_adds_done_when_upstream_closes_after_completed(self):
        def opener(body):
            return FakeStream(_final_message_stream()[:-1])

        stream_text = self._run_runtime(opener)

        self.assertIn("response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))

    def test_summary_mode_transforms_reasoning_stream(self):
        def opener(body):
            return FakeStream(_reasoning_message_stream())

        stream_text = self._run_runtime(opener, reasoning_stream_format="summary")

        self.assertNotIn("response.reasoning_text.delta", stream_text)
        self.assertIn("response.reasoning_summary_text.delta", stream_text)
        self.assertIn("stream ok", stream_text)

    def test_streaming_emits_timing_telemetry_without_changing_output(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("basic_message.raw"))

        stream_text = self._run_runtime(opener, telemetry=telemetry, request_id="req-stream-1")
        timing_events = [
            event
            for event in telemetry.recent()
            if event.get("type") == "stream_event_timing"
        ]

        self.assertIn("stream ok", stream_text)
        self.assertTrue(timing_events)
        self.assertTrue(any((event.get("payload") or {}).get("event_type") == "response.output_text.delta" for event in timing_events))
        for event in timing_events:
            self.assertEqual(event.get("request_id"), "req-stream-1")
            payload = event.get("payload") or {}
            self.assertEqual(payload.get("request_id"), "req-stream-1")
            self.assertIn("received_to_parsed_ms", payload)
            self.assertIn("parsed_to_forwarded_ms", payload)
            self.assertIn("received_to_telemetry_ms", payload)
            self.assertIn("forwarded_chunks", payload)
            self.assertIn("forwarded_bytes", payload)

    def test_live_protocol_drift_event_classifies_once(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("item_content_delta.raw"))

        result, stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-protocol-drift-live",
            return_result=True,
        )
        terminal = result.get("stream_terminal") or {}
        terminal_events = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertIn("response.output_item.content.delta", stream_text)
        self.assertEqual(terminal.get("classification"), "protocol_drift_seen")
        self.assertTrue(terminal.get("visible_answer_seen"))
        self.assertTrue(terminal.get("saw_output_text"))
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(
            terminal_events[0].get("payload", {}).get("classification"),
            "protocol_drift_seen",
        )

    def test_live_compact_failed_event_classifies_once_without_retry(self):
        telemetry = TelemetryBus()
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            return FakeStream(_fixture_chunks("compact_failed.raw"))

        result, stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-compact-failed-live",
            return_result=True,
        )
        terminal = result.get("stream_terminal") or {}
        terminal_events = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertEqual(len(calls), 1)
        self.assertIn("response.compact.failed", stream_text)
        self.assertEqual(terminal.get("classification"), "compact_failed")
        self.assertTrue(terminal.get("saw_compact_failed"))
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(
            terminal_events[0].get("payload", {}).get("classification"),
            "compact_failed",
        )

    def test_live_failed_event_classifies_once_without_looping(self):
        telemetry = TelemetryBus()
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            return FakeStream(_failed_without_answer_stream())

        result, stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-failed-live",
            return_result=True,
        )
        terminal = result.get("stream_terminal") or {}
        terminal_events = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertEqual(len(calls), 1)
        self.assertIn("response.failed", stream_text)
        self.assertEqual(terminal.get("classification"), "unrecoverable")
        self.assertTrue(terminal.get("saw_error"))
        self.assertEqual(len(terminal_events), 1)
        self.assertEqual(
            terminal_events[0].get("payload", {}).get("classification"),
            "unrecoverable",
        )

    def test_live_ok_stream_does_not_emit_terminal_classified(self):
        telemetry = TelemetryBus()

        def opener(body):
            return FakeStream(_fixture_chunks("basic_message.raw"))

        result, _stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-ok-live",
            return_result=True,
        )
        terminal_events = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertEqual(result.get("stream_terminal", {}).get("classification"), "ok")
        self.assertEqual(terminal_events, [])

    def test_live_terminal_timeout_preserves_partial_output_and_emits_once(self):
        telemetry = TelemetryBus()
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            return TimeoutAfterChunksStream(_visible_output_without_terminal_stream())

        result, stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-terminal-timeout-live",
            stream_terminal_timeout_s=2.0,
            return_result=True,
        )
        terminal = result.get("stream_terminal") or {}
        terminal_events = [
            event for event in telemetry.recent()
            if event.get("type") == "stream_terminal_classified"
        ]

        self.assertEqual(len(calls), 1)
        self.assertTrue(result["fallback"])
        self.assertEqual(terminal.get("classification"), "stream_terminal_timeout")
        self.assertTrue(terminal.get("saw_output_text"))
        self.assertTrue(terminal.get("terminal_timeout"))
        self.assertIn("hello", stream_text)
        self.assertIn("event: response.completed", stream_text)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertEqual(len(terminal_events), 1)
        payload = terminal_events[0].get("payload", {})
        self.assertEqual(payload.get("classification"), "stream_terminal_timeout")
        self.assertEqual(payload.get("signal_id"), "stream.terminal_timeout")
        self.assertEqual(payload.get("action"), "terminal_fallback")

    def test_live_terminal_timeout_does_not_duplicate_partial_output(self):
        telemetry = TelemetryBus()

        def opener(body):
            return TimeoutAfterChunksStream(_visible_output_without_terminal_stream())

        _result, stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-terminal-timeout-no-duplicate",
            stream_terminal_timeout_s=2.0,
            return_result=True,
        )
        events = _parse_sse_events(stream_text)
        text_deltas = [
            payload.get("delta")
            for event_type, payload in events
            if event_type == "response.output_text.delta" and isinstance(payload, dict)
        ]

        self.assertEqual(text_deltas, ["hello"])

    def test_golden_web_search_stream_replays_with_continuation(self):
        telemetry = TelemetryBus()
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            fixture = "web_search_final.raw" if has_tool_output else "web_search_call.raw"
            return FakeStream(_fixture_chunks(fixture))

        stream_text = self._run_runtime(
            opener,
            web_runtime=web_runtime,
            telemetry=telemetry,
            request_id="req-web-golden",
        )
        events = _parse_sse_events(stream_text)
        telemetry_events = telemetry.recent()
        output_indexes = [
            payload.get("output_index")
            for _event, payload in events
            if isinstance(payload, dict) and isinstance(payload.get("output_index"), int)
        ]
        completed = next(
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(web_runtime.calls), 1)
        self.assertIn('"type": "web_search_call"', stream_text)
        self.assertIn("response.web_search_call.in_progress", stream_text)
        self.assertIn("response.web_search_call.searching", stream_text)
        self.assertIn("response.web_search_call.completed", stream_text)
        self.assertIn("searched.", stream_text)
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertIn(1, output_indexes)
        self.assertEqual(completed["model"], "test-model.gguf")
        self.assertEqual(completed["output"][0]["type"], "web_search_call")
        event_names = [event for event, _payload in events]
        web_search_items = [
            payload["item"]
            for event, payload in events
            if event in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "web_search_call"
        ]
        self.assertEqual(len(web_search_items), 2)
        self.assertEqual(web_search_items[0]["id"], web_search_items[1]["id"])
        self.assertLess(
            event_names.index("response.web_search_call.in_progress"),
            event_names.index("response.web_search_call.completed"),
        )
        started = [
            event for event in telemetry_events
            if event.get("type") == "tool_call_started"
        ]
        completed_events = [
            event for event in telemetry_events
            if event.get("type") == "tool_call_completed"
        ]
        self.assertEqual(len(started), 1)
        self.assertEqual(len(completed_events), 1)
        self.assertEqual(started[0]["request_id"], "req-web-golden")
        self.assertEqual(started[0]["payload"]["tool"], "web_search")
        self.assertEqual(started[0]["payload"]["execution"], "proxy_local")
        self.assertEqual(started[0]["payload"]["public_item_type"], "web_search_call")
        self.assertEqual(completed_events[0]["payload"]["tool"], "web_search")
        self.assertEqual(completed_events[0]["payload"]["upstream_items"], 2)

    def test_proxy_local_continuation_can_multi_hop_from_registry_lifecycle(self):
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            outputs = [
                item
                for item in body.get("input") or []
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            if len(outputs) == 0:
                return FakeStream(_named_web_call_stream(
                    "fc_web_one",
                    "call_web_one",
                    json.dumps({"action": "search", "query": "one"}),
                ))
            if len(outputs) == 1:
                return FakeStream(_named_web_call_stream(
                    "fc_web_two",
                    "call_web_two",
                    json.dumps({"action": "search", "query": "two"}),
                ))
            return FakeStream(_final_message_stream())

        stream_text = self._run_runtime(opener, web_runtime=web_runtime)
        events = _parse_sse_events(stream_text)
        event_names = [event for event, _payload in events]
        completed = next(
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        )

        self.assertEqual(len(requests), 3)
        self.assertEqual(len(web_runtime.calls), 2)
        self.assertEqual(web_runtime.calls[0]["call_item"]["call_id"], "call_web_one")
        self.assertEqual(web_runtime.calls[1]["call_item"]["call_id"], "call_web_two")
        self.assertEqual(event_names.count("response.web_search_call.in_progress"), 2)
        self.assertEqual(event_names.count("response.web_search_call.completed"), 2)
        self.assertEqual(event_names.count("response.created"), 1)
        self.assertEqual(event_names.count("response.completed"), 1)
        self.assertEqual(completed["output"][0]["type"], "web_search_call")
        self.assertEqual(completed["output"][1]["type"], "web_search_call")
        self.assertIn("searched.", stream_text)
        self.assertTrue(any(
            item.get("call_id") == "call_web_one"
            for item in requests[1]["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ))
        self.assertTrue(any(
            item.get("call_id") == "call_web_two"
            for item in requests[2]["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ))

    def test_proxy_local_continuation_multi_hop_raw_fixture_replay(self):
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            outputs = [
                item
                for item in body.get("input") or []
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            if len(outputs) == 0:
                return FakeStream(_fixture_chunks("web_search_call.raw"))
            if len(outputs) == 1:
                return FakeStream(_fixture_chunks("web_search_call_second.raw"))
            return FakeStream(_fixture_chunks("web_search_final.raw"))

        stream_text = self._run_runtime(opener, web_runtime=web_runtime)
        events = _parse_sse_events(stream_text)
        event_names = [event for event, _payload in events]
        completed = next(
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        )

        self.assertEqual(len(requests), 3)
        self.assertEqual(len(web_runtime.calls), 2)
        self.assertEqual(web_runtime.calls[0]["call_item"]["call_id"], "call_fixture_web")
        self.assertEqual(web_runtime.calls[1]["call_item"]["call_id"], "call_fixture_web_second")
        self.assertEqual(event_names.count("response.created"), 1)
        self.assertEqual(event_names.count("response.completed"), 1)
        self.assertEqual(event_names.count("response.web_search_call.in_progress"), 2)
        self.assertEqual(event_names.count("response.web_search_call.searching"), 2)
        self.assertEqual(event_names.count("response.web_search_call.completed"), 2)
        self.assertEqual(completed["output"][0]["type"], "web_search_call")
        self.assertEqual(completed["output"][1]["type"], "web_search_call")
        self.assertEqual(completed["output"][2]["type"], "message")
        self.assertNotIn('"type": "function_call"', stream_text)
        self.assertTrue(any(
            item.get("call_id") == "call_fixture_web"
            for item in requests[1]["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ))
        self.assertTrue(any(
            item.get("call_id") == "call_fixture_web_second"
            for item in requests[2]["input"]
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ))

    def test_proxy_local_final_usage_is_normalized_for_codex_status(self):
        def opener(body):
            outputs = [
                item
                for item in body.get("input") or []
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            if not outputs:
                return FakeStream(_named_web_call_stream(
                    "fc_web_one",
                    "call_web_one",
                    json.dumps({"action": "search", "query": "one"}),
                ))
            return FakeStream(_final_message_stream({
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 7},
                "completion_tokens_details": {"reasoning_tokens": 2},
            }))

        stream_text = self._run_runtime(opener, web_runtime=FakeWebRuntime())
        completed = next(
            payload["response"]
            for event, payload in _parse_sse_events(stream_text)
            if event == "response.completed" and isinstance(payload, dict)
        )
        usage = completed["usage"]

        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 4)
        self.assertEqual(usage["total_tokens"], 14)
        self.assertEqual(usage["input_tokens_details"]["cached_tokens"], 7)
        self.assertEqual(usage["output_tokens_details"]["reasoning_tokens"], 2)

    def test_web_search_continuation_suppresses_duplicate_response_start(self):
        telemetry = TelemetryBus()
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            fixture = "web_search_final.raw" if has_tool_output else "web_search_call.raw"
            return FakeStream(_fixture_chunks(fixture))

        stream_text = self._run_runtime(
            opener,
            telemetry=telemetry,
            request_id="req-web-duplicate-start",
        )
        events = _parse_sse_events(stream_text)
        timing_events = [
            event
            for event in telemetry.recent()
            if event.get("type") == "stream_event_timing"
        ]

        self.assertEqual(len(requests), 2)
        self.assertEqual([event for event, _payload in events].count("response.created"), 1)
        self.assertEqual([event for event, _payload in events].count("response.completed"), 1)
        self.assertTrue(any(
            (event.get("payload") or {}).get("event_type") == "response.created"
            and (event.get("payload") or {}).get("suppressed") == "duplicate_response_start"
            for event in timing_events
        ))

    def test_web_search_continuation_final_completed_without_done_appends_done_once(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            if has_tool_output:
                return FakeStream(_fixture_chunks("completed_without_done.raw"))
            return FakeStream(_fixture_chunks("web_search_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        completed = next(
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        )

        self.assertEqual(len(requests), 2)
        self.assertEqual([event for event, _payload in events].count("response.created"), 1)
        self.assertEqual([event for event, _payload in events].count("response.completed"), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertEqual(completed["output"][0]["type"], "web_search_call")

    def test_web_search_continuation_final_empty_close_emits_completed_once(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            if has_tool_output:
                return FakeStream([])
            return FakeStream(_fixture_chunks("web_search_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        completed = [
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        ]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(completed), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertEqual(completed[0]["output"][0]["type"], "web_search_call")
        self.assertNotIn('"type": "function_call"', stream_text)

    def test_web_search_continuation_final_done_only_emits_completed_once(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            if has_tool_output:
                return FakeStream(_fixture_chunks("done_only.raw"))
            return FakeStream(_fixture_chunks("web_search_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        completed = [
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        ]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(completed), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertEqual(completed[0]["output"][0]["type"], "web_search_call")
        self.assertNotIn('"type": "function_call"', stream_text)

    def test_web_search_continuation_malformed_terminal_emits_completed_once(self):
        requests = []

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == "call_fixture_web"
                for item in body.get("input") or []
            )
            if has_tool_output:
                return FakeStream(_fixture_chunks("malformed_terminal.raw"))
            return FakeStream(_fixture_chunks("web_search_call.raw"))

        stream_text = self._run_runtime(opener)
        events = _parse_sse_events(stream_text)
        completed = [
            payload["response"]
            for event, payload in events
            if event == "response.completed" and isinstance(payload, dict)
        ]

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(completed), 1)
        self.assertEqual(stream_text.count("data: [DONE]\n\n"), 1)
        self.assertTrue(stream_text.endswith("data: [DONE]\n\n"))
        self.assertEqual(completed[0]["output"][0]["type"], "web_search_call")
        self.assertNotIn('"type": "function_call"', stream_text)


    def test_hop_budget_signal_injected_when_hops_tight(self):
        # ProbeProxyToolExecutor has continuation_hops=2 so max_hops=2.
        # After hop 0 completes, hops_remaining = 2 - (0+1) = 1.
        # threshold=1 means 1 <= 1, so the signal should appear in the second call.
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_call_stream())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            hop_budget_signal_threshold=1,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_texts = [
            item["content"][0]["text"]
            for item in second_input
            if isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), list)
            and item["content"]
            and "continuation hop" in (item["content"][0].get("text") or "")
        ]
        self.assertEqual(len(signal_texts), 1, f"Expected 1 hop budget signal, got: {signal_texts}")
        self.assertIn("1 continuation hop", signal_texts[0])

    def test_hop_budget_signal_not_injected_when_hops_plentiful(self):
        # ProbeProxyToolExecutor has continuation_hops=2, threshold=1.
        # Wait — with continuation_hops=2 and threshold=1 the signal DOES fire.
        # Use threshold=0 (fires when remaining=0, but loop already ends).
        # Alternatively just verify: after hop 0, hops_remaining=1 > threshold=0.
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_call_stream())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            hop_budget_signal_threshold=0,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_msgs = [
            item for item in second_input
            if isinstance(item, dict)
            and item.get("role") == "user"
            and "continuation hop" in json.dumps(item.get("content") or "")
        ]
        self.assertEqual(len(signal_msgs), 0, "Hop budget signal should not be injected when threshold=0 and 1 hop remains")

    def test_hop_budget_signal_disabled_with_minus_one(self):
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_call_stream())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            hop_budget_signal_threshold=-1,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_msgs = [
            item for item in second_input
            if isinstance(item, dict)
            and "continuation hop" in json.dumps(item.get("content") or "")
        ]
        self.assertEqual(len(signal_msgs), 0, "Hop budget signal must be absent when threshold=-1")

    def test_hop_budget_signal_emits_telemetry_event(self):
        telemetry = TelemetryBus()
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            if not getattr(opener, "_called", False):
                opener._called = True
                return FakeStream(_probe_call_stream())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            telemetry=telemetry,
            hop_budget_signal_threshold=1,
        )

        events = [e for e in telemetry.recent(100) if e.get("type") == "hop_budget_signal"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["hops_remaining"], 1)

    def test_context_pressure_signal_injected_at_threshold(self):
        # selected_model with context_length=1000, usage reports 850 input tokens (85%).
        # Threshold 0.8 should trigger.
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])
        usage_with_tokens = {"input_tokens": 850, "output_tokens": 10, "total_tokens": 860}

        def _probe_stream_with_usage():
            return _named_function_call_stream(
                "fc_probe_ctx", "call_probe_ctx", "qz_probe", json.dumps({"value": 1})
            )[:-1] + [
                _sse_block("response.completed", {
                    "response": {
                        "id": "resp_probe_ctx",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "completed",
                        "model": "fake",
                        "output": [],
                        "usage": usage_with_tokens,
                    },
                }),
                b"data: [DONE]\n\n",
            ]

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_stream_with_usage())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            selected_model={"context_length": 1000},
            hop_budget_signal_threshold=-1,
            context_pressure_signal_threshold=0.8,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_texts = [
            item["content"][0]["text"]
            for item in second_input
            if isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), list)
            and item["content"]
            and "Context window" in (item["content"][0].get("text") or "")
        ]
        self.assertEqual(len(signal_texts), 1, f"Expected 1 context pressure signal, got: {signal_texts}")
        self.assertIn("85%", signal_texts[0])

    def test_context_pressure_signal_not_injected_below_threshold(self):
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])
        usage_with_tokens = {"input_tokens": 500, "output_tokens": 10}

        def _probe_stream_with_usage():
            return _named_function_call_stream(
                "fc_probe_lo", "call_probe_lo", "qz_probe", json.dumps({"value": 1})
            )[:-1] + [
                _sse_block("response.completed", {
                    "response": {
                        "id": "resp_probe_lo",
                        "object": "response",
                        "created_at": 4102444800,
                        "status": "completed",
                        "model": "fake",
                        "output": [],
                        "usage": usage_with_tokens,
                    },
                }),
                b"data: [DONE]\n\n",
            ]

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_stream_with_usage())
            return FakeStream(_final_message_stream())

        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            selected_model={"context_length": 1000},
            hop_budget_signal_threshold=-1,
            context_pressure_signal_threshold=0.8,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_msgs = [
            item for item in second_input
            if isinstance(item, dict)
            and "Context window" in json.dumps(item.get("content") or "")
        ]
        self.assertEqual(len(signal_msgs), 0, "Context pressure signal must not fire when fill ratio is below threshold")

    def test_context_pressure_signal_skipped_without_context_length(self):
        requests = []
        registry = ProxyLocalToolRegistry([ProbeProxyToolExecutor()])

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1:
                return FakeStream(_probe_call_stream())
            return FakeStream(_final_message_stream())

        # No context_length in selected_model → signal should not fire even if threshold met.
        self._run_runtime(
            opener,
            proxy_tool_registry=registry,
            selected_model={"name": "no-context-length"},
            hop_budget_signal_threshold=-1,
            context_pressure_signal_threshold=0.0,
        )

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        signal_msgs = [
            item for item in second_input
            if isinstance(item, dict)
            and "Context window" in json.dumps(item.get("content") or "")
        ]
        self.assertEqual(len(signal_msgs), 0)


class SandboxEscalationDetectionTests(unittest.TestCase):
    """Tests for tool_escalation_requested telemetry on outgoing require_escalated calls."""

    def _make_runtime(self):
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=lambda _: None,
            capture_enabled=False,
        )
        return runtime

    def _exec_command_call(self, sandbox_permissions="use_default", cmd="ls", justification=""):
        args = {"cmd": cmd}
        if sandbox_permissions:
            args["sandbox_permissions"] = sandbox_permissions
        if justification:
            args["justification"] = justification
        return {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_test_1",
            "id": "fc_test_1",
            "arguments": json.dumps(args),
        }

    # ------------------------------------------------------------------
    # _check_sandbox_escalation unit tests
    # ------------------------------------------------------------------

    def test_require_escalated_returns_payload(self):
        rt = self._make_runtime()
        call = self._exec_command_call(sandbox_permissions="require_escalated", cmd="sudo thing")
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertEqual(result["sandbox_permissions"], "require_escalated")
        self.assertEqual(result["tool"], "exec_command")
        self.assertEqual(result["call_id"], "call_test_1")
        self.assertIn("sudo thing", result["cmd_preview"])

    def test_use_default_returns_none(self):
        rt = self._make_runtime()
        call = self._exec_command_call(sandbox_permissions="use_default")
        self.assertIsNone(rt._check_sandbox_escalation(call))

    def test_absent_sandbox_permissions_returns_none(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_no_perm",
            "arguments": json.dumps({"cmd": "ls"}),
        }
        self.assertIsNone(rt._check_sandbox_escalation(call))

    def test_non_json_arguments_returns_none_without_crash(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_bad_args",
            "arguments": "NOT VALID JSON {{{",
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNone(result)

    def test_empty_arguments_returns_none(self):
        rt = self._make_runtime()
        call = {"type": "function_call", "name": "exec_command", "call_id": "c", "arguments": ""}
        self.assertIsNone(rt._check_sandbox_escalation(call))

    def test_null_call_returns_none(self):
        rt = self._make_runtime()
        self.assertIsNone(rt._check_sandbox_escalation(None))

    def test_justification_included_in_payload(self):
        rt = self._make_runtime()
        call = self._exec_command_call(
            sandbox_permissions="require_escalated",
            cmd="systemctl restart thing",
            justification="need to restart service for task",
        )
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIn("restart service", result["justification"])

    def test_cmd_preview_is_truncated_at_80_chars(self):
        rt = self._make_runtime()
        long_cmd = "x" * 200
        call = self._exec_command_call(sandbox_permissions="require_escalated", cmd=long_cmd)
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result["cmd_preview"]), 80)

    def test_justification_truncated_at_200_chars(self):
        rt = self._make_runtime()
        long_just = "j" * 300
        call = self._exec_command_call(
            sandbox_permissions="require_escalated",
            justification=long_just,
        )
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result["justification"]), 200)

    def test_non_exec_command_tool_with_require_escalated_detected(self):
        """Any tool can theoretically set sandbox_permissions; detect them all."""
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "some_other_tool",
            "call_id": "call_other",
            "arguments": json.dumps({"sandbox_permissions": "require_escalated"}),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "some_other_tool")

    # ------------------------------------------------------------------
    # _safe_preview: non-string model-provided argument coercion
    # ------------------------------------------------------------------

    def test_safe_preview_string_is_used_directly(self):
        rt = self._make_runtime()
        self.assertEqual(rt._safe_preview("hello", 10), "hello")

    def test_safe_preview_string_truncated(self):
        rt = self._make_runtime()
        self.assertEqual(rt._safe_preview("abcdef", 4), "abcd")

    def test_safe_preview_none_returns_empty_string(self):
        rt = self._make_runtime()
        self.assertEqual(rt._safe_preview(None, 80), "")

    def test_safe_preview_int_returns_string(self):
        rt = self._make_runtime()
        result = rt._safe_preview(42, 80)
        self.assertIsInstance(result, str)
        self.assertEqual(result, "42")

    def test_safe_preview_bool_returns_string(self):
        rt = self._make_runtime()
        self.assertIsInstance(rt._safe_preview(True, 80), str)

    def test_safe_preview_list_returns_compact_json_string(self):
        rt = self._make_runtime()
        result = rt._safe_preview(["ls", "-la"], 80)
        self.assertIsInstance(result, str)
        self.assertIn("ls", result)

    def test_safe_preview_dict_returns_compact_json_string(self):
        rt = self._make_runtime()
        result = rt._safe_preview({"key": "val"}, 80)
        self.assertIsInstance(result, str)
        self.assertIn("val", result)

    def test_safe_preview_list_truncated_at_limit(self):
        rt = self._make_runtime()
        result = rt._safe_preview(["a"] * 100, 20)
        self.assertLessEqual(len(result), 20)

    # ------------------------------------------------------------------
    # _check_sandbox_escalation: non-string cmd and justification
    # ------------------------------------------------------------------

    def test_cmd_as_list_produces_string_cmd_preview(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_list_cmd",
            "arguments": json.dumps({
                "cmd": ["sudo", "systemctl", "restart", "nginx"],
                "sandbox_permissions": "require_escalated",
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["cmd_preview"], str)
        self.assertIn("nginx", result["cmd_preview"])

    def test_cmd_as_dict_produces_string_cmd_preview(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_dict_cmd",
            "arguments": json.dumps({
                "cmd": {"program": "sudo", "args": ["id"]},
                "sandbox_permissions": "require_escalated",
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["cmd_preview"], str)

    def test_cmd_as_int_produces_string_cmd_preview(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_int_cmd",
            "arguments": json.dumps({
                "cmd": 0,
                "sandbox_permissions": "require_escalated",
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["cmd_preview"], str)

    def test_justification_as_int_produces_string(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_int_just",
            "arguments": json.dumps({
                "sandbox_permissions": "require_escalated",
                "justification": 42,
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["justification"], str)
        self.assertEqual(result["justification"], "42")

    def test_justification_as_list_produces_string(self):
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_list_just",
            "arguments": json.dumps({
                "sandbox_permissions": "require_escalated",
                "justification": ["need", "host", "access"],
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        self.assertIsInstance(result["justification"], str)
        self.assertIn("host", result["justification"])

    def test_all_payload_fields_are_strings(self):
        """Regardless of argument types, every telemetry payload field is a str."""
        rt = self._make_runtime()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_mixed",
            "arguments": json.dumps({
                "cmd": ["sudo", "id"],
                "sandbox_permissions": "require_escalated",
                "justification": 99,
            }),
        }
        result = rt._check_sandbox_escalation(call)
        self.assertIsNotNone(result)
        for key, value in result.items():
            self.assertIsInstance(value, str, f"field '{key}' is {type(value).__name__}, expected str")

    # ------------------------------------------------------------------
    # Integration test: event emitted through the stream loop
    # ------------------------------------------------------------------

    def _run_escalation_stream(self, args_json, *, telemetry=None):
        """Run a single-hop stream with an exec_command function_call and return events."""
        stream_chunks = _named_function_call_stream(
            "fc_esc", "call_esc", "exec_command", args_json
        )
        chunks = []

        def opener(body):
            return FakeStream(stream_chunks)

        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="req_esc_test",
        )
        body = {"model": "fake", "input": [], "tools": []}
        runtime.run(body, "fake")
        return chunks

    def test_stream_emits_tool_escalation_requested_telemetry(self):
        """Full stream: exec_command with require_escalated emits tool_escalation_requested."""
        args = json.dumps({
            "cmd": "sudo systemctl restart nginx",
            "sandbox_permissions": "require_escalated",
            "justification": "restart web server for task",
        })
        telemetry = TelemetryBus()
        self._run_escalation_stream(args, telemetry=telemetry)

        events = telemetry.recent()
        escalation_events = [e for e in events if e["type"] == "tool_escalation_requested"]
        self.assertTrue(
            len(escalation_events) >= 1,
            f"Expected tool_escalation_requested event, got: {[e['type'] for e in events]}",
        )
        payload = escalation_events[0]["payload"]
        self.assertEqual(payload["tool"], "exec_command")
        self.assertEqual(payload["sandbox_permissions"], "require_escalated")
        self.assertIn("restart web server", payload["justification"])
        self.assertIn("nginx", payload["cmd_preview"])

    def test_stream_no_escalation_event_for_normal_exec_command(self):
        """Normal exec_command (use_default) does not emit tool_escalation_requested."""
        args = json.dumps({"cmd": "ls -la", "sandbox_permissions": "use_default"})
        telemetry = TelemetryBus()
        self._run_escalation_stream(args, telemetry=telemetry)

        events = telemetry.recent()
        escalation_events = [e for e in events if e["type"] == "tool_escalation_requested"]
        self.assertEqual(escalation_events, [])


class RepeatedReadStreamingTests(unittest.TestCase):
    """Tests for repeated-read signal in the streaming path."""

    def _exec_stream(self, cmd, call_id="call_rr"):
        arguments = json.dumps({"cmd": cmd})
        return _named_function_call_stream(call_id, call_id, "exec_command", arguments)

    def _run_with_history(self, history_calls, stream_cmd, call_id="call_rr"):
        """Build body with prior history, run stream, return telemetry events."""
        # Build input history
        input_history = [
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": f"hist_{i}",
                "arguments": json.dumps({"cmd": cmd}),
            }
            for i, cmd in enumerate(history_calls)
        ]

        stream_chunks = self._exec_stream(stream_cmd, call_id)
        telemetry = TelemetryBus()

        def opener(body):
            # On second hop return final answer
            has_rr_output = any(
                isinstance(item, dict) and item.get("type") == "function_call_output"
                and item.get("call_id") == call_id
                for item in (body.get("input") or [])
            )
            return FakeStream(_final_message_stream() if has_rr_output else stream_chunks)

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="req_rr_test",
        )
        body = {"model": "fake", "input": input_history, "tools": []}
        runtime.run(body, "fake")
        return telemetry.recent()

    def test_stream_emits_repeated_read_signal_for_history_read(self):
        """Prior read in history → repeated exec_command → repeated_read_signal emitted."""
        events = self._run_with_history(["cat README.md"], "cat README.md")
        rr_events = [e for e in events if e["type"] == "repeated_read_signal"]
        self.assertTrue(len(rr_events) >= 1, f"No repeated_read_signal; events: {[e['type'] for e in events]}")

    def test_stream_repeated_read_signal_not_tool_call_error(self):
        """Signal must not emit tool_call_error."""
        events = self._run_with_history(["cat README.md"], "cat README.md")
        error_events = [e for e in events if e["type"] == "tool_call_error"]
        self.assertEqual(error_events, [])

    def test_stream_repeated_read_signal_has_correct_payload(self):
        events = self._run_with_history(["cat README.md"], "cat README.md", "call_rr_p")
        rr_events = [e for e in events if e["type"] == "repeated_read_signal"]
        self.assertTrue(len(rr_events) >= 1)
        p = rr_events[0]["payload"]
        self.assertIn("README.md", p.get("paths", []))
        self.assertEqual(p.get("action"), "signalled")
        self.assertEqual(p.get("tool"), "exec_command")

    def test_stream_first_read_no_signal(self):
        """First read (no history) — no repeated_read_signal."""
        events = self._run_with_history([], "cat README.md")
        rr_events = [e for e in events if e["type"] == "repeated_read_signal"]
        self.assertEqual(rr_events, [])

    def test_stream_repeated_read_signal_in_retained_telemetry(self):
        """repeated_read_signal must be in REQUEST_LIFECYCLE_EVENT_TYPES (retained)."""
        from proxy.qz_telemetry import REQUEST_LIFECYCLE_EVENT_TYPES
        self.assertIn("repeated_read_signal", REQUEST_LIFECYCLE_EVENT_TYPES)


class StreamHopStateTests(unittest.TestCase):
    """Unit tests for StreamHopState — #37 Slice 1."""

    def _minimal_hop_body(self):
        return {"input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": "hi"}]}]}

    def test_fresh_defaults_event_lists(self):
        hs = StreamHopState.fresh(self._minimal_hop_body())
        self.assertEqual(hs.event_lines, [])
        self.assertIsNone(hs.event_started_at)

    def test_fresh_next_input_from_hop_body(self):
        body = self._minimal_hop_body()
        hs = StreamHopState.fresh(body)
        self.assertEqual(len(hs.next_input), 1)
        self.assertEqual(hs.next_input[0]["role"], "user")

    def test_fresh_next_input_empty_when_no_input(self):
        hs = StreamHopState.fresh({})
        self.assertEqual(hs.next_input, [])

    def test_fresh_completed_call_is_none(self):
        hs = StreamHopState.fresh({})
        self.assertIsNone(hs.completed_call)

    def test_fresh_injection_flags_false(self):
        hs = StreamHopState.fresh({})
        self.assertFalse(hs.error_injected)
        self.assertFalse(hs.signal_injected)
        self.assertFalse(hs.repair_injected)

    def test_fresh_output_counters_zero(self):
        hs = StreamHopState.fresh({})
        self.assertEqual(hs.output_text_chars, 0)
        self.assertEqual(hs.reasoning_only_chars, 0)
        self.assertEqual(hs.reasoning_only_sample, "")
        self.assertEqual(hs.max_output_index, -1)

    def test_fresh_item_seen_flags_false(self):
        hs = StreamHopState.fresh({})
        self.assertFalse(hs.visible_output_text_seen)
        self.assertFalse(hs.assistant_item_seen)
        self.assertFalse(hs.public_item_seen)

    def test_fresh_tool_call_state_is_new_instance(self):
        from proxy.qz_tool_lifecycle import StreamToolCallState
        hs = StreamHopState.fresh({})
        self.assertIsNotNone(hs.tool_call_state)
        self.assertIsInstance(hs.tool_call_state, StreamToolCallState)

    def test_fresh_watchdog_state_is_initialised(self):
        from proxy.qz_stream_watchdog import StreamWatchdogState
        hs = StreamHopState.fresh({}, stream_no_output_timeout_s=30.0,
                                   stream_terminal_timeout_s=10.0)
        self.assertIsNotNone(hs.watchdog_state)
        self.assertIsInstance(hs.watchdog_state, StreamWatchdogState)

    def test_fresh_stream_obs_acc_has_request_id(self):
        hs = StreamHopState.fresh({}, request_id="req-test-123")
        self.assertEqual(hs.stream_obs_acc.get("request_id"), "req-test-123")

    def test_fresh_reasoning_only_timestamps_none(self):
        hs = StreamHopState.fresh({})
        self.assertIsNone(hs.reasoning_only_started_at)
        self.assertIsNone(hs.reasoning_only_last_delta_at)

    def test_two_fresh_calls_return_independent_state(self):
        hs1 = StreamHopState.fresh({})
        hs2 = StreamHopState.fresh({})
        hs1.event_lines.append(b"test")
        self.assertEqual(hs2.event_lines, [], "fresh() must return independent state")

    def test_fresh_next_input_is_copy_not_reference(self):
        body = self._minimal_hop_body()
        hs = StreamHopState.fresh(body)
        hs.next_input.append({"extra": True})
        self.assertEqual(len(body["input"]), 1, "mutating next_input must not affect hop_body")

    def test_fresh_returns_independent_tool_call_state(self):
        """Two fresh states must have distinct StreamToolCallState instances."""
        from proxy.qz_tool_lifecycle import StreamToolCallState
        hs1 = StreamHopState.fresh({})
        hs2 = StreamHopState.fresh({})
        self.assertIsNot(hs1.tool_call_state, hs2.tool_call_state,
                         "fresh() must create independent StreamToolCallState instances")

    def test_fresh_returns_independent_watchdog_state(self):
        """Two fresh states must have distinct StreamWatchdogState instances."""
        from proxy.qz_stream_watchdog import StreamWatchdogState
        hs1 = StreamHopState.fresh({}, stream_no_output_timeout_s=30.0)
        hs2 = StreamHopState.fresh({}, stream_no_output_timeout_s=30.0)
        self.assertIsNot(hs1.watchdog_state, hs2.watchdog_state,
                         "fresh() must create independent StreamWatchdogState instances")

    def test_fresh_returns_independent_stream_obs_acc(self):
        """Two fresh states must have distinct stream_obs_acc dicts."""
        hs1 = StreamHopState.fresh({}, request_id="r1")
        hs2 = StreamHopState.fresh({}, request_id="r2")
        self.assertIsNot(hs1.stream_obs_acc, hs2.stream_obs_acc)
        hs1.stream_obs_acc["extra"] = True
        self.assertNotIn("extra", hs2.stream_obs_acc)

    def test_fresh_returns_independent_next_input_lists(self):
        """Two fresh states with the same hop_body must have independent next_input lists."""
        body = {"input": [{"type": "message", "role": "user"}]}
        hs1 = StreamHopState.fresh(body)
        hs2 = StreamHopState.fresh(body)
        self.assertIsNot(hs1.next_input, hs2.next_input)
        hs1.next_input.append("extra")
        self.assertNotIn("extra", hs2.next_input)



class DuplicateResponseStartHelperTests(unittest.TestCase):
    """Unit tests for _should_suppress_duplicate_response_start() — #37 Slice 2C."""

    def test_response_created_duplicate_suppressed(self):
        self.assertTrue(
            _should_suppress_duplicate_response_start("response.created", True)
        )

    def test_response_in_progress_duplicate_suppressed(self):
        self.assertTrue(
            _should_suppress_duplicate_response_start("response.in_progress", True)
        )

    def test_response_created_first_not_suppressed(self):
        self.assertFalse(
            _should_suppress_duplicate_response_start("response.created", False)
        )

    def test_response_in_progress_first_not_suppressed(self):
        self.assertFalse(
            _should_suppress_duplicate_response_start("response.in_progress", False)
        )

    def test_unrelated_event_not_suppressed_even_if_sent(self):
        self.assertFalse(
            _should_suppress_duplicate_response_start("response.output_text.delta", True)
        )

    def test_done_event_not_suppressed(self):
        self.assertFalse(
            _should_suppress_duplicate_response_start("done", True)
        )


class ContextPressureSignalHelperTests(unittest.TestCase):
    """Unit tests for _should_inject_context_pressure_signal() — #37 Slice 2E."""

    def test_threshold_zero_disables_signal(self):
        self.assertFalse(_should_inject_context_pressure_signal(900, 1000, 0.0))

    def test_threshold_negative_disables_signal(self):
        self.assertFalse(_should_inject_context_pressure_signal(900, 1000, -1.0))

    def test_context_length_zero_returns_false(self):
        self.assertFalse(_should_inject_context_pressure_signal(500, 0, 0.8))

    def test_context_length_negative_returns_false(self):
        self.assertFalse(_should_inject_context_pressure_signal(500, -100, 0.8))

    def test_input_tokens_zero_returns_false(self):
        self.assertFalse(_should_inject_context_pressure_signal(0, 1000, 0.8))

    def test_below_threshold_returns_false(self):
        # 500/1000 = 0.5 < 0.8 → no signal
        self.assertFalse(_should_inject_context_pressure_signal(500, 1000, 0.8))

    def test_exactly_at_threshold_returns_true(self):
        # Uses >= comparison: 800/1000 = 0.8 >= 0.8 → signal
        self.assertTrue(_should_inject_context_pressure_signal(800, 1000, 0.8))

    def test_above_threshold_returns_true(self):
        # 900/1000 = 0.9 >= 0.8 → signal
        self.assertTrue(_should_inject_context_pressure_signal(900, 1000, 0.8))

    def test_input_tokens_exceeds_context_length_returns_true(self):
        # fill ratio > 1.0 — signal fires if threshold is enabled
        self.assertTrue(_should_inject_context_pressure_signal(1200, 1000, 0.8))

    def test_float_input_tokens_at_threshold_returns_true(self):
        # input_tokens may be float per the parent coercion path
        self.assertTrue(_should_inject_context_pressure_signal(800.0, 1000, 0.8))

    def test_threshold_one_at_ratio_one_returns_true(self):
        # threshold = 1.0, ratio = 1.0 → exactly at threshold → signal
        self.assertTrue(_should_inject_context_pressure_signal(1000, 1000, 1.0))

    def test_threshold_one_below_ratio_returns_false(self):
        # threshold = 1.0, ratio < 1.0 → no signal
        self.assertFalse(_should_inject_context_pressure_signal(999, 1000, 1.0))


class HopBudgetSignalHelperTests(unittest.TestCase):
    """Unit tests for _should_inject_hop_budget_signal() — #37 Slice 2D."""

    def test_threshold_minus_one_disables_signal(self):
        self.assertFalse(_should_inject_hop_budget_signal(0, -1))

    def test_threshold_minus_one_disables_signal_any_remaining(self):
        self.assertFalse(_should_inject_hop_budget_signal(5, -1))

    def test_threshold_zero_hops_remaining_zero_injects(self):
        self.assertTrue(_should_inject_hop_budget_signal(0, 0))

    def test_threshold_zero_hops_remaining_one_skips(self):
        self.assertFalse(_should_inject_hop_budget_signal(1, 0))

    def test_threshold_one_hops_remaining_one_injects(self):
        self.assertTrue(_should_inject_hop_budget_signal(1, 1))

    def test_threshold_one_hops_remaining_two_skips(self):
        self.assertFalse(_should_inject_hop_budget_signal(2, 1))

    def test_threshold_three_hops_remaining_zero_injects(self):
        self.assertTrue(_should_inject_hop_budget_signal(0, 3))

    def test_threshold_three_hops_remaining_three_injects(self):
        self.assertTrue(_should_inject_hop_budget_signal(3, 3))

    def test_threshold_three_hops_remaining_four_skips(self):
        self.assertFalse(_should_inject_hop_budget_signal(4, 3))

    def test_negative_hops_remaining_with_positive_threshold_injects(self):
        # hops_remaining can go negative if hop_index overruns max_hops.
        # The conservative behaviour is to inject (remaining <= threshold),
        # consistent with treating a negative remaining as "very tight".
        self.assertTrue(_should_inject_hop_budget_signal(-1, 0))


class StreamDecisionTests(unittest.TestCase):
    """Unit tests for StreamDecision dataclass — #37 Slice 2B."""

    def test_stream_decision_defaults(self):
        d = StreamDecision(kind="continue")
        self.assertEqual(d.kind, "continue")
        self.assertEqual(d.reason, "")
        self.assertEqual(d.suppress_reason, "")
        self.assertEqual(d.upstream_items_to_append, [])
        self.assertIsNone(d.public_item_hint)
        self.assertIsNone(d.terminal_hint)
        self.assertIsNone(d.repair_hop_index)
        self.assertIsNone(d.carry_forward_snippet)
        self.assertIsNone(d.hop_budget_signal)
        self.assertIsNone(d.context_pressure_signal)
        self.assertEqual(d.telemetry_hint, "")
        self.assertEqual(d.warnings, [])

    def test_stream_decision_lists_are_independent(self):
        d1 = StreamDecision(kind="continue")
        d2 = StreamDecision(kind="continue")
        d1.upstream_items_to_append.append("x")
        d1.warnings.append("w")
        self.assertEqual(d2.upstream_items_to_append, [])
        self.assertEqual(d2.warnings, [])

    def test_stream_decision_kind_set_explicitly(self):
        d = StreamDecision(kind="abort_reasoning_only", reason="timeout")
        self.assertEqual(d.kind, "abort_reasoning_only")
        self.assertEqual(d.reason, "timeout")


class ReasoningOnlyAbortHelperTests(unittest.TestCase):
    """Unit tests for _reasoning_only_abort_reason() — #37 Slice 2B.

    Verifies that extraction preserved exact comparison semantics and priority.
    """

    # A sample that triggers the artifact detector: starts like JSON, has patch-shape markers
    _ARTIFACT_SAMPLE = (
        '{"operation": "update_file", "path": "/foo.py", '
        '"diff": "--- a/foo.py\\n+++ b/foo.py\\n@@ -1 +1 @@\\n-old\\n+new"}'
    )

    def test_artifact_wins_over_timeout(self):
        """Artifact check has highest priority."""
        result = _reasoning_only_abort_reason(
            reasoning_only_sample=self._ARTIFACT_SAMPLE,
            reasoning_only_chars=9999,
            reasoning_only_progress_at=0.0,
            now=9999.0,  # idle >> timeout
            reasoning_only_timeout_s=1.0,
            reasoning_only_char_limit=1,
        )
        self.assertEqual(result, "artifact_tool_payload")

    def test_artifact_wins_over_char_limit(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample=self._ARTIFACT_SAMPLE,
            reasoning_only_chars=9999,
            reasoning_only_progress_at=None,
            now=0.0,
            reasoning_only_timeout_s=-1.0,
            reasoning_only_char_limit=1,
        )
        self.assertEqual(result, "artifact_tool_payload")

    def test_timeout_returns_timeout(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=0.0,
            now=200.0,  # idle = 200 > timeout = 120
            reasoning_only_timeout_s=120.0,
            reasoning_only_char_limit=-1,
        )
        self.assertEqual(result, "timeout")

    def test_char_limit_returns_char_limit(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=101,
            reasoning_only_progress_at=None,
            now=0.0,
            reasoning_only_timeout_s=-1.0,
            reasoning_only_char_limit=100,
        )
        self.assertEqual(result, "char_limit")

    def test_disabled_timeout_negative_one_does_not_abort(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=0.0,
            now=9999.0,
            reasoning_only_timeout_s=-1.0,
            reasoning_only_char_limit=-1,
        )
        self.assertIsNone(result)

    def test_disabled_char_limit_negative_one_does_not_abort(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=9999,
            reasoning_only_progress_at=0.0,
            now=0.1,
            reasoning_only_timeout_s=-1.0,
            reasoning_only_char_limit=-1,
        )
        self.assertIsNone(result)

    def test_timeout_exact_boundary_does_not_abort(self):
        """idle == timeout_s must NOT abort — code uses strict > not >=."""
        timeout_s = 120.0
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=0.0,
            now=timeout_s,  # idle == timeout_s exactly
            reasoning_only_timeout_s=timeout_s,
            reasoning_only_char_limit=-1,
        )
        self.assertIsNone(result, "idle == timeout_s must not abort (uses strict >)")

    def test_char_limit_exact_boundary_does_not_abort(self):
        """chars == limit must NOT abort — code uses strict > not >=."""
        limit = 100
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=limit,  # exactly at limit
            reasoning_only_progress_at=None,
            now=0.0,
            reasoning_only_timeout_s=-1.0,
            reasoning_only_char_limit=limit,
        )
        self.assertIsNone(result, "chars == limit must not abort (uses strict >)")

    def test_none_progress_at_skips_timeout_check(self):
        """If progress_at is None, timeout check must be skipped safely."""
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=None,
            now=9999.0,
            reasoning_only_timeout_s=1.0,  # enabled
            reasoning_only_char_limit=-1,
        )
        self.assertIsNone(result)

    def test_no_conditions_returns_none(self):
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal safe text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=1000.0,
            now=1001.0,  # only 1s idle, timeout=120
            reasoning_only_timeout_s=120.0,
            reasoning_only_char_limit=1000,
        )
        self.assertIsNone(result)

    def test_timeout_zero_aborts_immediately(self):
        """timeout_s = 0 is enabled; any idle > 0 aborts."""
        result = _reasoning_only_abort_reason(
            reasoning_only_sample="normal text",
            reasoning_only_chars=10,
            reasoning_only_progress_at=0.0,
            now=0.001,  # tiny idle > 0
            reasoning_only_timeout_s=0.0,
            reasoning_only_char_limit=-1,
        )
        self.assertEqual(result, "timeout")


class ResponseIdThreadingTests(unittest.TestCase):
    """Tests for response.id threading through StreamRunState (Fix Pass I)."""

    def _run_and_parse(self, stream_chunks, web_runtime=None, telemetry=None):
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=web_runtime or FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(stream_chunks),
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
        }
        runtime.run(body, "test-model.gguf")
        return _parse_sse_events(b"".join(chunks).decode("utf-8"))

    def test_no_tool_response_id_matches_created_to_completed(self):
        """No-tool stream: response.created.id == response.completed.response.id."""
        events = self._run_and_parse(_fixture_chunks("basic_message.raw"))
        created = next(
            (p for et, p in events if et == "response.created" and isinstance(p, dict)),
            None,
        )
        completed = next(
            (p for et, p in events if et == "response.completed" and isinstance(p, dict)),
            None,
        )
        self.assertIsNotNone(created)
        self.assertIsNotNone(completed)
        created_id = (created.get("response") or {}).get("id")
        completed_id = (completed.get("response") or {}).get("id")
        self.assertIsNotNone(created_id)
        self.assertEqual(created_id, completed_id)

    def test_multi_hop_synthesised_terminal_uses_upstream_response_id(self):
        """After web_search continuation, synthesised response.completed uses hop-1 response.id."""
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_tool_output = any(
                isinstance(item, dict) and item.get("type") == "function_call_output"
                for item in body.get("input") or []
            )
            return FakeStream(_final_message_stream() if has_tool_output else _web_call_stream())

        events = self._run_and_parse([], web_runtime=web_runtime)
        # Run properly with the opener
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=web_runtime,
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }
        runtime.run(body, "test-model.gguf")
        events = _parse_sse_events(b"".join(chunks).decode("utf-8"))

        created_events = [(et, p) for et, p in events if et == "response.created"]
        completed_events = [(et, p) for et, p in events if et == "response.completed"]
        self.assertEqual(len(created_events), 1)
        self.assertEqual(len(completed_events), 1)

        created_id = (created_events[0][1].get("response") or {}).get("id")
        completed_id = (completed_events[0][1].get("response") or {}).get("id")
        self.assertIsNotNone(created_id)
        self.assertIsNotNone(completed_id)
        # After tool continuation, the synthesised completed must use the upstream ID
        self.assertEqual(created_id, completed_id)

    def test_upstream_response_id_initialises_empty(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertEqual(rs.upstream_response_id, "")

    def test_synthesise_response_id_returns_upstream_when_set(self):
        rid = ResponsesStreamRuntime._synthesize_response_id("resp_from_upstream", "req1")
        self.assertEqual(rid, "resp_from_upstream")

    def test_synthesise_response_id_fallback_is_unique(self):
        id1 = ResponsesStreamRuntime._synthesize_response_id("", "req1")
        id2 = ResponsesStreamRuntime._synthesize_response_id("", "req1")
        self.assertNotEqual(id1, id2)

    def test_synthesise_response_id_fallback_not_bare_timestamp(self):
        import re
        rid = ResponsesStreamRuntime._synthesize_response_id("", "req_abc")
        self.assertRegex(rid, r"resp_local_")
        # Should not be just `resp_local_<digits>` — must have more content
        self.assertFalse(re.fullmatch(r"resp_local_\d+", rid))

    def test_usage_synthetic_telemetry_emitted_on_zero_usage_fallback(self):
        """When usage is all-zeros in a fallback terminal, usage_synthetic is emitted."""
        telemetry = TelemetryBus()
        # reasoning abort triggers a synthesised terminal with no upstream usage
        stream_chunks = _stuck_reasoning_only_stream(delta_count=4)
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="summary",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(stream_chunks),
            capture_enabled=False,
            telemetry=telemetry,
            reasoning_only_char_limit=12,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
        }
        runtime.run(body, "test-model.gguf")
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "usage_synthetic" for e in telem_events),
            [e.get("type") for e in telem_events],
        )
        usage_evt = next(e for e in telem_events if e.get("type") == "usage_synthetic")
        self.assertTrue((usage_evt.get("payload") or {}).get("usage_unknown"))

    def test_normal_completion_no_usage_synthetic_telemetry(self):
        """Normal upstream usage does not trigger usage_synthetic telemetry."""
        telemetry = TelemetryBus()
        events = self._run_and_parse(_fixture_chunks("basic_message.raw"), telemetry=telemetry)
        telem_events = telemetry.recent()
        self.assertFalse(any(e.get("type") == "usage_synthetic" for e in telem_events))


class StreamRunStateTests(unittest.TestCase):
    """Unit tests for StreamRunState dataclass — #37 Slices 2H-impl/2I-impl."""

    def test_fresh_returns_all_false_defaults(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertFalse(rs.sent_response_start)
        self.assertFalse(rs.sent_terminal)
        self.assertFalse(rs.sent_done)

    def test_fresh_returns_new_instance_each_time(self):
        rs1 = StreamRunState.fresh(started_at=0.0)
        rs2 = StreamRunState.fresh(started_at=0.0)
        self.assertIsNot(rs1, rs2)

    def test_fields_are_independent_between_instances(self):
        rs1 = StreamRunState.fresh(started_at=0.0)
        rs2 = StreamRunState.fresh(started_at=0.0)
        rs1.sent_terminal = True
        rs1.sent_done = True
        self.assertFalse(rs2.sent_terminal)
        self.assertFalse(rs2.sent_done)

    def test_flags_can_be_mutated_independently(self):
        rs = StreamRunState.fresh(started_at=0.0)
        rs.sent_response_start = True
        self.assertTrue(rs.sent_response_start)
        self.assertFalse(rs.sent_terminal)
        self.assertFalse(rs.sent_done)
        rs.sent_terminal = True
        self.assertTrue(rs.sent_terminal)
        self.assertFalse(rs.sent_done)

    def test_fresh_requires_started_at(self):
        with self.assertRaises(TypeError):
            StreamRunState.fresh()  # missing required argument

    def test_fresh_started_at_preserved(self):
        rs = StreamRunState.fresh(started_at=123.456)
        self.assertEqual(rs.started_at, 123.456)

    def test_first_output_at_defaults_to_none(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertIsNone(rs.first_output_at)

    def test_output_index_offset_defaults_to_zero(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertEqual(rs.output_index_offset, 0)

    def test_final_usage_defaults_to_empty_normalized(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertIsInstance(rs.final_usage, dict)

    def test_independence_for_timing_fields(self):
        rs1 = StreamRunState.fresh(started_at=0.0)
        rs2 = StreamRunState.fresh(started_at=0.0)
        # Mutating rs1 must not affect rs2
        rs1.final_usage["input_tokens"] = 999
        rs1.output_index_offset = 5
        self.assertNotEqual(rs2.final_usage.get("input_tokens"), 999)
        self.assertEqual(rs2.output_index_offset, 0)
        # Also confirm the dicts are different objects
        self.assertIsNot(rs1.final_usage, rs2.final_usage)

    def test_upstream_response_id_defaults_to_empty(self):
        rs = StreamRunState.fresh(started_at=0.0)
        self.assertEqual(rs.upstream_response_id, "")


class ProxyLocalTerminalSuppressionHelperTests(unittest.TestCase):
    """Unit tests for _should_suppress_proxy_local_terminal() — #37 Slice 2G."""

    def test_returns_true_when_all_conditions_met(self):
        result = _should_suppress_proxy_local_terminal(
            event_type="response.completed",
            payload={},
            completed_call={"call_id": "call_1"},
            is_proxy_local=True,
        )
        self.assertTrue(result)

    def test_returns_false_when_event_is_not_terminal(self):
        result = _should_suppress_proxy_local_terminal(
            event_type="response.output_text.delta",
            payload={},
            completed_call={"call_id": "call_1"},
            is_proxy_local=True,
        )
        self.assertFalse(result)

    def test_returns_false_when_completed_call_is_none(self):
        result = _should_suppress_proxy_local_terminal(
            event_type="response.completed",
            payload={},
            completed_call=None,
            is_proxy_local=True,
        )
        self.assertFalse(result)

    def test_returns_false_when_not_proxy_local(self):
        result = _should_suppress_proxy_local_terminal(
            event_type="response.completed",
            payload={},
            completed_call={"call_id": "call_1"},
            is_proxy_local=False,
        )
        self.assertFalse(result)

    def test_returns_false_when_completed_call_is_empty_dict(self):
        """Preserves original truthiness semantics: falsey completed_call -> False."""
        result = _should_suppress_proxy_local_terminal(
            event_type="response.completed",
            payload={},
            completed_call={},
            is_proxy_local=True,
        )
        self.assertFalse(result)


class CoercionTelemetryStreamingTests(unittest.TestCase):
    """Tests for coercion_succeeded/coercion_failed telemetry in the streaming path."""

    def _run(self, stream_chunks, telemetry, apply_patch_output_style="native"):
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            if len(calls) == 1:
                return FakeStream(stream_chunks)
            return FakeStream(_final_message_stream())

        chunks = []
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }
        runtime.run(body, "test-model.gguf", apply_patch_output_style)
        return b"".join(chunks).decode("utf-8"), calls

    def test_malformed_web_search_emits_coercion_failed_telemetry(self):
        telemetry = TelemetryBus()
        bad_args_stream = _named_function_call_stream(
            "fc_bad", "call_bad", "web_search", "{not valid json}"
        )
        stream_text, calls = self._run(bad_args_stream, telemetry)

        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "coercion_failed" for e in telem_events), telem_events
        )
        failed = next(e for e in telem_events if e.get("type") == "coercion_failed")
        failed_payload = failed.get("payload") or {}
        self.assertEqual(failed_payload.get("tool"), "web_search")
        self.assertEqual(failed_payload.get("source"), "tool_adapter",
                         "coercion_failed must carry source=tool_adapter for operator observability")
        # No raw arguments in telemetry
        self.assertNotIn("{not valid json}", str(failed))

    def test_malformed_web_search_function_call_not_in_codex_stream(self):
        telemetry = TelemetryBus()
        bad_args_stream = _named_function_call_stream(
            "fc_bad", "call_bad", "web_search", "{not valid json}"
        )
        stream_text, calls = self._run(bad_args_stream, telemetry)

        # function_call type never appears in Codex-visible stream
        self.assertNotIn('"type": "function_call"', stream_text)
        # Error injected into next hop input
        self.assertGreater(len(calls), 1)
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input if isinstance(i, dict)
                       and i.get("type") == "function_call_output"]
        self.assertTrue(error_items, next_input)

    def test_valid_web_search_emits_coercion_succeeded_telemetry(self):
        import json as _json
        telemetry = TelemetryBus()
        good_args_stream = _named_function_call_stream(
            "fc_web", "call_web",
            "web_search", _json.dumps({"action": "search", "query": "test"})
        )
        stream_text, _ = self._run(good_args_stream, telemetry)

        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "coercion_succeeded" for e in telem_events), telem_events
        )
        succeeded = next(e for e in telem_events if e.get("type") == "coercion_succeeded")
        succeeded_payload = succeeded.get("payload") or {}
        self.assertEqual(succeeded_payload.get("tool"), "web_search")
        self.assertTrue(succeeded_payload.get("correction_applied"))
        self.assertEqual(succeeded_payload.get("source"), "tool_adapter",
                         "coercion_succeeded must carry source=tool_adapter for operator observability")

    def test_dropped_tool_emits_tool_call_error_not_coercion_failed(self):
        telemetry = TelemetryBus()
        dropped_stream = _named_function_call_stream(
            "fc_drop", "call_drop", "secret_tool", "{}"
        )

        chunks = []
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(
                dropped_stream if not chunks else _final_message_stream()
            ),
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "metadata": {"qz_dropped_tool_names": ["secret_tool"]},
        }
        runtime.run(body, "test-model.gguf")
        telem_events = telemetry.recent()

        self.assertTrue(any(e.get("type") == "tool_call_error" for e in telem_events))
        self.assertFalse(any(e.get("type") == "coercion_failed" for e in telem_events))

    def test_apply_patch_sibling_patch_coercion_emits_coercion_succeeded(self):
        import json as _json
        telemetry = TelemetryBus()
        sibling_args = _json.dumps({
            "operation": {"type": "create_file", "path": "test.py"},
            "patch": "x = 1\n",
        })
        patch_stream = _named_function_call_stream(
            "fc_patch", "call_patch", "apply_patch", sibling_args
        )

        calls = []
        chunks = []
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(patch_stream if not calls else []),
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "metadata": {"qz_tool_policy": {
                "schema": "qz.tool_policy.v1",
                "apply_patch_declared": True,
                "apply_patch_client_tool_type": "custom",
                "apply_patch_output_style": "custom",
            }},
        }
        runtime.run(body, "test-model.gguf", "custom")
        stream_text = b"".join(chunks).decode("utf-8")

        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "coercion_succeeded" for e in telem_events), telem_events
        )
        # Patch content appears only in structured custom_tool_call, not in assistant text
        self.assertIn("custom_tool_call", stream_text)
        self.assertNotIn('"type": "message"', stream_text)


class StreamingToolErrorFixtureTests(unittest.TestCase):
    """
    End-to-end streaming integration fixtures for the tool error injection paths.

    Covers:
      B. Malformed web_search coercion error (Gap 5 close)
      C. Malformed apply_patch coercion error
      D. Dropped write_stdin error injection
      E. Unknown tool error injection

    Each fixture drives a full ResponsesStreamRuntime run: hop 1 emits the
    malformed/dropped/unknown function_call; hop 2 returns a clean final message.
    Assertions verify error injection, call_id preservation, no raw-arg leaks, and
    telemetry events — without touching streaming behaviour for valid calls.
    """

    def _run(self, stream_chunks, telemetry=None, body_overrides=None):
        if telemetry is None:
            telemetry = TelemetryBus()
        calls = []

        def opener(body):
            calls.append(json.loads(json.dumps(body)))
            if len(calls) == 1:
                return FakeStream(stream_chunks)
            return FakeStream(_final_message_stream())

        chunks = []
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }
        if body_overrides:
            body.update(body_overrides)
        runtime.run(body, "test-model.gguf")
        return b"".join(chunks).decode("utf-8"), calls, telemetry

    # ----- B. Malformed web_search coercion error -----

    def test_malformed_web_search_no_lifecycle_events(self):
        """No web_search_call lifecycle event is emitted to Codex for malformed call."""
        bad_stream = _named_function_call_stream(
            "fc_bad", "call_bad_web", "web_search", "{not valid json}"
        )
        stream_text, calls, _ = self._run(bad_stream)
        self.assertNotIn("web_search_call", stream_text,
                         "no web_search_call lifecycle event should appear in Codex stream "
                         "when the web_search call was malformed")

    def test_malformed_web_search_error_call_id_preserved(self):
        """Error result in next hop has the original call_id from the malformed function_call."""
        bad_stream = _named_function_call_stream(
            "fc_bad", "call_bad_web", "web_search", "{not valid json}"
        )
        stream_text, calls, _ = self._run(bad_stream)

        self.assertGreater(len(calls), 1, "expected a second hop after error injection")
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items,
                        f"expected error function_call_output in second hop input, got: {next_input}")
        self.assertEqual(error_items[0].get("call_id"), "call_bad_web",
                         "error result call_id must match the original malformed call's call_id")

    def test_malformed_web_search_error_output_no_raw_args(self):
        """Raw malformed argument string does not appear in the model-visible error output."""
        raw_args = "{not valid json}"
        bad_stream = _named_function_call_stream("fc_bad", "call_bad_web", "web_search", raw_args)
        stream_text, calls, _ = self._run(bad_stream)

        self.assertGreater(len(calls), 1)
        for item in (calls[1].get("input") or []):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                self.assertNotIn(raw_args, item.get("output", ""),
                                 "raw malformed args must not appear in model-visible error output")

    def test_malformed_web_search_tool_call_error_telemetry(self):
        """tool_call_error telemetry fires for the coercion error path (in addition to
        coercion_failed which is already covered by CoercionTelemetryStreamingTests)."""
        telemetry = TelemetryBus()
        bad_stream = _named_function_call_stream(
            "fc_bad", "call_bad_web", "web_search", "{not valid json}"
        )
        self._run(bad_stream, telemetry=telemetry)
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "tool_call_error" for e in telem_events),
            "tool_call_error telemetry must fire alongside coercion_failed for coercion error path",
        )

    # ----- C. Malformed apply_patch coercion error -----

    _APPLY_PATCH_BODY_OVERRIDES = {
        "tools": [{"type": "apply_patch"}],
        "metadata": {
            "qz_tool_policy": {
                "schema": "qz.tool_policy.v1",
                "apply_patch_declared": True,
                "apply_patch_client_tool_type": "apply_patch",
                "apply_patch_output_style": "native",
            }
        },
    }

    def test_malformed_apply_patch_coercion_failed_streaming(self):
        """Malformed apply_patch args: coercion_failed emitted and error injected in next hop."""
        telemetry = TelemetryBus()
        raw_args = "{not valid json for patch}"
        patch_stream = _named_function_call_stream(
            "fc_patch", "call_bad_patch", "apply_patch", raw_args
        )
        stream_text, calls, _ = self._run(
            patch_stream,
            telemetry=telemetry,
            body_overrides=self._APPLY_PATCH_BODY_OVERRIDES,
        )
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "coercion_failed" for e in telem_events),
            "coercion_failed must be emitted for malformed apply_patch",
        )
        self.assertGreater(len(calls), 1, "expected a second hop after error injection")
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items,
                        "expected error function_call_output in next hop for malformed apply_patch")

    def test_malformed_apply_patch_call_id_preserved(self):
        """call_id is preserved in the error result for a malformed apply_patch call."""
        patch_stream = _named_function_call_stream(
            "fc_patch", "call_bad_patch", "apply_patch", "{}"
        )
        stream_text, calls, _ = self._run(
            patch_stream,
            body_overrides=self._APPLY_PATCH_BODY_OVERRIDES,
        )
        self.assertGreater(len(calls), 1)
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items, "expected error function_call_output for malformed apply_patch")
        self.assertEqual(error_items[0].get("call_id"), "call_bad_patch",
                         "error result call_id must match original call_id")

    def test_malformed_apply_patch_no_raw_args_in_error(self):
        """Raw malformed apply_patch argument string does not appear in model-visible error."""
        raw_args = "{not valid json for patch}"
        patch_stream = _named_function_call_stream(
            "fc_patch", "call_bad_patch", "apply_patch", raw_args
        )
        stream_text, calls, _ = self._run(
            patch_stream,
            body_overrides=self._APPLY_PATCH_BODY_OVERRIDES,
        )
        self.assertGreater(len(calls), 1)
        for item in (calls[1].get("input") or []):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                self.assertNotIn(raw_args, item.get("output", ""),
                                 "raw malformed apply_patch args must not appear in model-visible error")

    def test_malformed_apply_patch_no_apply_patch_call_in_stream(self):
        """No apply_patch_call public/lifecycle event emitted for a malformed apply_patch call."""
        raw_args = "{not valid json for patch}"
        patch_stream = _named_function_call_stream(
            "fc_patch", "call_bad_patch", "apply_patch", raw_args
        )
        stream_text, calls, _ = self._run(
            patch_stream,
            body_overrides=self._APPLY_PATCH_BODY_OVERRIDES,
        )
        self.assertNotIn("apply_patch_call", stream_text,
                         "no apply_patch_call event should appear in Codex stream for malformed call")

    # ----- D. Dropped write_stdin error injection -----

    _DROPPED_STDIN_BODY_OVERRIDES = {
        "metadata": {"qz_dropped_tool_names": ["write_stdin"]},
    }

    def test_dropped_write_stdin_error_injected_streaming(self):
        """write_stdin in dropped_tool_names: error result injected into next hop input."""
        telemetry = TelemetryBus()
        stdin_stream = _named_function_call_stream(
            "fc_stdin", "call_dropped_stdin", "write_stdin", "{}"
        )
        stream_text, calls, _ = self._run(
            stdin_stream,
            telemetry=telemetry,
            body_overrides=self._DROPPED_STDIN_BODY_OVERRIDES,
        )
        self.assertGreater(len(calls), 1, "expected second hop after dropped tool error injection")
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items,
                        "expected error function_call_output in next hop for dropped write_stdin")

    def test_dropped_write_stdin_call_id_preserved(self):
        """call_id is preserved in the error result for dropped write_stdin."""
        stdin_stream = _named_function_call_stream(
            "fc_stdin", "call_dropped_stdin", "write_stdin", "{}"
        )
        stream_text, calls, _ = self._run(
            stdin_stream,
            body_overrides=self._DROPPED_STDIN_BODY_OVERRIDES,
        )
        self.assertGreater(len(calls), 1)
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items)
        self.assertEqual(error_items[0].get("call_id"), "call_dropped_stdin",
                         "error result call_id must match original call_id for dropped write_stdin")

    def test_dropped_write_stdin_error_text_mentions_not_available(self):
        """Dropped write_stdin error text includes 'not available' message."""
        stdin_stream = _named_function_call_stream(
            "fc_stdin", "call_dropped_stdin", "write_stdin", "{}"
        )
        stream_text, calls, _ = self._run(
            stdin_stream,
            body_overrides=self._DROPPED_STDIN_BODY_OVERRIDES,
        )
        self.assertGreater(len(calls), 1)
        next_input = calls[1].get("input") or []
        for item in next_input:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                self.assertIn("not available", item.get("output", ""),
                              "dropped tool error must say 'not available' in its output")
                return
        self.fail("no function_call_output error item found in second hop input")

    def test_dropped_write_stdin_no_native_passthrough(self):
        """Dropped write_stdin does not produce a native-passthrough event in Codex stream."""
        stdin_stream = _named_function_call_stream(
            "fc_stdin", "call_dropped_stdin", "write_stdin", "{}"
        )
        stream_text, calls, _ = self._run(
            stdin_stream,
            body_overrides=self._DROPPED_STDIN_BODY_OVERRIDES,
        )
        # The function_call events are suppressed by the streaming path; the error
        # is injected only into the next hop's upstream input, not into Codex output.
        self.assertNotIn('"write_stdin"', stream_text,
                         "dropped write_stdin must not produce a public event in Codex stream")

    def test_dropped_write_stdin_tool_call_error_telemetry(self):
        """tool_call_error telemetry is emitted for dropped write_stdin."""
        telemetry = TelemetryBus()
        stdin_stream = _named_function_call_stream(
            "fc_stdin", "call_dropped_stdin", "write_stdin", "{}"
        )
        self._run(
            stdin_stream,
            telemetry=telemetry,
            body_overrides=self._DROPPED_STDIN_BODY_OVERRIDES,
        )
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "tool_call_error" for e in telem_events),
            "tool_call_error telemetry must fire for dropped write_stdin",
        )

    # ----- E. Unknown tool error injection -----

    def test_unknown_tool_error_injected_streaming(self):
        """Completely unknown tool: error injected in next hop with 'not recognised' text."""
        telemetry = TelemetryBus()
        unknown_stream = _named_function_call_stream(
            "fc_unk", "call_unknown_xyz", "totally_unknown_tool_xyz", "{}"
        )
        stream_text, calls, _ = self._run(unknown_stream, telemetry=telemetry)
        self.assertGreater(len(calls), 1, "expected second hop after unknown tool error injection")
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items, "expected error function_call_output for unknown tool")
        self.assertIn("not recognised", error_items[0].get("output", ""),
                      "unknown tool error must say 'not recognised' in its output")

    def test_unknown_tool_call_id_preserved(self):
        """call_id is preserved in the error result for an unknown tool."""
        unknown_stream = _named_function_call_stream(
            "fc_unk", "call_unknown_xyz", "totally_unknown_tool_xyz", "{}"
        )
        stream_text, calls, _ = self._run(unknown_stream)
        self.assertGreater(len(calls), 1)
        next_input = calls[1].get("input") or []
        error_items = [i for i in next_input
                       if isinstance(i, dict) and i.get("type") == "function_call_output"]
        self.assertTrue(error_items)
        self.assertEqual(error_items[0].get("call_id"), "call_unknown_xyz",
                         "error result call_id must match original call_id for unknown tool")

    def test_unknown_tool_no_raw_args_in_error(self):
        """Raw arguments string does not appear in model-visible error for unknown tool."""
        raw_args = '{"secret_data": "should_not_leak"}'
        unknown_stream = _named_function_call_stream(
            "fc_unk", "call_unknown_xyz", "totally_unknown_tool_xyz", raw_args
        )
        stream_text, calls, _ = self._run(unknown_stream)
        self.assertGreater(len(calls), 1)
        for item in (calls[1].get("input") or []):
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                self.assertNotIn(raw_args, item.get("output", ""),
                                 "raw args must not appear in model-visible error for unknown tool")

    def test_unknown_tool_call_error_telemetry(self):
        """tool_call_error telemetry is emitted for an unknown tool."""
        telemetry = TelemetryBus()
        unknown_stream = _named_function_call_stream(
            "fc_unk", "call_unknown_xyz", "totally_unknown_tool_xyz", "{}"
        )
        self._run(unknown_stream, telemetry=telemetry)
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "tool_call_error" for e in telem_events),
            "tool_call_error telemetry must fire for unknown tool",
        )


class NonStreamingDroppedToolGapTests(unittest.TestCase):
    """Tests that the non-streaming path applies dropped/unknown tool errors."""

    def _make_runtime(self):
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry
        web_runtime = FakeWebRuntime()
        return make_proxy_local_tool_registry(web_runtime), web_runtime

    def test_dropped_non_proxy_local_tool_error_injected_next_input(self):
        """Dropped apply_patch call (non-proxy-local) in a web_search response hop
        should produce error result in next_input, not pass through unchanged."""
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry, make_proxy_local_tool_registry
        from proxy.qz_tool_lifecycle import CompletedToolCallDecision

        registry, _ = self._make_runtime()
        dropped = frozenset({"apply_patch"})

        call = {
            "type": "function_call", "name": "apply_patch",
            "call_id": "call_ap", "arguments": "{}",
        }
        decision = registry.completed_call_decision(call, "native", dropped_tool_names=dropped)

        self.assertEqual(decision.kind, "error")
        self.assertIsNotNone(decision.error_result)
        self.assertIn("apply_patch", decision.error_result["output"])
        self.assertIn("not available", decision.error_result["output"])
        self.assertFalse(decision.coercion_applied)

    def test_unknown_non_proxy_local_tool_error_injected(self):
        registry, _ = self._make_runtime()
        call = {
            "type": "function_call", "name": "mystery_tool",
            "call_id": "call_myst", "arguments": "{}",
        }
        decision = registry.completed_call_decision(call, "native")

        self.assertEqual(decision.kind, "error")
        self.assertIn("mystery_tool", decision.error_result["output"])
        self.assertIn("not recognised", decision.error_result["output"])

    def test_exec_command_still_passes_through(self):
        registry, _ = self._make_runtime()
        call = {
            "type": "function_call", "name": "exec_command",
            "call_id": "call_exec", "arguments": "{}",
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "public")

    def test_tool_schema_replaced_event_has_correct_payload(self):
        """tool_schema_replaced payload is bounded and does not include raw schemas."""
        from proxy.qz_tool_request import normalize_tool_request_for_llamacpp
        body = {
            "tools": [
                {"type": "function", "name": "web_search",
                 "description": "stale schema", "parameters": {"type": "object"}},
            ]
        }
        report = normalize_tool_request_for_llamacpp(body, write_captures=False)
        self.assertEqual(report.replaced, ("web_search",))
        # Verify that the tool description was replaced with proxy schema
        upstream_tool = body["tools"][0]
        self.assertNotEqual(upstream_tool.get("description"), "stale schema")
        self.assertIn("capabilities", upstream_tool.get("description", ""))


class OutputTextArtifactDetectorTests(unittest.TestCase):
    """Unit tests for _looks_like_output_tool_artifact."""

    def _det(self, text):
        from proxy.qz_responses_stream import _looks_like_output_tool_artifact
        return _looks_like_output_tool_artifact(text)

    # --- True positive: patch envelopes ---
    def test_detects_begin_patch_envelope(self):
        self.assertTrue(self._det("*** Begin Patch\n*** Add File: foo.py\n+x=1\n*** End Patch"))

    def test_detects_end_patch_alone_within_first_120(self):
        self.assertTrue(self._det("Here is the change:\n*** End Patch"))

    def test_detects_diff_git_format(self):
        self.assertTrue(self._det("diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,3 @@"))

    def test_detects_two_diff_headers(self):
        self.assertTrue(self._det("--- a/foo.py\n+++ b/foo.py\n@@ ... @@"))

    # --- True positive: raw function_call JSON ---
    def test_detects_raw_function_call_json(self):
        import json
        payload = json.dumps({"type": "function_call", "name": "apply_patch", "arguments": "{}"})
        self.assertTrue(self._det(payload))

    # --- True positive: apply_patch operation JSON ---
    def test_detects_apply_patch_operation_json(self):
        import json
        payload = json.dumps({"type": "create_file", "path": "x.py", "diff": "x=1"})
        self.assertTrue(self._det(payload))

    def test_detects_apply_patch_nested_operation_json(self):
        import json
        payload = json.dumps({"operation": {"type": "update_file", "path": "x.py", "diff": "@@+x"}})
        self.assertTrue(self._det(payload))

    # --- True positive: named tool call with arguments ---
    def test_detects_named_web_search_call(self):
        import json
        payload = json.dumps({"name": "web_search", "arguments": '{"action":"search","query":"test"}'})
        self.assertTrue(self._det(payload))

    def test_detects_named_apply_patch_call(self):
        import json
        payload = json.dumps({"name": "apply_patch", "arguments": "{}"})
        self.assertTrue(self._det(payload))

    # --- True positive: web_search action object ---
    def test_detects_web_search_action_search_with_query(self):
        import json
        payload = json.dumps({"action": "search", "query": "quantzhai"})
        self.assertTrue(self._det(payload))

    def test_detects_web_search_action_retrieve_with_url(self):
        import json
        payload = json.dumps({"action": "retrieve", "url": "https://example.com"})
        self.assertTrue(self._det(payload))

    def test_detects_web_search_capabilities_action(self):
        import json
        payload = json.dumps({"action": "capabilities"})
        self.assertTrue(self._det(payload))

    # --- False positive: should NOT detect ---
    def test_does_not_detect_ordinary_json_config(self):
        self.assertFalse(self._det('{"profiles": {"broad": {"categories": ["general"]}}}'))

    def test_does_not_detect_prose_mentioning_search(self):
        self.assertFalse(self._det("You can use search to find information about topics you care about."))

    def test_does_not_detect_prose_mentioning_patch(self):
        self.assertFalse(self._det("A patch is a small code change. Here is how patches work in git."))

    def test_does_not_detect_empty(self):
        self.assertFalse(self._det(""))

    def test_does_not_detect_normal_code_block(self):
        self.assertFalse(self._det("```python\ndef hello():\n    print('hello world')\n```"))

    def test_does_not_detect_single_word_patch(self):
        self.assertFalse(self._det("patch"))

    def test_does_not_detect_json_explanation_not_starting_with_brace(self):
        self.assertFalse(self._det("To call web_search, pass {\"action\": \"search\", \"query\": \"...\"}"))

    def test_does_not_detect_search_result_summary(self):
        self.assertFalse(self._det("Found 3 results for your search. The first result is about Python."))

    def test_does_not_detect_small_json_without_tool_markers(self):
        self.assertFalse(self._det('{"name": "Alice", "age": 30}'))

    def test_does_not_detect_config_json_at_start(self):
        self.assertFalse(self._det('{"max_results": 12, "timeout": 15}'))


def _output_text_artifact_stream(artifact_text, response_id="resp_artifact_test"):
    """Build an output_text stream that contains a raw tool/patch artifact."""
    return [
        _sse_block("response.created", {
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 4102444800,
                "status": "in_progress",
                "model": "fake",
                "output": [],
            },
        }),
        _sse_block("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "msg_artifact",
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        }),
        _sse_block("response.output_text.delta", {
            "item_id": "msg_artifact",
            "output_index": 0,
            "content_index": 0,
            "delta": artifact_text,
        }),
        _sse_block("response.output_text.done", {
            "item_id": "msg_artifact",
            "output_index": 0,
            "content_index": 0,
            "text": artifact_text,
        }),
        _sse_block("response.completed", {
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 4102444800,
                "status": "completed",
                "model": "fake",
                "output": [{
                    "id": "msg_artifact",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": artifact_text}],
                }],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }),
        b"data: [DONE]\n\n",
    ]


class OutputTextArtifactStreamingTests(unittest.TestCase):
    """Integration tests for output_text artifact abort in ResponsesStreamRuntime."""

    def _run(self, stream_chunks, telemetry=None):
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(stream_chunks),
            capture_enabled=False,
            telemetry=telemetry,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
        }
        runtime.run(body, "test-model.gguf")
        return _parse_sse_events(b"".join(chunks).decode("utf-8"))

    def test_patch_envelope_aborts_before_reaching_codex(self):
        """*** Begin Patch in output_text aborts the stream — artifact not in final answer."""
        telemetry = TelemetryBus()
        artifact = "*** Begin Patch\n*** Add File: foo.py\n+x=1\n*** End Patch\n"
        events = self._run(_output_text_artifact_stream(artifact), telemetry=telemetry)

        # output_text_artifact_aborted telemetry emitted
        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "output_text_artifact_aborted" for e in telem_events),
            [e.get("type") for e in telem_events],
        )
        # Artifact text must not appear in any Codex-visible event
        events_str = str(events)
        self.assertNotIn("*** Begin Patch", events_str)
        # Fallback message appears
        self.assertTrue(any(
            isinstance(p, dict) and "malformed tool payload" in str(p)
            for _, p in events
        ), events)
        # Stream ends properly
        event_types = [et for et, _ in events]
        self.assertIn("response.completed", event_types)
        self.assertIn(None, event_types)  # [DONE]

    def test_raw_function_call_json_aborts(self):
        """Raw function_call JSON in output_text is stopped."""
        import json as _json
        telemetry = TelemetryBus()
        artifact = _json.dumps({"type": "function_call", "name": "apply_patch", "arguments": "{}"})
        events = self._run(_output_text_artifact_stream(artifact), telemetry=telemetry)

        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "output_text_artifact_aborted" for e in telem_events)
        )
        events_str = str(events)
        self.assertNotIn('"function_call"', events_str.replace('"response.completed"', '').replace('"output_text"', ''))

    def test_raw_web_search_json_aborts(self):
        """Raw web_search action JSON in output_text is stopped."""
        import json as _json
        telemetry = TelemetryBus()
        artifact = _json.dumps({"action": "search", "query": "test query", "profile": "broad"})
        events = self._run(_output_text_artifact_stream(artifact), telemetry=telemetry)

        telem_events = telemetry.recent()
        self.assertTrue(
            any(e.get("type") == "output_text_artifact_aborted" for e in telem_events)
        )

    def test_telemetry_does_not_include_raw_artifact_text(self):
        """output_text_artifact_aborted payload must not include the raw artifact."""
        telemetry = TelemetryBus()
        artifact = "*** Begin Patch\n*** Add File: secret.py\n+password=hunter2\n*** End Patch"
        self._run(_output_text_artifact_stream(artifact), telemetry=telemetry)

        telem_events = telemetry.recent()
        aborted = next(
            (e for e in telem_events if e.get("type") == "output_text_artifact_aborted"),
            None,
        )
        self.assertIsNotNone(aborted)
        self.assertNotIn("secret.py", str(aborted))
        self.assertNotIn("hunter2", str(aborted))
        self.assertNotIn("*** Begin Patch", str(aborted))

    def test_fallback_uses_canonical_response_id(self):
        """Fallback response.completed uses the upstream response.id from response.created."""
        import json as _json
        artifact = "*** Begin Patch\n*** Add File: foo.py\n+x=1\n*** End Patch"
        events = self._run(_output_text_artifact_stream(artifact, response_id="resp_canonical_123"))

        completed = next(
            (p for et, p in events if et == "response.completed" and isinstance(p, dict)),
            None,
        )
        self.assertIsNotNone(completed)
        self.assertEqual((completed.get("response") or {}).get("id"), "resp_canonical_123")

    def test_normal_output_text_streams_unchanged(self):
        """Normal assistant output_text is not affected by the artifact detector."""
        events = self._run(_fixture_chunks("basic_message.raw"))

        # response.completed and [DONE] present
        event_types = [et for et, _ in events]
        self.assertIn("response.completed", event_types)
        # output_text_artifact_aborted NOT emitted
        self.assertNotIn("output_text_artifact_aborted", str(events))

    def test_reasoning_artifact_abort_still_works(self):
        """Existing reasoning artifact abort is not affected."""
        telemetry = TelemetryBus()
        from proxy.qz_responses_stream import ResponsesStreamRuntime
        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="summary",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=lambda body: FakeStream(_stuck_reasoning_only_stream(delta_count=4)),
            capture_enabled=False,
            telemetry=telemetry,
            reasoning_only_char_limit=12,
        )
        runtime.run({"model": "test-model.gguf", "input": [{"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "test"}]}]}, "test-model.gguf")

        telem_events = telemetry.recent()
        self.assertTrue(any(e.get("type") == "reasoning_only_aborted" for e in telem_events))

    def test_web_search_continuation_unchanged(self):
        """web_search tool continuation still works after the artifact detector."""
        requests = []
        web_runtime = FakeWebRuntime()

        def opener(body):
            requests.append(body)
            has_output = any(
                isinstance(i, dict) and i.get("type") == "function_call_output"
                for i in body.get("input") or []
            )
            return FakeStream(_final_message_stream() if has_output else _web_call_stream())

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=web_runtime,
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }
        runtime.run(body, "test-model.gguf")
        stream_text = b"".join(chunks).decode("utf-8")

        self.assertEqual(len(requests), 2)
        self.assertIn("web_search_call", stream_text)
        self.assertNotIn("output_text_artifact_aborted", stream_text)


class ToolSchemaTelemetryStreamingTests(unittest.TestCase):
    """Tests for tool_schema_replaced telemetry emitted by the streaming runtime."""

    def _run(self, body, telemetry):
        """Run the streaming runtime with a single-hop final-message stream."""
        def opener(_b):
            return FakeStream(_final_message_stream())

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
        )
        runtime.run(body, "test-model.gguf")
        return b"".join(chunks).decode("utf-8")

    def _schema_events(self, telemetry):
        return [e for e in telemetry.recent() if e.get("type") == "tool_schema_replaced"]

    def test_stale_function_typed_web_search_emits_schema_telemetry(self):
        """Codex sends type=function name=web_search (stale) — proxy replaces schema."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{
                "type": "function",
                "name": "web_search",
                "description": "Stale Codex default web_search schema.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            }],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        self.assertEqual(len(events), 1, events)
        payload = events[0].get("payload") or {}
        self.assertIn("web_search", payload.get("replaced", []),
                      "stale function-typed web_search must appear in replaced")
        self.assertEqual(payload.get("source"), "tool_schema_normalizer")
        self.assertEqual(payload.get("translated"), [])

    def test_schema_event_does_not_contain_full_schema(self):
        """Telemetry payload must not leak the full tool schema body."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{
                "type": "function",
                "name": "web_search",
                "description": "Stale Codex default web_search schema.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            }],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        payload_str = str(events[0].get("payload") or {})
        self.assertNotIn("Stale Codex default", payload_str)
        self.assertNotIn("parameters", payload_str)

    def test_structured_web_search_emits_schema_telemetry_as_translated(self):
        """Normal type=web_search is translated; operator telemetry reflects this."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        self.assertEqual(len(events), 1, events)
        payload = events[0].get("payload") or {}
        self.assertIn("web_search", payload.get("translated", []))
        self.assertEqual(payload.get("replaced"), [])
        self.assertEqual(payload.get("source"), "tool_schema_normalizer")

    def test_duplicate_web_search_tools_emit_deduped_in_schema_telemetry(self):
        """Two web_search tools → one is deduped; telemetry reflects this."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "web_search", "description": "Stale."},
            ],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        self.assertEqual(len(events), 1, events)
        payload = events[0].get("payload") or {}
        self.assertIn("web_search", payload.get("deduped", []),
                      "second web_search must appear in deduped")

    def test_unchanged_tools_produce_no_schema_telemetry(self):
        """A custom function tool that passes through unchanged fires no schema event."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "function", "name": "my_custom_tool", "description": "Custom."}],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        self.assertEqual(len(events), 0,
                         "no schema telemetry expected when tools pass through unchanged")

    def test_schema_event_emitted_only_on_first_hop(self):
        """Schema telemetry is emitted at most once per run, on the first hop."""
        telemetry = TelemetryBus()
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "web_search"}],
        }

        self._run(body, telemetry)

        events = self._schema_events(telemetry)
        self.assertEqual(len(events), 1,
                         "schema telemetry must fire exactly once per streaming run")


class ApplyPatchLifecycleContractTests(unittest.TestCase):
    """Slice AP1: Lock the Codex-visible lifecycle contract for apply_patch.

    apply_patch uses execution="protocol_adapter" and has no ToolLifecycleSpec
    lifecycle_event_prefix. This means:
    - Only response.output_item.added and response.output_item.done are emitted.
    - No response.apply_patch_call.in_progress / .searching / .completed.
    - No response.web_search_call.* events.
    - No response.function_call_arguments.delta / .done forwarded to Codex.

    These tests lock that contract. Any future accidental emission of sub-lifecycle
    events for apply_patch would be caught here before it reaches Codex.

    Source: docs/apply-patch-codex-lifecycle-audit.md §5.1, §8 (G-AP1).
    """

    def _run_apply_patch(self, apply_patch_output_style="native"):
        """Run the streaming runtime against _apply_patch_call_stream."""
        def opener(_b):
            return FakeStream(_apply_patch_call_stream())

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "apply_patch"}],
        }
        runtime.run(body, "test-model.gguf", apply_patch_output_style)
        stream_text = b"".join(chunks).decode("utf-8")
        return _parse_sse_events(stream_text), stream_text

    def test_ap1_native_emits_output_item_added(self):
        """Valid native apply_patch must emit response.output_item.added."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertIn("response.output_item.added", names,
                      "apply_patch must emit response.output_item.added to Codex")

    def test_ap1_native_emits_output_item_done(self):
        """Valid native apply_patch must emit response.output_item.done."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertIn("response.output_item.done", names,
                      "apply_patch must emit response.output_item.done to Codex")

    def test_ap1_native_item_type_is_apply_patch_call(self):
        """The output_item.added/done item type must be 'apply_patch_call' in native mode."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        added_items = [
            p.get("item", {})
            for et, p in events
            if et == "response.output_item.added" and isinstance(p, dict)
        ]
        self.assertTrue(
            any(item.get("type") == "apply_patch_call" for item in added_items),
            "output_item.added must carry an apply_patch_call item in native mode",
        )

    def test_ap1_no_sub_lifecycle_in_progress(self):
        """apply_patch must NOT emit response.apply_patch_call.in_progress."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertNotIn(
            "response.apply_patch_call.in_progress", names,
            "apply_patch must not emit response.apply_patch_call.in_progress — "
            "no official event family exists; do not invent it without Codex client proof",
        )

    def test_ap1_no_sub_lifecycle_searching(self):
        """apply_patch must NOT emit response.apply_patch_call.searching."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertNotIn("response.apply_patch_call.searching", names,
                         "apply_patch must not emit response.apply_patch_call.searching")

    def test_ap1_no_sub_lifecycle_completed(self):
        """apply_patch must NOT emit response.apply_patch_call.completed."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertNotIn("response.apply_patch_call.completed", names,
                         "apply_patch must not emit response.apply_patch_call.completed")

    def test_ap1_no_web_search_call_events(self):
        """apply_patch must NOT emit any response.web_search_call.* events."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        web_search_events = [et for et, _ in events if (et or "").startswith("response.web_search_call.")]
        self.assertEqual(web_search_events, [],
                         "apply_patch must not emit any response.web_search_call.* events")

    def test_ap1_no_function_call_arguments_delta(self):
        """apply_patch must NOT forward response.function_call_arguments.delta to Codex."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertNotIn("response.function_call_arguments.delta", names,
                         "function_call_arguments.delta must be suppressed; raw args could leak patch body")

    def test_ap1_no_function_call_arguments_done(self):
        """apply_patch must NOT forward response.function_call_arguments.done to Codex."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertNotIn("response.function_call_arguments.done", names,
                         "function_call_arguments.done must be suppressed")

    def test_ap1_no_raw_function_call_item_type(self):
        """Codex must not see output_item.* with type=function_call for apply_patch."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        raw_calls = [
            p.get("item", {})
            for et, p in events
            if et in {"response.output_item.added", "response.output_item.done"} and isinstance(p, dict)
            and p.get("item", {}).get("type") == "function_call"
            and p.get("item", {}).get("name") == "apply_patch"
        ]
        self.assertEqual(raw_calls, [],
                         "apply_patch must not appear as a raw function_call item to Codex")

    def test_ap1_custom_mode_item_type_is_custom_tool_call(self):
        """In custom mode, output_item.added must carry a custom_tool_call item for apply_patch."""
        events, stream_text = self._run_apply_patch(apply_patch_output_style="custom")
        added_items = [
            p.get("item", {})
            for et, p in events
            if et == "response.output_item.added" and isinstance(p, dict)
        ]
        self.assertTrue(
            any(
                item.get("type") == "custom_tool_call" and item.get("name") == "apply_patch"
                for item in added_items
            ),
            "custom mode apply_patch must emit a custom_tool_call item (not apply_patch_call)",
        )
        # Also confirm no apply_patch_call appears in custom mode
        self.assertNotIn('"type": "apply_patch_call"', stream_text,
                         "custom mode must not emit apply_patch_call items")

    def test_ap1_custom_mode_no_sub_lifecycle_events(self):
        """Custom mode apply_patch must also not emit any sub-lifecycle events."""
        events, _ = self._run_apply_patch(apply_patch_output_style="custom")
        names = [et for et, _ in events]
        for forbidden in (
            "response.apply_patch_call.in_progress",
            "response.apply_patch_call.searching",
            "response.apply_patch_call.completed",
            "response.web_search_call.in_progress",
            "response.web_search_call.searching",
            "response.web_search_call.completed",
        ):
            self.assertNotIn(forbidden, names,
                             f"custom mode apply_patch must not emit {forbidden}")

    def test_ap1_response_completed_always_emitted(self):
        """response.completed must always be the terminal event after apply_patch."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        names = [et for et, _ in events]
        self.assertIn("response.completed", names,
                      "response.completed must be emitted after apply_patch")

    def test_ap1_native_item_status_progression(self):
        """output_item.added must carry status=in_progress; output_item.done must carry status=completed."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        patch_items_by_event = [
            (et, payload["item"])
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items_by_event), 2,
                         "must have one output_item.added and one output_item.done for apply_patch_call")
        added_et, added_item = patch_items_by_event[0]
        done_et, done_item = patch_items_by_event[1]
        self.assertEqual(added_et, "response.output_item.added")
        self.assertEqual(done_et, "response.output_item.done")
        self.assertEqual(added_item["status"], "in_progress",
                         "output_item.added must carry status=in_progress")
        self.assertEqual(done_item["status"], "completed",
                         "output_item.done must carry status=completed")

    def test_ap1_native_call_id_preserved(self):
        """call_id from the upstream function_call must be preserved in the apply_patch_call item."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        patch_items = [
            payload["item"]
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items), 2)
        # _apply_patch_call_stream uses call_id="call_patch"
        for item in patch_items:
            self.assertEqual(item.get("call_id"), "call_patch",
                             "call_id from upstream function_call must be preserved in apply_patch_call")

    def test_ap1_native_operation_field_present(self):
        """apply_patch_call items must carry an operation field with at least type and path."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        patch_items = [
            payload["item"]
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items), 2)
        for item in patch_items:
            operation = item.get("operation")
            self.assertIsInstance(operation, dict,
                                  "apply_patch_call item must carry an operation dict")
            self.assertIn("type", operation, "operation must have a 'type' field")
            self.assertIn("path", operation, "operation must have a 'path' field")

    def test_ap1_native_diff_no_unified_diff_headers(self):
        """Unified diff file headers (--- a/..., +++ b/...) must not appear in operation.diff."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        patch_items = [
            payload["item"]
            for et, payload in events
            if et == "response.output_item.added"
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items), 1)
        diff = patch_items[0].get("operation", {}).get("diff", "")
        self.assertNotIn("--- a/", diff,
                         "unified diff '--- a/' header must be stripped from operation.diff")
        self.assertNotIn("+++ b/", diff,
                         "unified diff '+++ b/' header must be stripped from operation.diff")
        self.assertNotIn("diff --git", diff,
                         "unified diff 'diff --git' header must be stripped from operation.diff")

    def test_ap1_no_file_search_call_events(self):
        """apply_patch must NOT emit any response.file_search_call.* events."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        file_search_events = [et for et, _ in events if (et or "").startswith("response.file_search_call.")]
        self.assertEqual(file_search_events, [],
                         "apply_patch must not emit any response.file_search_call.* events")

    def test_ap1_no_code_interpreter_call_events(self):
        """apply_patch must NOT emit any response.code_interpreter_call.* events."""
        events, _ = self._run_apply_patch(apply_patch_output_style="native")
        ci_events = [et for et, _ in events if (et or "").startswith("response.code_interpreter_call.")]
        self.assertEqual(ci_events, [],
                         "apply_patch must not emit any response.code_interpreter_call.* events")

    def test_ap1_custom_mode_begin_patch_in_input(self):
        """Custom mode apply_patch item must carry a *** Begin Patch envelope in its input field."""
        events, _ = self._run_apply_patch(apply_patch_output_style="custom")
        custom_items = [
            payload["item"]
            for et, payload in events
            if et == "response.output_item.added"
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
            and payload["item"].get("name") == "apply_patch"
        ]
        self.assertEqual(len(custom_items), 1)
        self.assertIn("*** Begin Patch", custom_items[0].get("input", ""),
                      "custom_tool_call apply_patch input must contain a *** Begin Patch envelope")

    def test_ap1_custom_mode_call_id_preserved(self):
        """call_id must be preserved in custom_tool_call apply_patch items."""
        events, _ = self._run_apply_patch(apply_patch_output_style="custom")
        custom_items = [
            payload["item"]
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "custom_tool_call"
            and payload["item"].get("name") == "apply_patch"
        ]
        self.assertEqual(len(custom_items), 2)
        for item in custom_items:
            self.assertEqual(item.get("call_id"), "call_patch",
                             "call_id must be preserved in custom_tool_call apply_patch items")


class ApplyPatchStreamingAP2Tests(unittest.TestCase):
    """Slice AP2 — Streaming runtime coercion/advice contract (integration level).

    Verifies that the streaming runtime, when it encounters a malformed apply_patch
    function_call:
    - Injects a function_call_output error into the model's next-hop input.
    - Emits coercion_failed / tool_call_error telemetry with source=tool_adapter.
    - Does NOT echo raw malformed argument content in model-visible output or telemetry.
    - Preserves call_id in the injected error item.
    - On success (sibling-patch promotion, legacy envelope): emits coercion_succeeded
      and emits the apply_patch_call item without error injection.

    Source: docs/apply-patch-codex-lifecycle-audit.md §5.3, §6.2, §7.2, §8 G-AP2.
    """

    def _make_apply_patch_stream(self, arguments: str, call_id: str = "call_ap2",
                                  item_id: str = "fc_ap2") -> list:
        """Return a FakeStream-compatible list for an apply_patch function_call."""
        return [
            _sse_block("response.created", {
                "response": {
                    "id": "resp_fake_ap2",
                    "object": "response",
                    "created_at": 4102444800,
                    "status": "in_progress",
                    "model": "fake",
                    "output": [],
                },
            }),
            _sse_block("response.output_item.added", {
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "status": "in_progress",
                    "call_id": call_id,
                    "name": "apply_patch",
                    "arguments": "",
                },
            }),
            _sse_block("response.function_call_arguments.delta", {
                "item_id": item_id,
                "output_index": 0,
                "delta": arguments,
            }),
            _sse_block("response.output_item.done", {
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": "apply_patch",
                },
            }),
            b"data: [DONE]\n\n",
        ]

    def _run_error_scenario(self, arguments: str, call_id: str = "call_ap2"):
        """Run the streaming runtime with a malformed apply_patch call.

        Returns (requests, stream_text, telemetry_events).
        The second hop is answered with a final message stream so the runtime completes.
        """
        requests = []
        telemetry = TelemetryBus()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            has_error_output = any(
                isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == call_id
                for item in body.get("input") or []
            )
            return FakeStream(
                _final_message_stream(text="model recovered.")
                if has_error_output
                else self._make_apply_patch_stream(arguments, call_id)
            )

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="req-ap2-error",
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "apply_patch"}],
        }
        runtime.run(body, "test-model.gguf", "native")
        stream_text = b"".join(chunks).decode("utf-8")
        return requests, stream_text, telemetry.recent()

    def _run_success_scenario(self, arguments: str, call_id: str = "call_ap2_ok",
                               apply_patch_output_style: str = "native"):
        """Run the streaming runtime with a well-formed apply_patch call (success path)."""
        requests = []
        telemetry = TelemetryBus()

        def opener(body):
            requests.append(json.loads(json.dumps(body)))
            return FakeStream(self._make_apply_patch_stream(arguments, call_id))

        chunks = []
        runtime = ResponsesStreamRuntime(
            upstream="http://127.0.0.1:1",
            authorization="Bearer local",
            reasoning_stream_format="raw",
            web_runtime=FakeWebRuntime(),
            chunk_writer=chunks.append,
            stream_opener=opener,
            capture_enabled=False,
            telemetry=telemetry,
            request_id="req-ap2-ok",
        )
        body = {
            "model": "test-model.gguf",
            "input": [{"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "test"}]}],
            "tools": [{"type": "apply_patch"}],
        }
        runtime.run(body, "test-model.gguf", apply_patch_output_style)
        stream_text = b"".join(chunks).decode("utf-8")
        return requests, stream_text, telemetry.recent()

    # ── Tests 4-7: Error path (coercion fails → error injected into next hop) ──

    def test_ap2_invalid_json_injects_function_call_output_error(self):
        """Invalid JSON apply_patch args: second hop receives function_call_output error."""
        call_id = "call_bad_json"
        raw_args = "{not valid json"
        requests, _, telemetry_events = self._run_error_scenario(raw_args, call_id)

        # Two hops: error injection on first, model recovery on second
        self.assertEqual(len(requests), 2,
                         "invalid JSON apply_patch must trigger a second hop with error injection")

        # Second hop input has a function_call_output error for the correct call_id
        second_input = requests[1].get("input") or []
        error_items = [
            item for item in second_input
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ]
        self.assertEqual(len(error_items), 1,
                         "exactly one function_call_output error must be injected for invalid JSON")

        error_output = error_items[0]["output"]
        # Error text names the failure specifically
        self.assertIn("apply_patch", error_output)
        self.assertIn("not valid JSON", error_output)
        # call_id preserved
        self.assertEqual(error_items[0]["call_id"], call_id)
        # Raw malformed args NOT echoed in error output
        self.assertNotIn("{not valid json", error_output,
                         "raw malformed JSON args must not appear in the injected error output")

        # Telemetry: coercion_failed with source=tool_adapter
        coercion_failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(coercion_failed) >= 1,
                         "coercion_failed telemetry must be emitted for invalid JSON apply_patch")
        payload = coercion_failed[0].get("payload") or {}
        self.assertEqual(payload.get("source"), "tool_adapter")
        self.assertEqual(payload.get("tool"), "apply_patch")
        # Telemetry error_summary must not echo raw args
        error_summary = payload.get("error_summary", "")
        self.assertNotIn("{not valid json", error_summary,
                         "telemetry error_summary must not echo raw malformed args")

    def test_ap2_invalid_json_no_output_item_for_failed_call(self):
        """When coercion fails, no output_item.added/done must be emitted for the failed call."""
        call_id = "call_bad_json_lifecycle"
        raw_args = "{not valid json"
        requests, stream_text, _ = self._run_error_scenario(raw_args, call_id)

        events = _parse_sse_events(stream_text)
        # No apply_patch_call item type should appear (the failed call never becomes public)
        apply_patch_call_items = [
            payload.get("item", {})
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(apply_patch_call_items, [],
                         "no apply_patch_call item must be emitted to Codex when coercion fails")

    def test_ap2_missing_diff_injects_specific_repair_text(self):
        """Missing diff on update_file: error names 'diff' and 'update_file'; call_id preserved; path not echoed."""
        call_id = "call_missing_diff"
        args = json.dumps({"operation": {"type": "update_file", "path": "src/main.py"}})
        requests, _, _ = self._run_error_scenario(args, call_id)

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        error_items = [
            item for item in second_input
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ]
        self.assertEqual(len(error_items), 1)
        error_output = error_items[0]["output"]

        self.assertIn("diff", error_output,
                      "error for missing diff must name 'diff'")
        self.assertIn("update_file", error_output,
                      "error for missing diff must name the operation type 'update_file'")
        self.assertEqual(error_items[0]["call_id"], call_id,
                         "call_id must be preserved in missing-diff error")
        # Raw path value must not be echoed in the error
        self.assertNotIn('"src/main.py"', error_output,
                         "raw path value from arguments must not appear in model-visible error")

    def test_ap2_missing_destination_injects_specific_repair_text(self):
        """Missing destination on move_file: error names 'destination'; call_id preserved."""
        call_id = "call_missing_dest"
        args = json.dumps({"operation": {"type": "move_file", "path": "old.py"}})
        requests, _, _ = self._run_error_scenario(args, call_id)

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        error_items = [
            item for item in second_input
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ]
        self.assertEqual(len(error_items), 1)
        error_output = error_items[0]["output"]

        self.assertIn("destination", error_output,
                      "error for missing destination must name 'destination'")
        self.assertEqual(error_items[0]["call_id"], call_id)
        self.assertNotIn('"old.py"', error_output,
                         "raw path value from arguments must not appear in model-visible error")

    def test_ap2_unknown_operation_type_injects_specific_repair_text(self):
        """Unknown operation type: error names the bad type; call_id preserved; raw args not echoed."""
        call_id = "call_unknown_op"
        args = json.dumps({"operation": {"type": "chmod_file", "path": "x.py"}})
        requests, _, telemetry_events = self._run_error_scenario(args, call_id)

        self.assertEqual(len(requests), 2)
        second_input = requests[1].get("input") or []
        error_items = [
            item for item in second_input
            if isinstance(item, dict)
            and item.get("type") == "function_call_output"
            and item.get("call_id") == call_id
        ]
        self.assertEqual(len(error_items), 1)
        error_output = error_items[0]["output"]

        self.assertIn("unknown operation type", error_output,
                      "error for unknown op type must say 'unknown operation type'")
        self.assertEqual(error_items[0]["call_id"], call_id)
        # Raw file path must not appear in error output
        self.assertNotIn('"x.py"', error_output,
                         "raw path value from arguments must not appear in model-visible error")

        # coercion_failed telemetry emitted with source=tool_adapter
        coercion_failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(coercion_failed) >= 1)
        self.assertEqual((coercion_failed[0].get("payload") or {}).get("source"), "tool_adapter")

    # ── Tests 8-9: Success path (coercion succeeds → apply_patch_call emitted) ──

    def test_ap2_sibling_patch_promotion_succeeds_no_coercion_failed(self):
        """Sibling patch promotion: coercion_succeeded emitted; apply_patch_call item present; no error hop."""
        call_id = "call_sibling_promo"
        args = json.dumps({
            "operation": {"type": "create_file", "path": "hello.txt"},
            "patch": "hello\n",
        })
        requests, stream_text, telemetry_events = self._run_success_scenario(args, call_id)

        # Single hop only (no error injection → no second hop)
        self.assertEqual(len(requests), 1,
                         "sibling patch promotion must not trigger a second hop")

        # apply_patch_call item present in stream
        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items), 2,
                         "sibling patch promotion must produce output_item.added + output_item.done")
        # operation.diff comes from the sibling patch field
        op = patch_items[0].get("operation", {})
        self.assertEqual(op.get("diff"), "hello\n",
                         "operation.diff must contain the promoted sibling patch content")

        # coercion_succeeded present; coercion_failed absent
        succeeded = [e for e in telemetry_events if e.get("type") == "coercion_succeeded"]
        failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(succeeded) >= 1,
                        "coercion_succeeded telemetry must be emitted for sibling patch promotion")
        self.assertEqual(len(failed), 0,
                         "coercion_failed must NOT be emitted when sibling patch promotion succeeds")
        self.assertEqual((succeeded[0].get("payload") or {}).get("source"), "tool_adapter")

    def test_ap2_legacy_begin_patch_envelope_succeeds(self):
        """Legacy *** Begin Patch envelope without top-level path: coercion_succeeded; operation extracted."""
        call_id = "call_legacy_env"
        args = json.dumps({
            "patch": "*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch\n",
        })
        requests, stream_text, telemetry_events = self._run_success_scenario(args, call_id)

        # Single hop (success path)
        self.assertEqual(len(requests), 1,
                         "legacy envelope coercion must not trigger an error hop")

        events = _parse_sse_events(stream_text)
        patch_items = [
            payload["item"]
            for et, payload in events
            if et in {"response.output_item.added", "response.output_item.done"}
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
            and payload["item"].get("type") == "apply_patch_call"
        ]
        self.assertEqual(len(patch_items), 2,
                         "legacy envelope must produce output_item.added + output_item.done")
        op = patch_items[0].get("operation", {})
        self.assertEqual(op.get("type"), "create_file",
                         "operation.type must be create_file (extracted from *** Add File: header)")
        self.assertEqual(op.get("path"), "hello.txt",
                         "operation.path must be extracted from *** Add File: header")

        # coercion_succeeded present; no coercion_failed
        succeeded = [e for e in telemetry_events if e.get("type") == "coercion_succeeded"]
        failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(succeeded) >= 1,
                        "coercion_succeeded telemetry must be emitted for legacy envelope success")
        self.assertEqual(len(failed), 0,
                         "coercion_failed must NOT be emitted for legacy envelope success")

    # ── Section D: Telemetry safety — no raw content in coercion_failed payload ──

    def test_ap2_coercion_failed_telemetry_has_no_raw_patch_body(self):
        """Telemetry coercion_failed error_summary must not contain raw patch body content."""
        call_id = "call_telemetry_safety"
        # Provide a patch with sentinel sensitive content in the wrong shape
        sentinel = "VERY_SENSITIVE_PATCH_BODY_XYZ"
        args = json.dumps({
            "operation": {"type": "create_file", "path": "f.py"},
            "extra_non_patch_field": sentinel,
        })
        # coerce() will fail because no diff; extra_non_patch_field is not 'patch'
        _, _, telemetry_events = self._run_error_scenario(args, call_id)

        coercion_failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        # If coercion failed, error_summary must not contain the sentinel content
        for event in coercion_failed:
            error_summary = (event.get("payload") or {}).get("error_summary", "")
            self.assertNotIn(sentinel, error_summary,
                             "telemetry error_summary must not contain raw non-standard field content")

    def test_ap2_coercion_failed_telemetry_has_bounded_error_summary(self):
        """Telemetry coercion_failed error_summary must be capped at 200 characters."""
        call_id = "call_telemetry_cap"
        # Provide long malformed args that would produce a long error if echoed verbatim
        args = "{" + "a" * 500
        _, _, telemetry_events = self._run_error_scenario(args, call_id)

        coercion_failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(coercion_failed) >= 1)
        error_summary = (coercion_failed[0].get("payload") or {}).get("error_summary", "")
        self.assertLessEqual(len(error_summary), 200,
                             "telemetry error_summary must be capped at 200 characters")

    def test_ap2_coercion_failed_telemetry_has_expected_safe_fields(self):
        """coercion_failed telemetry payload must carry safe fields only (tool, call_id, source, etc.)."""
        call_id = "call_telemetry_fields"
        args = "{bad json"
        _, _, telemetry_events = self._run_error_scenario(args, call_id)

        coercion_failed = [e for e in telemetry_events if e.get("type") == "coercion_failed"]
        self.assertTrue(len(coercion_failed) >= 1)
        payload = coercion_failed[0].get("payload") or {}
        # Required safe fields
        self.assertIn("tool", payload)
        self.assertIn("call_id", payload)
        self.assertIn("source", payload)
        self.assertIn("error_summary", payload)
        self.assertEqual(payload.get("tool"), "apply_patch")
        self.assertEqual(payload.get("call_id"), call_id)
        self.assertEqual(payload.get("source"), "tool_adapter")
        # Must not carry raw arguments string
        self.assertNotIn("arguments", payload,
                         "coercion_failed payload must not include raw arguments")


if __name__ == "__main__":
    unittest.main()
