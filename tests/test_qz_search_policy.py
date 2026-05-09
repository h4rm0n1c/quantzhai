import json
import tempfile
import unittest
from pathlib import Path

from proxy.qz_search_policy import resolve_search_policy_selection


class SearchPolicySelectionTests(unittest.TestCase):
    def test_model_override_can_select_policy_file_and_default_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            policy_dir = root / "config" / "user"
            policy_dir.mkdir(parents=True)
            policy_path = policy_dir / "research-search.json"
            policy_path.write_text(json.dumps({"web_search_profiles": {"deep": {}}}), encoding="utf-8")

            selected = {
                "overrides": {
                    "search": {
                        "policy_file": "research-search.json",
                        "default_profile": "deep",
                    }
                }
            }

            selection = resolve_search_policy_selection(
                base_policy={"web_search_profiles": {"broad": {}}},
                base_policy_path=str(root / "config" / "default" / "search-policy.json"),
                selected_model=selected,
                root=root,
            )

            self.assertEqual(selection.source, "model_override")
            self.assertEqual(selection.policy_path, str(policy_path))
            self.assertEqual(selection.default_profile, "deep")
            self.assertIn("deep", selection.policy["web_search_profiles"])

    def test_bad_override_file_falls_back_to_base_policy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base_policy = {"web_search_profiles": {"broad": {}}}
            selected = {"overrides": {"search_policy_file": "missing.json", "search_policy_profile": "broad"}}

            selection = resolve_search_policy_selection(
                base_policy=base_policy,
                base_policy_path="base.json",
                selected_model=selected,
                root=root,
            )

            self.assertEqual(selection.source, "base_fallback")
            self.assertEqual(selection.policy, base_policy)
            self.assertEqual(selection.default_profile, "broad")
            self.assertIn("missing.json", selection.error)


if __name__ == "__main__":
    unittest.main()
