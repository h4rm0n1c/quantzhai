#!/usr/bin/env python3
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List

RUNTIME_METRICS_SCHEMA = "qz.runtime.metrics.v1"
PROMPT_CONTRACT_SCHEMA = "qz.prompt.contract.v1"
CAPTURE_CONTRACT_SCHEMA = "qz.capture.contract.v1"
REASONING_STREAM_FORMATS = {"raw", "summary", "hidden"}

try:
    from .qz_codex_client_config import codex_client_config_payload, codex_model_catalog_content
    from .qz_config_report import effective_config_payload
    from .qz_paths import (
        codex_config_path as _codex_config_path,
        codex_model_catalog_path as _codex_model_catalog_path,
        model_inventory_path as _model_inventory_path,
        qz_var_dir as _qz_var_dir,
    )
    from .qz_telemetry import TELEMETRY_RECENT_SCHEMA, TELEMETRY_REQUEST_SCHEMA
    from .qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, api_contract_payload
    from .qz_responses import (
        _build_local_compaction_response,
        _decode_local_compaction_blob,
        _estimate_items_tokens,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _now_ts,
        clean_content,
        ensure_apply_patch_tool_policy,
        extract_response_output_text,
        normalize_responses_input_for_qwen,
        normalize_tool_output_for_codex,
        normalize_tools_for_llamacpp,
    )
    from .qz_tool_request import normalize_tool_request_for_llamacpp as _normalize_tool_request
    from .qz_responses_stream import ResponsesStreamRuntime
    from .qz_sse import _normalize_response_usage, make_sse_block
    from .qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from .qz_codex_metadata import extract_codex_request_context
    from .qz_native_tool_output import classify_native_tool_output_signals
    from .qz_file_signal import record_tool_call, seed_repeated_read_state
    from .qz_native_signal import record_native_tool_call, seed_native_advisory_state
    from .qz_responses_error import build_responses_error_payload, is_deprecated_alias
    from .qz_control_plane import build_control_plane_status
    from .qz_service_status import build_service_status
    from .qz_recovery_status import build_recovery_status
    from .qz_recovery_plan import ALLOWED_RECOVERY_ACTIONS, build_recovery_plan
    from .qz_recovery_state import RECOVERY_STATE
    from .qz_active_requests import ACTIVE_REQUESTS
    from .qz_recovery_jobs import RECOVERY_JOBS
    from .qz_vram_snapshot import get_cached_vram_snapshot
    from .qz_search_policy import resolve_search_policy_selection
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, build_web_search_capabilities, _safe_json_file, _unique_sources
    from .qz_runtime_io import (
        append_capture,
        append_request_capture,
        capture_enabled,
        incoming_headers_payload,
        open_dual_capture_append,
        runtime_log,
        write_capture,
        write_dual_capture,
        write_request_capture,
    )
except ImportError:
    from qz_codex_client_config import codex_client_config_payload, codex_model_catalog_content
    from qz_config_report import effective_config_payload
    from qz_paths import (
        codex_config_path as _codex_config_path,
        codex_model_catalog_path as _codex_model_catalog_path,
        model_inventory_path as _model_inventory_path,
        qz_var_dir as _qz_var_dir,
    )
    from qz_telemetry import TELEMETRY_RECENT_SCHEMA, TELEMETRY_REQUEST_SCHEMA
    from qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, api_contract_payload
    from qz_responses import (
        _build_local_compaction_response,
        _decode_local_compaction_blob,
        _estimate_items_tokens,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _now_ts,
        clean_content,
        ensure_apply_patch_tool_policy,
        extract_response_output_text,
        normalize_responses_input_for_qwen,
        normalize_tool_output_for_codex,
        normalize_tools_for_llamacpp,
    )
    from qz_tool_request import normalize_tool_request_for_llamacpp as _normalize_tool_request
    from qz_responses_stream import ResponsesStreamRuntime
    from qz_sse import _normalize_response_usage, make_sse_block
    from qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from qz_codex_metadata import extract_codex_request_context
    from qz_native_tool_output import classify_native_tool_output_signals
    from qz_file_signal import record_tool_call, seed_repeated_read_state
    from qz_native_signal import record_native_tool_call, seed_native_advisory_state
    from qz_responses_error import build_responses_error_payload, is_deprecated_alias
    from qz_control_plane import build_control_plane_status
    from qz_service_status import build_service_status
    from qz_recovery_status import build_recovery_status
    from qz_recovery_plan import ALLOWED_RECOVERY_ACTIONS, build_recovery_plan
    from qz_recovery_state import RECOVERY_STATE
    from qz_active_requests import ACTIVE_REQUESTS
    from qz_recovery_jobs import RECOVERY_JOBS
    from qz_vram_snapshot import get_cached_vram_snapshot
    from qz_search_policy import resolve_search_policy_selection
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, build_web_search_capabilities, _safe_json_file, _unique_sources
    from qz_runtime_io import (
        append_capture,
        append_request_capture,
        capture_enabled,
        incoming_headers_payload,
        open_dual_capture_append,
        runtime_log,
        write_capture,
        write_dual_capture,
        write_request_capture,
    )


SAFE_TRIGGER_ACTIONS: frozenset = frozenset({"refresh_catalog", "clear_failure"})
DANGEROUS_TRIGGER_ACTIONS: frozenset = frozenset({
    "start_backend",
    "restart_backend",
    "reload_selected_model",
    "select_model",
})
ASYNC_SUPPORTED_ACTIONS: frozenset = frozenset({"reload_selected_model"})
IMPLEMENTED_TRIGGER_ACTIONS: frozenset = SAFE_TRIGGER_ACTIONS | DANGEROUS_TRIGGER_ACTIONS
UNIMPLEMENTED_TRIGGER_ACTIONS: frozenset = ALLOWED_RECOVERY_ACTIONS - IMPLEMENTED_TRIGGER_ACTIONS
RECOVERY_TRIGGER_SCHEMA = "qz.recovery.trigger.v1"

# Advisory text injected when a native tool call is blocked by a read-only filesystem.
# Plain text — not a JSON error shape. The model may use or ignore the advisory.
_SANDBOX_READONLY_ADVISORY_TEXT = (
    "The previous native tool call failed because the sandbox reported a read-only filesystem. "
    "Do not retry the same write operation unchanged. "
    "Choose a writable path, request the required permission/escalation if available, "
    "or explain the blocker."
)

_TRIGGER_WARNINGS: dict = {
    "refresh_catalog":      "Refreshing the catalog does not restart the backend.",
    "clear_failure":        "Clearing failure state does not start or restart the backend.",
    "select_model":         (
        "Selecting a model changes the active selection state only; "
        "the model is NOT loaded. Call reload_selected_model to restart the backend with it."
    ),
    "start_backend":        (
        "Starting the backend launches llama-server with the selected model."
    ),
    "restart_backend":      (
        "Restarting the backend will interrupt active requests and relaunch the selected model."
    ),
    "reload_selected_model": (
        "Reloading the selected model may interrupt active requests. "
        "The backend process is restarted with -m for the selected model."
    ),
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_reasoning_stream_format(value, default: str = "raw") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in REASONING_STREAM_FORMATS:
            return normalized
    return default if default in REASONING_STREAM_FORMATS else "raw"


def profile_reasoning_stream_format(selected_model, fallback: str = "raw") -> str:
    fallback = normalize_reasoning_stream_format(fallback, "raw")
    selected_model = selected_model if isinstance(selected_model, dict) else {}
    overrides = selected_model.get("overrides")
    overrides = overrides if isinstance(overrides, dict) else {}

    if overrides.get("hide_reasoning_stream") is True:
        return "hidden"

    for key in ("reasoning_stream_format", "client_reasoning_stream_format"):
        value = overrides.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_reasoning_stream_format(value, fallback)

    return fallback


def _async_recovery_worker(
    router: "RequestRouter",
    action: str,
    request_id: str,
    base_event: dict,
    rs: object,
    job_store: object,
) -> None:
    """Daemon thread worker for async recovery actions (#50).

    Called after mark_started and the 202 response have been sent.
    Executes the action, updates RecoveryRuntimeState and RecoveryJobStore,
    and emits telemetry events. Never raises.
    """
    try:
        if action == "reload_selected_model":
            ok, error = router._do_reload_selected_model()
        else:
            ok, error = False, f"Async not implemented for {action!r}"
    except Exception as exc:
        ok, error = False, str(exc)

    try:
        post_status = router._get_recovery_status_snapshot()
    except Exception:
        post_status = None

    if ok:
        try:
            rs.mark_completed(action)
        except Exception:
            pass
        try:
            if job_store is not None:
                job_store.mark_completed(request_id, post_status=post_status)
        except Exception:
            pass
        try:
            router._emit_recovery_event("recovery_action_completed", {
                **base_event, "post_recovery_status": post_status,
            })
        except Exception:
            pass
    else:
        try:
            rs.mark_failed(action, error)
        except Exception:
            pass
        try:
            if job_store is not None:
                job_store.mark_failed(request_id, error=error, post_status=post_status)
        except Exception:
            pass
        try:
            router._emit_recovery_event("recovery_action_failed", {
                **base_event, "error": error,
            })
        except Exception:
            pass
        try:
            if rs.is_backoff_active(action):
                router._emit_recovery_event("recovery_backoff_started", {
                    **base_event, "error": error,
                })
        except Exception:
            pass


class _MultiRawLog:
    def __init__(self, handles):
        self.handles = list(handles or [])

    def write(self, data: bytes):
        for handle in list(self.handles):
            handle.write(data)

    def close(self):
        for handle in list(self.handles):
            try:
                handle.close()
            except Exception:
                pass


class RequestRouter:
    def __init__(self, handler):
        self.handler = handler

    def _emit_schema_normalization_telemetry(self, report, request_id: str = "") -> None:
        """Emit tool_schema_replaced telemetry when normalization changes the tool list.

        Fires a single compact operator event per request when any of replaced,
        translated, dropped, or deduped is non-empty.  Payload includes names and
        counts only — no full schemas or user arguments.  source is always
        "tool_schema_normalizer" for reliable operator filtering.

        No-ops safely if telemetry is unavailable or the report has no changes.
        """
        if not (report.replaced or report.translated or report.dropped or report.deduped):
            return
        try:
            self.handler.telemetry.emit("tool_schema_replaced", {
                "replaced": list(report.replaced),
                "translated": list(report.translated),
                "dropped": list(report.dropped),
                "dropped_count": len(report.dropped),
                "deduped": list(report.deduped),
                "tool_choice_forced_auto": report.tool_choice_forced_auto,
                "source": "tool_schema_normalizer",
                "request_id": request_id,
            })
        except Exception:
            pass

    def _emit_signal_decisions(self, signals: List[Any], request_id: str = "") -> None:
        """Emit telemetry for SignalDecision objects.

        Only OPERATOR/TELEMETRY signals are processed in this pass.
        No model-visible injection is performed.
        """
        try:
            from .qz_feedback import FeedbackChannel
        except ImportError:
            from qz_feedback import FeedbackChannel

        for signal in signals:
            try:
                # Visibility/Channel check
                # For now we only support TELEMETRY in this pass.
                # SignalDecision.channel is an Enum (FeedbackChannel).
                if signal.channel == FeedbackChannel.TELEMETRY:
                    payload = dict(signal.payload)
                    if request_id:
                        payload["request_id"] = request_id
                    self.handler.telemetry.emit(signal.event_type, payload)
            except Exception:
                pass

    def _model_visible_native_advisories(self, signals: List[Any]) -> List[dict]:
        """Build model-visible advisory function_call_output items for qualifying signals.

        Currently only produces advisories for:
        - event_type:   tool_sandbox_denied
        - classifier:   sandbox_denied_readonly_fs
        - confidence:   high

        Deduplicates per call_id within a single request: if multiple signals match
        for the same call_id, only one advisory is injected.

        Does not produce advisories for:
        - connection_refused (telemetry-only)
        - non-high-confidence sandbox signals
        - generic permission denied

        Does not mutate signal objects or the input list.
        Returns a list (possibly empty) of function_call_output dicts.
        """
        try:
            from .qz_feedback import render_advisory_output
        except ImportError:
            from qz_feedback import render_advisory_output

        seen_call_ids: set = set()
        advisories: list = []

        for signal in signals:
            try:
                if signal.event_type != "tool_sandbox_denied":
                    continue
                payload = signal.payload
                if not isinstance(payload, dict):
                    continue
                if payload.get("classifier") != "sandbox_denied_readonly_fs":
                    continue
                if signal.confidence != "high":
                    continue
                call_id = payload.get("call_id") or ""
                if call_id in seen_call_ids:
                    continue
                seen_call_ids.add(call_id)
                advisories.append(render_advisory_output(
                    {"call_id": call_id} if call_id else {},
                    _SANDBOX_READONLY_ADVISORY_TEXT,
                ))
            except Exception:
                pass

        return advisories

    def _proxy_startup_ready(self) -> bool:
        ready_fn = getattr(self.handler, "_startup_ready", None)
        if callable(ready_fn):
            try:
                return bool(ready_fn())
            except Exception:
                return False
        return True

    @staticmethod
    def _minimal_cp_for_service_status(
        initialization: dict,
        model_visible: bool = False,
        backend_reachable: bool = False,
        backend_ready: bool = False,
        backend_error: str = "",
    ) -> dict:
        """Build a minimal control-plane-like dict for build_service_status().

        Used by rejection paths that have partial readiness context and want to
        derive a qz.service.status.v1 payload without probing the backend again.
        """
        return {
            "proxy_initialization": initialization,
            "readiness": {
                "proxy_ready": bool(initialization.get("ready")),
                "catalog_ready": bool(initialization.get("catalog_ready")),
                "models_visible": model_visible,
                "backend_reachable": backend_reachable,
                "backend_ready": backend_ready,
            },
            "models": {
                "count": 1 if model_visible else 0,
                "ids": [],
                "selected": "",
                "selected_backend_id": "",
            },
            "backend": {
                "reachable": backend_reachable,
                "ready": backend_ready,
                "health_status": 200 if backend_reachable else None,
                "loaded_model": "",
                "loaded_count": 0,
                "restart_required": False,
                "error": backend_error or None,
            },
            "operator_hints": [],
        }

    def _proxy_initializing_error_payload(self) -> dict:
        payload_fn = getattr(self.handler, "_initializing_error_payload", None)
        if callable(payload_fn):
            try:
                payload = payload_fn()
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
        return {
            "error": "proxy initializing",
            "reason": "model catalog and startup policy are still loading",
        }

    def _request_gate(self, upstream_path: str, client_model: str = "", stream: bool = False):
        gate = getattr(self.handler.__class__, "request_gate", None)

        @contextmanager
        def _ctx():
            if gate is None:
                yield
                return
            queued_at = time.time()
            acquired = gate.acquire(blocking=False)
            if not acquired:
                try:
                    self.handler.telemetry.emit("request_queued", {
                        "method": "POST",
                        "path": self.handler.path,
                        "upstream_path": upstream_path,
                        "model": client_model,
                        "stream": bool(stream),
                    })
                except Exception:
                    pass
                gate.acquire()
            wait_ms = round(max(0.0, time.time() - queued_at) * 1000.0, 2)
            try:
                self.handler.telemetry.emit("request_admitted", {
                    "method": "POST",
                    "path": self.handler.path,
                    "upstream_path": upstream_path,
                    "model": client_model,
                    "stream": bool(stream),
                    "wait_ms": wait_ms,
                })
            except Exception:
                pass
            try:
                yield
            finally:
                gate.release()

        return _ctx()

    def _refresh_codex_catalog(self, catalog) -> bool:
        try:
            from proxy.qz_codex_catalog import generate as generate_codex_catalog
        except ImportError:
            try:
                from qz_codex_catalog import generate as generate_codex_catalog
            except ImportError:
                return False
        try:
            inventory_path = Path(os.environ.get(
                "QZ_MODEL_INVENTORY_CACHE",
                str(_model_inventory_path()),
            ))
            catalog_dst = _codex_model_catalog_path()
            config_dst = _codex_config_path()
            catalog_dst.parent.mkdir(parents=True, exist_ok=True)
            generate_codex_catalog(inventory_path, catalog_dst, config_dst)
            return True
        except Exception:
            return False

    def _log_request_path(self, method):
        if self.handler.path.startswith("/qz/telemetry"):
            return
        self.handler.telemetry.emit("request_started", {
            "method": method,
            "path": self.handler.path,
            "accept": self.handler.headers.get("Accept", ""),
            "content_type": self.handler.headers.get("Content-Type", ""),
        })
        try:
            append_capture(
                "latest-paths.log",
                f"{time.time():.3f} {method} {self.handler.path} accept={self.handler.headers.get('Accept','')} content_type={self.handler.headers.get('Content-Type','')}\n",
            )
        except Exception:
            pass

    def handle_get(self):
        self._log_request_path("GET")
        if self.handler._handle_ollama_get():
            return

        if self.handler._handle_ready_get():
            return

        if self.handler.path == "/health":
            initialization = self.handler._initialization_payload()
            if not initialization.get("ready"):
                catalog_payload = {
                    "status": initialization.get("state") or "initializing",
                    "initialization": initialization,
                }
            else:
                catalog_payload = self.handler._model_catalog_payload()
            self.handler._send_json(200, {
                "status": "ok" if initialization.get("ready") else (initialization.get("state") or "initializing"),
                "upstream": self.handler.upstream,
                "proxy_initialization": initialization,
                "catalog": catalog_payload,
                "supports": list(CURRENT_API_ENDPOINTS),
                "api_contract": api_contract_payload(),
            })
            return

        if self.handler.path == "/qz/telemetry/state":
            try:
                runtime = self.handler._model_router().status_summary(self.handler.path)
            except Exception:
                runtime = None
            state = self.handler.telemetry.state(runtime=runtime)
            self.handler._send_json(200, state)
            return

        if self.handler.path.startswith("/qz/telemetry/recent"):
            limit = 100
            try:
                query = self.handler.path.split("?", 1)[1] if "?" in self.handler.path else ""
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                limit = int(params.get("limit", limit))
            except Exception:
                limit = 100
            try:
                runtime = self.handler._model_router().status_summary(self.handler.path)
            except Exception:
                runtime = None
            payload = self.handler.telemetry.recent_payload(limit=limit, runtime=runtime)
            payload["schema"] = TELEMETRY_RECENT_SCHEMA
            try:
                payload["vram"] = get_cached_vram_snapshot(handler=self.handler)
            except Exception:
                pass
            self.handler._send_json(200, payload)
            return

        if self.handler.path.startswith("/qz/telemetry/request"):
            limit = 200
            request_id = ""
            try:
                query = self.handler.path.split("?", 1)[1] if "?" in self.handler.path else ""
                params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
                request_id = params.get("request_id") or params.get("id") or ""
                limit = int(params.get("limit", limit))
            except Exception:
                limit = 200
            try:
                runtime = self.handler._model_router().status_summary(self.handler.path)
            except Exception:
                runtime = None
            payload = self.handler.telemetry.request_payload(request_id, limit=limit, runtime=runtime)
            payload["schema"] = TELEMETRY_REQUEST_SCHEMA
            self.handler._send_json(200, payload)
            return

        if self.handler.path in ("/qz/telemetry/events", "/qz/telemetry/stream"):
            self.handler._send_telemetry_sse()
            return

        if self.handler.path in ("/qz/config/effective", "/qz/config/paths"):
            self.handler._send_json(200, effective_config_payload(self.handler))
            return

        if self.handler.path == "/qz/web-search/capabilities":
            try:
                payload = build_web_search_capabilities(self._web_runtime())
            except Exception as exc:
                payload = {
                    "ok": False,
                    "schema": "qz.web_search.capabilities.v1",
                    "error": str(exc),
                }
            self.handler._send_json(200, payload)
            return

        if self.handler.path == "/qz/codex/client-config":
            self.handler._send_json(200, codex_client_config_payload())
            return

        if self.handler.path == "/qz/codex/model-catalog":
            catalog, error = codex_model_catalog_content()
            if catalog is not None:
                self.handler._send_json(200, catalog)
            else:
                self.handler._send_json(404, {
                    "ok": False,
                    "error": error or "missing_codex_catalog",
                    "remediation": "POST /qz/models/refresh",
                })
            return

        if self.handler.path == "/v1/models":
            self.handler._send_json(200, self.handler._model_catalog_payload())
            return

        if self.handler.path == "/qz/models":
            if not self._proxy_startup_ready():
                self.handler._send_json(503, self._proxy_initializing_error_payload())
                return
            catalog = self.handler._model_catalog()
            self.handler._send_json(200, {
                "catalog": catalog.to_payload(),
                "backend": self.handler._backend_models(),
            })
            return

        if self.handler.path == "/qz/model/status":
            if not self._proxy_startup_ready():
                self.handler._send_json(503, self._proxy_initializing_error_payload())
                return
            try:
                from .qz_model_status import build_model_status
            except ImportError:
                from qz_model_status import build_model_status
            try:
                payload = build_model_status(self.handler)
            except Exception as exc:
                self.handler._send_json(500, {"ok": False, "error": str(exc)})
                return
            self.handler._send_json(200, payload)
            return

        if self.handler.path == "/qz/backend/status":
            mgr = getattr(self.handler, "backend_manager", None)
            if mgr is None:
                self.handler._send_json(503, {
                    "ok": False, "error": "backend_manager not initialised",
                })
                return
            self.handler._send_json(200, mgr.status())
            return

        if self.handler.path in ("/qz/control-plane", "/qz/control-plane/status"):
            # Proxy-owned client/control-plane summary. Safe when backend is down.
            # Answers: proxy ready? catalog ready? models? backend reachable? loaded?
            # Remote clients do not need local Docker or llama.cpp access.
            try:
                payload = build_control_plane_status(self.handler)
            except Exception as exc:
                payload = {
                    "schema": "qz.control_plane.status.v1",
                    "ok": False,
                    "status": "error",
                    "error": str(exc),
                }
            self.handler._send_json(200, payload)
            return

        if self.handler.path in ("/qz/recovery/status", "/qz/recovery"):
            # Read-only recovery summary. Safe when backend is down.
            # No actions are taken — this is diagnostic and advisory only.
            # Remote clients do not need local Docker or llama.cpp access.
            try:
                cp = build_control_plane_status(self.handler)
                ss = cp.get("service_status") or {}
                rs = getattr(self.handler, "recovery_state", RECOVERY_STATE)
                runtime_snapshot = rs.snapshot() if rs is not None else None
                ar = getattr(self.handler, "active_requests", ACTIVE_REQUESTS)
                ar_snapshot = ar.snapshot() if ar is not None else None
                jobs = getattr(self.handler, "recovery_jobs", RECOVERY_JOBS)
                jobs_snapshot = jobs.snapshot() if jobs is not None else None
                recovery_payload = build_recovery_status(
                    ss,
                    runtime_state=runtime_snapshot,
                    active_requests=ar_snapshot,
                    recovery_jobs=jobs_snapshot,
                )
            except Exception as exc:
                recovery_payload = {
                    "schema": "qz.recovery.status.v1",
                    "ok": False,
                    "state": "unknown",
                    "recoverable": False,
                    "retryable": False,
                    "fatal": False,
                    "operator_action": "",
                    "last_error": str(exc),
                    "remote_client_action": "contact_operator",
                    "local_operator_action": "inspect_logs",
                    "summary": "Recovery status unavailable; error building control-plane payload.",
                    "service_status": None,
                    "operator_hints": [],
                }
            self.handler._send_json(200, recovery_payload)
            return

        if self.handler.path.startswith("/qz/recovery/jobs/"):
            self._handle_recovery_job_get()
            return

        self.proxy_raw("GET")

    # -------------------------------------------------------------------------
    # Recovery helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _recovery_error_payload(
        error: str,
        message: str,
        action: str = "",
        blocked_by: str = "bad_request",
        recovery_status: dict | None = None,
    ) -> dict:
        """Build a qz.recovery.error.v1 error payload for recovery endpoint failures."""
        return {
            "schema": "qz.recovery.error.v1",
            "ok": False,
            "error": error,
            "message": message,
            "action": action,
            "blocked_by": blocked_by,
            "retry_after_secs": None,
            "recovery_status": recovery_status,
        }

    @staticmethod
    def _is_local_request(handler) -> bool:
        """Return True if the request originates from loopback."""
        try:
            addr = handler.client_address
            host = addr[0] if isinstance(addr, (list, tuple)) else str(addr)
            return host in ("127.0.0.1", "::1", "localhost")
        except Exception:
            return False

    # ---- Recovery preflight helpers -------------------------------------

    @staticmethod
    def _recovery_authority_enabled() -> bool:
        """Return True when QZ_RECOVERY_ACTIONS=1."""
        return os.environ.get("QZ_RECOVERY_ACTIONS", "0").strip() == "1"

    @staticmethod
    def _recovery_local_only_enabled() -> bool:
        """Return True when QZ_RECOVERY_BIND_LOCAL_ONLY is not explicitly 0."""
        return os.environ.get("QZ_RECOVERY_BIND_LOCAL_ONLY", "1").strip() != "0"

    @staticmethod
    def _confirm_phrase_required(action: str) -> bool:
        """Return True when a confirmation phrase is required for this action.

        - Safe actions (refresh_catalog, clear_failure): never require phrase.
        - Dangerous actions (restart_backend): always require phrase, regardless of env.
          The env var QZ_RECOVERY_CONFIRM_PHRASE must also be set before they proceed.
        - Other actions: required only when env phrase is set.
        """
        if action in SAFE_TRIGGER_ACTIONS:
            return False
        if action in DANGEROUS_TRIGGER_ACTIONS:
            return True
        return bool(os.environ.get("QZ_RECOVERY_CONFIRM_PHRASE", "").strip())

    @staticmethod
    def _confirm_phrase_matches(body: dict, action: str) -> tuple[bool, str]:
        """Validate the confirmation phrase for the requested action.

        Returns (ok, error_message).
        ok=True when phrase validation passes. ok=False with a message when it fails.

        Safe actions: always (True, "") — low-friction path.
        Dangerous actions: QZ_RECOVERY_CONFIRM_PHRASE must be set AND body.confirm
            must exactly match it. If env var is unset, rejected even if confirm is given.
        """
        if action in SAFE_TRIGGER_ACTIONS:
            return True, ""

        phrase = os.environ.get("QZ_RECOVERY_CONFIRM_PHRASE", "").strip()

        if action in DANGEROUS_TRIGGER_ACTIONS:
            if not phrase:
                return False, (
                    f"Action {action!r} requires QZ_RECOVERY_CONFIRM_PHRASE to be set "
                    "before restart actions are permitted. Set the env var to a non-empty phrase."
                )
            provided = str(body.get("confirm") or "").strip()
            if not provided:
                return False, (
                    f"Action {action!r} requires a 'confirm' field in the request body "
                    "that exactly matches QZ_RECOVERY_CONFIRM_PHRASE."
                )
            if provided == phrase:
                return True, ""
            return False, f"Confirmation phrase mismatch for action {action!r}."

        # Other (future) actions: apply phrase check only when env is set
        if not phrase:
            return True, ""
        provided = str(body.get("confirm") or "").strip()
        if not provided:
            return False, f"Action {action!r} requires a confirmation phrase."
        if provided == phrase:
            return True, ""
        return False, f"Confirmation phrase mismatch for action {action!r}."

    @staticmethod
    def _validate_recovery_trigger_body(
        body: dict,
    ) -> tuple[int, str, str, str] | None:
        """Validate request body fields for POST /qz/recovery/trigger.

        Returns None on success.
        Returns (status, error_code, blocked_by, message) on any validation failure.

        Checks in order:
        1. action present
        2. reason present
        3. action in ALLOWED_RECOVERY_ACTIONS
        4. action not in UNIMPLEMENTED_TRIGGER_ACTIONS
        5. force=true invalid for safe actions
        """
        action = str(body.get("action") or "")
        reason = str(body.get("reason") or "")
        force  = bool(body.get("force", False))

        if not action:
            return 400, "missing_action", "bad_request", \
                "Request body must include an 'action' field."

        if not reason:
            return 400, "missing_reason", "bad_request", \
                "Request body must include a 'reason' field explaining why the action is needed."

        if action not in ALLOWED_RECOVERY_ACTIONS:
            return 400, "unknown_action", "unknown_action", (
                f"Unknown action {action!r}. Allowed: {sorted(ALLOWED_RECOVERY_ACTIONS)}."
            )

        if action in UNIMPLEMENTED_TRIGGER_ACTIONS:
            return 409, "action_not_implemented", "state", (
                f"Action {action!r} is not yet implemented. "
                "Implemented: refresh_catalog, clear_failure, start_backend, "
                "restart_backend, reload_selected_model, select_model."
            )

        # select_model requires a model field
        if action == "select_model":
            model_val = str(body.get("model") or "")
            if not model_val:
                return 400, "missing_model", "bad_request", \
                    "Request body must include a 'model' field for select_model."

        # force=true is only valid for dangerous (interrupt) actions, not safe ones
        if force and action in SAFE_TRIGGER_ACTIONS:
            return 400, "force_not_allowed", "bad_request", (
                f"force=true is not allowed for {action!r}; "
                "it applies only to interrupt actions (restart_backend)."
            )

        return None  # all fields valid

    def _handle_recovery_job_get(self) -> None:
        """Handle GET /qz/recovery/jobs/<request_id> — return async job status."""
        path = self.handler.path
        request_id = path[len("/qz/recovery/jobs/"):].strip("/")
        if not request_id:
            self.handler._send_json(400, self._recovery_error_payload(
                "missing_request_id",
                "GET /qz/recovery/jobs/<request_id> requires a request_id in the path.",
                blocked_by="bad_request",
            ))
            return
        jobs = getattr(self.handler, "recovery_jobs", RECOVERY_JOBS)
        job = jobs.get(request_id) if jobs is not None else None
        if job is None:
            self.handler._send_json(404, self._recovery_error_payload(
                "job_not_found",
                f"No async recovery job found with request_id {request_id!r}.",
                blocked_by="state",
            ))
            return
        self.handler._send_json(200, job)

    def _emit_recovery_event(self, event_type: str, payload: dict) -> None:
        """Emit a recovery telemetry event. No-ops safely if telemetry unavailable."""
        try:
            self.handler.telemetry.emit(event_type, payload)
        except Exception:
            pass

    def _get_recovery_status_snapshot(self) -> dict | None:
        """Build a current qz.recovery.status.v1 snapshot for pre/post_status fields."""
        try:
            cp = build_control_plane_status(self.handler)
            ss = cp.get("service_status") or {}
            rs = getattr(self.handler, "recovery_state", RECOVERY_STATE)
            runtime_snap = rs.snapshot() if rs is not None else None
            return build_recovery_status(ss, runtime_state=runtime_snap)
        except Exception:
            return None

    @staticmethod
    def _build_trigger_response(
        action: str,
        request_id: str,
        accepted: bool,
        pre_status: dict | None,
        post_status: dict | None,
        operator_warning: str = "",
        telemetry_event: str = "",
    ) -> dict:
        """Build a qz.recovery.trigger.v1 response payload."""
        return {
            "schema": RECOVERY_TRIGGER_SCHEMA,
            "accepted": accepted,
            "action": action,
            "dry_run": False,
            "request_id": request_id,
            "pre_status": pre_status,
            "post_status": post_status,
            "operator_warning": operator_warning,
            "telemetry_event": telemetry_event,
        }

    def _do_refresh_catalog(self) -> tuple[bool, str]:
        """Execute catalog refresh: rescan + regenerate Codex catalog.

        Does not call Docker. Does not modify backend or model process state.
        Returns (success, error_message).
        """
        try:
            if not self._proxy_startup_ready():
                return False, "proxy not ready for catalog refresh"
            catalog = self.handler._model_catalog()
            catalog.refresh()
            self._refresh_codex_catalog(catalog)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _selected_model_for_reload(self) -> tuple[str, str]:
        """Resolve the selected model id for reload_selected_model.

        Returns (model_id, error_message). model_id is empty on failure.
        Uses the persisted proxy model-state selection.
        """
        try:
            try:
                from .qz_model_state import load_model_state, selected_identity_from_state
            except ImportError:
                from qz_model_state import load_model_state, selected_identity_from_state
            state_path = self._model_state_path_for_endpoint()
            state = load_model_state(state_path).state if state_path else None
            model_id = selected_identity_from_state(state) if state else ""
            if not model_id:
                return "", (
                    "No model selected. Use POST /qz/model/select-and-restart to choose a model "
                    "before calling reload_selected_model."
                )
            return model_id, ""
        except Exception as exc:
            return "", str(exc)

    def _do_reload_selected_model(self) -> tuple[bool, str]:
        """Restart the backend with the currently selected model.

        Direct -m mode is the only supported runtime path.
        Returns (success, error_message). Never raises.
        """
        try:
            model_id, resolve_err = self._selected_model_for_reload()
            if not model_id:
                return False, resolve_err or "No model selected."
            self._invoke_selected_model_reload(model_id)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _do_select_model(self, model: str) -> tuple[bool, str]:
        """Select a model in catalog/router state without loading it.

        Uses catalog.resolve() for lookup, then sets catalog.selected and persists
        model state via the model router. Does NOT call backend.load_model(),
        start_container(), or restart_container(). Does NOT load the model.
        Returns (success, error_message). Never raises.
        """
        try:
            if not model:
                return False, "No model specified."

            catalog = self.handler._model_catalog()
            selected, reason = catalog.resolve(query=model)
            if selected is None:
                return False, f"Model {model!r} not found in catalog: {reason}"
            if selected.get("profile_valid", True) is False:
                return False, f"Model {model!r} has an invalid profile (profile_valid=false)."

            # Set catalog selection (no backend interaction)
            catalog.selected = selected
            catalog.reason = reason

            # Persist selection via model router (no load, no backend call)
            router = self.handler._model_router()
            router._persist_model_state(
                selected,
                reason="recovery select_model",
                source="recovery_select_model",
            )

            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _do_start_backend(self) -> tuple[bool, str]:
        """Start BackendManager with the selected direct -m launch model.

        Returns (success, error_message). Never raises.
        """
        try:
            model_id, resolve_err = self._selected_model_for_reload()
            if not model_id:
                return False, resolve_err or "No model selected."
            mgr = getattr(self.handler, "backend_manager", None)
            if mgr is None:
                return False, "backend_manager not initialised"
            self._set_manager_launch_model(model_id, mgr)
            result = mgr.start()
            return bool(result.get("ok")), str(result.get("error") or "")
        except Exception as exc:
            error = str(exc)
            try:
                cls = self.handler.__class__
                cls.model_load_state = "failed"
                cls.model_load_error = error
                cls.model_load_finished_at = time.time()
            except Exception:
                pass
            return False, error

    def _do_restart_backend(self) -> tuple[bool, str]:
        """Restart BackendManager with the selected direct -m launch model.

        Returns (success, error_message). Never raises.
        """
        try:
            model_id, resolve_err = self._selected_model_for_reload()
            if not model_id:
                return False, resolve_err or "No model selected."
            mgr = getattr(self.handler, "backend_manager", None)
            if mgr is None:
                return False, "backend_manager not initialised"
            self._set_manager_launch_model(model_id, mgr)
            result = mgr.restart()
            return bool(result.get("ok")), str(result.get("error") or "")
        except Exception as exc:
            error = str(exc)
            try:
                cls = self.handler.__class__
                cls.model_load_state = "failed"
                cls.model_load_error = error
                cls.model_load_finished_at = time.time()
            except Exception:
                pass
            return False, error

    @staticmethod
    def _do_clear_failure(rs) -> tuple[bool, str]:
        """Clear in-memory recovery failure state.

        Clears RECOVERY_STATE only (slice 9 scope). Does not modify model_load_state,
        backend state files, var/model-state.json, or any process state.
        Returns (success, error_message).
        """
        try:
            rs.clear()
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _handle_recovery_trigger(self) -> None:
        """Handle POST /qz/recovery/trigger — state-changing recovery actions.

        Only refresh_catalog and clear_failure are implemented in slice 9.
        Requires QZ_RECOVERY_ACTIONS=1 and a local (loopback) request when
        QZ_RECOVERY_BIND_LOCAL_ONLY=1 (the default).

        No Docker calls. No backend/model restart. No automatic recovery loops.
        """
        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length) if length else b""

        # --- Parse body ---
        try:
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except Exception as exc:
            self.handler._send_json(400, self._recovery_error_payload(
                "invalid_json",
                f"Request body is not valid JSON: {exc}",
                blocked_by="bad_request",
            ))
            return

        if not isinstance(body, dict):
            self.handler._send_json(400, self._recovery_error_payload(
                "invalid_json",
                "Request body must be a JSON object.",
                blocked_by="bad_request",
            ))
            return

        action_hint = str(body.get("action") or "")

        # --- Authority check (must be first gate) ---
        if not self._recovery_authority_enabled():
            self._emit_recovery_event("recovery_trigger_rejected", {
                "source": "recovery_trigger",
                "reason": "authority_disabled",
                "action": action_hint,
            })
            self.handler._send_json(403, self._recovery_error_payload(
                "authority_disabled",
                "QZ_RECOVERY_ACTIONS is not set to 1; state-changing recovery is disabled. "
                "Set QZ_RECOVERY_ACTIONS=1 to enable.",
                action=action_hint,
                blocked_by="authority",
            ))
            return

        # --- Locality check ---
        if self._recovery_local_only_enabled() and not self._is_local_request(self.handler):
            self._emit_recovery_event("recovery_trigger_rejected", {
                "source": "recovery_trigger",
                "reason": "non_local_request",
                "action": action_hint,
            })
            self.handler._send_json(403, self._recovery_error_payload(
                "non_local_request",
                "QZ_RECOVERY_BIND_LOCAL_ONLY is enabled; trigger requires a local (loopback) request.",
                action=action_hint,
                blocked_by="authority",
            ))
            return

        # --- Body field validation ---
        val_err = self._validate_recovery_trigger_body(body)
        if val_err is not None:
            status, error, blocked_by, message = val_err
            action_for_err = str(body.get("action") or "")
            recovery_status = self._get_recovery_status_snapshot() if status == 409 else None
            self.handler._send_json(status, self._recovery_error_payload(
                error,
                message,
                action=action_for_err,
                blocked_by=blocked_by,
                recovery_status=recovery_status,
            ))
            return

        action          = str(body.get("action") or "")
        reason          = str(body.get("reason") or "")
        force           = bool(body.get("force", False))
        async_requested = bool(body.get("async", False))

        # --- Async support check (early: before runtime/planner) ---
        if async_requested and action not in ASYNC_SUPPORTED_ACTIONS:
            self.handler._send_json(400, self._recovery_error_payload(
                "async_not_supported",
                f"async=true is currently supported only for {sorted(ASYNC_SUPPORTED_ACTIONS)}. "
                f"Action {action!r} must use the synchronous trigger path.",
                action=action,
                blocked_by="bad_request",
            ))
            return

        # --- Confirmation phrase check ---
        confirm_ok, confirm_msg = self._confirm_phrase_matches(body, action)
        if not confirm_ok:
            self.handler._send_json(400, self._recovery_error_payload(
                "bad_confirm",
                confirm_msg,
                action=action,
                blocked_by="bad_confirm",
            ))
            return

        # --- Runtime state checks (with advisory recovery_status) ---
        rs = getattr(self.handler, "recovery_state", RECOVERY_STATE)
        runtime_rs = self._get_recovery_status_snapshot()

        if rs.is_recovery_in_progress():
            self.handler._send_json(423, self._recovery_error_payload(
                "recovery_in_progress",
                "Another recovery action is already in progress; wait for it to complete.",
                action=action,
                blocked_by="state",
                recovery_status=runtime_rs,
            ))
            return

        if rs.is_backoff_active(action):
            self.handler._send_json(429, self._recovery_error_payload(
                "backoff_active",
                f"Backoff or manual_required is active for {action!r}; "
                "retry after the backoff window expires or call clear_failure.",
                action=action,
                blocked_by="backoff",
                recovery_status=runtime_rs,
            ))
            return

        # --- For dangerous actions: run the planner for additional preflight ---
        if action in DANGEROUS_TRIGGER_ACTIONS:
            try:
                cp = build_control_plane_status(self.handler)
                ss_for_plan = cp.get("service_status") or {}
            except Exception:
                ss_for_plan = {}
            ar = getattr(self.handler, "active_requests", ACTIVE_REQUESTS)
            active_count = ar.count() if ar is not None else None
            plan = build_recovery_plan(
                ss_for_plan,
                action,
                force=force,
                authority_enabled=True,
                local_request=True,
                active_requests=active_count,
                backoff_active=rs.is_backoff_active(action),
                recovery_in_progress=rs.is_recovery_in_progress(),
            )
            if not plan["feasible"]:
                _flag_to_blocked = [
                    ("blocked_by_active_requests", "active_requests"),
                    ("blocked_by_in_progress",     "in_progress"),
                    ("blocked_by_backoff",          "backoff"),
                    ("blocked_by_state",            "state"),
                    ("blocked_by_authority",        "authority"),
                ]
                blocked_by_name = "state"
                for flag, name in _flag_to_blocked:
                    if plan.get(flag):
                        blocked_by_name = name
                        break
                self.handler._send_json(409, self._recovery_error_payload(
                    "plan_not_feasible",
                    f"Recovery plan for {action!r} is not feasible. "
                    + (plan.get("notes") or [""])[0],
                    action=action,
                    blocked_by=blocked_by_name,
                    recovery_status=runtime_rs,
                ))
                return

        # --- select_model model existence pre-check ---
        if action == "select_model":
            _sm_model = str(body.get("model") or "")
            try:
                _sm_catalog = self.handler._model_catalog()
                _sm_entry, _sm_reason = _sm_catalog.resolve(query=_sm_model)
                if _sm_entry is None:
                    self.handler._send_json(409, self._recovery_error_payload(
                        "model_not_found",
                        f"Model {_sm_model!r} not found in catalog: {_sm_reason}",
                        action=action,
                        blocked_by="state",
                        recovery_status=runtime_rs,
                    ))
                    return
                if _sm_entry.get("profile_valid", True) is False:
                    self.handler._send_json(409, self._recovery_error_payload(
                        "model_invalid_profile",
                        f"Model {_sm_model!r} has an invalid profile and cannot be selected.",
                        action=action,
                        blocked_by="state",
                        recovery_status=runtime_rs,
                    ))
                    return
            except Exception as exc:
                self.handler._send_json(409, self._recovery_error_payload(
                    "catalog_unavailable",
                    f"Catalog unavailable for model resolution: {exc}",
                    action=action,
                    blocked_by="state",
                    recovery_status=runtime_rs,
                ))
                return

        # --- Selected model pre-check (before mark_started to avoid spurious backoff) ---
        if action == "reload_selected_model":
            precheck_model_id, precheck_err = self._selected_model_for_reload()
            if not precheck_model_id:
                self.handler._send_json(409, self._recovery_error_payload(
                    "selected_model_missing",
                    precheck_err or "No model selected; use /qz/model/select-and-restart before reload.",
                    action=action,
                    blocked_by="state",
                    recovery_status=runtime_rs,
                ))
                return

        # --- Execute ---
        request_id = f"rec-{uuid.uuid4().hex[:12]}"
        pre_status = self._get_recovery_status_snapshot()
        ar = getattr(self.handler, "active_requests", ACTIVE_REQUESTS)
        active_count_at_start = ar.count() if ar is not None else None

        base_event: dict = {
            "source": "recovery_trigger",
            "request_id": request_id,
            "action": action,
            "reason": reason,
            "force": force,
            "async": async_requested,
            "model": str(body.get("model") or "") if action == "select_model" else None,
            "active_request_count": active_count_at_start,
            "pre_recovery_status": pre_status,
            "operator_action": action,
            "local_operator_required": True,
            "error": None,
        }

        if async_requested:
            # --- Async path: accept immediately, run in daemon thread ---
            job_store = getattr(self.handler, "recovery_jobs", RECOVERY_JOBS)
            job = job_store.create(
                request_id=request_id,
                action=action,
                pre_status=pre_status,
                operator_warning=_TRIGGER_WARNINGS.get(action, ""),
            ) if job_store is not None else {}

            # Mark in-progress before returning so concurrent requests get 423.
            rs.mark_started(action, request_id=request_id)
            if job_store is not None:
                job_store.mark_running(request_id)

            self._emit_recovery_event("recovery_trigger_requested", base_event)
            self._emit_recovery_event("recovery_action_started", base_event)

            worker = threading.Thread(
                target=_async_recovery_worker,
                args=(self, action, request_id, base_event, rs, job_store),
                daemon=True,
                name=f"qz-recovery-{action}-{request_id}",
            )
            worker.start()

            response = {
                **self._build_trigger_response(
                    action=action,
                    request_id=request_id,
                    accepted=True,
                    pre_status=pre_status,
                    post_status=None,
                    operator_warning=_TRIGGER_WARNINGS.get(action, ""),
                    telemetry_event="recovery_action_started",
                ),
                "async": True,
                "job": job,
            }
            self.handler._send_json(202, response)
            return

        # --- Sync path (default) ---
        self._emit_recovery_event("recovery_trigger_requested", base_event)

        rs.mark_started(action, request_id=request_id)
        self._emit_recovery_event("recovery_action_started", base_event)

        try:
            if action == "refresh_catalog":
                ok, error = self._do_refresh_catalog()
            elif action == "clear_failure":
                ok, error = self._do_clear_failure(rs)
            elif action == "select_model":
                ok, error = self._do_select_model(str(body.get("model") or ""))
            elif action == "start_backend":
                ok, error = self._do_start_backend()
            elif action == "restart_backend":
                ok, error = self._do_restart_backend()
            elif action == "reload_selected_model":
                ok, error = self._do_reload_selected_model()
            else:
                ok, error = False, f"Unhandled action: {action!r}"
        except Exception as exc:
            ok, error = False, str(exc)

        if ok:
            rs.mark_completed(action)
            post_status = self._get_recovery_status_snapshot()
            self._emit_recovery_event("recovery_action_completed", {
                **base_event,
                "post_recovery_status": post_status,
            })
            self.handler._send_json(200, self._build_trigger_response(
                action=action,
                request_id=request_id,
                accepted=True,
                pre_status=pre_status,
                post_status=post_status,
                operator_warning=_TRIGGER_WARNINGS.get(action, ""),
                telemetry_event="recovery_action_completed",
            ))
        else:
            rs.mark_failed(action, error)
            self._emit_recovery_event("recovery_action_failed", {
                **base_event, "error": error,
            })
            if rs.is_backoff_active(action):
                self._emit_recovery_event("recovery_backoff_started", {
                    **base_event, "error": error,
                })
            self.handler._send_json(500, self._recovery_error_payload(
                "action_failed",
                f"Action {action!r} failed unexpectedly: {error}",
                action=action,
                blocked_by="state",
            ))

    def _handle_recovery_plan(self) -> None:
        """Handle POST /qz/recovery/plan — dry-run feasibility planner.

        Reads body, validates action, calls build_recovery_plan(), returns plan.
        No side effects. No state mutation. No Docker calls. No telemetry emitted.
        """
        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length) if length else b""

        # --- Parse body ---
        try:
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except Exception as exc:
            self.handler._send_json(400, self._recovery_error_payload(
                "invalid_json",
                f"Request body is not valid JSON: {exc}",
                blocked_by="bad_request",
            ))
            return

        if not isinstance(body, dict):
            self.handler._send_json(400, self._recovery_error_payload(
                "invalid_json",
                "Request body must be a JSON object.",
                blocked_by="bad_request",
            ))
            return

        action = body.get("action", "")
        if not action:
            self.handler._send_json(400, self._recovery_error_payload(
                "missing_action",
                "Request body must include an 'action' field.",
                blocked_by="bad_request",
            ))
            return

        if action not in ALLOWED_RECOVERY_ACTIONS:
            self.handler._send_json(400, self._recovery_error_payload(
                "unknown_action",
                f"Unknown action {action!r}. Allowed: {sorted(ALLOWED_RECOVERY_ACTIONS)}.",
                action=action,
                blocked_by="unknown_action",
            ))
            return

        # --- Gather planner inputs from current runtime state ---
        try:
            cp = build_control_plane_status(self.handler)
            ss = cp.get("service_status") or {}
        except Exception:
            ss = {}

        model         = str(body.get("model") or "")
        force         = bool(body.get("force", False))
        authority     = os.environ.get("QZ_RECOVERY_ACTIONS", "0").strip() == "1"
        local_req     = self._is_local_request(self.handler)
        rs            = getattr(self.handler, "recovery_state", RECOVERY_STATE)
        backoff_act   = rs.is_backoff_active(action) if rs is not None else False
        in_progress   = rs.is_recovery_in_progress() if rs is not None else False
        ar            = getattr(self.handler, "active_requests", ACTIVE_REQUESTS)
        active_count  = ar.count() if ar is not None else None

        plan = build_recovery_plan(
            ss,
            action,
            model=model,
            force=force,
            authority_enabled=authority,
            local_request=local_req,
            active_requests=active_count,
            backoff_active=backoff_act,
            recovery_in_progress=in_progress,
        )

        self.handler._send_json(200, plan)

    def handle_post(self):
        self._log_request_path("POST")

        if self.handler._handle_ollama_post():
            return

        if self.handler.path in LEGACY_API_ENDPOINTS:
            self.handler._mark_deprecated_endpoint(self.handler.path)
            self.proxy_json_api("/v1/chat/completions")
            return

        if self.handler.path in ("/responses/compact", "/v1/responses/compact"):
            self.handler._handle_responses_compact()
            return

        if self.handler.path in ("/responses", "/v1/responses"):
            self.proxy_json_api("/v1/responses")
            return

        if self.handler.path in ("/qz/backend/start", "/qz/backend/stop",
                                   "/qz/backend/restart"):
            mgr = getattr(self.handler, "backend_manager", None)
            if mgr is None:
                self.handler._send_json(503, {
                    "ok": False, "error": "backend_manager not initialised",
                })
                return
            action = self.handler.path.split("/")[-1]
            result = getattr(mgr, action)()
            status_code = 200 if result.get("ok") else 409
            self.handler._send_json(status_code, result)
            return

        if self.handler.path == "/qz/recovery/plan":
            self._handle_recovery_plan()
            return

        if self.handler.path == "/qz/recovery/trigger":
            self._handle_recovery_trigger()
            return

        if self.handler.path == "/qz/codex/model-catalog/refresh":
            # Slice F follow-up: explicit catalog-only refresh.  Does NOT
            # touch backend selection or trigger any model load.  qz-codex
            # bootstrap calls this when /qz/codex/client-config reports the
            # generated catalog is stale relative to the model inventory.
            if not self._proxy_startup_ready():
                self.handler._send_json(503, self._proxy_initializing_error_payload())
                return
            try:
                from .qz_codex_client_config import codex_client_config_payload
            except ImportError:
                from qz_codex_client_config import codex_client_config_payload
            catalog = self.handler._model_catalog()
            # ModelCatalog.refresh() rescans var/models and rewrites the
            # model-inventory cache used as the source of truth for the
            # Codex catalog.  _refresh_codex_catalog then regenerates the
            # Codex-side artifact from that inventory.
            try:
                catalog.refresh()
            except Exception as exc:
                self.handler._send_json(500, {
                    "ok": False,
                    "error": f"catalog refresh failed: {exc}",
                })
                return
            written = self._refresh_codex_catalog(catalog)
            self.handler._send_json(200, {
                "ok": True,
                "schema": "qz.codex.catalog.refresh.v1",
                "catalog_updated": written,
                "client_config": codex_client_config_payload(),
            })
            return

        if self.handler.path == "/qz/models/refresh":
            initialization = self.handler._initialization_payload()
            if not self._proxy_startup_ready():
                self.handler._send_json(503, {
                    "schema": "qz.codex.catalog.refresh.v1",
                    "ok": False,
                    "error": "proxy not ready",
                    "reason": initialization.get("state") or "model catalog and startup policy are still loading",
                    "proxy_initialization": initialization,
                    "catalog_updated": False,
                    "catalog_path": None,
                    "model_ids": [],
                    "model_count": 0,
                    # backwards-compatible field
                    "codex_catalog_updated": False,
                })
                return
            catalog = self.handler._model_catalog()
            catalog.refresh()
            codex_catalog_written = self._refresh_codex_catalog(catalog)

            # Build model_ids from valid catalog entries
            _model_ids = [
                e.get("key") or e.get("stem") or e.get("backend_id") or ""
                for e in catalog.entries
                if isinstance(e, dict) and e.get("profile_valid", True) is not False
            ]
            _model_ids = [mid for mid in _model_ids if mid]

            # Catalog path from qz_paths (QZ_VAR_DIR / generated / codex), not CODEX_HOME
            _catalog_path = str(_codex_model_catalog_path())

            self.handler._send_json(200, {
                "schema": "qz.codex.catalog.refresh.v1",
                "ok": True,
                "catalog_updated": codex_catalog_written,
                "catalog_path": _catalog_path if codex_catalog_written else None,
                "model_count": len(_model_ids),
                "model_ids": _model_ids,
                "proxy_initialization": initialization,
                "error": None,
                # backwards-compatible existing fields
                "catalog": catalog.to_payload(),
                "backend": self.handler._backend_models(),
                "codex_catalog_updated": codex_catalog_written,
            })
            return

        if self.handler.path == "/qz/model/select":
            self._handle_model_select_endpoint(restart=False)
            return

        if self.handler.path == "/qz/model/select-and-restart":
            self._handle_model_select_endpoint(restart=True)
            return

        if self.handler.path == "/qz/model/reload":
            self._handle_model_reload_endpoint()
            return

        if self.handler.path in ("/qz/models/load", "/qz/models/select"):
            self.handler._send_json(410, {
                "ok": False,
                "error": "legacy router model-loading endpoint removed",
                "message": "Use /qz/model/select-and-restart.",
            })
            return

        self.proxy_raw("POST")

    # ------------------------------------------------------------------
    # Slice C: /qz/model/* endpoint handlers — proxy-owned model selection
    # ------------------------------------------------------------------

    def _model_state_path_for_endpoint(self) -> Path | None:
        raw = getattr(self.handler, "model_state_path", None)
        if isinstance(raw, str) and raw.strip():
            return Path(raw).expanduser()
        env_path = os.environ.get("QZ_MODEL_STATE_PATH")
        if env_path:
            return Path(env_path).expanduser()
        return None

    def _handle_model_select_endpoint(self, restart: bool) -> None:
        """Handle POST /qz/model/select or /qz/model/select-and-restart."""
        if not self._proxy_startup_ready():
            self.handler._send_json(503, self._proxy_initializing_error_payload())
            return

        try:
            from .qz_model_status import build_model_status
            from .qz_model_state import (
                SELECTED_SOURCES,
                load_model_state,
                state_from_selection,
                update_load_observation,
                write_model_state,
            )
            from .qz_model_catalog import match_model
        except ImportError:
            from qz_model_status import build_model_status
            from qz_model_state import (
                SELECTED_SOURCES,
                load_model_state,
                state_from_selection,
                update_load_observation,
                write_model_state,
            )
            from qz_model_catalog import match_model

        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self.handler._send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self.handler._send_json(400, {"ok": False, "error": "request body must be an object"})
            return

        requested = body.get("model") or body.get("key") or body.get("name")
        if not isinstance(requested, str) or not requested.strip():
            self.handler._send_json(400, {
                "ok": False,
                "error": "missing 'model' field in request body",
            })
            return

        source = body.get("source") or "operator"
        if source not in SELECTED_SOURCES:
            self.handler._send_json(400, {
                "ok": False,
                "error": f"unknown selected_source {source!r}; must be one of {sorted(SELECTED_SOURCES)}",
            })
            return

        catalog = self.handler._model_catalog()
        entry = match_model(catalog.entries, requested.strip())
        if entry is None:
            self.handler._send_json(404, {
                "ok": False,
                "error": f"model {requested!r} not found in catalog",
                "available_count": len(catalog.entries),
            })
            return
        if entry.get("profile_valid", True) is False:
            self.handler._send_json(400, {
                "ok": False,
                "error": f"profile invalid for model {requested!r}",
                "profile_error": entry.get("profile_error"),
            })
            return

        state_path = self._model_state_path_for_endpoint()
        existing = load_model_state(state_path).state if state_path else None
        runtime_context = entry.get("runtime_context_length")
        new_state = state_from_selection(
            entry,
            source=source,
            reason=f"POST {self.handler.path}",
            runtime_context_length=runtime_context if isinstance(runtime_context, int) else None,
        )
        if existing is not None:
            new_state = update_load_observation(
                new_state,
                loaded_model=existing.last_loaded_model,
                result=existing.last_load_result,
                error=existing.last_load_error,
                error_type=existing.last_load_error_type,
            )
            # Preserve recovery memory across a selection write — last_good_*
            # is the recently-confirmed-loaded selection, and any in-flight
            # failed_candidate_* should survive until cleared by a success.
            new_state.last_good_key = existing.last_good_key
            new_state.last_good_backend_id = existing.last_good_backend_id
            new_state.last_good_label = existing.last_good_label
            new_state.last_good_source = existing.last_good_source
            new_state.last_good_loaded_at = existing.last_good_loaded_at
            new_state.failed_candidate_key = existing.failed_candidate_key
            new_state.failed_candidate_backend_id = existing.failed_candidate_backend_id
            new_state.failed_candidate_label = existing.failed_candidate_label
            new_state.failed_candidate_at = existing.failed_candidate_at
        try:
            write_model_state(new_state, state_path)
        except Exception as exc:
            self.handler._send_json(500, {
                "ok": False,
                "error": f"failed to persist model state: {exc}",
            })
            return

        # Keep the in-memory ModelCatalog.selected aligned with the new persisted choice.
        try:
            catalog.select(query=requested.strip())
        except Exception:
            pass

        if not restart:
            try:
                payload = build_model_status(self.handler)
            except Exception as exc:
                self.handler._send_json(500, {"ok": False, "error": str(exc)})
                return
            self.handler._send_json(200, payload)
            return

        # select-and-restart: invoke the existing resolve+load path which handles
        # llama-server unload/load and (when needed) BackendManager context restart.
        # rollback_on_failure (body field, default True) controls whether
        # selected_* rolls back to last_good_* when the candidate fails.
        rollback_on_failure = True
        if "rollback_on_failure" in body:
            raw = body.get("rollback_on_failure")
            if isinstance(raw, bool):
                rollback_on_failure = raw
            elif isinstance(raw, str):
                rollback_on_failure = raw.strip().lower() not in ("0", "false", "no", "off")
        resolve_error: str | None = None
        try:
            self._invoke_selected_model_reload(requested.strip())
        except Exception as exc:
            resolve_error = str(exc)

        failed, _err, _err_type, _rollback = self._record_load_observation(
            resolve_succeeded=resolve_error is None,
            resolve_error=resolve_error,
            rollback_on_failure=rollback_on_failure,
        )

        payload = build_model_status(self.handler)
        if failed:
            payload["ok"] = False
            if resolve_error:
                payload["error"] = resolve_error
            # 409 Conflict: the request was valid, but the selected model
            # cannot become the active backend model.
            self.handler._send_json(409, payload)
            return

        self.handler._send_json(200, payload)

    def _handle_model_reload_endpoint(self) -> None:
        """Handle POST /qz/model/reload using the current persisted selection."""
        if not self._proxy_startup_ready():
            self.handler._send_json(503, self._proxy_initializing_error_payload())
            return

        try:
            from .qz_model_status import build_model_status
            from .qz_model_state import load_model_state, selected_identity_from_state
            from .qz_model_catalog import match_model
        except ImportError:
            from qz_model_status import build_model_status
            from qz_model_state import load_model_state, selected_identity_from_state
            from qz_model_catalog import match_model

        state_path = self._model_state_path_for_endpoint()
        state = load_model_state(state_path).state if state_path else None
        identity = selected_identity_from_state(state) if state else ""
        if not identity:
            self.handler._send_json(409, {
                "ok": False,
                "error": "no selected model; POST /qz/model/select first",
            })
            return

        catalog = self.handler._model_catalog()
        entry = match_model(catalog.entries, identity)
        if entry is None:
            self.handler._send_json(404, {
                "ok": False,
                "error": f"selected model {identity!r} not found in current catalog",
            })
            return

        # POST /qz/model/reload has no body; rollback is the safe default
        # (matches /qz/model/select-and-restart with rollback_on_failure=true).
        resolve_error: str | None = None
        try:
            self._invoke_selected_model_reload(identity)
        except Exception as exc:
            resolve_error = str(exc)

        failed, _err, _err_type, _rollback = self._record_load_observation(
            resolve_succeeded=resolve_error is None,
            resolve_error=resolve_error,
            rollback_on_failure=True,
        )

        payload = build_model_status(self.handler)
        if failed:
            payload["ok"] = False
            if resolve_error:
                payload["error"] = resolve_error
            self.handler._send_json(409, payload)
            return

        self.handler._send_json(200, payload)

    def _record_load_observation(
        self,
        *,
        resolve_succeeded: bool,
        resolve_error: str | None,
        rollback_on_failure: bool = True,
    ) -> tuple[bool, str | None, str | None, bool]:
        """Inspect recent backend logs after a reload attempt and update state.

        Returns ``(failed, error, error_type, rollback_performed)``.

        On success: routes through ``mark_load_success`` which clears
        ``last_load_error*`` and refreshes ``last_good_*`` from the current
        selection.

        On failure: routes through ``mark_load_failure`` which records
        ``failed_candidate_*`` and (when ``rollback_on_failure`` is True and
        a ``last_good_*`` exists) rolls back ``selected_*``.

        Always writes the updated qz.model_state.v1 file so /qz/model/status
        and /qz/control-plane reflect the most recent observation.
        """
        try:
            from .qz_model_state import (
                load_model_state,
                mark_load_failure,
                mark_load_success,
                write_model_state,
            )
            from .qz_model_load_failure import classify_model_load_failure
        except ImportError:
            from qz_model_state import (
                load_model_state,
                mark_load_failure,
                mark_load_success,
                write_model_state,
            )
            from qz_model_load_failure import classify_model_load_failure

        state_path = self._model_state_path_for_endpoint()
        if state_path is None:
            return (
                not resolve_succeeded,
                resolve_error,
                ("unknown" if resolve_error else None),
                False,
            )

        existing = load_model_state(state_path).state

        # Classify failure from recent container logs when BackendManager is wired.
        failure = None
        mgr = getattr(self.handler, "backend_manager", None)
        if mgr is not None and callable(getattr(mgr, "fetch_recent_logs", None)):
            try:
                logs = mgr.fetch_recent_logs()
            except Exception:
                logs = None
            if logs:
                failure = classify_model_load_failure(logs)

        backend_loaded = ""
        try:
            snap = getattr(self.handler, "backend_manager", None).snapshot()
            if snap.get("phase") == "healthy" and snap.get("backend_health_ok") is True:
                backend_loaded = (
                    snap.get("launch_model_backend_id")
                    or snap.get("launch_model_key")
                    or snap.get("launch_model_path_basename")
                    or ""
                ).strip()
        except Exception:
            pass

        if failure is not None:
            new_state, rollback = mark_load_failure(
                existing,
                error=failure.error,
                error_type=failure.error_type,
                rollback_to_last_good=rollback_on_failure,
            )
            try:
                write_model_state(new_state, state_path)
            except Exception:
                pass
            return True, failure.error, failure.error_type, rollback

        if not resolve_succeeded:
            # Resolution path raised, no log evidence — treat as unknown failure.
            error_text = (resolve_error or "model load failed").strip()[:200]
            new_state, rollback = mark_load_failure(
                existing,
                error=error_text,
                error_type="unknown",
                rollback_to_last_good=rollback_on_failure,
            )
            try:
                write_model_state(new_state, state_path)
            except Exception:
                pass
            return True, error_text, "unknown", rollback

        # Success: clear previous load_error / load_error_type and update
        # last_good_* from the current confirmed selection.
        new_state = mark_load_success(existing, loaded_model=backend_loaded)
        try:
            write_model_state(new_state, state_path)
        except Exception:
            pass
        return False, None, None, False

    def _invoke_selected_model_reload(self, requested: str):
        """Restart the backend with ``-m /models/<basename>`` for requested."""
        mgr = getattr(self.handler, "backend_manager", None)
        if mgr is None:
            raise RuntimeError("backend_manager not initialised")
        return self._direct_mode_reload(requested, mgr)

    def _set_manager_launch_model(self, requested: str, mgr) -> tuple[dict, str]:
        try:
            from .qz_model_catalog import match_model
        except ImportError:
            from qz_model_catalog import match_model

        catalog = self.handler._model_catalog()
        entry = match_model(catalog.entries, requested)
        if entry is None:
            raise RuntimeError(f"model {requested!r} not found in catalog")
        key = entry.get("key") or entry.get("slug") or entry.get("filename") or requested
        backend_id = entry.get("backend_id") or key
        filename = entry.get("filename") or entry.get("backend_target") or backend_id
        if isinstance(filename, str) and filename and not filename.endswith(".gguf"):
            filename = f"{filename}.gguf"
        if not filename:
            raise RuntimeError(f"could not resolve launch filename for {requested!r}")

        mgr.set_launch_model(key=key, backend_id=backend_id, path_basename=filename)
        return entry, "direct backend launch model set"

    def _direct_mode_reload(self, requested: str, mgr) -> tuple[dict, str]:
        """Restart the BackendManager container with the selected model."""
        entry, _ = self._set_manager_launch_model(requested, mgr)
        with self._request_gate("/qz/model/reload", requested, False):
            result = mgr.restart()
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "backend restart rejected")

        # Wait for the backend to settle: healthy = success, failed = error.
        deadline = time.time() + float(getattr(self.handler.__class__, "model_load_timeout", 600.0))
        last_phase = ""
        while time.time() < deadline:
            snap = mgr.snapshot()
            phase = snap.get("phase") or ""
            last_phase = phase
            if phase == "healthy":
                return entry, "direct backend restart"
            if phase == "failed":
                err = snap.get("last_error") or snap.get("launch_model_error") or "backend failed to launch"
                raise RuntimeError(str(err))
            if phase in ("disabled", "stopped"):
                raise RuntimeError(f"backend phase={phase!r} after restart")
            time.sleep(0.5)
        raise RuntimeError(
            f"backend did not become healthy within model_load_timeout (last phase={last_phase!r})"
        )

    def _web_runtime(self, selected_model=None):
        scr = getattr(self.handler, "search_config_result", None)
        search_config_profiles = (
            (scr.config.get("profiles") or {}) if scr is not None else {}
        )
        search_config_default = (
            ((scr.config.get("defaults") or {}).get("profile") or "")
            if scr is not None else ""
        )
        # Read budget config from search.json routing.*
        _routing = (scr.config.get("routing") or {}) if scr is not None else {}
        # Named budget modes table and absolute caps (§64)
        _budget_mode_table = _routing.get("budget_modes") or {}
        _default_budget_mode = str(_routing.get("default_budget_mode") or "").strip()
        _absolute_caps = {
            "results":         _routing.get("absolute_max_results"),
            "searches":        _routing.get("absolute_max_searches_per_turn"),
            "opens":           _routing.get("absolute_max_page_opens_per_turn"),
            "retrievals":      _routing.get("absolute_max_retrievals_per_turn"),
            "retrieved_chars": _routing.get("absolute_max_retrieved_chars"),
        }
        # Flat per-session overrides — #60 compat; used when budget_modes absent.
        search_config_budgets = {
            "max_searches_per_turn": _routing.get("max_searches_per_turn"),
            "max_page_opens_per_turn": _routing.get("max_page_opens_per_turn"),
            "max_results_per_query": _routing.get("max_results") or _routing.get("max_results_per_query"),
            "low_result_fallback_threshold": _routing.get("low_result_fallback_threshold"),
            "max_retrievals_per_turn": _routing.get("max_retrievals_per_turn"),
            "max_retrieved_chars": _routing.get("max_retrieved_chars"),
            "budget_mode_table": _budget_mode_table,
            "absolute_caps": _absolute_caps,
            "default_budget_mode": _default_budget_mode or None,
            "config_source": getattr(scr, "source", "") if scr is not None else "",
            "config_path": str(getattr(scr, "path", "") or "") if scr is not None else "",
            "config_warnings": list(getattr(scr, "warnings", []) or []) if scr is not None else [],
        }
        selection = resolve_search_policy_selection(
            base_policy=self.handler.searxng_policy,
            base_policy_path=getattr(self.handler, "searxng_policy_path", ""),
            selected_model=selected_model,
            root=getattr(self.handler, "root", Path(__file__).resolve().parents[1]),
            search_config_default_profile=search_config_default,
        )
        return WebSearchRuntime(
            base_url=self.handler.searxng_base_url,
            timeout=self.handler.searxng_timeout,
            policy=selection.policy,
            capabilities=self.handler.searxng_capabilities,
            search_cache=self.handler.web_search_cache,
            opened_page_cache=self.handler.opened_page_cache,
            telemetry=self.handler.telemetry,
            policy_path=selection.policy_path,
            default_profile=selection.default_profile,
            policy_selection=selection.metadata(),
            search_config_profiles=search_config_profiles,
            **search_config_budgets,
        )

    def _proxy_tool_registry(self, web_runtime):
        factory = getattr(self.handler, "proxy_tool_registry_factory", None)
        if callable(factory):
            return factory(web_runtime)
        return make_proxy_local_tool_registry(web_runtime)

    def _runtime_metrics(self, selected_model=None):
        try:
            snapshot = self.handler._model_router().status_snapshot()
        except Exception:
            snapshot = {}
        selected = snapshot.get("selected") if isinstance(snapshot, dict) else {}
        backend = snapshot.get("backend") if isinstance(snapshot, dict) else {}
        load = snapshot.get("load") if isinstance(snapshot, dict) else {}
        if not isinstance(selected, dict):
            selected = {}
        if not isinstance(backend, dict):
            backend = {}
        if not isinstance(load, dict):
            load = {}
        return {
            "schema": RUNTIME_METRICS_SCHEMA,
            "ready": bool(snapshot.get("ready")) if isinstance(snapshot, dict) else False,
            "load_state": load.get("state") or "unknown",
            "selected_model": selected_model or selected.get("label") or selected.get("slug") or selected.get("key") or "",
            "selected_key": backend.get("selected_key") or "",
            "selected_backend_id": backend.get("selected_backend_id") or "",
            "selected_context_length": backend.get("selected_context_length"),
            "backend_context_length": backend.get("backend_context_length"),
            "restart_required": bool(backend.get("restart_required")),
            "restart_required_state": backend.get("restart_required_state") or "unknown",
            "selected_context_length_state": backend.get("selected_context_length_state") or "intended",
            "selected_context_length_source": backend.get("selected_context_length_source") or "",
            "backend_context_length_state": backend.get("backend_context_length_state") or "unknown",
            "backend_context_length_source": backend.get("backend_context_length_source") or "",
            "selected_reasoning_level": backend.get("selected_reasoning_level") or "medium",
            "selected_reasoning_policy": backend.get("selected_reasoning_policy") or "prompt",
            "selected_thinking_budget_tokens": backend.get("selected_thinking_budget_tokens"),
            "selected_sampling": backend.get("selected_sampling_params") or {},
            "reasoning_level": backend.get("selected_reasoning_level") or "medium",
            "reasoning_policy": backend.get("selected_reasoning_policy") or "prompt",
            "thinking_budget_tokens": backend.get("selected_thinking_budget_tokens"),
            "sampling": backend.get("selected_sampling_params") or {},
            "runtime_truth_source": "selected_default",
        }

    def _effective_reasoning_stream_format(self, selected_model=None) -> str:
        return profile_reasoning_stream_format(selected_model, self.handler.reasoning_stream_format)

    def _promote_prompt_contract_runtime_truth(self, runtime_metrics: dict, prompt_contract: dict) -> dict:
        if not isinstance(runtime_metrics, dict):
            runtime_metrics = {}
        if not isinstance(prompt_contract, dict):
            prompt_contract = {}

        active_reasoning_level = (
            prompt_contract.get("reasoning_level")
            or runtime_metrics.get("selected_reasoning_level")
            or runtime_metrics.get("reasoning_level")
            or ""
        )
        active_reasoning_policy = (
            prompt_contract.get("reasoning_policy")
            or runtime_metrics.get("selected_reasoning_policy")
            or runtime_metrics.get("reasoning_policy")
            or ""
        )
        active_sampling = prompt_contract.get("sampling") if isinstance(prompt_contract.get("sampling"), dict) else {}
        if not active_sampling:
            active_sampling = (
                runtime_metrics.get("selected_sampling")
                if isinstance(runtime_metrics.get("selected_sampling"), dict)
                else {}
            )

        runtime_metrics["active_reasoning_level"] = active_reasoning_level
        runtime_metrics["active_reasoning_policy"] = active_reasoning_policy
        runtime_metrics["active_sampling"] = dict(active_sampling)
        runtime_metrics["active_thinking_budget_tokens"] = prompt_contract.get("thinking_budget_tokens")
        runtime_metrics["reasoning_level"] = active_reasoning_level
        runtime_metrics["reasoning_policy"] = active_reasoning_policy
        runtime_metrics["sampling"] = dict(active_sampling)
        runtime_metrics["thinking_budget_tokens"] = prompt_contract.get("thinking_budget_tokens")
        runtime_metrics["runtime_truth_source"] = "prompt_contract"
        if prompt_contract.get("reasoning_stream_format"):
            runtime_metrics["reasoning_stream_format"] = prompt_contract.get("reasoning_stream_format")
        return runtime_metrics

    def _prompt_contract(
        self,
        body: dict,
        selected_model: dict,
        client_model: str,
        backend_model: str,
        memory_domain: str | None = None,
        memory_domain_warning: str | None = None,
    ) -> dict:
        if not isinstance(body, dict):
            body = {}
        if not isinstance(selected_model, dict):
            selected_model = {}
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        prompt_policy = (
            metadata.get("qz_prompt_policy")
            if isinstance(metadata.get("qz_prompt_policy"), dict)
            else {}
        )
        turn_harness = (
            metadata.get("qz_turn_harness") if isinstance(metadata.get("qz_turn_harness"), dict) else {}
        )
        qz_runtime = (
            metadata.get("qz_runtime") if isinstance(metadata.get("qz_runtime"), dict) else {}
        )
        qz_reasoning = (
            metadata.get("qz_reasoning") if isinstance(metadata.get("qz_reasoning"), dict) else {}
        )

        def _display_path(value: str) -> str:
            text = str(value or "")
            if not text:
                return ""
            try:
                root = str(
                    Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1])).resolve()
                )
                if text.startswith(root + "/"):
                    return text[len(root) + 1 :]
            except Exception:
                pass
            return text

        def _strings(value):
            if isinstance(value, str) and value:
                return [_display_path(value)]
            if isinstance(value, list):
                return [_display_path(item) for item in value if isinstance(item, str) and item]
            return []

        prompt_files = []
        for key in ("replacement_files_loaded", "prompt_files_loaded"):
            for item in _strings(prompt_policy.get(key)):
                if item not in prompt_files:
                    prompt_files.append(item)

        sampling = {}
        raw_sampling = qz_reasoning.get("sampling")
        if isinstance(raw_sampling, dict):
            for key in (
                "temperature",
                "top_p",
                "top_k",
                "min_p",
                "presence_penalty",
                "repeat_penalty",
                "repeat_last_n",
                "dry_multiplier",
                "dry_base",
                "dry_allowed_length",
                "dry_penalty_last_n",
            ):
                if raw_sampling.get(key) is not None:
                    sampling[key] = raw_sampling.get(key)

        selected_key = (
            selected_model.get("key") or selected_model.get("slug") or client_model or ""
        )
        profile = (
            selected_model.get("label") or selected_model.get("name") or selected_key or client_model
        )
        return {
            "schema": PROMPT_CONTRACT_SCHEMA,
            "request_id": metadata.get("qz_request_id") or "",
            "profile": profile,
            "memory_domain": memory_domain or "isolated",
            "memory_domain_warning": memory_domain_warning,
            "requested_model": client_model or "",
            "selected_key": selected_key,
            "selected_label": profile,
            "selected_backend_id": backend_model or selected_model.get("backend_id") or selected_key,
            "profile_symlink": bool(selected_model.get("profile_symlink")),
            "prompt_policy": {
                "mode": prompt_policy.get("mode") or "",
                "disable_system_prompt": bool(prompt_policy.get("disable_system_prompt")),
                "replaced_client": bool(prompt_policy.get("replaced_client")),
                "synthesized_missing_client": bool(prompt_policy.get("synthesized_missing_client")),
                "reused_existing_replacement": bool(prompt_policy.get("reused_existing_replacement")),
                "ignored_replace": bool(prompt_policy.get("ignored_replace")),
                "client_blocks": prompt_policy.get("client_blocks") or 0,
                "existing_blocks": prompt_policy.get("existing_blocks") or 0,
                "prompt_files_loaded": _strings(prompt_policy.get("prompt_files_loaded")),
                "replacement_files_loaded": _strings(prompt_policy.get("replacement_files_loaded")),
                "prompt_files_missing": _strings(prompt_policy.get("prompt_files_missing")),
                "replacement_files_missing": _strings(prompt_policy.get("replacement_files_missing")),
                "prompt_files_failed": _strings(prompt_policy.get("prompt_files_failed")),
                "replacement_files_failed": _strings(prompt_policy.get("replacement_files_failed")),
            },
            "turn_harness": {
                "available": bool(turn_harness.get("available")),
                "applied": bool(turn_harness.get("applied")),
                "skipped_reason": turn_harness.get("skipped_reason") or "",
                "active": _strings(turn_harness.get("active")),
                "unknown": _strings(turn_harness.get("unknown")),
            },
            "prompt_files": prompt_files,
            "reasoning_level": qz_reasoning.get("level")
            or qz_runtime.get("reasoning_level")
            or "",
            "reasoning_policy": qz_reasoning.get("policy")
            or qz_runtime.get("reasoning_policy")
            or "",
            "reasoning_stream_format": metadata.get("qz_reasoning_stream_format") or "",
            "thinking_budget_tokens": qz_reasoning.get("thinking_budget_tokens"),
            "sampling": sampling,
            "context_length": qz_runtime.get("context_length"),
            "backend_context_length": qz_runtime.get("backend_context_length"),
        }

    def _emit_prompt_contract(self, contract: dict):
        if not contract:
            return
        try:
            self.handler.telemetry.emit("prompt_contract", contract)
        except Exception:
            pass

    def _emit_request_telemetry(self, event_type: str, started_at: float, upstream_path: str, client_model: str, backend_model: str = "", **extra):
        payload = {
            "method": "POST",
            "path": self.handler.path,
            "upstream_path": upstream_path,
            "stream": bool(extra.pop("stream", False)),
            "model": client_model,
            "backend_model": backend_model,
            "elapsed_ms": round(max(0.0, time.time() - started_at) * 1000.0, 2),
        }
        if extra:
            payload.update(extra)
        try:
            self.handler.telemetry.emit(event_type, payload)
        except Exception:
            pass
        if event_type == "request_completed":
            self._emit_throughput_sample(payload)

    def _request_id(self, started_at: float) -> str:
        return f"qz_req_{int(started_at * 1000)}_{id(self.handler) & 0xffff:x}"

    def _capture_contract(
        self,
        request_id: str,
        prompt_contract: dict,
        runtime_metrics: dict,
        memory_domain: str | None = None,
        memory_domain_warning: str | None = None,
    ) -> dict:
        prompt_contract = prompt_contract if isinstance(prompt_contract, dict) else {}
        runtime_metrics = runtime_metrics if isinstance(runtime_metrics, dict) else {}
        return {
            "schema": CAPTURE_CONTRACT_SCHEMA,
            "request_id": request_id or "",
            "status_schema": "qz.status.snapshot.v1",
            "runtime_metrics_schema": runtime_metrics.get("schema") or RUNTIME_METRICS_SCHEMA,
            "prompt_contract_schema": prompt_contract.get("schema") or PROMPT_CONTRACT_SCHEMA,
            "requested_model": prompt_contract.get("requested_model")
            or runtime_metrics.get("selected_model")
            or "",
            "selected_key": prompt_contract.get("selected_key")
            or runtime_metrics.get("selected_key")
            or "",
            "selected_backend_id": prompt_contract.get("selected_backend_id")
            or runtime_metrics.get("selected_backend_id")
            or "",
            "memory_domain": memory_domain or prompt_contract.get("memory_domain") or "isolated",
            "memory_domain_warning": memory_domain_warning
            or prompt_contract.get("memory_domain_warning"),
            "prompt_policy": prompt_contract.get("prompt_policy") or {},
            "turn_harness": prompt_contract.get("turn_harness") or {},
            "runtime_metrics": runtime_metrics,
        }

    def _emit_throughput_sample(self, payload: dict):
        if not isinstance(payload, dict):
            return

        usage = _normalize_response_usage(payload.get("usage") if isinstance(payload.get("usage"), dict) else {})
        try:
            prompt_tokens = int(usage.get("input_tokens") or 0)
        except Exception:
            prompt_tokens = 0
        try:
            gen_tokens = int(usage.get("output_tokens") or 0)
        except Exception:
            gen_tokens = 0
        try:
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + gen_tokens) or gen_tokens or 0)
        except Exception:
            total_tokens = 0

        prompt_ms = payload.get("prompt_ms")
        gen_ms = payload.get("gen_ms")
        elapsed_ms = payload.get("elapsed_ms")

        def _rate(tokens: int, ms):
            try:
                tokens = int(tokens)
                ms = float(ms)
            except Exception:
                return 0.0
            if tokens <= 0 or ms <= 0:
                return 0.0
            return tokens * 1000.0 / ms

        prompt_rate = _rate(prompt_tokens, prompt_ms)
        gen_rate = _rate(gen_tokens, gen_ms)
        total_rate = _rate(total_tokens, elapsed_ms)

        if not any(rate > 0 for rate in (prompt_rate, gen_rate, total_rate)):
            return

        sample = {
            "path": payload.get("path", ""),
            "upstream_path": payload.get("upstream_path", ""),
            "model": payload.get("model", ""),
            "backend_model": payload.get("backend_model", ""),
            "stream": bool(payload.get("stream")),
            "status": payload.get("status"),
            "prompt_tokens": prompt_tokens,
            "gen_tokens": gen_tokens,
            "total_tokens": total_tokens,
            "prompt_ms": prompt_ms,
            "gen_ms": gen_ms,
            "elapsed_ms": elapsed_ms,
            "prompt_rate": round(prompt_rate, 2) if prompt_rate > 0 else 0.0,
            "gen_rate": round(gen_rate, 2) if gen_rate > 0 else 0.0,
            "total_rate": round(total_rate, 2) if total_rate > 0 else 0.0,
        }
        runtime = payload.get("runtime_metrics") if isinstance(payload.get("runtime_metrics"), dict) else {}
        if runtime:
            sample["runtime_metrics"] = runtime
            sample["selected_context_length"] = runtime.get("selected_context_length")
            sample["backend_context_length"] = runtime.get("backend_context_length")
            sample["reasoning_level"] = runtime.get("reasoning_level")
            sample["reasoning_policy"] = runtime.get("reasoning_policy")
            sample["thinking_budget_tokens"] = runtime.get("thinking_budget_tokens")
            sample["selected_reasoning_level"] = runtime.get("selected_reasoning_level")
            sample["selected_reasoning_policy"] = runtime.get("selected_reasoning_policy")
            sample["active_reasoning_level"] = runtime.get("active_reasoning_level")
            sample["active_reasoning_policy"] = runtime.get("active_reasoning_policy")
            sample["restart_required"] = runtime.get("restart_required")
        try:
            self.handler.telemetry.emit("throughput_sample", sample)
        except Exception:
            pass

    def _annotate_output_with_url_citations(self, out: dict, sources):
        unique_sources = _unique_sources(sources)[:4]
        if not unique_sources:
            return out

        output_items = out.get("output") or []
        for item in reversed(output_items):
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content") or []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    continue
                text = part.get("text") or ""
                annotations = list(part.get("annotations") or [])
                for idx, source in enumerate(unique_sources, start=1):
                    marker = f" [{idx}]"
                    start_index = len(text)
                    text += marker
                    end_index = len(text) - 1
                    annotations.append({
                        "type": "url_citation",
                        "start_index": start_index,
                        "end_index": end_index,
                        "title": source.get("title") or source.get("url"),
                        "url": source.get("url"),
                    })
                part["text"] = text
                part["annotations"] = annotations
                return out
        return out

    def _call_upstream_json(self, url: str, body: dict):
        resp = self.handler._backend().post_json(url, body, timeout=900)
        return resp.status, resp.content_type, resp.data

    def _write_sse_chunk(self, chunk: bytes, raw_log=None, request_id: str = ""):
        if raw_log is not None:
            raw_log.write(chunk)
            raw_log.flush()
        if request_id and capture_enabled():
            try:
                append_request_capture(request_id, "forwarded-sse.raw", chunk)
            except Exception:
                pass
        self.handler._emit_sse_telemetry(chunk, request_id=request_id)
        self.handler.wfile.write(chunk)
        self.handler.wfile.flush()

    def _run_responses_streaming_locally(
        self,
        body: dict,
        requested_model: str,
        request_id: str = "",
        selected_model=None,
        reasoning_stream_format: str | None = None,
    ):
        web_runtime = self._web_runtime(selected_model)
        runtime = ResponsesStreamRuntime(
            upstream=self.handler.upstream,
            authorization=self.handler.headers.get("Authorization", "Bearer local"),
            reasoning_stream_format=normalize_reasoning_stream_format(
                reasoning_stream_format,
                self.handler.reasoning_stream_format,
            ),
            web_runtime=web_runtime,
            chunk_writer=lambda chunk: self._write_sse_chunk(chunk, request_id=request_id),
            telemetry=self.handler.telemetry,
            request_id=request_id,
            proxy_tool_registry=self._proxy_tool_registry(web_runtime),
            selected_model=selected_model,
            reasoning_carry_forward=_env_bool("QZ_REASONING_CARRY_FORWARD", False),
            hop_budget_signal_threshold=int(os.environ.get("QZ_HOP_BUDGET_SIGNAL_THRESHOLD", "3")),
            context_pressure_signal_threshold=float(os.environ.get("QZ_CONTEXT_PRESSURE_SIGNAL_THRESHOLD", "0.8")),
        )
        return runtime.run(body, requested_model)

    def _run_responses_locally(
        self,
        body: dict,
        requested_model: str,
        selected_model=None,
        request_id: str = "",
    ):
        url = self.handler.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0, "retrieve": 0}
        seen_signatures = set()
        web_runtime = self._web_runtime(selected_model)
        proxy_tool_registry = self._proxy_tool_registry(web_runtime)
        repeated_read_state = seed_repeated_read_state(working_body.get("input") or [])
        native_advisory_state = seed_native_advisory_state(working_body.get("input") or [])

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            hop_body = json.loads(json.dumps(working_body))
            hop_body["stream"] = False
            hop_body = normalize_responses_input_for_qwen(hop_body, selected_model=selected_model)
            hop_body = normalize_tools_for_llamacpp(hop_body)
            status, content_type, resp_data = self._call_upstream_json(url, hop_body)
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model

            output_items = out.get("output") or []
            web_calls = [
                item for item in output_items
                if proxy_tool_registry.is_proxy_local_call(item)
            ]

            if not web_calls:
                final_out = dict(out)
                final_out["output"] = public_trace + normalize_tool_output_for_codex(
                    output_items,
                )
                final_out["usage"] = _normalize_response_usage(final_out.get("usage"))
                self._annotate_output_with_url_citations(final_out, gathered_sources)
                runtime_log("latest-web-runtime-final.json", final_out)
                return status, content_type, final_out

            dropped_tool_names = frozenset(
                (hop_body.get("metadata") or {}).get("qz_dropped_tool_names") or []
            )
            next_input = list(hop_body.get("input") or [])
            for item in output_items:
                if not proxy_tool_registry.is_proxy_local_call(item):
                    # Check for repeated-read signal on Codex-native items
                    # before passing them through.
                    rr_decision = proxy_tool_registry.completed_call_decision(
                        item,
                        dropped_tool_names=dropped_tool_names,
                        repeated_read_state=repeated_read_state,
                        native_advisory_state=native_advisory_state,
                    )
                    if rr_decision.kind == "signal":
                        next_input.append(rr_decision.signal_result)
                        try:
                            # Determine telemetry event type based on metadata
                            signal_payload = rr_decision.signal_metadata or {}
                            event_type = "repeated_read_signal"
                            if "advisory_reason" in signal_payload:
                                event_type = "native_tool_advisory"
                            
                            self.handler.telemetry.emit(
                                event_type,
                                {**signal_payload, "request_id": request_id},
                            )
                        except Exception:
                            pass
                    elif rr_decision.kind == "error":
                        # Dropped/unknown non-proxy-local tool: inject error result so
                        # the model sees feedback instead of the original call.
                        next_input.append(rr_decision.error_result)
                        try:
                            self.handler.telemetry.emit("tool_call_error", {
                                "tool": item.get("name") or "",
                                "call_id": item.get("call_id") or item.get("id") or "",
                                "error": (rr_decision.error_result or {}).get("output", ""),
                                "source": "tool_decision",
                                "request_id": request_id,
                            })
                        except Exception:
                            pass
                    else:
                        record_tool_call(item, repeated_read_state)
                        record_native_tool_call(item, native_advisory_state)
                        next_input.append(item)
                    continue

                decision = proxy_tool_registry.completed_call_decision(
                    item,
                    dropped_tool_names=dropped_tool_names,
                    repeated_read_state=repeated_read_state,
                )
                if decision.coercion_applied:
                    _coercion_event = "coercion_failed" if decision.coercion_error else "coercion_succeeded"
                    try:
                        _tool_name = item.get("name") or ""
                        self.handler.telemetry.emit(_coercion_event, {
                            "tool": _tool_name,
                            "upstream_name": _tool_name,
                            "call_id": item.get("call_id") or item.get("id") or "",
                            "correction_applied": not bool(decision.coercion_error),
                            "error_summary": decision.coercion_error[:200] if decision.coercion_error else "",
                            "request_id": request_id,
                            "source": "tool_adapter",
                        })
                    except Exception:
                        pass
                if decision.kind == "error":
                    next_input.append(decision.error_result)
                    continue
                result = proxy_tool_registry.execute(
                    decision.call,
                    ProxyToolExecutionContext(
                        request_id=request_id,
                        counters=counters,
                        seen_signatures=seen_signatures,
                    ),
                )
                public_trace.append(result.public_item)
                gathered_sources.extend(result.sources)
                next_input.extend(result.upstream_items)

            working_body["input"] = next_input

        fallback_out = {
            "id": f"resp_local_{_now_ts()}",
            "object": "response",
            "created_at": _now_ts(),
            "model": requested_model,
            "output": public_trace + [{
                "id": f"msg_local_{_now_ts()}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text",
                    "text": proxy_tool_registry.continuation_limit_message(),
                    "annotations": [],
                }],
            }],
            "usage": _normalize_response_usage({}),
        }
        self._annotate_output_with_url_citations(fallback_out, gathered_sources)
        runtime_log("latest-web-runtime-final.json", fallback_out)
        return 200, "application/json", fallback_out

    def proxy_json_api(self, upstream_path):
        started_at = time.time()
        request_id = self._request_id(started_at)
        try:
            append_capture("latest-json-api.log", f"{time.time():.3f} ENTER path={self.handler.path} upstream_path={upstream_path} accept={self.handler.headers.get('Accept','')}\n")
        except Exception:
            pass

        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._emit_request_telemetry("request_failed", started_at, upstream_path, "", error=f"invalid JSON: {e}", phase="parse", request_id=request_id)
            self.handler._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        try:
            write_dual_capture("latest-request.json", request_id, "incoming-request.json", body)
            write_capture("latest-request-id.txt", request_id)
        except Exception:
            pass

        try:
            headers_raw = incoming_headers_payload(self.handler)
            headers_capture = {
                "schema": "qz.incoming.request.capture.v2",
                "request_id": request_id,
                "method": "POST",
                "path": self.handler.path,
                "headers_raw": headers_raw,
            }
            write_dual_capture("latest-request-headers.json", request_id, "incoming-request-headers.json", headers_capture)
        except Exception:
            headers_raw = {}

        if not self._proxy_startup_ready():
            initialization = self.handler._initialization_payload()
            _cp_a = self._minimal_cp_for_service_status(initialization)
            _ss_a = build_service_status(_cp_a)
            payload = build_responses_error_payload(
                error="proxy not ready",
                reason=initialization.get("error") or "model catalog and startup policy are still loading",
                requested_model=body.get("model") or "",
                proxy_initialization=initialization,
                readiness={
                    "proxy_ready": bool(initialization.get("ready")),
                    "catalog_ready": bool(initialization.get("catalog_ready")),
                    "model_visible": False,
                    "backend_ready": False,
                },
                operator_hint=(
                    "The QuantZhai proxy is initializing. "
                    "Check /qz/status or /health for readiness details. "
                    "qz-codex does not require local Docker or llama.cpp access."
                ),
                status_code=503,
                service_status=_ss_a,
            )
            self._emit_request_telemetry(
                "request_failed",
                started_at,
                upstream_path,
                body.get("model") or "",
                error=payload.get("reason"),
                phase="proxy_initialization",
                request_id=request_id,
            )
            try:
                self.handler.telemetry.emit("responses_rejected_proxy_not_ready", {
                    "request_id": request_id,
                    "model": body.get("model") or "",
                    "path": upstream_path,
                    "stream": body.get("stream", False),
                    "proxy_ready": bool(initialization.get("ready")),
                    "catalog_ready": bool(initialization.get("catalog_ready")),
                })
            except Exception:
                pass
            self.handler._send_json(503, payload)
            return

        try:
            status_summary = self.handler._model_router().status_summary(self.handler.path)
            self.handler.telemetry.emit("status_snapshot", status_summary)
            self.handler.telemetry.emit("runtime_snapshot", {
                "path": self.handler.path,
                "telemetry": self.handler.telemetry.state(),
                "runtime": status_summary,
            })
        except Exception:
            pass

        client_wants_stream = (
            body.get("stream") is True
            or "text/event-stream" in self.handler.headers.get("Accept", "")
        )

        client_model = body.get("model") or ""
        with self._request_gate(upstream_path, client_model, client_wants_stream):
            catalog = self.handler._model_catalog()
            if client_model:
                selected_model, selection_reason = catalog.resolve(query=client_model)
            else:
                selected_model = catalog.selected or (catalog.entries[0] if catalog.entries else None)
                selection_reason = "default"
            if selected_model is not None and selected_model.get("profile_valid", True) is False:
                try:
                    from .qz_model_router import profile_backend_error_payload
                except ImportError:
                    from qz_model_router import profile_backend_error_payload
                selection_reason = profile_backend_error_payload(selected_model, client_model or "")
                selected_model = None
            if selected_model is None:
                error_text = selection_reason.get("reason") if isinstance(selection_reason, dict) else selection_reason
                self._emit_request_telemetry("request_failed", started_at, upstream_path, client_model, error=error_text or "no model available", phase="select_model", request_id=request_id)

                # Gather available model IDs for a more actionable error.
                available_ids: list[str] = []
                try:
                    catalog = self.handler._model_catalog()
                    available_ids = [
                        e.get("key") or e.get("stem") or ""
                        for e in catalog.entries
                        if isinstance(e, dict) and e.get("profile_valid", True) is not False
                    ]
                    available_ids = [m for m in available_ids if m]
                except Exception:
                    pass

                initialization = self.handler._initialization_payload()
                # Distinguish profile-backend-missing (broken symlink) from generic
                # model-not-found so clients get the right error_code and action.
                _is_profile_missing = (
                    isinstance(selection_reason, dict)
                    and selection_reason.get("error") == "profile backend missing"
                )
                _error_label = "profile backend missing" if _is_profile_missing else "model not found"
                _op_action_b = "inspect_logs" if _is_profile_missing else "select_model"
                _cp_b = self._minimal_cp_for_service_status(initialization)
                _ss_b = build_service_status(_cp_b)
                payload = build_responses_error_payload(
                    error=_error_label,
                    reason=error_text or f"no model matching '{client_model}'",
                    requested_model=client_model,
                    available_models=available_ids,
                    proxy_initialization=initialization,
                    readiness={
                        "proxy_ready": bool(initialization.get("ready")),
                        "catalog_ready": bool(initialization.get("catalog_ready")),
                        "model_visible": False,
                        "backend_ready": False,
                    },
                    operator_hint=(
                        "Use a real model ID from /v1/models or POST /qz/models/refresh. "
                        "Old aliases (low/medium/high/max/caveman) are deprecated."
                    ),
                    status_code=503,
                    service_status=_ss_b,
                    operator_action=_op_action_b,
                )
                # Carry forward profile-backend-missing fix hint if present.
                if isinstance(selection_reason, dict) and selection_reason.get("fix"):
                    payload["fix"] = selection_reason["fix"]
                try:
                    self.handler.telemetry.emit("responses_rejected_model_missing", {
                        "request_id": request_id,
                        "model": client_model,
                        "path": upstream_path,
                        "stream": bool(client_wants_stream),
                        "available_count": len(available_ids),
                        "deprecated_alias": is_deprecated_alias(client_model),
                    })
                except Exception:
                    pass
                self.handler._send_json(503, payload)
                return

            if upstream_path == "/v1/responses":
                try:
                    from .qz_model_status import build_model_status, identity_matches_loaded
                except ImportError:
                    from qz_model_status import build_model_status, identity_matches_loaded
                model_status = build_model_status(self.handler)
                active_ready = bool(model_status.get("selected_model_ready"))
                active_loaded = str(model_status.get("backend_loaded_model") or "")
                requested_key = str(selected_model.get("key") or selected_model.get("slug") or "")
                requested_backend = str(selected_model.get("backend_id") or requested_key)
                request_matches_active = identity_matches_loaded(
                    requested_key,
                    requested_backend,
                    active_loaded,
                )
                if not active_ready or not request_matches_active:
                    initialization = self.handler._initialization_payload()
                    cp = build_control_plane_status(self.handler)
                    ss = cp.get("service_status") or build_service_status(cp)
                    reason = (
                        "selected model is not ready for direct backend launch"
                        if not active_ready
                        else "requested model is not the active direct-launched model"
                    )
                    payload = build_responses_error_payload(
                        error="model not ready",
                        reason=reason,
                        requested_model=client_model or requested_backend,
                        proxy_initialization=initialization,
                        readiness={
                            "proxy_ready": bool(initialization.get("ready")),
                            "catalog_ready": bool(initialization.get("catalog_ready")),
                            "model_visible": True,
                            "backend_ready": active_ready,
                            "selected_model_ready": active_ready,
                            "request_admission_state": model_status.get("request_admission_state"),
                        },
                        operator_hint=(
                            "Use POST /qz/model/select-and-restart to select and "
                            "restart the backend with the requested model."
                        ),
                        status_code=503,
                        service_status=ss,
                    )
                    payload["model_status"] = model_status
                    self._emit_request_telemetry(
                        "request_failed",
                        started_at,
                        upstream_path,
                        client_model,
                        error=reason,
                        phase="model_readiness",
                        request_id=request_id,
                    )
                    try:
                        self.handler.telemetry.emit("responses_rejected_model_not_ready", {
                            "request_id": request_id,
                            "model": client_model,
                            "path": upstream_path,
                            "stream": bool(client_wants_stream),
                            "selected_model_ready": active_ready,
                            "request_admission_state": model_status.get("request_admission_state"),
                        })
                    except Exception:
                        pass
                    self.handler._send_json(503, payload)
                    return

            selected_identity = (
                selected_model.get("slug")
                or selected_model.get("key")
                or selected_model.get("backend_id")
                or ""
            )

            backend_model = selected_model.get("backend_id") or selected_identity or client_model
            runtime_metrics = self._runtime_metrics(client_model)
            runtime_metrics["request_id"] = request_id

            explicit_domain = selected_model.get("memory_domain") if isinstance(selected_model, dict) else None
            ctx = extract_codex_request_context(
                headers_raw, body, explicit_memory_domain=explicit_domain
            )

            upstream_instructions = body.get("instructions")
            upstream_instructions_present = isinstance(upstream_instructions, str) and bool(
                upstream_instructions.strip()
            )
            metadata = body.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["qz_upstream_instructions_present"] = upstream_instructions_present
            metadata["qz_request_id"] = request_id
            body["metadata"] = metadata
            body["model"] = backend_model
            body = self.handler._model_router().apply_reasoning_policy(body, selected_model)
            effective_reasoning_stream_format = self._effective_reasoning_stream_format(
                selected_model
            )
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            metadata["qz_reasoning_stream_format"] = effective_reasoning_stream_format
            body["metadata"] = metadata

            if upstream_path == "/v1/responses":
                # Observe raw incoming native tool output BEFORE any normalization.
                # Normalization (microcompact, normalize_responses_input_for_qwen) may
                # rewrite or replace function_call_output items, losing the original
                # output text that classifiers need. Read-only: no mutation.
                _raw_input = body.get("input")
                if isinstance(_raw_input, list):
                    signals = classify_native_tool_output_signals(_raw_input)
                    self._emit_signal_decisions(signals, request_id=request_id)
                    # Inject model-visible advisories for high-confidence sandbox denials.
                    # Advisory items are appended to body["input"] before any normalization
                    # so the model receives corrective feedback alongside the original outputs.
                    _advisory_items = self._model_visible_native_advisories(signals)
                    if _advisory_items:
                        body["input"] = list(_raw_input) + _advisory_items
                        for _adv in _advisory_items:
                            try:
                                _adv_call_id = _adv.get("call_id") or ""
                                _adv_tel: dict = {
                                    "call_id": _adv_call_id,
                                    "request_id": request_id,
                                }
                                for _sig in signals:
                                    if (
                                        _sig.event_type == "tool_sandbox_denied"
                                        and isinstance(_sig.payload, dict)
                                        and _sig.payload.get("call_id") == _adv_call_id
                                    ):
                                        _adv_tel["tool"] = _sig.payload.get("tool") or ""
                                        _adv_tel["classifier"] = _sig.payload.get("classifier") or ""
                                        break
                                self.handler.telemetry.emit("tool_sandbox_advisory_injected", _adv_tel)
                            except Exception:
                                pass

                body = self.handler._model_router().inject_runtime_state(body, client_model)
                ensure_apply_patch_tool_policy(body, overwrite=True)
                input_items = body.get("input")
                if isinstance(input_items, list):
                    body["input"] = _microcompact_old_tool_results(
                        _expand_local_compaction_items(input_items)
                    )

                # Support context_management.compact_threshold
                context_mgmt = body.get("context_management")
                if isinstance(context_mgmt, dict):
                    threshold = context_mgmt.get("compact_threshold")
                    if isinstance(threshold, int) and threshold > 0:
                        current_tokens = _estimate_items_tokens(body.get("input", []))
                        if current_tokens > threshold:
                            out = _build_local_compaction_response(body)
                            self.handler._send_json(200, out)
                            self._emit_request_telemetry(
                                "request_completed",
                                started_at,
                                upstream_path,
                                client_model,
                                status=200,
                                suppressed="auto_compaction_triggered",
                            )
                            return
                body = normalize_responses_input_for_qwen(body, selected_model=selected_model)
                _tool_norm_report = _normalize_tool_request(body, write_captures=True)
                self._emit_schema_normalization_telemetry(_tool_norm_report, request_id)

                prompt_contract = self._prompt_contract(
                    body,
                    selected_model,
                    client_model,
                    backend_model,
                    memory_domain=ctx.memory_domain,
                    memory_domain_warning=ctx.memory_domain_warning,
                )
                runtime_metrics = self._promote_prompt_contract_runtime_truth(
                    runtime_metrics, prompt_contract
                )
                runtime_metrics["prompt_contract"] = prompt_contract
                self._emit_prompt_contract(prompt_contract)
                try:
                    capture_contract = self._capture_contract(
                        request_id,
                        prompt_contract,
                        runtime_metrics,
                        memory_domain=ctx.memory_domain,
                        memory_domain_warning=ctx.memory_domain_warning,
                    )
                    write_dual_capture(
                        "latest-normalized-request.json", request_id, "forwarded-request.json", body
                    )
                    write_dual_capture(
                        "latest-request-contract.json",
                        request_id,
                        "request-contract.json",
                        capture_contract,
                    )
                except Exception:
                    pass

                _ar = getattr(self.handler, "active_requests", ACTIVE_REQUESTS)
                _ar.begin(request_id, route=upstream_path, model=client_model)
                if client_wants_stream:
                    self.handler.send_response(200)
                    self.handler.send_header("Content-Type", "text/event-stream")
                    self.handler.send_header("Cache-Control", "no-cache")
                    self.handler.send_header("Connection", "close")
                    self.handler._send_codex_rate_limit_headers()
                    self.handler.end_headers()
                    self.handler._write_codex_rate_limits_event()
                    stream_result = None
                    try:
                        write_request_capture(request_id, "forwarded-sse.raw", b"", mode="bytes")
                        stream_result = self._run_responses_streaming_locally(
                            body,
                            client_model,
                            request_id=request_id,
                            selected_model=selected_model,
                            reasoning_stream_format=effective_reasoning_stream_format,
                        )
                        self._emit_request_telemetry(
                            "request_completed",
                            started_at,
                            upstream_path,
                            client_model,
                            backend_model=backend_model,
                            stream=True,
                            status=200,
                            usage=stream_result.get("usage") if isinstance(stream_result, dict) else None,
                            prompt_ms=stream_result.get("prompt_ms") if isinstance(stream_result, dict) else None,
                            gen_ms=stream_result.get("gen_ms") if isinstance(stream_result, dict) else None,
                            output_items=stream_result.get("output_items") if isinstance(stream_result, dict) else None,
                            runtime_metrics=runtime_metrics,
                            request_id=request_id,
                        )
                    except (BrokenPipeError, ConnectionResetError):
                        self._emit_request_telemetry(
                            "request_failed",
                            started_at,
                            upstream_path,
                            client_model,
                            backend_model=backend_model,
                            stream=True,
                            error="client disconnected",
                            phase="stream",
                            runtime_metrics=runtime_metrics,
                            request_id=request_id,
                        )
                        pass
                    except Exception as e:
                        try:
                            import traceback
                            runtime_log("latest-stream-runtime-error.txt", traceback.format_exc())
                        except Exception:
                            pass
                        self._emit_request_telemetry(
                            "request_failed",
                            started_at,
                            upstream_path,
                            client_model,
                            backend_model=backend_model,
                            stream=True,
                            error=str(e),
                            phase="stream",
                            runtime_metrics=runtime_metrics,
                            request_id=request_id,
                        )
                        error_payload = {
                            "type": "response.failed",
                            "response": {
                                "id": f"resp_failed_{request_id[:8]}_{_now_ts()}",
                                "object": "response",
                                "created_at": _now_ts(),
                                "status": "failed",
                                "model": client_model,
                                "error": {"message": f"local streaming runtime error: {e}"},
                                "output": [],
                                "usage": _normalize_response_usage({}),
                            },
                        }
                        self._write_sse_chunk(make_sse_block("response.failed", error_payload))
                        self._write_sse_chunk(b"data: [DONE]\n\n")
                    self.handler.close_connection = True
                    _ar.finish(request_id)
                    return

                try:
                    status, content_type, out = self._run_responses_locally(
                        body,
                        client_model,
                        selected_model=selected_model,
                        request_id=request_id,
                    )
                    if status >= 400:
                        try:
                            self.handler._send_json(status, out)
                        except Exception:
                            self.handler.send_response(status)
                            self.handler.send_header("Content-Type", "text/plain")
                            self.handler._send_codex_rate_limit_headers()
                            self.handler.end_headers()
                            self.handler.wfile.write(json.dumps(out).encode("utf-8"))
                        self._emit_request_telemetry(
                            "request_completed",
                            started_at,
                            upstream_path,
                            client_model,
                            backend_model=backend_model,
                            stream=False,
                            status=status,
                            content_type=content_type,
                            usage=out.get("usage"),
                            runtime_metrics=runtime_metrics,
                            request_id=request_id,
                        )
                        _ar.finish(request_id)
                        return

                    self.handler._send_json(status, out)
                    self._emit_request_telemetry(
                        "request_completed",
                        started_at,
                        upstream_path,
                        client_model,
                        backend_model=backend_model,
                        stream=False,
                        status=status,
                        content_type=content_type,
                        usage=out.get("usage"),
                        runtime_metrics=runtime_metrics,
                        request_id=request_id,
                    )
                    _ar.finish(request_id)
                    return
                except Exception as e:
                    try:
                        import traceback
                        runtime_log("latest-web-runtime-error.txt", traceback.format_exc())
                    except Exception:
                        pass
                    self._emit_request_telemetry(
                        "request_failed",
                        started_at,
                        upstream_path,
                        client_model,
                        backend_model=backend_model,
                        stream=False,
                        error=str(e),
                        phase="local_web_runtime",
                        runtime_metrics=runtime_metrics,
                        request_id=request_id,
                    )
                    self.handler._send_json(502, {"error": f"local web runtime error: {e}"})
                    _ar.finish(request_id)
                    return

        try:
            append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM url={self.handler.upstream + upstream_path} bytes={len(json.dumps(body).encode('utf-8'))} stream={body.get('stream')}\n")
        except Exception:
            pass
        try:
            self.handler.telemetry.emit("upstream_request", {
                "path": upstream_path,
                "model": client_model,
                "backend_model": backend_model,
                "stream": bool(body.get("stream")),
                "request_id": request_id,
            })
        except Exception:
            pass
        try:
            resp = self.handler._backend().request(
                upstream_path,
                method="POST",
                body=json.dumps(body).encode("utf-8"),
                headers={"Accept": self.handler.headers.get("Accept", "application/json")},
                timeout=900,
            )
        except Exception as e:
            try:
                import traceback
                append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM_EXCEPTION {type(e).__name__}: {e}\n")
                append_capture("latest-json-api.log", traceback.format_exc() + "\n")
            except Exception:
                pass
            if upstream_path == "/v1/responses":
                try:
                    mgr = getattr(self.handler, "backend_manager", None)
                    if mgr is not None and callable(getattr(mgr, "record_runtime_failure", None)):
                        mgr.record_runtime_failure(
                            error=str(e),
                            error_type="connection_failure",
                        )
                except Exception:
                    pass
            self._emit_request_telemetry(
                "request_failed",
                started_at,
                upstream_path,
                client_model,
                backend_model=backend_model,
                stream=bool(body.get("stream")),
                error=str(e),
                phase="upstream_request",
                runtime_metrics=runtime_metrics,
                request_id=request_id,
            )
            initialization = self.handler._initialization_payload()
            _cp_c = self._minimal_cp_for_service_status(
                initialization,
                model_visible=True,
                backend_reachable=False,
                backend_ready=False,
                backend_error=str(e),
            )
            _ss_c = build_service_status(_cp_c)
            _backend_payload = build_responses_error_payload(
                error="backend unavailable",
                reason=str(e),
                requested_model=client_model,
                proxy_initialization=initialization,
                readiness={
                    "proxy_ready": bool(initialization.get("ready")),
                    "catalog_ready": bool(initialization.get("catalog_ready")),
                    "model_visible": True,
                    "backend_ready": False,
                },
                operator_hint=(
                    "The QuantZhai proxy is running but the llama.cpp/TurboQuant backend "
                    "is unreachable. Check /qz/status for backend health. "
                    "qz-codex does not need local Docker or llama.cpp access."
                ),
                status_code=502,
                service_status=_ss_c,
            )
            try:
                self.handler.telemetry.emit("responses_rejected_backend_unavailable", {
                    "request_id": request_id,
                    "model": client_model,
                    "backend_model": backend_model,
                    "path": upstream_path,
                    "stream": bool(body.get("stream")),
                    "error": str(e),
                })
            except Exception:
                pass
            self.handler._send_json(502, _backend_payload)
            return

        content_type = resp.content_type
        status = resp.status

        if upstream_path == "/v1/responses" and client_wants_stream and "text/event-stream" in content_type:
            self.handler.send_response(status)
            self.handler.send_header("Content-Type", "text/event-stream")
            self.handler.send_header("Cache-Control", "no-cache")
            self.handler.send_header("Connection", "close")
            self.handler._send_codex_rate_limit_headers()
            self.handler.end_headers()
            self.handler._write_codex_rate_limits_event()
            stream_result = None

            try:
                status_text = (
                    f"status={status}\n"
                    f"content_type={content_type}\n"
                    "stream=passthrough\n"
                    f"reasoning_stream_format={self.handler.reasoning_stream_format}\n"
                    "rate_limits=local\n"
                )
                write_dual_capture("latest-upstream-status.txt", request_id, "upstream-status.txt", status_text)
                write_dual_capture("latest-upstream-response.raw", request_id, "upstream-response.raw", b"", mode="bytes")
                write_request_capture(request_id, "forwarded-sse.raw", b"", mode="bytes")
                raw_handles = open_dual_capture_append(
                    "latest-upstream-response.raw",
                    request_id=request_id,
                    request_name="upstream-response.raw",
                    binary=True,
                )
                raw_log = _MultiRawLog(raw_handles) if raw_handles else None
            except Exception:
                raw_log = None

            try:
                stream_result = self.handler._write_transformed_sse_stream(resp, raw_log, started_at=started_at)
            except (BrokenPipeError, ConnectionResetError):
                self._emit_request_telemetry(
                    "request_failed",
                    started_at,
                    upstream_path,
                    client_model,
                    backend_model=backend_model,
                    stream=True,
                    error="client disconnected",
                    phase="upstream_stream",
                    runtime_metrics=runtime_metrics,
                    request_id=request_id,
                )
                pass
            finally:
                if raw_log is not None:
                    raw_log.close()
            self._emit_request_telemetry(
                "request_completed",
                started_at,
                upstream_path,
                client_model,
                backend_model=backend_model,
                stream=True,
                status=status,
                content_type=content_type,
                usage=stream_result.get("usage") if isinstance(stream_result, dict) else None,
                prompt_ms=stream_result.get("prompt_ms") if isinstance(stream_result, dict) else None,
                gen_ms=stream_result.get("gen_ms") if isinstance(stream_result, dict) else None,
                output_items=stream_result.get("output_items") if isinstance(stream_result, dict) else None,
                runtime_metrics=runtime_metrics,
                request_id=request_id,
            )
            self.handler.close_connection = True
            return

        resp_data = resp.data

        if capture_enabled():
            try:
                write_dual_capture("latest-upstream-response.raw", request_id, "upstream-response.raw", resp_data, mode="bytes")
                write_dual_capture("latest-upstream-status.txt", request_id, "upstream-status.txt", f"status={status}\ncontent_type={content_type}\n")
            except Exception:
                pass

        try:
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = client_model
            if upstream_path == "/v1/responses":
                out["usage"] = _normalize_response_usage(out.get("usage"))

            self.handler._send_json(status, out)
            self._emit_request_telemetry(
                "request_completed",
                started_at,
                upstream_path,
                client_model,
                backend_model=backend_model,
                stream=bool(body.get("stream")),
                status=status,
                content_type=content_type,
                usage=out.get("usage"),
                runtime_metrics=runtime_metrics,
                request_id=request_id,
            )
        except Exception:
            self.handler.send_response(status)
            self.handler.send_header("Content-Type", content_type)
            self.handler._send_codex_rate_limit_headers()
            self.handler.send_header("Content-Length", str(len(resp_data)))
            self.handler.end_headers()
            self.handler.wfile.write(resp_data)
            self._emit_request_telemetry(
                "request_completed",
                started_at,
                upstream_path,
                client_model,
                backend_model=backend_model,
                stream=bool(body.get("stream")),
                status=status,
                content_type=content_type,
                runtime_metrics=runtime_metrics,
                request_id=request_id,
            )

    def proxy_raw(self, method):
        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        data = self.handler.rfile.read(length) if length else None

        try:
            resp = self.handler._backend().request(
                self.handler.path,
                method=method,
                body=data,
                headers={"Content-Type": self.handler.headers.get("Content-Type", "application/json")},
                timeout=900,
            )
        except Exception as e:
            self.handler._send_json(502, {"error": f"upstream error: {e}"})
            return

        self.handler.send_response(resp.status)
        self.handler.send_header("Content-Type", resp.content_type)
        self.handler._send_codex_rate_limit_headers()
        self.handler.send_header("Content-Length", str(len(resp.data)))
        self.handler.end_headers()
        self.handler.wfile.write(resp.data)
