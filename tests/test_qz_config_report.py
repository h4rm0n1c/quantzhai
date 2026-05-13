import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_config_report import EFFECTIVE_CONFIG_SCHEMA, effective_config_payload


class EffectiveConfigReportTests(unittest.TestCase):
    def test_report_classifies_current_paths(self):
        old_env = {name: os.environ.get(name) for name in (
            "QZ_ROOT",
            "QZ_VAR_DIR",
            "QZ_MODEL_DIR",
            "QZ_MODEL_OVERRIDES",
            "QZ_MODEL_INVENTORY_CACHE",
            "QZ_CAPTURE_MODE",
            "SEARXNG_POLICY",
            "SEARXNG_CAPABILITIES",
        )}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "config" / "example").mkdir(parents=True)
                (root / "proxy").mkdir()
                (var_dir / "models").mkdir(parents=True)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"profile.gguf":{"system_prompt_file":"config/user/prompts/profile.md"}}}\n',
                    encoding="utf-8",
                )
                (root / "config" / "example" / "model-overrides.json").write_text('{"models":{}}\n', encoding="utf-8")
                (root / "config" / "default" / "search-policy.json").write_text("{}\n", encoding="utf-8")
                (var_dir / "model-overrides.json").write_text('{"models":{}}\n', encoding="utf-8")
                (root / "proxy" / "searxng-capabilities.json").write_text("{}\n", encoding="utf-8")

                os.environ["QZ_ROOT"] = str(root)
                os.environ["QZ_VAR_DIR"] = str(var_dir)
                os.environ.pop("QZ_MODEL_OVERRIDES", None)
                os.environ["QZ_CAPTURE_MODE"] = "off"
                os.environ.pop("SEARXNG_POLICY", None)
                os.environ["SEARXNG_CAPABILITIES"] = str(root / "proxy" / "searxng-capabilities.json")

                payload = effective_config_payload()

                self.assertEqual(payload["schema"], EFFECTIVE_CONFIG_SCHEMA)
                paths = {item["name"]: item for item in payload["paths"]}
                self.assertEqual(paths["model_dir"]["classification"], "local_models")
                self.assertEqual(paths["model_overrides_default"]["state"], "file")
                self.assertEqual(paths["model_overrides_default"]["path"], str(root / "config" / "default" / "model-overrides.json"))
                self.assertEqual(paths["model_overrides_example"]["path"], str(root / "config" / "example" / "model-overrides.json"))
                self.assertEqual(paths["model_overrides_user"]["path"], str(root / "config" / "user" / "model-overrides.json"))
                self.assertEqual(paths["model_overrides_user"]["state"], "missing")
                self.assertNotIn("model_overrides_legacy_user", paths)
                self.assertEqual(paths["codex_model_catalog"]["source_layer"], "generated")
                self.assertEqual(paths["codex_config_template"]["path"], str(root / "config" / "example" / "codex-config.toml"))
                self.assertEqual(paths["benchmark_prompts_default"]["path"], str(root / "config" / "default" / "benchmark-prompts.json"))
                self.assertEqual(paths["searxng_policy"]["path"], str(root / "config" / "default" / "search-policy.json"))
                self.assertIn("prompt_file:system_prompt_file", paths)
                self.assertEqual(paths["prompt_file:system_prompt_file"]["state"], "missing")
                settings = {item["name"]: item for item in payload["settings"]}
                self.assertEqual(settings["capture_mode"]["active_value"], "off")
                self.assertEqual(settings["capture_mode"]["classification"], "debug_capture_policy")
                self.assertEqual(settings["capture_mode"]["source_layer"], "environment")
                self.assertEqual(payload["capture"]["mode"], "off")
                self.assertFalse(payload["capture"]["enabled"])
                self.assertEqual(payload["capture"]["state"], "disabled")
                self.assertTrue(any("captures are disabled" in item.get("warning", "") for item in payload["warnings"]))
                self.assertEqual(payload["prompt_files"]["schema"], "qz.prompt.files.v1")
                self.assertIn(str(root / "config" / "user" / "prompts" / "profile.md"), payload["prompt_files"]["missing"])
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_capture_mode_reports_normalized_env_policy(self):
        old_env = {name: os.environ.get(name) for name in (
            "QZ_ROOT",
            "QZ_VAR_DIR",
            "QZ_CAPTURE_MODE",
        )}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                var_dir.mkdir()
                os.environ["QZ_ROOT"] = str(root)
                os.environ["QZ_VAR_DIR"] = str(var_dir)
                os.environ["QZ_CAPTURE_MODE"] = "minimal"

                payload = effective_config_payload()

                settings = {item["name"]: item for item in payload["settings"]}
                self.assertEqual(settings["capture_mode"]["active_value"], "minimal")
                self.assertEqual(settings["capture_mode"]["env_value"], "minimal")
                self.assertEqual(payload["capture"]["mode"], "minimal")
                self.assertTrue(payload["capture"]["enabled"])
                self.assertTrue(payload["capture"]["write_latest"])
                self.assertTrue(payload["capture"]["write_request_scoped"])
                self.assertEqual(payload["capture"]["state"], "enabled")
                self.assertFalse(any("captures are disabled" in item.get("warning", "") for item in payload["warnings"]))
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
