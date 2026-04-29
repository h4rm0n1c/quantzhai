#!/usr/bin/env python3
import argparse
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .qz_backend import BackendClient
    from .qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
    from .qz_model_catalog import ModelCatalog
    from .qz_model_router import ModelRouter
    from .qz_request_router import RequestRouter
    from .qz_responses import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _build_local_compaction_response,
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
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from .qz_runtime_io import append_capture, capture_path, runtime_log, write_capture
except ImportError:
    from qz_backend import BackendClient
    from qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
    from qz_model_catalog import ModelCatalog
    from qz_model_router import ModelRouter
    from qz_request_router import RequestRouter
    from qz_responses import (
        _apply_patch_call_to_function_call,
        _apply_patch_output_style,
        _apply_patch_output_to_function_output,
        _build_local_compaction_response,
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
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from qz_runtime_io import append_capture, capture_path, runtime_log, write_capture

class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:18084"
    reasoning_stream_format = "raw"
    runtime_state_enabled = False
    model_catalog = None
    model_catalog_path = None
    backend_client = None
    searxng_base_url = None
    searxng_timeout = 15.0
    searxng_policy_path = None
    searxng_capabilities_path = None
    searxng_policy = {}
    searxng_capabilities = {}
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

    def log_message(self, fmt, *args):
        return

    def _send_json(self, status, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_telemetry_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_codex_rate_limit_headers()
        self.end_headers()

        with self.telemetry.subscribe() as events:
            try:
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

    def _emit_sse_telemetry(self, chunk):
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

        out = _build_local_compaction_response(body)

        try:
            import pathlib
            cmp_item = next((item for item in out.get("output", []) if isinstance(item, dict) and item.get("type") == "compaction"), None)
            payload = _decode_local_compaction_blob(cmp_item.get("encrypted_content", "")) if cmp_item else None
            if payload:
                capture_path("latest-compact-summary.txt").write_text(
                    payload.get("summary_text", ""),
                    encoding="utf-8",
                )
        except Exception:
            pass

        self._send_json(200, out)


    def _write_transformed_sse_stream(self, resp, raw_log=None, started_at=None):
        summary_started = set()
        function_calls = StreamedFunctionCallAssembler()
        event_lines = []
        stream_started_at = started_at if isinstance(started_at, (int, float)) else time.time()
        first_output_at = None
        completed_at = None
        final_usage = _normalize_response_usage({})
        output_items = 0

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
                event_lines = []
                if event_type == "response.completed":
                    completed_at = time.time()

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

    def _load_backend_model(self, model_id: str):
        self._model_router().load_backend_model(model_id)

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18180)
    ap.add_argument("--upstream", default="http://127.0.0.1:18084")
    ap.add_argument("--reasoning-stream-format", choices=("raw", "summary", "hidden"), default="raw")
    ap.add_argument("--searxng-base-url", default=os.environ.get("SEARXNG_BASE_URL"))
    ap.add_argument("--searxng-timeout", type=float, default=float(os.environ.get("SEARXNG_TIMEOUT", "15")))
    ap.add_argument("--searxng-policy", default=os.environ.get("SEARXNG_POLICY"))
    ap.add_argument("--searxng-capabilities", default=os.environ.get("SEARXNG_CAPABILITIES"))
    ap.add_argument("--qzstate", dest="runtime_state_enabled", action="store_true", default=os.environ.get("QZSTATE", "").strip() in {"1", "true", "yes", "on"})
    ap.add_argument("--no-qzstate", dest="runtime_state_enabled", action="store_false")
    args = ap.parse_args()

    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.reasoning_stream_format = args.reasoning_stream_format
    ProxyHandler.runtime_state_enabled = args.runtime_state_enabled

    script_dir = Path(__file__).resolve().parent
    policy_path = Path(args.searxng_policy) if args.searxng_policy else script_dir / "searxng-agent-policy.json"
    capabilities_path = Path(args.searxng_capabilities) if args.searxng_capabilities else script_dir / "searxng-capabilities.json"
    policy = _safe_json_file(policy_path)
    capabilities = _safe_json_file(capabilities_path)
    root = Path(os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1]))
    catalog = ModelCatalog.from_env(root)
    ProxyHandler.model_catalog = catalog
    ProxyHandler.model_catalog_path = str(catalog.cache_path)

    ProxyHandler.searxng_policy_path = str(policy_path)
    ProxyHandler.searxng_capabilities_path = str(capabilities_path)
    ProxyHandler.searxng_policy = policy
    ProxyHandler.searxng_capabilities = capabilities
    ProxyHandler.searxng_base_url = args.searxng_base_url or policy.get("searxng_base") or capabilities.get("base")
    ProxyHandler.searxng_timeout = args.searxng_timeout

    try:
        if catalog.selected is not None:
            startup_model_id = catalog.selected.get("backend_id") or catalog.selected.get("key")
            if startup_model_id:
                backend = BackendClient(args.upstream)
                entry = backend.get_model_entry(startup_model_id, timeout=15)
                status = entry.get("status") or {}
                state = status.get("value") if isinstance(status, dict) else ""
                if state not in {"loaded", "loading"}:
                    backend.load_model(startup_model_id, timeout=120)
    except Exception:
        pass

    server = ThreadingHTTPServer((args.listen, args.port), ProxyHandler)
    print(
        f"Qwen3.6Turbo proxy listening on {args.listen}:{args.port} -> {ProxyHandler.upstream}, reasoning_stream_format={ProxyHandler.reasoning_stream_format}, searxng_base={ProxyHandler.searxng_base_url}",
        flush=True,
    )
    server.serve_forever()

if __name__ == "__main__":
    main()
