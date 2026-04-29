#!/usr/bin/env python3
import json
import time

try:
    from .qz_tools import function_tool
except ImportError:
    from qz_tools import function_tool


APPLY_PATCH_OPERATION_TYPES = {"create_file", "update_file", "delete_file"}


def _now_ts() -> int:
    return int(time.time())


def _apply_patch_function_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "object",
                "description": "A single apply_patch operation to return to Codex.",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": sorted(APPLY_PATCH_OPERATION_TYPES),
                        "description": "The file operation to perform.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "diff": {
                        "type": "string",
                        "description": "V4A diff for create_file or update_file operations.",
                    },
                },
                "required": ["type", "path"],
                "additionalProperties": False,
            },
            "patch": {
                "type": "string",
                "description": "Legacy full apply_patch envelope. Prefer operation for native Codex apply_patch.",
            },
        },
        "additionalProperties": False,
    }


def _coerce_apply_patch_operation(value) -> dict | None:
    if not isinstance(value, dict):
        return None

    operation_type = value.get("type")
    path = value.get("path")
    diff = value.get("diff")

    if operation_type not in APPLY_PATCH_OPERATION_TYPES:
        return None
    if not isinstance(path, str) or not path.strip():
        return None

    operation = {
        "type": operation_type,
        "path": path.strip(),
    }

    if operation_type != "delete_file":
        if not isinstance(diff, str):
            return None
        operation["diff"] = diff
    elif isinstance(diff, str) and diff:
        operation["diff"] = diff

    return operation


def _parse_apply_patch_arguments(arguments: str) -> dict | None:
    try:
        data = json.loads(arguments or "{}")
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    operation = _coerce_apply_patch_operation(data.get("operation"))
    if operation:
        return operation

    operation = _coerce_apply_patch_operation(data)
    if operation:
        return operation

    patch = data.get("patch")
    path = data.get("path")
    operation_type = data.get("operation_type") or data.get("type") or "update_file"
    if isinstance(patch, str) and isinstance(path, str):
        return _coerce_apply_patch_operation({
            "type": operation_type,
            "path": path,
            "diff": patch,
        })

    return None


def _apply_patch_call_to_function_call(item: dict) -> dict:
    operation = _coerce_apply_patch_operation(item.get("operation"))
    arguments = json.dumps({"operation": operation}, ensure_ascii=False) if operation else "{}"
    return {
        "id": item.get("id") or item.get("call_id"),
        "type": "function_call",
        "status": item.get("status", "completed"),
        "call_id": item.get("call_id"),
        "name": "apply_patch",
        "arguments": arguments,
    }


def _apply_patch_output_to_function_output(item: dict) -> dict:
    output = {
        "status": item.get("status"),
        "output": item.get("output") or "",
    }
    return {
        "type": "function_call_output",
        "call_id": item.get("call_id"),
        "output": json.dumps(output, ensure_ascii=False),
    }


def _custom_apply_patch_call_to_function_call(item: dict) -> dict:
    return {
        "id": item.get("id") or item.get("call_id"),
        "type": "function_call",
        "status": item.get("status", "completed"),
        "call_id": item.get("call_id"),
        "name": "apply_patch",
        "arguments": json.dumps({"patch": item.get("input") or ""}, ensure_ascii=False),
    }


def _custom_apply_patch_output_to_function_output(item: dict) -> dict:
    return {
        "type": "function_call_output",
        "call_id": item.get("call_id"),
        "output": item.get("output") or "",
    }


def _apply_patch_operation_to_patch_text(operation: dict) -> str:
    operation_type = operation.get("type")
    path = operation.get("path")
    diff = operation.get("diff") or ""

    if operation_type == "create_file":
        lines = ["*** Begin Patch", f"*** Add File: {path}"]
        for line in diff.splitlines():
            if line == "@@" or line.startswith("@@ "):
                continue
            lines.append(line if line.startswith("+") else f"+{line}")
        lines.append("*** End Patch")
        return "\n".join(lines) + "\n"

    if operation_type == "delete_file":
        return f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n"

    return f"*** Begin Patch\n*** Update File: {path}\n{diff.rstrip()}\n*** End Patch\n"


def _function_call_to_apply_patch_call(item: dict) -> dict:
    operation = _parse_apply_patch_arguments(item.get("arguments") or "{}")
    if not operation:
        return item

    call_id = item.get("call_id") or item.get("id") or f"call_apply_patch_{_now_ts()}"
    item_id = item.get("id") or f"apc_local_{_now_ts()}"
    return {
        "id": item_id,
        "type": "apply_patch_call",
        "status": item.get("status", "completed"),
        "call_id": call_id,
        "operation": operation,
    }


def _function_call_to_custom_apply_patch_call(item: dict) -> dict:
    operation = _parse_apply_patch_arguments(item.get("arguments") or "{}")
    if operation:
        patch_text = _apply_patch_operation_to_patch_text(operation)
    else:
        try:
            data = json.loads(item.get("arguments") or "{}")
        except Exception:
            return item
        patch_text = data.get("patch") if isinstance(data, dict) else None
        if not isinstance(patch_text, str) or not patch_text.strip():
            return item

    return {
        "type": "custom_tool_call",
        "status": item.get("status", "completed"),
        "call_id": item.get("call_id") or item.get("id") or f"call_apply_patch_{_now_ts()}",
        "name": "apply_patch",
        "input": patch_text,
    }


def _apply_patch_output_style(body: dict) -> str:
    for tool in body.get("tools") or []:
        if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name") == "apply_patch":
            return "custom"
        if isinstance(tool, dict) and tool.get("type") == "apply_patch":
            return "native"
    return "native"


class ApplyPatchToolAdapter:
    upstream_name = "apply_patch"

    def accepts_tool(self, tool: dict) -> bool:
        if not isinstance(tool, dict):
            return False
        tool_type = tool.get("type")
        tool_name = tool.get("name") or tool.get("server_label") or tool_type
        return tool_type == "apply_patch" or (tool_type == "custom" and tool_name == "apply_patch")

    def to_upstream_tool(self, tool: dict) -> dict:
        return function_tool(
            "apply_patch",
            tool.get("description") or "Emit a single Codex apply_patch operation. QuantZhai adapts this call but does not apply files.",
            _apply_patch_function_parameters(),
        )

    def normalize_tool_choice(self, tool_choice: dict):
        if not isinstance(tool_choice, dict):
            return None
        tool_choice_type = tool_choice.get("type")
        tool_name = tool_choice.get("name")
        if tool_choice_type == "apply_patch" or (tool_choice_type == "custom" and tool_name == "apply_patch"):
            return {"type": "function", "name": "apply_patch"}
        return None

    def input_to_upstream(self, item: dict):
        if not isinstance(item, dict):
            return None
        if item.get("type") == "apply_patch_call":
            return _apply_patch_call_to_function_call(item)
        if item.get("type") == "apply_patch_call_output":
            return _apply_patch_output_to_function_output(item)
        if item.get("type") == "custom_tool_call" and item.get("name") == "apply_patch":
            return _custom_apply_patch_call_to_function_call(item)
        if item.get("type") == "custom_tool_call_output":
            return _custom_apply_patch_output_to_function_output(item)
        return None

    def output_to_codex(self, item: dict, output_style: str = "native"):
        if not (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and item.get("name") == "apply_patch"
        ):
            return None
        if output_style == "custom":
            return _function_call_to_custom_apply_patch_call(item)
        return _function_call_to_apply_patch_call(item)


APPLY_PATCH_TOOL_ADAPTER = ApplyPatchToolAdapter()


def normalize_apply_patch_output_for_codex(output_items, output_style: str = "native"):
    if not isinstance(output_items, list):
        return output_items

    normalized = []
    for item in output_items:
        adapted = APPLY_PATCH_TOOL_ADAPTER.output_to_codex(item, output_style)
        normalized.append(adapted if adapted is not None else item)
    return normalized


def normalize_apply_patch_input_for_llamacpp(input_items):
    if not isinstance(input_items, list):
        return input_items

    normalized = []
    for item in input_items:
        adapted = APPLY_PATCH_TOOL_ADAPTER.input_to_upstream(item)
        normalized.append(adapted if adapted is not None else item)
    return normalized
