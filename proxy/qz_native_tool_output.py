#!/usr/bin/env python3
"""Classify incoming native Codex tool outputs for observable failure patterns.

Native Codex tools (exec_command, write_stdin, etc.) are executed by the Codex
sandbox, not by the proxy. The proxy sees the results only when Codex sends the
next request with function_call_output items in the input array.

This module scans those items read-only, applies conservative string-based
classifiers, and returns (event_type, payload) pairs for the caller to emit.

It never mutates the request body and never retries or escalates automatically.

Classifier table (conservative — only high-signal strings):
  sandbox_denied_readonly_fs    "Read-only file system"  → tool_sandbox_denied
  native_tool_connection_refused  "Connection refused"   → tool_connection_failed

Deliberately NOT classified:
  plain "permission denied" alone  (too common — normal file ACLs)
  "Process exited with code 1"     (any failing command)
  exit code alone                  (too broad)
"""
import re
from typing import Any, Dict, List, Optional, Tuple


CLASSIFIERS: List[Dict[str, Any]] = [
    {
        "id": "sandbox_denied_readonly_fs",
        "event": "tool_sandbox_denied",
        "triggers": ["Read-only file system"],
        "confidence": "high",
    },
    {
        "id": "native_tool_connection_refused",
        "event": "tool_connection_failed",
        "triggers": ["Connection refused", "connection refused"],
        "confidence": "medium",
    },
]

_EXIT_CODE_RE = re.compile(r"Process exited with code\s+(-?\d+)", re.IGNORECASE)


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
        if item.get("type") != "function_call_output":
            continue

        output = item.get("output")
        if not isinstance(output, str) or not output:
            continue

        call_id = str(item.get("call_id") or "")
        tool = call_id_to_name.get(call_id) or "unknown"
        exit_code = _parse_exit_code(output)
        preview = _safe_output_preview(output, 200)

        for classifier in CLASSIFIERS:
            matched = next(
                (trigger for trigger in classifier["triggers"] if trigger in output),
                None,
            )
            if matched is not None:
                results.append((
                    classifier["event"],
                    {
                        "call_id": call_id,
                        "tool": tool,
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
