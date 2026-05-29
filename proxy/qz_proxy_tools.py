#!/usr/bin/env python3
from dataclasses import dataclass, field

try:
    from .qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
    from .qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from .qz_tool_lifecycle import CompletedToolCallDecision, ToolContinuationResult

    from .qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolRegistry, synthesize_tool_error_result
    from .qz_feedback import render_advisory_output
    from .qz_file_signal import RepeatedReadState, repeated_read_signal
    from .qz_native_signal import NativeToolAdvisoryState, check_native_advisories
except ImportError:
    from qz_tool_apply_patch import APPLY_PATCH_TOOL_ADAPTER
    from qz_tool_web import WEB_SEARCH_TOOL_ADAPTER
    from qz_tool_lifecycle import CompletedToolCallDecision, ToolContinuationResult

    from qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolRegistry, synthesize_tool_error_result
    from qz_feedback import render_advisory_output
    from qz_file_signal import RepeatedReadState, repeated_read_signal
    from qz_native_signal import NativeToolAdvisoryState, check_native_advisories


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

    def coerce(self, call: dict):
        return WEB_SEARCH_TOOL_ADAPTER.coerce(call)

    def started_public_item(self, call: dict, public_index: int) -> dict:
        item_id = call.get("id") or call.get("call_id") or f"{self.lifecycle.name}_local_{public_index}"
        item = {
            "id": item_id,
            "type": self.lifecycle.public_item_type,
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }
        # Include the action so Codex renders the query detail immediately:
        # "Searching the web: <query>" instead of a blank "Searching the web".
        # Codex parses web_search_call action using WebSearchAction:
        #   Search { query } → shows the query string
        #   OpenPage { url } → shows the URL
        #   FindInPage { url, pattern } → shows pattern + URL
        try:
            import json as _j
            args = _j.loads(call.get("arguments") or "{}")
            if isinstance(args, dict):
                action_type = str(args.get("action") or "search")
                if action_type in ("search", "retrieve", "capabilities"):
                    item["action"] = {"type": "search", "query": args.get("query") or ""}
                elif action_type == "open_page":
                    item["action"] = {"type": "open_page", "url": args.get("url") or ""}
                elif action_type == "find_in_page":
                    item["action"] = {
                        "type": "find_in_page",
                        "url": args.get("url") or "",
                        "pattern": args.get("query") or args.get("pattern") or "",
                    }
        except Exception:
            pass
        return item

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

    def is_proxy_local_name(self, name: str) -> bool:
        """True when the tool name is handled proxy-locally (e.g. web_search).

        Used to skip redundant working-status injection for tools that already
        emit a visible output_item.added before execution starts.
        """
        return isinstance(name, str) and name in self._executors

    def completed_call_decision(
        self,
        call: dict,
        dropped_tool_names: frozenset[str] = frozenset(),
        repeated_read_state: "RepeatedReadState | None" = None,
        native_advisory_state: "NativeToolAdvisoryState | None" = None,
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
                    return CompletedToolCallDecision(
                        kind="error", call=call, error_result=error,
                        coercion_applied=True,
                        coercion_error=(coercion.error_message or "")[:200],
                    )
                corrected = call if not coercion.corrected_arguments else dict(call, arguments=coercion.corrected_arguments)
                return CompletedToolCallDecision(
                    kind="proxy_local", call=corrected,
                    coercion_applied=True,
                )
            return CompletedToolCallDecision(kind="proxy_local", call=call)

        # 3. Protocol adapter (e.g. apply_patch): coerce then convert shape.
        if self.tool_registry.spec_for_name(name):
            coercion = self.tool_registry.coerce_call(call)
            if not coercion.succeeded():
                error = synthesize_tool_error_result(call, coercion.error_message or "")
                return CompletedToolCallDecision(
                    kind="error", call=call, error_result=error,
                    coercion_applied=True,
                    coercion_error=(coercion.error_message or "")[:200],
                )
            corrected = call if not coercion.corrected_arguments else dict(call, arguments=coercion.corrected_arguments)
            public_item = self.tool_registry.output_to_codex(corrected)
            return CompletedToolCallDecision(
                kind="public",
                call=corrected,
                public_item=public_item if public_item is not None else corrected,
                coercion_applied=True,
            )

        # 4. Known Codex-native tool: check for repeated-read signal first.
        #    If the state indicates this read was already seen, inject an advisory
        #    output to the model instead of passing the call through.
        #    First read and allowed-after-warning cases pass through normally.
        if name in CODEX_NATIVE_TOOL_NAMES:
            if repeated_read_state is not None:
                rr_decision = repeated_read_signal(call, repeated_read_state)
                if rr_decision.should_signal:
                    advisory = render_advisory_output(call, rr_decision.message)
                    metadata = {
                        "tool": name or "unknown",
                        "call_id": str(call.get("call_id") or call.get("id") or ""),
                        "paths": sorted(rr_decision.paths),
                        "action": rr_decision.action,
                        "scope": rr_decision.scope,
                    }
                    repeated_read_state.warned_paths.update(rr_decision.paths)
                    return CompletedToolCallDecision(
                        kind="signal",
                        call=call,
                        signal_result=advisory,
                        signal_metadata=metadata,
                    )
            
            if native_advisory_state is not None:
                nat_decision = check_native_advisories(call, native_advisory_state)
                if nat_decision and nat_decision.should_signal:
                    advisory = render_advisory_output(call, nat_decision.message)
                    return CompletedToolCallDecision(
                        kind="signal",
                        call=call,
                        signal_result=advisory,
                        signal_metadata=nat_decision.metadata,
                    )
            
            public_item = self.tool_registry.output_to_codex(call)
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


def make_proxy_local_tool_registry(web_runtime, db=None) -> ProxyLocalToolRegistry:
    """Build the default proxy-local tool registry.

    Includes web_search always. Includes BrainCase tools (braincase.render,
    braincase.recall) when QZ_BRAINCASE_TOOLS_ENABLED is set. All other
    BrainCase tools (write/update/search/inspect) are never included.
    """
    executors = [WebSearchProxyToolExecutor(web_runtime)]
    try:
        from .qz_braincase_tools import make_braincase_tool_executors
    except ImportError:
        try:
            from qz_braincase_tools import make_braincase_tool_executors
        except ImportError:
            make_braincase_tool_executors = None  # type: ignore[assignment]
    if make_braincase_tool_executors is not None:
        executors.extend(make_braincase_tool_executors(db=db))
    return ProxyLocalToolRegistry(executors)
