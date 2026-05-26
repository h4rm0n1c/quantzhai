import json
import unittest
from copy import deepcopy
from pathlib import Path

from proxy.quantzhai_proxy import (
    _custom_apply_patch_call_to_function_call,
    _custom_apply_patch_output_to_function_output,
    _parse_apply_patch_arguments,
    make_response_stream_events,
    normalize_apply_patch_output_for_codex,
    normalize_responses_input_for_qwen,
    normalize_tools_for_llamacpp,
)
from proxy.qz_tool_apply_patch import (
    APPLY_PATCH_TOOL_ADAPTER,
    ensure_apply_patch_tool_policy,
    inspect_apply_patch_arguments,
    _apply_patch_operation_to_patch_text,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "responses_input"


class ApplyPatchAdapterTests(unittest.TestCase):
    def test_native_tool_declaration_becomes_function_tool(self):
        """Codex declares apply_patch as {"type":"apply_patch"}.  Proxy accepts it and
        records apply_patch_client_tool_type=apply_patch.  Output is always custom_tool_call."""
        body = {
            "tools": [{"type": "apply_patch"}],
            "tool_choice": {"type": "apply_patch"},
        }

        out = normalize_tools_for_llamacpp(body)

        self.assertEqual(out["tools"][0]["type"], "function")
        self.assertEqual(out["tools"][0]["name"], "apply_patch")
        self.assertIn("operation", out["tools"][0]["parameters"]["properties"])
        operation_schema = out["tools"][0]["parameters"]["properties"]["operation"]
        self.assertIn("destination", operation_schema["properties"])
        self.assertNotIn("diff", operation_schema["required"])
        self.assertEqual(out["tool_choice"], {"type": "function", "name": "apply_patch"})
        self.assertEqual(out["metadata"]["qz_tool_policy"]["schema"], "qz.tool_policy.v1")
        self.assertTrue(out["metadata"]["qz_tool_policy"]["apply_patch_declared"])
        self.assertEqual(out["metadata"]["qz_tool_policy"]["apply_patch_client_tool_type"], "apply_patch")
        # apply_patch_output_style has been removed — custom_tool_call is the only output format
        self.assertNotIn("apply_patch_output_style", out["metadata"]["qz_tool_policy"])

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
        self.assertNotIn("apply_patch_output_style", policy)

    def test_tool_policy_survives_second_normalization_pass(self):
        body = {
            "tools": [{"type": "custom", "name": "apply_patch"}],
            "metadata": {},
        }

        first = normalize_tools_for_llamacpp(body)
        second = normalize_tools_for_llamacpp(first)

        self.assertEqual(second["tools"][0]["type"], "function")
        self.assertEqual(second["metadata"]["qz_tool_policy"]["apply_patch_client_tool_type"], "custom")
        self.assertNotIn("apply_patch_output_style", second["metadata"]["qz_tool_policy"])

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
        self.assertNotIn("apply_patch_output_style", policy)

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

    def test_model_function_call_becomes_custom_tool_call(self):
        """apply_patch function_call from upstream always produces custom_tool_call output."""
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

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertEqual(out["call_id"], "call_1")
        self.assertEqual(out["name"], "apply_patch")
        self.assertIn("*** Add File: notes.md", out["input"])

    def test_apply_patch_output_never_produces_apply_patch_call_type(self):
        """normalize_apply_patch_output_for_codex must never produce apply_patch_call items."""
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {"type": "delete_file", "path": "old.txt"},
            }),
        }
        out = normalize_apply_patch_output_for_codex([function_call])
        for item in out:
            self.assertNotEqual(item.get("type"), "apply_patch_call",
                                "apply_patch_call is a removed contract; custom_tool_call must be used")

    def test_model_function_call_move_becomes_custom_envelope(self):
        """move_file function_call always produces a custom_tool_call with *** Move to: envelope."""
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "move_file",
                    "path": "old.md",
                    "destination": "new.md",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("*** Update File: old.md", out["input"])
        self.assertIn("*** Move to: new.md", out["input"])

    def test_rename_operation_alias_becomes_custom_move_patch(self):
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "rename_file",
                    "path": "old.md",
                    "new_path": "new.md",
                    "diff": "@@\n unchanged context\n",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("*** Update File: old.md", out["input"])
        self.assertIn("*** Move to: new.md", out["input"])
        self.assertIn(" unchanged context", out["input"])

    def test_custom_move_without_diff_emits_partial_envelope(self):
        """Move without a content hunk now emits a partial Codex envelope so
        Codex's verifier produces a specific error the model can act on,
        instead of routing to the broken assistant-message path."""
        function_call = {
            "id": "fc_1",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "apply_patch",
            "arguments": json.dumps({
                "operation": {
                    "type": "move_file",
                    "path": "old.md",
                    "destination": "new.md",
                }
            }),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("*** Update File: old.md", out["input"])
        self.assertIn("*** Move to: new.md", out["input"])
        self.assertIn("*** End Patch", out["input"])

    def test_move_operation_requires_destination(self):
        operation = _parse_apply_patch_arguments(json.dumps({
            "operation": {
                "type": "move_file",
                "path": "old.md",
                "diff": "new.md",
            }
        }))

        self.assertIsNone(operation)

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

    def test_update_patch_strips_unified_diff_file_headers(self):
        """update_file always produces a custom_tool_call envelope with stripped unified diff headers."""
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

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("@@\n-alpha\n+ALPHA", out["input"])
        self.assertNotIn("@@ -1,5 +1,6 @@", out["input"])
        self.assertNotIn("--- a/patch_target.txt", out["input"])
        self.assertNotIn("+++ b/patch_target.txt", out["input"])

    def test_invalid_patch_function_call_with_no_path_falls_back_to_message(self):
        """When the args have no salvageable path, no envelope can be built —
        the legacy assistant-message path is the last resort. The reason is
        included so the user sees what was missing even though the model
        cannot consume it directly."""
        function_call = {
            "type": "function_call",
            "name": "apply_patch",
            "arguments": json.dumps({"operation": {"type": "update_file", "diff": "@@"}}),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "message")
        self.assertEqual(out["role"], "assistant")
        self.assertIn("invalid patch arguments", out["content"][0]["text"])
        self.assertIn("'path'", out["content"][0]["text"])

    def test_qwen_bare_create_file_emits_partial_envelope(self):
        """The Qwen Shape B failure (create_file with no diff) used to dead-end
        in an assistant message. Now the proxy emits a *** Add File: <path>
        envelope so Codex's verifier surfaces a specific error the model
        can act on next turn."""
        function_call = {
            "id": "fc_qwen_bare_create",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_qwen_bare_create",
            "name": "apply_patch",
            "arguments": json.dumps({"operation": {"type": "create_file", "path": "hello.py"}}),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("*** Add File: hello.py", out["input"])
        self.assertIn("*** End Patch", out["input"])

    def test_qwen_bare_update_file_emits_partial_envelope(self):
        function_call = {
            "id": "fc_qwen_bare_update",
            "type": "function_call",
            "status": "completed",
            "call_id": "call_qwen_bare_update",
            "name": "apply_patch",
            "arguments": json.dumps({"operation": {"type": "update_file", "path": "greeting.py"}}),
        }

        out = normalize_apply_patch_output_for_codex([function_call])[0]

        self.assertEqual(out["type"], "custom_tool_call")
        self.assertIn("*** Update File: greeting.py", out["input"])

    def test_qwen_legacy_patch_envelope_with_no_top_level_path_is_coerced(self):
        """Qwen-observed shape: full *** Begin Patch envelope as top-level
        'patch' string with no separate 'path'. Path is extracted from the
        envelope's *** Update File: header."""
        operation = _parse_apply_patch_arguments(json.dumps({
            "patch": (
                "*** Begin Patch\n"
                "*** Update File: quote.py\n"
                "@@\n"
                "-MESSAGE = 'plain'\n"
                "+MESSAGE = 'escaped'\n"
                "*** End Patch\n"
            ),
        }))

        self.assertIsNotNone(operation)
        self.assertEqual(operation["type"], "update_file")
        self.assertEqual(operation["path"], "quote.py")
        self.assertIn("MESSAGE = 'escaped'", operation["diff"])

    def test_qwen_legacy_patch_add_file_envelope_extracts_create_type(self):
        operation = _parse_apply_patch_arguments(json.dumps({
            "patch": (
                "*** Begin Patch\n"
                "*** Add File: new.py\n"
                "+x = 1\n"
                "*** End Patch\n"
            ),
        }))

        self.assertIsNotNone(operation)
        self.assertEqual(operation["type"], "create_file")
        self.assertEqual(operation["path"], "new.py")

    def test_normalize_responses_input_converts_custom_tool_call_items(self):
        """custom_tool_call apply_patch history items are converted to function_call for upstream."""
        body = {
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_1",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch\n",
                },
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call_1",
                    "output": "{\"ok\": true}",
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

    def test_golden_native_codex_first_request_shape_is_normalized(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("native_codex_first_request_shape.json").read_text())

        input_out = normalize_responses_input_for_qwen(deepcopy(fixture["body"]))
        tools_out = normalize_tools_for_llamacpp(deepcopy(fixture["body"]))

        self.assertEqual(input_out["input"], fixture["expected_input"])
        self.assertIn("You are Codex, powered by Qwen3.6", input_out["instructions"])
        self.assertNotIn("NATIVE CODEX TOP LEVEL INSTRUCTIONS", input_out["instructions"])
        self.assertNotIn("<permissions instructions>", input_out["instructions"])
        self.assertNotIn("<environment_context>", json.dumps(input_out["input"]))
        self.assertEqual(input_out["metadata"]["qz_prompt_policy"]["mode"], "replace_client")
        self.assertTrue(input_out["metadata"]["qz_prompt_policy"]["replaced_client"])

        self.assertEqual([tool.get("name") for tool in tools_out["tools"]], fixture["expected_tool_names"])
        self.assertEqual(set(tool.get("type") for tool in tools_out["tools"]), {"function"})
        self.assertEqual(tools_out["tool_choice"], "auto")
        self.assertEqual(tools_out["metadata"]["qz_tool_policy"], fixture["expected_policy"])
        self.assertIn("use apply_patch", tools_out["tools"][0]["description"])

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

    def test_qwen_create_file_sibling_patch_is_coerced(self):
        """Qwen-observed shape: operation lacks diff, sibling 'patch' carries the file content."""
        operation = _parse_apply_patch_arguments(json.dumps({
            "operation": {
                "type": "create_file",
                "path": "hello.py",
            },
            "patch": "def greet():\n    return 'hello'\n",
        }))

        self.assertIsNotNone(operation)
        self.assertEqual(operation["type"], "create_file")
        self.assertEqual(operation["path"], "hello.py")
        self.assertEqual(operation["diff"], "def greet():\n    return 'hello'\n")

    def test_qwen_update_file_sibling_patch_with_unified_headers_is_coerced(self):
        """Qwen-observed shape: update_file operation with unified-diff sibling patch."""
        operation = _parse_apply_patch_arguments(json.dumps({
            "operation": {
                "type": "update_file",
                "path": "config/app.json",
            },
            "patch": (
                "--- a/config/app.json\n"
                "+++ b/config/app.json\n"
                "@@ -1,4 +1,5 @@\n"
                " {\n"
                "+  \"debug\": false,\n"
                "   \"name\": \"demo\",\n"
                "   \"port\": 8080\n"
                " }\n"
            ),
        }))

        self.assertIsNotNone(operation)
        self.assertEqual(operation["type"], "update_file")
        self.assertEqual(operation["path"], "config/app.json")
        self.assertIn("\"debug\": false", operation["diff"])

    def test_qwen_sibling_patch_does_not_overwrite_explicit_diff(self):
        """If the operation already has a diff, the sibling patch must not clobber it."""
        operation = _parse_apply_patch_arguments(json.dumps({
            "operation": {
                "type": "create_file",
                "path": "hello.py",
                "diff": "intentional diff\n",
            },
            "patch": "ignored sibling content\n",
        }))

        self.assertIsNotNone(operation)
        self.assertEqual(operation["diff"], "intentional diff\n")

    def test_stream_synthesis_includes_custom_tool_call_item(self):
        """make_response_stream_events emits custom_tool_call for apply_patch, never apply_patch_call."""
        out = {
            "id": "resp_1",
            "model": "test-model.gguf",
            "output": [{
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** Delete File: old.txt\n*** End Patch\n",
            }],
        }

        stream = b"".join(make_response_stream_events(out)).decode("utf-8")

        self.assertIn("response.output_item.added", stream)
        self.assertIn("response.output_item.done", stream)
        self.assertIn("custom_tool_call", stream)
        self.assertNotIn("apply_patch_call", stream)
        self.assertIn('"input_tokens": 0', stream)
        self.assertIn('"output_tokens": 0', stream)
        self.assertIn('"total_tokens": 0', stream)

    def test_ap1_non_streaming_path_emits_no_apply_patch_sub_lifecycle_events(self):
        """AP1 Test 3: make_response_stream_events must not emit any response.apply_patch_call.* events.

        Source: docs/apply-patch-codex-lifecycle-audit.md §5.4, §8 G-AP1.
        """
        out = {
            "id": "resp_ns_ap1",
            "model": "test-model.gguf",
            "output": [{
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": "call_ns_ap1",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** Update File: src/main.py\n@@\n-old\n+new\n*** End Patch\n",
            }],
        }
        stream = b"".join(make_response_stream_events(out)).decode("utf-8")

        # Required generic lifecycle events must be present
        self.assertIn("response.output_item.added", stream)
        self.assertIn("response.output_item.done", stream)

        # Forbidden sub-lifecycle events must not appear in the non-streaming synthetic path
        for forbidden in (
            "response.apply_patch_call.in_progress",
            "response.apply_patch_call.searching",
            "response.apply_patch_call.completed",
            "response.web_search_call.in_progress",
            "response.web_search_call.searching",
            "response.web_search_call.completed",
            "response.file_search_call.in_progress",
            "response.code_interpreter_call.in_progress",
            "response.function_call_arguments.delta",
            "response.function_call_arguments.done",
        ):
            self.assertNotIn(
                forbidden, stream,
                f"Non-streaming make_response_stream_events must not emit {forbidden!r} for apply_patch",
            )


if __name__ == "__main__":
    unittest.main()


class ApplyPatchCoerceTests(unittest.TestCase):
    """Tests for the new coerce() method on ApplyPatchToolAdapter."""

    def _call(self, arguments: str) -> dict:
        return {"type": "function_call", "name": "apply_patch",
                "call_id": "test_call", "arguments": arguments}

    def test_coerce_valid_operation_returns_corrected_arguments(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py", "diff": "x=1\n"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(result.succeeded())
        self.assertIsNone(result.error_message)
        parsed = json.loads(result.corrected_arguments)
        self.assertEqual(parsed["operation"]["type"], "create_file")

    def test_coerce_sibling_patch_returns_corrected_arguments(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py"}, "patch": "x=1\n"}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(result.succeeded())
        parsed = json.loads(result.corrected_arguments)
        self.assertEqual(parsed["operation"]["diff"], "x=1\n")

    def test_coerce_bare_operation_returns_error_message(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIsNotNone(result.error_message)
        self.assertIn("diff", result.error_message)

    def test_coerce_missing_destination_returns_error_message(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "move_file", "path": "a.py"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIn("destination", result.error_message)

    def test_coerce_bad_json_returns_error_message(self):
        call = self._call("{not valid json}")
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIsNotNone(result.error_message)


class ApplyPatchAP2CoercionSafetyTests(unittest.TestCase):
    """Slice AP2: Coercion error safety — call_id preserved, raw args not leaked.

    Source: docs/apply-patch-codex-lifecycle-audit.md §6.2, §8 (G-AP2).
    """

    def _call(self, arguments: str, call_id: str = "ap2_call_99") -> dict:
        return {"type": "function_call", "name": "apply_patch",
                "call_id": call_id, "id": call_id, "arguments": arguments}

    # --- Error message specificity ------------------------------------------

    def test_ap2_bad_json_error_message_is_specific(self):
        """Invalid JSON produces 'arguments are not valid JSON' (not generic 'could not coerce')."""
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(self._call("{bad json"))
        self.assertFalse(result.succeeded())
        self.assertIn("not valid JSON", result.error_message,
                      "bad-JSON coercion error must be specific")

    def test_ap2_non_object_json_error_message_is_specific(self):
        """Non-object JSON (e.g. array) produces 'not a JSON object'."""
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(self._call(json.dumps([1, 2, 3])))
        self.assertFalse(result.succeeded())
        self.assertIn("not a JSON object", result.error_message,
                      "non-object coercion error must be specific")

    def test_ap2_unknown_operation_type_error_message_is_specific(self):
        """Unknown operation type names the bad type in the error."""
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(
            self._call(json.dumps({"operation": {"type": "invent_file", "path": "x.py"}}))
        )
        self.assertFalse(result.succeeded())
        self.assertIn("unknown operation type", result.error_message)
        self.assertIn("invent_file", result.error_message)

    def test_ap2_missing_diff_error_message_is_specific(self):
        """Missing diff on create_file/update_file names the operation type and 'diff'."""
        for op_type in ("create_file", "update_file"):
            with self.subTest(op_type=op_type):
                result = APPLY_PATCH_TOOL_ADAPTER.coerce(
                    self._call(json.dumps({"operation": {"type": op_type, "path": "x.py"}}))
                )
                self.assertFalse(result.succeeded())
                self.assertIn("diff", result.error_message)
                self.assertIn(op_type, result.error_message)

    def test_ap2_missing_destination_error_message_is_specific(self):
        """Missing destination on move_file names 'destination' in the error."""
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(
            self._call(json.dumps({"operation": {"type": "move_file", "path": "a.py"}}))
        )
        self.assertFalse(result.succeeded())
        self.assertIn("destination", result.error_message)

    def test_ap2_missing_path_error_message_is_specific(self):
        """Missing path names 'path' in the error."""
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(
            self._call(json.dumps({"operation": {"type": "update_file"}}))
        )
        self.assertFalse(result.succeeded())
        self.assertIn("path", result.error_message)

    # --- Raw args not leaked in error messages ------------------------------

    def test_ap2_bad_json_error_does_not_echo_raw_args(self):
        """Bad-JSON error must not echo the raw argument string in the error message."""
        raw_args = '{"broken": true, NOTJSON secret_content_xyz'
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(self._call(raw_args))
        self.assertFalse(result.succeeded())
        self.assertNotIn("secret_content_xyz", result.error_message,
                         "raw argument content must not appear in coercion error message")
        self.assertNotIn("NOTJSON", result.error_message,
                         "raw argument content must not appear in coercion error message")

    def test_ap2_missing_diff_error_does_not_echo_diff_content(self):
        """Missing-diff error must not include diff content from sibling fields."""
        # Provide a patch with potentially sensitive content in a wrong field.
        raw_args = json.dumps({
            "operation": {"type": "create_file", "path": "x.py"},
            "extra_sensitive_field": "SECRET_PATCH_CONTENT_DO_NOT_LEAK",
        })
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(self._call(raw_args))
        # This should succeed via sibling patch logic... but extra_sensitive_field is not "patch".
        # If it fails, the error must not include the sensitive content.
        if not result.succeeded():
            self.assertNotIn("SECRET_PATCH_CONTENT_DO_NOT_LEAK", result.error_message,
                             "extra field values must not leak into error messages")

    # --- call_id preservation -----------------------------------------------

    def test_ap2_synthesize_tool_error_preserves_call_id(self):
        """synthesize_tool_error_result must preserve the original call_id."""
        from proxy.qz_tools import synthesize_tool_error_result
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py"}}),
                          call_id="test_preserved_id_999")
        error_item = synthesize_tool_error_result(call, "apply_patch: missing diff")
        self.assertEqual(error_item.get("call_id"), "test_preserved_id_999",
                         "function_call_output error must preserve the original call_id")

    def test_ap2_error_output_is_function_call_output_type(self):
        """synthesize_tool_error_result must produce a function_call_output item."""
        from proxy.qz_tools import synthesize_tool_error_result
        call = self._call("{bad json", call_id="call_type_check")
        error_item = synthesize_tool_error_result(call, "apply_patch: arguments are not valid JSON")
        self.assertEqual(error_item.get("type"), "function_call_output",
                         "coercion error must be injected as function_call_output")

    def test_ap2_error_output_contains_error_text(self):
        """synthesize_tool_error_result output field must contain the error message."""
        from proxy.qz_tools import synthesize_tool_error_result
        call = self._call("{bad json", call_id="call_msg_check")
        msg = "apply_patch: arguments are not valid JSON"
        error_item = synthesize_tool_error_result(call, msg)
        output_str = error_item.get("output", "")
        self.assertIn("not valid JSON", output_str,
                      "error message must appear in function_call_output.output field")

    # --- No raw args in function_call_output --------------------------------

    def test_ap2_error_output_does_not_echo_raw_arguments(self):
        """The function_call_output error must not include the raw malformed arguments string."""
        from proxy.qz_tools import synthesize_tool_error_result
        raw_args = '{"type":"secret_op","secret_data":"VERY_SENSITIVE_PATCH_CONTENT"}'
        call = {"type": "function_call", "name": "apply_patch",
                "call_id": "call_raw_check", "arguments": raw_args}
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded(), "unknown op type must not coerce")
        error_item = synthesize_tool_error_result(call, result.error_message)
        output_str = error_item.get("output", "")
        self.assertNotIn("VERY_SENSITIVE_PATCH_CONTENT", output_str,
                         "raw argument content must not appear in the injected error output")


# ---------------------------------------------------------------------------
# AP3: inspect_apply_patch_arguments — safe telemetry metadata
# ---------------------------------------------------------------------------

class ApplyPatchAP3InspectTests(unittest.TestCase):
    """Verify inspect_apply_patch_arguments returns only safe metadata fields.

    All returned values must be booleans or fixed enum strings.  No raw
    arguments, patch text, diff text, file path values, or destination path
    values may appear anywhere in the returned dict.
    """

    # Sentinel strings that must never appear in any inspect output.
    _FORBIDDEN_VALUES = [
        "VERY_SENSITIVE_PATCH_CONTENT",
        "/etc/secret_path",
        "BEGIN PATCH",
        "--- old.py",
        "+++ new.py",
        "DEST_SECRET_PATH",
    ]

    def _assert_no_raw_content(self, meta: dict, label: str = ""):
        """Assert that none of the forbidden raw-content sentinels appear in meta."""
        meta_str = json.dumps(meta)
        for sentinel in self._FORBIDDEN_VALUES:
            self.assertNotIn(
                sentinel, meta_str,
                f"raw content '{sentinel}' must not appear in inspect output{' (' + label + ')' if label else ''}"
            )

    def _assert_safe_types(self, meta: dict):
        """All leaf values must be bool or str (enum strings)."""
        for key, val in meta.items():
            self.assertIsInstance(
                val, (bool, str),
                f"field '{key}' must be bool or str, got {type(val).__name__}"
            )

    # --- Empty / invalid inputs -----------------------------------------------

    def test_ap3_empty_string_returns_empty_shape(self):
        """Empty arguments string → args_shape=empty, failed strategy."""
        meta = inspect_apply_patch_arguments("")
        self.assertEqual(meta["args_shape"], "empty")
        self.assertEqual(meta["coercion_strategy"], "failed_invalid_json")
        self.assertFalse(meta["operation_present"])
        self.assertFalse(meta["patch_present"])
        self._assert_safe_types(meta)

    def test_ap3_invalid_json_returns_invalid_json_shape(self):
        """Invalid JSON → args_shape=invalid_json, coercion_strategy=failed_invalid_json."""
        raw = '{"type": "update_file", "path": "/etc/secret_path", "diff": "VERY_SENSITIVE_PATCH_CONTENT"'  # truncated
        meta = inspect_apply_patch_arguments(raw)
        self.assertEqual(meta["args_shape"], "invalid_json")
        self.assertEqual(meta["coercion_strategy"], "failed_invalid_json")
        self._assert_no_raw_content(meta, "invalid JSON case")
        self._assert_safe_types(meta)

    def test_ap3_invalid_json_no_raw_args_present(self):
        """No raw argument content may appear in the metadata for invalid JSON."""
        raw = '{"patch": "VERY_SENSITIVE_PATCH_CONTENT", bad'
        meta = inspect_apply_patch_arguments(raw)
        meta_str = json.dumps(meta)
        self.assertNotIn("VERY_SENSITIVE_PATCH_CONTENT", meta_str,
                         "raw patch body must not appear in invalid JSON inspect output")

    # --- Missing diff ----------------------------------------------------------

    def test_ap3_missing_diff_operation_type_and_path_present(self):
        """update_file with no diff → operation_type=update_file, path_present=True, diff_present=False."""
        args = json.dumps({"operation": {"type": "update_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self.assertEqual(meta["operation_type"], "update_file")
        self.assertTrue(meta["path_present"])
        self.assertFalse(meta["diff_present"])
        self.assertEqual(meta["coercion_strategy"], "failed_missing_diff")

    def test_ap3_missing_diff_no_path_value_present(self):
        """No raw path value ('/etc/secret_path') may appear in the metadata."""
        args = json.dumps({"operation": {"type": "update_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self._assert_no_raw_content(meta, "missing diff case")

    def test_ap3_missing_diff_safe_types(self):
        args = json.dumps({"operation": {"type": "create_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self._assert_safe_types(meta)
        self.assertEqual(meta["args_shape"], "object")

    # --- Missing destination ---------------------------------------------------

    def test_ap3_missing_destination_strategy(self):
        """move_file with no destination → coercion_strategy=failed_missing_destination."""
        args = json.dumps({"operation": {"type": "move_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self.assertEqual(meta["operation_type"], "move_file")
        self.assertTrue(meta["path_present"])
        self.assertFalse(meta["destination_present"])
        self.assertEqual(meta["coercion_strategy"], "failed_missing_destination")

    def test_ap3_missing_destination_no_path_value_present(self):
        """No raw path value may appear in the metadata for missing destination case."""
        args = json.dumps({"operation": {"type": "move_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self._assert_no_raw_content(meta, "missing destination case")

    # --- Sibling patch promotion -----------------------------------------------

    def test_ap3_sibling_patch_promoted_fields(self):
        """operation present with sibling patch → operation_present=True, patch_present=True, diff_present=False."""
        args = json.dumps({
            "operation": {"type": "update_file", "path": "/etc/secret_path"},
            "patch": "VERY_SENSITIVE_PATCH_CONTENT",
        })
        meta = inspect_apply_patch_arguments(args)
        self.assertTrue(meta["operation_present"])
        self.assertTrue(meta["patch_present"])
        self.assertFalse(meta["diff_present"])
        self.assertEqual(meta["coercion_strategy"], "sibling_patch_promoted")

    def test_ap3_sibling_patch_promoted_no_patch_body_present(self):
        """Raw patch body must not appear in sibling_patch_promoted metadata."""
        args = json.dumps({
            "operation": {"type": "update_file", "path": "/etc/secret_path"},
            "patch": "VERY_SENSITIVE_PATCH_CONTENT",
        })
        meta = inspect_apply_patch_arguments(args)
        self._assert_no_raw_content(meta, "sibling patch promotion case")

    # --- Legacy Begin Patch envelope ------------------------------------------

    def test_ap3_legacy_patch_envelope_strategy(self):
        """Legacy Begin Patch envelope → patch_present=True, coercion_strategy=legacy_patch_envelope."""
        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: /etc/secret_path\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+BEGIN PATCH\n"  # sentinel in patch content
            "*** End Patch"
        )
        args = json.dumps({"patch": patch_text})
        meta = inspect_apply_patch_arguments(args)
        self.assertTrue(meta["patch_present"])
        self.assertEqual(meta["coercion_strategy"], "legacy_patch_envelope")

    def test_ap3_legacy_patch_envelope_no_patch_body_present(self):
        """Raw patch body (including sentinels inside it) must not appear in metadata."""
        patch_text = (
            "*** Begin Patch\n"
            "*** Update File: /etc/secret_path\n"
            "@@ -1,3 +1,3 @@\n"
            "-VERY_SENSITIVE_PATCH_CONTENT\n"
            "+new content\n"
            "*** End Patch"
        )
        args = json.dumps({"patch": patch_text})
        meta = inspect_apply_patch_arguments(args)
        self._assert_no_raw_content(meta, "legacy patch envelope case")

    # --- Successful operation_object path -------------------------------------

    def test_ap3_operation_object_strategy(self):
        """Well-formed operation object → coercion_strategy=operation_object."""
        args = json.dumps({
            "operation": {
                "type": "create_file",
                "path": "/etc/secret_path",
                "diff": "--- /dev/null\n+++ new.py\n+content\n",
            }
        })
        meta = inspect_apply_patch_arguments(args)
        self.assertEqual(meta["coercion_strategy"], "operation_object")
        self.assertTrue(meta["operation_present"])
        self.assertTrue(meta["path_present"])
        self.assertTrue(meta["diff_present"])
        self.assertEqual(meta["operation_type"], "create_file")

    def test_ap3_operation_object_no_raw_content(self):
        """No raw path or diff content may appear in metadata for successful coerce."""
        args = json.dumps({
            "operation": {
                "type": "update_file",
                "path": "/etc/secret_path",
                "diff": "--- old.py\n+++ new.py\n-VERY_SENSITIVE_PATCH_CONTENT\n+new\n",
            }
        })
        meta = inspect_apply_patch_arguments(args)
        self._assert_no_raw_content(meta, "operation_object success case")

    # --- Unknown operation type -----------------------------------------------

    def test_ap3_unknown_operation_type_enum(self):
        """Non-standard operation type → operation_type=unknown (not the raw string)."""
        raw_type = "VERY_SENSITIVE_PATCH_CONTENT"  # reuse sentinel as type
        args = json.dumps({"operation": {"type": raw_type, "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self.assertEqual(meta["operation_type"], "unknown",
                         "operation_type must be the enum string 'unknown', not the raw value")
        self.assertNotIn(raw_type, json.dumps(meta),
                         "raw operation type string must not appear in metadata")

    # --- delete_file (no diff / destination required) -------------------------

    def test_ap3_delete_file_coerces_and_safe(self):
        """delete_file only needs path → operation_object strategy, safe output."""
        args = json.dumps({"operation": {"type": "delete_file", "path": "/etc/secret_path"}})
        meta = inspect_apply_patch_arguments(args)
        self.assertEqual(meta["operation_type"], "delete_file")
        self.assertTrue(meta["path_present"])
        self.assertFalse(meta["diff_present"])
        self.assertFalse(meta["destination_present"])
        self.assertEqual(meta["coercion_strategy"], "operation_object")
        self._assert_no_raw_content(meta, "delete_file case")
        self._assert_safe_types(meta)

class ApplyPatchCoerceTests(unittest.TestCase):
    """Tests for the new coerce() method on ApplyPatchToolAdapter."""

    def _call(self, arguments: str) -> dict:
        return {"type": "function_call", "name": "apply_patch",
                "call_id": "test_call", "arguments": arguments}

    def test_coerce_valid_operation_returns_corrected_arguments(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py", "diff": "x=1\n"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(result.succeeded())
        self.assertIsNone(result.error_message)
        parsed = json.loads(result.corrected_arguments)
        self.assertEqual(parsed["operation"]["type"], "create_file")

    def test_coerce_sibling_patch_returns_corrected_arguments(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py"}, "patch": "x=1\n"}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertTrue(result.succeeded())
        parsed = json.loads(result.corrected_arguments)
        self.assertEqual(parsed["operation"]["diff"], "x=1\n")

    def test_coerce_bare_operation_returns_error_message(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "create_file", "path": "x.py"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIsNotNone(result.error_message)
        self.assertIn("diff", result.error_message)

    def test_coerce_missing_destination_returns_error_message(self):
        import json
        call = self._call(json.dumps({"operation": {"type": "move_file", "path": "a.py"}}))
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIn("destination", result.error_message)

    def test_coerce_bad_json_returns_error_message(self):
        call = self._call("{not valid json}")
        result = APPLY_PATCH_TOOL_ADAPTER.coerce(call)
        self.assertFalse(result.succeeded())
        self.assertIsNotNone(result.error_message)
