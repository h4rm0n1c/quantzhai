#!/usr/bin/env python3
import json
import os
import time
import urllib.request

try:
    from .qz_responses import (
        _now_ts,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from .qz_runtime_io import capture_enabled, capture_path, request_capture_path, write_capture, write_request_capture
    from .qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from .qz_streaming import (
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_events,
        rewrite_sse_payload,
    )
    from .qz_tool_lifecycle import StreamToolCallState, completed_tool_call_decision, tool_continuation_result
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS
except ImportError:
    from qz_responses import (
        _now_ts,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from qz_runtime_io import capture_enabled, capture_path, request_capture_path, write_capture, write_request_capture
    from qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from qz_streaming import (
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_events,
        rewrite_sse_payload,
    )
    from qz_tool_lifecycle import StreamToolCallState, completed_tool_call_decision, tool_continuation_result
    from qz_tool_web import WEB_SEARCH_MAX_HOPS


PRIVATE_FUNCTION_CALL_TIMEOUT_S = float(os.environ.get("QZ_PRIVATE_TOOL_CALL_TIMEOUT_S", "120"))
PRIVATE_FUNCTION_CALL_DELTA_LIMIT = int(os.environ.get("QZ_PRIVATE_TOOL_CALL_DELTA_LIMIT", "1200"))
REASONING_ONLY_TIMEOUT_S = float(os.environ.get("QZ_REASONING_ONLY_TIMEOUT_S", "120"))
REASONING_ONLY_CHAR_LIMIT = int(os.environ.get("QZ_REASONING_ONLY_CHAR_LIMIT", "-1"))
REASONING_ARTIFACT_SCAN_LIMIT = int(os.environ.get("QZ_REASONING_ARTIFACT_SCAN_LIMIT", "8192"))


def _looks_like_reasoning_tool_artifact(text: str) -> bool:
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    lower = stripped.lower()
    starts_like_payload = (
        lower.startswith("{")
        or lower.startswith("json")
        or lower.startswith("```json")
        or lower.startswith("```")
    )
    if not starts_like_payload:
        return False

    markers = (
        '"operation"',
        '"path"',
        '"diff"',
        '"type"',
        "apply_patch",
        "update_file",
        "create_file",
        "delete_file",
        "--- a/",
        "+++ b/",
        "@@",
    )
    hits = sum(1 for marker in markers if marker in lower)
    has_patch_shape = (
        ('"operation"' in lower and '"path"' in lower and ('"diff"' in lower or "apply_patch" in lower))
        or (('"diff"' in lower or "@@" in lower) and ("--- a/" in lower or "+++ b/" in lower))
    )
    return has_patch_shape and hits >= 3


class _MultiRawLog:
    def __init__(self, handles):
        self.handles = [handle for handle in handles if handle is not None]

    def write(self, chunk: bytes):
        for handle in self.handles:
            handle.write(chunk)

    def flush(self):
        for handle in self.handles:
            handle.flush()

    def close(self):
        for handle in self.handles:
            handle.close()


class ResponsesStreamRuntime:
    """Runs the local Responses SSE tool-continuation loop."""

    def __init__(
        self,
        upstream: str,
        authorization: str,
        reasoning_stream_format: str,
        web_runtime,
        chunk_writer,
        stream_opener=None,
        capture_enabled: bool = True,
        telemetry=None,
        request_id: str = "",
        private_function_call_timeout_s: float | None = None,
        private_function_call_delta_limit: int | None = None,
        reasoning_only_timeout_s: float | None = None,
        reasoning_only_char_limit: int | None = None,
    ):
        self.upstream = upstream.rstrip("/")
        self.authorization = authorization or "Bearer local"
        self.reasoning_stream_format = reasoning_stream_format
        self.web_runtime = web_runtime
        self.chunk_writer = chunk_writer
        self.stream_opener = stream_opener or self._open_upstream_stream
        self.capture_enabled = capture_enabled
        self.telemetry = telemetry
        self.request_id = request_id or ""
        self.private_function_call_timeout_s = (
            PRIVATE_FUNCTION_CALL_TIMEOUT_S
            if private_function_call_timeout_s is None
            else float(private_function_call_timeout_s)
        )
        self.private_function_call_delta_limit = (
            PRIVATE_FUNCTION_CALL_DELTA_LIMIT
            if private_function_call_delta_limit is None
            else int(private_function_call_delta_limit)
        )
        self.reasoning_only_timeout_s = (
            REASONING_ONLY_TIMEOUT_S
            if reasoning_only_timeout_s is None
            else float(reasoning_only_timeout_s)
        )
        self.reasoning_only_char_limit = (
            REASONING_ONLY_CHAR_LIMIT
            if reasoning_only_char_limit is None
            else int(reasoning_only_char_limit)
        )

    def _open_upstream_stream(self, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.upstream + "/v1/responses",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.authorization,
                "Accept": "text/event-stream",
            },
        )
        return urllib.request.urlopen(req, timeout=900)

    def _write_chunk(self, chunk: bytes):
        self.chunk_writer(chunk)

    def _emit(self, event_type: str, payload: dict | None = None):
        if not self.telemetry:
            return
        try:
            event_payload = dict(payload) if isinstance(payload, dict) else {}
            if self.request_id and not event_payload.get("request_id"):
                event_payload["request_id"] = self.request_id
            self.telemetry.emit(event_type, event_payload)
        except Exception:
            pass

    def _emit_stream_event_timing(
        self,
        event_type: str,
        received_at: float,
        parsed_at: float,
        forwarded_at: float | None,
        *,
        forwarded_chunks: int = 0,
        forwarded_bytes: int = 0,
        suppressed: str = "",
    ):
        emitted_at = time.time()
        payload = {
            "event_type": event_type or "event",
            "received_to_parsed_ms": round(max(0.0, parsed_at - received_at) * 1000.0, 3),
            "parsed_to_forwarded_ms": None,
            "received_to_telemetry_ms": round(max(0.0, emitted_at - received_at) * 1000.0, 3),
            "forwarded_chunks": int(forwarded_chunks),
            "forwarded_bytes": int(forwarded_bytes),
        }
        if forwarded_at is not None:
            payload["parsed_to_forwarded_ms"] = round(max(0.0, forwarded_at - parsed_at) * 1000.0, 3)
        if suppressed:
            payload["suppressed"] = suppressed
        self._emit("stream_event_timing", payload)

    def _write_transformed_chunks(self, chunks):
        forwarded_chunks = 0
        forwarded_bytes = 0
        for out_chunk in chunks:
            forwarded_chunks += 1
            forwarded_bytes += len(out_chunk)
            self._write_chunk(out_chunk)
        return forwarded_chunks, forwarded_bytes

    def _transformed_chunks(
        self,
        event_type,
        payload,
        event_lines,
        summary_started,
        output_index_offset=0,
        prepend_output=None,
        model=None,
    ):
        if isinstance(payload, dict):
            event_type, payload = rewrite_sse_payload(
                event_type,
                payload,
                output_index_offset=output_index_offset,
                prepend_output=prepend_output,
                model=model,
            )
            return transform_sse_event(
                [make_sse_block(event_type, payload)],
                summary_started,
                self.reasoning_stream_format,
            )
        return transform_sse_event(event_lines, summary_started, self.reasoning_stream_format)

    def _start_capture(self):
        if not self.capture_enabled or not capture_enabled():
            return
        try:
            write_capture("latest-upstream-response.raw", b"", mode="bytes")
            capture_path("latest-upstream-status.txt").write_text(
                "status=streaming\n"
                "content_type=text/event-stream\n"
                "stream=real\n"
                f"reasoning_stream_format={self.reasoning_stream_format}\n"
                "rate_limits=local\n",
                encoding="utf-8",
            )
            if self.request_id:
                write_request_capture(self.request_id, "upstream-response.raw", b"", mode="bytes")
                write_request_capture(
                    self.request_id,
                    "upstream-status.txt",
                    "status=streaming\n"
                    "content_type=text/event-stream\n"
                    "stream=real\n"
                    f"reasoning_stream_format={self.reasoning_stream_format}\n"
                    "rate_limits=local\n",
                )
        except Exception:
            pass

    def _open_raw_log(self):
        if not self.capture_enabled or not capture_enabled():
            return None
        try:
            handles = [capture_path("latest-upstream-response.raw").open("ab")]
            if self.request_id:
                request_capture_path(self.request_id, "upstream-response.raw").parent.mkdir(parents=True, exist_ok=True)
                handles.append(request_capture_path(self.request_id, "upstream-response.raw").open("ab"))
            return _MultiRawLog(handles)
        except Exception:
            return None

    def _emit_public_tool_item(self, item: dict, public_index: int, sequence: int):
        chunks, sequence = public_tool_item_events(item, public_index, sequence)
        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(chunks)
        return sequence, forwarded_chunks, forwarded_bytes

    def _emit_completed(self, requested_model: str, output: list, summary_started: set, usage=None):
        completed_payload = {
            "type": "response.completed",
            "response": {
                "id": f"resp_local_{_now_ts()}",
                "object": "response",
                "created_at": _now_ts(),
                "status": "completed",
                "model": requested_model,
                "output": output,
                "usage": _normalize_response_usage(usage),
            },
        }
        for out_chunk in transform_sse_event(
            [make_sse_block("response.completed", completed_payload)],
            summary_started,
            self.reasoning_stream_format,
        ):
            self._write_chunk(out_chunk)
        self._write_chunk(b"data: [DONE]\n\n")

    def _emit_private_tool_call_aborted(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        reason: str,
        call_name: str = "",
    ):
        text = (
            "I stopped a private tool-call loop before it could stall the stream. "
            "No file was changed. Please retry with normal text feedback, or name "
            "an explicit output path if you want a file written."
        )
        output = [{
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": text,
                "annotations": [],
            }],
        }]
        self._emit("private_tool_call_aborted", {
            "model": requested_model,
            "reason": reason,
            "tool_name": call_name or "",
        })
        self._emit_completed(requested_model, output, summary_started, usage=final_usage)
        return output

    def _emit_reasoning_only_aborted(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        reason: str,
        reasoning_chars: int,
    ):
        if reason == "artifact_tool_payload":
            text = (
                "I stopped the stream because the model started writing a tool or patch payload "
                "inside the reasoning channel instead of emitting a real tool call. No file was "
                "changed. Please retry the edit so it can be sent as an explicit tool call."
            )
        else:
            text = (
                "I stopped a reasoning-only stream before it could stall the client. "
                "No file was changed. Retry with a narrower request, or ask for a "
                "normal final answer instead of continued internal drafting."
            )
        output = [{
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": text,
                "annotations": [],
            }],
        }]
        self._emit("reasoning_only_aborted", {
            "model": requested_model,
            "reason": reason,
            "reasoning_chars": int(reasoning_chars),
        })
        self._emit_completed(requested_model, output, summary_started, usage=final_usage)
        return output

    def _emit_stream_completed(self, requested_model: str, output_items: int, started_at: float, fallback: bool = False):
        self._emit("stream_completed", {
            "model": requested_model,
            "output_items": output_items,
            "duration_ms": round((time.time() - started_at) * 1000.0, 2),
            "fallback": bool(fallback),
        })

    def run(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = True

        started_at = time.time()
        first_output_at = None
        completed_at = None
        final_usage = _normalize_response_usage({})
        public_trace = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        summary_started = set()
        output_index_offset = 0
        sequence = 0
        sent_response_start = False
        sent_terminal = False
        sent_done = False
        self._start_capture()
        self._emit("stream_started", {
            "model": requested_model,
            "apply_patch_output_style": apply_patch_output_style,
            "tool_hops_max": WEB_SEARCH_MAX_HOPS,
        })

        try:
            for _hop in range(WEB_SEARCH_MAX_HOPS):
                hop_body = json.loads(json.dumps(working_body))
                hop_body["stream"] = True
                hop_body = normalize_responses_input_for_qwen(hop_body)
                hop_body = normalize_tools_for_llamacpp(hop_body)
                resp = None
                raw_log = None
                try:
                    resp = self.stream_opener(hop_body)
                    raw_log = self._open_raw_log()
                    tool_call_state = StreamToolCallState()
                    event_lines = []
                    event_started_at = None
                    next_input = list(hop_body.get("input") or [])
                    completed_call = None
                    reasoning_only_started_at = None
                    reasoning_only_last_delta_at = None
                    reasoning_only_chars = 0
                    reasoning_only_sample = ""
                    output_text_seen = False
                    public_item_seen = False
                    max_output_index = -1

                    while True:
                        chunk = resp.readline()
                        if not chunk:
                            break

                        if raw_log is not None:
                            raw_log.write(chunk)
                            raw_log.flush()

                        if event_started_at is None:
                            event_started_at = time.time()
                        event_lines.append(chunk)
                        if chunk not in (b"\n", b"\r\n"):
                            continue

                        event_received_at = event_started_at or time.time()
                        event_type, payload = parse_sse_event_lines(event_lines)
                        event_parsed_at = time.time()
                        event_started_at = None
                        if first_output_at is None and event_type not in {"response.created", "response.in_progress"}:
                            first_output_at = time.time()
                        if isinstance(payload, dict) and isinstance(payload.get("output_index"), int):
                            max_output_index = max(max_output_index, payload["output_index"])
                        if isinstance(payload, dict) and isinstance(payload.get("sequence_number"), int):
                            sequence = max(sequence, payload["sequence_number"])
                        if event_type == "response.output_text.delta":
                            output_text_seen = True
                        if event_type in {
                            "response.output_item.added",
                            "response.output_item.done",
                        } and isinstance(payload, dict):
                            item = payload.get("item")
                            if isinstance(item, dict) and item.get("type") not in {"reasoning"}:
                                public_item_seen = True
                        if event_type == "response.completed":
                            response = payload.get("response") if isinstance(payload, dict) else {}
                            if isinstance(response, dict):
                                final_usage = _normalize_response_usage(response.get("usage"))

                        completed = tool_call_state.observe(event_type, payload, event_received_at)
                        if is_function_call_stream_event(event_type, payload):
                            # Do not expose executable tool calls until arguments are complete.
                            # Codex currently treats response.output_item.added for function_call
                            # as runnable even when arguments are still streaming, which can execute
                            # an empty-argument command before response.function_call_arguments.done.
                            abort_reason = tool_call_state.abort_reason(
                                time.time(),
                                self.private_function_call_timeout_s,
                                self.private_function_call_delta_limit,
                            )
                            if abort_reason:
                                forwarded_chunks = 0
                                forwarded_bytes = 0
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    time.time() if forwarded_chunks else None,
                                    forwarded_chunks=forwarded_chunks,
                                    forwarded_bytes=forwarded_bytes,
                                    suppressed="function_call_aborted",
                                )
                                public_trace.extend(self._emit_private_tool_call_aborted(
                                    requested_model,
                                    summary_started,
                                    final_usage,
                                    abort_reason,
                                    tool_call_state.call_name,
                                ))
                                self._emit_stream_completed(requested_model, len(public_trace), started_at, fallback=True)
                                completed_at = time.time()
                                return self._build_result(
                                    requested_model,
                                    started_at,
                                    first_output_at,
                                    completed_at,
                                    final_usage,
                                    len(public_trace),
                                )
                        if (
                            event_type in {
                                "response.reasoning_text.delta",
                                "response.reasoning_summary_text.delta",
                            }
                            and isinstance(payload, dict)
                            and not output_text_seen
                            and not public_item_seen
                        ):
                            if reasoning_only_started_at is None:
                                reasoning_only_started_at = event_received_at
                            delta_text = str(payload.get("delta") or "")
                            if delta_text:
                                reasoning_only_last_delta_at = event_received_at
                                if len(reasoning_only_sample) < REASONING_ARTIFACT_SCAN_LIMIT:
                                    remaining = REASONING_ARTIFACT_SCAN_LIMIT - len(reasoning_only_sample)
                                    reasoning_only_sample += delta_text[:remaining]
                            reasoning_only_chars += len(delta_text)
                            reasoning_only_progress_at = reasoning_only_last_delta_at or reasoning_only_started_at
                            reasoning_only_idle = max(0.0, time.time() - reasoning_only_progress_at)
                            abort_reason = ""
                            if _looks_like_reasoning_tool_artifact(reasoning_only_sample):
                                abort_reason = "artifact_tool_payload"
                            elif (
                                self.reasoning_only_timeout_s >= 0
                                and reasoning_only_idle > self.reasoning_only_timeout_s
                            ):
                                abort_reason = "timeout"
                            elif (
                                self.reasoning_only_char_limit >= 0
                                and reasoning_only_chars > self.reasoning_only_char_limit
                            ):
                                abort_reason = "char_limit"
                            if abort_reason:
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed=(
                                        "reasoning_artifact_aborted"
                                        if abort_reason == "artifact_tool_payload"
                                        else "reasoning_only_aborted"
                                    ),
                                )
                                public_trace.extend(self._emit_reasoning_only_aborted(
                                    requested_model,
                                    summary_started,
                                    final_usage,
                                    abort_reason,
                                    reasoning_only_chars,
                                ))
                                self._emit_stream_completed(requested_model, len(public_trace), started_at, fallback=True)
                                completed_at = time.time()
                                return self._build_result(
                                    requested_model,
                                    started_at,
                                    first_output_at,
                                    completed_at,
                                    final_usage,
                                    len(public_trace),
                                    fallback=True,
                                )
                        if completed:
                            completed_call = completed[0]
                            completed_key = completed_call.get("id") or completed_call.get("call_id")
                            public_index = completed_call.get("output_index")
                            if not isinstance(public_index, int):
                                public_index = max_output_index + 1
                            public_index += output_index_offset

                            decision = completed_tool_call_decision(
                                completed_call,
                                apply_patch_output_style,
                            )
                            result = tool_continuation_result(
                                decision,
                                proxy_local_executor=lambda call: self.web_runtime.execute_web_search_call(
                                    call,
                                    counters,
                                    seen_signatures,
                                ),
                            )
                            public_item = result.public_item

                            if decision.kind == "proxy_local":
                                public_trace.append(public_item)
                                next_input.extend(result.upstream_items)
                                sequence, forwarded_chunks, forwarded_bytes = self._emit_public_tool_item(public_item, public_index, sequence)
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    time.time(),
                                    forwarded_chunks=forwarded_chunks,
                                    forwarded_bytes=forwarded_bytes,
                                    suppressed="function_call_private",
                                )
                                break

                            public_trace.append(public_item)
                            sequence, forwarded_chunks, forwarded_bytes = self._emit_public_tool_item(public_item, public_index, sequence)
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                time.time(),
                                forwarded_chunks=forwarded_chunks,
                                forwarded_bytes=forwarded_bytes,
                                suppressed="function_call_private",
                            )
                            self._emit_stream_completed(requested_model, len(public_trace), started_at)
                            completed_at = time.time()
                            self._emit_completed(requested_model, public_trace, summary_started, usage=final_usage)
                            return self._build_result(
                                requested_model,
                                started_at,
                                first_output_at,
                                completed_at,
                                final_usage,
                                len(public_trace),
                            )

                        if is_function_call_stream_event(event_type, payload):
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                None,
                                suppressed="function_call",
                            )
                            event_lines = []
                            continue

                        if is_terminal_stream_event(event_type, payload) and completed_call and completed_call.get("name") == "web_search":
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                None,
                                suppressed="web_search_terminal",
                            )
                            event_lines = []
                            continue

                        if event_type in {"response.created", "response.in_progress"}:
                            if sent_response_start:
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="duplicate_response_start",
                                )
                                event_lines = []
                                continue
                            sent_response_start = True

                        if is_terminal_stream_event(event_type, payload):
                            forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(self._transformed_chunks(
                                event_type,
                                payload,
                                event_lines,
                                summary_started,
                                output_index_offset=output_index_offset,
                                prepend_output=public_trace,
                                model=requested_model,
                            ))
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                time.time(),
                                forwarded_chunks=forwarded_chunks,
                                forwarded_bytes=forwarded_bytes,
                            )
                            if event_type == "done" or payload == "[DONE]":
                                sent_done = True
                            else:
                                sent_terminal = True
                            event_lines = []
                            continue

                        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(self._transformed_chunks(
                            event_type,
                            payload,
                            event_lines,
                            summary_started,
                            output_index_offset=output_index_offset,
                            model=requested_model,
                        ))
                        self._emit_stream_event_timing(
                            event_type,
                            event_received_at,
                            event_parsed_at,
                            time.time(),
                            forwarded_chunks=forwarded_chunks,
                            forwarded_bytes=forwarded_bytes,
                        )
                        event_lines = []
                finally:
                    if raw_log is not None:
                        raw_log.close()
                    if resp is not None:
                        resp.close()

                if completed_call and completed_call.get("name") == "web_search":
                    if max_output_index >= 0:
                        output_index_offset += max_output_index + 1
                    working_body["input"] = next_input
                    continue

                if sent_terminal and not sent_done:
                    self._write_chunk(b"data: [DONE]\n\n")
                    sent_done = True

                completed_at = time.time()
                return self._build_result(
                    requested_model,
                    started_at,
                    first_output_at,
                    completed_at,
                    final_usage,
                    len(public_trace),
                )
        except Exception as exc:
            self._emit("stream_failed", {
                "model": requested_model,
                "error": str(exc),
                "duration_ms": round((time.time() - started_at) * 1000.0, 2),
            })
            raise

        completed_at = time.time()
        fallback_output = public_trace + [{
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{
                "type": "output_text",
                "text": "I stopped the web tool loop after hitting the safety limit for repeated search/open actions.",
                "annotations": [],
            }],
        }]
        self._emit_stream_completed(requested_model, len(fallback_output), started_at, fallback=True)
        self._emit_completed(requested_model, fallback_output, summary_started, usage=final_usage)
        return self._build_result(
            requested_model,
            started_at,
            first_output_at,
            completed_at,
            final_usage,
            len(fallback_output),
            fallback=True,
        )

    def _build_result(
        self,
        requested_model: str,
        started_at: float,
        first_output_at: float | None,
        completed_at: float,
        usage: dict,
        output_items: int,
        fallback: bool = False,
    ) -> dict:
        prompt_ms = 0.0
        gen_ms = 0.0
        if isinstance(first_output_at, (int, float)) and first_output_at >= started_at:
            prompt_ms = max(0.0, (first_output_at - started_at) * 1000.0)
            gen_ms = max(0.0, (completed_at - first_output_at) * 1000.0)
        return {
            "model": requested_model,
            "usage": _normalize_response_usage(usage),
            "started_at": started_at,
            "first_output_at": first_output_at,
            "completed_at": completed_at,
            "elapsed_ms": max(0.0, (completed_at - started_at) * 1000.0),
            "prompt_ms": prompt_ms,
            "gen_ms": gen_ms,
            "output_items": output_items,
            "fallback": bool(fallback),
        }
