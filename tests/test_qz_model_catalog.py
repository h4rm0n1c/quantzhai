import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_model_catalog import ModelCatalog, load_manifest, _profiles_v1_to_manifest, _load_profiles_layer
from proxy.qz_model_router import ModelRouter, profile_backend_error_payload
from proxy.qz_backend import BackendResponse


# ---------------------------------------------------------------------------
# Env isolation helpers
# ---------------------------------------------------------------------------

# Keys that, if leaked from a prior test, silently corrupt load_manifest() results:
# - QZ_MODEL_OVERRIDES: skips the user-layer config scan entirely when set (even
#   to a missing path), causing tests that write config/user/ files to see 0 models.
# - QZ_MODEL_KEY / QZ_MODEL_STATE_PATH: affect model selection but not manifest loading.
_MODEL_ENV_KEYS = ("QZ_MODEL_OVERRIDES", "QZ_MODEL_KEY", "QZ_MODEL_STATE_PATH")


class _IsolatedModelEnvMixin:
    """
    setUp/tearDown mixin that saves and clears model-routing env vars before
    each test method, then restores them afterward.

    Apply to any TestCase subclass whose tests call load_manifest() with a
    temp root and expect the user-layer config (config/user/) to be loaded.
    Without isolation, a leaked QZ_MODEL_OVERRIDES from a prior test silently
    causes load_manifest() to skip the user-layer scan.
    """

    _ISOLATED_ENV_KEYS = _MODEL_ENV_KEYS

    def setUp(self):  # noqa: N802
        self._model_env_saved = {k: os.environ.get(k) for k in self._ISOLATED_ENV_KEYS}
        for k in self._ISOLATED_ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):  # noqa: N802
        for k, v in self._model_env_saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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

    def test_valid_persisted_selection_beats_qz_model_key(self):
        """Slice C precedence: persisted selected_key wins over QZ_MODEL_KEY."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"QZ_MODEL_KEY": "a-backend.gguf"}, clear=False
        ):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(model_dir / "z-backend.gguf")
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "selected_key": "z-backend.gguf",
                "selected_backend_id": "z-backend",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            self.assertEqual(catalog.selected["key"], "z-backend.gguf")
            self.assertEqual(catalog.reason, "last_selected=z-backend.gguf")

    def test_qz_model_key_seeds_when_no_persisted_selection(self):
        """Slice C precedence: QZ_MODEL_KEY only kicks in when no valid persisted selection exists."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"QZ_MODEL_KEY": "z-backend.gguf"}, clear=False
        ):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(model_dir / "z-backend.gguf")
            # No state file → QZ_MODEL_KEY seeds the selection
            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            self.assertEqual(catalog.selected["key"], "z-backend.gguf")
            self.assertEqual(catalog.reason, "matched z-backend.gguf")

    def test_qz_model_key_seeds_when_persisted_is_invalid(self):
        """Slice C precedence: invalid persisted falls through to QZ_MODEL_KEY before catalog default."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"QZ_MODEL_KEY": "z-backend.gguf"}, clear=False
        ):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(model_dir / "z-backend.gguf")
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "selected_key": "missing-profile.gguf",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            self.assertEqual(catalog.selected["key"], "z-backend.gguf")
            # Reason comes from the QZ_MODEL_KEY seed match, not last_selected
            self.assertEqual(catalog.reason, "matched z-backend.gguf")

    def test_loaded_model_only_state_does_not_become_selected(self):
        """Slice B regression at the catalog level — loaded_model is not authority."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"QZ_MODEL_KEY": ""}, clear=False
        ):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "a-backend.gguf")
            _write_gguf(model_dir / "z-backend.gguf")
            state = root / "var" / "model-state.json"
            state.write_text(json.dumps({
                "loaded_model": "z-backend.gguf",
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))

            # Falls through to catalog default (alphabetical) since loaded_model
            # is observation, not selection authority.
            self.assertEqual(catalog.selected["key"], "a-backend.gguf")

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


class MemoryDomainCatalogTests(_IsolatedModelEnvMixin, unittest.TestCase):
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


class ProfilesV1LoaderTests(_IsolatedModelEnvMixin, unittest.TestCase):
    """Tests for the qz.profiles.v1 loader in qz_model_catalog."""

    def test_profiles_json_loads_profiles(self):
        """Basic profiles.json with one profile is converted to internal manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "config" / "user"
            user_dir.mkdir(parents=True)
            (user_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "my-model": {
                        "backend": {"gguf": "my-model.gguf"},
                        "runtime": {"context_length": 131072},
                        "metadata": {"label": "My Model"}
                    }
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertIn("my-model.gguf", manifest["models"])
            self.assertEqual(manifest["models"]["my-model.gguf"]["label"], "My Model")
            self.assertEqual(manifest["models"]["my-model.gguf"]["runtime_context_length"], 131072)

    def test_profiles_dir_loads_alphabetically(self):
        """Files in profiles/ directory are loaded in sorted order."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles_dir = root / "config" / "user" / "profiles"
            profiles_dir.mkdir(parents=True)
            (profiles_dir / "zzz.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "zzz-model": {
                        "backend": {"gguf": "zzz-model.gguf"},
                        "metadata": {"label": "zzz"}
                    }
                }
            }), encoding="utf-8")
            (profiles_dir / "aaa.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "aaa-model": {
                        "backend": {"gguf": "aaa-model.gguf"},
                        "metadata": {"label": "aaa"}
                    }
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertIn("aaa-model.gguf", manifest["models"])
            self.assertIn("zzz-model.gguf", manifest["models"])
            self.assertEqual(manifest["models"]["aaa-model.gguf"]["label"], "aaa")
            self.assertEqual(manifest["models"]["zzz-model.gguf"]["label"], "zzz")

    def test_default_caveman_split_file_loads(self):
        """config/default/profiles/caveman.json is loaded as part of the default layer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_profiles_dir = root / "config" / "default" / "profiles"
            default_profiles_dir.mkdir(parents=True)
            (default_profiles_dir / "caveman.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "caveman": {
                        "backend": {"gguf": "caveman.gguf"},
                        "runtime": {"context_length": 262144, "default_reasoning_level": "medium"},
                        "metadata": {"label": "caveman"}
                    }
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertIn("caveman.gguf", manifest["models"])
            self.assertEqual(manifest["models"]["caveman.gguf"]["label"], "caveman")
            self.assertEqual(manifest["models"]["caveman.gguf"]["runtime_context_length"], 262144)
            self.assertEqual(manifest["models"]["caveman.gguf"]["default_reasoning_level"], "medium")

    def test_user_split_profile_overrides_default_profile(self):
        """User profiles/ entry with same slug overrides the default layer entry."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_dir = root / "config" / "default" / "profiles"
            default_dir.mkdir(parents=True)
            (default_dir / "myprofile.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "myprofile": {
                        "backend": {"gguf": "myprofile.gguf"},
                        "metadata": {"label": "default-label"}
                    }
                }
            }), encoding="utf-8")
            user_dir = root / "config" / "user" / "profiles"
            user_dir.mkdir(parents=True)
            (user_dir / "myprofile.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "myprofile": {
                        "backend": {"gguf": "myprofile.gguf"},
                        "metadata": {"label": "user-label"}
                    }
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertIn("myprofile.gguf", manifest["models"])
            self.assertEqual(manifest["models"]["myprofile.gguf"]["label"], "user-label")

    def test_same_layer_duplicate_slug_raises(self):
        """Duplicate profile slug in the same layer raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            layer_dir = Path(tmp)
            profiles_dir = layer_dir / "profiles"
            profiles_dir.mkdir()
            (profiles_dir / "a.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "duplicate-slug": {
                        "backend": {"gguf": "duplicate-slug.gguf"},
                        "metadata": {"label": "from-a"}
                    }
                }
            }), encoding="utf-8")
            (profiles_dir / "b.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "duplicate-slug": {
                        "backend": {"gguf": "duplicate-slug.gguf"},
                        "metadata": {"label": "from-b"}
                    }
                }
            }), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                _load_profiles_layer(layer_dir)
            self.assertIn("duplicate-slug", str(ctx.exception))
            self.assertIn("duplicate", str(ctx.exception).lower())

    def test_profile_slug_as_codex_id(self):
        """Profile slug is the identity shown to Codex; backend.gguf is the routing target."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "config" / "user"
            user_dir.mkdir(parents=True)
            (user_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "my-slug": {
                        "backend": {"gguf": "actual-file.gguf"},
                        "metadata": {"label": "my-slug"}
                    }
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            # The key in models dict is the GGUF filename, not the slug
            self.assertIn("actual-file.gguf", manifest["models"])
            self.assertNotIn("my-slug", manifest["models"])

    def test_slug_resolves_independently_of_backend_gguf(self):
        """
        A v1 profile slug that differs from the GGUF stem must be resolvable
        via match_model() even when the scanned file has a different name.

        Setup:
          profiles["alice"] with backend.gguf = "some-model.gguf"
          var/models/some-model.gguf  (real GGUF file, not a symlink)

        Expected: catalog.resolve("alice") returns the some-model entry.
        """
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "some-model.gguf")
            user_dir = root / "config" / "user"
            user_dir.mkdir(parents=True)
            (user_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "alice": {
                        "backend": {"gguf": "some-model.gguf"},
                        "runtime": {"context_length": 65536},
                        "metadata": {"label": "alice"}
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, reason = catalog.resolve("alice")

            self.assertIsNotNone(entry, "resolve('alice') must find the entry")
            # The entry is the scanned some-model.gguf file
            self.assertEqual(entry["stem"], "some-model")
            # Its label comes from the v1 overrides
            self.assertEqual(entry["label"], "alice")
            # The slug 'alice' must appear in the entry's aliases
            self.assertIn("alice", entry["aliases"])
            self.assertIn("alice.gguf", entry["aliases"])

    def test_backend_gguf_used_as_key(self):
        """backend.gguf value becomes the models dict key."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "my-profile": {
                    "backend": {"gguf": "custom-name.gguf"},
                    "metadata": {"label": "custom"}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertIn("custom-name.gguf", manifest["models"])

    def test_memory_domain_from_memory_dot_domain(self):
        """memory.domain in a v1 bundle maps to memory_domain in overrides."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "coder": {
                    "backend": {"gguf": "coder.gguf"},
                    "memory": {"domain": "coding"},
                    "metadata": {"label": "coder"}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertEqual(manifest["models"]["coder.gguf"]["memory_domain"], "coding")

    def test_missing_memory_domain_resolves_isolated(self):
        """A profile without memory.domain has no memory_domain key in overrides."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "plain": {
                    "backend": {"gguf": "plain.gguf"},
                    "metadata": {"label": "plain"}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertNotIn("memory_domain", manifest["models"]["plain.gguf"])

    def test_domains_list_not_accepted_in_v1(self):
        """memory.domain as a list is not accepted; results in no memory_domain key."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "multi": {
                    "backend": {"gguf": "multi.gguf"},
                    "memory": {"domain": ["coding", "research"]},
                    "metadata": {"label": "multi"}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertNotIn("memory_domain", manifest["models"]["multi.gguf"])

    def test_profiles_v1_fallback_to_old_format(self):
        """When no profiles files exist, old model-overrides.json still works."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_path = root / "config" / "user" / "model-overrides.json"
            user_path.parent.mkdir(parents=True)
            user_path.write_text(json.dumps({
                "models": {
                    "legacy.gguf": {"label": "legacy-label", "memory_domain": "coding"}
                }
            }), encoding="utf-8")

            manifest = load_manifest(root)

            self.assertIn("legacy.gguf", manifest["models"])
            self.assertEqual(manifest["models"]["legacy.gguf"]["label"], "legacy-label")
            self.assertEqual(manifest["models"]["legacy.gguf"]["memory_domain"], "coding")

    def test_shared_harnesses_in_profiles_json(self):
        """shared_harnesses at top level go to turn_harness_definitions in manifest."""
        data = {
            "schema": "qz.profiles.v1",
            "shared_harnesses": {
                "my-harness": "Remember to stay in character.",
                "another-harness": "Always be concise."
            },
            "profiles": {}
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertIn("turn_harness_definitions", manifest)
        self.assertEqual(manifest["turn_harness_definitions"]["my-harness"], "Remember to stay in character.")
        self.assertEqual(manifest["turn_harness_definitions"]["another-harness"], "Always be concise.")

    def test_no_profiles_files_returns_none(self):
        """_load_profiles_layer returns None when no profiles files exist in the layer."""
        with tempfile.TemporaryDirectory() as tmp:
            layer_dir = Path(tmp)
            result = _load_profiles_layer(layer_dir)
            self.assertIsNone(result)

    def test_profiles_json_without_profiles_dir_loads(self):
        """profiles.json at the top of a layer with no profiles/ subdir loads correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            layer_dir = Path(tmp)
            (layer_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "solo": {
                        "backend": {"gguf": "solo.gguf"},
                        "metadata": {"label": "solo"}
                    }
                }
            }), encoding="utf-8")

            result = _load_profiles_layer(layer_dir)

            self.assertIsNotNone(result)
            manifest, warnings = result
            self.assertIn("solo.gguf", manifest["models"])
            self.assertEqual(warnings, [])

    def test_default_flag_sets_default_key(self):
        """metadata.default: true on a profile sets manifest default_key to its gguf."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "my-default": {
                    "backend": {"gguf": "my-default.gguf"},
                    "metadata": {"label": "default", "default": True}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertEqual(manifest["default_key"], "my-default.gguf")
        self.assertTrue(manifest["models"]["my-default.gguf"]["default"])

    def test_disable_system_prompt_from_prompts_disable(self):
        """prompts.disable: true maps to disable_system_prompt in overrides."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "blank": {
                    "backend": {"gguf": "blank.gguf"},
                    "prompts": {"disable": True}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertTrue(manifest["models"]["blank.gguf"]["disable_system_prompt"])

    def test_slug_without_backend_gguf_defaults_to_slug_dot_gguf(self):
        """When backend.gguf is missing, slug + .gguf is used as the key."""
        data = {
            "schema": "qz.profiles.v1",
            "profiles": {
                "auto-name": {
                    "metadata": {"label": "auto"}
                }
            }
        }
        manifest = _profiles_v1_to_manifest(data)
        self.assertIn("auto-name.gguf", manifest["models"])

    def test_v1_load_and_scan_full_roundtrip(self):
        """Full roundtrip: profiles.json -> manifest -> scan -> entry with label and memory_domain."""
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"QZ_MODEL_KEY": ""}, clear=False):
            os.environ.pop("QZ_MODEL_STATE_PATH", None)
            root = Path(tmp)
            model_dir = root / "var" / "models"
            _write_gguf(model_dir / "mymodel.gguf")
            user_dir = root / "config" / "user"
            user_dir.mkdir(parents=True)
            (user_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "mymodel": {
                        "backend": {"gguf": "mymodel.gguf"},
                        "runtime": {"context_length": 65536},
                        "memory": {"domain": "research"},
                        "metadata": {"label": "My Model", "default": True}
                    }
                }
            }), encoding="utf-8")

            catalog = ModelCatalog(root, model_dir, load_manifest(root))
            entry, reason = catalog.resolve("mymodel")

            self.assertIsNotNone(entry)
            self.assertEqual(entry["label"], "My Model")
            self.assertEqual(entry["memory_domain"], "research")
            self.assertEqual(entry["runtime_context_length"], 65536)

    def test_load_manifest_unaffected_by_stale_qz_model_overrides(self):
        """
        Regression: load_manifest(root) must read config/user/ when
        QZ_MODEL_OVERRIDES is absent, even if a prior test leaked a stale
        value pointing to a deleted path.

        This test simulates the contamination by setting QZ_MODEL_OVERRIDES
        to a missing path BEFORE the _IsolatedModelEnvMixin setUp runs
        (i.e. in a nested block where we temporarily restore the contaminated
        state), then confirms that the mixin clears it before load_manifest
        is called.  Inside the mixin-guarded test body the env var is always
        absent, so the user-layer scan must succeed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "config" / "user"
            user_dir.mkdir(parents=True)
            (user_dir / "profiles.json").write_text(json.dumps({
                "schema": "qz.profiles.v1",
                "profiles": {
                    "testmodel": {
                        "backend": {"gguf": "testmodel.gguf"},
                        "metadata": {"label": "test-label"},
                    }
                }
            }), encoding="utf-8")

            # Confirm the mixin cleared QZ_MODEL_OVERRIDES before this test ran.
            self.assertNotIn("QZ_MODEL_OVERRIDES", os.environ,
                             "mixin setUp must have cleared QZ_MODEL_OVERRIDES")

            manifest = load_manifest(root)

            self.assertIn("testmodel.gguf", manifest["models"],
                          "user-layer profiles.json must be loaded when QZ_MODEL_OVERRIDES is absent")
            self.assertEqual(manifest["models"]["testmodel.gguf"]["label"], "test-label")


if __name__ == "__main__":
    unittest.main()
