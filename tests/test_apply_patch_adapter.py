import json
import unittest

from proxy.quantzhai_proxy import (
    _apply_patch_call_to_function_call,
    _apply_patch_output_style,
    _apply_patch_output_to_function_output,
    _custom_apply_patch_call_to_function_call,
    _custom_apply_patch_output_to_function_output,
    _parse_apply_patch_arguments,
    make_response_stream_events,
    normalize_apply_patch_output_for_codex,
    normalize_responses_input_for_qwen,
    normalize_tools_for_llamacpp,
)


class ApplyPatchAdapterTests(unittest.TestCase):
    def test_native_tool_declaration_becomes_function_tool(self):
        body = {
            "tools": [{"type": "apply_patch"}],
            "tool_choice": {"type": "apply_patch"},
        }

        out = normalize_tools_for_llamacpp(body)

        self.assertEqual(out["tools"][0]["type"], "function")
        self.assertEqual(out["tools"][0]["name"], "apply_patch")
        self.assertIn("operation", out["tools"][0]["parameters"]["properties"])
        self.assertEqual(out["tool_choice"], {"type": "function", "name": "apply_patch"})

    def test_apply_patch_call_history_becomes_function_call_history(self):
        item = {
            "id": "apc_1",
            "type": "apply_patch_call",
            "status": "completed",
            "call_id": "call_1",
            "operation": {
                "type": "update_file",
                "path": "README.md",
                "diff": "@@\n-old\n+new\n",
            },
        }

        out = _apply_patch_call_to_function_call(item)
        args = json.loads(out["arguments"])

        self.assertEqual(out["type"], "function_call")
        self.assertEqual(out["name"], "apply_patch")
        self.assertEqual(args["operation"]["path"], "README.md")

    def test_apply_patch_output_history_becomes_function_output_history(self):
        item = {
            "type": "apply_patch_call_output",
            "call_id": "call_1",
            "status": "completed",
            "output": "Updated README.md",
        }

        out = _apply_patch_output_to_function_output(item)
        payload = json.loads(out["output"])

        self.assertEqual(out["type"], "function_call_output")
        self.assertEqual(out["call_id"], "call_1")
        self.assertEqual(payload["status"], "completed")

    def test_model_function_call_becomes_native_apply_patch_call(self):
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "create_file",
                    "path": "notes.md",
                    "diff": "@@\n+hello\n",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "apply_patch_call")
        self.assertEqual(out["call_id"], "call_1")
        self.assertEqual(out["operation"]["type"], "create_file")
        self.assertEqual(out["operation"]["path"], "notes.md")

    def test_missing_apply_patch_tool_declaration_defaults_to_custom_output(self):
        self.assertEqual(_apply_patch_output_style({}), "custom")

        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "create_file",
                    "path": "notes.md",
                    "diff": "@@\n+hello\n",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call], "custom")[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertEqual(out["call_id"], "call_1")
        self.assertEqual(out["name"], "apply_patch")
        self.assertIn("*** Add File: notes.md", out["input"])

    def test_model_function_call_becomes_custom_apply_patch_call_when_requested(self):
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "create_file",
                    "path": "notes.md",
                    "diff": "@@\n+hello\n",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call], "custom")[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertEqual(out["call_id"], "call_1")
        self.assertEqual(out["name"], "apply_patch")
        self.assertIn("*** Add File: notes.md", out["input"])
        self.assertIn("+hello", out["input"])

    def test_invalid_patch_function_call_is_left_unchanged(self):
        function_call = {
            "type": "function_call",
            "name": "apply_patch",
            "arguments": json.dumps({"operation": {"type": "update_file", "diff": "@@"}}),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out, function_call)

    def test_normalize_responses_input_converts_patch_items(self):
        body = {
            "input": [
                {
                    "type": "apply_patch_call",
                    "call_id": "call_1",
                    "operation": {
                        "type": "delete_file",
                        "path": "old.txt",
                    },
                },
                {
                    "type": "apply_patch_call_output",
                    "call_id": "call_1",
                    "status": "completed",
                    "output": "Deleted old.txt",
                },
            ]
        }

        out = normalize_responses_input_for_qwen(body)

        self.assertEqual(out["input"][0]["type"], "function_call")
        self.assertEqual(out["input"][1]["type"], "function_call_output")

    def test_custom_patch_history_becomes_function_history(self):
        call_item = {
            "type": "custom_tool_call",
            "call_id": "call_1",
            "name": "apply_patch",
            "input": "*** Begin Patch\n*** Add File: notes.md\n+hello\n*** End Patch\n",
        }
        output_item = {
            "type": "custom_tool_call_output",
            "call_id": "call_1",
            "output": "{\"output\":\"Success\"}",
        }

        call_out = _custom_apply_patch_call_to_function_call(call_item)
        output_out = _custom_apply_patch_output_to_function_output(output_item)

        self.assertEqual(call_out["type"], "function_call")
        self.assertEqual(call_out["name"], "apply_patch")
        self.assertEqual(json.loads(call_out["arguments"])["patch"], call_item["input"])
        self.assertEqual(output_out["type"], "function_call_output")
        self.assertEqual(output_out["output"], output_item["output"])

    def test_legacy_patch_with_path_can_be_coerced(self):
        operation = _parse_apply_patch_arguments(json.dumps({
            "type": "update_file",
            "path": "README.md",
            "patch": "@@\n-old\n+new\n",
        }))

        self.assertEqual(operation["type"], "update_file")
        self.assertEqual(operation["path"], "README.md")

    def test_stream_synthesis_includes_apply_patch_call_item(self):
        out = {
            "id": "resp_1",
            "model": "QwenZhai-high",
            "output": [{
                "id": "apc_1",
                "type": "apply_patch_call",
                "status": "completed",
                "call_id": "call_1",
                "operation": {
                    "type": "delete_file",
                    "path": "old.txt",
                },
            }],
        }

        stream = b"".join(make_response_stream_events(out)).decode("utf-8")

        self.assertIn("response.output_item.added", stream)
        self.assertIn("response.output_item.done", stream)
        self.assertIn("apply_patch_call", stream)
        self.assertIn('"input_tokens": 0', stream)
        self.assertIn('"output_tokens": 0', stream)
        self.assertIn('"total_tokens": 0', stream)


if __name__ == "__main__":
    unittest.main()
