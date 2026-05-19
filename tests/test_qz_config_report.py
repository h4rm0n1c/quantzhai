import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_config_report import (
    EFFECTIVE_CONFIG_SCHEMA,
    _artifact_staleness_check,
    _file_meta,
    _prompt_file_records,
    effective_config_payload,
)


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


class GeneratedArtifactStalenessTests(unittest.TestCase):
    """Tests for _artifact_staleness_check() and staleness warnings — #5 Slice C."""

    _ENV_KEYS = (
        "QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES",
        "QZ_MODEL_INVENTORY_CACHE", "QZ_CAPTURE_MODE",
        "SEARXNG_POLICY", "SEARXNG_CAPABILITIES",
        "QZ_LOAD_EXAMPLE_MODEL_OVERRIDES",
    )

    def _save_env(self):
        return {k: os.environ.get(k) for k in self._ENV_KEYS}

    def _restore_env(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _minimal_root(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        (root / "config" / "default").mkdir(parents=True)
        (root / "config" / "example").mkdir(parents=True)
        (root / "proxy").mkdir()
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("QZ_MODEL_OVERRIDES", None)
        os.environ.pop("QZ_MODEL_INVENTORY_CACHE", None)
        os.environ.pop("QZ_CAPTURE_MODE", None)
        os.environ.pop("SEARXNG_POLICY", None)
        os.environ.pop("SEARXNG_CAPABILITIES", None)
        os.environ.pop("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", None)
        return root, var_dir

    # --- _artifact_staleness_check() unit tests ---

    def test_helper_returns_empty_when_artifact_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "missing.json"
            inp = root / "input.json"
            inp.write_text("{}", encoding="utf-8")
            result = _artifact_staleness_check(artifact, [inp])
            self.assertEqual(result, {})

    def test_helper_returns_empty_when_no_inputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.json"
            artifact.write_text("{}", encoding="utf-8")
            result = _artifact_staleness_check(artifact, [root / "missing.json"])
            self.assertEqual(result, {})

    def test_helper_returns_stale_when_input_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.json"
            inp = root / "input.json"
            artifact.write_text("{}", encoding="utf-8")
            inp.write_text("{}", encoding="utf-8")
            # Set artifact older than input
            old_time = 1_700_000_000.0
            new_time = 1_700_001_000.0
            os.utime(artifact, (old_time, old_time))
            os.utime(inp, (new_time, new_time))
            result = _artifact_staleness_check(artifact, [inp])
            self.assertIn("artifact_mtime_ms", result)
            self.assertIn("newest_input_mtime_ms", result)
            self.assertLess(result["artifact_mtime_ms"], result["newest_input_mtime_ms"])

    def test_helper_returns_empty_when_artifact_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.json"
            inp = root / "input.json"
            artifact.write_text("{}", encoding="utf-8")
            inp.write_text("{}", encoding="utf-8")
            new_time = 1_700_001_000.0
            old_time = 1_700_000_000.0
            os.utime(artifact, (new_time, new_time))
            os.utime(inp, (old_time, old_time))
            result = _artifact_staleness_check(artifact, [inp])
            self.assertEqual(result, {})

    # --- Integration tests via effective_config_payload() ---

    def _write_with_mtime(self, path, content, mtime):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def test_model_inventory_cache_stale_when_override_newer_than_inventory(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,  # newer
                )
                self._write_with_mtime(
                    var_dir / "generated" / "model-inventory.json",
                    '{"models":[]}', 1_700_000_000.0,  # older
                )
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertIn("stale_model_inventory_cache", warning_codes)
        finally:
            self._restore_env(saved)

    def test_model_inventory_cache_fresh_when_inventory_newer_than_overrides(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_000_000.0,  # older
                )
                self._write_with_mtime(
                    var_dir / "generated" / "model-inventory.json",
                    '{"models":[]}', 1_700_001_000.0,  # newer
                )
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertNotIn("stale_model_inventory_cache", warning_codes)
        finally:
            self._restore_env(saved)

    def test_model_inventory_cache_missing_produces_no_stale_warning(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,
                )
                # inventory does NOT exist
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertNotIn("stale_model_inventory_cache", warning_codes)
        finally:
            self._restore_env(saved)

    def test_codex_catalog_stale_when_inventory_newer_than_catalog(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_001_000.0)
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertIn("stale_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)

    def test_codex_catalog_fresh_when_catalog_newer_than_inventory(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_000_000.0)
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_001_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertNotIn("stale_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)

    def test_codex_catalog_missing_inventory_does_not_produce_stale_warning(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                # catalog exists but inventory is missing
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertNotIn("stale_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)

    def test_codex_config_stale_when_catalog_newer_than_config(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_001_000.0)
                self._write_with_mtime(var_dir / "codex-home" / "config.toml", 'x=1', 1_700_000_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertIn("stale_codex_config", warning_codes)
        finally:
            self._restore_env(saved)

    def test_codex_config_fresh_when_config_newer_than_catalog(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_000_000.0)
                self._write_with_mtime(var_dir / "codex-home" / "config.toml", 'x=1', 1_700_001_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertNotIn("stale_codex_config", warning_codes)
        finally:
            self._restore_env(saved)

    def test_staleness_warning_payload_bounded(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,
                )
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                stale_warns = [w for w in payload["warnings"] if w.get("warning") == "stale_model_inventory_cache"]
                self.assertEqual(len(stale_warns), 1)
                w = stale_warns[0]
                for key in ("warning", "path", "stale_against", "artifact_mtime_ms",
                            "newest_input_mtime_ms", "remediation"):
                    self.assertIn(key, w, f"missing key: {key}")
                self.assertEqual(w["remediation"], "POST /qz/models/refresh")
                self.assertIsInstance(w["stale_against"], list)
                # No file contents
                self.assertNotIn("content", w)
                self.assertNotIn("text", w)
        finally:
            self._restore_env(saved)

    def test_staleness_warning_does_not_promote_generated_artifact_to_authority(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_001_000.0)
                self._write_with_mtime(catalog_dir / "qwenzhai-models.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                self.assertEqual(paths["codex_model_catalog"]["source_layer"], "generated")
                self.assertEqual(paths["model_inventory_cache"]["source_layer"], "generated")
                self.assertEqual(paths["codex_config"]["source_layer"], "generated")
        finally:
            self._restore_env(saved)

    def test_models_refresh_not_called_by_effective_config(self):
        """effective_config_payload must not import or call ModelCatalog."""
        import proxy.qz_config_report as cr_module
        # qz_config_report should not import ModelCatalog or trigger any model scan
        self.assertFalse(hasattr(cr_module, "ModelCatalog"))
        self.assertFalse(hasattr(cr_module, "scan_models"))
        self.assertFalse(hasattr(cr_module, "write_cache"))

    def test_no_model_files_hashed(self):
        """model_dir is a directory record; GGUF files are not individually hashed."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                model_dir = var_dir / "models"
                model_dir.mkdir(parents=True)
                # Create a fake large GGUF to confirm it isn't hashed
                fake_gguf = model_dir / "fake-model.gguf"
                fake_gguf.write_bytes(b"GGUF" + b"\x00" * 1000)
                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                # model_dir record must be a directory, never a hashed file
                model_dir_rec = paths["model_dir"]
                self.assertIn(model_dir_rec["state"], ("dir", "missing"))
                self.assertNotIn("sha256_12", model_dir_rec)
                # No path record with gguf in name
                gguf_records = [r for r in payload["paths"] if ".gguf" in r.get("path", "")]
                self.assertEqual(gguf_records, [])
        finally:
            self._restore_env(saved)

    def test_stale_against_only_existing_default_override(self):
        """stale_against lists only existing inputs — missing user override excluded."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                # Only default override exists; user override is absent
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,
                )
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                stale_warns = [w for w in payload["warnings"] if w.get("warning") == "stale_model_inventory_cache"]
                self.assertEqual(len(stale_warns), 1)
                self.assertEqual(stale_warns[0]["stale_against"], ["model_overrides_default"])
        finally:
            self._restore_env(saved)

    def test_stale_against_includes_both_when_both_overrides_exist(self):
        """stale_against includes both names when both override files exist."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "user").mkdir(parents=True)
                self._write_with_mtime(
                    root / "config" / "default" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,
                )
                self._write_with_mtime(
                    root / "config" / "user" / "model-overrides.json",
                    '{"models":{}}', 1_700_001_000.0,
                )
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                stale_warns = [w for w in payload["warnings"] if w.get("warning") == "stale_model_inventory_cache"]
                self.assertEqual(len(stale_warns), 1)
                self.assertIn("model_overrides_default", stale_warns[0]["stale_against"])
                self.assertIn("model_overrides_user", stale_warns[0]["stale_against"])
        finally:
            self._restore_env(saved)

    def test_stale_against_only_existing_user_override(self):
        """stale_against lists only user override when default is absent."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "user").mkdir(parents=True)
                user_overrides = root / "config" / "user" / "model-overrides.json"
                self._write_with_mtime(user_overrides, '{"models":{}}', 1_700_001_000.0)
                # default override absent
                os.environ["QZ_MODEL_OVERRIDES"] = str(user_overrides)
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_000_000.0)
                payload = effective_config_payload()
                stale_warns = [w for w in payload["warnings"] if w.get("warning") == "stale_model_inventory_cache"]
                self.assertEqual(len(stale_warns), 1)
                self.assertEqual(stale_warns[0]["stale_against"], ["model_overrides_user"])
        finally:
            self._restore_env(saved)

    def test_missing_and_stale_are_separate_concepts(self):
        """A missing artifact gets missing_codex_catalog, not stale_codex_catalog."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                # inventory exists but catalog does not
                self._write_with_mtime(var_dir / "generated" / "model-inventory.json", '{}', 1_700_001_000.0)
                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]
                self.assertIn("missing_codex_catalog", warning_codes)
                self.assertNotIn("stale_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)


class ModelInventoryA1MigrationTests(unittest.TestCase):
    """Verify A1 migration: model_inventory_cache default is var/generated/ (#56 Slice C-impl)."""

    _ENV_KEYS = (
        "QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_INVENTORY_CACHE",
        "QZ_CAPTURE_MODE", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES",
    )

    def _save_env(self):
        return {k: os.environ.get(k) for k in self._ENV_KEYS}

    def _restore_env(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _minimal_root(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        (root / "config" / "default").mkdir(parents=True)
        (root / "proxy").mkdir()
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        for k in ("QZ_MODEL_INVENTORY_CACHE", "QZ_CAPTURE_MODE", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES"):
            os.environ.pop(k, None)
        return root, var_dir

    def test_config_effective_reports_generated_inventory_path(self):
        """model_inventory_cache default path is var/generated/model-inventory.json after A1 migration."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}

                rec = paths["model_inventory_cache"]
                expected = str(var_dir / "generated" / "model-inventory.json")
                self.assertEqual(rec["path"], expected)
                self.assertEqual(rec["default"], expected)
                self.assertEqual(rec["classification"], "generated_inventory")
                self.assertIn("generated", Path(rec["path"]).parts)
        finally:
            self._restore_env(saved)

    def test_qz_model_inventory_cache_override_still_wins(self):
        """QZ_MODEL_INVENTORY_CACHE env override takes precedence over the new default path."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                custom_path = root / "custom" / "my-inventory.json"
                os.environ["QZ_MODEL_INVENTORY_CACHE"] = str(custom_path)

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}

                rec = paths["model_inventory_cache"]
                self.assertEqual(rec["path"], str(custom_path))
                self.assertEqual(rec["env_value"], str(custom_path))
                self.assertEqual(rec["default"], str(var_dir / "generated" / "model-inventory.json"))
        finally:
            self._restore_env(saved)


class PromptFileSourceLabellingTests(unittest.TestCase):
    """Tests for prompt-file source labelling in /qz/config/effective — #5 Slice B."""

    _ENV_KEYS = (
        "QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES",
        "QZ_CAPTURE_MODE", "SEARXNG_POLICY", "SEARXNG_CAPABILITIES",
        "QZ_LOAD_EXAMPLE_MODEL_OVERRIDES",
    )

    def _save_env(self):
        return {k: os.environ.get(k) for k in self._ENV_KEYS}

    def _restore_env(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _minimal_root(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        (root / "config" / "default").mkdir(parents=True)
        (root / "config" / "example").mkdir(parents=True)
        (root / "proxy").mkdir()
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("QZ_MODEL_OVERRIDES", None)
        os.environ.pop("QZ_CAPTURE_MODE", None)
        os.environ.pop("SEARXNG_POLICY", None)
        os.environ.pop("SEARXNG_CAPABILITIES", None)
        os.environ.pop("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", None)
        return root, var_dir

    # --- _prompt_file_records() unit tests (direct) ---

    def test_prompt_ref_from_default_overrides_has_default_source_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "default").mkdir(parents=True)
            manifest = root / "config" / "default" / "model-overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"config/default/prompts/a.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(manifest): "default"}
            _records, summary = _prompt_file_records(root, [manifest], source_layers=source_layers)
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "default" / "prompts" / "a.md")
            self.assertIn(key, referenced)
            self.assertEqual(referenced[key]["source_layer"], "default")

    def test_prompt_ref_from_user_overrides_has_user_source_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "user").mkdir(parents=True)
            manifest = root / "config" / "user" / "model-overrides.json"
            manifest.write_text(
                '{"models":{"b.gguf":{"system_prompt_file":"config/user/prompts/b.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(manifest): "user"}
            _records, summary = _prompt_file_records(root, [manifest], source_layers=source_layers)
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "user" / "prompts" / "b.md")
            self.assertIn(key, referenced)
            self.assertEqual(referenced[key]["source_layer"], "user")

    def test_unknown_manifest_gets_unknown_source_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "some-other.json"
            manifest.write_text(
                '{"models":{"c.gguf":{"system_prompt_file":"config/default/prompts/c.md"}}}\n',
                encoding="utf-8",
            )
            # No source_layers provided
            _records, summary = _prompt_file_records(root, [manifest])
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "default" / "prompts" / "c.md")
            self.assertIn(key, referenced)
            self.assertEqual(referenced[key]["source_layer"], "unknown")

    def test_existing_prompt_file_appears_with_state_file_and_file_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / "default").mkdir(parents=True)
            prompt_dir = root / "config" / "default" / "prompts"
            prompt_dir.mkdir(parents=True)
            (prompt_dir / "a.md").write_text("# prompt\n", encoding="utf-8")
            manifest = root / "config" / "default" / "model-overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"config/default/prompts/a.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(manifest): "default"}
            _records, summary = _prompt_file_records(root, [manifest], source_layers=source_layers)
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "default" / "prompts" / "a.md")
            entry = referenced[key]
            self.assertEqual(entry["state"], "file")
            self.assertIn("mtime_ms", entry)
            self.assertIn("size_bytes", entry)
            self.assertIn("sha256_12", entry)
            self.assertIsNotNone(entry["sha256_12"])
            self.assertEqual(len(entry["sha256_12"]), 12)

    def test_missing_prompt_file_appears_with_state_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"config/user/prompts/missing.md"}}}\n',
                encoding="utf-8",
            )
            _records, summary = _prompt_file_records(root, [manifest])
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "user" / "prompts" / "missing.md")
            self.assertEqual(referenced[key]["state"], "missing")
            self.assertIn(key, summary["missing"])
            self.assertNotIn("mtime_ms", referenced[key])
            self.assertNotIn("size_bytes", referenced[key])

    def test_referenced_by_includes_field_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"config/user/prompts/a.md"}}}\n',
                encoding="utf-8",
            )
            _records, summary = _prompt_file_records(root, [manifest])
            referenced = {e["path"]: e for e in summary["referenced"]}
            key = str(root / "config" / "user" / "prompts" / "a.md")
            refs = referenced[key]["referenced_by"]
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]["field"], "system_prompt_file")
            self.assertEqual(refs[0]["source"], str(manifest))

    def test_same_prompt_referenced_by_two_manifests_deduplicates_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m1 = root / "default.json"
            m2 = root / "user.json"
            m1.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            m2.write_text(
                '{"models":{"b.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(m1): "default", str(m2): "user"}
            _records, summary = _prompt_file_records(root, [m1, m2], source_layers=source_layers)
            # Path appears only once in referenced
            keys = [e["path"] for e in summary["referenced"]]
            key = str(root / "shared" / "prompt.md")
            self.assertEqual(keys.count(key), 1)
            # But referenced_by has two entries
            entry = next(e for e in summary["referenced"] if e["path"] == key)
            self.assertEqual(len(entry["referenced_by"]), 2)
            sources = {r["source_layer"] for r in entry["referenced_by"]}
            self.assertEqual(sources, {"default", "user"})

    def test_prompt_over_64k_has_hash_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir(parents=True)
            big_prompt = prompt_dir / "big.md"
            big_prompt.write_bytes(b"x" * 65537)
            manifest = root / "overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"prompts/big.md"}}}\n',
                encoding="utf-8",
            )
            _records, summary = _prompt_file_records(root, [manifest])
            referenced = {e["path"]: e for e in summary["referenced"]}
            entry = referenced[str(big_prompt)]
            self.assertEqual(entry["state"], "file")
            self.assertIsNone(entry.get("sha256_12"))
            self.assertEqual(entry.get("hash_skipped"), "too_large")

    def test_prompt_content_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir(parents=True)
            sentinel = "PROMPT_SENTINEL_CONTENT_XYZ"
            (prompt_dir / "secret.md").write_text(f"# {sentinel}\n", encoding="utf-8")
            manifest = root / "overrides.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"prompts/secret.md"}}}\n',
                encoding="utf-8",
            )
            _records, summary = _prompt_file_records(root, [manifest])
            summary_json = json.dumps(summary)
            self.assertNotIn(sentinel, summary_json)

    # --- Integration tests via effective_config_payload() ---

    def test_prompt_files_referenced_key_present_in_payload(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"a.gguf":{"system_prompt_file":"config/default/prompts/a.md"}}}\n',
                    encoding="utf-8",
                )
                payload = effective_config_payload()
                self.assertIn("referenced", payload["prompt_files"])
                self.assertEqual(payload["prompt_files"]["schema"], "qz.prompt.files.v1")
        finally:
            self._restore_env(saved)

    def test_missing_prompt_warning_includes_referenced_by(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"a.gguf":{"system_prompt_file":"config/user/prompts/a.md"}}}\n',
                    encoding="utf-8",
                )
                payload = effective_config_payload()
                missing_warns = [w for w in payload["warnings"] if w.get("warning") == "missing_prompt_file"]
                self.assertTrue(len(missing_warns) >= 1)
                self.assertIn("referenced_by", missing_warns[0])
        finally:
            self._restore_env(saved)

    def test_source_layers_present_for_single_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "default.json"
            manifest.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"config/default/prompts/a.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(manifest): "default"}
            _records, summary = _prompt_file_records(root, [manifest], source_layers=source_layers)
            entry = summary["referenced"][0]
            self.assertIn("source_layers", entry)
            self.assertEqual(entry["source_layers"], ["default"])

    def test_source_layers_contains_all_origins_for_multi_manifest_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m1 = root / "default.json"
            m2 = root / "user.json"
            m1.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            m2.write_text(
                '{"models":{"b.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            source_layers = {str(m1): "default", str(m2): "user"}
            _records, summary = _prompt_file_records(root, [m1, m2], source_layers=source_layers)
            key = str(root / "shared" / "prompt.md")
            entry = next(e for e in summary["referenced"] if e["path"] == key)
            # source_layers contains both origins, sorted default-first
            self.assertEqual(entry["source_layers"], ["default", "user"])
            # source_layer still reflects first reference for backward compat
            self.assertEqual(entry["source_layer"], "default")
            # referenced_by still contains both detailed references
            self.assertEqual(len(entry["referenced_by"]), 2)

    def test_duplicate_field_source_in_same_manifest_does_not_duplicate_referenced_by(self):
        # Two model entries in the same manifest file reference the same prompt
        # with the same field. referenced_by should contain it only once.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "overrides.json"
            manifest.write_text(json.dumps({
                "models": {
                    "a.gguf": {"system_prompt_file": "shared/prompt.md"},
                    "b.gguf": {"system_prompt_file": "shared/prompt.md"},
                }
            }), encoding="utf-8")
            _records, summary = _prompt_file_records(root, [manifest])
            key = str(root / "shared" / "prompt.md")
            entry = next(e for e in summary["referenced"] if e["path"] == key)
            # Same (field, source) pair — should appear only once in referenced_by
            self.assertEqual(len(entry["referenced_by"]), 1)

    def test_different_fields_from_same_manifest_are_not_deduped(self):
        # Two different fields in the same manifest that point to the same path
        # are genuinely distinct references and should both appear in referenced_by.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "overrides.json"
            manifest.write_text(json.dumps({
                "models": {
                    "a.gguf": {
                        "system_prompt_file": "shared/prompt.md",
                        "base_instructions_file": "shared/prompt.md",
                    },
                }
            }), encoding="utf-8")
            _records, summary = _prompt_file_records(root, [manifest])
            key = str(root / "shared" / "prompt.md")
            entry = next(e for e in summary["referenced"] if e["path"] == key)
            fields = {r["field"] for r in entry["referenced_by"]}
            self.assertIn("system_prompt_file", fields)
            self.assertIn("base_instructions_file", fields)

    def test_source_layers_order_default_before_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            m_user = root / "user.json"
            m_default = root / "default.json"
            # User manifest is listed first
            m_user.write_text(
                '{"models":{"a.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            m_default.write_text(
                '{"models":{"b.gguf":{"system_prompt_file":"shared/prompt.md"}}}\n',
                encoding="utf-8",
            )
            # User comes first in paths but "default" should still sort before "user"
            source_layers = {str(m_user): "user", str(m_default): "default"}
            _records, summary = _prompt_file_records(
                root, [m_user, m_default], source_layers=source_layers
            )
            key = str(root / "shared" / "prompt.md")
            entry = next(e for e in summary["referenced"] if e["path"] == key)
            self.assertEqual(entry["source_layers"], ["default", "user"])

    def test_backward_compat_missing_list_still_present(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"a.gguf":{"system_prompt_file":"config/user/prompts/a.md"}}}\n',
                    encoding="utf-8",
                )
                payload = effective_config_payload()
                pf = payload["prompt_files"]
                # All legacy keys preserved
                self.assertIn("schema", pf)
                self.assertIn("loaded", pf)
                self.assertIn("missing", pf)
                self.assertIn("failed", pf)
                # Missing path still in legacy list
                missing_key = str(root / "config" / "user" / "prompts" / "a.md")
                self.assertIn(missing_key, pf["missing"])
        finally:
            self._restore_env(saved)


class SourceFileMetaTests(unittest.TestCase):
    """Tests for file metadata fields and source warnings — #5 Slice A."""

    _ENV_KEYS = (
        "QZ_ROOT", "QZ_VAR_DIR", "QZ_MODEL_OVERRIDES",
        "QZ_MODEL_INVENTORY_CACHE", "QZ_CAPTURE_MODE",
        "SEARXNG_POLICY", "SEARXNG_CAPABILITIES",
        "QZ_LOAD_EXAMPLE_MODEL_OVERRIDES",
    )

    def _save_env(self):
        return {k: os.environ.get(k) for k in self._ENV_KEYS}

    def _restore_env(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _minimal_root(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        (root / "config" / "default").mkdir(parents=True)
        (root / "config" / "example").mkdir(parents=True)
        (root / "proxy").mkdir()
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("QZ_MODEL_OVERRIDES", None)
        os.environ.pop("QZ_CAPTURE_MODE", None)
        os.environ.pop("SEARXNG_POLICY", None)
        os.environ.pop("SEARXNG_CAPABILITIES", None)
        os.environ.pop("QZ_LOAD_EXAMPLE_MODEL_OVERRIDES", None)
        return root, var_dir

    def test_existing_source_file_has_mtime_size_hash(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                overrides_path = root / "config" / "default" / "model-overrides.json"
                overrides_path.write_text('{"models":{}}\n', encoding="utf-8")

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                rec = paths["model_overrides_default"]

                self.assertEqual(rec["state"], "file")
                self.assertIn("mtime_ms", rec)
                self.assertIn("size_bytes", rec)
                self.assertIn("sha256_12", rec)
                self.assertIsInstance(rec["mtime_ms"], int)
                self.assertGreater(rec["mtime_ms"], 0)
                self.assertIsInstance(rec["size_bytes"], int)
                self.assertGreater(rec["size_bytes"], 0)
                self.assertIsNotNone(rec["sha256_12"])
                self.assertEqual(len(rec["sha256_12"]), 12)
        finally:
            self._restore_env(saved)

    def test_missing_file_has_no_mtime_or_size(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                rec = paths["model_overrides_user"]

                self.assertEqual(rec["state"], "missing")
                self.assertNotIn("mtime_ms", rec)
                self.assertNotIn("size_bytes", rec)
                self.assertNotIn("sha256_12", rec)
        finally:
            self._restore_env(saved)

    def test_sha256_12_is_twelve_chars_when_present(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"a.gguf":{"label":"test"}}}\n', encoding="utf-8"
                )

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                sha = paths["model_overrides_default"].get("sha256_12")

                self.assertIsNotNone(sha)
                self.assertEqual(len(sha), 12)
        finally:
            self._restore_env(saved)

    def test_no_full_file_content_in_record(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                sentinel = "SENTINEL_CONTENT_SHOULD_NOT_APPEAR"
                (root / "config" / "default" / "model-overrides.json").write_text(
                    f'{{"models":{{"secret":"{sentinel}"}}}}\n', encoding="utf-8"
                )

                payload = effective_config_payload()
                payload_json = json.dumps(payload)

                self.assertNotIn(sentinel, payload_json)
        finally:
            self._restore_env(saved)

    def test_missing_user_overrides_warns_when_env_var_set(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                missing_path = root / "config" / "user" / "my-overrides.json"
                os.environ["QZ_MODEL_OVERRIDES"] = str(missing_path)

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertIn("missing_user_model_overrides", warning_codes)
        finally:
            self._restore_env(saved)

    def test_present_user_overrides_do_not_warn_even_with_env_var(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                user_dir = root / "config" / "user"
                user_dir.mkdir(parents=True)
                overrides_path = user_dir / "model-overrides.json"
                overrides_path.write_text('{"models":{}}\n', encoding="utf-8")
                os.environ["QZ_MODEL_OVERRIDES"] = str(overrides_path)

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertNotIn("missing_user_model_overrides", warning_codes)
        finally:
            self._restore_env(saved)

    def test_missing_prompt_file_appears_in_toplevel_warnings(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                # A default overrides file that references a prompt file that won't exist.
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{"a.gguf":{"system_prompt_file":"config/user/prompts/a.md"}}}\n',
                    encoding="utf-8",
                )

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertIn("missing_prompt_file", warning_codes)
        finally:
            self._restore_env(saved)

    def test_missing_codex_catalog_warns(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertIn("missing_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)

    def test_missing_codex_config_warns(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertIn("missing_codex_config", warning_codes)
        finally:
            self._restore_env(saved)

    def test_present_codex_catalog_does_not_warn(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                catalog_dir = var_dir / "codex-home" / "model-catalogs"
                catalog_dir.mkdir(parents=True)
                (catalog_dir / "qwenzhai-models.json").write_text('{"models":[]}\n', encoding="utf-8")

                payload = effective_config_payload()
                warning_codes = [w.get("warning") for w in payload["warnings"]]

                self.assertNotIn("missing_codex_catalog", warning_codes)
        finally:
            self._restore_env(saved)

    def test_generated_paths_classified_as_generated(self):
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}

                self.assertEqual(paths["codex_model_catalog"]["source_layer"], "generated")
                self.assertEqual(paths["codex_config"]["source_layer"], "generated")
                self.assertEqual(paths["model_inventory_cache"]["source_layer"], "generated")
        finally:
            self._restore_env(saved)

    def test_file_at_hash_boundary_is_hashed(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"x" * 65536)
            path = Path(f.name)
        try:
            meta = _file_meta(path)
            self.assertIsNotNone(meta.get("sha256_12"), "file at boundary should be hashed")
            self.assertEqual(len(meta["sha256_12"]), 12)
            self.assertNotIn("hash_skipped", meta)
        finally:
            path.unlink(missing_ok=True)

    def test_file_over_hash_boundary_is_not_hashed(self):
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"x" * 65537)
            path = Path(f.name)
        try:
            meta = _file_meta(path)
            self.assertIsNone(meta.get("sha256_12"), "file over boundary must not be hashed")
            self.assertEqual(meta.get("hash_skipped"), "too_large")
        finally:
            path.unlink(missing_ok=True)

    def test_existing_keys_unchanged(self):
        """Existing _record() fields remain present after file-meta extension."""
        saved = self._save_env()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root, var_dir = self._minimal_root(tmp)
                (root / "config" / "default" / "model-overrides.json").write_text(
                    '{"models":{}}\n', encoding="utf-8"
                )

                payload = effective_config_payload()
                paths = {item["name"]: item for item in payload["paths"]}
                rec = paths["model_overrides_default"]

                for key in ("name", "path", "state", "source_layer", "classification",
                            "env_var", "env_value", "default", "active", "note"):
                    self.assertIn(key, rec, f"existing key missing: {key}")
        finally:
            self._restore_env(saved)


if __name__ == "__main__":
    unittest.main()
