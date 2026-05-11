#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

# Tool names that Codex handles natively and should be passed through unchanged.
# Do NOT inject coercion errors for these — Codex will execute them itself.
CODEX_NATIVE_TOOL_NAMES: frozenset[str] = frozenset({
    "exec_command",
    "write_stdin",
    "shell_command",
    "computer",
})


@dataclass(frozen=True)
class ToolLifecycleSpec:
    name: str
    execution: str = "protocol_adapter"
    public_item_type: str = ""
    telemetry_name: str = ""
    continuation_hops: int = 0
    lifecycle_event_prefix: str = ""
    lifecycle_start_stages: tuple[str, ...] = ()
    lifecycle_done_stages: tuple[str, ...] = ()

    def __post_init__(self):
        if self.execution not in {"protocol_adapter", "proxy_local"}:
            raise ValueError(f"unsupported tool execution mode: {self.execution}")

    @property
    def emits_continuation(self) -> bool:
        return self.execution == "proxy_local" and self.continuation_hops > 0


@dataclass
class ToolCoercionResult:
    """Result of a tool's coerce() call.

    Exactly one field is set:
    - corrected_arguments: coercion succeeded; re-run with this JSON string.
    - error_message: coercion failed; inject this as an error tool result so
      the model can see what went wrong and retry.
    """
    corrected_arguments: str | None = None
    error_message: str | None = None

    def succeeded(self) -> bool:
        return self.corrected_arguments is not None


def _coercion_error(name: str, reason: str = "") -> ToolCoercionResult:
    """Generic coercion failure result for use as a default."""
    detail = f": {reason}" if reason else "."
    return ToolCoercionResult(
        error_message=(
            f"Tool call for '{name}' could not be completed by the proxy{detail} "
            "Check your arguments and retry, or use a different tool."
        )
    )


def synthesize_tool_error_result(call: dict, message: str) -> dict:
    """Build a function_call_output carrying an error message.

    Used to inject a proxy-generated error back to the model when a tool
    call cannot be executed or coerced into a valid form.
    """
    call_id = (
        call.get("call_id") or call.get("id")
        if isinstance(call, dict) else None
    ) or f"err_{int(time.time())}"
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"ok": False, "error": message}, ensure_ascii=False),
    }


def function_tool(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


class ToolAdapter(Protocol):
    upstream_name: str
    lifecycle: ToolLifecycleSpec

    def accepts_tool(self, tool: dict) -> bool:
        ...

    def to_upstream_tool(self, tool: dict) -> dict:
        ...

    def normalize_tool_choice(self, tool_choice: dict):
        ...

    def input_to_upstream(self, item: dict):
        ...

    def output_to_codex(self, item: dict, output_style: str = "native"):
        ...

    def coerce(self, call: dict) -> ToolCoercionResult:
        """Attempt to coerce a malformed function_call into a valid one.

        Return ToolCoercionResult with corrected_arguments on success, or
        error_message on failure. The default returns a generic error.
        Adapters override this to provide tool-specific argument recovery
        and targeted error messages.
        """
        name = call.get("name", "unknown") if isinstance(call, dict) else "unknown"
        return _coercion_error(name)


@dataclass(frozen=True)
class ToolRegistry:
    adapters: tuple[ToolAdapter, ...]

    def specs(self) -> tuple[ToolLifecycleSpec, ...]:
        return tuple(
            adapter.lifecycle
            for adapter in self.adapters
            if isinstance(getattr(adapter, "lifecycle", None), ToolLifecycleSpec)
        )

    def spec_for_name(self, name: str):
        for spec in self.specs():
            if spec.name == name:
                return spec
        return None

    def adapter_for_tool(self, tool: dict):
        for adapter in self.adapters:
            if adapter.accepts_tool(tool):
                return adapter
        return None

    def normalize_tool_choice(self, tool_choice: dict):
        for adapter in self.adapters:
            normalized = adapter.normalize_tool_choice(tool_choice)
            if normalized is not None:
                return normalized
        return None

    def input_to_upstream(self, item: dict):
        for adapter in self.adapters:
            normalized = adapter.input_to_upstream(item)
            if normalized is not None:
                return normalized
        return None

    def output_to_codex(self, item: dict, output_style: str = "native"):
        for adapter in self.adapters:
            normalized = adapter.output_to_codex(item, output_style)
            if normalized is not None:
                return normalized
        return None

    def coerce_call(self, call: dict) -> ToolCoercionResult:
        """Run the first matching adapter's coerce() against this function_call.

        If no adapter matches, returns the generic error fallback.
        """
        if not isinstance(call, dict):
            return _coercion_error("unknown")
        for adapter in self.adapters:
            if hasattr(adapter, "coerce") and hasattr(adapter, "accepts_tool"):
                # Check if this adapter owns the call's tool name
                name = call.get("name")
                spec = getattr(adapter, "lifecycle", None)
                if isinstance(spec, ToolLifecycleSpec) and spec.name == name:
                    return adapter.coerce(call)
        name = call.get("name", "unknown") if isinstance(call, dict) else "unknown"
        return _coercion_error(name)

    def output_items_to_codex(self, items, output_style: str = "native"):
        if not isinstance(items, list):
            return items
        out = []
        for item in items:
            normalized = self.output_to_codex(item, output_style)
            out.append(normalized if normalized is not None else item)
        return out
