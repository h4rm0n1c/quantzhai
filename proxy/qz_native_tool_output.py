#!/usr/bin/env python3
"""Classify incoming native Codex tool outputs for observable failure patterns.

Native Codex tools (exec_command, write_stdin, etc.) are executed by the Codex
sandbox, not by the proxy. The proxy sees the results only when Codex sends the
next request with function_call_output items in the input array.

This module scans those items read-only, applies conservative string-based
classifiers, and returns (event_type, payload) pairs for the caller to emit.

It never mutates the request body and never retries or escalates automatically.

Classifier table (conservative — only high-signal strings / source-backed shapes):
  request_permissions_granted            Codex RequestPermissionsResponse JSON
                                          with non-empty permissions
                                          → request_permissions_outcome
  request_permissions_denied_or_unavailable
                                          Codex RequestPermissionsResponse JSON
                                          with empty permissions
                                          → request_permissions_outcome
  sandbox_denied_readonly_fs              "Read-only file system"
                                          → tool_sandbox_denied
  native_tool_connection_refused          "Connection refused"
                                          → tool_connection_failed

  apply_patch_context_mismatch       "Failed to find expected lines"       (AP-4)
                                      "Failed to find context"
                                      in custom_tool_call_output
                                          → apply_patch_context_mismatch

Deliberately NOT classified:
  plain "permission denied" alone  (too common — normal file ACLs)
  "Process exited with code 1"     (any failing command)
  exit code alone                  (too broad)
"""
import json
import re
from typing import Any, Dict, List, Optional, Tuple


CLASSIFIERS: List[Dict[str, Any]] = [
    {
        "id": "sandbox_denied_readonly_fs",
        "event": "tool_sandbox_denied",
        "triggers": ["Read-only file system"],
        "confidence": "high",
        "item_types": {"function_call_output"},
    },
    {
        "id": "native_tool_connection_refused",
        "event": "tool_connection_failed",
        "triggers": ["Connection refused", "connection refused"],
        "confidence": "medium",
        "item_types": {"function_call_output"},
    },
    {
        # AP-4: apply_patch context mismatch.
        # Codex apply-patch/src/lib.rs:715,772 — both strings are produced when
        # the diff context lines don't match the current file content.  These
        # arrive as custom_tool_call_output (apply_patch is a custom_tool_call).
        "id": "apply_patch_context_mismatch",
        "event": "apply_patch_context_mismatch",
        "triggers": ["Failed to find expected lines", "Failed to find context"],
        "confidence": "high",
        "item_types": {"custom_tool_call_output"},
    },
]

_EXIT_CODE_RE = re.compile(r"Process exited with code\s+(-?\d+)", re.IGNORECASE)
_REQUEST_PERMISSIONS_TOOL = "request_permissions"
_REQUEST_PERMISSIONS_SCOPES = {"turn", "session"}


def _parse_exit_code(output: str) -> Optional[int]:
    """Parse exit code from Codex sandbox output envelope.

    Codex wraps exec_command output as:
        Process exited with code N
        Output:
        <shell stdout/stderr>
    """
    m = _EXIT_CODE_RE.search(output)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            pass
    return None


def _safe_output_preview(value: Any, limit: int = 200) -> str:
    """Return a bounded string preview of tool output. Never raises."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return str(value)[:limit]
    except Exception:
        return ""


def _build_call_id_to_name(input_items: List[Any]) -> Dict[str, str]:
    """Map call_id → tool name from function_call items in the same input array."""
    mapping: Dict[str, str] = {}
    for item in input_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id") or item.get("id")
        name = item.get("name")
        if call_id and isinstance(name, str) and name:
            mapping[str(call_id)] = name
    return mapping


def _permission_profile_summary(permissions: Dict[str, Any]) -> Dict[str, bool]:
    """Summarize a RequestPermissionProfile without storing raw paths/rules."""
    return {
        # Codex RequestPermissionProfile::is_empty checks only whether these
        # Option fields are None, not whether their inner structs are empty.
        "network": permissions.get("network") is not None,
        "file_system": permissions.get("file_system") is not None,
    }


def _classify_request_permissions_output(
    output: str,
    *,
    call_id: str,
    tool: str,
    preview: str,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Classify Codex request_permissions result JSON.

    Codex source at audit SHA 46f30d02828bd4c52827e5f0482a6f2a982cce5b shows
    request_permissions returns a serialized RequestPermissionsResponse through
    FunctionToolOutput::from_text(). Empty permissions are used for denial,
    disabled/unavailable policy, abort, timeout, and network deny paths; there is
    no separate deny/unavailable field to distinguish those outcomes.
    """
    if tool != _REQUEST_PERMISSIONS_TOOL:
        return None

    try:
        parsed = json.loads(output)
    except (TypeError, ValueError):
        return None

    if not isinstance(parsed, dict):
        return None

    permissions = parsed.get("permissions")
    scope = parsed.get("scope")
    strict_auto_review = parsed.get("strict_auto_review", False)

    if not isinstance(permissions, dict):
        return None
    if scope not in _REQUEST_PERMISSIONS_SCOPES:
        return None
    if not isinstance(strict_auto_review, bool):
        return None

    permission_summary = _permission_profile_summary(permissions)
    granted = any(permission_summary.values())
    if granted:
        classifier = "request_permissions_granted"
        outcome = "granted"
        confidence = "high"
    else:
        classifier = "request_permissions_denied_or_unavailable"
        outcome = "denied_or_unavailable"
        confidence = "high"

    return (
        "request_permissions_outcome",
        {
            "call_id": call_id,
            "tool": tool,
            "classifier": classifier,
            "outcome": outcome,
            "scope": scope,
            "strict_auto_review": strict_auto_review,
            "permission_summary": permission_summary,
            "output_preview": preview,
            "confidence": confidence,
        },
    )


def classify_native_tool_outputs(
    input_items: List[Any],
) -> List[Tuple[str, Dict[str, Any]]]:
    """Scan incoming request input items for classifiable native tool output patterns.

    Legacy/compatibility helper. Returns a list of (event_type, payload_dict) tuples.
    Newer callers should prefer classify_native_tool_output_signals().

    Each payload includes: call_id, tool, classifier, matched_string,
    exit_code, output_preview, confidence.

    Does not mutate input_items or any item within them.
    Returns an empty list for empty/invalid input.
    """
    if not isinstance(input_items, list):
        return []

    call_id_to_name = _build_call_id_to_name(input_items)
    results: List[Tuple[str, Dict[str, Any]]] = []

    for item in input_items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type not in ("function_call_output", "custom_tool_call_output"):
            continue

        output = item.get("output")
        if not isinstance(output, str) or not output:
            continue

        call_id = str(item.get("call_id") or "")
        # For custom_tool_call_output, the tool name is on the item itself;
        # for function_call_output, look it up from the sibling function_call.
        tool = (
            str(item.get("name") or "")
            if item_type == "custom_tool_call_output"
            else call_id_to_name.get(call_id) or "unknown"
        )
        exit_code = _parse_exit_code(output)
        preview = _safe_output_preview(output, 200)

        if item_type == "function_call_output":
            request_permissions_result = _classify_request_permissions_output(
                output,
                call_id=call_id,
                tool=tool,
                preview=preview,
            )
            if request_permissions_result is not None:
                results.append(request_permissions_result)
                continue

        for classifier in CLASSIFIERS:
            # Respect the item_types filter if present.
            allowed_types = classifier.get("item_types")
            if allowed_types and item_type not in allowed_types:
                continue
            matched = next(
                (trigger for trigger in classifier["triggers"] if trigger in output),
                None,
            )
            if matched is not None:
                results.append((
                    classifier["event"],
                    {
                        "call_id": call_id,
                        "tool": tool or "apply_patch",
                        "classifier": classifier["id"],
                        "matched_string": matched,
                        "exit_code": exit_code,
                        "output_preview": preview,
                        "confidence": classifier["confidence"],
                    },
                ))

    return results


def classify_native_tool_output_signals(
    input_items: List[Any],
) -> List[Any]:
    """Classify native tool outputs as SignalDecision objects.

    Canonical router-facing helper. Returns a list of SignalDecision instances
    instead of raw (event_type, payload) tuples.

    All current classifiers remain operator-visible / telemetry-only — no
    model injection is performed by this function.
    """
    try:
        from .qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision
    except ImportError:
        from qz_feedback import FeedbackChannel, FeedbackVisibility, SignalDecision

    return [
        SignalDecision(
            event_type=event_type,
            payload=payload,
            visibility=FeedbackVisibility.OPERATOR,
            channel=FeedbackChannel.TELEMETRY,
            confidence=payload.get("confidence", ""),
        )
        for event_type, payload in classify_native_tool_outputs(input_items)
    ]
