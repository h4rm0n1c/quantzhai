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
from proxy.qz_prompt_policy import assemble_instruction_stack, system_prompt_disabled


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

    def test_disable_system_prompt_strips_all_forwarded_instruction_text(self):
        body = {
            "instructions": "NATIVE CODEX TOP LEVEL INSTRUCTIONS",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "developer harness"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "talk directly"}],
                },
            ],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={"overrides": {"disable_system_prompt": True}},
        )

        self.assertNotIn("instructions", out)
        self.assertEqual(out["input"], [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "talk directly"}],
        }])
        self.assertTrue(out["metadata"]["qz_prompt_policy"]["disable_system_prompt"])

    def test_assemble_instruction_stack_reports_system_prompt_disabled(self):
        text, report = assemble_instruction_stack(
            existing_instructions="client prompt",
            client_blocks=["developer prompt"],
            selected_model={"overrides": {"disable_system_prompt": True}},
        )

        self.assertEqual(text, "")
        self.assertTrue(report["disable_system_prompt"])
        self.assertFalse(report["replacement_available"])

    def test_system_prompt_disabled_reads_selected_model_overrides(self):
        self.assertTrue(system_prompt_disabled({"overrides": {"disable_system_prompt": True}}))
        self.assertFalse(system_prompt_disabled({"overrides": {"disable_system_prompt": False}}))

    def test_turn_harness_injects_into_latest_user_when_prompt_stack_absent(self):
        body = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "old turn"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "old answer"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "continue"}],
                },
            ],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={"overrides": {"turn_harnesses": ["roleplay-private-thoughts"]}},
        )

        first_user = out["input"][0]["content"][0]["text"]
        latest_user = out["input"][2]["content"][0]["text"]
        self.assertEqual(first_user, "old turn")
        self.assertNotIn("Behavioral guidance:", latest_user)
        self.assertIn("Keep internal reasoning", latest_user)
        self.assertIn("User message:", latest_user)
        self.assertTrue(latest_user.endswith("continue"))
        self.assertTrue(out["metadata"]["qz_turn_harness"]["applied"])
        self.assertEqual(out["metadata"]["qz_turn_harness"]["active"], ["roleplay-private-thoughts"])

    def test_turn_harness_skips_on_first_user_turn_even_with_system_prompt_stack(self):
        body = {
            "instructions": "native client prompt",
            "input": [{
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            }],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={
                "overrides": {
                    "system_prompt": "profile system prompt",
                    "prompt_append": "initial roleplay harness",
                    "turn_harnesses": ["roleplay-private-thoughts"],
                },
            },
        )

        self.assertIn("profile system prompt", out["instructions"])
        self.assertIn("initial roleplay harness", out["instructions"])
        self.assertEqual(out["input"][0]["content"][0]["text"], "hello")
        self.assertFalse(out["metadata"]["qz_turn_harness"]["applied"])
        self.assertEqual(out["metadata"]["qz_turn_harness"]["skipped_reason"], "first_turn")

    def test_turn_harness_applies_after_first_turn_with_system_prompt_stack(self):
        body = {
            "instructions": "native client prompt",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "hi"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "later turn"}],
                },
            ],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={
                "overrides": {
                    "system_prompt": "profile system prompt",
                    "turn_harnesses": ["caveman-ultra-lock"],
                },
            },
        )

        self.assertIn("profile system prompt", out["instructions"])
        self.assertTrue(out["metadata"]["qz_turn_harness"]["applied"])
        self.assertEqual(out["input"][0]["content"][0]["text"], "hello")
        self.assertIn("Caveman ultra is ON and locked", out["input"][2]["content"][0]["text"])

    def test_turn_harness_dedupes_and_reports_unknown_names(self):
        body = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "old"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "old answer"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "fix this"}],
                },
            ],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={
                "overrides": {
                    "turn_harness": "caveman-ultra-lock",
                    "turn_harnesses": ["caveman-ultra-lock", "missing-harness"],
                },
            },
        )

        text = out["input"][2]["content"][0]["text"]
        self.assertEqual(text.count("Caveman ultra is ON and locked"), 1)
        self.assertEqual(out["metadata"]["qz_turn_harness"]["active"], ["caveman-ultra-lock"])
        self.assertEqual(out["metadata"]["qz_turn_harness"]["unknown"], ["missing-harness"])

    def test_turn_harness_strips_old_replayed_harness_blocks(self):
        body = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "Behavioral guidance:\nold reminder\n\nUser message:\nold turn",
                    }],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "old answer"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "new turn"}],
                },
            ],
        }

        out = normalize_responses_input_for_qwen(
            body,
            selected_model={"overrides": {"turn_harnesses": ["caveman-ultra-lock"]}},
        )

        self.assertEqual(out["input"][0]["content"][0]["text"], "old turn")
        self.assertNotIn("Behavioral guidance:", out["input"][2]["content"][0]["text"])
        self.assertEqual(out["input"][2]["content"][0]["text"].count("User message:"), 1)
        self.assertTrue(out["input"][2]["content"][0]["text"].endswith("new turn"))

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
