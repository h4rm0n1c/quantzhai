import sys
import unittest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy import qz_reasoning_policy
from proxy.qz_reasoning_policy import (
    REASONING_POLICIES,
    THINKING_MODE_BUDGET_TOKENS,
    REASONING_BUDGET_MESSAGE,
    normalize_thinking_mode,
)


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


class NormalizeThinkingModeTests(unittest.TestCase):
    """normalize_thinking_mode() alias coverage."""

    def _n(self, value):
        return normalize_thinking_mode(value)

    def test_thinking_aliases(self):
        for alias in ("think", "thinking", "on", "true", "1", "THINKING", "Think"):
            self.assertEqual(self._n(alias), "thinking", f"alias {alias!r} should be 'thinking'")

    def test_non_thinking_aliases(self):
        for alias in ("non_thinking", "nonthinking", "non-thinking",
                      "instruct", "off", "false", "0", "none", "no",
                      "NON_THINKING", "Instruct"):
            self.assertEqual(self._n(alias), "non_thinking", f"alias {alias!r} should be 'non_thinking'")

    def test_auto_for_none(self):
        self.assertEqual(self._n(None), "auto")

    def test_auto_for_empty(self):
        self.assertEqual(self._n(""), "auto")
        self.assertEqual(self._n("   "), "auto")

    def test_auto_for_unknown(self):
        self.assertEqual(self._n("banana"), "auto")
        self.assertEqual(self._n("auto"), "auto")


class ThinkingModeBudgetTableTests(unittest.TestCase):
    """THINKING_MODE_BUDGET_TOKENS covers all effort levels with correct values."""

    def test_all_effort_levels_present(self):
        for level in ("low", "medium", "high", "xhigh"):
            self.assertIn(level, THINKING_MODE_BUDGET_TOKENS)

    def test_low_budget(self):
        self.assertEqual(THINKING_MODE_BUDGET_TOKENS["low"], 16384)

    def test_medium_budget(self):
        self.assertEqual(THINKING_MODE_BUDGET_TOKENS["medium"], 24576)

    def test_high_budget(self):
        self.assertEqual(THINKING_MODE_BUDGET_TOKENS["high"], 32768)

    def test_xhigh_budget(self):
        self.assertEqual(THINKING_MODE_BUDGET_TOKENS["xhigh"], 49152)

    def test_budgets_strictly_increasing(self):
        vals = [THINKING_MODE_BUDGET_TOKENS[l] for l in ("low", "medium", "high", "xhigh")]
        self.assertEqual(vals, sorted(vals))

    def test_reasoning_budget_message_non_empty(self):
        self.assertIsInstance(REASONING_BUDGET_MESSAGE, str)
        self.assertTrue(REASONING_BUDGET_MESSAGE.strip())


class ApplyReasoningPolicyThinkingModeTests(unittest.TestCase):
    """apply_reasoning_policy() behaviour with thinking_mode argument."""

    def _apply(self, level="medium", thinking_mode=None, body=None):
        b = body if body is not None else {"instructions": "Do the task."}
        return qz_reasoning_policy.apply_reasoning_policy(b, level, thinking_mode=thinking_mode)

    # --- thinking mode ---

    def test_thinking_mode_injects_budget_tokens(self):
        out = self._apply("medium", "thinking")
        self.assertEqual(out["reasoning_budget_tokens"], THINKING_MODE_BUDGET_TOKENS["medium"])

    def test_thinking_mode_injects_budget_message(self):
        out = self._apply("medium", "thinking")
        self.assertEqual(out["reasoning_budget_message"], REASONING_BUDGET_MESSAGE)

    def test_thinking_mode_does_not_overwrite_explicit_budget(self):
        """Caller-supplied reasoning_budget_tokens must not be overwritten."""
        out = self._apply("high", "thinking", body={"reasoning_budget_tokens": 999})
        self.assertEqual(out["reasoning_budget_tokens"], 999)

    def test_thinking_budget_in_metadata_for_thinking_mode(self):
        out = self._apply("high", "thinking")
        self.assertEqual(
            out["metadata"]["qz_reasoning"]["thinking_budget_tokens"],
            THINKING_MODE_BUDGET_TOKENS["high"],
        )

    def test_thinking_mode_in_metadata(self):
        out = self._apply("medium", "thinking")
        self.assertEqual(out["metadata"]["qz_reasoning"]["thinking_mode"], "thinking")

    def test_low_effort_thinking_budget(self):
        out = self._apply("low", "thinking")
        self.assertEqual(out["reasoning_budget_tokens"], 16384)

    def test_xhigh_effort_thinking_budget(self):
        out = self._apply("xhigh", "thinking")
        self.assertEqual(out["reasoning_budget_tokens"], 49152)

    # --- non_thinking mode ---

    def test_non_thinking_omits_budget_tokens(self):
        out = self._apply("high", "non_thinking")
        self.assertNotIn("reasoning_budget_tokens", out)

    def test_non_thinking_omits_budget_message(self):
        out = self._apply("high", "non_thinking")
        self.assertNotIn("reasoning_budget_message", out)

    def test_non_thinking_removes_existing_budget_tokens(self):
        out = self._apply("high", "non_thinking", body={"reasoning_budget_tokens": 32768})
        self.assertNotIn("reasoning_budget_tokens", out)

    def test_non_thinking_metadata_budget_is_none(self):
        out = self._apply("high", "non_thinking")
        self.assertIsNone(out["metadata"]["qz_reasoning"]["thinking_budget_tokens"])

    def test_non_thinking_mode_in_metadata(self):
        out = self._apply("medium", "non_thinking")
        self.assertEqual(out["metadata"]["qz_reasoning"]["thinking_mode"], "non_thinking")

    # --- auto / None (safe fallback) ---

    def test_auto_mode_no_budget_injected(self):
        out = self._apply("high", "auto")
        self.assertNotIn("reasoning_budget_tokens", out)

    def test_none_thinking_mode_no_budget_injected(self):
        out = self._apply("high", None)
        self.assertNotIn("reasoning_budget_tokens", out)

    def test_auto_mode_in_metadata(self):
        out = self._apply("medium", "auto")
        self.assertEqual(out["metadata"]["qz_reasoning"]["thinking_mode"], "auto")

    # --- backward compat / OAI path field mirroring ---

    def test_thinking_mode_sets_thinking_budget_tokens_for_oai_path(self):
        """thinking mode must set thinking_budget_tokens for server-common.cpp OAI compat."""
        out = self._apply("medium", "thinking")
        self.assertIn("thinking_budget_tokens", out)
        self.assertEqual(out["thinking_budget_tokens"], THINKING_MODE_BUDGET_TOKENS["medium"])

    def test_thinking_mode_thinking_budget_tokens_mirrors_reasoning_budget(self):
        """thinking_budget_tokens must mirror reasoning_budget_tokens (honours caller override)."""
        out = self._apply("high", "thinking", body={"reasoning_budget_tokens": 999})
        self.assertEqual(out["reasoning_budget_tokens"], 999)
        self.assertEqual(out["thinking_budget_tokens"], 999)

    def test_thinking_mode_caller_thinking_budget_replaced_by_policy(self):
        """Caller-supplied thinking_budget_tokens is replaced by policy budget (pop+set)."""
        out = self._apply("medium", "thinking", body={"thinking_budget_tokens": 512})
        self.assertIn("thinking_budget_tokens", out)
        self.assertEqual(out["thinking_budget_tokens"], THINKING_MODE_BUDGET_TOKENS["medium"])

    def test_non_thinking_thinking_budget_tokens_still_removed(self):
        out = self._apply("medium", "non_thinking", body={"thinking_budget_tokens": 512})
        self.assertNotIn("thinking_budget_tokens", out)

    def test_auto_mode_thinking_budget_tokens_absent(self):
        """Auto mode must not inject thinking_budget_tokens."""
        out = self._apply("medium", "auto")
        self.assertNotIn("thinking_budget_tokens", out)


if __name__ == "__main__":
    unittest.main()
