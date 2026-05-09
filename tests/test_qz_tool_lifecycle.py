import unittest

from proxy.qz_tool_lifecycle import (
    StreamToolCallState,
    ToolHistoryReplayFilter,
    completed_tool_call_decision,
    function_call_key,
    is_proxy_local_function_call,
    public_tool_item_from_function_call,
    tool_continuation_result,
)


class ToolLifecycleTests(unittest.TestCase):
    def test_tool_history_replay_filter_drops_empty_call_and_matching_output(self):
        history_filter = ToolHistoryReplayFilter()

        self.assertTrue(history_filter.should_drop({
            "type": "function_call",
            "id": "fc_empty",
            "call_id": "call_empty",
            "name": "apply_patch",
            "arguments": "",
        }))
        self.assertTrue(history_filter.should_drop({
            "type": "function_call_output",
            "call_id": "call_empty",
            "output": "result that should not be replayed",
        }))

    def test_tool_history_replay_filter_drops_parse_error_and_later_output(self):
        history_filter = ToolHistoryReplayFilter()

        self.assertTrue(history_filter.should_drop({
            "type": "function_call_output",
            "call_id": "call_bad_args",
            "output": "failed to parse function arguments: EOF while parsing",
        }))
        self.assertTrue(history_filter.should_drop({
            "type": "function_call_output",
            "call_id": "call_bad_args",
            "output": "late output for bad call",
        }))

    def test_tool_history_replay_filter_keeps_valid_call_and_unrelated_output(self):
        history_filter = ToolHistoryReplayFilter()

        self.assertFalse(history_filter.should_drop({
            "type": "function_call",
            "id": "fc_ok",
            "call_id": "call_ok",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
        }))
        self.assertFalse(history_filter.should_drop({
            "type": "function_call_output",
            "call_id": "call_ok",
            "output": "ok",
        }))
        self.assertFalse(history_filter.should_drop({
            "type": "function_call_output",
            "call_id": "call_other",
            "output": "failed command, not failed to parse function arguments:",
        }))

    def test_function_call_key_uses_item_id_then_item_then_output_index(self):
        self.assertEqual(function_call_key({"item_id": "fc_1"}), "fc_1")
        self.assertEqual(function_call_key({"item": {"id": "fc_2", "call_id": "call_2"}}), "fc_2")
        self.assertEqual(function_call_key({"item": {"call_id": "call_3"}}), "call_3")
        self.assertEqual(function_call_key({"output_index": 4}), "output:4")
        self.assertIsNone(function_call_key({"delta": "{}"}))

    def test_stream_tool_call_state_tracks_name_completion_and_delta_abort(self):
        state = StreamToolCallState()
        completed = state.observe("response.output_item.added", {
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "status": "in_progress",
                "call_id": "call_1",
                "name": "exec_command",
                "arguments": "",
            },
        }, received_at=10.0)
        self.assertEqual(completed, [])
        self.assertEqual(state.call_name, "exec_command")

        state.observe("response.function_call_arguments.delta", {
            "item_id": "fc_1",
            "delta": "{\"cmd\":",
        }, received_at=10.1)
        state.observe("response.function_call_arguments.delta", {
            "item_id": "fc_1",
            "delta": "\"pwd\"}",
        }, received_at=10.2)

        self.assertEqual(state.abort_reason(now=10.3, timeout_s=120, delta_limit=1), "delta_limit")
        self.assertEqual(state.abort_reason(now=10.3, timeout_s=0.1, delta_limit=10), "timeout")

        completed = state.observe("response.output_item.done", {
            "output_index": 0,
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "exec_command",
            },
        }, received_at=10.4)

        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["arguments"], "{\"cmd\":\"pwd\"}")
        self.assertEqual(completed[0]["output_index"], 0)

    def test_public_tool_item_from_function_call_rewrites_apply_patch(self):
        item = public_tool_item_from_function_call({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }, "native")

        self.assertEqual(item["type"], "apply_patch_call")
        self.assertEqual(item["call_id"], "call_patch")

    def test_public_tool_item_from_function_call_leaves_public_function_calls(self):
        call = {
            "id": "fc_exec",
            "type": "function_call",
            "call_id": "call_exec",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
        }

        self.assertEqual(public_tool_item_from_function_call(call, "native"), call)

    def test_completed_tool_call_decision_classifies_proxy_local_web_search(self):
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        decision = completed_tool_call_decision(call, "native", frozenset({"web_search"}))

        self.assertTrue(is_proxy_local_function_call(call, frozenset({"web_search"})))
        self.assertEqual(decision.kind, "proxy_local")
        self.assertEqual(decision.call, call)
        self.assertIsNone(decision.public_item)

    def test_completed_tool_call_decision_uses_supplied_proxy_local_names(self):
        call = {
            "id": "fc_custom",
            "type": "function_call",
            "call_id": "call_custom",
            "name": "custom_local",
            "arguments": "{}",
        }

        decision = completed_tool_call_decision(call, "native", frozenset({"custom_local"}))

        self.assertTrue(is_proxy_local_function_call(call, frozenset({"custom_local"})))
        self.assertEqual(decision.kind, "proxy_local")
        self.assertEqual(decision.call, call)

    def test_completed_tool_call_decision_adapts_public_apply_patch(self):
        call = {
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }

        decision = completed_tool_call_decision(call, "custom", frozenset())

        self.assertFalse(is_proxy_local_function_call(call, frozenset()))
        self.assertEqual(decision.kind, "public")
        self.assertEqual(decision.call, call)
        self.assertEqual(decision.public_item["type"], "custom_tool_call")
        self.assertEqual(decision.public_item["name"], "apply_patch")

    def test_tool_continuation_result_keeps_public_call_out_of_upstream_replay(self):
        call = {
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }

        decision = completed_tool_call_decision(call, "native", frozenset())
        result = tool_continuation_result(decision)

        self.assertEqual(result.public_item["type"], "apply_patch_call")
        self.assertEqual(result.upstream_items, ())
        self.assertEqual(result.sources, ())

    def test_tool_continuation_result_adds_private_call_and_output_for_proxy_local_tool(self):
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        public_item = {
            "id": "wsc_1",
            "type": "web_search_call",
            "status": "completed",
            "call_id": "call_web",
        }
        output_item = {
            "type": "function_call_output",
            "call_id": "call_web",
            "output": "{\"ok\":true}",
        }
        source = {"url": "https://example.test"}

        decision = completed_tool_call_decision(call, "native", frozenset({"web_search"}))
        result = tool_continuation_result(
            decision,
            proxy_local_executor=lambda tool_call: (public_item, output_item, [source]),
        )

        self.assertEqual(result.public_item, public_item)
        self.assertEqual(result.upstream_items, (call, output_item))
        self.assertEqual(result.sources, (source,))

    def test_tool_continuation_result_requires_executor_for_proxy_local_tool(self):
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        decision = completed_tool_call_decision(call, "native", frozenset({"web_search"}))

        with self.assertRaises(ValueError):
            tool_continuation_result(decision)


if __name__ == "__main__":
    unittest.main()
