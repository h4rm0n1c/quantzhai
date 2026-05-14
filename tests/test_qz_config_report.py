import json
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


class MemoryDomainReportTests(unittest.TestCase):
    """Tests for memory_domains section in the effective config report."""

    def _setup_env(self, root, var_dir):
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("QZ_MODEL_OVERRIDES", None)
        os.environ.pop("SEARXNG_POLICY", None)
        os.environ.pop("SEARXNG_CAPABILITIES", None)

    def test_memory_domains_section_present_in_payload(self):
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                self.assertIn("memory_domains", payload)
                md = payload["memory_domains"]
                self.assertEqual(md["schema"], "qz.memory.domains.v1")
                self.assertIn("profiles", md)
                self.assertIn("note", md)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_explicit_memory_domain_appears_in_report(self):
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                user_dir = root / "config" / "user"
                user_dir.mkdir(parents=True)
                (user_dir / "model-overrides.json").write_text(json.dumps({
                    "models": {
                        "prompt-compiler.gguf": {"memory_domain": "coding"},
                        "roleplay-character.gguf": {"memory_domain": "roleplay"},
                    }
                }), encoding="utf-8")
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                self.assertEqual(md["profiles"].get("prompt-compiler.gguf"), "coding")
                self.assertEqual(md["profiles"].get("roleplay-character.gguf"), "roleplay")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_profile_without_memory_domain_absent_from_report(self):
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                user_dir = root / "config" / "user"
                user_dir.mkdir(parents=True)
                (user_dir / "model-overrides.json").write_text(json.dumps({
                    "models": {
                        "plain.gguf": {"label": "plain"},
                    }
                }), encoding="utf-8")
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                self.assertNotIn("plain.gguf", md["profiles"])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_two_profiles_sharing_domain_both_appear_in_report(self):
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                user_dir = root / "config" / "user"
                user_dir.mkdir(parents=True)
                (user_dir / "model-overrides.json").write_text(json.dumps({
                    "models": {
                        "alpha.gguf": {"memory_domain": "coding"},
                        "beta.gguf":  {"memory_domain": "coding"},
                    }
                }), encoding="utf-8")
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                self.assertEqual(md["profiles"].get("alpha.gguf"), "coding")
                self.assertEqual(md["profiles"].get("beta.gguf"), "coding")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_memory_domains_report_contains_no_memory_contents(self):
        """The report is config/policy only — no state, no memory contents."""
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                forbidden_keys = {"contents", "memories", "facts", "state", "store", "db"}
                self.assertTrue(forbidden_keys.isdisjoint(md.keys()),
                                f"memory_domains report must not expose state: {md.keys()}")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class ProfilesV1ConfigReportTests(unittest.TestCase):
    """Tests for qz.profiles.v1 paths and memory domain reporting in effective config."""

    def _setup_env(self, root, var_dir):
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("QZ_MODEL_OVERRIDES", None)
        os.environ.pop("SEARXNG_POLICY", None)
        os.environ.pop("SEARXNG_CAPABILITIES", None)

    def test_effective_config_includes_profiles_v1_paths(self):
        """profiles_default and profiles_user path records are present in the payload."""
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                paths = {item["name"]: item for item in payload["paths"]}
                self.assertIn("profiles_default", paths)
                self.assertIn("profiles_user", paths)
                self.assertIn("profiles_default_dir", paths)
                self.assertIn("profiles_user_dir", paths)
                self.assertEqual(paths["profiles_default"]["source_layer"], "tracked_default")
                self.assertEqual(paths["profiles_user"]["source_layer"], "user_override")
                self.assertEqual(paths["profiles_default"]["classification"], "source_config")
                self.assertEqual(paths["profiles_user"]["classification"], "local_config")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_memory_domain_report_reads_v1_profiles(self):
        """User profiles.json with memory.domain entries appear in the memory_domains report."""
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                user_dir = root / "config" / "user"
                user_dir.mkdir(parents=True)
                (user_dir / "profiles.json").write_text(json.dumps({
                    "schema": "qz.profiles.v1",
                    "profiles": {
                        "coder": {
                            "backend": {"gguf": "coder.gguf"},
                            "memory": {"domain": "coding"},
                            "metadata": {"label": "coder"}
                        },
                        "writer": {
                            "backend": {"gguf": "writer.gguf"},
                            "metadata": {"label": "writer"}
                        }
                    }
                }), encoding="utf-8")
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                self.assertEqual(md["profiles"].get("coder.gguf"), "coding")
                self.assertNotIn("writer.gguf", md["profiles"])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_memory_domain_report_handles_v1_dir_profiles(self):
        """User profiles/*.json with memory.domain entries appear in the memory_domains report."""
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                profiles_dir = root / "config" / "user" / "profiles"
                profiles_dir.mkdir(parents=True)
                (profiles_dir / "roleplay.json").write_text(json.dumps({
                    "schema": "qz.profiles.v1",
                    "profiles": {
                        "amber": {
                            "backend": {"gguf": "amber.gguf"},
                            "memory": {"domain": "roleplay"},
                            "metadata": {"label": "amber"}
                        }
                    }
                }), encoding="utf-8")
                (profiles_dir / "coder.json").write_text(json.dumps({
                    "schema": "qz.profiles.v1",
                    "profiles": {
                        "coder": {
                            "backend": {"gguf": "coder.gguf"},
                            "memory": {"domain": "coding"},
                            "metadata": {"label": "coder"}
                        }
                    }
                }), encoding="utf-8")
                self._setup_env(root, var_dir)

                payload = effective_config_payload()

                md = payload["memory_domains"]
                self.assertEqual(md["profiles"].get("amber.gguf"), "roleplay")
                self.assertEqual(md["profiles"].get("coder.gguf"), "coding")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_profiles_example_path_active_only_with_env_flag(self):
        """profiles_example path record is active only when QZ_LOAD_EXAMPLE_MODEL_OVERRIDES is set."""
        old_env = {k: os.environ.get(k) for k in ("QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES", "QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES")}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                var_dir = root / "var"
                (root / "config" / "default").mkdir(parents=True)
                (root / "proxy").mkdir()
                self._setup_env(root, var_dir)
                os.environ.pop("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", None)

                payload = effective_config_payload()

                paths = {item["name"]: item for item in payload["paths"]}
                self.assertFalse(paths["profiles_example"]["active"])
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
