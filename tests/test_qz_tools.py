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

        out = registry.output_items_to_codex([passthrough, patch_call], "native")

        self.assertEqual(out[0], passthrough)
        self.assertEqual(out[1]["type"], "apply_patch_call")
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
            call, "custom", dropped_tool_names=frozenset({"secret_tool"})
        )
        self.assertEqual(decision.kind, "error")
        self.assertIsNotNone(decision.error_result)
        self.assertIn("secret_tool", decision.error_result["output"])
        self.assertIn("not available", decision.error_result["output"])

    def test_unknown_tool_returns_error_decision(self):
        registry = self._make_registry()
        call = {"type": "function_call", "name": "totally_unknown", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call, "custom")
        self.assertEqual(decision.kind, "error")
        self.assertIn("totally_unknown", decision.error_result["output"])

    def test_codex_native_tool_passes_through(self):
        registry = self._make_registry()
        call = {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call, "custom")
        self.assertEqual(decision.kind, "public")
