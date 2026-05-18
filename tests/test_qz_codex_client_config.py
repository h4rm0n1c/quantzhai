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
                    self.assertEqual(w["remediation"], "POST /qz/models/refresh")
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
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
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
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
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
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
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
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
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
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
            catalog_dir.mkdir(parents=True)
            (catalog_dir / CODEX_MODEL_CATALOG_FILENAME).write_bytes(b"not json {{{{")
            result, error = codex_model_catalog_content()
            self.assertIsNone(result)
            self.assertEqual(error, "invalid_codex_catalog")

    def test_non_dict_catalog_returns_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, var_dir = self._set_minimal_env(tmp)
            catalog_dir = var_dir / "codex-home" / "model-catalogs"
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


if __name__ == "__main__":
    unittest.main()
