import unittest

from proxy.qz_proxy_tools import (
    ProxyLocalToolExecutor,
    ProxyLocalToolRegistry,
    ProxyToolExecutionContext,
    make_proxy_local_tool_registry,
)
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolLifecycleSpec


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
    """Test-only proxy-local executor.

    Uses a fictional 'qz_probe_call' public item type.
    No lifecycle_event_prefix — the fake lifecycle subevent system was removed.
    See docs/codex-source-tool-contract.md.
    """
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


class NativeToolListContractTests(unittest.TestCase):
    """Enforce the Codex-source-backed native tool name list.

    See docs/codex-source-tool-contract.md — only exec_command, write_stdin,
    shell_command are proven in codex-rs/core/src/tools/handlers/.
    computer is a reserved validation namespace only, not a handler.
    """

    def test_exec_command_in_native_tool_names(self):
        self.assertIn("exec_command", CODEX_NATIVE_TOOL_NAMES)

    def test_write_stdin_in_native_tool_names(self):
        self.assertIn("write_stdin", CODEX_NATIVE_TOOL_NAMES)

    def test_shell_command_in_native_tool_names(self):
        self.assertIn("shell_command", CODEX_NATIVE_TOOL_NAMES)

    def test_computer_not_in_native_tool_names(self):
        """computer is a reserved namespace, not a native Codex handler.

        codex-rs/app-server/src/request_processors/thread_processor.rs:194
        lists 'computer' only as a reserved tool namespace for validation,
        not as a handler or spec.  Removed from CODEX_NATIVE_TOOL_NAMES to
        prevent silent pass-through of unsupported tool calls.
        """
        self.assertNotIn(
            "computer", CODEX_NATIVE_TOOL_NAMES,
            "'computer' is not a native Codex handler — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_native_tool_names_contains_exactly_proven_tools(self):
        expected = {"exec_command", "write_stdin", "shell_command"}
        self.assertEqual(
            set(CODEX_NATIVE_TOOL_NAMES), expected,
            f"CODEX_NATIVE_TOOL_NAMES should be exactly {expected}",
        )


class ToolLifecycleSpecContractTests(unittest.TestCase):
    """ToolLifecycleSpec no longer carries lifecycle_event_prefix or stage lists.

    The fake lifecycle subevent system (response.<tool>_call.in_progress etc.) was
    removed because current Codex source does not parse those events.
    See docs/codex-source-tool-contract.md.
    """

    def test_spec_has_no_lifecycle_event_prefix_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_event_prefix"),
            "ToolLifecycleSpec must not have lifecycle_event_prefix — removed in issue #66",
        )

    def test_spec_has_no_lifecycle_start_stages_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_start_stages"),
            "ToolLifecycleSpec must not have lifecycle_start_stages — removed in issue #66",
        )

    def test_spec_has_no_lifecycle_done_stages_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_done_stages"),
            "ToolLifecycleSpec must not have lifecycle_done_stages — removed in issue #66",
        )


class ProxyToolRegistryTests(unittest.TestCase):
    def test_registry_classifies_only_registered_proxy_local_calls(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        self.assertEqual(registry.function_names, frozenset({"web_search"}))
        self.assertEqual(registry.max_continuation_hops, 6)
        self.assertEqual(registry.specs[0].execution, "proxy_local")
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

    def test_spec_for_call_exposes_web_search_contract(self):
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
        # Lifecycle subevent fields were removed — check they are absent.
        self.assertFalse(hasattr(spec, "lifecycle_event_prefix"),
                         "lifecycle_event_prefix must not exist on web_search ToolLifecycleSpec")
        self.assertFalse(hasattr(spec, "lifecycle_start_stages"),
                         "lifecycle_start_stages must not exist on web_search ToolLifecycleSpec")
        self.assertFalse(hasattr(spec, "lifecycle_done_stages"),
                         "lifecycle_done_stages must not exist on web_search ToolLifecycleSpec")

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

    def test_proxy_local_registry_has_no_fake_lifecycle_methods(self):
        """lifecycle_event_chunks/start/done_event_chunks were removed (issue #66).

        These methods emitted fake response.<tool>_call.* SSE events that
        current Codex source does not parse.
        """
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        self.assertFalse(hasattr(registry, "lifecycle_event_chunks"),
                         "lifecycle_event_chunks must be removed")
        self.assertFalse(hasattr(registry, "lifecycle_start_event_chunks"),
                         "lifecycle_start_event_chunks must be removed")
        self.assertFalse(hasattr(registry, "lifecycle_done_event_chunks"),
                         "lifecycle_done_event_chunks must be removed")

    def test_completed_call_decision_uses_registered_proxy_local_names(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        web_decision = registry.completed_call_decision({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        })
        patch_decision = registry.completed_call_decision({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        })

        self.assertEqual(web_decision.kind, "proxy_local")
        self.assertEqual(patch_decision.kind, "public")
        self.assertEqual(patch_decision.public_item["type"], "custom_tool_call")

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

    def test_registry_is_not_web_search_specific(self):
        """ProxyLocalToolRegistry works with any proxy-local executor.

        Tests basic execution contract using qz_probe test tool.
        Does NOT test fake lifecycle subevent generation — those were removed.
        """
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
