import os
import sys
import unittest

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0])

from proxy import qz_reasoning_policy


class ReasoningPolicyTests(unittest.TestCase):
    def test_prompt_policy_injects_sampling_and_removes_budget(self):
        old_policy = os.environ.get("QZ_REASONING_POLICY")
        try:
            os.environ.pop("QZ_REASONING_POLICY", None)
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
            self.assertEqual(out["presence_penalty"], 0)
            self.assertEqual(out["repeat_penalty"], 1.0)
            self.assertTrue(out["instructions"].startswith("Use high reasoning effort."))
            self.assertIn("Base instructions.", out["instructions"])
            self.assertEqual(out["metadata"]["qz_reasoning"]["policy"], "prompt")
        finally:
            if old_policy is None:
                os.environ.pop("QZ_REASONING_POLICY", None)
            else:
                os.environ["QZ_REASONING_POLICY"] = old_policy

    def test_hard_budget_policy_keeps_explicit_budget_path(self):
        old_policy = os.environ.get("QZ_REASONING_POLICY")
        try:
            os.environ["QZ_REASONING_POLICY"] = "hard_budget"
            body = {}
            out = qz_reasoning_policy.apply_reasoning_policy(body, "xhigh")

            self.assertEqual(out["thinking_budget_tokens"], -1)
            self.assertEqual(out["metadata"]["qz_reasoning"]["policy"], "hard_budget")
            self.assertEqual(out["metadata"]["qz_reasoning"]["thinking_budget_tokens"], -1)
        finally:
            if old_policy is None:
                os.environ.pop("QZ_REASONING_POLICY", None)
            else:
                os.environ["QZ_REASONING_POLICY"] = old_policy


if __name__ == "__main__":
    unittest.main()
