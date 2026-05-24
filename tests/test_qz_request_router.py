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


class SandboxAdvisoryHelperTests(unittest.TestCase):
    """Unit tests for RequestRouter._model_visible_native_advisories()."""

    class _FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    def _make_router(self):
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        handler.telemetry = self._FakeTelemetry()
        return RequestRouter(handler)

    def _make_sandbox_signal(self, call_id="call_ro", confidence="high"):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        return SignalDecision(
            event_type="tool_sandbox_denied",
            payload={
                "call_id": call_id,
                "tool": "exec_command",
                "classifier": "sandbox_denied_readonly_fs",
                "matched_string": "Read-only file system",
                "exit_code": 1,
                "output_preview": "...Read-only file system...",
                "confidence": confidence,
            },
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence=confidence,
        )

    def _make_connection_refused_signal(self, call_id="call_conn"):
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        return SignalDecision(
            event_type="tool_connection_failed",
            payload={
                "call_id": call_id,
                "tool": "exec_command",
                "classifier": "native_tool_connection_refused",
                "matched_string": "Connection refused",
                "exit_code": 1,
                "output_preview": "...Connection refused...",
                "confidence": "medium",
            },
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence="medium",
        )

    def test_sandbox_denied_high_confidence_produces_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertEqual(len(advisories), 1)

    def test_advisory_is_function_call_output(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertEqual(advisories[0]["type"], "function_call_output")

    def test_advisory_uses_original_call_id(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal("call_ro_original")])
        self.assertEqual(advisories[0]["call_id"], "call_ro_original")

    def test_advisory_text_mentions_readonly_filesystem(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertIn("read-only filesystem", advisories[0]["output"].lower())

    def test_advisory_text_mentions_not_retrying(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        self.assertIn("retry", advisories[0]["output"].lower())

    def test_advisory_is_plain_text_not_json_error(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal()])
        output = advisories[0]["output"]
        self.assertIsInstance(output, str)
        # Must not be a JSON {"ok": false, ...} error payload
        self.assertNotIn('"ok"', output)
        try:
            parsed = json.loads(output)
            # If it somehow parses as JSON, it must not have "ok"
            self.assertNotIn("ok", parsed)
        except (ValueError, KeyError):
            pass  # plain text is the expected form

    def test_connection_refused_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_connection_refused_signal()])
        self.assertEqual(advisories, [])

    def test_non_high_confidence_sandbox_signal_produces_no_advisory(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([self._make_sandbox_signal(confidence="medium")])
        self.assertEqual(advisories, [])

    def test_duplicate_call_id_produces_one_advisory(self):
        router = self._make_router()
        signals = [self._make_sandbox_signal("call_same"), self._make_sandbox_signal("call_same")]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 1)

    def test_empty_signals_produces_no_advisory(self):
        router = self._make_router()
        self.assertEqual(router._model_visible_native_advisories([]), [])

    def test_malformed_signals_do_not_crash(self):
        router = self._make_router()
        advisories = router._model_visible_native_advisories([None, "bad", 42, {}])
        self.assertEqual(advisories, [])

    def test_multiple_different_call_ids_produce_multiple_advisories(self):
        router = self._make_router()
        signals = [self._make_sandbox_signal("call_a"), self._make_sandbox_signal("call_b")]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 2)
        call_ids = {a["call_id"] for a in advisories}
        self.assertIn("call_a", call_ids)
        self.assertIn("call_b", call_ids)

    def test_connection_refused_mixed_with_sandbox_denied(self):
        """Only sandbox_denied triggers advisory; connection_refused does not."""
        router = self._make_router()
        signals = [
            self._make_sandbox_signal("call_ro"),
            self._make_connection_refused_signal("call_conn"),
        ]
        advisories = router._model_visible_native_advisories(signals)
        self.assertEqual(len(advisories), 1)
        self.assertEqual(advisories[0]["call_id"], "call_ro")

    def test_telemetry_is_not_suppressed_by_advisory(self):
        """_emit_signal_decisions still emits tool_sandbox_denied even when advisory is added."""
        from proxy.qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
        handler = FakeHandler.__new__(FakeHandler)
        handler.telemetry = self._FakeTelemetry()
        handler.proxy_tool_registry_factory = None
        router = RequestRouter(handler)

        signal = self._make_sandbox_signal("call_tel")
        router._emit_signal_decisions([signal], request_id="req_abc")
        router._model_visible_native_advisories([signal])

        # Telemetry emitted once by _emit_signal_decisions
        emitted_events = [e[0] for e in handler.telemetry.emitted]
        self.assertIn("tool_sandbox_denied", emitted_events)


class SandboxAdvisoryInjectionTests(unittest.TestCase):
    """Integration tests: advisory items are injected into body input for sandbox-denied signals."""

    _READONLY_FS_OUTPUT = (
        "Chunk ID: 0c3a9b\n"
        "Wall time: 0.0000 seconds\n"
        "Process exited with code 1\n"
        "Original token count: 16\n"
        "Output:\n"
        "/bin/bash: line 1: /etc/qz-denied-test: Read-only file system\n"
    )
    _CONN_REFUSED_OUTPUT = (
        "Chunk ID: ab12cd\n"
        "Wall time: 0.0010 seconds\n"
        "Process exited with code 1\n"
        "Output:\n"
        "curl: (7) Failed to connect to 127.0.0.1 port 18180: Connection refused\n"
    )

    def _classify_and_advise(self, input_items):
        """Classify input_items and return the advisory items a router would produce."""
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        router = RequestRouter(handler)
        signals = classify_native_tool_output_signals(input_items)
        return router._model_visible_native_advisories(signals)

    def test_sandbox_denied_input_produces_advisory(self):
        input_items = [
            {"type": "function_call", "call_id": "call_denied", "name": "exec_command",
             "arguments": '{"cmd": "echo hi > /etc/test"}'},
            {"type": "function_call_output", "call_id": "call_denied",
             "output": self._READONLY_FS_OUTPUT},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(len(advisories), 1)
        advisory = advisories[0]
        self.assertEqual(advisory["type"], "function_call_output")
        self.assertEqual(advisory["call_id"], "call_denied")
        self.assertIn("read-only filesystem", advisory["output"].lower())

    def test_advisory_added_to_body_input(self):
        """Advisory items are appended after the existing input items."""
        from proxy.qz_native_tool_output import classify_native_tool_output_signals
        from proxy.qz_proxy_tools import ProxyLocalToolRegistry
        handler = FakeHandler(ProxyLocalToolRegistry([]))
        router = RequestRouter(handler)

        input_items = [
            {"type": "function_call_output", "call_id": "call_ro",
             "output": self._READONLY_FS_OUTPUT},
        ]
        body = {"input": list(input_items)}
        signals = classify_native_tool_output_signals(input_items)
        advisories = router._model_visible_native_advisories(signals)

        self.assertEqual(len(advisories), 1)
        # Simulate the injection (as done in proxy_json_api)
        body["input"] = list(input_items) + advisories

        self.assertEqual(len(body["input"]), 2)
        self.assertEqual(body["input"][0]["type"], "function_call_output")
        self.assertEqual(body["input"][1]["type"], "function_call_output")
        self.assertEqual(body["input"][1]["call_id"], "call_ro")

    def test_normal_output_produces_no_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_ok",
             "output": "Process exited with code 0\nOutput:\nall good\n"},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_permission_denied_does_not_add_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_perm",
             "output": "Process exited with code 1\nOutput:\nbash: permission denied\n"},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_connection_refused_produces_no_advisory(self):
        input_items = [
            {"type": "function_call_output", "call_id": "call_conn",
             "output": self._CONN_REFUSED_OUTPUT},
        ]
        advisories = self._classify_and_advise(input_items)
        self.assertEqual(advisories, [])

    def test_original_input_items_not_mutated_by_advisory_path(self):
        import copy
        input_items = [
            {"type": "function_call_output", "call_id": "call_ro",
             "output": self._READONLY_FS_OUTPUT},
        ]
        original = copy.deepcopy(input_items)
        self._classify_and_advise(input_items)
        self.assertEqual(input_items, original)


class ToolSchemaTelemetryRouterTests(unittest.TestCase):
    """Tests for RequestRouter._emit_schema_normalization_telemetry helper."""

    class FakeTelemetry:
        def __init__(self):
            self.emitted = []

        def emit(self, event_type, payload):
            self.emitted.append((event_type, payload))

    class FakeHandlerWithTelemetry:
        def __init__(self):
            self.telemetry = ToolSchemaTelemetryRouterTests.FakeTelemetry()

    def _router(self):
        return RequestRouter(self.FakeHandlerWithTelemetry())

    def _make_report(self, **kwargs):
        from proxy.qz_tool_request import ToolRequestNormalizationReport
        return ToolRequestNormalizationReport(**kwargs)

    def test_emits_event_when_replaced_nonempty(self):
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_1")

        self.assertEqual(len(router.handler.telemetry.emitted), 1)
        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["replaced"])

    def test_emits_event_when_translated_nonempty(self):
        router = self._router()
        report = self._make_report(translated=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_2")

        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["translated"])

    def test_emits_event_when_deduped_nonempty(self):
        router = self._router()
        report = self._make_report(deduped=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_3")

        event_type, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(event_type, "tool_schema_replaced")
        self.assertIn("web_search", payload["deduped"])

    def test_payload_has_source_field(self):
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_4")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(payload["source"], "tool_schema_normalizer")

    def test_payload_has_request_id(self):
        router = self._router()
        report = self._make_report(translated=("apply_patch",))

        router._emit_schema_normalization_telemetry(report, request_id="req_xyz")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertEqual(payload["request_id"], "req_xyz")

    def test_payload_has_dropped_list_and_count(self):
        router = self._router()
        report = self._make_report(dropped=("write_stdin(no live exec session)",))

        router._emit_schema_normalization_telemetry(report, request_id="req_5")

        _, payload = router.handler.telemetry.emitted[0]
        self.assertIsInstance(payload["dropped"], list)
        self.assertIn("write_stdin(no live exec session)", payload["dropped"])
        self.assertEqual(payload["dropped_count"], 1)

    def test_no_emit_when_nothing_changed(self):
        router = self._router()
        report = self._make_report()  # all empty

        router._emit_schema_normalization_telemetry(report, request_id="req_6")

        self.assertEqual(len(router.handler.telemetry.emitted), 0)

    def test_payload_does_not_include_full_schema(self):
        """Only names/counts go into telemetry — not full schema dicts."""
        router = self._router()
        report = self._make_report(replaced=("web_search",))

        router._emit_schema_normalization_telemetry(report, request_id="req_7")

        _, payload = router.handler.telemetry.emitted[0]
        # Values must all be scalars or lists of strings, not dicts
        for val in payload.values():
            if isinstance(val, list):
                for item in val:
                    self.assertIsInstance(item, str, f"non-string in payload list: {item}")


if __name__ == "__main__":
    unittest.main()
