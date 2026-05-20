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


class SearchPolicySearchConfigDefaultTests(unittest.TestCase):
    """search_config_default_profile fallback — #39 Slice D."""

    def test_search_config_default_used_when_no_model_override(self):
        selection = resolve_search_policy_selection(
            base_policy={"web_search_profiles": {"broad": {}}},
            base_policy_path="base.json",
            selected_model=None,
            search_config_default_profile="research",
        )
        self.assertEqual(selection.default_profile, "research")

    def test_model_override_wins_over_search_config_default(self):
        selected = {"overrides": {"search": {"default_profile": "coding"}}}
        selection = resolve_search_policy_selection(
            base_policy={},
            base_policy_path="base.json",
            selected_model=selected,
            search_config_default_profile="research",
        )
        self.assertEqual(selection.default_profile, "coding")

    def test_empty_search_config_default_still_works(self):
        selection = resolve_search_policy_selection(
            base_policy={},
            base_policy_path="base.json",
            selected_model=None,
            search_config_default_profile="",
        )
        self.assertEqual(selection.default_profile, "")


class ProfilesBundleSearchDefaultTests(unittest.TestCase):
    """qz.profiles.v1 bundle search.default_profile — #39 Slice D."""

    def test_bundle_search_default_profile_flows_to_overrides(self):
        """Profile bundle with search.default_profile sets overrides.search.default_profile."""
        from proxy.qz_model_catalog import _profiles_v1_to_manifest

        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "researcher": {
                    "backend": {"gguf": "researcher.gguf"},
                    "metadata": {"label": "Researcher"},
                    "search": {"default_profile": "research"},
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        entry = manifest["models"].get("researcher.gguf", {})
        self.assertIn("search", entry)
        self.assertEqual(entry["search"]["default_profile"], "research")

    def test_bundle_without_search_does_not_set_search_override(self):
        from proxy.qz_model_catalog import _profiles_v1_to_manifest

        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "plain": {
                    "backend": {"gguf": "plain.gguf"},
                    "metadata": {"label": "Plain"},
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        entry = manifest["models"].get("plain.gguf", {})
        self.assertNotIn("search", entry)

    def test_bundle_search_default_profile_resolved_via_selection(self):
        """Full path: bundle search.default_profile flows to SearchPolicySelection."""
        from proxy.qz_model_catalog import _profiles_v1_to_manifest

        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "newsreader": {
                    "backend": {"gguf": "newsreader.gguf"},
                    "search": {"default_profile": "news"},
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        selected_model = manifest["models"].get("newsreader.gguf", {})
        selected_model = {"overrides": selected_model}

        selection = resolve_search_policy_selection(
            base_policy={"web_search_profiles": {"news": {}, "broad": {}}},
            base_policy_path="base.json",
            selected_model=selected_model,
        )
        self.assertEqual(selection.default_profile, "news")

    def test_unknown_bundle_search_default_profile_does_not_crash(self):
        """Unknown profile names flow through safely; runtime falls back."""
        selection = resolve_search_policy_selection(
            base_policy={"web_search_profiles": {"broad": {}}},
            base_policy_path="base.json",
            selected_model={"overrides": {"search": {"default_profile": "unknown-profile"}}},
        )
        # The selection passes it through; WebSearchRuntime falls back at use time
        self.assertEqual(selection.default_profile, "unknown-profile")

    def test_routing_rules_not_in_bundle(self):
        """search.default_profile in bundle does not set engine/category routing."""
        from proxy.qz_model_catalog import _profiles_v1_to_manifest

        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "x": {
                    "backend": {"gguf": "x.gguf"},
                    "search": {"default_profile": "coding"},
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        entry = manifest["models"].get("x.gguf", {})
        search_override = entry.get("search", {})
        self.assertNotIn("engines", search_override)
        self.assertNotIn("categories", search_override)


if __name__ == "__main__":
    unittest.main()
