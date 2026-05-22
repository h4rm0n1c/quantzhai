#!/usr/bin/env python3
from dataclasses import dataclass

try:
    from .qz_streaming import StreamedFunctionCallAssembler, is_function_call_stream_event
except ImportError:
    from qz_streaming import StreamedFunctionCallAssembler, is_function_call_stream_event


@dataclass(frozen=True)
class CompletedToolCallDecision:
    kind: str          # "proxy_local" | "public" | "error" | "signal"
    call: dict
    public_item: dict | None = None
    error_result: dict | None = None   # set when kind == "error"
    signal_result: dict | None = None  # set when kind == "signal" (advisory function_call_output)
    signal_metadata: dict | None = None  # telemetry payload for repeated_read_signal
    coercion_applied: bool = False     # True when coerce() was called for this decision
    coercion_error: str = ""           # Non-empty when coerce() returned a failure


@dataclass(frozen=True)
class ToolContinuationResult:
    public_item: dict
    upstream_items: tuple[dict, ...] = ()
    sources: tuple[dict, ...] = ()


class ToolHistoryReplayFilter:
    """Drops malformed historical tool items before upstream replay."""

    def __init__(self):
        self.dropped_invalid_call_ids = set()

    def should_drop(self, item: dict) -> bool:
        if not isinstance(item, dict):
            return False

        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id") or item.get("id")
            arguments = item.get("arguments")
            if not isinstance(arguments, str) or not arguments.strip():
                if call_id:
                    self.dropped_invalid_call_ids.add(call_id)
                return True
            return False

        if item_type == "function_call_output":
            call_id = item.get("call_id")
            output = item.get("output")
            output_text = output if isinstance(output, str) else ""
            if call_id in self.dropped_invalid_call_ids:
                return True
            if output_text.strip().startswith("failed to parse function arguments:"):
                if call_id:
                    self.dropped_invalid_call_ids.add(call_id)
                return True
            return False

        return False


def function_call_key(payload):
    if not isinstance(payload, dict):
        return None
    item_id = payload.get("item_id")
    if item_id:
        return item_id
    item = payload.get("item")
    if isinstance(item, dict):
        return item.get("id") or item.get("call_id")
    output_index = payload.get("output_index")
    if output_index is not None:
        return f"output:{output_index}"
    return None


def public_tool_item_from_function_call(call: dict, output_style: str, tool_registry=None):
    if tool_registry is None:
        return call
    public_item = tool_registry.output_to_codex(call, output_style)
    return public_item if public_item is not None else call


def is_proxy_local_function_call(call: dict, proxy_local_tool_names) -> bool:
    return (
        isinstance(call, dict)
        and call.get("type") == "function_call"
        and call.get("name") in proxy_local_tool_names
    )


def completed_tool_call_decision(
    call: dict,
    output_style: str,
    proxy_local_tool_names,
    tool_registry=None,
) -> CompletedToolCallDecision:
    if is_proxy_local_function_call(call, proxy_local_tool_names):
        return CompletedToolCallDecision(kind="proxy_local", call=call)
    return CompletedToolCallDecision(
        kind="public",
        call=call,
        public_item=public_tool_item_from_function_call(call, output_style, tool_registry),
    )


def tool_continuation_result(decision: CompletedToolCallDecision, proxy_local_executor=None) -> ToolContinuationResult:
    if decision.kind == "proxy_local":
        if proxy_local_executor is None:
            raise ValueError("proxy_local_executor is required for proxy-local tool calls")
        public_item, tool_output_item, sources = proxy_local_executor(decision.call)
        return ToolContinuationResult(
            public_item=public_item,
            upstream_items=(decision.call, tool_output_item),
            sources=tuple(sources or ()),
        )

    if decision.public_item is None:
        raise ValueError("public tool call decision missing public_item")
    return ToolContinuationResult(public_item=decision.public_item)


class StreamToolCallState:
    """Owns private streamed function-call assembly and stall accounting."""

    def __init__(self):
        self.assembler = StreamedFunctionCallAssembler()
        self.started_at = None
        self.delta_count = 0
        self.call_name = ""

    def observe(self, event_type, payload, received_at):
        completed = self.assembler.observe(event_type, payload)
        if not is_function_call_stream_event(event_type, payload):
            return completed

        if self.started_at is None:
            self.started_at = received_at
        if (
            event_type == "response.output_item.added"
            and isinstance(payload, dict)
            and isinstance(payload.get("item"), dict)
        ):
            item = payload["item"]
            self.call_name = item.get("name") or self.call_name
        if event_type == "response.function_call_arguments.delta":
            self.delta_count += 1
        return completed

    def abort_reason(self, now, timeout_s: float, delta_limit: int):
        if self.started_at is None:
            return ""
        elapsed = max(0.0, now - self.started_at)
        if timeout_s >= 0 and elapsed > timeout_s:
            return "timeout"
        if delta_limit >= 0 and self.delta_count > delta_limit:
            return "delta_limit"
        return ""
