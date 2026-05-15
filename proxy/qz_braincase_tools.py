#!/usr/bin/env python3
"""Slice F: BrainCase harness/tool plane — braincase.render tool surface.

Exposes braincase.render as the first minimal model-visible BrainCase tool.
Feature flag: QZ_BRAINCASE_TOOLS_ENABLED (default: disabled).

When disabled (default):
  - No tool definition is injected into body["tools"].
  - No harness policy text is added to the turn harness.
  - No runtime behaviour changes.
  - Forwarded /v1/responses bodies are not mutated.

When enabled:
  - braincase.render tool definition is injected into body["tools"].
  - Compact harness policy text is added to the turn harness.
  - braincase_render_tool() dispatches calls to braincase_render_packet().
  - DB availability is checked at execution time; disabled DB returns a
    safe warning packet rather than failing the proxy request.

Not exposed (intentionally):
  braincase.recall, braincase.search, braincase.inspect,
  braincase.write, braincase.update.
  These remain internal until future slices define their semantics and
  operator exposure policies.

This module does NOT:
  - add automatic ingestion
  - persist requests, turns, sessions, telemetry, or stream events
  - expose raw StateRecords to the model
  - inject memory without an explicit tool/harness path
  - change forwarded /v1/responses bodies when the flag is disabled
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proxy.qz_braincase_db import BrainCaseDB

QZ_BRAINCASE_TOOLS_ENABLED_ENV = "QZ_BRAINCASE_TOOLS_ENABLED"

_RENDER_PACKET_SCHEMA = "braincase/render-packet@1"

# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

BRAINCASE_RENDER_TOOL_DEF: dict = {
    "type": "function",
    "name": "braincase.render",
    "description": (
        "Produce a bounded RenderPacket from explicitly stored BrainCase memory records. "
        "Use when the task needs scoped memory context from a configured memory_domain. "
        "Returns bounded rendered_text and source_record_ids only. "
        "Does not expose raw StateRecords. "
        "Does not store anything. "
        "Does not ingest current conversation/request data. "
        "memory_domain must be supplied from configured context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "description": (
                    "What this render is for, e.g. 'task_continuity', "
                    "'project_constraints', 'open_loops'. Required."
                ),
            },
            "memory_domain": {
                "type": "string",
                "description": (
                    "Configured memory isolation domain for this session. "
                    "Must be supplied from configured context. Do not guess."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "Optional narrow search query to filter records. "
                    "Use when specific content is expected."
                ),
            },
            "tiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional memory tier filter, e.g. ['project_state', 'working_state']. "
                    "Omit to include all tiers."
                ),
            },
            "record_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional specific record IDs to render. "
                    "Use when exact records are known."
                ),
            },
            "budget_tokens": {
                "type": "integer",
                "description": "Token budget for rendered output. Default 600.",
                "default": 600,
                "minimum": 80,
                "maximum": 2000,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum candidate records to consider. Default 12.",
                "default": 12,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["purpose", "memory_domain"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Harness policy text
# ---------------------------------------------------------------------------

BRAINCASE_HARNESS_POLICY: str = """\
## BrainCase Memory Tools

BrainCase memory is opt-in and tool-mediated. Use only when scoped project/domain
memory would meaningfully help the current task.

**braincase.render** is the only currently exposed BrainCase tool.
- Supply memory_domain explicitly from configured session context.
- Prefer narrow query or record_ids when known.
- Keep budget_tokens small (default 600).
- Do not use it as a broad memory dump or context prefill.
- Do not assume memory exists — an empty packet is a valid result.
- Do not attempt to store current conversation through render.
- Only rendered_text and source_record_ids are model-visible; do not expose raw records.
- If memory_domain is unknown, do not call the tool.

Not yet exposed: braincase.recall, braincase.write, braincase.update,
braincase.search, braincase.inspect. These remain internal until future slices
define their semantics and operator exposure policies."""

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_braincase_tools_enabled(env: dict | None = None) -> bool:
    """Return True if QZ_BRAINCASE_TOOLS_ENABLED is set to a truthy value.

    Default is disabled (returns False) unless explicitly enabled.
    """
    source = os.environ if env is None else env
    value = source.get(QZ_BRAINCASE_TOOLS_ENABLED_ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_braincase_tool_definitions(env: dict | None = None) -> list[dict]:
    """Return braincase tool definitions when the feature flag is enabled.

    Returns [] when disabled (default). When enabled, returns only
    [BRAINCASE_RENDER_TOOL_DEF].

    braincase.recall, write, update, search, inspect are never included.
    """
    if not is_braincase_tools_enabled(env):
        return []
    return [BRAINCASE_RENDER_TOOL_DEF]


def get_braincase_harness_policy(env: dict | None = None) -> str | None:
    """Return the compact BrainCase harness policy text when the flag is enabled.

    Returns None when disabled (default).
    """
    if not is_braincase_tools_enabled(env):
        return None
    return BRAINCASE_HARNESS_POLICY

# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

def braincase_render_tool(db: "BrainCaseDB", args: dict) -> dict:
    """Execute braincase.render for the given args dict.

    Returns a RenderPacket-shaped dict. Never raises.
    Returns a warning packet for missing required args or disabled/unavailable DB.
    Does not write records. No automatic ingestion occurs.

    Required args:
      purpose: str
      memory_domain: str

    Optional args:
      query: str
      tiers: list[str]
      record_ids: list[str]
      budget_tokens: int (default 600, clamped 80–2000)
      limit: int (default 12, clamped 1–50)
    """
    try:
        from .qz_braincase_render import braincase_render_packet
    except ImportError:
        from qz_braincase_render import braincase_render_packet

    if not isinstance(args, dict):
        args = {}

    purpose = args.get("purpose")
    memory_domain = args.get("memory_domain")
    ts = int(time.time() * 1000)

    # purpose is required; braincase_render_packet doesn't validate it
    if not purpose or not isinstance(purpose, str) or not purpose.strip():
        return _warning_packet(
            "purpose_required",
            purpose=str(purpose) if purpose is not None else "unknown",
            memory_domain=str(memory_domain) if memory_domain else "",
            ts=ts,
        )

    # Clamp numeric args to safe bounds
    budget_tokens = args.get("budget_tokens", 600)
    if not isinstance(budget_tokens, int) or isinstance(budget_tokens, bool) or budget_tokens < 80:
        budget_tokens = 600
    elif budget_tokens > 2000:
        budget_tokens = 2000

    limit = args.get("limit", 12)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        limit = 12
    elif limit > 50:
        limit = 50

    query = args.get("query")
    tiers = args.get("tiers")
    record_ids = args.get("record_ids")

    # braincase_render_packet handles: missing memory_domain, disabled DB, empty results
    return braincase_render_packet(
        db,
        purpose=purpose,
        memory_domain=memory_domain if isinstance(memory_domain, str) else None,
        query=query if isinstance(query, str) else None,
        tiers=tiers if isinstance(tiers, list) else None,
        record_ids=record_ids if isinstance(record_ids, list) else None,
        budget_tokens=budget_tokens,
        limit=limit,
        now_ms=ts,
    )


def _warning_packet(
    warning: str,
    *,
    purpose: str,
    memory_domain: str,
    ts: int,
) -> dict[str, Any]:
    """Return a minimal empty RenderPacket with a warning code."""
    try:
        from .qz_braincase_render import make_render_packet_id
    except ImportError:
        from qz_braincase_render import make_render_packet_id
    return {
        "packet_id": make_render_packet_id(ts, purpose or "unknown", memory_domain or "unknown"),
        "schema": _RENDER_PACKET_SCHEMA,
        "purpose": purpose,
        "memory_domain": memory_domain,
        "generated_at_ms": ts,
        "budget_tokens": 600,
        "rendered_text": "",
        "source_record_ids": [],
        "omitted_count": 0,
        "warnings": [warning],
        "metadata": None,
    }

# ---------------------------------------------------------------------------
# Body injection helper
# ---------------------------------------------------------------------------

def inject_braincase_tools_to_body(body: dict, *, env: dict | None = None) -> dict:
    """Inject braincase.render tool definition into body["tools"] if enabled.

    No-op when QZ_BRAINCASE_TOOLS_ENABLED is not set (default).
    Idempotent: does not add a duplicate if braincase.render is already present.

    Harness policy injection into the turn text is handled separately in
    normalize_responses_input_for_qwen via get_braincase_harness_policy().

    Does not change forwarded /v1/responses bodies when disabled.
    Does not add automatic ingestion.
    Does not expose raw StateRecords.
    """
    tool_defs = get_braincase_tool_definitions(env)
    if not tool_defs:
        return body

    tools = list(body.get("tools") or [])
    existing_names = {t.get("name") for t in tools if isinstance(t, dict)}
    for td in tool_defs:
        if td.get("name") not in existing_names:
            tools.append(td)
    body["tools"] = tools
    return body
