import unittest

from proxy.qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
from proxy.qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
from proxy.qz_tools import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_registry_adapts_apply_patch_tool_and_choice(self):
        registry = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER,))
        tool = {"type": "apply_patch"}

        adapted = registry.adapter_for_tool(tool).to_upstream_tool(tool)
        choice = registry.normalize_tool_choice({"type": "apply_patch"})

        self.assertEqual(adapted["type"], "function")
        self.assertEqual(adapted["name"], "apply_patch")
        self.assertEqual(choice, {"type": "function", "name": "apply_patch"})

    def test_registry_adapts_output_items_to_codex(self):
        registry = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER,))
        passthrough = {"type": "message", "role": "assistant", "content": []}
        patch_call = {
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }

        out = registry.output_items_to_codex([passthrough, patch_call])

        self.assertEqual(out[0], passthrough)
        self.assertEqual(out[1]["type"], "custom_tool_call")
        self.assertEqual(out[1]["call_id"], "call_patch")

    def test_registry_adapts_web_search_tool_and_choice(self):
        registry = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER, WEB_SEARCH_TOOL_ADAPTER))
        tool = {"type": "web_search"}

        adapted = registry.adapter_for_tool(tool).to_upstream_tool(tool)
        choice = registry.normalize_tool_choice({"type": "web_search"})

        self.assertEqual(adapted["type"], "function")
        self.assertEqual(adapted["name"], "web_search")
        self.assertEqual(choice, {"type": "function", "name": "web_search"})


if __name__ == "__main__":
    unittest.main()


class ToolCoercionResultTests(unittest.TestCase):
    def test_succeeded_when_corrected_arguments_set(self):
        from proxy.qz_tools import ToolCoercionResult
        r = ToolCoercionResult(corrected_arguments='{"ok": true}')
        self.assertTrue(r.succeeded())
        self.assertIsNone(r.error_message)

    def test_not_succeeded_when_error_message_set(self):
        from proxy.qz_tools import ToolCoercionResult
        r = ToolCoercionResult(error_message="something went wrong")
        self.assertFalse(r.succeeded())
        self.assertIsNone(r.corrected_arguments)

    def test_neither_set_raises(self):
        from proxy.qz_tools import ToolCoercionResult
        with self.assertRaises(ValueError):
            ToolCoercionResult()

    def test_both_set_raises(self):
        from proxy.qz_tools import ToolCoercionResult
        with self.assertRaises(ValueError):
            ToolCoercionResult(corrected_arguments='{}', error_message="oops")

    def test_empty_error_message_raises(self):
        from proxy.qz_tools import ToolCoercionResult
        with self.assertRaises(ValueError):
            ToolCoercionResult(error_message="")


class SynthesizeToolErrorResultTests(unittest.TestCase):
    def test_produces_function_call_output(self):
        import json
        from proxy.qz_tools import synthesize_tool_error_result
        call = {"type": "function_call", "name": "my_tool", "call_id": "c1", "arguments": "{}"}
        result = synthesize_tool_error_result(call, "tool not available")
        self.assertEqual(result["type"], "function_call_output")
        self.assertEqual(result["call_id"], "c1")
        payload = json.loads(result["output"])
        self.assertFalse(payload["ok"])
        self.assertIn("not available", payload["error"])

    def test_uses_call_id_from_call(self):
        from proxy.qz_tools import synthesize_tool_error_result
        call = {"call_id": "my_call_id"}
        result = synthesize_tool_error_result(call, "error")
        self.assertEqual(result["call_id"], "my_call_id")

    def test_uses_id_field_when_call_id_absent(self):
        """Falls back to 'id' key when 'call_id' is missing."""
        from proxy.qz_tools import synthesize_tool_error_result
        call = {"id": "fc_fallback"}
        result = synthesize_tool_error_result(call, "error")
        self.assertEqual(result["call_id"], "fc_fallback")

    def test_null_call_uses_err_fallback_id(self):
        """A None call dict does not raise; produces an err_<ts> fallback call_id."""
        from proxy.qz_tools import synthesize_tool_error_result
        result = synthesize_tool_error_result(None, "error")  # type: ignore[arg-type]
        self.assertTrue(result["call_id"].startswith("err_"))
        self.assertEqual(result["type"], "function_call_output")

    def test_empty_call_dict_uses_err_fallback_id(self):
        from proxy.qz_tools import synthesize_tool_error_result
        result = synthesize_tool_error_result({}, "some error")
        self.assertTrue(result["call_id"].startswith("err_"))

    def test_delegates_to_render_coercion_error(self):
        """synthesize_tool_error_result output must be identical to render_coercion_error."""
        import json
        from proxy.qz_tools import synthesize_tool_error_result
        from proxy.qz_feedback import render_coercion_error
        for call, msg in [
            ({"call_id": "c1"}, "error text"),
            ({"id": "fc_1"}, "another error"),
            ({}, "fallback test"),
        ]:
            old = synthesize_tool_error_result(call, msg)
            new = render_coercion_error(call, msg)
            self.assertEqual(old["type"], new["type"],
                             f"type mismatch for {call}")
            self.assertEqual(old["call_id"], new["call_id"],
                             f"call_id mismatch for {call}")
            # Both outputs are JSON strings; compare parsed to avoid key-order issues
            old_payload = json.loads(old["output"])
            new_payload = json.loads(new["output"])
            self.assertEqual(old_payload, new_payload,
                             f"payload mismatch for {call}")


class WebSearchCoerceTests(unittest.TestCase):
    def test_valid_json_passes_through(self):
        import json
        call = {"name": "web_search", "call_id": "c1",
                "arguments": json.dumps({"action": "search", "query": "test"})}
        result = WEB_SEARCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(result.succeeded())

    def test_bad_json_returns_error(self):
        call = {"name": "web_search", "call_id": "c1", "arguments": "{not json}"}
        result = WEB_SEARCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIn("JSON", result.error_message)

    def test_non_dict_json_returns_error(self):
        call = {"name": "web_search", "call_id": "c1", "arguments": '"just a string"'}
        result = WEB_SEARCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())


class DroppedToolFeedbackTests(unittest.TestCase):
    def _make_registry(self):
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry

        class FakeWebRuntime:
            def execute_web_search_call(self, call, counters, seen_signatures, request_id=""):
                return type('R', (), {'public_item': {}, 'upstream_items': (), 'sources': ()})()

        return make_proxy_local_tool_registry(FakeWebRuntime())

    def test_dropped_tool_returns_error_decision(self):
        registry = self._make_registry()
        call = {"type": "function_call", "name": "secret_tool", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(
            call, dropped_tool_names=frozenset({"secret_tool"})
        )
        self.assertEqual(decision.kind, "error")
        self.assertIsNotNone(decision.error_result)
        self.assertIn("secret_tool", decision.error_result["output"])
        self.assertIn("not available", decision.error_result["output"])

    def test_unknown_tool_returns_error_decision(self):
        registry = self._make_registry()
        call = {"type": "function_call", "name": "totally_unknown", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "error")
        self.assertIn("totally_unknown", decision.error_result["output"])

    def test_codex_native_tool_passes_through(self):
        registry = self._make_registry()
        call = {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "public")

    def test_new_native_tools_pass_through_without_error(self):
        """Tools added in issues #67 and #68 must not be given unsupported-tool errors."""
        registry = self._make_registry()
        new_tools = [
            # issue #67
            "update_plan",
            "request_user_input",
            "request_permissions",
            "view_image",
            "get_goal",
            "create_goal",
            "update_goal",
            # issue #68 — shell + container.exec
            "shell",
            "container.exec",
        ]
        for tool_name in new_tools:
            with self.subTest(tool=tool_name):
                call = {"type": "function_call", "name": tool_name, "call_id": "c1", "arguments": "{}"}
                decision = registry.completed_call_decision(call)
                self.assertEqual(
                    decision.kind, "public",
                    f"Tool '{tool_name}' should be native pass-through (kind='public'), got '{decision.kind}'",
                )

    def test_shell_passes_through_as_public(self):
        """shell is a confirmed function_call handler (shell_handler.rs issue #68).

        Registered as ShellHandler::default() fallback when shell_type != Default.
        Must pass through, not receive an unsupported-tool error.
        """
        registry = self._make_registry()
        call = {"type": "function_call", "name": "shell", "call_id": "c2",
                "arguments": '{"command": ["bash", "-lc", "echo hi"]}'}
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "public",
                         "shell must be native pass-through (kind='public')")

    def test_container_exec_passes_through_as_public(self):
        """container.exec is a confirmed function_call fallback handler (container_exec.rs issue #68).

        Never advertised (no spec), always registered in non-disabled configs.
        Must pass through, not receive an unsupported-tool error.
        """
        registry = self._make_registry()
        call = {"type": "function_call", "name": "container.exec", "call_id": "c3",
                "arguments": '{"command": ["ls", "-la"]}'}
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "public",
                         "container.exec must be native pass-through (kind='public')")

    def test_no_fake_lifecycle_event_prefix_for_native_tools(self):
        """Native tools must not produce fake response.<tool>_call.* SSE events.

        The registry completed_call_decision must not reference lifecycle_event_prefix
        or any response.<tool>_call.* string for native tool names.
        Regression guard: ensures issue #66 removals stay removed.
        Updated in issue #68: shell and container.exec added to the check list.
        """
        registry = self._make_registry()
        native_names = [
            "update_plan", "request_user_input", "request_permissions",
            "view_image", "get_goal", "create_goal", "update_goal",
            "exec_command", "write_stdin", "shell_command",
            "shell", "container.exec",
        ]
        for name in native_names:
            with self.subTest(tool=name):
                call = {"type": "function_call", "name": name, "call_id": "c1", "arguments": "{}"}
                decision = registry.completed_call_decision(call)
                # Decision must be public (pass-through), never signal or error for native tools
                self.assertNotEqual(
                    decision.kind, "error",
                    f"Native tool '{name}' must not produce an error decision",
                )
                # The decision object must not carry fake lifecycle event strings
                decision_repr = repr(decision)
                for forbidden in [f"response.{name}_call.", "lifecycle_event_prefix", "_call.in_progress"]:
                    self.assertNotIn(
                        forbidden, decision_repr,
                        f"Native tool '{name}' decision contains forbidden lifecycle string '{forbidden}'",
                    )


class NativeToolNamesMembershipTests(unittest.TestCase):
    """Confirm CODEX_NATIVE_TOOL_NAMES membership directly, without going through the proxy registry.

    These tests are redundant with NativeToolListContractTests in test_qz_proxy_tools.py
    but provide a fast, direct check that imports only qz_tools.
    """

    def _names(self):
        from proxy.qz_tools import CODEX_NATIVE_TOOL_NAMES
        return CODEX_NATIVE_TOOL_NAMES

    # --- presence ---

    def test_exec_command_present(self):
        self.assertIn("exec_command", self._names())

    def test_write_stdin_present(self):
        self.assertIn("write_stdin", self._names())

    def test_shell_command_present(self):
        self.assertIn("shell_command", self._names())

    def test_update_plan_present(self):
        self.assertIn("update_plan", self._names())

    def test_request_user_input_present(self):
        self.assertIn("request_user_input", self._names())

    def test_request_permissions_present(self):
        self.assertIn("request_permissions", self._names())

    def test_view_image_present(self):
        self.assertIn("view_image", self._names())

    def test_get_goal_present(self):
        self.assertIn("get_goal", self._names())

    def test_create_goal_present(self):
        self.assertIn("create_goal", self._names())

    def test_update_goal_present(self):
        self.assertIn("update_goal", self._names())

    def test_shell_present(self):
        """shell: shell/shell_handler.rs ToolName::plain("shell") — issue #68"""
        self.assertIn("shell", self._names())

    def test_container_exec_present(self):
        """container.exec: shell/container_exec.rs ToolName::plain("container.exec") — issue #68"""
        self.assertIn("container.exec", self._names())

    # --- absence ---

    def test_computer_absent(self):
        self.assertNotIn("computer", self._names())

    def test_apply_patch_absent(self):
        self.assertNotIn("apply_patch", self._names())

    def test_web_search_absent(self):
        self.assertNotIn("web_search", self._names())

    def test_tool_search_absent(self):
        """tool_search: ToolSearchCall/ToolSearchOutput item type — NOT function_call. (issue #69)"""
        self.assertNotIn("tool_search", self._names())

    def test_local_shell_absent(self):
        """local_shell: LocalShellCall item + ToolPayload::LocalShell dispatch — NOT function_call. (issue #69)"""
        self.assertNotIn("local_shell", self._names())

    def test_image_generation_absent(self):
        """image_generation: hosted ToolSpec::ImageGeneration, no function_call handler. (issue #70)"""
        self.assertNotIn("image_generation", self._names())

    def test_list_mcp_resources_absent(self):
        """list_mcp_resources: function_call handler but gated on MCP infra QuantZhai lacks. (issue #70)"""
        self.assertNotIn("list_mcp_resources", self._names())

    def test_spawn_agent_absent(self):
        """spawn_agent: function_call handler gated by collab_tools — not in QuantZhai. (issue #70)"""
        self.assertNotIn("spawn_agent", self._names())

    def test_request_plugin_install_absent(self):
        """request_plugin_install: function_call handler gated on plugin/auth infra. (issue #70)"""
        self.assertNotIn("request_plugin_install", self._names())

    def test_spawn_agents_on_csv_absent(self):
        """spawn_agents_on_csv: function_call handler gated by agent_jobs_tools flag. (issue #70)"""
        self.assertNotIn("spawn_agents_on_csv", self._names())


class NativeToolNamesPolicyTests(unittest.TestCase):
    """Lock the corrected policy: CODEX_NATIVE_TOOL_NAMES is a routing inventory, not an
    authority or immutability boundary. Tests are low-risk and focus on doc/comment presence
    and invariant enforcement.
    """

    def test_comment_identifies_as_routing_inventory(self):
        """The block comment near CODEX_NATIVE_TOOL_NAMES must describe it as routing inventory."""
        with open("proxy/qz_tools.py", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("routing inventory", content,
                      "CODEX_NATIVE_TOOL_NAMES comment must describe it as routing inventory")
        self.assertIn("NOT an authority", content,
                      "CODEX_NATIVE_TOOL_NAMES comment must clarify it is not an authority")
        # The comment wraps "immutability\\n# boundary" across two lines, so
        # check for each substring separately.
        self.assertIn("NOT an authority or immutability", content,
                      "CODEX_NATIVE_TOOL_NAMES comment must clarify it is not an authority or immutability boundary")
        self.assertIn("boundary", content,
                      "CODEX_NATIVE_TOOL_NAMES comment must contain 'boundary'")

    def test_parity_policy_doc_exists(self):
        """The parity/policy matrix doc must exist and cover the audit."""
        from pathlib import Path
        doc = Path("docs/codex-tool-parity-and-proxy-policy.md")
        self.assertTrue(doc.exists(), "Parity/policy matrix doc must exist")
        text = doc.read_text(encoding="utf-8")
        self.assertIn("46f30d02828bd4c52827e5f0482a6f2a982cce5b", text,
                      "Parity doc must contain the audit SHA")
        self.assertIn("CODEX_NATIVE_TOOL_NAMES", text,
                      "Parity doc must mention CODEX_NATIVE_TOOL_NAMES")

    def test_no_custom_tool_call_input_done_regression(self):
        """Response custom_tool_call_input.done must not appear in streaming code."""
        with open("proxy/qz_streaming.py", encoding="utf-8") as f:
            content = f.read()
        # The string 'custom_tool_call_input.done' must not be in the streaming module
        # (it was removed in issue #73 because Codex does not parse it)
        self.assertNotIn(
            "custom_tool_call_input.done",
            content,
            "response.custom_tool_call_input.done must not be emitted — Codex does not parse it",
        )

    def test_apply_patch_native_advisory_path_not_used(self):
        """apply_patch must NOT be assumed part of the native function_call advisory path.
        It is a protocol adapter (custom_tool_call), not a native tool.
        The completed_call_decision path for apply_patch goes through path #3 not #4.
        """
        from proxy.qz_tools import CODEX_NATIVE_TOOL_NAMES
        self.assertNotIn("apply_patch", CODEX_NATIVE_TOOL_NAMES,
                         "apply_patch is a protocol adapter, not native pass-through")

    def test_web_search_not_in_native_names(self):
        """web_search must remain proxy-local, not in CODEX_NATIVE_TOOL_NAMES."""
        from proxy.qz_tools import CODEX_NATIVE_TOOL_NAMES
        self.assertNotIn("web_search", CODEX_NATIVE_TOOL_NAMES,
                         "web_search is proxy-local, not native pass-through")
