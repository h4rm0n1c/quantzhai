import json
import unittest

from proxy.qz_proxy_tools import ProxyLocalToolExecutor, ProxyLocalToolRegistry
from proxy.qz_request_router import RequestRouter
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import ToolLifecycleSpec


class ProbeProxyToolExecutor(ProxyLocalToolExecutor):
    function_name = "qz_probe"
    lifecycle = ToolLifecycleSpec(
        name="qz_probe",
        execution="proxy_local",
        public_item_type="qz_probe_call",
        telemetry_name="qz_probe",
        continuation_hops=2,
    )

    def __init__(self):
        self.calls = []

    def started_public_item(self, call, public_index):
        return {
            "id": call.get("id") or f"qz_probe_{public_index}",
            "type": "qz_probe_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }

    def execute(self, call, context):
        self.calls.append({
            "call": call,
            "request_id": context.request_id,
            "counters": context.counters,
            "seen_signatures": context.seen_signatures,
        })
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


class FakeHandler:
    upstream = "http://127.0.0.1:1"

    def __init__(self, registry):
        self.proxy_tool_registry_factory = lambda _web_runtime: registry


class FakeRouter(RequestRouter):
    def __init__(self, handler, responses):
        super().__init__(handler)
        self.responses = list(responses)
        self.request_bodies = []

    def _web_runtime(self, selected_model=None):
        return object()

    def _call_upstream_json(self, url, body):
        self.request_bodies.append(json.loads(json.dumps(body)))
        payload = self.responses.pop(0)
        return 200, "application/json", json.dumps(payload).encode("utf-8")


class RequestRouterProxyLocalToolTests(unittest.TestCase):
    def test_non_streaming_proxy_local_loop_is_not_web_search_specific(self):
        executor = ProbeProxyToolExecutor()
        registry = ProxyLocalToolRegistry([executor])
        router = FakeRouter(
            FakeHandler(registry),
            responses=[
                {
                    "id": "resp_probe_call",
                    "object": "response",
                    "output": [{
                        "id": "fc_probe",
                        "type": "function_call",
                        "status": "completed",
                        "call_id": "call_probe",
                        "name": "qz_probe",
                        "arguments": "{\"value\":1}",
                    }],
                    "usage": {},
                },
                {
                    "id": "resp_final",
                    "object": "response",
                    "output": [{
                        "id": "msg_final",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "probe done", "annotations": []}],
                    }],
                    "usage": {},
                },
            ],
        )

        status, content_type, out = router._run_responses_locally(
            {
                "model": "fake",
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "probe"}],
                }],
                "tools": [{"type": "function", "name": "qz_probe"}],
            },
            "fake",
            request_id="qz_req_probe",
        )

        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(executor.calls[0]["request_id"], "qz_req_probe")
        self.assertEqual(out["output"][0]["type"], "qz_probe_call")
        self.assertEqual(out["output"][1]["type"], "message")
        self.assertEqual(router.request_bodies[1]["input"][-1]["type"], "function_call_output")
        self.assertEqual(router.request_bodies[1]["input"][-1]["call_id"], "call_probe")


if __name__ == "__main__":
    unittest.main()
