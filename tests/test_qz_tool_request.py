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
        self.assertEqual(body["metadata"]["qz_tool_policy"]["apply_patch_output_style"], "native")
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


if __name__ == "__main__":
    unittest.main()
