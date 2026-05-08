import sys
import unittest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy import qz_reasoning_policy


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
        self.assertEqual(out["presence_penalty"], 1.5)
        self.assertEqual(out["repeat_penalty"], 1.0)
        self.assertNotIn("repeat_last_n", out)
        self.assertNotIn("dry_multiplier", out)
        self.assertIn("Reasoning effort: high.", out["instructions"])
        self.assertNotIn("Do not narrate hidden analysis.", out["instructions"])
        self.assertIn("Base instructions.", out["instructions"])
        self.assertEqual(out["metadata"]["qz_reasoning"]["policy"], "prompt")
        self.assertIsNone(out["metadata"]["qz_reasoning"]["thinking_budget_tokens"])


if __name__ == "__main__":
    unittest.main()
