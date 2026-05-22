import unittest

from proxy.qz_proxy_tools import (
    ProxyLocalToolExecutor,
    ProxyLocalToolRegistry,
    ProxyToolExecutionContext,
    make_proxy_local_tool_registry,
)
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import ToolLifecycleSpec


class FakeWebRuntime:
    def __init__(self):
        self.calls = []

    def execute_web_search_call(self, call, counters, seen_signatures, request_id=""):
        self.calls.append({
            "call": call,
            "counters": counters,
            "seen_signatures": seen_signatures,
            "request_id": request_id,
        })
        return (
            {
                "id": "wsc_fake",
                "type": "web_search_call",
                "status": "completed",
                "call_id": call.get("call_id"),
            },
            {
                "type": "function_call_output",
                "call_id": call.get("call_id"),
                "output": "{\"ok\":true}",
            },
            [{"url": "https://example.test"}],
        )


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

    def __init__(self):
        self.calls = []

    def started_public_item(self, call: dict, public_index: int) -> dict:
        return {
            "id": call.get("id") or f"qz_probe_{public_index}",
            "type": "qz_probe_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }

    def execute(self, call: dict, context: ProxyToolExecutionContext) -> ToolContinuationResult:
        self.calls.append({
            "call": call,
            "request_id": context.request_id,
            "counters": context.counters,
            "seen_signatures": context.seen_signatures,
        })
        return ToolContinuationResult(
            public_item={
                "id": call.get("id") or "qz_probe_0",
                "type": "qz_probe_call",
                "status": "completed",
                "call_id": call.get("call_id"),
                "output": "probe ok",
            },
            upstream_items=(
                call,
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": "{\"probe\":\"ok\"}",
                },
            ),
            sources=({"url": "probe://local", "title": "probe"},),
        )


class ProxyToolRegistryTests(unittest.TestCase):
    def test_registry_classifies_only_registered_proxy_local_calls(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        self.assertEqual(registry.function_names, frozenset({"web_search"}))
        self.assertEqual(registry.max_continuation_hops, 6)
        self.assertEqual(registry.specs[0].execution, "proxy_local")
        self.assertEqual(registry.specs[0].lifecycle_event_prefix, "response.web_search_call")
        self.assertTrue(registry.is_proxy_local_call({
            "type": "function_call",
            "name": "web_search",
        }))
        self.assertFalse(registry.is_proxy_local_call({
            "type": "function_call",
            "name": "apply_patch",
        }))

    def test_started_public_item_uses_web_search_display_shape(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        item = registry.started_public_item({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
        }, public_index=3)

        self.assertEqual(item["id"], "fc_web")
        self.assertEqual(item["type"], "web_search_call")
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["call_id"], "call_web")

    def test_spec_for_call_exposes_generic_lifecycle_contract(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        spec = registry.spec_for_call({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
        })

        self.assertEqual(spec.name, "web_search")
        self.assertEqual(spec.public_item_type, "web_search_call")
        self.assertEqual(spec.telemetry_name, "web_search")
        self.assertEqual(spec.lifecycle_start_stages, ("in_progress", "searching"))
        self.assertEqual(spec.lifecycle_done_stages, ("completed",))

    def test_proxy_local_telemetry_payload_comes_from_lifecycle_spec(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        started = registry.telemetry_payload(call)

        self.assertEqual(started["tool"], "web_search")
        self.assertEqual(started["function_name"], "web_search")
        self.assertEqual(started["call_id"], "call_web")
        self.assertEqual(started["execution"], "proxy_local")
        self.assertEqual(started["public_item_type"], "web_search_call")
        self.assertNotIn("sources", started)

    def test_proxy_local_completed_telemetry_includes_result_counts(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters={},
                seen_signatures=set(),
            ),
        )

        completed = registry.telemetry_payload(call, result=result)

        self.assertEqual(completed["sources"], 1)
        self.assertEqual(completed["upstream_items"], 2)

    def test_proxy_local_stream_reasons_are_registry_owned(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        self.assertEqual(registry.terminal_suppression_reason(call), "web_search_terminal")
        self.assertIn("web_search", registry.continuation_limit_message())

    def test_proxy_local_lifecycle_events_are_registry_owned(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        start_chunks, sequence = registry.lifecycle_start_event_chunks(call, "wsc_1", 4, 10)
        done_chunks, sequence = registry.lifecycle_done_event_chunks(call, "wsc_1", 4, sequence)

        start_text = b"".join(start_chunks).decode("utf-8")
        done_text = b"".join(done_chunks).decode("utf-8")
        self.assertIn("response.web_search_call.in_progress", start_text)
        self.assertIn("response.web_search_call.searching", start_text)
        self.assertIn("response.web_search_call.completed", done_text)
        self.assertEqual(sequence, 13)

    def test_proxy_local_lifecycle_rejects_unsupported_stage(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        with self.assertRaises(ValueError):
            registry.lifecycle_event_chunks(call, "bogus", "wsc_1", 4, 0)

    def test_completed_call_decision_uses_registered_proxy_local_names(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        web_decision = registry.completed_call_decision({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }, "native")
        patch_decision = registry.completed_call_decision({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }, "native")

        self.assertEqual(web_decision.kind, "proxy_local")
        self.assertEqual(patch_decision.kind, "public")
        self.assertEqual(patch_decision.public_item["type"], "apply_patch_call")

    def test_completed_call_decision_keeps_unknown_function_call_public(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_exec",
            "type": "function_call",
            "call_id": "call_exec",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
        }

        decision = registry.completed_call_decision(call, "native")

        self.assertEqual(decision.kind, "public")
        self.assertEqual(decision.public_item, call)

    def test_continuation_result_returns_public_protocol_adapter_item(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        decision = registry.completed_call_decision({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }, "custom")

        result = registry.continuation_result(decision)

        self.assertEqual(result.public_item["type"], "custom_tool_call")
        self.assertEqual(result.public_item["name"], "apply_patch")
        self.assertEqual(result.upstream_items, ())
        self.assertEqual(result.sources, ())

    def test_execute_returns_public_item_and_hidden_upstream_continuation_items(self):
        web_runtime = FakeWebRuntime()
        registry = make_proxy_local_tool_registry(web_runtime)
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        counters = {"search": 0, "open_page": 0}
        seen = set()

        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters=counters,
                seen_signatures=seen,
            ),
        )

        self.assertEqual(web_runtime.calls[0]["request_id"], "qz_req_test")
        self.assertIs(web_runtime.calls[0]["counters"], counters)
        self.assertIs(web_runtime.calls[0]["seen_signatures"], seen)
        self.assertEqual(result.public_item["type"], "web_search_call")
        self.assertEqual(result.upstream_items[0], call)
        self.assertEqual(result.upstream_items[1]["type"], "function_call_output")
        self.assertEqual(result.sources, ({"url": "https://example.test"},))

    def test_continuation_result_executes_proxy_local_tool_with_context(self):
        web_runtime = FakeWebRuntime()
        registry = make_proxy_local_tool_registry(web_runtime)
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        counters = {"search": 0, "open_page": 0}
        seen = set()
        decision = registry.completed_call_decision(call, "native")

        result = registry.continuation_result(
            decision,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters=counters,
                seen_signatures=seen,
            ),
        )

        self.assertEqual(web_runtime.calls[0]["request_id"], "qz_req_test")
        self.assertEqual(result.public_item["type"], "web_search_call")
        self.assertEqual(result.upstream_items[0], call)

    def test_continuation_result_requires_context_for_proxy_local_tool(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        decision = registry.completed_call_decision({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }, "native")

        with self.assertRaises(ValueError):
            registry.continuation_result(decision)

    def test_registry_lifecycle_is_not_web_search_specific(self):
        executor = ProbeProxyToolExecutor()
        registry = ProxyLocalToolRegistry([executor])
        call = {
            "id": "fc_probe",
            "type": "function_call",
            "call_id": "call_probe",
            "name": "qz_probe",
            "arguments": "{\"value\":1}",
        }

        self.assertEqual(registry.function_names, frozenset({"qz_probe"}))
        self.assertEqual(registry.max_continuation_hops, 2)
        self.assertTrue(registry.is_proxy_local_call(call))

        started_item = registry.started_public_item(call, public_index=7)
        self.assertEqual(started_item["type"], "qz_probe_call")
        self.assertEqual(started_item["status"], "in_progress")

        start_chunks, sequence = registry.lifecycle_start_event_chunks(call, "qz_probe_1", 7, 20)
        done_chunks, sequence = registry.lifecycle_done_event_chunks(call, "qz_probe_1", 7, sequence)
        lifecycle_text = b"".join(start_chunks + done_chunks).decode("utf-8")
        self.assertIn("response.qz_probe_call.in_progress", lifecycle_text)
        self.assertIn("response.qz_probe_call.working", lifecycle_text)
        self.assertIn("response.qz_probe_call.completed", lifecycle_text)
        self.assertEqual(sequence, 23)

        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_probe",
                counters={"probe": 0},
                seen_signatures=set(),
            ),
        )
        telemetry = registry.telemetry_payload(call, result=result)
        self.assertEqual(executor.calls[0]["request_id"], "qz_req_probe")
        self.assertEqual(result.public_item["type"], "qz_probe_call")
        self.assertEqual(result.upstream_items[1]["type"], "function_call_output")
        self.assertEqual(telemetry["tool"], "qz_probe")
        self.assertEqual(telemetry["public_item_type"], "qz_probe_call")
        self.assertEqual(telemetry["sources"], 1)
        self.assertEqual(registry.terminal_suppression_reason(call), "qz_probe_terminal")


class RepeatedReadDecisionTests(unittest.TestCase):
    """Tests for repeated-read signal integration in completed_call_decision."""

    def _make_registry(self):
        return make_proxy_local_tool_registry(FakeWebRuntime())

    def _exec_call(self, cmd, call_id="call_abc"):
        return {
            "type": "function_call",
            "name": "exec_command",
            "call_id": call_id,
            "arguments": {"cmd": cmd},
        }

    def _make_state_with_read(self, path):
        from proxy.qz_file_signal import RepeatedReadState
        state = RepeatedReadState()
        state.read_paths.add(path)
        state.history_read_paths.add(path)
        return state

    def test_first_read_codex_native_passthrough(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = RepeatedReadState()
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "public")

    def test_repeated_read_signal_before_codex_native_passthrough(self):
        registry = self._make_registry()
        call = self._exec_call("cat README.md", "call_1")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "signal")

    def test_repeated_read_signal_uses_original_call_id(self):
        import json
        registry = self._make_registry()
        call = self._exec_call("cat README.md", "call_orig_id")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "signal")
        self.assertIsNotNone(decision.signal_result)
        self.assertEqual(decision.signal_result["call_id"], "call_orig_id")
        self.assertEqual(decision.signal_result["type"], "function_call_output")

    def test_repeated_read_signal_not_tool_call_error(self):
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertNotEqual(decision.kind, "error")
        self.assertEqual(decision.kind, "signal")

    def test_repeated_read_signal_output_is_not_json_ok_false(self):
        """Signal output is advisory text, not an error JSON payload."""
        import json
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        # Advisory output is plain text, not {"ok": false, ...}
        output = decision.signal_result["output"]
        self.assertIsInstance(output, str)
        self.assertNotIn('"ok"', output)
        self.assertIn("already read", output.lower())

    def test_repeat_after_warning_passthrough(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = RepeatedReadState()
        state.read_paths.add("README.md")
        state.warned_paths.add("README.md")  # already warned
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        # After prior warning, allow through
        self.assertEqual(decision.kind, "public")

    def test_signal_marks_warned_paths(self):
        """Returning a signal marks the paths as warned so next repeat is allowed."""
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "signal")
        self.assertIn("README.md", state.warned_paths)

    def test_signal_metadata_present(self):
        registry = self._make_registry()
        call = self._exec_call("cat README.md", "call_m")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        md = decision.signal_metadata
        self.assertIsNotNone(md)
        self.assertIn("README.md", md.get("paths", []))
        self.assertEqual(md.get("action"), "signalled")
        self.assertEqual(md.get("tool"), "exec_command")
        self.assertEqual(md.get("call_id"), "call_m")

    def test_no_state_exec_command_still_public(self):
        """Without repeated_read_state, exec_command is a normal public passthrough."""
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=None)
        self.assertEqual(decision.kind, "public")

    def test_apply_patch_unaffected(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "apply_patch",
            "call_id": "call_ap",
            "arguments": '{"operation": "create", "path": "foo.py", "file_text": ""}',
        }
        state = RepeatedReadState()
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        # apply_patch goes through protocol adapter, not Codex-native signal check
        self.assertIn(decision.kind, {"public", "error"})
        self.assertNotEqual(decision.kind, "signal")

    def test_web_search_unaffected(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "web_search",
            "call_id": "call_ws",
            "arguments": '{"action": "search", "query": "quantzhai"}',
        }
        state = RepeatedReadState()
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        # web_search is proxy_local, not affected by repeated-read check
        self.assertEqual(decision.kind, "proxy_local")

    def test_unknown_tool_unaffected(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "some_unknown_tool",
            "call_id": "call_unk",
            "arguments": "{}",
        }
        state = RepeatedReadState()
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "error")


class CoercionInfoTests(unittest.TestCase):
    """Tests that coercion_applied and coercion_error are set correctly in decisions."""

    def _make_registry(self):
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry
        return make_proxy_local_tool_registry(FakeWebRuntime())

    def test_malformed_web_search_sets_coercion_applied_and_error(self):
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "web_search",
            "call_id": "c1", "arguments": "{not valid json}",
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "error")
        self.assertTrue(decision.coercion_applied)
        self.assertTrue(bool(decision.coercion_error))
        self.assertNotIn("{not valid json}", decision.coercion_error)

    def test_valid_web_search_sets_coercion_applied_no_error(self):
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "web_search",
            "call_id": "c1",
            "arguments": json.dumps({"action": "search", "query": "test"}),
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "proxy_local")
        self.assertTrue(decision.coercion_applied)
        self.assertEqual(decision.coercion_error, "")

    def test_dropped_tool_does_not_set_coercion_applied(self):
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "dropped_tool",
            "call_id": "c1", "arguments": "{}",
        }
        decision = registry.completed_call_decision(
            call, "native", dropped_tool_names=frozenset({"dropped_tool"})
        )
        self.assertEqual(decision.kind, "error")
        self.assertFalse(decision.coercion_applied)

    def test_unknown_tool_does_not_set_coercion_applied(self):
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "mystery_tool",
            "call_id": "c1", "arguments": "{}",
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "error")
        self.assertFalse(decision.coercion_applied)

    def test_codex_native_does_not_set_coercion_applied(self):
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "exec_command",
            "call_id": "c1", "arguments": "{}",
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "public")
        self.assertFalse(decision.coercion_applied)

    def test_apply_patch_sibling_patch_sets_coercion_applied(self):
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "apply_patch",
            "call_id": "c1",
            "arguments": json.dumps({
                "operation": {"type": "create_file", "path": "test.py"},
                "patch": "x = 1\n",
            }),
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "public")
        self.assertTrue(decision.coercion_applied)
        self.assertEqual(decision.coercion_error, "")

    def test_apply_patch_malformed_sets_coercion_error(self):
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call", "name": "apply_patch",
            "call_id": "c1",
            "arguments": json.dumps({"operation": {"type": "create_file", "path": "x.py"}}),
        }
        decision = registry.completed_call_decision(call, "native")
        self.assertEqual(decision.kind, "error")
        self.assertTrue(decision.coercion_applied)
        self.assertTrue(bool(decision.coercion_error))


if __name__ == "__main__":
    unittest.main()
