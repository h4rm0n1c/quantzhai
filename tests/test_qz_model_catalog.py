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
from proxy.qz_backend import BackendResponse


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


class FakeBackend:
    def __init__(self, models):
        self.models = models
        self.load_calls = []

    def get_models(self):
        data = []
        for model_id, state in self.models.items():
            data.append({
                "id": model_id,
                "status": {
                    "value": state,
                    "args": ["--ctx-size=131072"],
                },
            })
        return {"data": data}

    def load_model(self, model_id, timeout=120):
        self.load_calls.append(model_id)
        raise AssertionError(f"loaded backend model should be reused without POST /models/load: {model_id}")


class FakeStatusBackend:
    def __init__(self):
        self.model_timeouts = []
        self.health_timeouts = []

    def get_models(self, timeout=30):
        self.model_timeouts.append(timeout)
        return {"data": []}

    def get_health(self, timeout=10):
        self.health_timeouts.append(timeout)
        return BackendResponse(status=0, content_type="application/json", data=b'{"status":"loading"}')


class FakeLoadHandler:
    model_load_timeout = 120.0
    model_load_state = "idle"
    model_load_error = None
    model_load_started_at = None
    model_load_finished_at = None
    model_load_model = None
    model_load_health = None

    def __init__(self, catalog, backend):
        self.catalog = catalog
        self.backend = backend
        self.telemetry = None

    def _model_catalog(self):
        return self.catalog

    def _backend(self):
        return self.backend


class ModelCatalogProfileValidationTests(unittest.TestCase):
    def setUp(self):
        FakeLoadHandler.model_load_state = "idle"
        FakeLoadHandler.model_load_error = None
        FakeLoadHandler.model_load_started_at = None
        FakeLoadHandler.model_load_finished_at = None
        FakeLoadHandler.model_load_model = None
        FakeLoadHandler.model_load_health = None

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

    def test_load_manifest_reads_config_user_not_var(self):
        """User overrides must be in config/user/model-overrides.json.
        var/model-overrides.json is no longer a fallback location."""
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

            # var/ path should NOT be read — manifest should be empty.
            manifest = load_manifest(root)
            self.assertNotIn("profile.gguf", manifest.get("models", {}))

            # config/user/ IS read.
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

    def test_profile_override_sets_default_reasoning_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "real-backend.gguf"
            _write_gguf(target)
            (model_dir / "roleplay.gguf").symlink_to(target)
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "roleplay.gguf": {
                        "label": "roleplay",
                        "default_reasoning_level": "low",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))
            profile, _ = catalog.resolve("roleplay")

            self.assertEqual(profile["default_reasoning_level"], "low")
            default_levels = [
                item["effort"]
                for item in profile["supported_reasoning_levels"]
                if item.get("default")
            ]
            self.assertEqual(default_levels, ["low"])

    def test_exact_profile_slug_beats_colliding_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "real-backend.gguf"
            _write_gguf(target)
            (model_dir / "example-roleplay.gguf").symlink_to(target)
            (model_dir / "prompt-compiler.gguf").symlink_to(target)
            overrides = root / "var" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "example-roleplay.gguf": {
                        "label": "prompt-compiler",
                    },
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                    },
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root, overrides))
            selected, reason = catalog.resolve("prompt-compiler")

            self.assertIsNotNone(selected)
            self.assertEqual(selected["key"], "prompt-compiler.gguf")
            self.assertEqual(reason, "matched prompt-compiler")

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

    def test_router_reuses_loaded_backend_without_marking_new_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            target = model_dir / "qwen-backend.gguf"
            _write_gguf(target)
            (model_dir / "caveman.gguf").symlink_to(target)
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "caveman.gguf": {
                        "label": "caveman",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            backend = FakeBackend({"qwen-backend": "loaded"})
            selected, reason = ModelRouter(FakeLoadHandler(catalog, backend)).resolve_model_selection("caveman")

            self.assertEqual(selected["key"], "caveman.gguf")
            self.assertEqual(selected["backend_id"], "qwen-backend")
            self.assertEqual(backend.load_calls, [])
            self.assertIsNone(FakeLoadHandler.model_load_started_at)
            self.assertIsNone(FakeLoadHandler.model_load_finished_at)
            self.assertEqual(FakeLoadHandler.model_load_state, "idle")

    def test_status_snapshot_uses_short_backend_probe_timeout(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_CONTROL_PLANE_BACKEND_TIMEOUT": "0.12"}, clear=False):
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "default.gguf")
            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            backend = FakeStatusBackend()
            handler = FakeLoadHandler(catalog, backend)

            snapshot = ModelRouter(handler).status_snapshot()

            self.assertFalse(snapshot["ready"])
            self.assertEqual(snapshot["health"]["status"], 0)
            self.assertEqual(backend.model_timeouts, [0.12])
            self.assertEqual(backend.health_timeouts, [0.12])

    def test_compact_error_payload_shape(self):
        payload = profile_backend_error_payload({
            "label": "prompt-compiler",
            "profile_error": "symlink target not found in scanned GGUF models: outside.gguf",
        })

        self.assertEqual(payload["error"], "profile backend missing")
        self.assertEqual(payload["profile"], "prompt-compiler")
        self.assertIn("restore the missing target GGUF", payload["fix"])


class MemoryDomainCatalogTests(unittest.TestCase):
    """Tests for memory_domain plumbing through model-overrides -> catalog entry."""

    def test_explicit_memory_domain_stored_on_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "prompt-compiler.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                        "memory_domain": "coding",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, _ = catalog.resolve("prompt-compiler")

            self.assertIsNotNone(entry)
            self.assertEqual(entry["memory_domain"], "coding")

    def test_missing_memory_domain_is_none_on_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "plain.gguf")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, _ = catalog.resolve("plain")

            self.assertIsNotNone(entry)
            self.assertIsNone(entry["memory_domain"])

    def test_two_profiles_sharing_same_memory_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "alpha.gguf")
            _write_gguf(model_dir / "beta.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "alpha.gguf": {"memory_domain": "coding"},
                    "beta.gguf":  {"memory_domain": "coding"},
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            alpha, _ = catalog.resolve("alpha")
            beta, _ = catalog.resolve("beta")

            self.assertEqual(alpha["memory_domain"], "coding")
            self.assertEqual(beta["memory_domain"], "coding")

    def test_different_profiles_different_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "coder.gguf")
            _write_gguf(model_dir / "roleplay.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "coder.gguf":   {"memory_domain": "coding"},
                    "roleplay.gguf": {"memory_domain": "roleplay"},
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            coder, _ = catalog.resolve("coder")
            rp, _ = catalog.resolve("roleplay")

            self.assertEqual(coder["memory_domain"], "coding")
            self.assertEqual(rp["memory_domain"], "roleplay")

    def test_memory_domain_exposed_in_v1_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "prompt-compiler.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "prompt-compiler.gguf": {
                        "label": "prompt-compiler",
                        "memory_domain": "coding",
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            v1 = catalog.to_v1_models()

            # entry_identity uses filename ("prompt-compiler.gguf") as the /v1/models id
            model = next(m for m in v1["data"] if m["id"] == "prompt-compiler.gguf")
            self.assertEqual(model["memory_domain"], "coding")

    def test_missing_memory_domain_is_none_in_v1_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "plain.gguf")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            v1 = catalog.to_v1_models()

            model = next(m for m in v1["data"] if m["id"] == "plain.gguf")
            self.assertIsNone(model["memory_domain"])

    def test_domains_list_not_accepted(self):
        """domains:[] must not be treated as a valid memory_domain — only singular string."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "multi.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "multi.gguf": {
                        "memory_domain": ["coding", "research"],
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, _ = catalog.resolve("multi")

            # A list value must not be stored as the memory_domain; fall back to None (isolated)
            self.assertIsNone(entry["memory_domain"])

    def test_whitespace_only_memory_domain_stored_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "ws.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {"ws.gguf": {"memory_domain": "   "}}
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, _ = catalog.resolve("ws")

            self.assertIsNone(entry["memory_domain"])

    def test_memory_domain_on_broken_symlink_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model_dir = root / "var" / "models"
            model_dir.mkdir(parents=True, exist_ok=True)
            _write_gguf(model_dir / "healthy.gguf")
            (model_dir / "broken.gguf").symlink_to(model_dir / "missing.gguf")
            overrides = root / "config" / "user" / "model-overrides.json"
            overrides.parent.mkdir(parents=True, exist_ok=True)
            overrides.write_text(json.dumps({
                "models": {
                    "broken.gguf": {"memory_domain": "coding"},
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            broken, _ = catalog.resolve("broken")

            self.assertIsNotNone(broken)
            self.assertFalse(broken["profile_valid"])
            self.assertEqual(broken["memory_domain"], "coding")


if __name__ == "__main__":
    unittest.main()
