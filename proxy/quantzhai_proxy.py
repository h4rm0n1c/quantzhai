#!/usr/bin/env python3
import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
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
        make_response_stream_events,
        make_sse_block,
        transform_sse_event,
    )
    from .qz_streaming import StreamedFunctionCallAssembler, parse_sse_event_lines
    from .qz_telemetry import DEFAULT_TELEMETRY
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from .qz_runtime_io import append_capture, capture_path, runtime_log, write_capture
except ImportError:
    from qz_proxy_config import (
        CURRENT_API_ENDPOINTS,
        LEGACY_API_ENDPOINTS,
        LOCAL_CODEX_RATE_LIMITS,
        MODEL_BUDGETS,
        api_contract_payload,
    )
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
        make_response_stream_events,
        make_sse_block,
        transform_sse_event,
    )
    from qz_streaming import StreamedFunctionCallAssembler, parse_sse_event_lines
    from qz_telemetry import DEFAULT_TELEMETRY
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from qz_runtime_io import append_capture, capture_path, runtime_log, write_capture

class ProxyHandler(BaseHTTPRequestHandler):
    upstream = "http://127.0.0.1:18084"
    reasoning_stream_format = "raw"
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
                for key in ("id", "model", "status", "created_at")
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


    def _write_transformed_sse_stream(self, resp, raw_log=None):
        summary_started = set()
        function_calls = StreamedFunctionCallAssembler()
        event_lines = []

        while True:
            chunk = resp.readline()
            if not chunk:
                if event_lines:
                    event_type, payload = parse_sse_event_lines(event_lines)
                    function_calls.observe(event_type, payload)
                    for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                        self._emit_sse_telemetry(out_chunk)
                        self.wfile.write(out_chunk)
                        self.wfile.flush()
                break

            if raw_log is not None:
                raw_log.write(chunk)
                raw_log.flush()

            event_lines.append(chunk)
            if chunk in (b"\n", b"\r\n"):
                event_type, payload = parse_sse_event_lines(event_lines)
                function_calls.observe(event_type, payload)
                for out_chunk in transform_sse_event(event_lines, summary_started, self.reasoning_stream_format):
                    self._emit_sse_telemetry(out_chunk)
                    self.wfile.write(out_chunk)
                    self.wfile.flush()
                event_lines = []


    def _ollama_models(self):
        now = "2026-04-27T00:00:00Z"
        models = []
        for name in MODEL_BUDGETS:
            models.append({
                "name": name,
                "model": name,
                "modified_at": now,
                "size": 1,
                "digest": "local-qwen36turbo",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                }
            })
        return models

    def _handle_ollama_get(self):
        # Codex --oss may probe Ollama-compatible endpoints before using /v1.
        if self.path in ("/api/tags", "/v1/api/tags"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        if self.path in ("/api/version", "/v1/api/version"):
            self._send_json(200, {"version": "0.13.4"})
            return True

        if self.path in ("/api/ps", "/v1/api/ps"):
            self._send_json(200, {"models": self._ollama_models()})
            return True

        return False

    def _handle_ollama_post(self):
        if self.path not in ("/api/pull", "/v1/api/pull", "/api/show", "/v1/api/show"):
            return False

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        model = body.get("model") or body.get("name") or "Qwen3.6Turbo-medium"

        if self.path in ("/api/pull", "/v1/api/pull"):
            # Pretend the model is already installed.
            # Ollama permits non-stream response {"status":"success"} when stream=false;
            # Codex only needs pull to not fail.
            self._send_json(200, {"status": "success"})
            return True

        if self.path in ("/api/show", "/v1/api/show"):
            self._send_json(200, {
                "modelfile": f"FROM {model}\n",
                "parameters": "",
                "template": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": "qwen",
                    "families": ["qwen"],
                    "parameter_size": "35B-A3B",
                    "quantization_level": "Q4_K_M+TurboQuant"
                },
                "model_info": {
                    "general.architecture": "qwen",
                    "general.name": model,
                    "qwen36turbo.context_length": 131072
                },
                "capabilities": ["completion", "tools", "thinking"]
            })
            return True

        return False


    def _log_request_path(self, method):
        if self.path.startswith("/qz/telemetry"):
            return
        self.telemetry.emit("request_started", {
            "method": method,
            "path": self.path,
            "accept": self.headers.get("Accept", ""),
            "content_type": self.headers.get("Content-Type", ""),
        })
        try:
            import time
            append_capture("latest-paths.log", f"{time.time():.3f} {method} {self.path} accept={self.headers.get('Accept','')} content_type={self.headers.get('Content-Type','')}\n")
        except Exception:
            pass

    def do_GET(self):
        self.active_deprecation = None
        self._log_request_path("GET")
        if self._handle_ollama_get():
            return

        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "proxy": "Qwen3.6Turbo",
                "upstream": self.upstream,
                "models": MODEL_BUDGETS,
                "supports": list(CURRENT_API_ENDPOINTS),
                "api_contract": api_contract_payload(),
            })
            return

        if self.path == "/qz/telemetry/state":
            self._send_json(200, self.telemetry.state())
            return

        if self.path.startswith("/qz/telemetry/recent"):
            limit = 100
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                limit = int((params.get("limit") or [limit])[0])
            except Exception:
                limit = 100
            self._send_json(200, {
                "events": self.telemetry.recent(limit),
                "state": self.telemetry.state(),
            })
            return

        if self.path == "/qz/telemetry/events":
            self._send_telemetry_sse()
            return

        if self.path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "local"}
                    for name in MODEL_BUDGETS
                ],
            })
            return

        self._proxy_raw("GET")

    def do_POST(self):
        self.active_deprecation = None
        self._log_request_path("POST")

        if self._handle_ollama_post():
            return

        if self.path in LEGACY_API_ENDPOINTS:
            self._mark_deprecated_endpoint(self.path)
            self._proxy_json_api("/v1/chat/completions")
            return

        if self.path in ("/responses/compact", "/v1/responses/compact"):
            self._handle_responses_compact()
            return

        if self.path in ("/responses", "/v1/responses"):
            self._proxy_json_api("/v1/responses")
            return

        self._proxy_raw("POST")

    def _web_runtime(self):
        return WebSearchRuntime(
            base_url=self.searxng_base_url,
            timeout=self.searxng_timeout,
            policy=self.searxng_policy,
            capabilities=self.searxng_capabilities,
            search_cache=self.web_search_cache,
            opened_page_cache=self.opened_page_cache,
        )

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
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=900) as resp:
            resp_data = resp.read()
            status = resp.status
            content_type = resp.headers.get("Content-Type", "application/json")
        return status, content_type, resp_data

    def _write_sse_chunk(self, chunk: bytes, raw_log=None):
        if raw_log is not None:
            raw_log.write(chunk)
            raw_log.flush()
        self._emit_sse_telemetry(chunk)
        self.wfile.write(chunk)
        self.wfile.flush()

    def _run_responses_streaming_locally(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        runtime = ResponsesStreamRuntime(
            upstream=self.upstream,
            authorization=self.headers.get("Authorization", "Bearer local"),
            reasoning_stream_format=self.reasoning_stream_format,
            web_runtime=self._web_runtime(),
            chunk_writer=self._write_sse_chunk,
        )
        runtime.run(body, requested_model, apply_patch_output_style)

    def _run_responses_locally(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        url = self.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        web_runtime = self._web_runtime()

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            status, content_type, resp_data = self._call_upstream_json(url, working_body)
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model

            output_items = out.get("output") or []
            web_calls = [
                item for item in output_items
                if isinstance(item, dict) and item.get("type") == "function_call" and item.get("name") == "web_search"
            ]

            if not web_calls:
                final_out = dict(out)
                final_out["output"] = public_trace + normalize_apply_patch_output_for_codex(
                    output_items,
                    apply_patch_output_style,
                )
                final_out["usage"] = _normalize_response_usage(final_out.get("usage"))
                self._annotate_output_with_url_citations(final_out, gathered_sources)
                runtime_log("latest-web-runtime-final.json", final_out)
                return status, content_type, final_out

            next_input = list(working_body.get("input") or [])
            next_input.extend(output_items)

            for call in web_calls:
                public_item, tool_output_item, sources = web_runtime.execute_web_search_call(call, counters, seen_signatures)
                public_trace.append(public_item)
                gathered_sources.extend(sources)
                next_input.append(tool_output_item)

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
                    "text": "I stopped the web tool loop after hitting the safety limit for repeated search/open actions.",
                    "annotations": [],
                }],
            }],
            "usage": _normalize_response_usage({}),
        }
        self._annotate_output_with_url_citations(fallback_out, gathered_sources)
        runtime_log("latest-web-runtime-final.json", fallback_out)
        return 200, "application/json", fallback_out



    def _proxy_json_api(self, upstream_path):
        try:
            import time
            append_capture("latest-json-api.log", f"{time.time():.3f} ENTER path={self.path} upstream_path={upstream_path} accept={self.headers.get('Accept','')}\n")
        except Exception:
            pass

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Debug: dump latest request so we can see Codex tool schema.
        try:
            write_capture("latest-request.json", body)
        except Exception:
            pass

        client_wants_stream = (
            body.get("stream") is True
            or "text/event-stream" in self.headers.get("Accept", "")
        )

        requested_model = body.get("model") or "Qwen3.6Turbo-medium"
        budget = MODEL_BUDGETS.get(requested_model, MODEL_BUDGETS["Qwen3.6Turbo"])

        body["model"] = requested_model
        body["thinking_budget_tokens"] = budget
        body.setdefault("temperature", 0.1)

        if upstream_path == "/v1/responses":
            apply_patch_output_style = _apply_patch_output_style(body)
            input_items = body.get("input")
            if isinstance(input_items, list):
                body["input"] = _microcompact_old_tool_results(_expand_local_compaction_items(input_items))
            body = normalize_responses_input_for_qwen(body)
            body = normalize_tools_for_llamacpp(body)
            try:
                write_capture("latest-normalized-request.json", body)
            except Exception:
                pass

            if client_wants_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                try:
                    self._run_responses_streaming_locally(
                        body,
                        requested_model,
                        apply_patch_output_style,
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    try:
                        import traceback
                        runtime_log("latest-stream-runtime-error.txt", traceback.format_exc())
                    except Exception:
                        pass
                    error_payload = {
                        "type": "response.failed",
                        "response": {
                            "id": f"resp_local_{_now_ts()}",
                            "object": "response",
                            "created_at": _now_ts(),
                            "status": "failed",
                            "model": requested_model,
                            "error": {"message": f"local streaming runtime error: {e}"},
                            "output": [],
                            "usage": _normalize_response_usage({}),
                        },
                    }
                    self._write_sse_chunk(make_sse_block("response.failed", error_payload))
                    self._write_sse_chunk(b"data: [DONE]\n\n")
                self.close_connection = True
                return

            try:
                status, content_type, out = self._run_responses_locally(
                    body,
                    requested_model,
                    apply_patch_output_style,
                )
            except urllib.error.HTTPError as e:
                resp_data = e.read()
                try:
                    self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
                except Exception:
                    self.send_response(e.code)
                    self.send_header("Content-Type", "text/plain")
                    self._send_codex_rate_limit_headers()
                    self.end_headers()
                    self.wfile.write(resp_data)
                return
            except Exception as e:
                try:
                    import traceback
                    runtime_log("latest-web-runtime-error.txt", traceback.format_exc())
                except Exception:
                    pass
                self._send_json(502, {"error": f"local web runtime error: {e}"})
                return

            self._send_json(status, out)
            return

        data = json.dumps(body).encode("utf-8")
        url = self.upstream + upstream_path

        try:
            import time
            append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM url={url} bytes={len(data)} stream={body.get('stream')}\n")
        except Exception:
            pass

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.headers.get("Authorization", "Bearer local"),
                "Accept": self.headers.get("Accept", "application/json"),
            },
        )

        try:
            resp = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            try:
                self._send_json(e.code, json.loads(resp_data.decode("utf-8")))
            except Exception:
                self.send_response(e.code)
                self.send_header("Content-Type", "text/plain")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self.wfile.write(resp_data)
            return
        except Exception as e:
            try:
                import time, traceback
                append_capture("latest-json-api.log", f"{time.time():.3f} UPSTREAM_EXCEPTION {type(e).__name__}: {e}\n")
                append_capture("latest-json-api.log", traceback.format_exc() + "\n")
            except Exception:
                pass
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        content_type = resp.headers.get("Content-Type", "application/json")
        status = resp.status

        if upstream_path == "/v1/responses" and client_wants_stream and "text/event-stream" in content_type:
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self._send_codex_rate_limit_headers()
            self.end_headers()
            self._write_codex_rate_limits_event()

            try:
                raw_log_path = capture_path("latest-upstream-response.raw")
                status_path = capture_path("latest-upstream-status.txt")
                status_path.write_text(
                    f"status={status}\ncontent_type={content_type}\nstream=passthrough\nreasoning_stream_format={self.reasoning_stream_format}\nrate_limits=local\n",
                    encoding="utf-8"
                )
                raw_log = raw_log_path.open("wb")
            except Exception:
                raw_log = None

            try:
                self._write_transformed_sse_stream(resp, raw_log)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if raw_log is not None:
                    raw_log.close()
                resp.close()
            self.close_connection = True
            return

        resp_data = resp.read()
        resp.close()

        try:
            write_capture("latest-upstream-response.raw", resp_data, mode="bytes")
            capture_path("latest-upstream-status.txt").write_text(
                f"status={status}\ncontent_type={content_type}\n",
                encoding="utf-8"
            )
        except Exception:
            pass

        try:
            out = json.loads(resp_data.decode("utf-8"))
            out["model"] = requested_model
            if upstream_path == "/v1/responses":
                out["usage"] = _normalize_response_usage(out.get("usage"))
            # Do not recursively clean response text here.
            # Reasoning format conversion belongs in the SSE transform layer.
            # Final output_text must pass through unchanged.

            if upstream_path == "/v1/responses" and client_wants_stream:
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self._send_codex_rate_limit_headers()
                self.end_headers()
                self._write_codex_rate_limits_event()
                for chunk in make_response_stream_events(out):
                    self._emit_sse_telemetry(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return

            self._send_json(status, out)
        except Exception:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._send_codex_rate_limit_headers()
            self.send_header("Content-Length", str(len(resp_data)))
            self.end_headers()
            self.wfile.write(resp_data)

    def _proxy_raw(self, method):
        length = int(self.headers.get("Content-Length", "0") or "0")
        data = self.rfile.read(length) if length else None
        url = self.upstream + self.path

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Content-Type": self.headers.get("Content-Type", "application/json"),
                "Authorization": self.headers.get("Authorization", "Bearer local"),
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                resp_data = resp.read()
                status = resp.status
                content_type = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            resp_data = e.read()
            status = e.code
            content_type = e.headers.get("Content-Type", "application/json")
        except Exception as e:
            self._send_json(502, {"error": f"upstream error: {e}"})
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self._send_codex_rate_limit_headers()
        self.send_header("Content-Length", str(len(resp_data)))
        self.end_headers()
        self.wfile.write(resp_data)

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
    args = ap.parse_args()

    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.reasoning_stream_format = args.reasoning_stream_format

    script_dir = Path(__file__).resolve().parent
    policy_path = Path(args.searxng_policy) if args.searxng_policy else script_dir / "searxng-agent-policy.json"
    capabilities_path = Path(args.searxng_capabilities) if args.searxng_capabilities else script_dir / "searxng-capabilities.json"
    policy = _safe_json_file(policy_path)
    capabilities = _safe_json_file(capabilities_path)

    ProxyHandler.searxng_policy_path = str(policy_path)
    ProxyHandler.searxng_capabilities_path = str(capabilities_path)
    ProxyHandler.searxng_policy = policy
    ProxyHandler.searxng_capabilities = capabilities
    ProxyHandler.searxng_base_url = args.searxng_base_url or policy.get("searxng_base") or capabilities.get("base")
    ProxyHandler.searxng_timeout = args.searxng_timeout

    server = ThreadingHTTPServer((args.listen, args.port), ProxyHandler)
    print(
        f"Qwen3.6Turbo proxy listening on {args.listen}:{args.port} -> {ProxyHandler.upstream}, reasoning_stream_format={ProxyHandler.reasoning_stream_format}, searxng_base={ProxyHandler.searxng_base_url}",
        flush=True,
    )
    server.serve_forever()

if __name__ == "__main__":
    main()
