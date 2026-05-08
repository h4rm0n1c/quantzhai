import unittest

from proxy.qz_tool_lifecycle import (
    StreamToolCallState,
    function_call_key,
    public_tool_item_from_function_call,
)


class ToolLifecycleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
