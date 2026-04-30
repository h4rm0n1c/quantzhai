#!/usr/bin/env python3
import json
import time
from contextlib import contextmanager

try:
    from .qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, MODEL_BUDGETS, api_contract_payload
    from .qz_responses import (
        _apply_patch_output_style,
        _build_local_compaction_response,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _now_ts,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from .qz_responses_stream import ResponsesStreamRuntime
    from .qz_sse import _normalize_response_usage, make_sse_block
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from .qz_runtime_io import append_capture, capture_enabled, capture_path, runtime_log, write_capture
except ImportError:
    from qz_proxy_config import CURRENT_API_ENDPOINTS, LEGACY_API_ENDPOINTS, MODEL_BUDGETS, api_contract_payload
    from qz_responses import (
        _apply_patch_output_style,
        _build_local_compaction_response,
        _decode_local_compaction_blob,
        _expand_local_compaction_items,
        _microcompact_old_tool_results,
        _now_ts,
        clean_content,
        extract_response_output_text,
        normalize_apply_patch_output_for_codex,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from qz_responses_stream import ResponsesStreamRuntime
    from qz_sse import _normalize_response_usage, make_sse_block
    from qz_tool_web import WEB_SEARCH_MAX_HOPS, WebSearchRuntime, _safe_json_file, _unique_sources
    from qz_runtime_io import append_capture, capture_enabled, capture_path, runtime_log, write_capture


class RequestRouter:
    def __init__(self, handler):
        self.handler = handler

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
            self.handler._send_json(200, {
                "status": "ok",
                "proxy": "Qwen3.6Turbo",
                "upstream": self.handler.upstream,
                "models": MODEL_BUDGETS,
                "catalog": self.handler._model_catalog_payload(),
                "supports": list(CURRENT_API_ENDPOINTS),
                "api_contract": api_contract_payload(),
            })
            return

        if self.handler.path == "/qz/telemetry/state":
            state = self.handler.telemetry.state()
            try:
                state["runtime"] = self.handler._model_router().status_summary(self.handler.path)
            except Exception:
                state["runtime"] = {}
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
            self.handler._send_json(200, {
                "events": self.handler.telemetry.recent(limit),
                "state": self.handler.telemetry.state(),
            })
            return

        if self.handler.path == "/qz/telemetry/events":
            self.handler._send_telemetry_sse()
            return

        if self.handler.path == "/v1/models":
            self.handler._send_json(200, self.handler._model_catalog_payload())
            return

        if self.handler.path == "/qz/models":
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
            catalog = self.handler._model_catalog()
            catalog.refresh()
            self.handler._send_json(200, {
                "catalog": catalog.to_payload(),
                "backend": self.handler._backend_models(),
            })
            return

        if self.handler.path in ("/qz/models/load", "/qz/models/select"):
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
                self.handler._send_json(404, {"error": "no model selected", "reason": reason, "catalog": catalog.to_payload()})
                return
            self.handler._send_json(200, {
                "selected": selected,
                "reason": reason,
                "backend": self.handler._backend_models(),
                "catalog": catalog.to_payload(),
            })
            return

        self.proxy_raw("POST")

    def _web_runtime(self):
        return WebSearchRuntime(
            base_url=self.handler.searxng_base_url,
            timeout=self.handler.searxng_timeout,
            policy=self.handler.searxng_policy,
            capabilities=self.handler.searxng_capabilities,
            search_cache=self.handler.web_search_cache,
            opened_page_cache=self.handler.opened_page_cache,
            telemetry=self.handler.telemetry,
        )

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
            "ready": bool(snapshot.get("ready")) if isinstance(snapshot, dict) else False,
            "load_state": load.get("state") or "unknown",
            "selected_model": selected_model or selected.get("label") or selected.get("slug") or selected.get("key") or "",
            "selected_key": backend.get("selected_key") or "",
            "selected_backend_id": backend.get("selected_backend_id") or "",
            "selected_context_length": backend.get("selected_context_length"),
            "backend_context_length": backend.get("backend_context_length"),
            "restart_required": bool(backend.get("restart_required")),
            "reasoning_level": backend.get("selected_reasoning_level") or "medium",
            "reasoning_policy": backend.get("selected_reasoning_policy") or "prompt",
            "thinking_budget_tokens": backend.get("selected_thinking_budget_tokens"),
            "sampling": backend.get("selected_sampling_params") or {},
        }

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

    def _emit_throughput_sample(self, payload: dict):
        if not isinstance(payload, dict):
            return

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        try:
            prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        except Exception:
            prompt_tokens = 0
        try:
            gen_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
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

    def _write_sse_chunk(self, chunk: bytes, raw_log=None):
        if raw_log is not None:
            raw_log.write(chunk)
            raw_log.flush()
        self.handler._emit_sse_telemetry(chunk)
        self.handler.wfile.write(chunk)
        self.handler.wfile.flush()

    def _run_responses_streaming_locally(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        runtime = ResponsesStreamRuntime(
            upstream=self.handler.upstream,
            authorization=self.handler.headers.get("Authorization", "Bearer local"),
            reasoning_stream_format=self.handler.reasoning_stream_format,
            web_runtime=self._web_runtime(),
            chunk_writer=self._write_sse_chunk,
            telemetry=self.handler.telemetry,
        )
        return runtime.run(body, requested_model, apply_patch_output_style)

    def _run_responses_locally(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        url = self.handler.upstream + "/v1/responses"
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = False

        public_trace = []
        gathered_sources = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        web_runtime = self._web_runtime()

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            hop_body = json.loads(json.dumps(working_body))
            hop_body["stream"] = False
            hop_body = normalize_responses_input_for_qwen(hop_body)
            hop_body = normalize_tools_for_llamacpp(hop_body)
            status, content_type, resp_data = self._call_upstream_json(url, hop_body)
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

            next_input = list(hop_body.get("input") or [])
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

    def proxy_json_api(self, upstream_path):
        started_at = time.time()
        try:
            append_capture("latest-json-api.log", f"{time.time():.3f} ENTER path={self.handler.path} upstream_path={upstream_path} accept={self.handler.headers.get('Accept','')}\n")
        except Exception:
            pass

        length = int(self.handler.headers.get("Content-Length", "0") or "0")
        raw = self.handler.rfile.read(length)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._emit_request_telemetry("request_failed", started_at, upstream_path, "", error=f"invalid JSON: {e}", phase="parse")
            self.handler._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        try:
            write_capture("latest-request.json", body)
        except Exception:
            pass

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

        client_model = body.get("model") or "Qwen3.6Turbo-medium"
        with self._request_gate(upstream_path, client_model, client_wants_stream):
            selected_model, selection_reason = self.handler._resolve_model_selection(client_model)
            if selected_model is None:
                self._emit_request_telemetry("request_failed", started_at, upstream_path, client_model, error=selection_reason or "no model available", phase="select_model")
                self.handler._send_json(503, {
                    "error": "no model available",
                    "reason": selection_reason,
                    "catalog": self.handler._model_catalog().to_payload(),
                })
                return

            selected_identity = selected_model.get("slug") or selected_model.get("key") or selected_model.get("backend_id") or ""

            backend_model = selected_model.get("backend_id") or selected_identity or client_model
            runtime_metrics = self._runtime_metrics(client_model)
            upstream_instructions = body.get("instructions")
            upstream_instructions_present = isinstance(upstream_instructions, str) and bool(upstream_instructions.strip())
            metadata = body.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["qz_upstream_instructions_present"] = upstream_instructions_present
            body["metadata"] = metadata
            body["model"] = backend_model
            body = self.handler._model_router().apply_reasoning_policy(body, selected_model)

            if upstream_path == "/v1/responses":
                body = self.handler._model_router().inject_runtime_state(body, client_model)
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
                    self.handler.send_response(200)
                    self.handler.send_header("Content-Type", "text/event-stream")
                    self.handler.send_header("Cache-Control", "no-cache")
                    self.handler.send_header("Connection", "close")
                    self.handler._send_codex_rate_limit_headers()
                    self.handler.end_headers()
                    self.handler._write_codex_rate_limits_event()
                    stream_result = None
                    try:
                        stream_result = self._run_responses_streaming_locally(
                            body,
                            client_model,
                            apply_patch_output_style,
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
                if capture_enabled():
                    raw_log_path = capture_path("latest-upstream-response.raw")
                    status_path = capture_path("latest-upstream-status.txt")
                    status_path.write_text(
                        f"status={status}\ncontent_type={content_type}\nstream=passthrough\nreasoning_stream_format={self.handler.reasoning_stream_format}\nrate_limits=local\n",
                        encoding="utf-8"
                    )
                    raw_log = raw_log_path.open("wb")
                else:
                    raw_log = None
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
            )
            self.handler.close_connection = True
            return

        resp_data = resp.data

        if capture_enabled():
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
