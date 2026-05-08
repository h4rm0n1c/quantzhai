#!/usr/bin/env python3
from dataclasses import dataclass

try:
    from .qz_responses import normalize_apply_patch_output_for_codex
    from .qz_streaming import StreamedFunctionCallAssembler, is_function_call_stream_event
except ImportError:
    from qz_responses import normalize_apply_patch_output_for_codex
    from qz_streaming import StreamedFunctionCallAssembler, is_function_call_stream_event


PROXY_LOCAL_FUNCTION_TOOLS = frozenset({"web_search"})


@dataclass(frozen=True)
class CompletedToolCallDecision:
    kind: str
    call: dict
    public_item: dict | None = None


@dataclass(frozen=True)
class ToolContinuationResult:
    public_item: dict
    upstream_items: tuple[dict, ...] = ()
    sources: tuple[dict, ...] = ()


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


def public_tool_item_from_function_call(call: dict, apply_patch_output_style: str):
    if call.get("name") == "apply_patch":
        return normalize_apply_patch_output_for_codex([call], apply_patch_output_style)[0]
    return call


def is_proxy_local_function_call(call: dict) -> bool:
    return (
        isinstance(call, dict)
        and call.get("type") == "function_call"
        and call.get("name") in PROXY_LOCAL_FUNCTION_TOOLS
    )


def completed_tool_call_decision(call: dict, apply_patch_output_style: str) -> CompletedToolCallDecision:
    if is_proxy_local_function_call(call):
        return CompletedToolCallDecision(kind="proxy_local", call=call)
    return CompletedToolCallDecision(
        kind="public",
        call=call,
        public_item=public_tool_item_from_function_call(call, apply_patch_output_style),
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
