"""Tests for the qz.control_plane.status.v1 endpoint helpers."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_control_plane import (
    QZ_CONTROL_PLANE_SCHEMA,
    _codex_catalog_info,
    _operator_hints,
    _overall_status,
    build_control_plane_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(
    *,
    proxy_ready: bool = True,
    catalog_ready: bool = True,
    model_ids: list[str] | None = None,
    selected_key: str = "kuato.gguf",
    backend_health_status: int | None = 200,
    backend_ready: bool = True,
    loaded_model: str = "kuato",
    raise_router: bool = False,
    raise_catalog: bool = False,
):
    """Build a minimal mock handler that build_control_plane_status can call."""
    handler = MagicMock()

    # _initialization_payload
    handler._initialization_payload.return_value = {
        "schema": "qz.proxy.initialization.v1",
        "state": "ready" if proxy_ready else "initializing",
        "ready": proxy_ready,
        "catalog_ready": catalog_ready,
        "error": None,
    }

    # _model_catalog
    if raise_catalog:
        handler._model_catalog.side_effect = RuntimeError("catalog error")
    else:
        catalog = MagicMock()
        if model_ids is None:
            model_ids = ["kuato.gguf", "qwen-blank.gguf"]
        entries = [
            {"key": mid, "stem": mid.replace(".gguf", ""), "backend_id": mid.replace(".gguf", ""), "profile_valid": True}
            for mid in model_ids
        ]
        catalog.entries = entries
        selected = {"key": selected_key, "stem": selected_key.replace(".gguf", ""), "backend_id": "kuato"}
        catalog.selected = selected
        handler._model_catalog.return_value = catalog

    # _model_router().status_summary
    if raise_router:
        handler._model_router.return_value.status_summary.side_effect = RuntimeError("backend error")
    else:
        summary = {
            "health_status": backend_health_status,
            "ready": backend_ready,
            "loaded_model": loaded_model,
            "loaded_count": 1 if loaded_model else 0,
            "restart_required": False,
        }
        handler._model_router.return_value.status_summary.return_value = summary

    return handler


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    def test_schema_field(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertEqual(p["schema"], QZ_CONTROL_PLANE_SCHEMA)

    def test_ok_field_present(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertIn("ok", p)

    def test_status_field_present(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertIn("status", p)

    def test_json_serialisable(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        serialised = json.dumps(p)
        rt = json.loads(serialised)
        self.assertEqual(rt["schema"], QZ_CONTROL_PLANE_SCHEMA)


# ---------------------------------------------------------------------------
# Ready state
# ---------------------------------------------------------------------------

class ReadyStateTests(unittest.TestCase):
    def test_fully_ready_ok_true(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertTrue(p["ok"])
        self.assertEqual(p["status"], "ready")

    def test_fully_ready_all_readiness_true(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        r = p["readiness"]
        self.assertTrue(r["proxy_http"])
        self.assertTrue(r["proxy_ready"])
        self.assertTrue(r["catalog_ready"])
        self.assertTrue(r["models_visible"])
        self.assertTrue(r["backend_reachable"])
        self.assertTrue(r["backend_ready"])


# ---------------------------------------------------------------------------
# Proxy not ready
# ---------------------------------------------------------------------------

class ProxyNotReadyTests(unittest.TestCase):
    def test_proxy_not_ready_ok_false(self):
        h = _make_handler(proxy_ready=False, catalog_ready=False)
        p = build_control_plane_status(h)
        self.assertFalse(p["ok"])

    def test_proxy_not_ready_status_initializing(self):
        h = _make_handler(proxy_ready=False, catalog_ready=False)
        p = build_control_plane_status(h)
        self.assertEqual(p["status"], "initializing")

    def test_proxy_not_ready_readiness(self):
        h = _make_handler(proxy_ready=False, catalog_ready=False)
        p = build_control_plane_status(h)
        r = p["readiness"]
        self.assertFalse(r["proxy_ready"])
        self.assertFalse(r["catalog_ready"])
        self.assertFalse(r["models_visible"])

    def test_proxy_not_ready_returns_json(self):
        """Endpoint always returns JSON, even in initializing state."""
        h = _make_handler(proxy_ready=False, catalog_ready=False)
        p = build_control_plane_status(h)
        self.assertIsInstance(p, dict)
        self.assertEqual(p["schema"], QZ_CONTROL_PLANE_SCHEMA)


# ---------------------------------------------------------------------------
# Backend unavailable
# ---------------------------------------------------------------------------

class BackendUnavailableTests(unittest.TestCase):
    def test_backend_unreachable_returns_json(self):
        h = _make_handler(backend_health_status=0, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        self.assertIsInstance(p, dict)
        self.assertEqual(p["schema"], QZ_CONTROL_PLANE_SCHEMA)

    def test_backend_unreachable_ok_true_proxy_still_usable(self):
        """ok reflects proxy+catalog readiness, not backend readiness."""
        h = _make_handler(backend_health_status=0, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        self.assertTrue(p["ok"])  # proxy and catalog are ready

    def test_backend_unreachable_status(self):
        h = _make_handler(backend_health_status=0, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        self.assertEqual(p["status"], "backend_unavailable")

    def test_backend_unreachable_readiness_flags(self):
        h = _make_handler(backend_health_status=0, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        r = p["readiness"]
        self.assertTrue(r["proxy_ready"])
        self.assertTrue(r["catalog_ready"])
        self.assertFalse(r["backend_reachable"])
        self.assertFalse(r["backend_ready"])

    def test_backend_error_captured(self):
        h = _make_handler(raise_router=True)
        p = build_control_plane_status(h)
        self.assertIsNotNone(p["backend"]["error"])
        self.assertIsInstance(p["backend"]["error"], str)


# ---------------------------------------------------------------------------
# Model not loaded
# ---------------------------------------------------------------------------

class ModelNotLoadedTests(unittest.TestCase):
    def test_backend_reachable_no_model_status(self):
        h = _make_handler(backend_health_status=200, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        self.assertEqual(p["status"], "model_not_loaded")

    def test_backend_reachable_no_model_flags(self):
        h = _make_handler(backend_health_status=200, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        r = p["readiness"]
        self.assertTrue(r["backend_reachable"])
        self.assertFalse(r["backend_ready"])


# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------

class ModelIdsTests(unittest.TestCase):
    def test_model_ids_present(self):
        h = _make_handler(model_ids=["a.gguf", "b.gguf", "c.gguf"])
        p = build_control_plane_status(h)
        self.assertEqual(p["models"]["count"], 3)
        self.assertIn("a.gguf", p["models"]["ids"])

    def test_model_ids_sorted(self):
        h = _make_handler(model_ids=["z.gguf", "a.gguf"])
        p = build_control_plane_status(h)
        self.assertEqual(p["models"]["ids"], ["a.gguf", "z.gguf"])

    def test_no_models_visible(self):
        h = _make_handler(catalog_ready=True, model_ids=[])
        p = build_control_plane_status(h)
        self.assertEqual(p["models"]["count"], 0)
        self.assertFalse(p["readiness"]["models_visible"])

    def test_selected_model_present(self):
        h = _make_handler(selected_key="kuato.gguf")
        p = build_control_plane_status(h)
        self.assertEqual(p["models"]["selected"], "kuato.gguf")


# ---------------------------------------------------------------------------
# Operator hints
# ---------------------------------------------------------------------------

class OperatorHintsTests(unittest.TestCase):
    def test_hints_present(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertIn("operator_hints", p)
        self.assertIsInstance(p["operator_hints"], list)
        self.assertTrue(len(p["operator_hints"]) > 0)

    def test_hints_no_docker_requirement(self):
        h = _make_handler(backend_health_status=0, backend_ready=False)
        p = build_control_plane_status(h)
        combined = " ".join(p["operator_hints"])
        self.assertNotIn("Docker is required", combined)
        self.assertNotIn("requires Docker", combined)

    def test_hints_remote_friendly(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        combined = " ".join(p["operator_hints"])
        # Should mention remote clients don't need local infrastructure
        self.assertIn("Remote", combined)

    def test_backend_unreachable_hint(self):
        h = _make_handler(backend_health_status=0, backend_ready=False)
        p = build_control_plane_status(h)
        combined = " ".join(p["operator_hints"])
        self.assertIn("backend", combined.lower())


# ---------------------------------------------------------------------------
# _overall_status helper
# ---------------------------------------------------------------------------

class OverallStatusTests(unittest.TestCase):
    def test_all_ready(self):
        r = {"proxy_ready": True, "catalog_ready": True, "backend_reachable": True, "backend_ready": True}
        self.assertEqual(_overall_status(r), "ready")

    def test_proxy_not_ready(self):
        r = {"proxy_ready": False, "catalog_ready": False, "backend_reachable": False, "backend_ready": False}
        self.assertEqual(_overall_status(r), "initializing")

    def test_catalog_not_ready(self):
        r = {"proxy_ready": True, "catalog_ready": False, "backend_reachable": False, "backend_ready": False}
        self.assertEqual(_overall_status(r), "initializing")

    def test_backend_unavailable(self):
        r = {"proxy_ready": True, "catalog_ready": True, "backend_reachable": False, "backend_ready": False}
        self.assertEqual(_overall_status(r), "backend_unavailable")

    def test_model_not_loaded(self):
        r = {"proxy_ready": True, "catalog_ready": True, "backend_reachable": True, "backend_ready": False}
        self.assertEqual(_overall_status(r), "model_not_loaded")


# ---------------------------------------------------------------------------
# Codex catalog info
# ---------------------------------------------------------------------------

class CodexCatalogInfoTests(unittest.TestCase):
    def test_returns_dict(self):
        info = _codex_catalog_info()
        self.assertIsInstance(info, dict)
        self.assertIn("path", info)
        self.assertIn("exists", info)

    def test_respects_codex_home_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat_dir = Path(tmp) / "model-catalogs"
            cat_dir.mkdir()
            cat_file = cat_dir / "qwenzhai-models.json"
            cat_file.write_text("{}", encoding="utf-8")
            old = os.environ.get("CODEX_HOME")
            try:
                os.environ["CODEX_HOME"] = tmp
                info = _codex_catalog_info()
                self.assertTrue(info["exists"])
            finally:
                if old is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old


class ServiceStatusInControlPlaneTests(unittest.TestCase):
    """Verify service_status is present in build_control_plane_status output."""

    def test_service_status_field_present_in_cp_payload(self):
        from proxy.qz_service_status import SERVICE_STATUS_SCHEMA
        h = _make_handler()
        p = build_control_plane_status(h)
        self.assertIn("service_status", p)
        ss = p["service_status"]
        self.assertEqual(ss.get("schema"), SERVICE_STATUS_SCHEMA)

    def test_service_status_has_required_fields(self):
        h = _make_handler()
        p = build_control_plane_status(h)
        ss = p["service_status"]
        for field in ("proxy_state", "catalog_state", "backend_state", "model_state",
                      "request_admission", "recovery_state", "recoverable", "retryable",
                      "fatal", "last_error", "operator_action", "operator_hints"):
            self.assertIn(field, ss, f"missing: {field}")

    def test_existing_cp_fields_unchanged(self):
        """Adding service_status must not remove any existing top-level fields."""
        h = _make_handler()
        p = build_control_plane_status(h)
        for field in ("schema", "ok", "status", "readiness", "proxy_initialization",
                      "models", "backend", "codex_catalog", "operator_hints"):
            self.assertIn(field, p, f"existing field removed: {field}")


if __name__ == "__main__":
    unittest.main()
