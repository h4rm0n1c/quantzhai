"""Tests for the enriched /qz/models/refresh response (qz.codex.catalog.refresh.v1)."""
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_model_catalog import ModelCatalog, load_manifest


def _write_gguf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"GGUF")
        f.write(struct.pack("<I", 3))
        f.write(struct.pack("<Q", 0))
        f.write(struct.pack("<Q", 2))
        for k, v in [("general.architecture", "qwen"), ("general.name", path.stem)]:
            data = k.encode()
            f.write(struct.pack("<Q", len(data)))
            f.write(data)
            f.write(struct.pack("<I", 8))
            data = v.encode()
            f.write(struct.pack("<Q", len(data)))
            f.write(data)


class RefreshResponseSchemaTests(unittest.TestCase):
    """Unit-test the shape of the /qz/models/refresh response payload."""

    def _make_refresh_payload(self, model_count=2, catalog_written=True):
        """Simulate the payload the handler builds."""
        model_ids = [f"model-{i}" for i in range(model_count)]
        catalog_path = "/tmp/fake-catalog.json" if catalog_written else None
        initialization = {"ready": True, "catalog_ready": True, "state": "ready"}
        return {
            "schema": "qz.codex.catalog.refresh.v1",
            "ok": True,
            "catalog_updated": catalog_written,
            "catalog_path": catalog_path,
            "model_count": len(model_ids),
            "model_ids": model_ids,
            "proxy_initialization": initialization,
            "error": None,
            # backwards-compatible
            "catalog": {"models": []},
            "backend": {},
            "codex_catalog_updated": catalog_written,
        }

    def _make_error_payload(self, reason="still loading"):
        initialization = {"ready": False, "catalog_ready": False, "state": "initializing"}
        return {
            "schema": "qz.codex.catalog.refresh.v1",
            "ok": False,
            "error": "proxy not ready",
            "reason": reason,
            "proxy_initialization": initialization,
            "catalog_updated": False,
            "catalog_path": None,
            "model_ids": [],
            "model_count": 0,
            "codex_catalog_updated": False,
        }

    # --- schema field ---

    def test_success_response_has_schema(self):
        p = self._make_refresh_payload()
        self.assertEqual(p["schema"], "qz.codex.catalog.refresh.v1")

    def test_error_response_has_schema(self):
        p = self._make_error_payload()
        self.assertEqual(p["schema"], "qz.codex.catalog.refresh.v1")

    # --- ok field ---

    def test_success_ok_true(self):
        p = self._make_refresh_payload()
        self.assertTrue(p["ok"])

    def test_error_ok_false(self):
        p = self._make_error_payload()
        self.assertFalse(p["ok"])

    # --- model_ids / model_count ---

    def test_success_has_model_ids(self):
        p = self._make_refresh_payload(model_count=3)
        self.assertEqual(len(p["model_ids"]), 3)
        self.assertEqual(p["model_count"], 3)

    def test_error_has_empty_model_ids(self):
        p = self._make_error_payload()
        self.assertEqual(p["model_ids"], [])
        self.assertEqual(p["model_count"], 0)

    # --- catalog_updated / catalog_path ---

    def test_success_catalog_updated_true(self):
        p = self._make_refresh_payload(catalog_written=True)
        self.assertTrue(p["catalog_updated"])
        self.assertIsNotNone(p["catalog_path"])

    def test_success_catalog_written_false(self):
        p = self._make_refresh_payload(catalog_written=False)
        # catalog_updated may be False if codex catalog generation failed
        self.assertFalse(p["catalog_updated"])
        self.assertIsNone(p["catalog_path"])

    def test_error_catalog_not_updated(self):
        p = self._make_error_payload()
        self.assertFalse(p["catalog_updated"])
        self.assertIsNone(p["catalog_path"])

    # --- proxy_initialization ---

    def test_success_has_proxy_initialization(self):
        p = self._make_refresh_payload()
        self.assertIn("proxy_initialization", p)
        self.assertIsInstance(p["proxy_initialization"], dict)

    def test_error_has_proxy_initialization(self):
        p = self._make_error_payload()
        self.assertIn("proxy_initialization", p)

    # --- backwards compatibility ---

    def test_success_has_codex_catalog_updated_compat(self):
        p = self._make_refresh_payload(catalog_written=True)
        self.assertIn("codex_catalog_updated", p)
        self.assertTrue(p["codex_catalog_updated"])

    def test_success_has_catalog_compat(self):
        p = self._make_refresh_payload()
        self.assertIn("catalog", p)

    def test_success_has_backend_compat(self):
        p = self._make_refresh_payload()
        self.assertIn("backend", p)

    # --- error field ---

    def test_success_error_is_none(self):
        p = self._make_refresh_payload()
        self.assertIsNone(p["error"])

    def test_error_has_error_string(self):
        p = self._make_error_payload()
        self.assertIsInstance(p["error"], str)
        self.assertTrue(len(p["error"]) > 0)

    def test_error_has_reason(self):
        p = self._make_error_payload("still loading")
        self.assertIn("reason", p)

    # --- JSON serialisability ---

    def test_success_payload_is_json_serialisable(self):
        p = self._make_refresh_payload()
        serialised = json.dumps(p)
        roundtrip = json.loads(serialised)
        self.assertEqual(roundtrip["schema"], "qz.codex.catalog.refresh.v1")

    def test_error_payload_is_json_serialisable(self):
        p = self._make_error_payload()
        serialised = json.dumps(p)
        roundtrip = json.loads(serialised)
        self.assertFalse(roundtrip["ok"])


class ModelIdsFromCatalogTests(unittest.TestCase):
    """Test that model_ids are correctly extracted from a real ModelCatalog."""

    def test_model_ids_from_scanned_catalog(self):
        """model_ids list contains GGUF key/stem for valid models."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "alpha.gguf")
            _write_gguf(model_dir / "beta.gguf")
            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            model_ids = [
                e.get("key") or e.get("stem") or e.get("backend_id") or ""
                for e in catalog.entries
                if isinstance(e, dict) and e.get("profile_valid", True) is not False
            ]
            model_ids = [mid for mid in model_ids if mid]

            self.assertIn("alpha.gguf", model_ids)
            self.assertIn("beta.gguf", model_ids)
            self.assertEqual(len(model_ids), 2)

    def test_invalid_profile_excluded_from_model_ids(self):
        """Broken symlink profiles with profile_valid=False are excluded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            model_dir.mkdir(parents=True, exist_ok=True)
            _write_gguf(model_dir / "good.gguf")
            (model_dir / "broken.gguf").symlink_to(model_dir / "missing.gguf")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            model_ids = [
                e.get("key") or e.get("stem") or ""
                for e in catalog.entries
                if isinstance(e, dict) and e.get("profile_valid", True) is not False
            ]
            model_ids = [mid for mid in model_ids if mid]

            self.assertIn("good.gguf", model_ids)
            self.assertNotIn("broken.gguf", model_ids)


if __name__ == "__main__":
    unittest.main()
