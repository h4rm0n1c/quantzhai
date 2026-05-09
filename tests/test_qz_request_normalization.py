#!/usr/bin/env python3
import json
import unittest
from copy import deepcopy
from pathlib import Path

from proxy.qz_request_normalization import (
    clean_content,
    normalize_responses_input_for_qwen,
    recursive_clean,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "responses_input"


class RequestNormalizationTests(unittest.TestCase):
    def test_golden_mixed_history_is_normalized_for_qwen(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("mixed_history_normalization.json").read_text())

        out = normalize_responses_input_for_qwen(deepcopy(fixture["body"]))

        self.assertEqual(out["input"], fixture["expected_input"])
        self.assertIn("qz_prompt_policy", out["metadata"])
        self.assertTrue(out["metadata"]["qz_prompt_policy"]["mode"])

    def test_golden_malformed_empty_tool_history_is_filtered(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("malformed_empty_tool_history.json").read_text())

        out = normalize_responses_input_for_qwen(deepcopy(fixture["body"]))

        self.assertEqual(out["input"], fixture["expected_input"])
        self.assertIn("qz_prompt_policy", out["metadata"])

    def test_golden_native_codex_first_request_input_is_normalized(self):
        fixture = json.loads(FIXTURE_DIR.joinpath("native_codex_first_request_shape.json").read_text())

        out = normalize_responses_input_for_qwen(deepcopy(fixture["body"]))

        self.assertEqual(out["input"], fixture["expected_input"])
        self.assertIn("You are Codex, powered by Qwen3.6", out["instructions"])
        self.assertNotIn("NATIVE CODEX TOP LEVEL INSTRUCTIONS", out["instructions"])
        self.assertNotIn("<permissions instructions>", out["instructions"])
        self.assertNotIn("<environment_context>", json.dumps(out["input"]))
        self.assertEqual(out["metadata"]["qz_prompt_policy"]["mode"], "replace_client")
        self.assertTrue(out["metadata"]["qz_prompt_policy"]["replaced_client"])

    def test_recursive_clean_removes_think_and_scratchpad_text(self):
        body = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "<think>private</think>\nSelf-Correction\n1. final answer",
                        }
                    ],
                }
            ]
        }

        out = recursive_clean(body)

        self.assertEqual(out["output"][0]["content"][0]["text"], "1. final answer")
        self.assertEqual(clean_content("</think>\nDone"), "Done")


if __name__ == "__main__":
    unittest.main()
