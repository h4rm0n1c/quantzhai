#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Tool names that Codex handles natively and should be passed through unchanged.
#
# Source-backed as function_call items (codex-rs/core/src/tools/handlers/):
#
#   exec_command    — unified_exec handler  (ToolName::plain("exec_command"))
#   write_stdin     — unified_exec handler  (ToolName::plain("write_stdin"))
#   shell_command   — shell_command handler (ToolName::plain("shell_command"))
#   update_plan     — plan handler          (ToolName::plain("update_plan"))
#   request_user_input — request_user_input handler
#                       (REQUEST_USER_INPUT_TOOL_NAME = "request_user_input")
#   request_permissions — request_permissions handler
#                         (ToolName::plain("request_permissions"))
#   view_image      — view_image handler    (ToolName::plain("view_image"))
#   get_goal        — goal/get_goal handler  (GET_GOAL_TOOL_NAME = "get_goal")
#   create_goal     — goal/create_goal handler (CREATE_GOAL_TOOL_NAME = "create_goal")
#   update_goal     — goal/update_goal handler (UPDATE_GOAL_TOOL_NAME = "update_goal")
#
# Audit SHA: 46f30d02828bd4c52827e5f0482a6f2a982cce5b
# See docs/codex-source-tool-contract.md, docs/codex-source-tool-inventory.md.
#
# NOT included here:
#   apply_patch  — custom_tool_call item (Freeform handler), not function_call
#   web_search   — web_search_call item, proxy_local execution
#   local_shell  — LocalShell item (not function_call); separate contract slice
#   tool_search  — ToolSearchCall item; separate contract slice
#   image_generation — ImageGenerationCall item; document only
#   shell / container.exec — function_call handlers exist, but model-facing
#                             usage needs further audit; next audit slice
#   computer     — reserved validation namespace, not a handler
#
# Do NOT inject coercion errors for names in this set — Codex executes them.
CODEX_NATIVE_TOOL_NAMES: frozenset[str] = frozenset({
    "exec_command",
    "write_stdin",
    "shell_command",
    "update_plan",
    "request_user_input",
    "request_permissions",
    "view_image",
    "get_goal",
    "create_goal",
    "update_goal",
})


@dataclass(frozen=True)
class ToolLifecycleSpec:
    name: str
    execution: str = "protocol_adapter"
    public_item_type: str = ""
    telemetry_name: str = ""
    continuation_hops: int = 0
    # lifecycle_event_prefix, lifecycle_start_stages, lifecycle_done_stages were removed.
    # They generated fake response.<tool>_call.* Codex-visible SSE events that no
    # current Codex client actually parses.  Codex-visible tool lifecycle is
    # output_item.added / output_item.done / completed only.
    # See docs/codex-source-tool-contract.md.

    def __post_init__(self):
        if self.execution not in {"protocol_adapter", "proxy_local"}:
            raise ValueError(f"unsupported tool execution mode: {self.execution}")

    @property
    def emits_continuation(self) -> bool:
        return self.execution == "proxy_local" and self.continuation_hops > 0


@dataclass
class ToolCoercionResult:
    """Result of a tool's coerce() call.

    Exactly one field must be set:
    - corrected_arguments: coercion succeeded; re-run with this JSON string.
    - error_message: coercion failed; inject this as an error tool result so
      the model can see what went wrong and retry.
    """
    corrected_arguments: str | None = None
    error_message: str | None = None

    def __post_init__(self):
        has_args = self.corrected_arguments is not None
        has_err = self.error_message is not None
        if has_args and has_err:
            raise ValueError("ToolCoercionResult: set corrected_arguments OR error_message, not both")
        if not has_args and not has_err:
            raise ValueError("ToolCoercionResult: exactly one of corrected_arguments or error_message must be set")
        if has_err and not self.error_message:
            raise ValueError("ToolCoercionResult: error_message must be non-empty when set")

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

    Compatibility wrapper — delegates to qz_feedback.render_coercion_error(),
    which is the canonical implementation.  Do not remove this function;
    qz_proxy_tools.py and other callers import it from here.

    Output is byte-for-byte identical to render_coercion_error(call, message).
    """
    try:
        from .qz_feedback import render_coercion_error
    except ImportError:
        from qz_feedback import render_coercion_error
    return render_coercion_error(call, message)


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

    def adapter_for_name(self, name: str):
        """Return the adapter whose upstream_name matches name, or None."""
        if not name:
            return None
        for adapter in self.adapters:
            if getattr(adapter, "upstream_name", None) == name:
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
