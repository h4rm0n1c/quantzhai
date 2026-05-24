#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from .qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from .qz_runtime_io import capture_enabled, write_dual_capture
    from .qz_tool_apply_patch import ensure_apply_patch_tool_policy
except ImportError:
    from qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from qz_runtime_io import capture_enabled, write_dual_capture
    from qz_tool_apply_patch import ensure_apply_patch_tool_policy


EXEC_SESSION_OUTPUT_RE = re.compile(r"(?i)\b(?:session_id|session id)\s*[:=]\s*\d+\b")
FILE_EDIT_TOOL_HINT = (
    " For file creation or edits, use apply_patch when that tool is available; "
    "do not use shell redirection, heredocs, or write_stdin for file edits."
)
WRITE_STDIN_TOOL_HINT = (
    " Only use this with an existing running session id from prior exec_command output. "
    "Do not invent session ids, and do not use write_stdin for file creation or edits."
)


@dataclass(frozen=True)
class ToolRequestNormalizationReport:
    translated: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    deduped: tuple[str, ...] = ()
    has_exec_session: bool = False
    tool_choice_normalized: bool = False
    tool_choice_forced_auto: bool = False

    def capture_notes(self) -> str:
        notes = []
        if self.translated:
            notes.append("translated: " + ", ".join(self.translated))
        if self.replaced:
            notes.append("replaced: " + ", ".join(self.replaced))
        if self.deduped:
            notes.append("deduped: " + ", ".join(self.deduped))
        if self.dropped:
            notes.append("dropped: " + ", ".join(self.dropped))
        return "\n".join(notes) + ("\n" if notes else "")


def input_has_exec_session(input_items) -> bool:
    if not isinstance(input_items, list):
        return False

    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        output = item.get("output")
        if not isinstance(output, str):
            continue
        if EXEC_SESSION_OUTPUT_RE.search(output):
            return True
    return False


def append_tool_hint(tool: dict, hint: str) -> dict:
    description = tool.get("description")
    if not isinstance(description, str):
        description = ""
    if hint.strip() in description:
        return dict(tool)
    out = dict(tool)
    out["description"] = (description.rstrip() + hint).strip()
    return out


def normalize_tool_request_for_llamacpp(
    body: dict,
    tool_registry=DEFAULT_TOOL_REGISTRY,
    *,
    write_captures: bool = True,
) -> ToolRequestNormalizationReport:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return ToolRequestNormalizationReport()

    ensure_apply_patch_tool_policy(body)

    clean = []
    dropped = []
    translated = []
    replaced = []
    deduped = []
    seen_names: set = set()
    has_exec_session = input_has_exec_session(body.get("input"))

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        tool_type = tool.get("type")
        tool_name = tool.get("name") or tool.get("server_label") or tool_type

        if tool_type == "function":
            if tool_name == "write_stdin" and not has_exec_session:
                dropped.append("write_stdin(no live exec session)")
                continue
            if tool_name == "exec_command":
                if tool_name not in seen_names:
                    seen_names.add(tool_name)
                    clean.append(append_tool_hint(tool, FILE_EDIT_TOOL_HINT))
                else:
                    deduped.append(tool_name)
                continue
            if tool_name == "write_stdin":
                if tool_name not in seen_names:
                    seen_names.add(tool_name)
                    clean.append(append_tool_hint(tool, WRITE_STDIN_TOOL_HINT))
                else:
                    deduped.append(tool_name)
                continue
            # If a proxy-local adapter owns this function name, replace client schema.
            name_adapter = tool_registry.adapter_for_name(tool_name)
            if name_adapter:
                if tool_name not in seen_names:
                    seen_names.add(tool_name)
                    clean.append(name_adapter.to_upstream_tool(tool))
                    replaced.append(tool_name)
                else:
                    deduped.append(tool_name)
                continue
            if tool_name in seen_names:
                deduped.append(tool_name)
                continue
            seen_names.add(tool_name)
            clean.append(tool)
            continue

        tool_adapter = tool_registry.adapter_for_tool(tool)
        if tool_adapter:
            if tool_adapter.upstream_name not in seen_names:
                seen_names.add(tool_adapter.upstream_name)
                clean.append(tool_adapter.to_upstream_tool(tool))
                translated.append(tool_adapter.upstream_name)
            else:
                deduped.append(tool_adapter.upstream_name)
            continue

        dropped.append(str(tool_name))

    body["tools"] = clean

    tool_choice_normalized = False
    tool_choice_forced_auto = False
    if isinstance(body.get("tool_choice"), dict):
        tool_choice_type = body["tool_choice"].get("type")
        adapted_choice = tool_registry.normalize_tool_choice(body["tool_choice"])
        if adapted_choice is not None:
            body["tool_choice"] = adapted_choice
            tool_choice_normalized = True
        elif tool_choice_type not in (None, "function"):
            body["tool_choice"] = "auto"
            tool_choice_forced_auto = True

    report = ToolRequestNormalizationReport(
        translated=tuple(translated),
        dropped=tuple(dropped),
        replaced=tuple(replaced),
        deduped=tuple(deduped),
        has_exec_session=has_exec_session,
        tool_choice_normalized=tool_choice_normalized,
        tool_choice_forced_auto=tool_choice_forced_auto,
    )
    # Store the raw dropped names (without reason suffixes) in metadata so
    # the response routing path can check them when a function_call arrives
    # for a tool that was removed from the declaration.
    if dropped:
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["qz_dropped_tool_names"] = [
            d.split("(")[0].strip() for d in dropped
        ]
        body["metadata"] = metadata
    if write_captures and capture_enabled():
        try:
            metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
            request_id = metadata.get("qz_request_id") if isinstance(metadata, dict) else None
            write_dual_capture("latest-dropped-tools.txt", request_id, "dropped-tools.txt", report.capture_notes())
            write_dual_capture("latest-forwarded.json", request_id, "forwarded-request-after-tools.json", body)
        except Exception:
            pass
    return report


def normalize_tools_for_llamacpp(body: dict) -> dict:
    normalize_tool_request_for_llamacpp(body)
    return body
