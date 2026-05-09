import unittest

from proxy.qz_proxy_tools import (
    ProxyToolExecutionContext,
    make_proxy_local_tool_registry,
)


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


class ProxyToolRegistryTests(unittest.TestCase):
    def test_registry_classifies_only_registered_proxy_local_calls(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        self.assertEqual(registry.function_names, frozenset({"web_search"}))
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


if __name__ == "__main__":
    unittest.main()
