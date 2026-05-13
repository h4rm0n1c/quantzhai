import json
import unittest
from pathlib import Path

from proxy.qz_proxy_tools import ProxyLocalToolExecutor, ProxyLocalToolRegistry
from proxy.qz_responses_stream import ClientStreamDisconnected, ResponsesStreamRuntime
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
        runtime.run(body, "test-model.gguf", apply_patch_output_style)
        return b"".join(chunks).decode("utf-8")

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


if __name__ == "__main__":
    unittest.main()
