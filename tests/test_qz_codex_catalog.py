import json
import tempfile
import unittest
from pathlib import Path

from proxy.qz_codex_catalog import (
    _boolish,
    _dedupe_blocks,
    build_live_model,
    catalog_defaults,
    deep_merge,
    generate,
    profile_slug,
    reasoning_level,
    supported_reasoning_levels,
    truncation_limit,
)


class DeepMergeTests(unittest.TestCase):
    def test_overlay_wins_on_scalar(self):
        self.assertEqual(deep_merge({"a": 1}, {"a": 2}), {"a": 2})

    def test_nested_dicts_are_merged_not_replaced(self):
        base = {"a": {"x": 1, "y": 2}}
        overlay = {"a": {"y": 99, "z": 3}}
        self.assertEqual(deep_merge(base, overlay), {"a": {"x": 1, "y": 99, "z": 3}})

    def test_base_not_a_dict_returns_empty(self):
        self.assertEqual(deep_merge(None, {"a": 1}), {"a": 1})

    def test_overlay_not_a_dict_returns_base(self):
        self.assertEqual(deep_merge({"a": 1}, None), {"a": 1})


class BoolishTests(unittest.TestCase):
    def test_true_values(self):
        for v in (True, 1, "true", "yes", "on", "1"):
            self.assertTrue(_boolish(v), v)

    def test_false_values(self):
        for v in (False, 0, "false", "no", "off", "0", ""):
            self.assertFalse(_boolish(v), v)

    def test_none_is_false(self):
        self.assertFalse(_boolish(None))


class DedupeBlocksTests(unittest.TestCase):
    def test_dedupes_exact_matches(self):
        self.assertEqual(_dedupe_blocks(["a", "b", "a"]), ["a", "b"])

    def test_strips_before_deduping(self):
        self.assertEqual(_dedupe_blocks(["  a  ", "a"]), ["a"])

    def test_empty_strings_dropped(self):
        self.assertEqual(_dedupe_blocks(["", "a", "  "]), ["a"])


class ProfileSlugTests(unittest.TestCase):
    def test_stem_wins(self):
        self.assertEqual(profile_slug({"stem": "my-model", "label": "My Model"}), "my-model")

    def test_filename_strips_gguf(self):
        self.assertEqual(profile_slug({"filename": "model.gguf"}), "model")

    def test_filename_without_gguf_kept(self):
        self.assertEqual(profile_slug({"filename": "model.bin"}), "model.bin")

    def test_empty_entry_returns_empty(self):
        self.assertEqual(profile_slug({}), "")


class ReasoningLevelTests(unittest.TestCase):
    def test_iq4_gives_low(self):
        self.assertEqual(reasoning_level({"label": "Qwen-IQ4-fast"}), "low")

    def test_aggressive_gives_low(self):
        self.assertEqual(reasoning_level({"stem": "model-aggressive"}), "low")

    def test_apex_gives_high(self):
        self.assertEqual(reasoning_level({"name": "model-apex"}), "high")

    def test_reasoning_gives_high(self):
        self.assertEqual(reasoning_level({"label": "qwq-reasoning-32b"}), "high")

    def test_default_is_medium(self):
        self.assertEqual(reasoning_level({"label": "ordinary-model"}), "medium")


class TruncationLimitTests(unittest.TestCase):
    def test_explicit_override_wins(self):
        self.assertEqual(truncation_limit({}, {"truncation_limit": 50000}, 262144), 50000)

    def test_derives_from_context_at_95_percent(self):
        self.assertEqual(truncation_limit({}, {}, 262144), int(262144 * 0.95))

    def test_minimum_10000_when_no_context(self):
        self.assertEqual(truncation_limit({}, {}, None), 10000)

    def test_minimum_floor_enforced(self):
        self.assertEqual(truncation_limit({}, {}, 1000), 10000)


class SupportedReasoningLevelsTests(unittest.TestCase):
    def test_returns_all_four_levels(self):
        levels = supported_reasoning_levels("medium")
        efforts = {l["effort"] for l in levels}
        self.assertEqual(efforts, {"low", "medium", "high", "xhigh"})

    def test_default_flag_set_correctly(self):
        levels = supported_reasoning_levels("high")
        defaults = [l for l in levels if l["default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["effort"], "high")


class BuildLiveModelTests(unittest.TestCase):
    def _entry(self, **kwargs):
        base = {
            "stem": "my-model",
            "label": "My Model",
            "backend_id": "my-model",
            "context_length": 131072,
            "overrides": {},
        }
        base.update(kwargs)
        return base

    def test_valid_entry_produces_model(self):
        model = build_live_model(self._entry(), 0)
        self.assertIsNotNone(model)
        self.assertEqual(model["slug"], "my-model")
        self.assertEqual(model["display_name"], "My Model")

    def test_invalid_profile_returns_none(self):
        entry = self._entry(profile_valid=False)
        self.assertIsNone(build_live_model(entry, 0))

    def test_missing_slug_returns_none(self):
        self.assertIsNone(build_live_model({}, 0))

    def test_context_window_set_from_runtime_context_length(self):
        model = build_live_model(self._entry(runtime_context_length=262144), 0)
        self.assertEqual(model["context_window"], 262144)
        self.assertEqual(model["max_context_window"], 262144)

    def test_context_window_falls_back_to_context_length(self):
        model = build_live_model(self._entry(context_length=131072), 0)
        self.assertEqual(model["context_window"], 131072)

    def test_truncation_limit_derived_from_context(self):
        model = build_live_model(self._entry(runtime_context_length=262144), 0)
        self.assertEqual(model["truncation_policy"]["limit"], int(262144 * 0.95))

    def test_priority_assigned(self):
        model = build_live_model(self._entry(), 1500)
        self.assertEqual(model["priority"], 1500)

    def test_catalog_defaults_present(self):
        model = build_live_model(self._entry(), 0)
        defaults = catalog_defaults()
        for key in ("shell_type", "visibility", "apply_patch_tool_type", "input_modalities"):
            self.assertIn(key, model, key)
        self.assertEqual(model["shell_type"], defaults["shell_type"])


class GenerateIntegrationTests(unittest.TestCase):
    """End-to-end: generate() reads inventory, writes catalog and patches toml."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config" / "default").mkdir(parents=True)
        (self.root / "config" / "user").mkdir(parents=True)
        (self.root / "var").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_inventory(self, models):
        path = self.root / "var" / "model-inventory.json"
        path.write_text(json.dumps({"models": models}), encoding="utf-8")
        return path

    def _write_toml(self, text=""):
        path = self.root / "var" / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def _catalog_path(self):
        return self.root / "var" / "catalog.json"

    def test_generates_catalog_from_inventory(self):
        inventory_path = self._write_inventory([
            {"stem": "model-a", "label": "Model A", "backend_id": "model-a",
             "context_length": 131072, "overrides": {}},
            {"stem": "model-b", "label": "Model B", "backend_id": "model-b",
             "context_length": 262144, "overrides": {}},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        generate(inventory_path, catalog_path, toml_path)

        catalog = json.loads(catalog_path.read_text())
        slugs = [m["slug"] for m in catalog["models"]]
        self.assertIn("model-a", slugs)
        self.assertIn("model-b", slugs)

    def test_invalid_profiles_excluded(self):
        inventory_path = self._write_inventory([
            {"stem": "good", "backend_id": "good", "context_length": 131072,
             "overrides": {}, "profile_valid": True},
            {"stem": "bad", "backend_id": "bad", "context_length": 131072,
             "overrides": {}, "profile_valid": False},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        generate(inventory_path, catalog_path, toml_path)

        catalog = json.loads(catalog_path.read_text())
        slugs = [m["slug"] for m in catalog["models"]]
        self.assertIn("good", slugs)
        self.assertNotIn("bad", slugs)

    def test_empty_inventory_produces_empty_catalog(self):
        inventory_path = self._write_inventory([])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        generate(inventory_path, catalog_path, toml_path)

        catalog = json.loads(catalog_path.read_text())
        self.assertEqual(catalog["models"], [])

    def test_patches_catalog_path_into_toml(self):
        inventory_path = self._write_inventory([])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("model = \"gpt-4o\"\n")

        generate(inventory_path, catalog_path, toml_path)

        toml_text = toml_path.read_text()
        self.assertIn(f'model_catalog_json = "{catalog_path}"', toml_text)
        self.assertIn('model = "gpt-4o"', toml_text)

    def test_updates_existing_catalog_line_in_toml(self):
        inventory_path = self._write_inventory([])
        catalog_path = self._catalog_path()
        old_line = 'model_catalog_json = "/old/path.json"'
        toml_path = self._write_toml(f"{old_line}\n")

        generate(inventory_path, catalog_path, toml_path)

        toml_text = toml_path.read_text()
        self.assertNotIn("/old/path.json", toml_text)
        self.assertIn(f'model_catalog_json = "{catalog_path}"', toml_text)

    def test_strips_stale_model_context_window_from_toml(self):
        inventory_path = self._write_inventory([])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("model_context_window = 131072\nmodel = \"gpt-4o\"\n")

        generate(inventory_path, catalog_path, toml_path)

        toml_text = toml_path.read_text()
        self.assertNotIn("model_context_window", toml_text)

    def test_strips_stale_model_max_output_tokens_from_toml(self):
        inventory_path = self._write_inventory([])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("model_max_output_tokens = 4096\n")

        generate(inventory_path, catalog_path, toml_path)

        self.assertNotIn("model_max_output_tokens", toml_path.read_text())

    def test_no_prompt_warning_printed_to_stderr(self):
        """When a model has no system prompt and disable_system_prompt is not
        set, generate() should print a warning to stderr."""
        inventory_path = self._write_inventory([
            {"stem": "promptless", "backend_id": "promptless",
             "context_length": 131072, "overrides": {}},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        import io
        from unittest.mock import patch

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            generate(inventory_path, catalog_path, toml_path)

        self.assertIn("warning", stderr_buf.getvalue().lower())
        self.assertIn("promptless", stderr_buf.getvalue())

    def test_no_warning_when_disable_system_prompt_set(self):
        """No warning when disable_system_prompt is explicitly set."""
        inventory_path = self._write_inventory([
            {"stem": "intentionally-blank", "backend_id": "intentionally-blank",
             "context_length": 131072,
             "overrides": {"disable_system_prompt": True}},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        import io
        from unittest.mock import patch

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            generate(inventory_path, catalog_path, toml_path)

        self.assertEqual(stderr_buf.getvalue(), "")

    def test_missing_inventory_produces_empty_catalog(self):
        inventory_path = self.root / "var" / "nonexistent.json"
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        generate(inventory_path, catalog_path, toml_path)

        catalog = json.loads(catalog_path.read_text())
        self.assertEqual(catalog["models"], [])
