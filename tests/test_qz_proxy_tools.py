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


if __name__ == "__main__":
    unittest.main()
