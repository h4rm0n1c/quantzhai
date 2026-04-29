#!/usr/bin/env python3
import json
import urllib.request

try:
    from .qz_responses import _now_ts, normalize_apply_patch_output_for_codex
    from .qz_runtime_io import capture_path, write_capture
    from .qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from .qz_streaming import (
        StreamedFunctionCallAssembler,
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_events,
        rewrite_sse_payload,
    )
    from .qz_tool_web import WEB_SEARCH_MAX_HOPS
except ImportError:
    from qz_responses import _now_ts, normalize_apply_patch_output_for_codex
    from qz_runtime_io import capture_path, write_capture
    from qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from qz_streaming import (
        StreamedFunctionCallAssembler,
        is_function_call_stream_event,
        is_terminal_stream_event,
        parse_sse_event_lines,
        public_tool_item_events,
        rewrite_sse_payload,
    )
    from qz_tool_web import WEB_SEARCH_MAX_HOPS


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
    ):
        self.upstream = upstream.rstrip("/")
        self.authorization = authorization or "Bearer local"
        self.reasoning_stream_format = reasoning_stream_format
        self.web_runtime = web_runtime
        self.chunk_writer = chunk_writer
        self.stream_opener = stream_opener or self._open_upstream_stream
        self.capture_enabled = capture_enabled

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

    def _public_tool_item_from_function_call(self, call: dict, apply_patch_output_style: str):
        if call.get("name") == "apply_patch":
            return normalize_apply_patch_output_for_codex([call], apply_patch_output_style)[0]
        return call

    def _start_capture(self):
        if not self.capture_enabled:
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
        except Exception:
            pass

    def _open_raw_log(self):
        if not self.capture_enabled:
            return None
        try:
            return capture_path("latest-upstream-response.raw").open("ab")
        except Exception:
            return None

    def _emit_public_tool_item(self, item: dict, public_index: int, sequence: int):
        chunks, sequence = public_tool_item_events(item, public_index, sequence)
        for out_chunk in chunks:
            self._write_chunk(out_chunk)
        return sequence

    def _emit_completed(self, requested_model: str, output: list, summary_started: set):
        completed_payload = {
            "type": "response.completed",
            "response": {
                "id": f"resp_local_{_now_ts()}",
                "object": "response",
                "created_at": _now_ts(),
                "status": "completed",
                "model": requested_model,
                "output": output,
                "usage": _normalize_response_usage({}),
            },
        }
        for out_chunk in transform_sse_event(
            [make_sse_block("response.completed", completed_payload)],
            summary_started,
            self.reasoning_stream_format,
        ):
            self._write_chunk(out_chunk)
        self._write_chunk(b"data: [DONE]\n\n")

    def run(self, body: dict, requested_model: str, apply_patch_output_style: str = "native"):
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = True

        public_trace = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        summary_started = set()
        output_index_offset = 0
        sequence = 0
        sent_response_start = False
        self._start_capture()

        for _hop in range(WEB_SEARCH_MAX_HOPS):
            resp = self.stream_opener(working_body)
            raw_log = self._open_raw_log()
            assembler = StreamedFunctionCallAssembler()
            event_lines = []
            next_input = list(working_body.get("input") or [])
            completed_call = None
            max_output_index = -1

            try:
                while True:
                    chunk = resp.readline()
                    if not chunk:
                        break

                    if raw_log is not None:
                        raw_log.write(chunk)
                        raw_log.flush()

                    event_lines.append(chunk)
                    if chunk not in (b"\n", b"\r\n"):
                        continue

                    event_type, payload = parse_sse_event_lines(event_lines)
                    if isinstance(payload, dict) and isinstance(payload.get("output_index"), int):
                        max_output_index = max(max_output_index, payload["output_index"])
                    if isinstance(payload, dict) and isinstance(payload.get("sequence_number"), int):
                        sequence = max(sequence, payload["sequence_number"])

                    completed = assembler.observe(event_type, payload)
                    if completed:
                        completed_call = completed[0]
                        public_index = completed_call.get("output_index")
                        if not isinstance(public_index, int):
                            public_index = max_output_index + 1
                        public_index += output_index_offset

                        if completed_call.get("name") == "web_search":
                            public_item, tool_output_item, _sources = self.web_runtime.execute_web_search_call(
                                completed_call,
                                counters,
                                seen_signatures,
                            )
                            public_trace.append(public_item)
                            next_input.append(completed_call)
                            next_input.append(tool_output_item)
                            sequence = self._emit_public_tool_item(public_item, public_index, sequence)
                            break

                        public_item = self._public_tool_item_from_function_call(completed_call, apply_patch_output_style)
                        public_trace.append(public_item)
                        sequence = self._emit_public_tool_item(public_item, public_index, sequence)
                        self._emit_completed(requested_model, public_trace, summary_started)
                        return

                    if is_function_call_stream_event(event_type, payload):
                        event_lines = []
                        continue

                    if is_terminal_stream_event(event_type, payload) and completed_call and completed_call.get("name") == "web_search":
                        event_lines = []
                        continue

                    if event_type in {"response.created", "response.in_progress"}:
                        if sent_response_start:
                            event_lines = []
                            continue
                        sent_response_start = True

                    if is_terminal_stream_event(event_type, payload):
                        for out_chunk in self._transformed_chunks(
                            event_type,
                            payload,
                            event_lines,
                            summary_started,
                            output_index_offset=output_index_offset,
                            prepend_output=public_trace,
                            model=requested_model,
                        ):
                            self._write_chunk(out_chunk)
                        event_lines = []
                        continue

                    for out_chunk in self._transformed_chunks(
                        event_type,
                        payload,
                        event_lines,
                        summary_started,
                        output_index_offset=output_index_offset,
                        model=requested_model,
                    ):
                        self._write_chunk(out_chunk)
                    event_lines = []
            finally:
                if raw_log is not None:
                    raw_log.close()
                resp.close()

            if completed_call and completed_call.get("name") == "web_search":
                if max_output_index >= 0:
                    output_index_offset += max_output_index + 1
                working_body["input"] = next_input
                continue

            return

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
        self._emit_completed(requested_model, fallback_output, summary_started)
