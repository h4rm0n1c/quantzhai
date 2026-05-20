"""Tests for proxy/qz_search_config.py — #39 Slice B."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_search_config import (
    SEARCH_CONFIG_SCHEMA,
    SearchConfigResult,
    load_search_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_search_json(tmp: str, subdir: str, payload: dict) -> Path:
    d = Path(tmp) / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / "search.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Default config loads
# ---------------------------------------------------------------------------


class DefaultSearchConfigTests(unittest.TestCase):
    """Loads from config/default/search.json when present."""

    def test_loads_repo_default(self):
        """Real config/default/search.json loads without error."""
        result = load_search_config(env={}, root=_REPO_ROOT)
        self.assertIsInstance(result, SearchConfigResult)
        self.assertEqual(result.config.get("schema"), SEARCH_CONFIG_SCHEMA)
        self.assertIn(result.source, ("default", "legacy"))

    def test_default_contains_profiles(self):
        result = load_search_config(env={}, root=_REPO_ROOT)
        profiles = result.config.get("profiles") or {}
        self.assertIn("auto", profiles)
        self.assertIn("coding", profiles)

    def test_no_files_returns_empty_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "default").mkdir(parents=True)
            result = load_search_config(env={}, root=root)
            self.assertEqual(result.source, "empty")
            self.assertIsNotNone(result.config)

    def test_no_files_empty_base_url_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "default").mkdir(parents=True)
            result = load_search_config(env={}, root=root)
            warning_text = " ".join(result.warnings)
            self.assertIn("base URL", warning_text)

    def test_default_only_sets_source_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://local:8080"},
            })
            result = load_search_config(env={}, root=root)
            self.assertEqual(result.source, "default")


# ---------------------------------------------------------------------------
# User override
# ---------------------------------------------------------------------------


class UserSearchConfigTests(unittest.TestCase):
    """config/user/search.json overrides default."""

    def test_user_overrides_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "", "timeout_s": 15},
                "defaults": {"profile": "broad"},
            })
            _make_search_json(tmp, "config/user", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://private:9090"},
                "defaults": {"profile": "research"},
            })
            result = load_search_config(env={}, root=root)
            self.assertEqual(result.source, "user")
            self.assertEqual(result.config["searxng"]["base_url"], "http://private:9090")
            self.assertEqual(result.config["defaults"]["profile"], "research")
            # Default timeout is preserved via deep merge
            self.assertEqual(result.config["searxng"]["timeout_s"], 15)

    def test_user_missing_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://default:7070"},
            })
            result = load_search_config(env={}, root=root)
            self.assertEqual(result.source, "default")
            self.assertEqual(result.config["searxng"]["base_url"], "http://default:7070")


# ---------------------------------------------------------------------------
# QZ_SEARCH_CONFIG_PATH explicit override
# ---------------------------------------------------------------------------


class ExplicitPathTests(unittest.TestCase):
    """QZ_SEARCH_CONFIG_PATH overrides all file sources."""

    def test_explicit_path_overrides_default_and_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://default:7070"},
            })
            _make_search_json(tmp, "config/user", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://user:8080"},
            })
            explicit = Path(tmp) / "custom-search.json"
            explicit.write_text(json.dumps({
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://explicit:9999"},
            }), encoding="utf-8")
            result = load_search_config(
                env={"QZ_SEARCH_CONFIG_PATH": str(explicit)},
                root=root,
            )
            self.assertEqual(result.source, "explicit")
            self.assertEqual(result.config["searxng"]["base_url"], "http://explicit:9999")

    def test_explicit_path_does_not_inherit_tracked_default_profiles(self):
        """QZ_SEARCH_CONFIG_PATH must not inherit profiles from config/default/search.json.

        The explicit file replaces the tracked-default file selection entirely.
        It merges only over built-in _DEFAULT_CONFIG.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Default has coding profile with specific engines.
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "profiles": {
                    "coding": {"engines": ["stackoverflow", "github"]},
                    "broad": {"categories": ["general"]},
                },
            })
            # Explicit has only one profile — should NOT also get coding/broad.
            explicit = Path(tmp) / "custom.json"
            explicit.write_text(json.dumps({
                "schema": SEARCH_CONFIG_SCHEMA,
                "profiles": {"myprofile": {"categories": ["it"]}},
                "searxng": {"base_url": "http://x:1234"},
            }), encoding="utf-8")
            result = load_search_config(
                env={"QZ_SEARCH_CONFIG_PATH": str(explicit)},
                root=root,
            )
            self.assertEqual(result.source, "explicit")
            profiles = result.config.get("profiles", {})
            self.assertIn("myprofile", profiles)
            self.assertNotIn("coding", profiles,
                "explicit file must not inherit tracked default profiles")
            self.assertNotIn("broad", profiles,
                "explicit file must not inherit tracked default profiles")

    def test_explicit_path_not_found_adds_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = load_search_config(
                env={"QZ_SEARCH_CONFIG_PATH": str(Path(tmp) / "nonexistent.json")},
                root=Path(tmp),
            )
            self.assertTrue(any("QZ_SEARCH_CONFIG_PATH" in w for w in result.warnings))


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------


class EnvOverrideTests(unittest.TestCase):
    """SEARXNG_BASE_URL and SEARXNG_TIMEOUT override file values."""

    def test_base_url_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://from-file:7070"},
            })
            result = load_search_config(
                env={"SEARXNG_BASE_URL": "http://from-env:8888"},
                root=root,
            )
            self.assertEqual(result.config["searxng"]["base_url"], "http://from-env:8888")

    def test_timeout_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://x:1234", "timeout_s": 15},
            })
            result = load_search_config(
                env={"SEARXNG_TIMEOUT": "30"},
                root=root,
            )
            self.assertEqual(result.config["searxng"]["timeout_s"], 30.0)

    def test_invalid_timeout_env_adds_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://x:1234"},
            })
            result = load_search_config(
                env={"SEARXNG_TIMEOUT": "not_a_number"},
                root=root,
            )
            self.assertTrue(any("SEARXNG_TIMEOUT" in w for w in result.warnings))


# ---------------------------------------------------------------------------
# Legacy SEARXNG_POLICY fallback
# ---------------------------------------------------------------------------


class LegacyFallbackTests(unittest.TestCase):
    """SEARXNG_POLICY env provides legacy fallback when no search.json exists."""

    def test_legacy_env_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = Path(tmp) / "my-policy.json"
            legacy.write_text(json.dumps({"version": "profiled-web-search-v1"}), encoding="utf-8")
            result = load_search_config(
                env={"SEARXNG_POLICY": str(legacy)},
                root=root,
            )
            self.assertEqual(result.source, "legacy")
            self.assertEqual(result.legacy_policy_path, legacy)
            self.assertTrue(any("legacy" in w.lower() or "search-policy" in w.lower()
                                for w in result.warnings))

    def test_legacy_fallback_warning_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = Path(tmp) / "p.json"
            legacy.write_text("{}", encoding="utf-8")
            result = load_search_config(
                env={"SEARXNG_POLICY": str(legacy)},
                root=root,
            )
            self.assertTrue(len(result.warnings) > 0)

    def test_no_fallback_when_search_json_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://x:1234"},
            })
            legacy = Path(tmp) / "p.json"
            legacy.write_text("{}", encoding="utf-8")
            result = load_search_config(
                env={"SEARXNG_POLICY": str(legacy)},
                root=root,
            )
            self.assertNotEqual(result.source, "legacy")


# ---------------------------------------------------------------------------
# Effective summary (never exposes base_url)
# ---------------------------------------------------------------------------


class EffectiveSummaryTests(unittest.TestCase):
    """effective_summary() never exposes the actual base URL."""

    def test_base_url_not_in_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://private-host:9090"},
            })
            result = load_search_config(env={}, root=root)
            summary = result.effective_summary()
            summary_str = json.dumps(summary)
            self.assertNotIn("http://private-host", summary_str)
            self.assertNotIn("private-host", summary_str)

    def test_base_url_set_true_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": "http://configured:8080"},
            })
            result = load_search_config(env={}, root=root)
            self.assertTrue(result.effective_summary()["searxng_base_url_set"])

    def test_base_url_set_false_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_search_json(tmp, "config/default", {
                "schema": SEARCH_CONFIG_SCHEMA,
                "searxng": {"base_url": ""},
            })
            result = load_search_config(env={}, root=root)
            self.assertFalse(result.effective_summary()["searxng_base_url_set"])

    def test_summary_contains_expected_keys(self):
        result = load_search_config(env={}, root=_REPO_ROOT)
        summary = result.effective_summary()
        for key in ("schema", "source", "path", "searxng_base_url_set",
                    "default_profile", "profile_names", "warnings"):
            self.assertIn(key, summary)


# ---------------------------------------------------------------------------
# Invalid JSON / resilience
# ---------------------------------------------------------------------------


class ResilienceTests(unittest.TestCase):
    """Malformed files produce warnings and fall through safely."""

    def test_invalid_json_default_adds_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = Path(tmp) / "config" / "default"
            d.mkdir(parents=True)
            (d / "search.json").write_text("not { valid json }", encoding="utf-8")
            result = load_search_config(env={}, root=root)
            self.assertTrue(any("parse error" in w for w in result.warnings))

    def test_invalid_json_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = Path(tmp) / "config" / "default"
            d.mkdir(parents=True)
            (d / "search.json").write_text("{broken}", encoding="utf-8")
            try:
                load_search_config(env={}, root=root)
            except Exception as exc:
                self.fail(f"load_search_config raised unexpectedly: {exc}")

    def test_non_object_json_adds_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = Path(tmp) / "config" / "default"
            d.mkdir(parents=True)
            (d / "search.json").write_text("[1, 2, 3]", encoding="utf-8")
            result = load_search_config(env={}, root=root)
            self.assertTrue(any("parse error" in w or "expected JSON" in w
                                for w in result.warnings))


# ---------------------------------------------------------------------------
# Gitignore check
# ---------------------------------------------------------------------------


class GitignoreTests(unittest.TestCase):
    """config/user/search.json must be covered by .gitignore."""

    def test_user_search_json_is_gitignored(self):
        gitignore = _REPO_ROOT / ".gitignore"
        if not gitignore.exists():
            self.skipTest(".gitignore not found")
        text = gitignore.read_text(encoding="utf-8")
        # config/user/* or config/user/search.json must match
        self.assertTrue(
            "config/user/*" in text or "config/user/search.json" in text,
            "config/user/search.json must be covered by .gitignore "
            "(expected 'config/user/*' or 'config/user/search.json')",
        )


if __name__ == "__main__":
    unittest.main()
