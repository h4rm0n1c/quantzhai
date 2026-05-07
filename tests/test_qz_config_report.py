import os
import tempfile
import unittest
from pathlib import Path

from proxy.qz_config_report import EFFECTIVE_CONFIG_SCHEMA, effective_config_payload


class EffectiveConfigReportTests(unittest.TestCase):
    def test_report_classifies_current_paths(self):
        old_env = {name: os.environ.get(name) for name in (
            "QZ_ROOT",
            "QZ_VAR_DIR",
            "QZ_MODEL_DIR",
            "QZ_MODEL_OVERRIDES",
            "QZ_MODEL_INVENTORY_CACHE",
            "SEARXNG_POLICY",
            "SEARXNG_CAPABILITIES",
        )}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config").mkdir()
                (root / "proxy").mkdir()
                (var_dir / "models").mkdir(parents=True)
                (root / "config" / "qz-model-overrides.default.json").write_text(
                    '{"models":{"profile.gguf":{"system_prompt_file":"config/user/prompts/profile.md"}}}\n',
                    encoding="utf-8",
                )
                (var_dir / "model-overrides.json").write_text('{"models":{}}\n', encoding="utf-8")
                (root / "proxy" / "searxng-capabilities.json").write_text("{}\n", encoding="utf-8")

                os.environ["QZ_ROOT"] = str(root)
                os.environ["QZ_VAR_DIR"] = str(var_dir)
                os.environ.pop("SEARXNG_POLICY", None)
                os.environ["SEARXNG_CAPABILITIES"] = str(root / "proxy" / "searxng-capabilities.json")

                payload = effective_config_payload()

                self.assertEqual(payload["schema"], EFFECTIVE_CONFIG_SCHEMA)
                paths = {item["name"]: item for item in payload["paths"]}
                self.assertEqual(paths["model_dir"]["classification"], "local_models")
                self.assertEqual(paths["model_overrides_default"]["state"], "file")
                self.assertEqual(paths["model_overrides_user"]["state"], "file")
                self.assertEqual(paths["codex_model_catalog"]["source_layer"], "generated")
                self.assertIn("prompt_file:system_prompt_file", paths)
                self.assertEqual(paths["prompt_file:system_prompt_file"]["state"], "missing")
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
