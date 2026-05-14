import sys
import unittest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy import qz_reasoning_policy
from proxy.qz_reasoning_policy import REASONING_POLICIES


class ReasoningPolicyTests(unittest.TestCase):
    def test_prompt_policy_injects_sampling_and_removes_budget(self):
        body = {
            "instructions": "Base instructions.",
            "thinking_budget_tokens": 512,
            "temperature": 0.2,
        }
        out = qz_reasoning_policy.apply_reasoning_policy(body, "high")

        self.assertNotIn("thinking_budget_tokens", out)
        self.assertEqual(out["temperature"], 0.2)
        self.assertEqual(out["top_p"], 0.95)
        self.assertEqual(out["top_k"], 20)
        self.assertEqual(out["min_p"], 0)
        self.assertEqual(out["presence_penalty"], 0.5)
        self.assertEqual(out["repeat_penalty"], 1.0)
        self.assertNotIn("repeat_last_n", out)
        self.assertNotIn("dry_multiplier", out)
        self.assertIn("Base instructions.", out["instructions"])
        self.assertEqual(out["metadata"]["qz_reasoning"]["policy"], "prompt")
        self.assertIsNone(out["metadata"]["qz_reasoning"]["thinking_budget_tokens"])

    # ------------------------------------------------------------------
    # Effort prompt shape: short depth-controls only
    # ------------------------------------------------------------------

    def test_all_effort_levels_present(self):
        for level in ("low", "medium", "high", "xhigh"):
            self.assertIn(level, REASONING_POLICIES)

    def test_effort_prompts_are_short(self):
        """Each effort prompt must stay short — depth control, not a mini system prompt."""
        max_words = 25
        for level, policy in REASONING_POLICIES.items():
            word_count = len(policy["prompt"].split())
            self.assertLessEqual(
                word_count,
                max_words,
                f"'{level}' prompt is too long ({word_count} words > {max_words}): {policy['prompt']!r}",
            )

    def test_prompts_do_not_demand_reasoning_disclosure(self):
        """Effort prompts must not tell the model to explain or show its reasoning."""
        forbidden = ("explain your reasoning", "show your reasoning", "show your work")
        for level, policy in REASONING_POLICIES.items():
            text = policy["prompt"].lower()
            for phrase in forbidden:
                self.assertNotIn(
                    phrase,
                    text,
                    f"'{level}' prompt demands reasoning disclosure: {policy['prompt']!r}",
                )

    def test_prompts_do_not_contain_cross_file_mandates(self):
        """Effort prompts must not contain broad cross-file or tool-count mandates."""
        forbidden_phrases = (
            "read at least two",
            "cross-reference at least",
            "at least two sources",
            "tool calls",
        )
        for level, policy in REASONING_POLICIES.items():
            text = policy["prompt"].lower()
            for phrase in forbidden_phrases:
                self.assertNotIn(
                    phrase,
                    text,
                    f"'{level}' prompt contains cross-file mandate {phrase!r}: {policy['prompt']!r}",
                )

    def test_high_and_xhigh_include_boundedness(self):
        """high and xhigh prompts must signal stopping / bounded exploration."""
        bounded_words = {"bounded", "diminishing", "stop"}
        for level in ("high", "xhigh"):
            text = REASONING_POLICIES[level]["prompt"].lower()
            has_bound = any(w in text for w in bounded_words)
            self.assertTrue(
                has_bound,
                f"'{level}' prompt lacks boundedness signal: {REASONING_POLICIES[level]['prompt']!r}",
            )

    def test_high_and_xhigh_include_final_answer_obligation(self):
        """high and xhigh must preserve the obligation to produce a final answer."""
        obligation_words = {"final answer", "produce", "answer"}
        for level in ("high", "xhigh"):
            text = REASONING_POLICIES[level]["prompt"].lower()
            has_obligation = any(w in text for w in obligation_words)
            self.assertTrue(
                has_obligation,
                f"'{level}' prompt lacks final-answer obligation: {REASONING_POLICIES[level]['prompt']!r}",
            )

    def test_low_prompt_discourages_over_investigation(self):
        """low prompt must signal restraint, not over-investigation."""
        text = REASONING_POLICIES["low"]["prompt"].lower()
        self.assertIn("over-invest", text)

    def test_each_policy_has_description_prompt_sampling(self):
        for level, policy in REASONING_POLICIES.items():
            self.assertIn("description", policy, f"'{level}' missing description")
            self.assertIn("prompt", policy, f"'{level}' missing prompt")
            self.assertIn("sampling", policy, f"'{level}' missing sampling")
            self.assertTrue(policy["prompt"].strip(), f"'{level}' prompt is empty")
            self.assertIsInstance(policy["sampling"], dict)

    def test_sampling_params_shared_across_levels(self):
        """All levels use the same sampling params (no per-level tuning without evidence)."""
        base = REASONING_POLICIES["medium"]["sampling"]
        for level, policy in REASONING_POLICIES.items():
            self.assertEqual(
                policy["sampling"],
                base,
                f"'{level}' sampling diverges from medium unexpectedly",
            )

    def test_apply_reasoning_policy_appends_to_existing_instructions(self):
        body = {"instructions": "Do the task."}
        out = qz_reasoning_policy.apply_reasoning_policy(body, "low")
        self.assertIn("Do the task.", out["instructions"])
        self.assertIn(REASONING_POLICIES["low"]["prompt"], out["instructions"])

    def test_apply_reasoning_policy_no_duplicate_prompt_injection(self):
        """Injecting the same effort level twice must not duplicate the prompt block."""
        body = {"instructions": "Start."}
        out = qz_reasoning_policy.apply_reasoning_policy(body, "medium")
        count_before = out["instructions"].count(REASONING_POLICIES["medium"]["prompt"])
        out2 = qz_reasoning_policy.apply_reasoning_policy(out, "medium")
        count_after = out2["instructions"].count(REASONING_POLICIES["medium"]["prompt"])
        self.assertEqual(count_before, count_after)


if __name__ == "__main__":
    unittest.main()
