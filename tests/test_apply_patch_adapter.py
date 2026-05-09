import json
import unittest
from pathlib import Path

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
from proxy.qz_tool_apply_patch import ensure_apply_patch_tool_policy
from proxy.qz_tool_apply_patch import _apply_patch_operation_to_patch_text

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "responses_input"


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
        self.assertIn("diff", out["tools"][0]["parameters"]["properties"]["operation"]["required"])
        self.assertEqual(out["tool_choice"], {"type": "function", "name": "apply_patch"})
        self.assertEqual(out["metadata"]["qz_tool_policy"]["schema"], "qz.tool_policy.v1")
        self.assertTrue(out["metadata"]["qz_tool_policy"]["apply_patch_declared"])
        self.assertEqual(out["metadata"]["qz_tool_policy"]["apply_patch_client_tool_type"], "apply_patch")
        self.assertEqual(out["metadata"]["qz_tool_policy"]["apply_patch_output_style"], "native")

    def test_custom_tool_declaration_records_client_tool_shape(self):
        body = {
            "tools": [{"type": "custom", "name": "apply_patch"}],
            "tool_choice": {"type": "custom", "name": "apply_patch"},
        }

        out = normalize_tools_for_llamacpp(body)

        self.assertEqual(out["tools"][0]["type"], "function")
        self.assertEqual(out["tools"][0]["name"], "apply_patch")
        self.assertEqual(out["tool_choice"], {"type": "function", "name": "apply_patch"})
        policy = out["metadata"]["qz_tool_policy"]
        self.assertTrue(policy["apply_patch_declared"])
        self.assertEqual(policy["apply_patch_client_tool_type"], "custom")
        self.assertEqual(policy["apply_patch_output_style"], "custom")

    def test_tool_policy_survives_second_normalization_pass(self):
        body = {
            "tools": [{"type": "custom", "name": "apply_patch"}],
            "metadata": {},
        }

        first = normalize_tools_for_llamacpp(body)
        second = normalize_tools_for_llamacpp(first)

        self.assertEqual(second["tools"][0]["type"], "function")
        self.assertEqual(second["metadata"]["qz_tool_policy"]["apply_patch_client_tool_type"], "custom")
        self.assertEqual(second["metadata"]["qz_tool_policy"]["apply_patch_output_style"], "custom")

    def test_router_can_overwrite_stale_client_tool_policy(self):
        body = {
            "tools": [{"type": "apply_patch"}],
            "metadata": {
                "qz_tool_policy": {
                    "schema": "qz.tool_policy.v1",
                    "apply_patch_declared": True,
                    "apply_patch_client_tool_type": "custom",
                    "apply_patch_output_style": "custom",
                }
            },
        }

        policy = ensure_apply_patch_tool_policy(body, overwrite=True)

        self.assertEqual(policy["apply_patch_client_tool_type"], "apply_patch")
        self.assertEqual(policy["apply_patch_output_style"], "native")

    def test_write_stdin_is_dropped_without_live_exec_session(self):
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "exec_command", "description": "Runs shell commands."},
                {"type": "function", "name": "write_stdin", "description": "Writes stdin."},
                {"type": "custom", "name": "apply_patch"},
            ],
        }

        out = normalize_tools_for_llamacpp(body)

        names = [tool.get("name") for tool in out["tools"]]
        self.assertEqual(names, ["exec_command", "apply_patch"])
        self.assertIn("use apply_patch", out["tools"][0]["description"])

    def test_write_stdin_is_kept_with_live_exec_session(self):
        body = {
            "input": [{
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "Session ID: 81\nPTY still running.",
            }],
            "tools": [
                {"type": "function", "name": "write_stdin", "description": "Writes stdin."},
            ],
        }

        out = normalize_tools_for_llamacpp(body)

        self.assertEqual(out["tools"][0]["name"], "write_stdin")
        self.assertIn("Do not invent session ids", out["tools"][0]["description"])

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

    def test_custom_update_patch_strips_unified_diff_file_headers(self):
        patch = _apply_patch_operation_to_patch_text({
            "type": "update_file",
            "path": "patch_target.txt",
            "diff": (
                "--- a/patch_target.txt\n"
                "+++ b/patch_target.txt\n"
                "@@ -1,5 +1,6 @@\n"
                "-alpha\n"
                "+ALPHA\n"
                " beta\n"
                "-gamma\n"
                "+GAMMA\n"
                " delta\n"
                " epsilon\n"
                "+zeta\n"
            ),
        })

        self.assertIn("*** Update File: patch_target.txt", patch)
        self.assertIn("@@\n-alpha\n+ALPHA", patch)
        self.assertNotIn("@@ -1,5 +1,6 @@", patch)
        self.assertNotIn("--- a/patch_target.txt", patch)
        self.assertNotIn("+++ b/patch_target.txt", patch)

    def test_native_update_patch_strips_unified_diff_file_headers(self):
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "update_file",
                    "path": "patch_target.txt",
                    "diff": (
                        "--- a/patch_target.txt\n"
                        "+++ b/patch_target.txt\n"
                        "@@ -1,5 +1,6 @@\n"
                        "-alpha\n"
                        "+ALPHA\n"
                    ),
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call], "native")[0]

        self.assertEqual(out["type"], "apply_patch_call")
        self.assertIn("@@\n-alpha\n+ALPHA", out["operation"]["diff"])
        self.assertNotIn("@@ -1,5 +1,6 @@", out["operation"]["diff"])
        self.assertNotIn("--- a/patch_target.txt", out["operation"]["diff"])
        self.assertNotIn("+++ b/patch_target.txt", out["operation"]["diff"])

    def test_invalid_patch_function_call_becomes_message_not_private_call(self):
        function_call = {
            "type": "function_call",
            "name": "apply_patch",
            "arguments": json.dumps({"operation": {"type": "update_file", "diff": "@@"}}),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "message")
        self.assertEqual(out["role"], "assistant")
        self.assertIn("invalid patch arguments", out["content"][0]["text"])

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

    def test_normalize_responses_input_drops_empty_tool_call_parse_error_pair(self):
        body = {
            "input": [
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call_bad",
                    "arguments": "",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_bad",
                    "output": "failed to parse function arguments: EOF while parsing a value at line 1 column 0",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ]
        }

        out = normalize_responses_input_for_qwen(body)

        self.assertEqual(len(out["input"]), 1)
        self.assertEqual(out["input"][0]["type"], "message")
        self.assertEqual(out["input"][0]["role"], "user")

    def test_golden_malformed_empty_tool_history_is_filtered(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("malformed_empty_tool_history.json").read_text())

        out = normalize_responses_input_for_qwen(fixture["body"])

        self.assertEqual(out["input"], fixture["expected_input"])
        self.assertIn("qz_prompt_policy", out["metadata"])

    def test_golden_mixed_history_is_normalized_for_qwen(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("mixed_history_normalization.json").read_text())

        out = normalize_responses_input_for_qwen(fixture["body"])

        self.assertEqual(out["input"], fixture["expected_input"])
        self.assertIn("qz_prompt_policy", out["metadata"])
        self.assertTrue(out["metadata"]["qz_prompt_policy"]["mode"])

    def test_golden_tool_declarations_are_normalized_for_llamacpp(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("tool_declaration_normalization.json").read_text())

        out = normalize_tools_for_llamacpp(fixture["body"])

        self.assertEqual([tool.get("name") for tool in out["tools"]], fixture["expected_tool_names"])
        self.assertEqual([tool.get("type") for tool in out["tools"]], ["function", "function", "function"])
        self.assertEqual(out["tool_choice"], fixture["expected_tool_choice"])
        self.assertEqual(out["metadata"]["qz_tool_policy"], fixture["expected_policy"])
        self.assertIn("use apply_patch", out["tools"][0]["description"])

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
            "model": "test-model.gguf",
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
