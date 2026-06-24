#!/usr/bin/env python3
import argparse
import json
import os
import time
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .qz_backend import BackendClient
    from .qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        api_contract_payload,
    )
    from .qz_model_catalog import ModelCatalog
    from .qz_model_router import ModelRouter
    from .qz_request_router import RequestRouter
    from .qz_responses import (
        _build_local_compaction_response,
        _resolve_compact_context_window,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _normalize_ws,
        _now_ts,
        _parse_apply_patch_arguments,
        _truncate,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
        recursive_clean,
    )
    from .qz_responses_stream import ResponsesStreamRuntime
    from .qz_sse import (
        _normalize_response_usage,
        make_sse_block,
        make_response_stream_events,
        transform_sse_event,
    )
    from .qz_streaming import StreamedFunctionCallAssembler, parse_sse_event_lines
    from .qz_telemetry import DEFAULT_TELEMETRY
    from .qz_recovery_state import RECOVERY_STATE
    from .qz_active_requests import ACTIVE_REQUESTS
    from .qz_recovery_jobs import RECOVERY_JOBS
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from .qz_runtime_io import append_capture, read_json, runtime_log, write_capture
    from .qz_search_config import load_search_config as _load_search_config, resolve_searchengines_base_url as _resolve_searchengines_base_url
    from .qz_backend_manager import BackendManager, _parse_bool_env
except ImportError:
    from qz_backend import BackendClient
    from qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        api_contract_payload,
    )
    from qz_model_catalog import ModelCatalog
    from qz_model_router import ModelRouter
    from qz_request_router import RequestRouter
    from qz_responses import (
        _build_local_compaction_response,
        _resolve_compact_context_window,
        _custom_apply_patch_call_to_function_call,
        _custom_apply_patch_output_to_function_output,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _normalize_ws,
        _now_ts,
        _parse_apply_patch_arguments,
        _truncate,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_input_for_llamacpp,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
        recursive_clean,
    )
    from qz_responses_stream import ResponsesStreamRuntime
    from qz_sse import (
        _normalize_response_usage,
        make_sse_block,
        make_response_stream_events,
        transform_sse_event,
    )
    from qz_streaming import StreamedFunctionCallAssembler, parse_sse_event_lines
    from qz_telemetry import DEFAULT_TELEMETRY
    from qz_recovery_state import RECOVERY_STATE
    from qz_active_requests import ACTIVE_REQUESTS
    from qz_recovery_jobs import RECOVERY_JOBS
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from qz_runtime_io import append_capture, read_json, runtime_log, write_capture
    from qz_search_config import load_search_config as _load_search_config, resolve_searchengines_base_url as _resolve_searchengines_base_url
    from qz_backend_manager import BackendManager, _parse_bool_env


def _load_operational_store_optional():
    """Return an OperationalStore for BackendManager backend lifecycle event recording.

    Fail-open: returns None on any error (import failure, init exception, etc.).
    BackendManager._emit() is a no-op when operational_store is None, so the proxy
    and BackendManager start correctly even when OperationalStore is unavailable.
    """
    try:
        try:
            from .qz_operational_store import OperationalStore as _OpStore
        except ImportError:
            from qz_operational_store import OperationalStore as _OpStore  # type: ignore[no-redef]
        store = _OpStore.from_env()
        store.init()
        return store
    except Exception:
        return None


class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:18084"
    reasoning_stream_format = "raw"
    runtime_state_enabled = False
    model_load_timeout = 120.0
    request_gate = threading.Lock()
    recovery_state = RECOVERY_STATE
    active_requests = ACTIVE_REQUESTS
    recovery_jobs = RECOVERY_JOBS
    model_catalog = None
    model_catalog_path = None
    backend_manager = None
    backend_client = None
    searxng_base_url = None
    searxng_timeout = 15.0
    searxng_policy_path = None
    searxng_capabilities_path = None
    searxng_policy = {}
    searxng_capabilities = {}
    search_config_result = None
    web_search_cache = {}
    opened_page_cache = {}
    active_deprecation = None
    telemetry = DEFAULT_TELEMETRY
    model_load_state = "idle"
    model_load_error = None
    model_load_started_at = None
    model_load_finished_at = None
    model_load_model = None
    model_load_health = None
    model_state_path = None
    backend_state_path = None
    root = None
    initialization_lock = threading.Lock()
    initialization_state = "ready"
    initialization_error = None
    initialization_started_at = None
    initialization_finished_at = None

    def log_message(self, fmt, *args):
        return

    @classmethod
    def _set_initialization_state(cls, state: str, error: str | None = None):
        now = time.time()
        with cls.initialization_lock:
            previous = getattr(cls, "initialization_state", "ready")
            cls.initialization_state = state or "unknown"
            cls.initialization_error = error or None
            if state == "initializing" and not cls.initialization_started_at:
                cls.initialization_started_at = now
                cls.initialization_finished_at = None
            elif state in {"ready", "failed"}:
                cls.initialization_finished_at = now
        try:
            cls.telemetry.emit("proxy_initialization", {
                "state": cls.initialization_state,
                "previous": previous,
                "error": cls.initialization_error,
            })
        except Exception:
            pass

    def _initialization_payload(self):
        cls = self.__class__
        with cls.initialization_lock:
            state = getattr(cls, "initialization_state", "ready") or "ready"
            error = getattr(cls, "initialization_error", None)
            started_at = getattr(cls, "initialization_started_at", None)
            finished_at = getattr(cls, "initialization_finished_at", None)
        return {
            "schema": "qz.proxy.initialization.v1",
            "state": state,
            "ready": state == "ready",
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error,
            "catalog_ready": getattr(cls, "model_catalog", None) is not None,
            "search_ready": bool(getattr(cls, "searxng_policy", None) or getattr(cls, "searxng_capabilities", None)),
        }

    def _startup_ready(self) -> bool:
        return bool(self._initialization_payload().get("ready"))

    def _initializing_error_payload(self):
        init = self._initialization_payload()
        if init.get("state") == "failed":
            return {
                "error": "proxy initialization failed",
                "reason": init.get("error") or "startup initialization failed",
                "initialization": init,
            }
        return {
            "error": "proxy initializing",
            "reason": "model catalog and startup policy are still loading",
            "initialization": init,
        }

    def _send_json(self, status, obj, headers=None):
        data = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self._send_codex_rate_limit_headers()
            for key, value in (headers or {}).items():
                self.send_header(str(key), str(value))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before the response could be sent.
            # The action has already completed; this is a transport-only failure.
            try:
                runtime_log(
                    "latest-send-json-disconnect.txt",
                    f"_send_json: client disconnected (status={status})\n",
                )
            except Exception:
                pass
            self.close_connection = True

    def _send_telemetry_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_codex_rate_limit_headers()
        self.end_headers()

        with self.telemetry.subscribe() as events:
            try:
                runtime = None
                try:
                    runtime = self._model_router().status_summary(self.path)
                except Exception:
                    runtime = None
                self.wfile.write(make_sse_block("telemetry_stream_open", self.telemetry.stream_open_event(runtime=runtime)))
                self.wfile.flush()

                for event in self.telemetry.recent(50):
                    self.wfile.write(make_sse_block(event["type"], event))
                    self.wfile.flush()

                while True:
                    try:
                        event = events.get(timeout=30)
                    except Exception:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    self.wfile.write(make_sse_block(event["type"], event))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _telemetry_sse_payload(self, event_type, payload):
        if not isinstance(payload, dict):
            return None
        compact = dict(payload)
        response = compact.get("response")
        if isinstance(response, dict):
            compact["response"] = {
                key: response.get(key)
                for key in ("id", "model", "status", "created_at", "usage")
                if response.get(key) is not None
            }
            if event_type == "response.completed":
                try:
                    compact["qz_runtime"] = self._model_router().runtime_state_payload(response.get("model") or "")
                except Exception:
                    pass
        item = compact.get("item")
        if isinstance(item, dict):
            compact["item"] = {
                key: item.get(key)
                for key in ("id", "type", "status", "role", "call_id", "name")
                if item.get(key) is not None
            }
        return {
            "event_type": event_type,
            "payload": compact,
        }

    def _emit_sse_telemetry(self, chunk, request_id: str = ""):
        if not chunk:
            return
        event_name = ""
        data_lines = []
        for raw_line in chunk.splitlines():
            try:
                line = raw_line.decode("utf-8", errors="replace")
            except AttributeError:
                line = str(raw_line)
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except Exception:
            return

        event_type = event_name or payload.get("type") or "event"
        if event_type not in {
            "response.created",
            "response.in_progress",
            "response.completed",
            "response.output_item.added",
            "response.output_item.done",
            "response.reasoning_text.delta",
            "response.reasoning_text.done",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary_text.done",
            "response.output_text.delta",
            "response.output_text.done",
            "response.function_call_arguments.delta",
        }:
            return

        compact = self._telemetry_sse_payload(event_type, payload)
        if compact is not None:
            if request_id:
                compact["request_id"] = request_id
            self.telemetry.emit("sse_event", compact)


    def _send_codex_rate_limit_headers(self):
        primary = LOCAL_CODEX_RATE_LIMITS["primary"]
        secondary = LOCAL_CODEX_RATE_LIMITS["secondary"]
        credits = LOCAL_CODEX_RATE_LIMITS["credits"]

        self.send_header("x-codex-limit-id", LOCAL_CODEX_RATE_LIMITS["limit_id"])
        self.send_header("x-codex-limit-name", LOCAL_CODEX_RATE_LIMITS["limit_name"])
        self.send_header("x-codex-plan-type", LOCAL_CODEX_RATE_LIMITS["plan_type"])
        self.send_header("x-codex-primary-used-percent", str(primary["used_percent"]))
        self.send_header("x-codex-primary-window-minutes", str(primary["window_minutes"]))
        self.send_header("x-codex-primary-resets-in-seconds", str(primary["resets_in_seconds"]))
        self.send_header("x-codex-primary-resets-at", str(primary["resets_at"]))
        self.send_header("x-codex-secondary-used-percent", str(secondary["used_percent"]))
        self.send_header("x-codex-secondary-window-minutes", str(secondary["window_minutes"]))
        self.send_header("x-codex-secondary-resets-in-seconds", str(secondary["resets_in_seconds"]))
        self.send_header("x-codex-secondary-resets-at", str(secondary["resets_at"]))
        self.send_header("x-codex-credits-has-credits", "true" if credits["has_credits"] else "false")
        self.send_header("x-codex-credits-unlimited", "true" if credits["unlimited"] else "false")
        self._send_deprecation_headers()

    def _send_deprecation_headers(self):
        info = self.active_deprecation
        if not isinstance(info, dict):
            return
        reason = info.get("reason") or "Endpoint is deprecated."
        replacement = info.get("replacement") or "/v1/responses"
        self.send_header("Deprecation", "true")
        self.send_header("X-QuantZhai-Deprecated", "true")
        self.send_header("X-QuantZhai-Replacement", replacement)
        self.send_header("Warning", f'299 QuantZhai "{reason}"')

    def _mark_deprecated_endpoint(self, path: str):
        self.active_deprecation = LEGACY_API_ENDPOINTS.get(path)
        if not self.active_deprecation:
            return
        self.telemetry.emit("deprecated_endpoint", {
            "path": path,
            "replacement": self.active_deprecation.get("replacement"),
            "removal": self.active_deprecation.get("removal"),
        })
        try:
            import time
            append_capture(
                "latest-deprecated-api.log",
                f"{time.time():.3f} {path} replacement={self.active_deprecation.get('replacement')} removal={self.active_deprecation.get('removal')}\n",
            )
        except Exception:
            pass

    def _codex_rate_limits_payload(self):
        payload = {
            "type": "codex.rate_limits",
            "rate_limits": LOCAL_CODEX_RATE_LIMITS,
            "metered_limit_name": "local",
        }
        payload.update(LOCAL_CODEX_RATE_LIMITS)
        return payload

    def _write_codex_rate_limits_event(self):
        self.wfile.write(make_sse_block("codex.rate_limits", self._codex_rate_limits_payload()))
        self.wfile.flush()


    def _handle_responses_compact(self):
        """Handle POST /v1/responses/compact — QuantZhai remote compaction endpoint.

        Codex calls this endpoint when supports_remote_compaction() is true (triggered
        by provider.name == "OpenAI" in the Codex config.toml — Stage 6.10.1 masquerade).

        Request body is CompactionInput: { model, input: [...ResponseItems], instructions,
        tools, parallel_tool_calls, ... }.  QuantZhai runs Zenkai v3 (or heuristic v2
        fallback) on request.input and returns:
            { "output": [...compacted ResponseItems...] }

        The "output" list starts with a compaction blob item followed by the preserved
        recent items. Codex reads only the "output" field; extra top-level fields in
        the response dict (id, object, created_at, usage) are ignored.

        Double-compaction: this path is separate from the inline /v1/responses path.
        The inline compaction only triggers when context_management.compact_threshold
        is set in the request body. Codex does not set that when using remote compaction.
        """
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        try:
            write_capture("latest-compact-request.json", body)
        except Exception:
            pass

        # Resolve the currently selected model's context window for budget-aware
        # v3 compaction. The CompactionInput body includes a "model" field that
        # Codex passes for model identification. We use _resolve_compact_context_window()
        # which checks catalog fields 'runtime_context_length' and 'context_length'
        # (ModelCatalog entries do NOT have a 'context_window' field — that is only
        # in the Codex-facing catalog JSON). The resolution reason is forwarded into
        # the v2 blob metadata for diagnostics when v3 falls back.
        _ctx_tokens = None
        _ctx_reason = None
        try:
            catalog = self._model_catalog()
            client_model = str(body.get("model") or "").strip()
            _ctx_tokens, _ctx_reason, _ctx_label = _resolve_compact_context_window(catalog, client_model)
        except Exception as e:
            _ctx_reason = f"catalog_exception:{type(e).__name__}"

        # Resolve the LLM backend URL from BackendManager.
        # BackendManager owns the live backend runtime details; _call_llm_compactor()
        # must never guess the URL from QZ_SERVER_HOST/QZ_SERVER_PORT.
        mgr = getattr(self.__class__, "backend_manager", None)
        llm_base_url = ""
        backend_resolution_detail: dict = {
            "source": "backend_manager",
            "ok": False,
            "reason": "backend_manager_missing",
        }
        if mgr is not None:
            try:
                snap = mgr.snapshot()
                phase = snap.get("phase") or ""
                launch_basename = snap.get("launch_model_path_basename") or ""
                backend_resolution_detail = {
                    "source": "backend_manager",
                    "ok": False,
                    "phase": phase,
                    "backend_health_ok": snap.get("backend_health_ok"),
                    "launch_model_key": snap.get("launch_model_key") or "",
                    "launch_model_backend_id": snap.get("launch_model_backend_id") or "",
                    "launch_model_path_basename": launch_basename,
                    "reason": "",
                }
                if phase not in {"running", "healthy"}:
                    backend_resolution_detail["reason"] = "backend_not_running"
                elif not launch_basename:
                    backend_resolution_detail["reason"] = "launch_model_missing"
                elif not callable(getattr(mgr, "llm_base_url", None)):
                    backend_resolution_detail["reason"] = "backend_manager_missing_llm_base_url"
                else:
                    llm_base_url = mgr.llm_base_url()
                    backend_resolution_detail["ok"] = True
                    backend_resolution_detail["base_url_source"] = "backend_manager"
                    backend_resolution_detail["reason"] = "ok"
            except Exception as exc:
                backend_resolution_detail = {
                    "source": "backend_manager",
                    "ok": False,
                    "reason": f"backend_manager_exception:{type(exc).__name__}",
                }

        out = _build_local_compaction_response(
            body,
            selected_context_tokens=_ctx_tokens,
            remote_compaction=True,
            context_resolution_reason=_ctx_reason,
            llm_base_url=llm_base_url,
            backend_resolution_detail=backend_resolution_detail,
        )

        try:
            cmp_item = next((item for item in out.get("output", []) if isinstance(item, dict) and item.get("type") == "compaction"), None)
            payload = _decode_local_compaction_blob(cmp_item.get("encrypted_content", "")) if cmp_item else None
            if payload:
                write_capture("latest-compact-summary.txt", payload.get("summary_text", ""))
        except Exception:
            pass

        self._send_json(200, out)

        # Post-compaction memory pressure check — fire the memory manager in a
        # background thread if accumulated management debt exceeds the threshold.
        # The manager call uses the same backend so the next Codex request will
        # see a hold-open wait while it runs (user sees a brief pause — intended).
        try:
            from .qz_braincase_db import BrainCaseDB
            from .qz_braincase_manager import maybe_run_manager_async
        except ImportError:
            try:
                from qz_braincase_db import BrainCaseDB
                from qz_braincase_manager import maybe_run_manager_async
            except ImportError:
                BrainCaseDB = None  # type: ignore[assignment,misc]
                maybe_run_manager_async = None  # type: ignore[assignment]
        if maybe_run_manager_async is not None and BrainCaseDB is not None:
            try:
                _db = BrainCaseDB.from_env()
                _db.init()
                if _db.available:
                    _input_items = list(body.get("input") or [])
                    _selected = getattr(self.__class__, "model_catalog", None)
                    _domain = None
                    try:
                        _sel_entry = _selected.selected if _selected else None
                        if isinstance(_sel_entry, dict):
                            _domain = _sel_entry.get("memory_domain")
                    except Exception:
                        pass
                    maybe_run_manager_async(
                        _input_items,
                        _db,
                        root=os.environ.get("QZ_ROOT", ""),
                        llm_base_url=llm_base_url,
                        llm_model=str((backend_resolution_detail or {}).get("launch_model_backend_id") or ""),
                        memory_domain=_domain,
                    )
            except Exception:
                pass


    def _write_transformed_sse_stream(self, resp, raw_log=None, started_at=None):
        summary_started = set()
        function_calls = StreamedFunctionCallAssembler()
        event_lines = []
        stream_started_at = started_at if isinstance(started_at, (int, float)) else time.time()
        first_output_at = None
        completed_at = None
        final_usage = _normalize_response_usage({})
        output_items = 0
        sent_terminal = False
        sent_done = False

        while True:
            chunk = resp.readline()
            if not chunk:
                if event_lines:
                    event_type, payload = parse_sse_event_lines(event_lines)
                    if first_output_at is None and event_type not in {"response.created", "response.in_progress"}:
                        first_output_at = time.time()
                    if event_type == "response.completed" and isinstance(payload, dict):
                        response = payload.get("response")
                        if isinstance(response, dict):
                            final_usage = _normalize_response_usage(response.get("usage"))
                    function_calls.observe(event_type, payload)
                    for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                        self._emit_sse_telemetry(out_chunk)
                        self.wfile.write(out_chunk)
                        self.wfile.flush()
                    if event_type == "done" or payload == "[DONE]":
                        sent_done = True
                    elif is_terminal_stream_event(event_type, payload):
                        sent_terminal = True
                    completed_at = time.time()
                break

            if raw_log is not None:
                raw_log.write(chunk)
                raw_log.flush()

            event_lines.append(chunk)
            if chunk in (b"\n", b"\r\n"):
                event_type, payload = parse_sse_event_lines(event_lines)
                if first_output_at is None and event_type not in {"response.created", "response.in_progress"}:
                    first_output_at = time.time()
                if event_type == "response.completed" and isinstance(payload, dict):
                    response = payload.get("response")
                    if isinstance(response, dict):
                        final_usage = _normalize_response_usage(response.get("usage"))
                if event_type == "response.output_item.done":
                    output_items += 1
                function_calls.observe(event_type, payload)
                for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                    self._emit_sse_telemetry(out_chunk)
                    self.wfile.write(out_chunk)
                    self.wfile.flush()
                if event_type == "done" or payload == "[DONE]":
                    sent_done = True
                elif is_terminal_stream_event(event_type, payload):
                    sent_terminal = True
                event_lines = []
                if event_type == "response.completed":
                    completed_at = time.time()

        if sent_terminal and not sent_done:
            done_chunk = b"data: [DONE]\n\n"
            self._emit_sse_telemetry(done_chunk)
            self.wfile.write(done_chunk)
            self.wfile.flush()

        if completed_at is None:
            completed_at = time.time()
        prompt_ms = 0.0
        gen_ms = 0.0
        if isinstance(first_output_at, (int, float)) and first_output_at >= stream_started_at:
            prompt_ms = max(0.0, (first_output_at - stream_started_at) * 1000.0)
            gen_ms = max(0.0, (completed_at - first_output_at) * 1000.0)
        return {
            "usage": final_usage,
            "prompt_ms": prompt_ms,
            "gen_ms": gen_ms,
            "first_output_at": first_output_at,
            "completed_at": completed_at,
            "output_items": output_items,
        }


    def _model_catalog(self):
        if self.model_catalog is None:
            if not self._startup_ready():
                raise RuntimeError("proxy initialization is not complete")
            root = Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1]))
            self.__class__.model_catalog = ModelCatalog.from_env(root)
            self.__class__.model_catalog_path = str(self.model_catalog.cache_path)
        return self.model_catalog

    def _model_router(self):
        return ModelRouter(self)

    def _backend(self, authorization=None):
        auth = authorization or self.headers.get("Authorization", "Bearer local")
        if self.backend_client is None or self.backend_client.authorization != auth or self.backend_client.upstream != self.upstream:
            self.__class__.backend_client = BackendClient(self.upstream, auth)
        return self.backend_client

    def _backend_models(self):
        return self._model_router().backend_models()

    def _load_backend_model(self, model_id: str, wait: bool = False, timeout: float | None = None):
        self._model_router().load_backend_model(model_id, wait=wait, timeout=timeout)

    def _resolve_model_selection(self, requested_model):
        return self._model_router().resolve_model_selection(requested_model)

    def _model_catalog_payload(self):
        return self._model_router().model_catalog_payload()

    def _ollama_models(self):
        return self._model_router().ollama_models()

    def _handle_ollama_get(self):
        return self._model_router().handle_ollama_get()

    def _handle_ollama_post(self):
        return self._model_router().handle_ollama_post()

    def _handle_ready_get(self):
        return self._model_router().handle_ready_get()
    def _request_router(self):
        return RequestRouter(self)

    def do_GET(self):
        self.active_deprecation = None
        self._request_router().handle_get()

    def do_POST(self):
        self.active_deprecation = None
        self._request_router().handle_post()

def _default_search_policy_path(root: Path, script_dir: Path) -> Path:
    for path in (
        root / "config" / "default" / "search-policy.json",
        root / "docs" / "searxng-agent-policy-profiled.json",
        script_dir / "searxng-agent-policy.json",
    ):
        if path.is_file():
            return path
    return root / "config" / "default" / "search-policy.json"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18180)
    ap.add_argument("--upstream", default="http://127.0.0.1:18084")
    ap.add_argument("--model-load-timeout", type=float, default=float(os.environ.get("QZ_MODEL_LOAD_TIMEOUT", "120")))
    ap.add_argument("--reasoning-stream-format", choices=("raw", "summary", "hidden"), default="raw")
    ap.add_argument("--searxng-base-url", default=_resolve_searchengines_base_url())
    ap.add_argument("--searxng-timeout", type=float, default=float(os.environ.get("SEARXNG_TIMEOUT", "15")))
    ap.add_argument("--searxng-policy", default=os.environ.get("SEARXNG_POLICY"))
    ap.add_argument("--searxng-capabilities", default=os.environ.get("SEARXNG_CAPABILITIES"))
    ap.add_argument("--qzstate", dest="runtime_state_enabled", action="store_true", default=os.environ.get("QZSTATE", "").strip() in {"1", "true", "yes", "on"})
    ap.add_argument("--no-qzstate", dest="runtime_state_enabled", action="store_false")
    args = ap.parse_args()

    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.model_load_timeout = float(args.model_load_timeout)
    ProxyHandler.reasoning_stream_format = args.reasoning_stream_format
    ProxyHandler.runtime_state_enabled = args.runtime_state_enabled

    root = Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1]))
    script_dir = Path(__file__).resolve().parent
    policy_path = Path(args.searxng_policy) if args.searxng_policy else _default_search_policy_path(root, script_dir)
    capabilities_path = Path(args.searxng_capabilities) if args.searxng_capabilities else script_dir / "searxng-capabilities.json"
    ProxyHandler.root = str(root)
    ProxyHandler.model_catalog = None
    ProxyHandler.model_catalog_path = None
    ProxyHandler.model_state_path = str(Path(os.environ.get("QZ_MODEL_STATE_PATH", str(root / "var" / "model-state.json"))).expanduser())
    ProxyHandler.backend_state_path = str(Path(os.environ.get("QZ_BACKEND_STATE_PATH", str(root / "var" / "backend-state.json"))).expanduser())

    ProxyHandler.searxng_policy_path = str(policy_path)
    ProxyHandler.searxng_capabilities_path = str(capabilities_path)
    ProxyHandler.searxng_policy = {}
    ProxyHandler.searxng_capabilities = {}
    ProxyHandler.searxng_base_url = args.searxng_base_url
    ProxyHandler.searxng_timeout = args.searxng_timeout
    ProxyHandler.initialization_started_at = None
    ProxyHandler.initialization_finished_at = None
    ProxyHandler._set_initialization_state("initializing")

    server = ThreadingHTTPServer((args.listen, args.port), ProxyHandler)

    def _resolve_launch_model_entry():
        """Resolve the model that BackendManager should launch with.

        Precedence (post-cleanup): persisted qz.model_state.v1 selection >
        catalog.selected (which already honours QZ_MODEL_KEY seed > config
        default).  Returns the catalog entry dict or None.

        Self-heal: if the persisted state is poisoned (selected_source is
        observational, e.g. "status_snapshot"), recover in this order:
          1. last_good_backend_id  — most recent confirmed-healthy selection
          2. last_loaded_model     — one-time salvage when last_good is empty
             and the model uniquely matches a catalog entry
        After salvage the state file is rewritten in canonical form so the
        poison is removed rather than carried forward on every restart.
        """
        catalog = getattr(ProxyHandler, "model_catalog", None)
        if not isinstance(catalog, ModelCatalog):
            return None

        # Load persisted selection with migration support.
        try:
            from .qz_model_state import (
                SELECTED_SOURCES,
                load_model_state,
                selected_identity_from_state,
                write_model_state,
            )
            from dataclasses import replace as _dc_replace
        except ImportError:
            from qz_model_state import (
                SELECTED_SOURCES,
                load_model_state,
                selected_identity_from_state,
                write_model_state,
            )
            from dataclasses import replace as _dc_replace

        state_path = Path(ProxyHandler.model_state_path)
        state_result = load_model_state(state_path)
        state = state_result.state

        # Detect poisoned state: selection authority overwritten by an
        # observational source (e.g. "status_snapshot").
        _is_poisoned = (
            state.has_selection()
            and state.selected_source
            and state.selected_source not in SELECTED_SOURCES
        )

        if _is_poisoned:
            print(
                f"QuantZhai: startup self-heal — persisted selection "
                f"(key={state.selected_key!r}, source={state.selected_source!r}) "
                "is observational; attempting recovery.",
                flush=True,
            )
            # Recovery priority 1: last_good_backend_id
            salvage_id = state.last_good_backend_id or state.last_good_key
            if salvage_id:
                _entry, _ = catalog.resolve(query=salvage_id)
                if _entry is not None:
                    print(
                        f"QuantZhai: self-heal recovered via last_good: {salvage_id!r}",
                        flush=True,
                    )
                    try:
                        _healed = _dc_replace(
                            state,
                            selected_key=_entry.get("key") or _entry.get("slug") or salvage_id,
                            selected_backend_id=_entry.get("backend_id") or salvage_id,
                            selected_label=_entry.get("label") or "",
                            selected_source="fallback",
                            selected_reason="startup self-heal: last_good_backend_id",
                        )
                        write_model_state(_healed, state_path)
                    except Exception as _exc:
                        print(f"QuantZhai: self-heal state rewrite failed: {_exc}", flush=True)
                    return _entry

            # Recovery priority 2: last_loaded_model (one-time salvage).
            # Allowed only when last_loaded_model is a unique exact match against
            # catalog/backend identity fields.  Fuzzy or ambiguous resolution is
            # rejected — it is safer to fall through to the catalog default than
            # to silently load the wrong model.
            salvage_model = state.last_loaded_model
            if salvage_model:
                _q = salvage_model.strip()
                _identity_fields = ("backend_id", "key", "slug", "filename")
                _exact_matches = [
                    e for e in (catalog.entries or [])
                    if isinstance(e, dict)
                    and any(
                        isinstance(e.get(f), str) and e.get(f, "").strip() == _q
                        for f in _identity_fields
                    )
                ]
                _entry = _exact_matches[0] if len(_exact_matches) == 1 else None
                if _entry is not None:
                    _backend_id = _entry.get("backend_id") or salvage_model
                    print(
                        f"QuantZhai: self-heal recovered via last_loaded_model "
                        f"(unique exact match): {salvage_model!r} → backend_id={_backend_id!r}",
                        flush=True,
                    )
                    try:
                        _healed = _dc_replace(
                            state,
                            selected_key=_entry.get("key") or _entry.get("slug") or salvage_model,
                            selected_backend_id=_backend_id,
                            selected_label=_entry.get("label") or "",
                            selected_source="fallback",
                            selected_reason="startup self-heal: last_loaded_model salvage",
                        )
                        write_model_state(_healed, state_path)
                    except Exception as _exc:
                        print(f"QuantZhai: self-heal state rewrite failed: {_exc}", flush=True)
                    return _entry
                else:
                    print(
                        f"QuantZhai: self-heal skipping last_loaded_model {salvage_model!r} "
                        f"— {'ambiguous' if len(_exact_matches) > 1 else 'no'} exact catalog match.",
                        flush=True,
                    )

            print(
                "QuantZhai: self-heal found no usable recovery anchor "
                "(last_good empty, last_loaded_model unresolvable); "
                "falling through to catalog default.",
                flush=True,
            )

        # Normal path: use canonical persisted selection.
        if not _is_poisoned:
            requested = selected_identity_from_state(state)
            if requested:
                entry, _reason = catalog.resolve(query=requested)
                if entry is not None:
                    return entry

        # No valid persisted selection — fall back to catalog default
        # (which already incorporates QZ_MODEL_KEY seed).
        fallback = getattr(catalog, "selected", None)
        return fallback if isinstance(fallback, dict) else None

    def _preload_last_model():
        """Resolve launch model and enqueue autostart. Synchronous resolution."""
        catalog = getattr(ProxyHandler, "model_catalog", None)
        if catalog is None:
            return

        entry = _resolve_launch_model_entry()
        if entry is None:
            print(
                "QuantZhai: no launch model resolved (no persisted selection, "
                "QZ_MODEL_KEY seed failed, or catalog empty); "
                "POST /qz/model/select-and-restart to choose one.",
                flush=True,
            )
            return

        key = entry.get("key") or entry.get("slug") or entry.get("filename") or ""
        backend_id = entry.get("backend_id") or key
        # Use backend_target first so profile symlinks load by their real GGUF name.
        # Loading by filename (the symlink) registers the model under the alias in
        # the router, but requests are forwarded using backend_id (the real name).
        # That split causes router 404 on every request.  Using backend_target here
        # keeps load-name and forward-name consistent.
        filename = entry.get("backend_target") or entry.get("filename") or backend_id
        if isinstance(filename, str) and filename and not filename.endswith(".gguf"):
            filename = f"{filename}.gguf"

        if not filename:
            print(
                f"QuantZhai: resolved launch model {key!r} has no filename; "
                "POST /qz/model/select-and-restart to choose one.",
                flush=True,
            )
            return

        print(f"QuantZhai: preloading launch model {key!r} (backend_id={backend_id}, filename={filename})", flush=True)
        try:
            _backend_manager.set_launch_model(
                key=key, backend_id=backend_id, path_basename=filename,
            )
        except Exception as exc:
            print(f"QuantZhai: set_launch_model failed: {exc}", flush=True)
            return

        # Normalize persisted status_snapshot source: the reconciliation path
        # writes "status_snapshot" as the source when it observes model state
        # from a backend probe.  On proxy restart, once a real model entry is
        # resolved and the launch model is set, upgrade the source to "fallback"
        # so status_snapshot never acts as a permanent authority label.
        try:
            from .qz_model_state import load_model_state, write_model_state
            _ms_result = load_model_state(Path(ProxyHandler.model_state_path))
            _ms = _ms_result.state
            if _ms.selected_source == "status_snapshot" and _ms.has_selection():
                from dataclasses import replace as _replace
                _normalized = _replace(_ms, selected_source="fallback")
                write_model_state(_normalized, Path(ProxyHandler.model_state_path))
        except Exception:
            pass

        if _backend_autostart:
            print("QuantZhai: requesting backend autostart...", flush=True)
            _backend_manager.begin_autostart()

    def _initialize_proxy_state():
        try:
            policy = _safe_json_file(policy_path)
            capabilities = _safe_json_file(capabilities_path)
            catalog = ModelCatalog.from_env(root)
            ProxyHandler.model_catalog = catalog
            ProxyHandler.model_catalog_path = str(catalog.cache_path)
            ProxyHandler.searxng_policy = policy
            ProxyHandler.searxng_capabilities = capabilities
            ProxyHandler.searxng_base_url = args.searxng_base_url or policy.get("searxng_base") or capabilities.get("base") or _resolve_searchengines_base_url()
            try:
                ProxyHandler.search_config_result = _load_search_config(root=root)
            except Exception:
                ProxyHandler.search_config_result = None

            # Restore startup preload contract: resolve and set launch model BEFORE marking ready.
            # This ensures /health waits for resolution and autostart is correctly enqueued.
            _preload_last_model()

            ProxyHandler._set_initialization_state("ready")
            print(
                f"QuantZhai proxy initialized: catalog={ProxyHandler.model_catalog_path}, searxng_base={ProxyHandler.searxng_base_url}",
                flush=True,
            )
        except Exception as exc:
            ProxyHandler._set_initialization_state("failed", str(exc))
            print(f"QuantZhai proxy initialization failed: {exc}", flush=True)

    # Instantiate BackendManager — no Docker calls yet.
    # Use safe int parse: bad/empty env vars disable backend rather than crash proxy.
    def _eint(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default

    _backend_autostart = _parse_bool_env(os.environ.get("QZ_BACKEND_AUTOSTART", "1"), default=True)
    _operational_store = _load_operational_store_optional()
    try:
        _backend_manager = BackendManager(
            docker_cmd=os.environ.get("QZ_DOCKER_CMD", "docker"),
            container_name=os.environ.get("QZ_CONTAINER", "qwen36turbo"),
            image=os.environ.get("QZ_IMAGE", "thetom-llama-cpp-turboquant:cuda-server"),
            model_dir=os.environ.get("QZ_MODEL_DIR", str(root / "var" / "models")),
            server_host=os.environ.get("QZ_SERVER_HOST", "127.0.0.1"),
            server_port=_eint("QZ_SERVER_PORT", 18084),
            context=_eint("QZ_CONTEXT", 262144),
            parallel=_eint("QZ_PARALLEL", 1),
            batch=_eint("QZ_BATCH", 4096),
            ubatch=_eint("QZ_UBATCH", 512),
            threads=_eint("QZ_THREADS", 12),
            thread_batch=_eint("QZ_THREAD_BATCH", 12),
            tensor_split=os.environ.get("QZ_TENSOR_SPLIT", ""),
            main_gpu=_eint("QZ_MAIN_GPU", 1),
            cache_ram=_eint("QZ_CACHE_RAM", 8192),
            cache_reuse=_eint("QZ_CACHE_REUSE", 256),
            kv_key=os.environ.get("QZ_KV_KEY", "q8_0"),
            kv_value=os.environ.get("QZ_KV_VALUE", "turbo3"),
            reasoning_budget=os.environ.get("QZ_REASONING_BUDGET", "-1"),
            reasoning_budget_message=os.environ.get(
                "QZ_REASONING_BUDGET_MESSAGE",
                "I have reasoned long enough. Let me now produce my final answer.",
            ),
            spec_default=_parse_bool_env(os.environ.get("QZ_SPEC_DEFAULT", "0")),
            autostart=_backend_autostart,
            require_gpu=_parse_bool_env(os.environ.get("QZ_REQUIRE_GPU", "1"), default=True),
            gpu_log_tail=_eint("QZ_GPU_LOG_TAIL", 1000),
            operational_store=_operational_store,
        )
    except Exception as _bm_exc:
        print(f"BackendManager init failed (backend disabled): {_bm_exc}", flush=True)
        _backend_manager = BackendManager(autostart=False)
    ProxyHandler.backend_manager = _backend_manager

    threading.Thread(target=_initialize_proxy_state, daemon=True).start()
    print(
        f"QuantZhai proxy listening on {args.listen}:{args.port} -> {ProxyHandler.upstream}, reasoning_stream_format={ProxyHandler.reasoning_stream_format}, initialization=background",
        flush=True,
    )
    # NOTE: begin_autostart() is now deferred to _preload_last_model() so the
    # launch model is resolved before docker run.  In direct mode the
    # container is parameterised with -m /models/<basename>; starting before
    # the model is known would fail the launch_model safety gate.
    server.serve_forever()

if __name__ == "__main__":
    main()
