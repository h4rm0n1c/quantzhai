import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_config_report import EFFECTIVE_CONFIG_SCHEMA, _file_meta, effective_config_payload


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
