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
    from .qz_runtime_io import capture_enabled, open_dual_capture_append, write_dual_capture
    from .qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from .qz_streaming import (
        _sse_block_with_sequence,
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_done_event,
        public_tool_item_events,
        public_tool_item_started_event,
        rewrite_sse_payload,
    )
    from .qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from .qz_tool_lifecycle import StreamToolCallState
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS
    from .qz_telemetry import RequestTelemetryEmitter
except ImportError:
    from qz_responses import (
        _now_ts,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from qz_runtime_io import capture_enabled, open_dual_capture_append, write_dual_capture
    from qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from qz_streaming import (
        _sse_block_with_sequence,
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_done_event,
        public_tool_item_events,
        public_tool_item_started_event,
        rewrite_sse_payload,
    )
    from qz_proxy_tools import ProxyToolExecutionContext, make_proxy_local_tool_registry
    from qz_tool_lifecycle import StreamToolCallState
    from qz_tool_web import WEB_SEARCH_MAX_HOPS
    from qz_telemetry import RequestTelemetryEmitter


PRIVATE_FUNCTION_CALL_TIMEOUT_S = float(os.environ.get("QZ_PRIVATE_TOOL_CALL_TIMEOUT_S", "120"))
PRIVATE_FUNCTION_CALL_DELTA_LIMIT = int(os.environ.get("QZ_PRIVATE_TOOL_CALL_DELTA_LIMIT", "1200"))
REASONING_ONLY_TIMEOUT_S = float(os.environ.get("QZ_REASONING_ONLY_TIMEOUT_S", "120"))
REASONING_ONLY_CHAR_LIMIT = int(os.environ.get("QZ_REASONING_ONLY_CHAR_LIMIT", "-1"))
REASONING_ARTIFACT_SCAN_LIMIT = int(os.environ.get("QZ_REASONING_ARTIFACT_SCAN_LIMIT", "8192"))

# Hop budget signal: inject a plain-instruction message when remaining continuation
# hops fall to this threshold or below. Set to -1 to disable entirely.
HOP_BUDGET_SIGNAL_THRESHOLD = int(os.environ.get("QZ_HOP_BUDGET_SIGNAL_THRESHOLD", "3"))

# Context pressure signal: inject when input token fill ratio meets this fraction
# of the configured context window. Set to a negative value to disable.
CONTEXT_PRESSURE_SIGNAL_THRESHOLD = float(os.environ.get("QZ_CONTEXT_PRESSURE_SIGNAL_THRESHOLD", "0.8"))


class ClientStreamDisconnected(BrokenPipeError):
    """Raised when the downstream client closes while streaming."""


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
        proxy_tool_registry=None,
        selected_model=None,
        reasoning_carry_forward: bool = False,
        hop_budget_signal_threshold: int | None = None,
        context_pressure_signal_threshold: float | None = None,
    ):
        self.upstream = upstream.rstrip("/")
        self.authorization = authorization or "Bearer local"
        self.reasoning_stream_format = reasoning_stream_format
        self.chunk_writer = chunk_writer
        self.stream_opener = stream_opener or self._open_upstream_stream
        self.capture_enabled = capture_enabled
        self.telemetry = telemetry
        self.request_id = request_id or ""
        self.selected_model = selected_model if isinstance(selected_model, dict) else None
        self.telemetry_emitter = RequestTelemetryEmitter(telemetry, self.request_id)
        self.proxy_tool_registry = proxy_tool_registry or make_proxy_local_tool_registry(web_runtime)
        self.reasoning_carry_forward = bool(reasoning_carry_forward)
        self.hop_budget_signal_threshold = (
            HOP_BUDGET_SIGNAL_THRESHOLD
            if hop_budget_signal_threshold is None
            else int(hop_budget_signal_threshold)
        )
        self.context_pressure_signal_threshold = (
            CONTEXT_PRESSURE_SIGNAL_THRESHOLD
            if context_pressure_signal_threshold is None
            else float(context_pressure_signal_threshold)
        )
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
        try:
            self.chunk_writer(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise ClientStreamDisconnected(str(exc) or exc.__class__.__name__) from exc

    def _emit(self, event_type: str, payload: dict | None = None):
        self.telemetry_emitter.emit(event_type, payload)

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
        self.telemetry_emitter.emit_stream_event_timing(
            event_type,
            received_at,
            parsed_at,
            forwarded_at,
            forwarded_chunks=forwarded_chunks,
            forwarded_bytes=forwarded_bytes,
            suppressed=suppressed,
        )

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
            write_dual_capture("latest-upstream-response.raw", self.request_id, "upstream-response.raw", b"", mode="bytes")
            write_dual_capture(
                "latest-upstream-status.txt",
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
            handles = open_dual_capture_append(
                "latest-upstream-response.raw",
                request_id=self.request_id,
                request_name="upstream-response.raw",
                binary=True,
            )
            return _MultiRawLog(handles)
        except Exception:
            return None

    def _emit_public_tool_item(self, item: dict, public_index: int, sequence: int):
        chunks, sequence = public_tool_item_events(item, public_index, sequence)
        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(chunks)
        return sequence, forwarded_chunks, forwarded_bytes

    def _emit_proxy_local_started(self, call: dict, public_index: int, sequence: int):
        public_item = self.proxy_tool_registry.started_public_item(call, public_index)
        item_id = public_item["id"]
        chunks, sequence = public_tool_item_started_event(public_item, public_index, sequence)
        stage_chunks, sequence = self.proxy_tool_registry.lifecycle_start_event_chunks(
            call,
            item_id,
            public_index,
            sequence,
        )
        chunks.extend(stage_chunks)
        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(chunks)
        return sequence, forwarded_chunks, forwarded_bytes, item_id

    def _emit_proxy_local_completed(self, call: dict, item: dict, public_index: int, sequence: int, item_id: str):
        item = dict(item)
        item["id"] = item_id
        completed_chunks, sequence = self.proxy_tool_registry.lifecycle_done_event_chunks(
            call,
            item_id,
            public_index,
            sequence,
        )
        chunks, sequence = public_tool_item_done_event(item, public_index, sequence)
        chunks = completed_chunks + chunks
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

    def _stream_fallback_message(self, item: dict, public_index: int, sequence: int) -> int:
        """Stream a pre-built message item as proper SSE events before response.completed.

        Emits the full incremental sequence expected by Codex for a message output item:
        output_item.added → content_part.added → output_text.delta → output_text.done
        → content_part.done → output_item.done.

        Returns the updated sequence number.
        """
        item_id = item.get("id") or f"msg_local_fallback_{public_index}"
        role = item.get("role", "assistant")
        content = item.get("content") or []
        text = (content[0].get("text") or "") if content and isinstance(content[0], dict) else ""

        sequence += 1
        self._write_chunk(_sse_block_with_sequence("response.output_item.added", {
            "output_index": public_index,
            "item": {"id": item_id, "type": "message", "status": "in_progress",
                     "role": role, "content": []},
        }, sequence))

        sequence += 1
        self._write_chunk(_sse_block_with_sequence("response.content_part.added", {
            "item_id": item_id, "output_index": public_index, "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        }, sequence))

        if text:
            sequence += 1
            self._write_chunk(_sse_block_with_sequence("response.output_text.delta", {
                "item_id": item_id, "output_index": public_index, "content_index": 0,
                "delta": text,
            }, sequence))

        sequence += 1
        self._write_chunk(_sse_block_with_sequence("response.output_text.done", {
            "item_id": item_id, "output_index": public_index, "content_index": 0,
            "text": text, "logprobs": [],
        }, sequence))

        sequence += 1
        self._write_chunk(_sse_block_with_sequence("response.content_part.done", {
            "item_id": item_id, "output_index": public_index, "content_index": 0,
            "part": {"type": "output_text", "text": text, "annotations": []},
        }, sequence))

        done_item = dict(item)
        done_item["id"] = item_id
        done_item["status"] = "completed"
        sequence += 1
        self._write_chunk(_sse_block_with_sequence("response.output_item.done", {
            "output_index": public_index,
            "item": done_item,
        }, sequence))

        return sequence

    def _emit_private_tool_call_aborted(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        reason: str,
        call_name: str = "",
        public_index: int = 0,
        sequence: int = 0,
    ) -> tuple:
        text = (
            "I stopped a private tool-call loop before it could stall the stream. "
            "No file was changed. Please retry with normal text feedback, or name "
            "an explicit output path if you want a file written."
        )
        output_item = {
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        self._emit("private_tool_call_aborted", {
            "model": requested_model,
            "reason": reason,
            "tool_name": call_name or "",
        })
        sequence = self._stream_fallback_message(output_item, public_index, sequence)
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage)
        return [output_item], sequence

    def _emit_reasoning_only_aborted(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        reason: str,
        reasoning_chars: int,
        public_index: int = 0,
        sequence: int = 0,
    ) -> tuple:
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
        output_item = {
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        self._emit("reasoning_only_aborted", {
            "model": requested_model,
            "reason": reason,
            "reasoning_chars": int(reasoning_chars),
        })
        sequence = self._stream_fallback_message(output_item, public_index, sequence)
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage)
        return [output_item], sequence

    def _emit_stream_completed(self, requested_model: str, output_items: int, started_at: float, fallback: bool = False):
        self._emit("stream_completed", {
            "model": requested_model,
            "output_items": output_items,
            "duration_ms": round((time.time() - started_at) * 1000.0, 2),
            "fallback": bool(fallback),
        })

    @staticmethod
    def _drain_stream_for_usage(resp) -> dict | None:
        """Read remaining SSE events from resp (still open after a tool break) to
        capture a response.completed usage payload before the stream is closed.

        Bounded to 200 events so a runaway upstream cannot stall the proxy.
        Returns a normalized usage dict if found, None otherwise.
        """
        event_lines = []
        for _ in range(200):
            try:
                chunk = resp.readline()
            except Exception:
                break
            if not chunk:
                break
            event_lines.append(chunk)
            if chunk not in (b"\n", b"\r\n"):
                continue
            event_type, payload = parse_sse_event_lines(event_lines)
            event_lines = []
            if event_type == "response.completed" and isinstance(payload, dict):
                response = payload.get("response") or {}
                if isinstance(response, dict):
                    return _normalize_response_usage(response.get("usage"))
            if event_type == "done" or payload == "[DONE]":
                break
        return None

    @staticmethod
    def _make_signal_message(text: str) -> dict:
        return {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }

    def _hop_budget_signal_message(self, hops_remaining: int) -> dict | None:
        if self.hop_budget_signal_threshold < 0 or hops_remaining > self.hop_budget_signal_threshold:
            return None
        text = (
            f"You have {hops_remaining} continuation hop(s) remaining this turn. "
            "If the task is complete or nearly complete, give a direct answer rather than calling more tools."
        )
        return self._make_signal_message(text)

    def _context_pressure_signal_message(self, usage: dict) -> dict | None:
        if self.context_pressure_signal_threshold <= 0:
            return None
        context_length = None
        if isinstance(self.selected_model, dict):
            context_length = (
                self.selected_model.get("runtime_context_length")
                or self.selected_model.get("context_length")
            )
        if not isinstance(context_length, int) or context_length <= 0:
            return None
        input_tokens = usage.get("input_tokens") or 0
        if not isinstance(input_tokens, (int, float)) or input_tokens <= 0:
            return None
        fill_ratio = input_tokens / context_length
        if fill_ratio < self.context_pressure_signal_threshold:
            return None
        pct = int(fill_ratio * 100)
        text = (
            f"Context window is {pct}% full. "
            "Prefer concise responses and avoid unnecessary tool calls to prevent compaction."
        )
        return self._make_signal_message(text)

    def run(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = True
        metadata = working_body.get("metadata")
        tool_policy = metadata.get("qz_tool_policy") if isinstance(metadata, dict) else None
        if isinstance(tool_policy, dict):
            policy_style = tool_policy.get("apply_patch_output_style")
            if policy_style in {"native", "custom"}:
                apply_patch_output_style = policy_style
        dropped_tool_names = frozenset(
            metadata.get("qz_dropped_tool_names") or []
            if isinstance(metadata, dict) else []
        )

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
            "tool_hops_max": self.proxy_tool_registry.max_continuation_hops or WEB_SEARCH_MAX_HOPS,
        })

        max_hops = self.proxy_tool_registry.max_continuation_hops or WEB_SEARCH_MAX_HOPS
        try:
            for hop_index in range(max_hops):
                hop_body = json.loads(json.dumps(working_body))
                hop_body["stream"] = True
                hop_body = normalize_responses_input_for_qwen(hop_body, selected_model=self.selected_model)
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
                    error_injected = False
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
                            if event_lines:
                                chunk = b"\n"
                            else:
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
                                abort_items, sequence = self._emit_private_tool_call_aborted(
                                    requested_model,
                                    summary_started,
                                    final_usage,
                                    abort_reason,
                                    tool_call_state.call_name,
                                    public_index=len(public_trace),
                                    sequence=sequence,
                                )
                                public_trace.extend(abort_items)
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
                                abort_items, sequence = self._emit_reasoning_only_aborted(
                                    requested_model,
                                    summary_started,
                                    final_usage,
                                    abort_reason,
                                    reasoning_only_chars,
                                    public_index=len(public_trace),
                                    sequence=sequence,
                                )
                                public_trace.extend(abort_items)
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

                            decision = self.proxy_tool_registry.completed_call_decision(
                                completed_call,
                                apply_patch_output_style,
                                dropped_tool_names=dropped_tool_names,
                            )

                            if decision.kind == "error":
                                # Inject the error result upstream so the model sees
                                # it on the next hop. No lifecycle events emitted to
                                # Codex — the tool never ran.
                                next_input.append(decision.error_result)
                                error_injected = True
                                self._emit("tool_call_error", {
                                    "tool": completed_call.get("name"),
                                    "error": (decision.error_result or {}).get("output", ""),
                                })
                                self._emit_stream_event_timing(
                                    event_type, event_received_at, event_parsed_at,
                                    time.time(), suppressed="function_call_error",
                                )
                                break

                            if decision.kind == "proxy_local":
                                (
                                    sequence,
                                    started_chunks,
                                    started_bytes,
                                    proxy_local_item_id,
                                ) = self._emit_proxy_local_started(
                                    completed_call,
                                    public_index,
                                    sequence,
                                )
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    time.time(),
                                    forwarded_chunks=started_chunks,
                                    forwarded_bytes=started_bytes,
                                    suppressed="function_call_private_started",
                                )
                                self._emit(
                                    "tool_call_started",
                                    self.proxy_tool_registry.telemetry_payload(completed_call),
                                )
                                try:
                                    result = self.proxy_tool_registry.execute(
                                        completed_call,
                                        ProxyToolExecutionContext(
                                            request_id=self.request_id,
                                            counters=counters,
                                            seen_signatures=seen_signatures,
                                        ),
                                    )
                                except Exception as exc:
                                    self._emit(
                                        "tool_call_failed",
                                        self.proxy_tool_registry.telemetry_payload(
                                            completed_call,
                                            error=str(exc),
                                        ),
                                    )
                                    raise
                                self._emit(
                                    "tool_call_completed",
                                    self.proxy_tool_registry.telemetry_payload(
                                        completed_call,
                                        result=result,
                                    ),
                                )
                                public_item = result.public_item
                                public_item["id"] = proxy_local_item_id
                                public_trace.append(public_item)
                                next_input.extend(result.upstream_items)
                                sequence, forwarded_chunks, forwarded_bytes = self._emit_proxy_local_completed(
                                    completed_call,
                                    public_item,
                                    public_index,
                                    sequence,
                                    proxy_local_item_id,
                                )
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

                            result = self.proxy_tool_registry.continuation_result(decision)
                            public_item = result.public_item
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

                        if (
                            is_terminal_stream_event(event_type, payload)
                            and completed_call
                            and self.proxy_tool_registry.is_proxy_local_call(completed_call)
                        ):
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                None,
                                suppressed=self.proxy_tool_registry.terminal_suppression_reason(completed_call),
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
                            if (
                                event_type in {"response.completed", "response.failed", "response.cancelled", "response.incomplete"}
                                and payload is None
                                and public_trace
                                and not sent_terminal
                            ):
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="malformed_terminal",
                                )
                                self._emit_stream_completed(requested_model, len(public_trace), started_at)
                                completed_at = time.time()
                                self._emit_completed(requested_model, public_trace, summary_started, usage=final_usage)
                                sent_terminal = True
                                sent_done = True
                                event_lines = []
                                continue
                            if (
                                (event_type == "done" or payload == "[DONE]")
                                and public_trace
                                and not sent_terminal
                            ):
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="done_without_completed",
                                )
                                self._emit_stream_completed(requested_model, len(public_trace), started_at)
                                completed_at = time.time()
                                self._emit_completed(requested_model, public_trace, summary_started, usage=final_usage)
                                sent_terminal = True
                                sent_done = True
                                event_lines = []
                                continue
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
                    # Drain remaining SSE events to capture the server's
                    # response.completed usage before resp is closed by the
                    # finally block.  Only runs on proxy-local or error breaks
                    # where the while loop exits before the terminal events.
                    if resp is not None and (
                        error_injected
                        or (completed_call and self.proxy_tool_registry.is_proxy_local_call(completed_call))
                    ):
                        drained_usage = self._drain_stream_for_usage(resp)
                        if drained_usage is not None:
                            final_usage = drained_usage
                finally:
                    if raw_log is not None:
                        raw_log.close()
                    if resp is not None:
                        resp.close()

                if error_injected or (completed_call and self.proxy_tool_registry.is_proxy_local_call(completed_call)):
                    if max_output_index >= 0:
                        output_index_offset += max_output_index + 1
                    # Experimental: carry a compact prior-turn reasoning summary
                    # forward as a lightweight context anchor for the next hop.
                    # Controlled by reasoning_carry_forward; off by default.
                    if self.reasoning_carry_forward and reasoning_only_sample.strip():
                        snippet = reasoning_only_sample.strip()[:300]
                        carry_msg = {
                            "type": "message",
                            "role": "user",
                            "content": [{
                                "type": "input_text",
                                "text": f"[Prior reasoning summary: {snippet}]",
                            }],
                        }
                        next_input.insert(0, carry_msg)
                        self._emit("reasoning_carry_forward", {"chars": len(snippet)})
                    # Ephemeral self-management signals for the next hop.
                    hops_remaining = max_hops - (hop_index + 1)
                    hop_signal = self._hop_budget_signal_message(hops_remaining)
                    if hop_signal is not None:
                        next_input.append(hop_signal)
                        self._emit("hop_budget_signal", {
                            "hops_remaining": hops_remaining,
                            "threshold": self.hop_budget_signal_threshold,
                        })
                    ctx_signal = self._context_pressure_signal_message(final_usage)
                    if ctx_signal is not None:
                        next_input.append(ctx_signal)
                        input_tokens = final_usage.get("input_tokens") or 0
                        context_length = (
                            self.selected_model.get("runtime_context_length")
                            or self.selected_model.get("context_length")
                            if isinstance(self.selected_model, dict) else 0
                        ) or 0
                        self._emit("context_pressure_signal", {
                            "input_tokens": int(input_tokens),
                            "context_length": int(context_length),
                            "threshold": self.context_pressure_signal_threshold,
                        })
                    working_body["input"] = next_input
                    continue

                if sent_terminal and not sent_done:
                    self._write_chunk(b"data: [DONE]\n\n")
                    sent_done = True

                if public_trace and not sent_terminal and not sent_done:
                    self._emit_stream_completed(requested_model, len(public_trace), started_at)
                    completed_at = time.time()
                    self._emit_completed(requested_model, public_trace, summary_started, usage=final_usage)
                    sent_terminal = True
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
        except ClientStreamDisconnected as exc:
            self._emit("client_disconnected", {
                "model": requested_model,
                "phase": "stream_write",
                "error": str(exc),
                "duration_ms": round((time.time() - started_at) * 1000.0, 2),
            })
            raise
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
                    "text": self.proxy_tool_registry.continuation_limit_message(),
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
