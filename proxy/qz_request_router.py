#!/usr/bin/env python3
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

RUNTIME_METRICS_SCHEMA = "qz.runtime.metrics.v1"
PROMPT_CONTRACT_SCHEMA = "qz.prompt.contract.v1"
CAPTURE_CONTRACT_SCHEMA = "qz.capture.contract.v1"
REASONING_STREAM_FORMATS = {"raw", "summary", "hidden"}

try:
    from .qz_config_report import effective_config_payload
    from .qz_telemetry import TELEMETRY_RECENT_SCHEMA, TELEMETRY_REQUEST_SCHEMA
    from .qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, api_contract_payload
    from .qz_responses import (
        _apply_patch_output_style,
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
    from .qz_responses_stream import ResponsesStreamRuntime
    from .qz_sse import _normalize_response_usage, make_sse_block
    from .qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from .qz_codex_metadata import extract_codex_request_context
    from .qz_native_tool_output import classify_native_tool_outputs
    from .qz_search_policy import resolve_search_policy_selection
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
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
    from qz_config_report import effective_config_payload
    from qz_telemetry import TELEMETRY_RECENT_SCHEMA, TELEMETRY_REQUEST_SCHEMA
    from qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, api_contract_payload
    from qz_responses import (
        _apply_patch_output_style,
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
    from qz_responses_stream import ResponsesStreamRuntime
    from qz_sse import _normalize_response_usage, make_sse_block
    from qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from qz_codex_metadata import extract_codex_request_context
    from qz_native_tool_output import classify_native_tool_outputs
    from qz_search_policy import resolve_search_policy_selection
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
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

    def _proxy_startup_ready(self) -> bool:
        ready_fn = getattr(self.handler, "_startup_ready", None)
        if callable(ready_fn):
            try:
                return bool(ready_fn())
            except Exception:
                return False
        return True

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
            root = Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1])).resolve()
            inventory_path = Path(os.environ.get(
                "QZ_MODEL_INVENTORY_CACHE",
                root / "var" / "model-inventory.json",
            ))
            codex_home = Path(os.environ.get("CODEX_HOME", root / "var" / "codex-home"))
            catalog_dst = codex_home / "model-catalogs" / "qwenzhai-models.json"
            config_dst = codex_home / "config.toml"
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

        self.proxy_raw("GET")

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

        if self.handler.path == "/qz/models/refresh":
            if not self._proxy_startup_ready():
                self.handler._send_json(503, self._proxy_initializing_error_payload())
                return
            catalog = self.handler._model_catalog()
            catalog.refresh()
            codex_catalog_written = self._refresh_codex_catalog(catalog)
            self.handler._send_json(200, {
                "catalog": catalog.to_payload(),
                "backend": self.handler._backend_models(),
                "codex_catalog_updated": codex_catalog_written,
            })
            return

        if self.handler.path in ("/qz/models/load", "/qz/models/select"):
            if not self._proxy_startup_ready():
                self.handler._send_json(503, self._proxy_initializing_error_payload())
                return
            length = int(self.handler.headers.get("Content-Length", "0") or "0")
            raw = self.handler.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self.handler._send_json(400, {"error": f"invalid JSON: {e}"})
                return
            requested = body.get("model") or body.get("key") or body.get("name")
            catalog = self.handler._model_catalog()
            with self._request_gate(self.handler.path, requested or "", False):
                selected, reason = self.handler._resolve_model_selection(requested)
            if selected is None:
                if isinstance(reason, dict):
                    self.handler._send_json(503, reason)
                else:
                    self.handler._send_json(404, {"error": "no model selected", "reason": reason})
                return
            self.handler._send_json(200, {
                "selected": selected,
                "reason": reason,
                "backend": self.handler._backend_models(),
                "catalog": catalog.to_payload(),
            })
            return

        self.proxy_raw("POST")

    def _web_runtime(self, selected_model=None):
        selection = resolve_search_policy_selection(
            base_policy=self.handler.searxng_policy,
            base_policy_path=getattr(self.handler, "searxng_policy_path", ""),
            selected_model=selected_model,
            root=getattr(self.handler, "root", Path(__file__).resolve().parents[1]),
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
        apply_patch_output_style: str = "native",
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
        return runtime.run(body, requested_model, apply_patch_output_style)

    def _run_responses_locally(
        self,
        body: dict,
        requested_model: str,
        apply_patch_output_style: str = "native",
        selected_model=None,
        request_id: str = "",
    ):
        url = self.handler.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        web_runtime = self._web_runtime(selected_model)
        proxy_tool_registry = self._proxy_tool_registry(web_runtime)

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
                    apply_patch_output_style,
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
                    next_input.append(item)
                    continue

                decision = proxy_tool_registry.completed_call_decision(
                    item,
                    apply_patch_output_style,
                    dropped_tool_names=dropped_tool_names,
                )
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
            payload = self._proxy_initializing_error_payload()
            self._emit_request_telemetry(
                "request_failed",
                started_at,
                upstream_path,
                body.get("model") or "",
                error=payload.get("reason") or payload.get("error"),
                phase="proxy_initialization",
                request_id=request_id,
            )
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
            selected_model, selection_reason = self.handler._resolve_model_selection(client_model)
            if selected_model is None:
                error_text = selection_reason.get("reason") if isinstance(selection_reason, dict) else selection_reason
                self._emit_request_telemetry("request_failed", started_at, upstream_path, client_model, error=error_text or "no model available", phase="select_model", request_id=request_id)
                if isinstance(selection_reason, dict):
                    self.handler._send_json(503, selection_reason)
                else:
                    self.handler._send_json(503, {
                        "error": "no model available",
                        "reason": selection_reason,
                    })
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
                    for _obs_event, _obs_payload in classify_native_tool_outputs(_raw_input):
                        try:
                            self.handler.telemetry.emit(_obs_event, {**_obs_payload, "request_id": request_id})
                        except Exception:
                            pass

                body = self.handler._model_router().inject_runtime_state(body, client_model)
                ensure_apply_patch_tool_policy(body, overwrite=True)
                apply_patch_output_style = _apply_patch_output_style(body)
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
                body = normalize_tools_for_llamacpp(body)

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
                            apply_patch_output_style,
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
                                "id": f"resp_local_{_now_ts()}",
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
                    return

                try:
                    status, content_type, out = self._run_responses_locally(
                        body,
                        client_model,
                        apply_patch_output_style,
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
            self.handler._send_json(502, {"error": f"upstream error: {e}"})
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
