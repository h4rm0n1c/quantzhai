#!/usr/bin/env python3
import json
import re
import time

try:
    from .qz_tools import ToolCoercionResult, ToolLifecycleSpec, ToolRegistry, function_tool
except ImportError:
    from qz_tools import ToolCoercionResult, ToolLifecycleSpec, ToolRegistry, function_tool


APPLY_PATCH_OPERATION_TYPES = {"create_file", "update_file", "delete_file", "move_file", "rename_file"}
APPLY_PATCH_DESTINATION_KEYS = ("destination", "new_path", "to", "move_to", "target_path")
TOOL_POLICY_SCHEMA = "qz.tool_policy.v1"


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
                        "description": (
                            "Patch content. Rules:\n"
                            "- update_file: unified diff lines. Each line starts with ' ' (context, must match file EXACTLY), '+' (add), or '-' (remove). "
                            "Include 1-3 lines of context before and after each change. "
                            "Context lines must be verbatim copies from the current file — do NOT invent or paraphrase context. "
                            "Do NOT wrap in markdown code fences (no ```diff or ```). "
                            "Do NOT include --- a/file or +++ b/file headers.\n"
                            "- create_file: the complete new file content (no diff markers needed).\n"
                            "- delete_file: omit or empty string.\n"
                            "- move_file/rename_file: context hunk from the source file."
                        ),
                    },
                    "destination": {
                        "type": "string",
                        "description": "Required workspace-relative destination path for move_file or rename_file operations.",
                    },
                    "new_path": {
                        "type": "string",
                        "description": "Alias for destination on move_file or rename_file operations.",
                    },
                    "to": {
                        "type": "string",
                        "description": "Alias for destination on move_file or rename_file operations.",
                    },
                    "move_to": {
                        "type": "string",
                        "description": "Alias for destination on move_file or rename_file operations.",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Alias for destination on move_file or rename_file operations.",
                    },
                },
                "required": ["type", "path"],
                "additionalProperties": False,
            },
            "patch": {
                "type": "string",
                "description": "Legacy apply_patch patch envelope. Prefer the operation field.",
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
    destination = next(
        (
            value.get(key)
            for key in APPLY_PATCH_DESTINATION_KEYS
            if isinstance(value.get(key), str) and value.get(key).strip()
        ),
        None,
    )

    if operation_type not in APPLY_PATCH_OPERATION_TYPES:
        return None
    if not isinstance(path, str) or not path.strip():
        return None
    if operation_type == "rename_file":
        operation_type = "move_file"

    operation = {
        "type": operation_type,
        "path": path.strip(),
    }

    if operation_type in {"create_file", "update_file"}:
        if not isinstance(diff, str):
            return None
        operation["diff"] = diff
    elif operation_type == "move_file":
        if not isinstance(destination, str) or not destination.strip():
            return None
        operation["destination"] = destination.strip()
        if isinstance(diff, str) and diff:
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

    # Qwen-observed shape: operation lacks diff but a sibling top-level `patch`
    # string carries the file content. Promote the sibling into the operation's
    # diff before retrying coercion. The existing diff-stripping path then
    # handles unified-diff file headers in the promoted content.
    nested = data.get("operation")
    sibling_patch = data.get("patch")
    if (
        isinstance(nested, dict)
        and isinstance(sibling_patch, str)
        and not isinstance(nested.get("diff"), str)
    ):
        merged = dict(nested)
        merged["diff"] = sibling_patch
        operation = _coerce_apply_patch_operation(merged)
        if operation:
            return operation

    operation = _coerce_apply_patch_operation(data)
    if operation:
        return operation

    patch = data.get("patch")
    path = data.get("path")
    operation_type = data.get("operation_type") or data.get("type") or "update_file"
    if isinstance(patch, str):
        # Qwen-observed variant: full *** Begin Patch ... *** End Patch envelope
        # at top-level with no separate path. Extract the path and operation
        # type from the envelope header lines.
        if not isinstance(path, str) or not path.strip():
            extracted = _extract_op_and_path_from_patch_envelope(patch)
            if extracted:
                operation_type, path = extracted
        if isinstance(path, str) and path.strip():
            return _coerce_apply_patch_operation({
                "type": operation_type,
                "path": path,
                "diff": patch,
            })

    return None


def _extract_op_and_path_from_patch_envelope(patch_text: str) -> tuple[str, str] | None:
    """Pull (operation_type, path) from a Codex *** Begin Patch ... *** End
    Patch envelope's header lines. Returns None if no recognised header is
    found.
    """
    if not isinstance(patch_text, str):
        return None
    for line in patch_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("*** Add File: "):
            return ("create_file", stripped[len("*** Add File: "):].strip())
        if stripped.startswith("*** Update File: "):
            return ("update_file", stripped[len("*** Update File: "):].strip())
        if stripped.startswith("*** Delete File: "):
            return ("delete_file", stripped[len("*** Delete File: "):].strip())
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

    if operation_type == "move_file":
        destination = operation.get("destination")
        if not diff:
            raise ValueError("custom apply_patch move_file requires a diff/context hunk")
        lines = ["*** Begin Patch", f"*** Update File: {path}", f"*** Move to: {destination}"]
        diff = _strip_unified_diff_headers(diff, path)
        lines.append(diff.rstrip())
        lines.append("*** End Patch")
        return "\n".join(lines) + "\n"

    diff = _strip_unified_diff_headers(diff, path)
    return f"*** Begin Patch\n*** Update File: {path}\n{diff.rstrip()}\n*** End Patch\n"


def _normalize_apply_patch_operation_for_codex(operation: dict) -> dict:
    operation = dict(operation)
    if operation.get("type") in {"update_file", "move_file"} and operation.get("diff"):
        operation["diff"] = _strip_unified_diff_headers(operation.get("diff") or "", operation.get("path"))
    return operation


def _strip_markdown_code_fences(diff: str) -> str:
    """Strip markdown code fence markers that models sometimes wrap diffs in.

    The model may generate:
        ```diff
        - old
        + new
        ```
    or:
        ```rust
        ...
        ```
    Strip the opening ``` or ```lang and closing ``` lines so the actual
    diff content reaches the patch verifier.
    """
    lines = diff.splitlines(keepends=True)
    if not lines:
        return diff
    # Strip leading code fence
    first = lines[0].rstrip()
    if re.match(r"^`{3,}[a-zA-Z]*$", first):
        lines = lines[1:]
    # Strip trailing code fence
    if lines and re.match(r"^`{3,}\s*$", lines[-1].rstrip()):
        lines = lines[:-1]
    return "".join(lines)


def _strip_unified_diff_headers(diff: str, path: str | None = None) -> str:
    """Drop file-level unified diff metadata before Codex-facing output."""
    diff = _strip_markdown_code_fences(diff)
    lines = diff.splitlines()
    if not lines:
        return diff

    stripped = []
    seen_hunk = False
    for line in lines:
        normalized_hunk = _normalize_unified_diff_hunk_header(line)
        if normalized_hunk is not None:
            seen_hunk = True
            stripped.append(normalized_hunk)
            continue

        if not seen_hunk and _is_unified_diff_metadata_line(line, path):
            continue

        stripped.append(line)

    trailing_newline = "\n" if diff.endswith("\n") else ""
    return "\n".join(stripped) + trailing_newline


def _normalize_unified_diff_hunk_header(line: str) -> str | None:
    if line == "@@":
        return "@@"
    if re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$", line):
        return "@@"
    if line.startswith("@@ "):
        return line
    return None


def _is_unified_diff_metadata_line(line: str, path: str | None = None) -> bool:
    if line.startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ")):
        return True
    if line.startswith(("similarity index ", "rename from ", "rename to ", "old mode ", "new mode ")):
        return True
    if line.startswith("--- ") or line.startswith("+++ "):
        return True
    if path:
        return line in {f"--- {path}", f"+++ {path}", f"--- a/{path}", f"+++ b/{path}"}
    return False


def _function_call_to_custom_apply_patch_call(item: dict) -> dict:
    arguments = item.get("arguments") or "{}"
    operation = _parse_apply_patch_arguments(arguments)
    patch_text = None

    if operation:
        try:
            patch_text = _apply_patch_operation_to_patch_text(operation)
        except ValueError:
            patch_text = _build_partial_custom_envelope(operation)

    if patch_text is None:
        partial = _extract_partial_custom_envelope_from_args(arguments)
        if partial is not None:
            patch_text = partial

    if patch_text is None:
        # Last resort: a recognisable legacy patch string at top-level.
        try:
            data = json.loads(arguments)
        except Exception:
            data = None
        if isinstance(data, dict):
            legacy = data.get("patch")
            if isinstance(legacy, str) and legacy.strip():
                patch_text = legacy

    if patch_text is None:
        return _invalid_apply_patch_call_message(item, _describe_args_failure(arguments))

    return {
        "type": "custom_tool_call",
        "status": item.get("status", "completed"),
        "call_id": item.get("call_id") or item.get("id") or f"call_apply_patch_{_now_ts()}",
        "name": "apply_patch",
        "input": patch_text,
    }


def _build_partial_custom_envelope(operation: dict) -> str:
    """Build a Codex apply_patch envelope from a partially-valid operation.

    The envelope is intentionally minimal — enough to carry the type/path/
    destination so Codex's V4A verifier produces a specific error message
    instead of the proxy silently dropping the call.
    """
    op_type = operation.get("type")
    path = operation.get("path") or "unknown"
    if op_type == "move_file":
        destination = operation.get("destination") or "unknown"
        return (
            "*** Begin Patch\n"
            f"*** Update File: {path}\n"
            f"*** Move to: {destination}\n"
            "*** End Patch\n"
        )
    if op_type == "delete_file":
        return f"*** Begin Patch\n*** Delete File: {path}\n*** End Patch\n"
    if op_type == "create_file":
        return f"*** Begin Patch\n*** Add File: {path}\n*** End Patch\n"
    return f"*** Begin Patch\n*** Update File: {path}\n*** End Patch\n"


def _extract_partial_custom_envelope_from_args(arguments: str) -> str | None:
    """When full coercion fails, try to pull at least type+path from the args
    and emit a partial envelope so Codex's verifier surfaces a specific error.
    """
    candidate = _extract_partial_operation(arguments)
    if not candidate:
        return None
    return _build_partial_custom_envelope(candidate)


def _extract_partial_operation(arguments: str) -> dict | None:
    """Pull a {type, path, destination?} dict out of best-effort args parsing.
    Returns None if there is not enough to build a meaningful envelope."""
    try:
        data = json.loads(arguments or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    nested = data.get("operation")
    candidate = nested if isinstance(nested, dict) else data
    if not isinstance(candidate, dict):
        return None
    op_type = candidate.get("type")
    path = candidate.get("path")
    if op_type not in APPLY_PATCH_OPERATION_TYPES:
        return None
    if not isinstance(path, str) or not path.strip():
        return None
    if op_type == "rename_file":
        op_type = "move_file"
    out = {"type": op_type, "path": path.strip()}
    if op_type == "move_file":
        destination = next(
            (
                candidate.get(key).strip()
                for key in APPLY_PATCH_DESTINATION_KEYS
                if isinstance(candidate.get(key), str) and candidate.get(key).strip()
            ),
            None,
        )
        if not destination:
            # Without a destination there is no usable envelope; fall back to
            # the descriptive assistant message rather than emit garbage.
            return None
        out["destination"] = destination
    return out


def inspect_apply_patch_arguments(arguments: str) -> dict:
    """Return safe telemetry metadata for an apply_patch arguments string.

    All returned fields are safe for operator telemetry — booleans and enum
    strings only.  No raw content (arguments, patch body, diff, file paths,
    or destination paths) is included.

    Fields:
    - args_shape:           "object" | "non_object" | "invalid_json" | "empty"
    - operation_present:    bool — data["operation"] is a dict
    - patch_present:        bool — data["patch"] is a non-empty string
    - path_present:         bool — candidate["path"] is a non-empty string
    - diff_present:         bool — candidate["diff"] is present (pre-promotion)
    - destination_present:  bool — any destination alias is a non-empty string
    - operation_type:       enum or "unknown" or "missing"
    - coercion_strategy:    enum (success) or "failed_*" (failure)

    Note: diff_present reflects the operation object before sibling-patch
    promotion.  When coercion_strategy == "sibling_patch_promoted", diff_present
    is False (the nested operation lacked diff; the sibling patch supplied it).
    """
    if not arguments or not arguments.strip():
        return {
            "args_shape": "empty",
            "operation_present": False,
            "patch_present": False,
            "path_present": False,
            "diff_present": False,
            "destination_present": False,
            "operation_type": "missing",
            "coercion_strategy": "failed_invalid_json",
        }

    try:
        data = json.loads(arguments)
    except Exception:
        return {
            "args_shape": "invalid_json",
            "operation_present": False,
            "patch_present": False,
            "path_present": False,
            "diff_present": False,
            "destination_present": False,
            "operation_type": "missing",
            "coercion_strategy": "failed_invalid_json",
        }

    if not isinstance(data, dict):
        return {
            "args_shape": "non_object",
            "operation_present": False,
            "patch_present": False,
            "path_present": False,
            "diff_present": False,
            "destination_present": False,
            "operation_type": "missing",
            "coercion_strategy": "failed_non_object_json",
        }

    nested = data.get("operation")
    sibling_patch = data.get("patch")
    candidate = nested if isinstance(nested, dict) else data

    operation_present = isinstance(nested, dict)
    patch_present = isinstance(sibling_patch, str) and bool(sibling_patch)

    # All booleans — no raw values
    path_val = candidate.get("path") if isinstance(candidate, dict) else None
    path_present = isinstance(path_val, str) and bool(path_val.strip())
    diff_present = isinstance(candidate.get("diff"), str) if isinstance(candidate, dict) else False
    destination_present = bool(
        isinstance(candidate, dict)
        and any(
            isinstance(candidate.get(k), str) and candidate.get(k, "").strip()
            for k in APPLY_PATCH_DESTINATION_KEYS
        )
    )

    # operation_type: enum string, "unknown", or "missing" — never raw value
    if isinstance(candidate, dict):
        op_type = candidate.get("type")
        if op_type in APPLY_PATCH_OPERATION_TYPES:
            operation_type = op_type
        elif op_type is None:
            operation_type = "missing"
        else:
            operation_type = "unknown"
    else:
        operation_type = "missing"

    meta = {
        "args_shape": "object",
        "operation_present": operation_present,
        "patch_present": patch_present,
        "path_present": path_present,
        "diff_present": diff_present,
        "destination_present": destination_present,
        "operation_type": operation_type,
        "coercion_strategy": "failed_unclassified",
    }

    # Strategy detection — mirrors _parse_apply_patch_arguments coerce paths.
    # Each check is a pure function call; no raw content escapes into meta.

    # Path 1: valid nested operation object
    if isinstance(nested, dict) and _coerce_apply_patch_operation(nested) is not None:
        meta["coercion_strategy"] = "operation_object"
        return meta

    # Path 2: sibling patch promoted into operation.diff
    if (
        isinstance(nested, dict)
        and isinstance(sibling_patch, str)
        and not isinstance(nested.get("diff"), str)
        and _coerce_apply_patch_operation(dict(nested, diff=sibling_patch)) is not None
    ):
        meta["coercion_strategy"] = "sibling_patch_promoted"
        return meta

    # Path 3: top-level operation fields (no nested "operation" key)
    if _coerce_apply_patch_operation(data) is not None:
        meta["coercion_strategy"] = "top_level_operation"
        return meta

    # Path 4: legacy patch paths
    patch = data.get("patch")
    path = data.get("path")
    if isinstance(patch, str):
        if not isinstance(path, str) or not path.strip():
            extracted = _extract_op_and_path_from_patch_envelope(patch)
            if extracted:
                _etype, _epath = extracted
                if isinstance(_epath, str) and _epath.strip():
                    meta["coercion_strategy"] = "legacy_patch_envelope"
                    return meta
        if isinstance(path, str) and path.strip():
            meta["coercion_strategy"] = "legacy_patch_with_path"
            return meta

    # All coerce paths failed — classify failure from candidate
    if isinstance(candidate, dict):
        cand_type = candidate.get("type")
        if cand_type not in APPLY_PATCH_OPERATION_TYPES:
            meta["coercion_strategy"] = "failed_unknown_operation_type"
        elif not isinstance(candidate.get("path"), str) or not candidate.get("path", "").strip():
            meta["coercion_strategy"] = "failed_missing_path"
        elif cand_type in {"create_file", "update_file"} and not isinstance(candidate.get("diff"), str):
            meta["coercion_strategy"] = "failed_missing_diff"
        elif cand_type in {"move_file", "rename_file"} and not any(
            isinstance(candidate.get(k), str) and candidate.get(k, "").strip()
            for k in APPLY_PATCH_DESTINATION_KEYS
        ):
            meta["coercion_strategy"] = "failed_missing_destination"

    return meta


def _describe_args_failure(arguments: str) -> str:
    """Produce a specific human-readable reason for an args-coercion failure.
    Used in the fallback assistant message when no envelope can be salvaged."""
    try:
        data = json.loads(arguments or "{}")
    except Exception:
        return "arguments are not valid JSON"
    if not isinstance(data, dict):
        return "arguments are not a JSON object"
    nested = data.get("operation")
    candidate = nested if isinstance(nested, dict) else data
    if not isinstance(candidate, dict):
        return "could not extract operation object from arguments"
    op_type = candidate.get("type")
    if op_type not in APPLY_PATCH_OPERATION_TYPES:
        return (
            f"unknown operation type {op_type!r}; expected one of: "
            f"{', '.join(sorted(APPLY_PATCH_OPERATION_TYPES))}"
        )
    path = candidate.get("path")
    if not isinstance(path, str) or not path.strip():
        return f"missing or empty 'path' on operation type {op_type!r}"
    if op_type in {"create_file", "update_file"} and not isinstance(candidate.get("diff"), str):
        return (
            f"missing 'diff' on operation type {op_type!r}; "
            "include file content (create_file) or V4A hunk (update_file) "
            "as operation.diff or top-level patch"
        )
    if op_type in {"move_file", "rename_file"}:
        for key in APPLY_PATCH_DESTINATION_KEYS:
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                break
        else:
            return (
                f"missing destination on operation type {op_type!r}; "
                f"expected one of: {', '.join(APPLY_PATCH_DESTINATION_KEYS)}"
            )
    return "could not coerce arguments to a valid apply_patch operation"


def _invalid_apply_patch_call_message(item: dict, reason: str | None = None) -> dict:
    item_id = item.get("id") or item.get("call_id") or f"msg_apply_patch_invalid_{_now_ts()}"
    text = "apply_patch call rejected by QuantZhai proxy: model emitted invalid patch arguments."
    if reason:
        text = f"{text} Reason: {reason}"
    return {
        "id": f"msg_{item_id}",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "text": text,
        }],
    }


def _infer_apply_patch_tool_policy(body: dict) -> dict:
    for tool in body.get("tools") or []:
        if isinstance(tool, dict) and tool.get("type") == "custom" and tool.get("name") == "apply_patch":
            return {
                "schema": TOOL_POLICY_SCHEMA,
                "apply_patch_declared": True,
                "apply_patch_client_tool_type": "custom",
            }
        if isinstance(tool, dict) and tool.get("type") == "apply_patch":
            # Codex declares apply_patch as {"type":"apply_patch"}.  The only
            # Codex-facing wire format is custom_tool_call — there is no native
            # apply_patch_call item type in current Codex.
            return {
                "schema": TOOL_POLICY_SCHEMA,
                "apply_patch_declared": True,
                "apply_patch_client_tool_type": "apply_patch",
            }
    return {
        "schema": TOOL_POLICY_SCHEMA,
        "apply_patch_declared": False,
        "apply_patch_client_tool_type": "absent",
    }


def ensure_apply_patch_tool_policy(body: dict, overwrite: bool = False) -> dict:
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    policy = metadata.get("qz_tool_policy")
    if overwrite or not isinstance(policy, dict) or policy.get("schema") != TOOL_POLICY_SCHEMA:
        policy = _infer_apply_patch_tool_policy(body)
        metadata["qz_tool_policy"] = policy
        body["metadata"] = metadata
    return policy


class ApplyPatchToolAdapter:
    upstream_name = "apply_patch"
    lifecycle = ToolLifecycleSpec(
        name="apply_patch",
        execution="protocol_adapter",
        # Current Codex routes apply_patch as a freeform custom tool.
        # The only Codex-facing wire format is custom_tool_call (name=apply_patch).
        # apply_patch_call was a mistaken contract and has been removed.
        public_item_type="custom_tool_call",
        telemetry_name="apply_patch",
    )

    def accepts_tool(self, tool: dict) -> bool:
        if not isinstance(tool, dict):
            return False
        tool_type = tool.get("type")
        tool_name = tool.get("name") or tool.get("server_label") or tool_type
        return tool_type == "apply_patch" or (tool_type == "custom" and tool_name == "apply_patch")

    def to_upstream_tool(self, tool: dict) -> dict:
        description = tool.get("description") or (
            "Emit a single file operation (create, update, delete, move) to Codex.\n"
            "For update_file: the diff field must be a unified diff where every context "
            "line (' ') is a verbatim copy from the current file. Read the file first, "
            "copy lines exactly — do not paraphrase or invent context lines.\n"
            "Do NOT wrap the diff in markdown code fences (no ```diff or ```rust).\n"
            "A 'verification failed' error means context lines did not match the file — "
            "it is NOT a filesystem permission error. Re-read the file and retry with "
            "exact verbatim context from the current file content."
        )
        return function_tool("apply_patch", description, _apply_patch_function_parameters())

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
        if item.get("type") == "custom_tool_call" and item.get("name") == "apply_patch":
            return _custom_apply_patch_call_to_function_call(item)
        if item.get("type") == "custom_tool_call_output":
            return _custom_apply_patch_output_to_function_output(item)
        return None

    def coerce(self, call: dict) -> ToolCoercionResult:
        """Attempt to recover a malformed apply_patch function_call.

        If the arguments can be coerced into a valid operation (e.g. sibling
        patch field, legacy envelope), return the corrected canonical JSON.
        If coercion fails, return a specific error message the model can act on.
        """
        arguments = call.get("arguments") or "{}" if isinstance(call, dict) else "{}"
        operation = _parse_apply_patch_arguments(arguments)
        if operation:
            return ToolCoercionResult(
                corrected_arguments=json.dumps({"operation": operation}, ensure_ascii=False)
            )
        reason = _describe_args_failure(arguments)
        return ToolCoercionResult(
            error_message=(
                f"apply_patch argument error: {reason} "
                "— Check the operation type and path fields, then retry."
            )
        )

    def output_to_codex(self, item: dict, output_style: str = "custom"):
        # output_style is ignored; Codex apply_patch is always custom_tool_call.
        if not (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and item.get("name") == "apply_patch"
        ):
            return None
        return _function_call_to_custom_apply_patch_call(item)


APPLY_PATCH_TOOL_ADAPTER = ApplyPatchToolAdapter()


def normalize_apply_patch_output_for_codex(output_items):
    return ToolRegistry((APPLY_PATCH_TOOL_ADAPTER,)).output_items_to_codex(output_items)


def normalize_apply_patch_input_for_llamacpp(input_items):
    if not isinstance(input_items, list):
        return input_items

    normalized = []
    for item in input_items:
        adapted = APPLY_PATCH_TOOL_ADAPTER.input_to_upstream(item)
        normalized.append(adapted if adapted is not None else item)
    return normalized
