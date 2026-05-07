import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_model_catalog import ModelCatalog, load_manifest
from proxy.qz_model_router import ModelRouter, profile_backend_error_payload


def _write_string(handle, value):
    data = value.encode("utf-8")
    handle.write(struct.pack("<Q", len(data)))
    handle.write(data)


def _write_metadata_string(handle, key, value):
    _write_string(handle, key)
    handle.write(struct.pack("<I", 8))
    _write_string(handle, value)


def _write_gguf(path, name=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(b"GGUF")
        handle.write(struct.pack("<I", 3))
        handle.write(struct.pack("<Q", 0))
        handle.write(struct.pack("<Q", 2))
        _write_metadata_string(handle, "general.architecture", "qwen")
        _write_metadata_string(handle, "general.name", name or path.stem)


class FakeHandler:
    def __init__(self, catalog):
        self.catalog = catalog
        self.telemetry = None

    def _model_catalog(self):
        return self.catalog

    def _backend(self):
        raise AssertionError("invalid profile selection must not touch backend")


class ModelCatalogProfileValidationTests(unittest.TestCase):
    def test_load_manifest_uses_config_default_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config" / "default" / "model-overrides.json"
            default_path.parent.mkdir(parents=True, exist_ok=True)
            default_path.write_text(json.dumps({
                "default_key": "profile.gguf",
                "models": {
                    "profile.gguf": {
                        "label": "profile",
                    }
                },
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertEqual(manifest["default_key"], "profile.gguf")
            self.assertEqual(manifest["models"]["profile.gguf"]["label"], "profile")

    def test_load_manifest_prefers_config_user_then_legacy_var(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QZ_MODEL_OVERRIDES", None)
            root = Path(tmp)
            user_path = root / "config" / "user" / "model-overrides.json"
            legacy_path = root / "var" / "model-overrides.json"
            user_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(json.dumps({
                "models": {"profile.gguf": {"label": "legacy"}},
            }), encoding="utf-8")

            manifest = load_manifest(root)
            self.assertEqual(manifest["models"]["profile.gguf"]["label"], "legacy")

            user_path.write_text(json.dumps({
                "models": {"profile.gguf": {"label": "user"}},
            }), encoding="utf-8")

            manifest = load_manifest(root)
            self.assertEqual(manifest["models"]["profile.gguf"]["label"], "user")

    def test_load_manifest_keeps_legacy_default_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = root / "config" / "qz-model-overrides.default.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(json.dumps({
                "default_key": "legacy.gguf",
                "models": {
                    "legacy.gguf": {
                        "label": "legacy",
                    }
                },
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertEqual(manifest["default_key"], "legacy.gguf")
            self.assertEqual(manifest["models"]["legacy.gguf"]["label"], "legacy")

    def test_last_selected_profile_wins_over_alphabetical_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "z-backend.gguf"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(target)
            (model_dir / "prompt-compiler.gguf").symlink_to(target)
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "selected_key": "prompt-compiler.gguf",
                "selected_backend_id": "z-backend",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            self.assertEqual(catalog.selected["key"], "prompt-compiler.gguf")
            self.assertEqual(catalog.selected["backend_id"], "z-backend")
            self.assertEqual(catalog.reason, "last_selected=prompt-compiler.gguf")

    def test_explicit_query_wins_over_last_selected_profile(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "z-backend.gguf"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(target)
            (model_dir / "prompt-compiler.gguf").symlink_to(target)
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "selected_key": "prompt-compiler.gguf",
                "selected_backend_id": "z-backend",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            selected, reason = catalog.resolve("a-backend")

            self.assertEqual(selected["key"], "a-backend.gguf")
            self.assertEqual(reason, "matched a-backend")

    def test_invalid_last_selected_falls_back_to_manifest_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(model_dir / "z-backend.gguf")
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "default_key": "z-backend.gguf",
                "models": {},
            }), encoding="utf-8")
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "selected_key": "missing-profile.gguf",
                "selected_backend_id": "missing-backend",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))

            self.assertEqual(catalog.selected["key"], "z-backend.gguf")
            self.assertEqual(catalog.reason, "default_key=z-backend.gguf")

    def test_symlink_profile_routes_to_target_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "real-backend.gguf"
            _write_gguf(target)
            (model_dir / "prompt-compiler.gguf").symlink_to(target)
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))
            profile, _ = catalog.resolve("prompt-compiler")

            self.assertIsNotNone(profile)
            self.assertTrue(profile["profile_valid"])
            self.assertTrue(profile["profile_symlink"])
            self.assertEqual(profile["backend_target"], "real-backend")
            self.assertEqual(profile["backend_id"], "real-backend")
            self.assertEqual(profile["stem"], "prompt-compiler")

            backend, _ = catalog.resolve("real-backend")
            self.assertIsNotNone(backend)
            self.assertEqual(backend["stem"], "real-backend")

    def test_symlink_target_outside_scanned_models_marks_profile_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            external = root / "external" / "outside.gguf"
            _write_gguf(external)
            _write_gguf(model_dir / "healthy.gguf")
            (model_dir / "prompt-compiler.gguf").symlink_to(external)
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))
            profile, _ = catalog.resolve("prompt-compiler")

            self.assertIsNotNone(profile)
            self.assertFalse(profile["profile_valid"])
            self.assertEqual(profile["backend_target"], "")
            self.assertIn("outside.gguf", profile["profile_error"])
            self.assertEqual(catalog.selected["key"], "healthy.gguf")

    def test_broken_symlink_profile_is_visible_as_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            model_dir.mkdir(parents=True, exist_ok=True)
            _write_gguf(model_dir / "healthy.gguf")
            (model_dir / "prompt-compiler.gguf").symlink_to(model_dir / "missing.gguf")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            profile, _ = catalog.resolve("prompt-compiler")

            self.assertIsNotNone(profile)
            self.assertFalse(profile["profile_valid"])
            self.assertTrue(profile["profile_symlink"])
            self.assertEqual(profile["backend_target"], "")
            self.assertIn("missing.gguf", profile["profile_error"])
            self.assertEqual(catalog.selected["key"], "healthy.gguf")

    def test_router_returns_compact_invalid_profile_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            external = root / "external" / "outside.gguf"
            _write_gguf(external)
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "prompt-compiler.gguf").symlink_to(external)
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))
            selected, reason = ModelRouter(FakeHandler(catalog)).resolve_model_selection("prompt-compiler")

            self.assertIsNone(selected)
            self.assertEqual(reason["error"], "profile backend missing")
            self.assertEqual(reason["profile"], "prompt-compiler")
            self.assertNotIn("models", reason)
            self.assertNotIn("catalog", reason)

    def test_router_rejects_removed_synthetic_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "healthy.gguf")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            selected, reason = ModelRouter(FakeHandler(catalog)).resolve_model_selection("Qwen3.6Turbo-high")

            self.assertIsNone(selected)
            self.assertEqual(reason, "no match for Qwen3.6Turbo-high")

    def test_compact_error_payload_shape(self):
        payload = profile_backend_error_payload({
            "label": "prompt-compiler",
            "profile_error": "symlink target not found in scanned GGUF models: outside.gguf",
        })

        self.assertEqual(payload["error"], "profile backend missing")
        self.assertEqual(payload["profile"], "prompt-compiler")
        self.assertIn("restore the missing target GGUF", payload["fix"])


if __name__ == "__main__":
    unittest.main()
