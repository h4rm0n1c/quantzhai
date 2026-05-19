import json
import tempfile
import unittest
from pathlib import Path

from proxy.qz_codex_catalog import (
    build_live_model,
    catalog_defaults,
    generate,
    profile_slug,
    reasoning_level,
    supported_reasoning_levels,
    truncation_limit,
)


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
            # Provide an inline system_prompt so assemble_instruction_stack
            # doesn't need to read any files from disk in the test environment.
            "overrides": {"system_prompt": "test prompt"},
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

    def test_inline_system_prompt_appears_in_base_instructions(self):
        model = build_live_model(self._entry(), 0)
        self.assertIn("test prompt", model["base_instructions"])


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
        path = self.root / "var" / "generated" / "model-inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"models": models}), encoding="utf-8")
        return path

    def _write_toml(self, text=""):
        path = self.root / "var" / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def _catalog_path(self):
        return self.root / "var" / "catalog.json"

    def _entry(self, stem="model-a", **kwargs):
        base = {
            "stem": stem,
            "label": stem.replace("-", " ").title(),
            "backend_id": stem,
            "context_length": 131072,
            # Inline prompt avoids needing real prompt files on disk.
            "overrides": {"system_prompt": f"prompt for {stem}"},
        }
        base.update(kwargs)
        return base

    def test_generates_catalog_from_inventory(self):
        inventory_path = self._write_inventory([
            self._entry("model-a"),
            self._entry("model-b", context_length=262144),
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
            self._entry("good", profile_valid=True),
            self._entry("bad", profile_valid=False),
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

    def test_missing_inventory_produces_empty_catalog(self):
        inventory_path = self.root / "var" / "nonexistent.json"
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        generate(inventory_path, catalog_path, toml_path)

        catalog = json.loads(catalog_path.read_text())
        self.assertEqual(catalog["models"], [])

    def _minimal_overrides_env(self):
        """Return env patches that make assemble_instruction_stack see an empty
        global manifest — so a model with no overrides genuinely has no prompt."""
        empty_overrides = self.root / "config" / "user" / "empty-overrides.json"
        empty_overrides.write_text('{"models": {}}', encoding="utf-8")
        return {"QZ_MODEL_OVERRIDES": str(empty_overrides), "QZ_ROOT": str(self.root)}

    def test_no_prompt_warning_printed_to_stderr(self):
        """When a model has no system prompt and disable_system_prompt is not
        set, generate() should print a warning to stderr."""
        import io
        import os
        from unittest.mock import patch

        inventory_path = self._write_inventory([
            {"stem": "promptless", "backend_id": "promptless",
             "context_length": 131072, "overrides": {}},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        stderr_buf = io.StringIO()
        with patch.dict(os.environ, self._minimal_overrides_env()), \
             patch("sys.stderr", stderr_buf):
            generate(inventory_path, catalog_path, toml_path)

        self.assertIn("warning", stderr_buf.getvalue().lower())
        self.assertIn("promptless", stderr_buf.getvalue())

    def test_no_warning_when_disable_system_prompt_set(self):
        """No warning when disable_system_prompt is explicitly set."""
        import io
        import os
        from unittest.mock import patch

        inventory_path = self._write_inventory([
            {"stem": "intentionally-blank", "backend_id": "intentionally-blank",
             "context_length": 131072,
             "overrides": {"disable_system_prompt": True}},
        ])
        catalog_path = self._catalog_path()
        toml_path = self._write_toml("")

        stderr_buf = io.StringIO()
        with patch.dict(os.environ, self._minimal_overrides_env()), \
             patch("sys.stderr", stderr_buf):
            generate(inventory_path, catalog_path, toml_path)

        self.assertEqual(stderr_buf.getvalue(), "")
