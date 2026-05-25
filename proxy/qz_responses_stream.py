#!/usr/bin/env python3
import json
import os
import socket
import time
import urllib.request
from dataclasses import dataclass, field

try:
    from .qz_responses import (
        _now_ts,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from .qz_tool_request import normalize_tool_request_for_llamacpp as _normalize_tool_request_schema
    from .qz_runtime_io import capture_enabled, open_dual_capture_append, write_dual_capture
    from .qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from .qz_streaming import (
        _sse_block_with_sequence,
        custom_tool_call_input_events,
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
    from .qz_tool_apply_patch import inspect_apply_patch_arguments
    from .qz_telemetry import RequestTelemetryEmitter
    from .qz_file_signal import record_tool_call, seed_repeated_read_state
    from .qz_stream_terminal import (
        StreamObservation,
        accumulate,
        classify_stream_terminal,
        observation_from_dict,
        observation_from_event_type,
    )
    from .qz_stream_watchdog import (
        STREAM_NO_OUTPUT_TIMEOUT_S,
        STREAM_TERMINAL_TIMEOUT_S,
        StreamWatchdogState,
        build_terminal_timeout_telemetry_payload,
        build_timeout_telemetry_payload,
        should_trigger_no_output_timeout,
        should_trigger_terminal_timeout,
        stream_timeout_kind,
    )
except ImportError:
    from qz_responses import (
        _now_ts,
        normalize_responses_input_for_qwen,
        normalize_tools_for_llamacpp,
    )
    from qz_tool_request import normalize_tool_request_for_llamacpp as _normalize_tool_request_schema
    from qz_runtime_io import capture_enabled, open_dual_capture_append, write_dual_capture
    from qz_sse import _normalize_response_usage, make_sse_block, transform_sse_event
    from qz_streaming import (
        _sse_block_with_sequence,
        custom_tool_call_input_events,
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
    from qz_tool_apply_patch import inspect_apply_patch_arguments
    from qz_telemetry import RequestTelemetryEmitter
    from qz_file_signal import record_tool_call, seed_repeated_read_state
    from qz_stream_terminal import (
        StreamObservation,
        accumulate,
        classify_stream_terminal,
        observation_from_dict,
        observation_from_event_type,
    )
    from qz_stream_watchdog import (
        STREAM_NO_OUTPUT_TIMEOUT_S,
        STREAM_TERMINAL_TIMEOUT_S,
        StreamWatchdogState,
        build_terminal_timeout_telemetry_payload,
        build_timeout_telemetry_payload,
        should_trigger_no_output_timeout,
        should_trigger_terminal_timeout,
        stream_timeout_kind,
    )


@dataclass
class StreamHopState:
    """Per-hop mutable state for the Responses SSE streaming loop.

    Bundles the mutable locals that are reset at the start of each
    continuation hop. Does not hold outer-loop state such as
    public_trace, sequence, hop counts, or the evolving request body.

    Production code must use StreamHopState.fresh(...) to construct
    an instance so all dependent defaults are initialised correctly.
    Direct construction via StreamHopState(...) is only appropriate
    in tests that explicitly verify field-level behaviour.
    """
    # SSE frame accumulator
    tool_call_state: "StreamToolCallState" = field(default_factory=lambda: None)
    event_lines: list = field(default_factory=list)
    event_started_at: float | None = None
    # Items to forward to the next hop
    next_input: list = field(default_factory=list)
    # Completed function_call seen this hop (if any)
    completed_call: dict | None = None
    # Injection flags (reset each hop)
    error_injected: bool = False
    signal_injected: bool = False
    repair_injected: bool = False
    # Reasoning-only detection state
    reasoning_only_started_at: float | None = None
    reasoning_only_last_delta_at: float | None = None
    reasoning_only_chars: int = 0
    reasoning_only_sample: str = ""
    # Output accounting
    output_text_chars: int = 0
    visible_output_text_seen: bool = False
    assistant_item_seen: bool = False
    public_item_seen: bool = False
    max_output_index: int = -1
    # Output-text artifact detection sample (bounded; checked per delta)
    output_text_artifact_sample: str = ""
    # Stream observation accumulator (dict keyed by request_id + event counts)
    stream_obs_acc: dict = field(default_factory=dict)
    # Watchdog for no-output and terminal timeouts
    watchdog_state: "StreamWatchdogState" = field(default_factory=lambda: None)

    @classmethod
    def fresh(
        cls,
        hop_body: dict,
        request_id: str = "",
        stream_no_output_timeout_s: float = 0.0,
        stream_terminal_timeout_s: float = 0.0,
    ) -> "StreamHopState":
        """Return a new StreamHopState initialised with per-hop defaults.

        Preserves the exact defaults from the pre-extraction local variable block.
        hop_body["input"] is shallow-copied into next_input.
        """
        return cls(
            tool_call_state=StreamToolCallState(),
            event_lines=[],
            event_started_at=None,
            next_input=list(hop_body.get("input") or []),
            completed_call=None,
            error_injected=False,
            signal_injected=False,
            repair_injected=False,
            reasoning_only_started_at=None,
            reasoning_only_last_delta_at=None,
            reasoning_only_chars=0,
            reasoning_only_sample="",
            output_text_chars=0,
            visible_output_text_seen=False,
            assistant_item_seen=False,
            public_item_seen=False,
            max_output_index=-1,
            output_text_artifact_sample="",
            stream_obs_acc={"request_id": request_id},
            watchdog_state=StreamWatchdogState(
                timeout_secs=stream_no_output_timeout_s,
                started_at=time.time(),
                terminal_timeout_secs=stream_terminal_timeout_s,
            ),
        )


@dataclass
class StreamDecision:
    """Vocabulary object for future stream reducer decisions.

    A future reducer will return StreamDecision instances describing what
    qz_responses_stream.py should do next. The stream runtime remains the
    sole side-effect owner: it reads from this decision and acts.

    This slice introduces the dataclass only. qz_responses_stream.py does not
    yet consume StreamDecision broadly; it is used only in the narrow seam
    extracted so far.

    Decision kinds (string constants, not an enum):
      "continue"                  no decision needed; render normally
      "forward_event"             forward this event to client
      "suppress_event"            do not forward; discard
      "finish_no_output_timeout"  terminate, emit timeout fallback
      "finish_terminal_timeout"   terminate, emit terminal fallback
      "abort_reasoning_only"      emit reasoning-only fallback, return
      "abort_tool_call"           emit stuck-tool fallback, return
      "inject_signal"             append signal to next_input, break hop
      "inject_tool_error"         append error to next_input, break hop
      "run_proxy_local_tool"      execute tool locally, break hop
      "forward_public_tool"       emit public tool item, emit terminal
      "start_empty_answer_repair" trigger repair hop
      "emit_terminal"             emit terminal event and [DONE]
      "start_next_hop"            prepare next hop with updated inputs
      "finish_result"             assemble and return final result
    """
    kind: str
    reason: str = ""
    suppress_reason: str = ""
    upstream_items_to_append: list = field(default_factory=list)
    public_item_hint: dict | None = None
    terminal_hint: dict | None = None
    repair_hop_index: int | None = None
    carry_forward_snippet: str | None = None
    hop_budget_signal: dict | None = None
    context_pressure_signal: dict | None = None
    telemetry_hint: str = ""
    warnings: list = field(default_factory=list)


@dataclass
class StreamRunState:
    """Cross-hop mutable outer-loop state for one Responses SSE streaming run.

    Bundles cross-hop locals: terminal emission flags, run timing, final usage,
    and the cross-hop output-index arithmetic.

    Does not include sequence, public_trace, working_body, repair state,
    summary_started, completed_at, or tool lifecycle state — those remain locals.

    Production code must use StreamRunState.fresh(started_at=...) to construct.
    """
    started_at: float
    sent_response_start: bool = False
    sent_terminal: bool = False
    sent_done: bool = False
    first_output_at: float | None = None
    final_usage: dict = field(default_factory=dict)
    output_index_offset: int = 0
    upstream_response_id: str = ""  # response.id from first non-suppressed response.created

    @classmethod
    def fresh(cls, started_at: float) -> "StreamRunState":
        """Return a new StreamRunState initialised for a fresh run."""
        return cls(
            started_at=started_at,
            sent_response_start=False,
            sent_terminal=False,
            sent_done=False,
            first_output_at=None,
            final_usage=_normalize_response_usage({}),
            output_index_offset=0,
            upstream_response_id="",
        )


def _should_suppress_duplicate_response_start(
    event_type: str,
    sent_response_start: bool,
) -> bool:
    """Return True if this response-start event is a duplicate that should be suppressed.

    Pure helper — no I/O, no state mutation.
    response.created and response.in_progress are forwarded only once per hop;
    subsequent arrivals are suppressed with reason "duplicate_response_start".
    """
    return sent_response_start and event_type in {
        "response.created",
        "response.in_progress",
    }


def _should_inject_hop_budget_signal(hops_remaining: int, threshold: int) -> bool:
    """Return True if the hop-budget signal should be injected for this hop.

    Pure helper — no I/O, no state mutation.
    threshold < 0 disables the signal entirely.
    Signal is injected when hops_remaining <= threshold.
    """
    if threshold < 0:
        return False
    return hops_remaining <= threshold


def _should_inject_context_pressure_signal(
    input_tokens: int | float,
    context_length: int,
    threshold: float,
) -> bool:
    """Return True if the context-pressure signal should be injected for this hop.

    Pure helper — no I/O, no state mutation.
    threshold <= 0 disables the signal entirely.
    Signal is injected when input_tokens / context_length >= threshold.
    """
    if threshold <= 0:
        return False
    if context_length <= 0:
        return False
    if input_tokens <= 0:
        return False
    return input_tokens / context_length >= threshold


def _should_suppress_proxy_local_terminal(
    event_type: str,
    payload: object,
    completed_call: dict | None,
    is_proxy_local: bool,
) -> bool:
    """Return True if a proxy-local terminal event should be suppressed.

    Pure helper — no I/O, no state mutation.
    The caller precomputes is_proxy_local via proxy_tool_registry.is_proxy_local_call().
    Returns False whenever completed_call is falsey (None or empty).
    Uses truthiness check to match the original inline condition semantics.
    """
    return (
        is_terminal_stream_event(event_type, payload)
        and bool(completed_call)
        and is_proxy_local
    )


def _reasoning_only_abort_reason(
    *,
    reasoning_only_sample: str,
    reasoning_only_chars: int,
    reasoning_only_progress_at: float | None,
    now: float,
    reasoning_only_timeout_s: float,
    reasoning_only_char_limit: int,
) -> str | None:
    """Return the reasoning-only abort reason string, or None if no abort.

    Pure helper — no I/O, no side effects, no state mutation.

    Abort priority (matches inline logic order):
      1. artifact_tool_payload — sample looks like a tool-call artifact
      2. timeout              — idle time > reasoning_only_timeout_s (disabled if < 0)
      3. char_limit           — chars > reasoning_only_char_limit (disabled if < 0)

    Comparisons are strict (>) to preserve pre-extraction semantics.
    """
    if _looks_like_reasoning_tool_artifact(reasoning_only_sample):
        return "artifact_tool_payload"

    if (
        reasoning_only_timeout_s >= 0
        and reasoning_only_progress_at is not None
        and (now - reasoning_only_progress_at) > reasoning_only_timeout_s
    ):
        return "timeout"

    if (
        reasoning_only_char_limit >= 0
        and reasoning_only_chars > reasoning_only_char_limit
    ):
        return "char_limit"

    return None


OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT = int(os.environ.get("QZ_OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT", "2048"))


def _looks_like_output_tool_artifact(text: str) -> bool:
    """Return True when accumulated output_text content looks like a raw tool or patch payload.

    Stricter than _looks_like_reasoning_tool_artifact — output_text is user-visible and
    false positives disrupt normal answers.  Only triggers on high-confidence exact markers.

    Tier 1 — single unambiguous marker:
      *** Begin Patch / *** End Patch   (Codex patch envelope)
      diff --git a/...                  (git diff format)

    Tier 2 — two structural diff markers:
      diff --git + (--- a/ or +++ b/)

    Tier 3 — JSON starting with '{' + multiple specific markers:
      Raw function_call envelope, apply_patch operation JSON,
      named-tool-call-with-arguments, or raw web_search action object.

    Does NOT flag:
      Ordinary JSON examples in prose, small snippets, code blocks,
      tool result summaries, or any text not starting with the artifact.
    """
    if not text:
        return False
    stripped = text.lstrip()

    # Tier 1A: Exact Codex patch envelope markers — unambiguous regardless of context.
    if stripped.startswith("*** Begin Patch") or "*** End Patch" in stripped[:120]:
        return True

    # Tier 1B/2: Git diff format starting the text — very strong signal.
    if stripped.startswith("diff --git "):
        return True
    diff_hits = sum(1 for m in ("--- a/", "+++ b/", "diff --git ") if m in text[:400])
    if diff_hits >= 2:
        return True

    # Tier 3: JSON-shaped payloads — only when the sample itself starts with '{'.
    if not stripped.startswith("{"):
        return False

    # Collapse whitespace for pattern matching (keep original for length checks).
    compact = text.replace(" ", "").replace("\n", "").replace("\t", "")

    # Raw function_call envelope: {"type":"function_call","name":...,"arguments":...}
    if '"type":"function_call"' in compact and '"name"' in compact:
        return True

    # Raw apply_patch operation JSON: {"operation":{"type":"create_file",...},...}
    # or {"type":"create_file","path":...,"diff":...}
    has_op_type = any(t in compact for t in (
        '"type":"create_file"', '"type":"update_file"', '"type":"modify_file"',
        '"type":"delete_file"', '"type":"move_file"', '"type":"rename_file"',
    ))
    if has_op_type and '"path"' in compact and ('"diff"' in compact or '"content"' in compact):
        return True

    # Named-tool-call-with-arguments: {"name":"web_search","arguments":...}
    known_tool_names = ('"name":"web_search"', '"name":"apply_patch"',
                        '"name":"exec_command"', '"name":"write_stdin"')
    if '"arguments"' in compact and any(n in compact for n in known_tool_names):
        return True

    # Raw web_search action object at top level.
    # Require action value + at least one other web_search-specific field.
    if '"action":"capabilities"' in compact:
        return True
    web_search_actions = ('"action":"search"', '"action":"retrieve"',
                          '"action":"open_page"', '"action":"find_in_page"')
    if any(a in compact for a in web_search_actions):
        if '"query"' in compact or '"url"' in compact or '"profile"' in compact:
            return True

    return False


PRIVATE_FUNCTION_CALL_TIMEOUT_S = float(os.environ.get("QZ_PRIVATE_TOOL_CALL_TIMEOUT_S", "120"))
PRIVATE_FUNCTION_CALL_DELTA_LIMIT = int(os.environ.get("QZ_PRIVATE_TOOL_CALL_DELTA_LIMIT", "1200"))
REASONING_ONLY_TIMEOUT_S = float(os.environ.get("QZ_REASONING_ONLY_TIMEOUT_S", "120"))
REASONING_ONLY_CHAR_LIMIT = int(os.environ.get("QZ_REASONING_ONLY_CHAR_LIMIT", "-1"))
REASONING_ARTIFACT_SCAN_LIMIT = int(os.environ.get("QZ_REASONING_ARTIFACT_SCAN_LIMIT", "8192"))
EMPTY_ANSWER_REPAIR_HOPS = int(os.environ.get("QZ_EMPTY_ANSWER_REPAIR_HOPS", "1"))
EMPTY_ANSWER_REPAIR_DISABLE_TOOLS = int(os.environ.get("QZ_EMPTY_ANSWER_REPAIR_DISABLE_TOOLS", "1")) != 0
EMPTY_ANSWER_REPAIR_MESSAGE = (
    "Protocol repair: your previous completion ended without any user-visible output_text.\n\n"
    "Produce the final answer for the original user request now.\n"
    "Do not call tools.\n"
    "Do not continue reasoning.\n"
    "Do not mention this repair step.\n"
    "Write only the final assistant answer."
)

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


def _message_content_output_text(content) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "output_text":
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _response_output_items(response: dict) -> list:
    output = response.get("output") if isinstance(response, dict) else None
    return output if isinstance(output, list) else []


def _response_has_visible_output_text(response: dict) -> bool:
    for item in _response_output_items(response):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if _message_content_output_text(item.get("content")).strip():
            return True
    return False


def _response_reasoning_chars(response: dict) -> int:
    total = 0
    for item in _response_output_items(response):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"reasoning_text", "summary_text"}:
                total += len(str(part.get("text") or ""))
    return total


def _is_valid_public_output_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    item_type = item.get("type")
    if item_type in {None, "reasoning", "message"}:
        return False
    if item.get("status") in {"in_progress", "incomplete"}:
        return False
    return True


def _is_completed_assistant_message_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") != "message":
        return False
    return item.get("status") not in {"in_progress", "incomplete"}


def _response_has_valid_public_item(response: dict) -> bool:
    return any(_is_valid_public_output_item(item) for item in _response_output_items(response))


def _usage_telemetry_fields(usage: dict | None) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    return {
        "usage": _normalize_response_usage(usage),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


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
        empty_answer_repair_hops: int | None = None,
        empty_answer_repair_disable_tools: bool | None = None,
        stream_no_output_timeout_s: float | None = None,
        stream_terminal_timeout_s: float | None = None,
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
        self.empty_answer_repair_hops = (
            EMPTY_ANSWER_REPAIR_HOPS
            if empty_answer_repair_hops is None
            else int(empty_answer_repair_hops)
        )
        self.empty_answer_repair_disable_tools = (
            EMPTY_ANSWER_REPAIR_DISABLE_TOOLS
            if empty_answer_repair_disable_tools is None
            else bool(empty_answer_repair_disable_tools)
        )
        self.stream_no_output_timeout_s = (
            STREAM_NO_OUTPUT_TIMEOUT_S
            if stream_no_output_timeout_s is None
            else float(stream_no_output_timeout_s)
        )
        self.stream_terminal_timeout_s = (
            STREAM_TERMINAL_TIMEOUT_S
            if stream_terminal_timeout_s is None
            else float(stream_terminal_timeout_s)
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

    def _configure_stream_read_timeout(self, resp, timeout_secs: float | None = None):
        """Apply a watchdog read timeout to the upstream socket if possible."""
        if timeout_secs is None:
            timeout_secs = self.stream_no_output_timeout_s
        if timeout_secs <= 0:
            return None
        timeout = max(0.001, float(timeout_secs))
        targets = [resp]
        fp = getattr(resp, "fp", None)
        if fp is not None:
            targets.append(fp)
            raw = getattr(fp, "raw", None)
            if raw is not None:
                targets.append(raw)
                sock = getattr(raw, "_sock", None)
                if sock is not None:
                    targets.append(sock)
        for target in targets:
            settimeout = getattr(target, "settimeout", None)
            if callable(settimeout):
                try:
                    gettimeout = getattr(target, "gettimeout", None)
                    previous_timeout = gettimeout() if callable(gettimeout) else None
                    settimeout(timeout)
                    return target, previous_timeout
                except Exception:
                    continue
        return None

    @staticmethod
    def _restore_stream_read_timeout(handle) -> None:
        if not handle:
            return
        target, previous_timeout = handle
        settimeout = getattr(target, "settimeout", None)
        if callable(settimeout):
            try:
                settimeout(previous_timeout)
            except Exception:
                pass

    @staticmethod
    def _read_stream_line(resp):
        return resp.readline()

    def _write_chunk(self, chunk: bytes):
        try:
            self.chunk_writer(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise ClientStreamDisconnected(str(exc) or exc.__class__.__name__) from exc

    def _emit(self, event_type: str, payload: dict | None = None):
        self.telemetry_emitter.emit(event_type, payload)

    @staticmethod
    def _safe_preview(value, limit: int) -> str:
        """Coerce a model-supplied argument value to a bounded string.

        Model-provided JSON fields may be any type. Convert non-strings
        to a compact JSON representation before truncating so telemetry
        payload values are always plain strings.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value[:limit]
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:limit]
            except Exception:
                return ""
        return str(value)[:limit]

    def _check_sandbox_escalation(self, call: dict) -> dict | None:
        """Return a telemetry payload if this outgoing function_call requests
        sandbox escalation (sandbox_permissions == 'require_escalated').

        Returns None for any other call or if arguments are unparseable.
        The cmd_preview is truncated to avoid exposing full command text in
        telemetry.
        """
        if not isinstance(call, dict):
            return None
        raw_args = call.get("arguments")
        if not isinstance(raw_args, str) or not raw_args.strip():
            return None
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(args, dict):
            return None
        if args.get("sandbox_permissions") != "require_escalated":
            return None
        return {
            "tool": str(call.get("name") or "exec_command"),
            "call_id": str(call.get("call_id") or call.get("id") or ""),
            "sandbox_permissions": "require_escalated",
            "justification": self._safe_preview(args.get("justification"), 200),
            "cmd_preview": self._safe_preview(args.get("cmd"), 80),
        }

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
        response_id: str = "",
    ):
        if isinstance(payload, dict):
            event_type, payload = rewrite_sse_payload(
                event_type,
                payload,
                output_index_offset=output_index_offset,
                prepend_output=prepend_output,
                model=model,
                response_id=response_id,
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
        """Emit output_item.added + [custom_tool_call_input.delta/done] + output_item.done.

        For custom_tool_call items (apply_patch), also emits the streaming input
        delta/done events that Codex parses as ResponseEvent::ToolCallInputDelta.
        Source: codex-rs/codex-api/src/sse/responses.rs:314
        """
        started_chunks, sequence = public_tool_item_started_event(item, public_index, sequence)
        all_chunks = list(started_chunks)

        # For custom_tool_call items, emit input delta/done between added and done.
        if isinstance(item, dict) and item.get("type") == "custom_tool_call":
            input_chunks, sequence = custom_tool_call_input_events(item, public_index, sequence)
            all_chunks.extend(input_chunks)

        done_chunks, sequence = public_tool_item_done_event(item, public_index, sequence)
        all_chunks.extend(done_chunks)

        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(all_chunks)
        return sequence, forwarded_chunks, forwarded_bytes

    def _emit_proxy_local_started(self, call: dict, public_index: int, sequence: int):
        public_item = self.proxy_tool_registry.started_public_item(call, public_index)
        item_id = public_item["id"]
        # Only emit output_item.added. Fake response.<tool>_call.* subevents removed.
        # Codex only parses output_item.added / output_item.done / completed.
        # See docs/codex-source-tool-contract.md.
        chunks, sequence = public_tool_item_started_event(public_item, public_index, sequence)
        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(chunks)
        return sequence, forwarded_chunks, forwarded_bytes, item_id

    def _emit_proxy_local_completed(self, call: dict, item: dict, public_index: int, sequence: int, item_id: str):
        item = dict(item)
        item["id"] = item_id
        # Only emit output_item.done. Fake response.<tool>_call.* subevents removed.
        # Codex only parses output_item.added / output_item.done / completed.
        # See docs/codex-source-tool-contract.md.
        chunks, sequence = public_tool_item_done_event(item, public_index, sequence)
        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(chunks)
        return sequence, forwarded_chunks, forwarded_bytes

    @staticmethod
    def _synthesize_response_id(response_id: str, request_id: str = "") -> str:
        """Return the upstream response.id when known, otherwise a unique fallback.

        Avoids timestamp collisions by including the request_id (when available)
        and a uuid segment. Never uses a bare _now_ts() timestamp.
        """
        if response_id:
            return response_id
        import uuid as _uuid
        suffix = f"{request_id[:8]}_" if request_id else ""
        return f"resp_local_{suffix}{_uuid.uuid4().hex[:12]}"

    def _emit_completed(
        self,
        requested_model: str,
        output: list,
        summary_started: set,
        usage=None,
        response_id: str = "",
    ):
        usage_obj = _normalize_response_usage(usage)
        _usage_is_synthetic = not (
            (usage_obj.get("input_tokens") or 0) > 0
            or (usage_obj.get("output_tokens") or 0) > 0
        )
        completed_payload = {
            "type": "response.completed",
            "response": {
                "id": self._synthesize_response_id(response_id, self.request_id),
                "object": "response",
                "created_at": _now_ts(),
                "status": "completed",
                "model": requested_model,
                "output": output,
                "usage": usage_obj,
            },
        }
        for out_chunk in transform_sse_event(
            [make_sse_block("response.completed", completed_payload)],
            summary_started,
            self.reasoning_stream_format,
        ):
            self._write_chunk(out_chunk)
        self._write_chunk(b"data: [DONE]\n\n")
        if _usage_is_synthetic:
            self._emit("usage_synthetic", {
                "model": requested_model,
                "usage_source": "synthetic_empty",
                "usage_unknown": True,
            })

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
        response_id: str = "",
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
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage, response_id=response_id)
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
        response_id: str = "",
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
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage, response_id=response_id)
        return [output_item], sequence

    def _emit_no_output_timeout_fallback(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        watchdog_state: "StreamWatchdogState",
        now: float,
        obs: "StreamObservation",
        public_index: int = 0,
        sequence: int = 0,
        response_id: str = "",
    ) -> tuple:
        """Emit a fallback terminal when no visible output appeared before deadline.

        Follows the same SSE-event pattern as _emit_reasoning_only_aborted().
        Emits stream_terminal_classified telemetry with signal-compatible payload.
        """
        text = (
            "The stream started but produced no visible output before the "
            "no-output timeout. The request may have stalled upstream. "
            "Please retry."
        )
        output_item = {
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        terminal = classify_stream_terminal(obs)
        payload = build_timeout_telemetry_payload(
            watchdog_state, terminal, now, request_id=self.request_id
        )
        self._emit("stream_terminal_classified", payload)
        sequence = self._stream_fallback_message(output_item, public_index, sequence)
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage, response_id=response_id)
        return [output_item], sequence

    def _finish_no_output_timeout(
        self,
        requested_model: str,
        started_at: float,
        first_output_at: float | None,
        final_usage,
        public_trace: list,
        summary_started: set,
        watchdog_state: "StreamWatchdogState",
        timeout_at: float,
        *,
        reasoning_only_chars: int,
        visible_output_text_seen: bool,
        assistant_item_seen: bool,
        completed_call,
        sent_terminal: bool,
        sent_done: bool,
        sequence: int,
        stream_obs_acc: dict | None = None,
        response_id: str = "",
    ) -> dict:
        watchdog_state.triggered = True
        timeout_acc = dict(stream_obs_acc or {"request_id": self.request_id})
        self._merge_manual_stream_observation(
            timeout_acc,
            reasoning_only_chars=reasoning_only_chars,
            visible_output_text_seen=visible_output_text_seen,
            assistant_item_seen=assistant_item_seen,
            completed_call=completed_call,
            sent_terminal=sent_terminal,
            sent_done=sent_done,
        )
        timeout_acc["output_timeout"] = True
        timeout_obs = observation_from_dict(timeout_acc)
        timeout_items, _sequence = self._emit_no_output_timeout_fallback(
            requested_model,
            summary_started,
            final_usage,
            watchdog_state,
            timeout_at,
            timeout_obs,
            public_index=len(public_trace),
            sequence=sequence,
            response_id=response_id,
        )
        public_trace.extend(timeout_items)
        self._emit_stream_completed(
            requested_model, len(public_trace), started_at, fallback=True
        )
        completed_at = time.time()
        timeout_obs.fallback_emitted = True
        return self._build_result(
            requested_model,
            started_at,
            first_output_at,
            completed_at,
            final_usage,
            len(public_trace),
            fallback=True,
            obs=timeout_obs,
            emit_terminal_classified=False,
        )

    def _emit_terminal_timeout_completion(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        output: list,
        response_id: str = "",
    ) -> None:
        """Close a partial visible-output stream without replaying the answer."""
        self._emit_completed(requested_model, output, summary_started, usage=final_usage, response_id=response_id)

    def _finish_terminal_timeout_after_output(
        self,
        requested_model: str,
        started_at: float,
        first_output_at: float | None,
        final_usage,
        public_trace: list,
        summary_started: set,
        watchdog_state: "StreamWatchdogState",
        timeout_at: float,
        *,
        reasoning_only_chars: int,
        visible_output_text_seen: bool,
        assistant_item_seen: bool,
        completed_call,
        sent_terminal: bool,
        sent_done: bool,
        sequence: int,
        stream_obs_acc: dict | None = None,
        response_id: str = "",
    ) -> dict:
        watchdog_state.triggered = True
        timeout_acc = dict(stream_obs_acc or {"request_id": self.request_id})
        self._merge_manual_stream_observation(
            timeout_acc,
            reasoning_only_chars=reasoning_only_chars,
            visible_output_text_seen=visible_output_text_seen,
            assistant_item_seen=assistant_item_seen,
            completed_call=completed_call,
            sent_terminal=sent_terminal,
            sent_done=sent_done,
        )
        timeout_acc["terminal_timeout"] = True
        timeout_acc["fallback_emitted"] = True
        timeout_obs = observation_from_dict(timeout_acc)
        terminal = classify_stream_terminal(timeout_obs)
        payload = build_terminal_timeout_telemetry_payload(
            watchdog_state, terminal, timeout_at, request_id=self.request_id
        )
        self._emit("stream_terminal_classified", payload)
        self._emit_terminal_timeout_completion(
            requested_model,
            summary_started,
            final_usage,
            public_trace,
            response_id=response_id,
        )
        self._emit_stream_completed(
            requested_model, len(public_trace), started_at, fallback=True
        )
        completed_at = time.time()
        return self._build_result(
            requested_model,
            started_at,
            first_output_at,
            completed_at,
            final_usage,
            len(public_trace),
            fallback=True,
            obs=timeout_obs,
            emit_terminal_classified=False,
        )

    def _merge_manual_stream_observation(
        self,
        stream_obs_acc: dict,
        *,
        reasoning_only_chars: int,
        visible_output_text_seen: bool,
        assistant_item_seen: bool,
        completed_call,
        sent_terminal: bool,
        sent_done: bool,
    ) -> dict:
        stream_obs_acc["request_id"] = self.request_id
        return accumulate(
            stream_obs_acc,
            {
                "saw_reasoning": bool(reasoning_only_chars > 0),
                "saw_output_text": bool(visible_output_text_seen),
                "saw_assistant_item": bool(assistant_item_seen),
                "saw_tool_call": bool(completed_call is not None),
                "saw_response_completed": bool(sent_terminal),
                "saw_done": bool(sent_done),
            },
        )

    @staticmethod
    def _timeout_check_time(exc: BaseException, state: "StreamWatchdogState") -> float:
        exc_now = getattr(exc, "now", None)
        if isinstance(exc_now, (int, float)):
            return float(exc_now)
        if (
            state.terminal_timeout_secs > 0
            and state.first_visible_output_at is not None
            and state.first_terminal_at is None
        ):
            return state.first_visible_output_at + state.terminal_timeout_secs
        if state.timeout_secs > 0:
            reference = state.first_event_at if state.first_event_at is not None else state.started_at
            return reference + state.timeout_secs
        return time.time()

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
        if not _should_inject_hop_budget_signal(hops_remaining, self.hop_budget_signal_threshold):
            return None
        text = (
            f"You have {hops_remaining} continuation hop(s) remaining this turn. "
            "If the task is complete or nearly complete, give a direct answer rather than calling more tools."
        )
        return self._make_signal_message(text)

    def _context_pressure_signal_message(self, usage: dict) -> dict | None:
        context_length = None
        if isinstance(self.selected_model, dict):
            context_length = (
                self.selected_model.get("runtime_context_length")
                or self.selected_model.get("context_length")
            )
        context_length = context_length if isinstance(context_length, int) else 0
        input_tokens = usage.get("input_tokens") or 0
        if not isinstance(input_tokens, (int, float)):
            input_tokens = 0
        if not _should_inject_context_pressure_signal(
            input_tokens,
            context_length,
            self.context_pressure_signal_threshold,
        ):
            return None
        fill_ratio = input_tokens / context_length
        pct = int(fill_ratio * 100)
        text = (
            f"Context window is {pct}% full. "
            "Prefer concise responses and avoid unnecessary tool calls to prevent compaction."
        )
        return self._make_signal_message(text)

    def _empty_answer_repair_payload(
        self,
        requested_model: str,
        reasoning_chars: int,
        upstream_output_items: int,
        usage,
        repair_hop_index: int,
    ) -> dict:
        payload = {
            "model": requested_model,
            "reasoning_chars": int(reasoning_chars),
            "upstream_output_items": int(upstream_output_items),
            "repair_hop_index": int(repair_hop_index),
        }
        payload.update(_usage_telemetry_fields(usage))
        return payload

    def _apply_empty_answer_repair(self, body: dict, next_input: list) -> dict:
        repair_body = json.loads(json.dumps(body))
        repair_body["input"] = list(next_input) + [self._make_signal_message(EMPTY_ANSWER_REPAIR_MESSAGE)]
        if self.empty_answer_repair_disable_tools:
            repair_body.pop("tools", None)
            repair_body.pop("tool_choice", None)
        return repair_body

    def _emit_output_text_artifact_aborted(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        chars_scanned: int,
        public_index: int = 0,
        sequence: int = 0,
        response_id: str = "",
    ) -> tuple:
        text = (
            "I stopped a malformed tool payload before it could leak into the assistant response. "
            "Please retry the request."
        )
        output_item = {
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        self._emit("output_text_artifact_aborted", {
            "model": requested_model,
            "reason": "output_text_tool_artifact",
            "chars_scanned": int(chars_scanned),
        })
        sequence = self._stream_fallback_message(output_item, public_index, sequence)
        self._emit_completed(requested_model, [output_item], summary_started, usage=final_usage, response_id=response_id)
        return [output_item], sequence

    def _emit_reasoning_only_completed_without_answer(
        self,
        requested_model: str,
        summary_started: set,
        final_usage,
        reasoning_chars: int,
        public_index: int = 0,
        sequence: int = 0,
        prefix_output: list | None = None,
        response_id: str = "",
    ) -> tuple:
        text = (
            "The model completed a reasoning-only response without producing a "
            "user-visible answer. Please retry with a narrower request."
        )
        output_item = {
            "id": f"msg_local_{_now_ts()}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }
        self._emit("reasoning_only_completed_without_answer", {
            "model": requested_model,
            "reason": "reasoning_only_completed_without_answer",
            "reasoning_chars": int(reasoning_chars),
        })
        sequence = self._stream_fallback_message(output_item, public_index, sequence)
        self._emit_completed(
            requested_model,
            list(prefix_output or []) + [output_item],
            summary_started,
            usage=final_usage,
            response_id=response_id,
        )
        return [output_item], sequence

    def run(self, body: dict, requested_model: str):
        working_body = json.loads(json.dumps(body))
        working_body["stream"] = True
        metadata = working_body.get("metadata")
        dropped_tool_names = frozenset(
            metadata.get("qz_dropped_tool_names") or []
            if isinstance(metadata, dict) else []
        )

        # Seed repeated-read state from incoming body["input"] before the hop loop.
        # This detects cross-request repeated reads that Codex replays in history.
        repeated_read_state = seed_repeated_read_state(working_body.get("input") or [])

        completed_at = None
        public_trace = []
        counters = {"search": 0, "open_page": 0}
        seen_signatures = set()
        summary_started = set()
        sequence = 0
        rs = StreamRunState.fresh(started_at=time.time())
        self._start_capture()
        self._emit("stream_started", {
            "model": requested_model,
            "tool_hops_max": self.proxy_tool_registry.max_continuation_hops or WEB_SEARCH_MAX_HOPS,
        })

        max_hops = self.proxy_tool_registry.max_continuation_hops or WEB_SEARCH_MAX_HOPS
        repair_hops_used = 0
        pending_repair_hop_index = None
        try:
            for hop_index in range(max_hops + max(0, self.empty_answer_repair_hops)):
                active_repair_hop_index = pending_repair_hop_index
                pending_repair_hop_index = None
                if hop_index >= max_hops and active_repair_hop_index is None:
                    break
                hop_body = json.loads(json.dumps(working_body))
                hop_body["stream"] = True
                hop_body = normalize_responses_input_for_qwen(hop_body, selected_model=self.selected_model)
                if hop_index == 0:
                    # On the first hop capture the normalization report so we can
                    # emit a compact operator event when tools are replaced/deduped.
                    _schema_report = _normalize_tool_request_schema(hop_body)
                    if (_schema_report.replaced or _schema_report.translated
                            or _schema_report.dropped or _schema_report.deduped):
                        try:
                            self._emit("tool_schema_replaced", {
                                "replaced": list(_schema_report.replaced),
                                "translated": list(_schema_report.translated),
                                "dropped": list(_schema_report.dropped),
                                "dropped_count": len(_schema_report.dropped),
                                "deduped": list(_schema_report.deduped),
                                "source": "tool_schema_normalizer",
                                "request_id": self.request_id,
                            })
                        except Exception:
                            pass
                else:
                    normalize_tools_for_llamacpp(hop_body)
                resp = None
                raw_log = None
                read_timeout_handle = None
                def restore_read_timeout_once():
                    nonlocal read_timeout_handle
                    if read_timeout_handle is not None:
                        self._restore_stream_read_timeout(read_timeout_handle)
                        read_timeout_handle = None

                try:
                    resp = self.stream_opener(hop_body)
                    read_timeout_handle = self._configure_stream_read_timeout(
                        resp,
                        self.stream_no_output_timeout_s,
                    )
                    def set_stream_read_timeout(timeout_secs):
                        nonlocal read_timeout_handle
                        restore_read_timeout_once()
                        if timeout_secs > 0:
                            read_timeout_handle = self._configure_stream_read_timeout(
                                resp,
                                timeout_secs,
                            )

                    raw_log = self._open_raw_log()
                    hs = StreamHopState.fresh(
                        hop_body,
                        request_id=self.request_id,
                        stream_no_output_timeout_s=self.stream_no_output_timeout_s,
                        stream_terminal_timeout_s=self.stream_terminal_timeout_s,
                    )

                    def sync_terminal_read_timeout(now):
                        if hs.watchdog_state.first_terminal_at is not None:
                            restore_read_timeout_once()
                            return
                        if hs.watchdog_state.first_visible_output_at is None:
                            return
                        if self.stream_terminal_timeout_s <= 0:
                            restore_read_timeout_once()
                            return
                        deadline = hs.watchdog_state.first_visible_output_at + self.stream_terminal_timeout_s
                        set_stream_read_timeout(max(0.001, deadline - now))

                    while True:
                        try:
                            chunk = self._read_stream_line(resp)
                        except (TimeoutError, socket.timeout) as exc:
                            timeout_at = self._timeout_check_time(exc, hs.watchdog_state)
                            timeout_kind = stream_timeout_kind(hs.watchdog_state, timeout_at)
                            if timeout_kind == "no_output":
                                return self._finish_no_output_timeout(
                                    requested_model,
                                    rs.started_at,
                                    rs.first_output_at,
                                    rs.final_usage,
                                    public_trace,
                                    summary_started,
                                    hs.watchdog_state,
                                    timeout_at,
                                    reasoning_only_chars=hs.reasoning_only_chars,
                                    visible_output_text_seen=hs.visible_output_text_seen,
                                    assistant_item_seen=hs.assistant_item_seen,
                                    completed_call=hs.completed_call,
                                    sent_terminal=rs.sent_terminal,
                                    sent_done=rs.sent_done,
                                    sequence=sequence,
                                    stream_obs_acc=hs.stream_obs_acc,
                                    response_id=rs.upstream_response_id,
                                )
                            if timeout_kind == "terminal":
                                return self._finish_terminal_timeout_after_output(
                                    requested_model,
                                    rs.started_at,
                                    rs.first_output_at,
                                    rs.final_usage,
                                    public_trace,
                                    summary_started,
                                    hs.watchdog_state,
                                    timeout_at,
                                    reasoning_only_chars=hs.reasoning_only_chars,
                                    visible_output_text_seen=hs.visible_output_text_seen,
                                    assistant_item_seen=hs.assistant_item_seen,
                                    completed_call=hs.completed_call,
                                    sent_terminal=rs.sent_terminal,
                                    sent_done=rs.sent_done,
                                    sequence=sequence,
                                    stream_obs_acc=hs.stream_obs_acc,
                                    response_id=rs.upstream_response_id,
                                )
                            raise
                        if not chunk:
                            if hs.event_lines:
                                chunk = b"\n"
                            else:
                                break

                        if raw_log is not None:
                            raw_log.write(chunk)
                            raw_log.flush()

                        if hs.event_started_at is None:
                            hs.event_started_at = time.time()
                        hs.event_lines.append(chunk)
                        if chunk not in (b"\n", b"\r\n"):
                            continue

                        event_received_at = hs.event_started_at or time.time()
                        event_type, payload = parse_sse_event_lines(hs.event_lines)
                        event_parsed_at = time.time()
                        hs.event_started_at = None
                        hs.watchdog_state.mark_event(event_parsed_at)
                        accumulate(
                            hs.stream_obs_acc,
                            observation_from_event_type(event_type, payload),
                        )
                        if rs.first_output_at is None and event_type not in {"response.created", "response.in_progress"}:
                            rs.first_output_at = time.time()
                        if isinstance(payload, dict) and isinstance(payload.get("output_index"), int):
                            hs.max_output_index = max(hs.max_output_index, payload["output_index"])
                        if isinstance(payload, dict) and isinstance(payload.get("sequence_number"), int):
                            sequence = max(sequence, payload["sequence_number"])
                        if event_type == "response.output_text.delta" and isinstance(payload, dict):
                            delta = payload.get("delta")
                            delta_text = delta if isinstance(delta, str) else ""
                            hs.output_text_chars += len(delta_text)
                            if delta_text.strip():
                                hs.visible_output_text_seen = True
                                hs.watchdog_state.mark_visible_output(event_parsed_at)
                            # Accumulate bounded sample for artifact detection.
                            if len(hs.output_text_artifact_sample) < OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT:
                                remaining = OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT - len(hs.output_text_artifact_sample)
                                hs.output_text_artifact_sample += delta_text[:remaining]
                            if delta_text and _looks_like_output_tool_artifact(hs.output_text_artifact_sample):
                                self._emit_stream_event_timing(
                                    event_type, event_received_at, event_parsed_at,
                                    None, suppressed="output_text_artifact_aborted",
                                )
                                abort_items, sequence = self._emit_output_text_artifact_aborted(
                                    requested_model,
                                    summary_started,
                                    rs.final_usage,
                                    len(hs.output_text_artifact_sample),
                                    public_index=len(public_trace),
                                    sequence=sequence,
                                    response_id=rs.upstream_response_id,
                                )
                                public_trace.extend(abort_items)
                                self._emit_stream_completed(requested_model, len(public_trace), rs.started_at, fallback=True)
                                completed_at = time.time()
                                return self._build_result(
                                    requested_model,
                                    rs.started_at,
                                    rs.first_output_at,
                                    completed_at,
                                    rs.final_usage,
                                    len(public_trace),
                                    fallback=True,
                                )
                        if event_type in {
                            "response.output_item.added",
                            "response.output_item.done",
                        } and isinstance(payload, dict):
                            item = payload.get("item")
                            if event_type == "response.output_item.done" and _is_completed_assistant_message_item(item):
                                hs.assistant_item_seen = True
                                hs.watchdog_state.mark_visible_output(event_parsed_at)
                            if event_type == "response.output_item.done" and _is_valid_public_output_item(item):
                                hs.public_item_seen = True
                        if event_type == "response.completed":
                            response = payload.get("response") if isinstance(payload, dict) else {}
                            if isinstance(response, dict):
                                rs.final_usage = _normalize_response_usage(response.get("usage"))
                        if is_terminal_stream_event(event_type, payload):
                            hs.watchdog_state.mark_terminal(event_parsed_at)
                            restore_read_timeout_once()

                        # Watchdog: fires on any event arrival if a timeout deadline passed.
                        timeout_kind = stream_timeout_kind(hs.watchdog_state, event_parsed_at)
                        if timeout_kind == "no_output":
                            return self._finish_no_output_timeout(
                                requested_model,
                                rs.started_at,
                                rs.first_output_at,
                                rs.final_usage,
                                public_trace,
                                summary_started,
                                hs.watchdog_state,
                                event_parsed_at,
                                reasoning_only_chars=hs.reasoning_only_chars,
                                visible_output_text_seen=hs.visible_output_text_seen,
                                assistant_item_seen=hs.assistant_item_seen,
                                completed_call=hs.completed_call,
                                sent_terminal=rs.sent_terminal,
                                sent_done=rs.sent_done,
                                sequence=sequence,
                                stream_obs_acc=hs.stream_obs_acc,
                                response_id=rs.upstream_response_id,
                            )
                        if timeout_kind == "terminal":
                            return self._finish_terminal_timeout_after_output(
                                requested_model,
                                rs.started_at,
                                rs.first_output_at,
                                rs.final_usage,
                                public_trace,
                                summary_started,
                                hs.watchdog_state,
                                event_parsed_at,
                                reasoning_only_chars=hs.reasoning_only_chars,
                                visible_output_text_seen=hs.visible_output_text_seen,
                                assistant_item_seen=hs.assistant_item_seen,
                                completed_call=hs.completed_call,
                                sent_terminal=rs.sent_terminal,
                                sent_done=rs.sent_done,
                                sequence=sequence,
                                stream_obs_acc=hs.stream_obs_acc,
                                response_id=rs.upstream_response_id,
                            )
                        sync_terminal_read_timeout(event_parsed_at)

                        completed = hs.tool_call_state.observe(event_type, payload, event_received_at)
                        if is_function_call_stream_event(event_type, payload):
                            # Do not expose executable tool calls until arguments are complete.
                            # Codex currently treats response.output_item.added for function_call
                            # as runnable even when arguments are still streaming, which can execute
                            # an empty-argument command before response.function_call_arguments.done.
                            abort_reason = hs.tool_call_state.abort_reason(
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
                                    rs.final_usage,
                                    abort_reason,
                                    hs.tool_call_state.call_name,
                                    public_index=len(public_trace),
                                    sequence=sequence,
                                    response_id=rs.upstream_response_id,
                                )
                                public_trace.extend(abort_items)
                                self._emit_stream_completed(requested_model, len(public_trace), rs.started_at, fallback=True)
                                completed_at = time.time()
                                return self._build_result(
                                    requested_model,
                                    rs.started_at,
                                    rs.first_output_at,
                                    completed_at,
                                    rs.final_usage,
                                    len(public_trace),
                                )
                        if (
                            event_type in {
                                "response.reasoning_text.delta",
                                "response.reasoning_summary_text.delta",
                            }
                            and isinstance(payload, dict)
                            and not hs.visible_output_text_seen
                            and not hs.assistant_item_seen
                            and not hs.public_item_seen
                        ):
                            if hs.reasoning_only_started_at is None:
                                hs.reasoning_only_started_at = event_received_at
                            delta_text = str(payload.get("delta") or "")
                            if delta_text:
                                hs.reasoning_only_last_delta_at = event_received_at
                                if len(hs.reasoning_only_sample) < REASONING_ARTIFACT_SCAN_LIMIT:
                                    remaining = REASONING_ARTIFACT_SCAN_LIMIT - len(hs.reasoning_only_sample)
                                    hs.reasoning_only_sample += delta_text[:remaining]
                            hs.reasoning_only_chars += len(delta_text)
                            reasoning_only_progress_at = hs.reasoning_only_last_delta_at or hs.reasoning_only_started_at
                            abort_reason = _reasoning_only_abort_reason(
                                reasoning_only_sample=hs.reasoning_only_sample,
                                reasoning_only_chars=hs.reasoning_only_chars,
                                reasoning_only_progress_at=reasoning_only_progress_at,
                                now=time.time(),
                                reasoning_only_timeout_s=self.reasoning_only_timeout_s,
                                reasoning_only_char_limit=self.reasoning_only_char_limit,
                            ) or ""
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
                                    rs.final_usage,
                                    abort_reason,
                                    hs.reasoning_only_chars,
                                    public_index=len(public_trace),
                                    sequence=sequence,
                                    response_id=rs.upstream_response_id,
                                )
                                public_trace.extend(abort_items)
                                self._emit_stream_completed(requested_model, len(public_trace), rs.started_at, fallback=True)
                                completed_at = time.time()
                                return self._build_result(
                                    requested_model,
                                    rs.started_at,
                                    rs.first_output_at,
                                    completed_at,
                                    rs.final_usage,
                                    len(public_trace),
                                    fallback=True,
                                )
                        if completed:
                            hs.completed_call = completed[0]
                            completed_key = hs.completed_call.get("id") or hs.completed_call.get("call_id")
                            public_index = hs.completed_call.get("output_index")
                            if not isinstance(public_index, int):
                                public_index = hs.max_output_index + 1
                            public_index += rs.output_index_offset

                            escalation = self._check_sandbox_escalation(hs.completed_call)
                            if escalation:
                                self._emit("tool_escalation_requested", escalation)

                            decision = self.proxy_tool_registry.completed_call_decision(
                                hs.completed_call,
                                dropped_tool_names=dropped_tool_names,
                                repeated_read_state=repeated_read_state,
                            )

                            if decision.coercion_applied:
                                _coercion_event = "coercion_failed" if decision.coercion_error else "coercion_succeeded"
                                _coerce_tool = hs.completed_call.get("name") or ""
                                _coercion_payload = {
                                    "tool": _coerce_tool,
                                    "upstream_name": _coerce_tool,
                                    "call_id": hs.completed_call.get("call_id") or hs.completed_call.get("id") or "",
                                    "correction_applied": not bool(decision.coercion_error),
                                    "error_summary": decision.coercion_error[:200] if decision.coercion_error else "",
                                    "source": "tool_adapter",
                                }
                                if _coerce_tool == "apply_patch":
                                    _coercion_payload["apply_patch"] = inspect_apply_patch_arguments(
                                        hs.completed_call.get("arguments") or ""
                                    )
                                self._emit(_coercion_event, _coercion_payload)

                            if decision.kind == "signal":
                                # Advisory repeated-read signal: inject output upstream,
                                # continue the hop loop. No public Codex lifecycle event.
                                hs.next_input.append(decision.signal_result)
                                hs.signal_injected = True
                                self._emit("repeated_read_signal", decision.signal_metadata or {})
                                self._emit_stream_event_timing(
                                    event_type, event_received_at, event_parsed_at,
                                    time.time(), suppressed="repeated_read_signal",
                                )
                                break

                            if decision.kind == "error":
                                # Inject the error result upstream so the model sees
                                # it on the next hop. No lifecycle events emitted to
                                # Codex — the tool never ran.
                                hs.next_input.append(decision.error_result)
                                hs.error_injected = True
                                self._emit("tool_call_error", {
                                    "tool": hs.completed_call.get("name"),
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
                                    hs.completed_call,
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
                                    self.proxy_tool_registry.telemetry_payload(hs.completed_call),
                                )
                                try:
                                    result = self.proxy_tool_registry.execute(
                                        hs.completed_call,
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
                                            hs.completed_call,
                                            error=str(exc),
                                        ),
                                    )
                                    raise
                                self._emit(
                                    "tool_call_completed",
                                    self.proxy_tool_registry.telemetry_payload(
                                        hs.completed_call,
                                        result=result,
                                    ),
                                )
                                public_item = result.public_item
                                public_item["id"] = proxy_local_item_id
                                public_trace.append(public_item)
                                hs.next_input.extend(result.upstream_items)
                                sequence, forwarded_chunks, forwarded_bytes = self._emit_proxy_local_completed(
                                    hs.completed_call,
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
                            # Record this public call so within-run repeated-read
                            # tracking works for subsequent tool calls in the same run.
                            record_tool_call(hs.completed_call, repeated_read_state)
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
                            self._emit_stream_completed(requested_model, len(public_trace), rs.started_at)
                            completed_at = time.time()
                            self._emit_completed(requested_model, public_trace, summary_started, usage=rs.final_usage, response_id=rs.upstream_response_id)
                            return self._build_result(
                                requested_model,
                                rs.started_at,
                                rs.first_output_at,
                                completed_at,
                                rs.final_usage,
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
                            hs.event_lines = []
                            continue

                        _completed_call_is_proxy_local = (
                            self.proxy_tool_registry.is_proxy_local_call(hs.completed_call)
                            if hs.completed_call else False
                        )
                        if _should_suppress_proxy_local_terminal(
                            event_type,
                            payload,
                            hs.completed_call,
                            _completed_call_is_proxy_local,
                        ):
                            self._emit_stream_event_timing(
                                event_type,
                                event_received_at,
                                event_parsed_at,
                                None,
                                suppressed=self.proxy_tool_registry.terminal_suppression_reason(hs.completed_call),
                            )
                            hs.event_lines = []
                            continue

                        if event_type == "response.completed" and isinstance(payload, dict):
                            response = payload.get("response") or {}
                            if isinstance(response, dict):
                                upstream_output_items = len(_response_output_items(response))
                                completed_reasoning_chars = max(
                                    int(hs.reasoning_only_chars),
                                    _response_reasoning_chars(response),
                                )
                                completed_without_visible_answer = (
                                    not hs.visible_output_text_seen
                                    and not hs.public_item_seen
                                    and not _response_has_visible_output_text(response)
                                    and not _response_has_valid_public_item(response)
                                )
                                completed_without_answer = (
                                    completed_without_visible_answer
                                    and (
                                        completed_reasoning_chars > 0
                                        or active_repair_hop_index is not None
                                    )
                                )
                                if completed_without_answer:
                                    if repair_hops_used < max(0, self.empty_answer_repair_hops):
                                        repair_hop_index = repair_hops_used
                                        repair_hops_used += 1
                                        working_body = self._apply_empty_answer_repair(hop_body, hs.next_input)
                                        pending_repair_hop_index = repair_hop_index
                                        hs.repair_injected = True
                                        self._emit(
                                            "empty_answer_repair_started",
                                            self._empty_answer_repair_payload(
                                                requested_model,
                                                completed_reasoning_chars,
                                                upstream_output_items,
                                                rs.final_usage,
                                                repair_hop_index,
                                            ),
                                        )
                                        self._emit_stream_event_timing(
                                            event_type,
                                            event_received_at,
                                            event_parsed_at,
                                            None,
                                            suppressed="empty_answer_repair_started",
                                        )
                                        hs.event_lines = []
                                        break

                                    repair_hop_index = (
                                        active_repair_hop_index
                                        if active_repair_hop_index is not None
                                        else max(0, repair_hops_used - 1)
                                    )
                                    payload_fields = self._empty_answer_repair_payload(
                                        requested_model,
                                        completed_reasoning_chars,
                                        upstream_output_items,
                                        rs.final_usage,
                                        repair_hop_index,
                                    )
                                    self._emit("empty_answer_repair_failed", payload_fields)
                                    self._emit_stream_event_timing(
                                        event_type,
                                        event_received_at,
                                        event_parsed_at,
                                        None,
                                        suppressed="reasoning_only_completed_without_answer",
                                    )
                                    fallback_items, sequence = self._emit_reasoning_only_completed_without_answer(
                                        requested_model,
                                        summary_started,
                                        rs.final_usage,
                                        completed_reasoning_chars,
                                        public_index=len(public_trace),
                                        sequence=sequence,
                                        prefix_output=public_trace,
                                        response_id=rs.upstream_response_id,
                                    )
                                    public_trace.extend(fallback_items)
                                    self._emit_stream_completed(
                                        requested_model,
                                        len(public_trace),
                                        rs.started_at,
                                        fallback=True,
                                    )
                                    completed_at = time.time()
                                    return self._build_result(
                                        requested_model,
                                        rs.started_at,
                                        rs.first_output_at,
                                        completed_at,
                                        rs.final_usage,
                                        len(public_trace),
                                        fallback=True,
                                    )

                                if active_repair_hop_index is not None:
                                    self._emit(
                                        "empty_answer_repair_completed",
                                        self._empty_answer_repair_payload(
                                            requested_model,
                                            completed_reasoning_chars,
                                            upstream_output_items,
                                            rs.final_usage,
                                            active_repair_hop_index,
                                        ),
                                    )

                        if event_type in {"response.created", "response.in_progress"}:
                            if _should_suppress_duplicate_response_start(event_type, rs.sent_response_start):
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="duplicate_response_start",
                                )
                                hs.event_lines = []
                                continue
                            # Capture the logical response.id from the first non-suppressed
                            # response.created for this client-visible response.
                            if event_type == "response.created" and not rs.upstream_response_id:
                                if isinstance(payload, dict):
                                    _resp = payload.get("response") if isinstance(payload.get("response"), dict) else {}
                                    _rid = str(_resp.get("id") or "")
                                    if _rid:
                                        rs.upstream_response_id = _rid
                            rs.sent_response_start = True

                        if is_terminal_stream_event(event_type, payload):
                            if (
                                event_type in {"response.completed", "response.failed", "response.cancelled", "response.incomplete"}
                                and payload is None
                                and public_trace
                                and not rs.sent_terminal
                            ):
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="malformed_terminal",
                                )
                                self._emit_stream_completed(requested_model, len(public_trace), rs.started_at)
                                completed_at = time.time()
                                self._emit_completed(requested_model, public_trace, summary_started, usage=rs.final_usage, response_id=rs.upstream_response_id)
                                rs.sent_terminal = True
                                rs.sent_done = True
                                hs.watchdog_state.mark_terminal(event_parsed_at)
                                hs.event_lines = []
                                continue
                            if (
                                (event_type == "done" or payload == "[DONE]")
                                and public_trace
                                and not rs.sent_terminal
                            ):
                                self._emit_stream_event_timing(
                                    event_type,
                                    event_received_at,
                                    event_parsed_at,
                                    None,
                                    suppressed="done_without_completed",
                                )
                                self._emit_stream_completed(requested_model, len(public_trace), rs.started_at)
                                completed_at = time.time()
                                self._emit_completed(requested_model, public_trace, summary_started, usage=rs.final_usage, response_id=rs.upstream_response_id)
                                rs.sent_terminal = True
                                rs.sent_done = True
                                hs.watchdog_state.mark_terminal(event_parsed_at)
                                hs.event_lines = []
                                continue
                            forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(self._transformed_chunks(
                                event_type,
                                payload,
                                hs.event_lines,
                                summary_started,
                                output_index_offset=rs.output_index_offset,
                                prepend_output=public_trace,
                                model=requested_model,
                                response_id=rs.upstream_response_id,
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
                                rs.sent_done = True
                            else:
                                rs.sent_terminal = True
                            hs.watchdog_state.mark_terminal(event_parsed_at)
                            hs.event_lines = []
                            continue

                        forwarded_chunks, forwarded_bytes = self._write_transformed_chunks(self._transformed_chunks(
                            event_type,
                            payload,
                            hs.event_lines,
                            summary_started,
                            output_index_offset=rs.output_index_offset,
                            model=requested_model,
                            response_id=rs.upstream_response_id,
                        ))
                        self._emit_stream_event_timing(
                            event_type,
                            event_received_at,
                            event_parsed_at,
                            time.time(),
                            forwarded_chunks=forwarded_chunks,
                            forwarded_bytes=forwarded_bytes,
                        )
                        hs.event_lines = []
                    # Drain remaining SSE events to capture the server's
                    # response.completed usage before resp is closed by the
                    # finally block.  Only runs on proxy-local or error breaks
                    # where the while loop exits before the terminal events.
                    if resp is not None and (
                        hs.error_injected or hs.signal_injected
                        or (hs.completed_call and self.proxy_tool_registry.is_proxy_local_call(hs.completed_call))
                    ):
                        drained_usage = self._drain_stream_for_usage(resp)
                        if drained_usage is not None:
                            rs.final_usage = drained_usage
                finally:
                    if raw_log is not None:
                        raw_log.close()
                    restore_read_timeout_once()
                    if resp is not None:
                        resp.close()

                if hs.repair_injected:
                    continue

                if hs.error_injected or hs.signal_injected or (hs.completed_call and self.proxy_tool_registry.is_proxy_local_call(hs.completed_call)):
                    if hs.max_output_index >= 0:
                        rs.output_index_offset += hs.max_output_index + 1
                    # Experimental: carry a compact prior-turn reasoning summary
                    # forward as a lightweight context anchor for the next hop.
                    # Controlled by reasoning_carry_forward; off by default.
                    if self.reasoning_carry_forward and hs.reasoning_only_sample.strip():
                        snippet = hs.reasoning_only_sample.strip()[:300]
                        carry_msg = {
                            "type": "message",
                            "role": "user",
                            "content": [{
                                "type": "input_text",
                                "text": f"[Prior reasoning summary: {snippet}]",
                            }],
                        }
                        hs.next_input.insert(0, carry_msg)
                        self._emit("reasoning_carry_forward", {"chars": len(snippet)})
                    # Ephemeral self-management signals for the next hop.
                    hops_remaining = max_hops - (hop_index + 1)
                    hop_signal = self._hop_budget_signal_message(hops_remaining)
                    if hop_signal is not None:
                        hs.next_input.append(hop_signal)
                        self._emit("hop_budget_signal", {
                            "hops_remaining": hops_remaining,
                            "threshold": self.hop_budget_signal_threshold,
                        })
                    ctx_signal = self._context_pressure_signal_message(rs.final_usage)
                    if ctx_signal is not None:
                        hs.next_input.append(ctx_signal)
                        input_tokens = rs.final_usage.get("input_tokens") or 0
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
                    working_body["input"] = hs.next_input
                    continue

                if rs.sent_terminal and not rs.sent_done:
                    self._write_chunk(b"data: [DONE]\n\n")
                    rs.sent_done = True

                if public_trace and not rs.sent_terminal and not rs.sent_done:
                    self._emit_stream_completed(requested_model, len(public_trace), rs.started_at)
                    completed_at = time.time()
                    self._emit_completed(requested_model, public_trace, summary_started, usage=rs.final_usage, response_id=rs.upstream_response_id)
                    rs.sent_terminal = True
                    rs.sent_done = True

                completed_at = time.time()
                self._merge_manual_stream_observation(
                    hs.stream_obs_acc,
                    reasoning_only_chars=hs.reasoning_only_chars,
                    visible_output_text_seen=hs.visible_output_text_seen,
                    assistant_item_seen=hs.assistant_item_seen,
                    completed_call=hs.completed_call,
                    sent_terminal=rs.sent_terminal,
                    sent_done=rs.sent_done,
                )
                hop_obs = observation_from_dict(hs.stream_obs_acc)
                return self._build_result(
                    requested_model,
                    rs.started_at,
                    rs.first_output_at,
                    completed_at,
                    rs.final_usage,
                    len(public_trace),
                    obs=hop_obs,
                )
        except ClientStreamDisconnected as exc:
            self._emit("client_disconnected", {
                "model": requested_model,
                "phase": "stream_write",
                "error": str(exc),
                "duration_ms": round((time.time() - rs.started_at) * 1000.0, 2),
            })
            raise
        except Exception as exc:
            self._emit("stream_failed", {
                "model": requested_model,
                "error": str(exc),
                "duration_ms": round((time.time() - rs.started_at) * 1000.0, 2),
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
        self._emit_stream_completed(requested_model, len(fallback_output), rs.started_at, fallback=True)
        self._emit_completed(requested_model, fallback_output, summary_started, usage=rs.final_usage, response_id=rs.upstream_response_id)
        return self._build_result(
            requested_model,
            rs.started_at,
            rs.first_output_at,
            completed_at,
            rs.final_usage,
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
        obs: "StreamObservation | None" = None,
        emit_terminal_classified: bool = True,
    ) -> dict:
        prompt_ms = 0.0
        gen_ms = 0.0
        if isinstance(first_output_at, (int, float)) and first_output_at >= started_at:
            prompt_ms = max(0.0, (first_output_at - started_at) * 1000.0)
            gen_ms = max(0.0, (completed_at - first_output_at) * 1000.0)
        result = {
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
        if obs is not None:
            try:
                terminal = classify_stream_terminal(obs)
                result["stream_terminal"] = terminal
                if emit_terminal_classified and terminal.get("classification") != "ok":
                    self._emit("stream_terminal_classified", terminal)
            except Exception:
                pass
        return result
