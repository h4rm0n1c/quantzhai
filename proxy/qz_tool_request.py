#!/usr/bin/env python3
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    from .qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from .qz_runtime_io import capture_enabled, write_capture
    from .qz_tool_apply_patch import ensure_apply_patch_tool_policy
except ImportError:
    from qz_proxy_tools import DEFAULT_TOOL_REGISTRY
    from qz_runtime_io import capture_enabled, write_capture
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
    has_exec_session: bool = False
    tool_choice_normalized: bool = False
    tool_choice_forced_auto: bool = False

    def capture_notes(self) -> str:
        notes = []
        if self.translated:
            notes.append("translated: " + ", ".join(self.translated))
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
                clean.append(append_tool_hint(tool, FILE_EDIT_TOOL_HINT))
                continue
            if tool_name == "write_stdin":
                clean.append(append_tool_hint(tool, WRITE_STDIN_TOOL_HINT))
                continue
            clean.append(tool)
            continue

        tool_adapter = tool_registry.adapter_for_tool(tool)
        if tool_adapter:
            clean.append(tool_adapter.to_upstream_tool(tool))
            translated.append(tool_adapter.upstream_name)
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
        has_exec_session=has_exec_session,
        tool_choice_normalized=tool_choice_normalized,
        tool_choice_forced_auto=tool_choice_forced_auto,
    )
    if write_captures and capture_enabled():
        try:
            write_capture("latest-dropped-tools.txt", report.capture_notes())
            write_capture("latest-forwarded.json", body)
        except Exception:
            pass
    return report


def normalize_tools_for_llamacpp(body: dict) -> dict:
    normalize_tool_request_for_llamacpp(body)
    return body
