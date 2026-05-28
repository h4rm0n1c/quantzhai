"""Tests for the qz.control_plane.status.v1 endpoint helpers."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    bm = MagicMock()
    phase = "healthy" if backend_ready or loaded_model else "running"
    launch_key = f"{loaded_model}.gguf" if loaded_model and not loaded_model.endswith(".gguf") else loaded_model
    bm.snapshot.return_value = {
        "phase": phase,
        "backend_health_ok": backend_health_status == 200,
        "backend_model_mode": "direct",
        "launch_model_key": launch_key,
        "launch_model_backend_id": loaded_model.replace(".gguf", "") if loaded_model else "",
        "launch_model_path_basename": launch_key,
        "launch_model_error": None,
    }
    bm.get_models_status.return_value = {
        "data": [
            {
                "id": f"/models/{launch_key}",
                "status": {"value": "loaded"},
            }
        ]
    } if launch_key else {"data": []}
    handler.backend_manager = bm

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
    _ENV_KEYS = ("QZ_ROOT", "QZ_VAR_DIR", "CODEX_HOME")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV_KEYS}
        for k in self._ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_returns_dict(self):
        info = _codex_catalog_info()
        self.assertIsInstance(info, dict)
        self.assertIn("path", info)
        self.assertIn("exists", info)

    def test_path_uses_qz_var_dir_not_codex_home(self):
        """CODEX_HOME must not redirect server catalog path; QZ_VAR_DIR controls it."""
        with tempfile.TemporaryDirectory() as tmp:
            custom_var = Path(tmp) / "custom-var"
            custom_var.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(custom_var)
            os.environ.pop("CODEX_HOME", None)
            info = _codex_catalog_info()
            self.assertIn(str(custom_var), info["path"])
            self.assertNotIn("CODEX_HOME", info["path"])

    def test_codex_home_env_ignored_for_catalog_path(self):
        """Setting CODEX_HOME must not change the server catalog path."""
        with tempfile.TemporaryDirectory() as tmp:
            custom_var = Path(tmp) / "custom-var"
            custom_var.mkdir(parents=True)
            os.environ["QZ_VAR_DIR"] = str(custom_var)
            os.environ["CODEX_HOME"] = str(Path(tmp) / "client-home")
            info = _codex_catalog_info()
            self.assertIn(str(custom_var), info["path"])
            self.assertNotIn("client-home", info["path"])


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


# ---------------------------------------------------------------------------
# Slice C — proxy-owned model selection fields in /qz/control-plane
# ---------------------------------------------------------------------------

class ModelSelectionFieldsTests(unittest.TestCase):
    """Verify /qz/control-plane surfaces configured vs selected vs loaded."""

    def _with_state_file(self, state_payload: dict):
        """Return a context-manager-like helper for a state file + env patch."""
        return _StateFileFixture(state_payload)

    def test_models_section_has_new_fields(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_label": "Kuato",
            "selected_source": "operator",
            "selected_at": "2026-05-21T10:00:00Z",
            "selected_reason": "POST /qz/model/select",
        }) as fix:
            h = _make_handler()
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        m = p["models"]
        for field in (
            "configured_env_model",
            "selected_source",
            "selected_at",
            "backend_loaded_model",
            "selected_loaded_mismatch",
            "selection_reason",
            "model_visible",
            "profile_valid",
            "restart_required",
        ):
            self.assertIn(field, m, f"models.{field} missing")
        self.assertEqual(m["selected_source"], "operator")
        self.assertEqual(m["selection_reason"], "POST /qz/model/select")

    def test_profile_section_has_reasoning_budget(self):
        h = _make_handler()
        summary = h._model_router.return_value.status_summary.return_value
        summary["backend_reasoning_budget"] = 123
        p = build_control_plane_status(h)
        self.assertEqual(p["profile"]["backend_reasoning_budget"], 123)

    def test_backend_section_has_load_failure_fields(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "last_load_result": "failed",
            "last_load_error": "cudaMalloc failed",
            "last_load_error_type": "insufficient_vram",
        }) as fix:
            h = _make_handler(loaded_model="")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        b = p["backend"]
        self.assertTrue(b["model_load_failed"])
        self.assertEqual(b["load_error"], "cudaMalloc failed")
        self.assertEqual(b["load_error_type"], "insufficient_vram")
        self.assertTrue(b["insufficient_vram"])
        self.assertTrue(b["selected_model_failed"])

    def test_configured_env_model_exposed(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler()
            h.model_state_path = fix.path
            with patch.dict(os.environ, {"QZ_MODEL_KEY": "envseed.gguf"}, clear=False):
                p = build_control_plane_status(h)
        self.assertEqual(p["models"]["configured_env_model"], "envseed.gguf")
        self.assertEqual(p["models"]["selected"], "kuato.gguf")

    def test_selected_loaded_mismatch_true(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler(loaded_model="other-model")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        self.assertTrue(p["models"]["selected_loaded_mismatch"])
        self.assertTrue(any("differs from loaded" in hint for hint in p["operator_hints"]))

    def test_selected_loaded_mismatch_false_when_aligned(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        self.assertFalse(p["models"]["selected_loaded_mismatch"])

    def test_qz_model_key_seed_only_hint_emitted(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            with patch.dict(os.environ, {"QZ_MODEL_KEY": "envseed"}, clear=False):
                p = build_control_plane_status(h)
        self.assertTrue(any("QZ_MODEL_KEY" in hint for hint in p["operator_hints"]))

    def test_qz_model_key_matches_selection_no_hint(self):
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            with patch.dict(os.environ, {"QZ_MODEL_KEY": "kuato"}, clear=False):
                p = build_control_plane_status(h)
        self.assertFalse(any("QZ_MODEL_KEY" in hint for hint in p["operator_hints"]))

    def test_no_selection_no_mismatch(self):
        with self._with_state_file({}) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        self.assertFalse(p["models"]["selected_loaded_mismatch"])
        self.assertEqual(p["models"]["selected_source"], "")


    def test_rollback_operator_hint_emitted_on_failure(self):
        """Post-Slice F: hint mentions rollback when last_good exists + failure recorded."""
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_reason": "rolled back from failed candidate too-large.gguf",
            "last_good_key": "kuato.gguf",
            "last_good_backend_id": "kuato",
            "failed_candidate_key": "too-large.gguf",
            "failed_candidate_backend_id": "too-large",
            "last_load_result": "failed",
            "last_load_error": "cudaMalloc failed",
            "last_load_error_type": "insufficient_vram",
        }) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        self.assertTrue(any("rolled back to last-good" in hint for hint in p["operator_hints"]))

    def test_no_last_good_hint_when_failure_without_history(self):
        """No last_good + failed candidate → hint asks for a different model."""
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "too-large.gguf",
            "selected_backend_id": "too-large",
            "failed_candidate_key": "too-large.gguf",
            "failed_candidate_backend_id": "too-large",
            "last_load_result": "failed",
            "last_load_error_type": "unknown",
        }) as fix:
            h = _make_handler(loaded_model="")
            h.model_state_path = fix.path
            p = build_control_plane_status(h)
        self.assertTrue(any("no last-good" in hint for hint in p["operator_hints"]))


    def test_backend_model_mode_and_launch_model_in_models_section(self):
        """Models block exposes direct-mode readiness and launch_model_*."""
        with self._with_state_file({
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
        }) as fix:
            h = _make_handler(loaded_model="kuato")
            h.model_state_path = fix.path
            # Inject a fake backend manager snapshot via MagicMock attr
            bm = MagicMock()
            bm.snapshot.return_value = {
                "phase": "healthy",
                "backend_health_ok": True,
                "backend_model_mode": "direct",
                "launch_model_key": "kuato.gguf",
                "launch_model_backend_id": "kuato",
                "launch_model_path_basename": "kuato.gguf",
            }
            bm.get_models_status.return_value = {
                "data": [{"id": "/models/kuato.gguf", "status": {"value": "loaded"}}]
            }
            h.backend_manager = bm
            p = build_control_plane_status(h)
        m = p["models"]
        self.assertEqual(m["backend_model_mode"], "direct")
        self.assertEqual(m["launch_model_key"], "kuato.gguf")
        self.assertEqual(m["launch_model_backend_id"], "kuato")
        self.assertEqual(m["launch_model_path_basename"], "kuato.gguf")
        self.assertTrue(m["selected_model_ready"])
        self.assertEqual(m["request_admission_state"], "ready")
        self.assertIn("model_switch_state", m)
        self.assertIn("active_load_operation", m)
        self.assertIn("last_good_key", m)
        self.assertIn("failed_candidate_key", m)


class DirectModeControlPlaneTests(unittest.TestCase):
    """Verify control-plane correctness when direct-m backend is healthy."""

    def _make_direct_handler(self, *, state_payload: dict, loaded_model: str = "kuato"):
        """Build a handler with a state file and direct backend manager."""
        from unittest.mock import MagicMock
        tmp_dir = tempfile.mkdtemp()
        state_path = str(Path(tmp_dir) / "model-state.json")
        Path(state_path).write_text(json.dumps(state_payload), encoding="utf-8")
        self._tmp_dirs = getattr(self, "_tmp_dirs", [])
        self._tmp_dirs.append(tmp_dir)

        h = _make_handler(loaded_model=loaded_model)
        h.model_state_path = state_path
        bm = MagicMock()
        launch_key = f"{loaded_model}.gguf" if loaded_model and not loaded_model.endswith(".gguf") else loaded_model
        bm.snapshot.return_value = {
            "phase": "healthy",
            "backend_health_ok": True,
            "backend_model_mode": "direct",
            "gpu_required": True,
            "gpu_offload_state": "gpu",
            "gpu_observed": True,
            "launch_model_key": launch_key,
            "launch_model_backend_id": loaded_model.replace(".gguf", "") if loaded_model else "",
            "launch_model_path_basename": launch_key,
            "launch_model_error": None,
            "container_running": True,
        }
        bm.get_models_status.return_value = {
            "data": [
                {
                    "id": f"/models/{launch_key}",
                    "status": {"value": "loaded"},
                }
            ]
        } if launch_key else {"data": []}
        h.backend_manager = bm
        return h

    def tearDown(self):
        import shutil
        for d in getattr(self, "_tmp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def test_direct_healthy_model_state_is_loaded(self):
        """service_status.model_state must be 'loaded' when direct backend is healthy."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        ss = p["service_status"]
        self.assertEqual(ss["model_state"], "loaded",
                         f"Expected model_state=loaded, got {ss['model_state']!r}")

    def test_direct_healthy_not_rejected_model_not_loaded(self):
        """service_status.request_admission must not be rejected_model_not_loaded."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        ss = p["service_status"]
        self.assertNotEqual(ss["request_admission"], "rejected_model_not_loaded",
                            f"Got unexpected: {ss['request_admission']!r}")
        self.assertEqual(ss["request_admission"], "accepted")

    def test_direct_healthy_stale_failed_still_shows_loaded(self):
        """Stale last_load_result=failed must not block a healthy direct backend."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
            "last_load_result": "failed",
            "last_load_error": "previous attempt failed",
            "last_load_error_type": "unknown",
        })
        p = build_control_plane_status(h)
        self.assertTrue(p["models"]["selected_model_ready"],
                        "Healthy direct backend must override stale last_load_result=failed")
        ss = p["service_status"]
        self.assertEqual(ss["model_state"], "loaded")
        self.assertEqual(ss["request_admission"], "accepted")

    def test_direct_healthy_backend_loaded_model_populated_in_cp(self):
        """backend.loaded_model must be non-empty in control-plane when direct is healthy."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        self.assertTrue(p["backend"]["loaded_model"],
                        "backend.loaded_model must not be empty for a healthy direct backend")

    # --- A: readiness and top-level status correctness ---

    def test_direct_healthy_readiness_backend_ready_true(self):
        """readiness.backend_ready must be True when direct backend is healthy.

        Regression test for the ordering bug: readiness was built before
        model_status updated backend_ready, leaving it False even when the
        model was ready.
        """
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        self.assertTrue(p["readiness"]["backend_ready"],
                        f"readiness.backend_ready should be True; got {p['readiness']['backend_ready']!r}")
        self.assertTrue(p["readiness"]["backend_reachable"],
                        "readiness.backend_reachable should be True")

    def test_direct_healthy_status_not_model_not_loaded(self):
        """Top-level status must not be 'model_not_loaded' when direct backend is healthy.

        Regression test: status was derived from stale readiness before model_status
        promoted backend_ready, so it incorrectly showed 'model_not_loaded'.
        """
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        self.assertNotEqual(p["status"], "model_not_loaded",
                            f"status must not be 'model_not_loaded'; got {p['status']!r}")
        self.assertEqual(p["status"], "ready")

    def test_direct_healthy_no_stale_model_not_loaded_hint(self):
        """Operator hints must not say 'no model is loaded' when direct backend is healthy."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        stale_hint = "no model is loaded yet"
        for hint in p.get("operator_hints", []):
            self.assertNotIn(stale_hint, str(hint).lower(),
                             f"Stale hint found: {hint!r}")

    def test_direct_healthy_model_switch_state_loaded(self):
        """models.model_switch_state must be 'loaded' when direct backend is healthy."""
        h = self._make_direct_handler(state_payload={
            "schema": "qz.model_state.v1",
            "selected_key": "kuato.gguf",
            "selected_backend_id": "kuato",
            "selected_source": "operator",
        })
        p = build_control_plane_status(h)
        self.assertEqual(p["models"]["model_switch_state"], "loaded",
                         f"model_switch_state should be 'loaded'; got {p['models']['model_switch_state']!r}")

    def test_router_mode_backend_ready_false_still_shows_model_not_loaded(self):
        """Non-direct path (router says not ready) must still show model_not_loaded.

        Regression guard: verifies the fix doesn't break non-direct cases.
        """
        h = _make_handler(backend_health_status=200, backend_ready=False, loaded_model="")
        p = build_control_plane_status(h)
        self.assertEqual(p["status"], "model_not_loaded")
        self.assertFalse(p["readiness"]["backend_ready"])


class _StateFileFixture:
    """Context manager that creates a tempfile state file."""

    def __init__(self, payload: dict):
        self.payload = payload
        self._tmp = None
        self.path = None

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "model-state.json")
        Path(self.path).write_text(json.dumps(self.payload), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
