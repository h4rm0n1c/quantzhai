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

    def test_non_streaming_renormalization_preserves_disabled_system_prompt(self):
        router = FakeRouter(
            FakeHandler(ProxyLocalToolRegistry([])),
            responses=[{
                "id": "resp_final",
                "object": "response",
                "output": [{
                    "id": "msg_final",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }],
                "usage": {},
            }],
        )

        router._run_responses_locally(
            {
                "model": "fake",
                "input": [{
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "direct"}],
                }],
                "tools": [],
            },
            "fake",
            selected_model={"overrides": {"disable_system_prompt": True}},
            request_id="qz_req_blank",
        )

        self.assertEqual(len(router.request_bodies), 1)
        self.assertNotIn("instructions", router.request_bodies[0])
        self.assertTrue(router.request_bodies[0]["metadata"]["qz_prompt_policy"]["disable_system_prompt"])


class WebSearchCapabilitiesEndpointTests(unittest.TestCase):
    def test_get_web_search_capabilities_returns_same_schema(self):
        from proxy.qz_tool_web import WebSearchRuntime, build_web_search_capabilities

        runtime = WebSearchRuntime(
            search_config_profiles={"docs_live": {"categories": ["it"], "engines": ["mdn"]}},
            budget_mode_table={"normal": {"max_results": 5}},
        )
        expected_schema = build_web_search_capabilities(runtime)["schema"]

        class Handler:
            path = "/qz/web-search/capabilities"
            sent = None

            def _send_json(self, status, payload):
                self.sent = (status, payload)

            def _handle_ollama_get(self):
                return False

            def _handle_ready_get(self):
                return False

        class Router(RequestRouter):
            def _log_request_path(self, method):
                pass

            def _web_runtime(self, selected_model=None):
                return runtime

        handler = Handler()
        Router(handler).handle_get()
        status, payload = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], expected_schema)
        self.assertIn("docs_live", payload["profiles"])

    def test_get_web_search_capabilities_failure_is_read_only_json(self):
        class Handler:
            path = "/qz/web-search/capabilities"
            sent = None

            def _send_json(self, status, payload):
                self.sent = (status, payload)

            def _handle_ollama_get(self):
                return False

            def _handle_ready_get(self):
                return False

        class Router(RequestRouter):
            def _log_request_path(self, method):
                pass

            def _web_runtime(self, selected_model=None):
                raise RuntimeError("agent api probe failed")

        handler = Handler()
        Router(handler).handle_get()
        status, payload = handler.sent
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema"], "qz.web_search.capabilities.v1")
        self.assertFalse(payload["ok"])


class SignalDecisionEmissionTests(unittest.TestCase):
    """Tests for RequestRouter._emit_signal_decisions helper."""

    def setUp(self):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        self.FeedbackChannel = FeedbackChannel
        self.FeedbackVisibility = FeedbackVisibility
        self.SignalDecision = SignalDecision

    class FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    class FakeHandler:
        def __init__(self):
            self.telemetry = SignalDecisionEmissionTests.FakeTelemetry()

    def test_emits_telemetry_for_telemetry_channel_signal(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        signal = self.SignalDecision(
            event_type="test_event",
            payload={"key": "val"},
            visibility=self.FeedbackVisibility.OPERATOR,
            channel=self.FeedbackChannel.TELEMETRY,
        )

        router._emit_signal_decisions([signal], request_id="req_123")

        self.assertEqual(len(handler.telemetry.emitted), 1)
        event_type, payload = handler.telemetry.emitted[0]
        self.assertEqual(event_type, "test_event")
        self.assertEqual(payload["key"], "val")
        self.assertEqual(payload["request_id"], "req_123")

    def test_ignores_non_telemetry_channel_signals(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        signal = self.SignalDecision(
            event_type="ignored_event",
            payload={},
            visibility=self.FeedbackVisibility.MODEL,
            channel=self.FeedbackChannel.FUNCTION_CALL_OUTPUT,
        )

        router._emit_signal_decisions([signal])

        self.assertEqual(len(handler.telemetry.emitted), 0)

    def test_handles_empty_signal_list(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        router._emit_signal_decisions([])
        self.assertEqual(len(handler.telemetry.emitted), 0)

    def test_robust_to_malformed_signals(self):
        handler = self.FakeHandler()
        router = RequestRouter(handler)
        # Should not crash on None or non-SignalDecision items
        router._emit_signal_decisions([None, "not-a-signal", 42])
        self.assertEqual(len(handler.telemetry.emitted), 0)


if __name__ == "__main__":
    unittest.main()
