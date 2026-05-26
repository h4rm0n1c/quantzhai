import json
import os
import tempfile
import unittest
from pathlib import Path

from proxy.qz_tool_request import normalize_tool_request_for_llamacpp, normalize_tools_for_llamacpp


class ToolRequestNormalizationTests(unittest.TestCase):
    def test_normalizer_reports_translated_dropped_and_tool_choice(self):
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "exec_command", "description": "Run command."},
                {"type": "function", "name": "write_stdin", "description": "Write stdin."},
                {"type": "web_search"},
                {"type": "apply_patch"},
                {"type": "computer_use_preview"},
            ],
            "tool_choice": {"type": "apply_patch"},
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual([tool.get("name") for tool in body["tools"]], [
            "exec_command",
            "web_search",
            "apply_patch",
        ])
        self.assertEqual(report.translated, ("web_search", "apply_patch"))
        self.assertEqual(report.dropped, ("write_stdin(no live exec session)", "computer_use_preview"))
        self.assertFalse(report.has_exec_session)
        self.assertTrue(report.tool_choice_normalized)
        self.assertFalse(report.tool_choice_forced_auto)
        self.assertEqual(body["tool_choice"], {"type": "function", "name": "apply_patch"})
        self.assertNotIn("apply_patch_output_style", body["metadata"]["qz_tool_policy"])
        self.assertIn("use apply_patch", body["tools"][0]["description"])

    def test_write_stdin_kept_when_history_contains_live_session(self):
        body = {
            "input": [{
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "session_id=42\nstill running",
            }],
            "tools": [
                {"type": "function", "name": "write_stdin", "description": "Write stdin."},
            ],
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual([tool.get("name") for tool in body["tools"]], ["write_stdin"])
        self.assertEqual(report.dropped, ())
        self.assertTrue(report.has_exec_session)
        self.assertIn("Do not invent session ids", body["tools"][0]["description"])

    def test_unknown_structured_tool_choice_is_forced_to_auto(self):
        body = {
            "tools": [{"type": "function", "name": "exec_command"}],
            "tool_choice": {"type": "computer_use_preview"},
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(body["tool_choice"], "auto")
        self.assertFalse(report.tool_choice_normalized)
        self.assertTrue(report.tool_choice_forced_auto)

    def test_compatibility_wrapper_returns_mutated_body(self):
        body = {"tools": [{"type": "web_search"}]}

        out = normalize_tools_for_llamacpp(body)

        self.assertIs(out, body)
        self.assertEqual(out["tools"][0]["type"], "function")
        self.assertEqual(out["tools"][0]["name"], "web_search")

    def test_capture_notes_and_forwarded_body_are_written_when_enabled(self):
        old_capture_mode = os.environ.get("QZ_CAPTURE_MODE")
        old_var_dir = os.environ.get("QZ_VAR_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.environ["QZ_CAPTURE_MODE"] = "latest"
                os.environ["QZ_VAR_DIR"] = tmp
                body = {
                    "input": [],
                    "metadata": {"qz_request_id": "req/tool:1"},
                    "tools": [
                        {"type": "function", "name": "write_stdin"},
                        {"type": "web_search"},
                    ],
                }

                report = normalize_tool_request_for_llamacpp(body)

                capture_dir = Path(tmp) / "captures"
                notes = capture_dir.joinpath("latest-dropped-tools.txt").read_text(encoding="utf-8")
                forwarded = json.loads(capture_dir.joinpath("latest-forwarded.json").read_text(encoding="utf-8"))
                request_dir = capture_dir / "requests" / "req_tool_1"
                self.assertEqual(notes, report.capture_notes())
                self.assertIn("translated: web_search", notes)
                self.assertIn("dropped: write_stdin(no live exec session)", notes)
                self.assertEqual(forwarded["tools"][0]["name"], "web_search")
                self.assertEqual(request_dir.joinpath("dropped-tools.txt").read_text(encoding="utf-8"), notes)
                self.assertEqual(
                    json.loads(request_dir.joinpath("forwarded-request-after-tools.json").read_text(encoding="utf-8")),
                    forwarded,
                )
            finally:
                if old_capture_mode is None:
                    os.environ.pop("QZ_CAPTURE_MODE", None)
                else:
                    os.environ["QZ_CAPTURE_MODE"] = old_capture_mode
                if old_var_dir is None:
                    os.environ.pop("QZ_VAR_DIR", None)
                else:
                    os.environ["QZ_VAR_DIR"] = old_var_dir


class ToolDedupAndReplacementTests(unittest.TestCase):
    def test_function_typed_web_search_is_replaced_by_proxy_schema(self):
        """Codex sends type=function name=web_search — proxy must replace with its schema."""
        codex_tool = {
            "type": "function",
            "name": "web_search",
            "description": "Stale Codex default web search description.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
        body = {"tools": [codex_tool]}

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(len(body["tools"]), 1)
        tool = body["tools"][0]
        self.assertEqual(tool["name"], "web_search")
        self.assertIn("capabilities", tool["description"])
        self.assertNotIn("Stale Codex default", tool["description"])
        self.assertEqual(report.replaced, ("web_search",))
        self.assertEqual(report.translated, ())

    def test_function_typed_web_search_deduped_when_structured_also_present(self):
        """If both type=web_search and type=function name=web_search present, only one upstream tool."""
        body = {
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "web_search", "description": "Stale."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        names = [t.get("name") for t in body["tools"]]
        self.assertEqual(names.count("web_search"), 1)
        # First occurrence (type=web_search) was translated; second was dropped as duplicate
        self.assertIn("web_search", report.translated)

    def test_duplicate_function_tool_names_deduped(self):
        """Two function tools with the same name collapse to one."""
        body = {
            "tools": [
                {"type": "function", "name": "exec_command", "description": "First."},
                {"type": "function", "name": "exec_command", "description": "Second."},
            ]
        }

        normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(len(body["tools"]), 1)

    def test_replaced_appears_in_capture_notes(self):
        body = {"tools": [{"type": "function", "name": "web_search", "description": "Old."}]}

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        notes = report.capture_notes()
        self.assertIn("replaced: web_search", notes)

    def test_non_proxy_function_tool_passes_through_unchanged(self):
        custom = {"type": "function", "name": "my_custom_tool", "description": "Custom."}
        body = {"tools": [custom]}

        normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(body["tools"], [custom])

    def test_structured_then_function_typed_web_search_populates_deduped(self):
        """type=web_search translated first; subsequent type=function name=web_search is deduped."""
        body = {
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "web_search", "description": "Stale."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual([t.get("name") for t in body["tools"]].count("web_search"), 1)
        self.assertIn("web_search", report.translated)
        self.assertIn("web_search", report.deduped)
        self.assertEqual(report.replaced, ())

    def test_function_typed_then_structured_web_search_populates_deduped(self):
        """type=function name=web_search replaced first; subsequent type=web_search is deduped."""
        body = {
            "tools": [
                {"type": "function", "name": "web_search", "description": "Stale."},
                {"type": "web_search"},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual([t.get("name") for t in body["tools"]].count("web_search"), 1)
        self.assertIn("web_search", report.replaced)
        self.assertIn("web_search", report.deduped)
        self.assertEqual(report.translated, ())

    def test_duplicate_exec_command_populates_deduped_not_dropped(self):
        """Duplicate exec_command goes to deduped, not to dropped or qz_dropped_tool_names."""
        body = {
            "tools": [
                {"type": "function", "name": "exec_command", "description": "First."},
                {"type": "function", "name": "exec_command", "description": "Second."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(len(body["tools"]), 1)
        self.assertIn("exec_command", report.deduped)
        self.assertNotIn("exec_command", report.dropped)
        # exec_command still present — must NOT appear in dropped_tool_names
        dropped_names = (body.get("metadata") or {}).get("qz_dropped_tool_names", [])
        self.assertNotIn("exec_command", dropped_names)

    def test_deduped_tools_not_in_qz_dropped_tool_names(self):
        """Deduped proxy-managed tools (web_search) must not land in qz_dropped_tool_names.

        The first occurrence is still in the tool list, so the model can call it.
        If the deduped name appeared in dropped_tool_names, a model call would
        incorrectly receive a 'not available' error injection.
        """
        body = {
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "web_search", "description": "Stale."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertIn("web_search", report.deduped)
        dropped_names = (body.get("metadata") or {}).get("qz_dropped_tool_names", [])
        self.assertNotIn("web_search", dropped_names)

    def test_no_change_normalization_produces_empty_deduped_and_dropped(self):
        """A single custom function tool that passes through produces an empty report."""
        body = {
            "tools": [
                {"type": "function", "name": "my_custom_tool", "description": "Fine."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        self.assertEqual(report.translated, ())
        self.assertEqual(report.replaced, ())
        self.assertEqual(report.deduped, ())
        self.assertEqual(report.dropped, ())

    def test_deduped_appears_in_capture_notes(self):
        """Deduplication is recorded in capture notes for operator visibility."""
        body = {
            "tools": [
                {"type": "web_search"},
                {"type": "function", "name": "web_search", "description": "Stale."},
            ]
        }

        report = normalize_tool_request_for_llamacpp(body, write_captures=False)

        notes = report.capture_notes()
        self.assertIn("deduped: web_search", notes)


class PermissionToolHintTests(unittest.TestCase):
    """Tests for REQUEST_PERMISSIONS_TOOL_HINT appended to request_permissions description."""

    def test_request_permissions_receives_permission_hint(self):
        """request_permissions description must include the permission affordance hint."""
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "request_permissions", "description": "Request extra permissions for this turn."},
            ],
        }
        normalize_tool_request_for_llamacpp(body, write_captures=False)
        desc = body["tools"][0]["description"]
        self.assertIn("request broader permissions", desc,
                      "request_permissions description should include permission affordance hint")

    def test_request_permissions_hint_not_added_to_other_tools(self):
        """Other tools must not receive the request_permissions hint."""
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "exec_command", "description": "Run a command."},
            ],
        }
        normalize_tool_request_for_llamacpp(body, write_captures=False)
        self.assertNotIn("request broader permissions", body["tools"][0]["description"])

    def test_request_permissions_deduped(self):
        """Duplicate request_permissions must be deduped, not passed through twice."""
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "request_permissions", "description": "First."},
                {"type": "function", "name": "request_permissions", "description": "Second."},
            ],
        }
        report = normalize_tool_request_for_llamacpp(body, write_captures=False)
        self.assertEqual(len(body["tools"]), 1)
        self.assertIn("request_permissions", report.deduped)
        # The surviving tool should have the hint
        self.assertIn("request broader permissions", body["tools"][0]["description"])

    def test_request_permissions_survives_normalization(self):
        """request_permissions must remain in the tool list after normalization."""
        body = {
            "input": [],
            "tools": [
                {"type": "function", "name": "request_permissions", "description": "Original."},
            ],
        }
        normalize_tool_request_for_llamacpp(body, write_captures=False)
        names = [t.get("name") for t in body["tools"]]
        self.assertIn("request_permissions", names)


if __name__ == "__main__":
    unittest.main()
