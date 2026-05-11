#!/usr/bin/env python3
from dataclasses import dataclass, field

try:
    from .qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
    from .qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from .qz_tool_lifecycle import CompletedToolCallDecision, ToolContinuationResult
    from .qz_streaming import public_tool_lifecycle_event
    from .qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolRegistry, synthesize_tool_error_result
except ImportError:
    from qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
    from qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from qz_tool_lifecycle import CompletedToolCallDecision, ToolContinuationResult
    from qz_streaming import public_tool_lifecycle_event
    from qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolRegistry, synthesize_tool_error_result


DEFAULT_TOOL_REGISTRY = ToolRegistry((APPLY_PATCH_TOOL_ADAPTER, WEB_SEARCH_TOOL_ADAPTER))


@dataclass
class ProxyToolExecutionContext:
    request_id: str = ""
    counters: dict = field(default_factory=dict)
    seen_signatures: set = field(default_factory=set)


class ProxyLocalToolExecutor:
    function_name = ""
    lifecycle = None

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
    lifecycle = WEB_SEARCH_TOOL_ADAPTER.lifecycle

    def __init__(self, web_runtime):
        self.web_runtime = web_runtime

    def started_public_item(self, call: dict, public_index: int) -> dict:
        item_id = call.get("id") or call.get("call_id") or f"{self.lifecycle.name}_local_{public_index}"
        return {
            "id": item_id,
            "type": self.lifecycle.public_item_type,
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
    def __init__(self, executors, tool_registry=None):
        self.tool_registry = tool_registry or DEFAULT_TOOL_REGISTRY
        self._executors = {
            executor.function_name: executor
            for executor in executors
            if executor.function_name
        }
        self.function_names = frozenset(self._executors)
        self.specs = tuple(
            executor.lifecycle
            for executor in self._executors.values()
            if executor.lifecycle is not None
        )
        self.max_continuation_hops = max(
            (int(getattr(spec, "continuation_hops", 0) or 0) for spec in self.specs),
            default=0,
        )

    def spec_for_call(self, call: dict):
        return self.executor_for_call(call).lifecycle

    def telemetry_payload(
        self,
        call: dict,
        result: ToolContinuationResult | None = None,
        error: str = "",
    ) -> dict:
        spec = self.spec_for_call(call)
        payload = {
            "tool": spec.telemetry_name or call.get("name") or "",
            "function_name": call.get("name") or "",
            "call_id": call.get("call_id") or call.get("id") or "",
            "execution": spec.execution,
            "public_item_type": spec.public_item_type,
        }
        if result is not None:
            payload.update({
                "sources": len(result.sources),
                "upstream_items": len(result.upstream_items),
            })
        if error:
            payload["error"] = error
        return payload

    def terminal_suppression_reason(self, call: dict) -> str:
        spec = self.spec_for_call(call)
        tool_name = spec.telemetry_name or call.get("name") or "proxy_local"
        return f"{tool_name}_terminal"

    def lifecycle_event_chunks(
        self,
        call: dict,
        stage: str,
        item_id: str,
        output_index: int,
        sequence_start: int = 0,
    ):
        spec = self.spec_for_call(call)
        allowed_stages = tuple(spec.lifecycle_start_stages) + tuple(spec.lifecycle_done_stages)
        if not spec.lifecycle_event_prefix:
            return [], sequence_start
        return public_tool_lifecycle_event(
            spec.lifecycle_event_prefix,
            allowed_stages,
            stage,
            item_id,
            output_index,
            sequence_start,
        )

    def lifecycle_start_event_chunks(self, call: dict, item_id: str, output_index: int, sequence_start: int = 0):
        chunks = []
        sequence = sequence_start
        for stage in self.spec_for_call(call).lifecycle_start_stages:
            stage_chunks, sequence = self.lifecycle_event_chunks(call, stage, item_id, output_index, sequence)
            chunks.extend(stage_chunks)
        return chunks, sequence

    def lifecycle_done_event_chunks(self, call: dict, item_id: str, output_index: int, sequence_start: int = 0):
        chunks = []
        sequence = sequence_start
        for stage in self.spec_for_call(call).lifecycle_done_stages:
            stage_chunks, sequence = self.lifecycle_event_chunks(call, stage, item_id, output_index, sequence)
            chunks.extend(stage_chunks)
        return chunks, sequence

    def continuation_limit_message(self) -> str:
        names = sorted(self.function_names)
        if not names:
            return "I stopped the proxy-local tool loop after hitting the continuation safety limit."
        return (
            "I stopped the proxy-local tool loop after hitting the continuation "
            f"safety limit for {', '.join(names)}."
        )

    def is_proxy_local_call(self, call: dict) -> bool:
        return (
            isinstance(call, dict)
            and call.get("type") == "function_call"
            and call.get("name") in self._executors
        )

    def completed_call_decision(
        self,
        call: dict,
        apply_patch_output_style: str,
        dropped_tool_names: frozenset[str] = frozenset(),
    ) -> CompletedToolCallDecision:
        name = call.get("name") if isinstance(call, dict) else None

        # 1. Dropped tool: model called something explicitly removed from its
        #    declaration list. Inject a specific error so it can try again.
        if name and name in dropped_tool_names:
            error = synthesize_tool_error_result(
                call,
                f"Tool '{name}' is not available in this session. "
                "It was removed from the tool list before this request. "
                "Use a different tool or approach.",
            )
            return CompletedToolCallDecision(kind="error", call=call, error_result=error)

        # 2. Proxy-local executor: coerce via the executor if it supports it,
        #    otherwise pass through (in-band errors handle runtime failures).
        if self.is_proxy_local_call(call):
            executor = self._executors.get(name)
            if executor is not None and hasattr(executor, "coerce"):
                coercion = executor.coerce(call)
                if not coercion.succeeded():
                    error = synthesize_tool_error_result(call, coercion.error_message or "")
                    return CompletedToolCallDecision(kind="error", call=call, error_result=error)
                corrected = call if not coercion.corrected_arguments else dict(call, arguments=coercion.corrected_arguments)
                return CompletedToolCallDecision(kind="proxy_local", call=corrected)
            return CompletedToolCallDecision(kind="proxy_local", call=call)

        # 3. Protocol adapter (e.g. apply_patch): coerce then convert shape.
        if self.tool_registry.spec_for_name(name):
            coercion = self.tool_registry.coerce_call(call)
            if not coercion.succeeded():
                error = synthesize_tool_error_result(call, coercion.error_message or "")
                return CompletedToolCallDecision(kind="error", call=call, error_result=error)
            corrected = call if not coercion.corrected_arguments else dict(call, arguments=coercion.corrected_arguments)
            public_item = self.tool_registry.output_to_codex(corrected, apply_patch_output_style)
            return CompletedToolCallDecision(
                kind="public",
                call=corrected,
                public_item=public_item if public_item is not None else corrected,
            )

        # 4. Known Codex-native tool: pass through as public, Codex handles it.
        if name in CODEX_NATIVE_TOOL_NAMES:
            public_item = self.tool_registry.output_to_codex(call, apply_patch_output_style)
            return CompletedToolCallDecision(
                kind="public",
                call=call,
                public_item=public_item if public_item is not None else call,
            )

        # 5. Unknown tool: inject generic error so the model can try something else.
        error = synthesize_tool_error_result(
            call,
            f"Tool '{name}' is not recognised by the proxy and cannot be executed. "
            "Check the available tools and retry with a supported tool name.",
        )
        return CompletedToolCallDecision(kind="error", call=call, error_result=error)

    def continuation_result(
        self,
        decision: CompletedToolCallDecision,
        context: ProxyToolExecutionContext | None = None,
    ) -> ToolContinuationResult:
        if decision.kind == "error":
            # Return the error result as an upstream item so the model sees it.
            # No public display item — the tool never ran.
            return ToolContinuationResult(
                public_item=decision.error_result,
                upstream_items=(decision.error_result,),
            )

        if decision.kind == "proxy_local":
            if context is None:
                raise ValueError("context is required for proxy-local tool calls")
            return self.execute(decision.call, context)

        if decision.public_item is None:
            raise ValueError("public tool call decision missing public_item")
        return ToolContinuationResult(public_item=decision.public_item)

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
