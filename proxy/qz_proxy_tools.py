#!/usr/bin/env python3
from dataclasses import dataclass, field

try:
    from .qz_tool_lifecycle import ToolContinuationResult
except ImportError:
    from qz_tool_lifecycle import ToolContinuationResult


@dataclass
class ProxyToolExecutionContext:
    request_id: str = ""
    counters: dict = field(default_factory=dict)
    seen_signatures: set = field(default_factory=set)


class ProxyLocalToolExecutor:
    function_name = ""

    def is_call(self, call: dict) -> bool:
        return (
            isinstance(call, dict)
            and call.get("type") == "function_call"
            and call.get("name") == self.function_name
        )

    def started_public_item(self, call: dict, public_index: int) -> dict:
        raise NotImplementedError

    def execute(self, call: dict, context: ProxyToolExecutionContext) -> ToolContinuationResult:
        raise NotImplementedError


class WebSearchProxyToolExecutor(ProxyLocalToolExecutor):
    function_name = "web_search"

    def __init__(self, web_runtime):
        self.web_runtime = web_runtime

    def started_public_item(self, call: dict, public_index: int) -> dict:
        item_id = call.get("id") or call.get("call_id") or f"web_search_local_{public_index}"
        return {
            "id": item_id,
            "type": "web_search_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }

    def execute(self, call: dict, context: ProxyToolExecutionContext) -> ToolContinuationResult:
        public_item, tool_output_item, sources = self.web_runtime.execute_web_search_call(
            call,
            context.counters,
            context.seen_signatures,
            request_id=context.request_id,
        )
        return ToolContinuationResult(
            public_item=public_item,
            upstream_items=(call, tool_output_item),
            sources=tuple(sources or ()),
        )


class ProxyLocalToolRegistry:
    def __init__(self, executors):
        self._executors = {
            executor.function_name: executor
            for executor in executors
            if executor.function_name
        }
        self.function_names = frozenset(self._executors)

    def is_proxy_local_call(self, call: dict) -> bool:
        return (
            isinstance(call, dict)
            and call.get("type") == "function_call"
            and call.get("name") in self._executors
        )

    def executor_for_call(self, call: dict) -> ProxyLocalToolExecutor:
        if not self.is_proxy_local_call(call):
            raise KeyError(f"no proxy-local executor for tool call: {call.get('name') if isinstance(call, dict) else None}")
        return self._executors[call.get("name")]

    def started_public_item(self, call: dict, public_index: int) -> dict:
        return self.executor_for_call(call).started_public_item(call, public_index)

    def execute(self, call: dict, context: ProxyToolExecutionContext) -> ToolContinuationResult:
        return self.executor_for_call(call).execute(call, context)


def make_proxy_local_tool_registry(web_runtime) -> ProxyLocalToolRegistry:
    return ProxyLocalToolRegistry([
        WebSearchProxyToolExecutor(web_runtime),
    ])
