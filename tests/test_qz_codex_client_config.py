"""Tests for proxy/qz_codex_client_config.py — #57 Slice C1."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_codex_client_config import (
    CODEX_CLIENT_CONFIG_SCHEMA,
    CODEX_ENV_KEY,
    CODEX_MODEL_CATALOG_FILENAME,
    CODEX_MODEL_PROVIDER,
    CODEX_WIRE_API,
    codex_client_config_payload,
    codex_model_catalog_content,
)

_ENV_KEYS = (
    "QZ_ROOT", "QZ_VAR_DIR", "QZ_PROXY_HOST", "QZ_PROXY_PORT", "CODEX_HOME",
    "LOCAL_QWEN_API_KEY",
)


def _save_env():
    return {k: os.environ.get(k) for k in _ENV_KEYS}


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class CodexClientConfigPayloadTests(unittest.TestCase):
    """Tests for codex_client_config_payload() — no catalog file present."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def _set_minimal_env(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("CODEX_HOME", None)
        return root, var_dir

    def test_schema_and_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["schema"], CODEX_CLIENT_CONFIG_SCHEMA)

    def test_model_provider_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            self.assertEqual(payload["model_provider"], CODEX_MODEL_PROVIDER)

    def test_provider_fields_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            provider = payload["provider"]
            self.assertIn("name", provider)
            self.assertIn("base_url", provider)
            self.assertIn("wire_api", provider)
            self.assertIn("env_key", provider)

    def test_base_url_uses_proxy_host_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            os.environ["QZ_PROXY_HOST"] = "192.168.1.50"
            os.environ["QZ_PROXY_PORT"] = "18180"
            payload = codex_client_config_payload()
            base_url = payload["provider"]["base_url"]
            self.assertIn("192.168.1.50", base_url)
            self.assertIn("18180", base_url)
            self.assertNotIn("127.0.0.1", base_url)

    def test_base_url_not_hardcoded_localhost(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            os.environ["QZ_PROXY_HOST"] = "10.0.0.1"
            os.environ["QZ_PROXY_PORT"] = "9999"
            payload = codex_client_config_payload()
            self.assertIn("10.0.0.1", payload["provider"]["base_url"])
            self.assertIn("9999", payload["provider"]["base_url"])

    def test_no_api_key_values_in_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            sentinel = "SENTINEL_SECRET_KEY_THAT_MUST_NOT_APPEAR"
            os.environ["LOCAL_QWEN_API_KEY"] = sentinel
            payload = codex_client_config_payload()
            payload_json = json.dumps(payload)
            self.assertNotIn(sentinel, payload_json)
            # env_key must be the name only
            self.assertEqual(payload["provider"]["env_key"], CODEX_ENV_KEY)

    def test_catalog_url_points_to_model_catalog_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            os.environ["QZ_PROXY_HOST"] = "192.168.1.50"
            os.environ["QZ_PROXY_PORT"] = "18180"
            payload = codex_client_config_payload()
            catalog_url = payload["model_catalog"]["url"]
            self.assertIn("/qz/codex/model-catalog", catalog_url)
            self.assertIn("192.168.1.50", catalog_url)

    def test_missing_catalog_adds_bounded_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            # Catalog file does not exist in tmp dir
            payload = codex_client_config_payload()
            warning_codes = [w.get("warning") for w in payload["warnings"]]
            self.assertIn("missing_codex_catalog", warning_codes)

    def test_missing_catalog_warning_has_remediation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            for w in payload["warnings"]:
                if w.get("warning") == "missing_codex_catalog":
                    self.assertIn("remediation", w)
                    # Post-Slice F: catalog-specific refresh endpoint
                    # (the explicit /qz/models/refresh path also still works
                    # but the catalog-only refresh is the preferred path).
                    self.assertEqual(w["remediation"], "POST /qz/codex/model-catalog/refresh")
                    break

    def test_missing_catalog_model_catalog_has_mode_url_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            mc = payload["model_catalog"]
            self.assertEqual(mc["mode"], "download")
            self.assertIn("url", mc)
            self.assertEqual(mc["local_filename"], CODEX_MODEL_CATALOG_FILENAME)

    def test_no_stack_trace_in_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            payload = codex_client_config_payload()
            payload_json = json.dumps(payload)
            self.assertNotIn("Traceback", payload_json)
            self.assertNotIn("Exception", payload_json)


class CodexClientConfigWithCatalogTests(unittest.TestCase):
    """Tests for codex_client_config_payload() when catalog file exists."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def test_sha256_and_mtime_present_when_catalog_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_dir = root / "var"
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            catalog_file = catalog_dir / CODEX_MODEL_CATALOG_FILENAME
            catalog_file.write_text('{"models":[]}\n', encoding="utf-8")
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ.pop("CODEX_HOME", None)

            payload = codex_client_config_payload()
            mc = payload["model_catalog"]
            self.assertIn("sha256_12", mc)
            self.assertIn("mtime_ms", mc)
            self.assertIsNotNone(mc["sha256_12"])
            self.assertEqual(len(mc["sha256_12"]), 12)
            self.assertIsInstance(mc["mtime_ms"], int)
            self.assertGreater(mc["mtime_ms"], 0)

    def test_no_missing_catalog_warning_when_catalog_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_dir = root / "var"
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text('{"models":[]}\n', encoding="utf-8")
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ.pop("CODEX_HOME", None)

            payload = codex_client_config_payload()
            warning_codes = [w.get("warning") for w in payload["warnings"]]
            self.assertNotIn("missing_codex_catalog", warning_codes)

    def test_no_prompt_or_file_content_in_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_dir = root / "var"
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            sentinel = "CATALOG_SENTINEL_CONTENT_XYZ"
            catalog = {"models": [{"slug": sentinel}]}
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text(
                json.dumps(catalog), encoding="utf-8"
            )
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ.pop("CODEX_HOME", None)

            payload = codex_client_config_payload()
            payload_json = json.dumps(payload)
            # client-config payload must not include the catalog content itself
            self.assertNotIn(sentinel, payload_json)


class CodexModelCatalogContentTests(unittest.TestCase):
    """Tests for codex_model_catalog_content()."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def _set_minimal_env(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("CODEX_HOME", None)
        return root, var_dir

    def test_returns_catalog_dict_when_catalog_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            catalog = {"models": [{"slug": "test-model", "display_name": "Test"}]}
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text(
                json.dumps(catalog), encoding="utf-8"
            )
            result, error = codex_model_catalog_content()
            self.assertIsNone(error)
            self.assertIsNotNone(result)
            self.assertEqual(result["models"][0]["slug"], "test-model")

    def test_missing_catalog_returns_none_and_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            result, error = codex_model_catalog_content()
            self.assertIsNone(result)
            self.assertEqual(error, "missing_codex_catalog")

    def test_malformed_catalog_returns_none_and_invalid_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_bytes(b"not json {{{{")
            result, error = codex_model_catalog_content()
            self.assertIsNone(result)
            self.assertEqual(error, "invalid_codex_catalog")

    def test_non_dict_catalog_returns_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text(
                "[]", encoding="utf-8"
            )
            result, error = codex_model_catalog_content()
            self.assertIsNone(result)
            self.assertEqual(error, "invalid_codex_catalog")


class CodexClientConfigWireApiTests(unittest.TestCase):
    """Tests for wire_api and env_key constants."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def test_wire_api_is_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_dir = root / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ.pop("CODEX_HOME", None)
            payload = codex_client_config_payload()
            self.assertEqual(payload["provider"]["wire_api"], CODEX_WIRE_API)

    def test_env_key_name_not_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            var_dir = root / "var"
            var_dir.mkdir(parents=True)
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(var_dir)
            os.environ.pop("CODEX_HOME", None)
            os.environ["LOCAL_QWEN_API_KEY"] = "sk-secret-value-must-never-appear"
            payload = codex_client_config_payload()
            self.assertEqual(payload["provider"]["env_key"], "LOCAL_QWEN_API_KEY")
            self.assertNotIn("sk-secret-value-must-never-appear", json.dumps(payload))


class CodexClientConfigC11AuditTests(unittest.TestCase):
    """Audit tests added in Slice C1.1."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def _set_minimal_env(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("CODEX_HOME", None)
        return root, var_dir

    def test_catalog_path_ignores_codex_home(self):
        """CODEX_HOME (client launcher concept) must not redirect server catalog path."""
        from proxy.qz_codex_client_config import _catalog_path
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            unrelated_codex_home = Path(tmp) / "unrelated-client-home"
            unrelated_codex_home.mkdir(parents=True)
            os.environ["CODEX_HOME"] = str(unrelated_codex_home)

            path = _catalog_path()

            # Must be under QZ_VAR_DIR/generated/codex, not under CODEX_HOME
            self.assertIn("var", str(path))
            self.assertNotIn("unrelated-client-home", str(path))

    def test_catalog_path_uses_qz_var_dir(self):
        """Server catalog path follows QZ_VAR_DIR, not CODEX_HOME."""
        from proxy.qz_codex_client_config import _catalog_path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom_var = root / "custom-var"
            custom_var.mkdir(parents=True)
            os.environ["QZ_ROOT"] = str(root)
            os.environ["QZ_VAR_DIR"] = str(custom_var)
            os.environ.pop("CODEX_HOME", None)

            path = _catalog_path()

            self.assertTrue(str(path).startswith(str(custom_var)))

    def test_codex_home_set_does_not_affect_client_config_catalog_metadata(self):
        """Catalog warning/metadata reflect QZ_VAR_DIR path, not CODEX_HOME path."""
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            # Write catalog under var_dir/generated/codex (server path)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text('{"models":[]}\n', encoding="utf-8")
            # Set CODEX_HOME to an unrelated path with no catalog
            unrelated = Path(tmp) / "client-home"
            unrelated.mkdir()
            os.environ["CODEX_HOME"] = str(unrelated)

            payload = codex_client_config_payload()

            # Should find the catalog (under var_dir, not CODEX_HOME)
            self.assertIn("sha256_12", payload["model_catalog"])
            self.assertIn("mtime_ms", payload["model_catalog"])
            warning_codes = [w.get("warning") for w in payload["warnings"]]
            self.assertNotIn("missing_codex_catalog", warning_codes)

    def test_catalog_url_uses_same_host_port_as_provider_base_url(self):
        """model_catalog.url and provider.base_url share the same host:port."""
        with tempfile.TemporaryDirectory() as tmp:
            self._set_minimal_env(tmp)
            os.environ["QZ_PROXY_HOST"] = "192.168.1.99"
            os.environ["QZ_PROXY_PORT"] = "9876"

            payload = codex_client_config_payload()
            provider_base = payload["provider"]["base_url"]
            catalog_url = payload["model_catalog"]["url"]

            # Both must use the same host and port
            self.assertIn("192.168.1.99:9876", provider_base)
            self.assertIn("192.168.1.99:9876", catalog_url)

    def test_client_config_does_not_include_catalog_content(self):
        """client-config payload contains only metadata, not the full catalog JSON."""
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            models_sentinel = "UNIQUE_MODELS_CONTENT_SHOULD_NOT_APPEAR_IN_CLIENT_CONFIG"
            catalog = {"models": [{"slug": models_sentinel}]}
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_text(
                json.dumps(catalog), encoding="utf-8"
            )

            payload = codex_client_config_payload()
            payload_json = json.dumps(payload)

            self.assertNotIn(models_sentinel, payload_json)
            # Confirm sha256_12 is present (metadata) without content
            self.assertIn("sha256_12", payload["model_catalog"])

    def test_large_catalog_gets_hash_skipped(self):
        """Catalog file >64 KiB gets hash_skipped: 'too_large' in client-config."""
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            # Write a catalog that is just over 64 KiB
            large_content = '{"models":' + '[{"slug":"x"}]' * 5000 + '}'
            large_bytes = large_content.encode("utf-8")
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_bytes(large_bytes)

            if len(large_bytes) <= 65536:
                self.skipTest("generated content was not large enough — test assumption wrong")

            payload = codex_client_config_payload()
            mc = payload["model_catalog"]

            self.assertIsNone(mc.get("sha256_12"))
            self.assertEqual(mc.get("hash_skipped"), "too_large")
            self.assertIn("mtime_ms", mc)

    def test_invalid_catalog_endpoint_returns_bounded_error(self):
        """Malformed catalog JSON gives error code, not exception or stack trace."""
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "generated" / "codex"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_bytes(b"not valid json {{{{")

            result, error = codex_model_catalog_content()
            self.assertIsNone(result)
            self.assertEqual(error, "invalid_codex_catalog")


# ---------------------------------------------------------------------------
# Post-Slice F: catalog freshness metadata
# ---------------------------------------------------------------------------

class CatalogFreshnessTests(unittest.TestCase):
    """Tests for the freshness block added to model_catalog payload."""

    def setUp(self):
        self._saved = _save_env()

    def tearDown(self):
        _restore_env(self._saved)

    def _set_env(self, tmp):
        root = Path(tmp)
        var_dir = root / "var"
        var_dir.mkdir(parents=True)
        os.environ["QZ_ROOT"] = str(root)
        os.environ["QZ_VAR_DIR"] = str(var_dir)
        os.environ.pop("CODEX_HOME", None)
        return root, var_dir

    def _write_inventory(self, var_dir, content="{}"):
        path = var_dir / "generated" / "model-inventory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_catalog(self, var_dir, content="{}"):
        path = var_dir / "generated" / "codex" / CODEX_MODEL_CATALOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_missing_catalog_marked_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_env(tmp)
            payload = codex_client_config_payload()
            freshness = payload["model_catalog"]["freshness"]
            self.assertTrue(freshness["stale"])
            self.assertEqual(freshness["reason"], "catalog_missing")
            self.assertIn("model-catalog/refresh", freshness["remediation"])

    def test_inventory_newer_than_catalog_marked_stale(self):
        import time as _time
        with tempfile.TemporaryDirectory() as tmp:
            _, var_dir = self._set_env(tmp)
            catalog_path = self._write_catalog(var_dir)
            # Make inventory newer than catalog by bumping its mtime forward
            inv_path = self._write_inventory(var_dir)
            future = _time.time() + 60
            os.utime(inv_path, (future, future))
            payload = codex_client_config_payload()
            freshness = payload["model_catalog"]["freshness"]
            self.assertTrue(freshness["stale"])
            self.assertEqual(freshness["reason"], "source_newer_than_catalog")

    def test_catalog_newer_than_inventory_not_stale(self):
        import time as _time
        with tempfile.TemporaryDirectory() as tmp:
            _, var_dir = self._set_env(tmp)
            inv_path = self._write_inventory(var_dir)
            catalog_path = self._write_catalog(var_dir)
            # Catalog newer than inventory
            future = _time.time() + 60
            os.utime(catalog_path, (future, future))
            payload = codex_client_config_payload()
            freshness = payload["model_catalog"]["freshness"]
            self.assertFalse(freshness["stale"])
            self.assertIsNotNone(freshness["catalog_age_seconds"])

    def test_refresh_url_in_model_catalog_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_env(tmp)
            payload = codex_client_config_payload()
            self.assertIn("refresh_url", payload["model_catalog"])
            self.assertIn("/qz/codex/model-catalog/refresh", payload["model_catalog"]["refresh_url"])

    def test_stale_catalog_warning_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._set_env(tmp)
            # Missing catalog → stale
            payload = codex_client_config_payload()
            codes = [w.get("warning") for w in payload["warnings"]]
            self.assertTrue(
                "stale_codex_catalog" in codes or "missing_codex_catalog" in codes,
                f"expected stale/missing warning; got {codes}",
            )


if __name__ == "__main__":
    unittest.main()
