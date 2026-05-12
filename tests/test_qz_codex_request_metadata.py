import unittest
import json
from proxy.qz_codex_metadata import extract_codex_body_metadata, CodexRequestMetadata

class CodexRequestMetadataTests(unittest.TestCase):
    def test_extract_text_verbosity(self):
        body = {
            "text": {
                "verbosity": "high"
            }
        }
        metadata = extract_codex_body_metadata(body)
        self.assertEqual(metadata.text_verbosity, "high")

    def test_extract_text_verbosity_missing(self):
        body = {
            "text": {
                "format": "markdown"
            }
        }
        metadata = extract_codex_body_metadata(body)
        self.assertIsNone(metadata.text_verbosity)

    def test_extract_text_verbosity_non_string(self):
        body = {
            "text": {
                "verbosity": 123
            }
        }
        metadata = extract_codex_body_metadata(body)
        self.assertIsNone(metadata.text_verbosity)

    def test_extract_reasoning_effort(self):
        body = {
            "reasoning": {
                "effort": "high"
            }
        }
        metadata = extract_codex_body_metadata(body)
        self.assertEqual(metadata.reasoning_effort, "high")

    def test_extract_has_output_schema(self):
        body = {
            "text": {
                "schema": {"type": "object"}
            }
        }
        metadata = extract_codex_body_metadata(body)
        self.assertTrue(metadata.has_output_schema)

    def test_extract_tools_count_and_names(self):
        body = {
            "tools": [
                {"name": "tool1", "type": "function"},
                {"function": {"name": "tool2"}, "type": "function"},
                {"type": "code_interpreter"}
            ]
        }
        metadata = extract_codex_body_metadata(body)
        self.assertEqual(metadata.tools_count, 3)
        self.assertEqual(metadata.tool_names, ["tool1", "tool2", "code_interpreter"])

if __name__ == "__main__":
    unittest.main()
