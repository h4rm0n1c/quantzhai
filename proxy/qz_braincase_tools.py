#!/usr/bin/env python3
"""Slices F/G/G.1/H.2: BrainCase harness/tool plane.

Exposed tools:
  braincase.render   — bounded RenderPacket from stored records
  braincase.recall   — tier-routed recall returning RenderPacket
  braincase.write_candidate — candidate-only StateRecord write (Slice H.2)

Feature flags:
  QZ_BRAINCASE_TOOLS_ENABLED          — controls render + recall
  QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED — controls write_candidate
    Requires BOTH flags to be true for write_candidate to be active.
    Default disabled for both.

Not exposed (intentionally):
  braincase.write, braincase.update, braincase.search, braincase.inspect,
  braincase.promote_candidate.

braincase.write_candidate semantics (Slice H.2):
  Stores a candidate-only StateRecord for operator review.
  Forced: status=candidate, visibility=internal. Always.
  Reject-first: if model supplies status/visibility → error, no storage.
  Defensive backstop: candidate/internal forced before any DB write.
  Claim/summary must not contain raw prompt/log/session content.
  Result is WriteCandidateResult, not RenderPacket.
  Candidate records are not returned by braincase.render or braincase.recall.
  No automatic ingestion.

This module does NOT:
  - add automatic ingestion
  - persist requests, turns, sessions, telemetry, or stream events
  - expose raw StateRecords to the model
  - inject memory without an explicit tool/harness path
  - change forwarded /v1/responses bodies when the flags are disabled
  - make candidate records active or renderable
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proxy.qz_braincase_db import BrainCaseDB
    from proxy.qz_proxy_tools import ProxyToolExecutionContext

try:
    from .qz_tools import ToolLifecycleSpec
    from .qz_tool_lifecycle import ToolContinuationResult
except ImportError:
    from qz_tools import ToolLifecycleSpec
    from qz_tool_lifecycle import ToolContinuationResult

QZ_BRAINCASE_TOOLS_ENABLED_ENV = "QZ_BRAINCASE_TOOLS_ENABLED"
QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV = "QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED"

_RENDER_PACKET_SCHEMA = "braincase/render-packet@1"
_WRITE_CANDIDATE_RESULT_SCHEMA = "braincase/write-candidate-result@1"

# Raw content markers that must not appear in claim or summary (v1 guard rail).
# These indicate raw prompt/log/session blobs rather than durable memory facts.
_RAW_CONTENT_MARKERS: tuple[str, ...] = (
    "raw_request_body",
    "raw_prompt",
    "User:",
    "Assistant:",
    "[Turn",
    "tool_call",
    "function_call",
    "telemetry_event",
    "stream_event",
)

# Forbidden top-level tool input fields for write_candidate.
_WRITE_CANDIDATE_FORBIDDEN_ARGS: frozenset[str] = frozenset({
    "status",
    "visibility",
    "raw_prompt",
    "raw_request_body",
    "request_body",
    "full_log",
    "telemetry_event",
    "stream_event",
})

_VALID_TIERS: frozenset[str] = frozenset({
    "working_state", "session_state", "project_state",
    "semantic_memory", "procedural_memory", "episodic_memory",
    "artifact_memory", "perceptual_index", "preference_constraint_memory",
})

_VALID_RECORD_TYPES: frozenset[str] = frozenset({
    "constraint", "preference", "project_decision", "project_state",
    "procedure", "artifact_reference", "diagnostic", "open_question",
    "identity_note", "correction", "episode", "recent_topic",
})

_VALID_RETENTIONS: frozenset[str] = frozenset({
    "ephemeral", "session", "project", "durable",
})

# ---------------------------------------------------------------------------
# Recall mode tier routing (Slice G)
# ---------------------------------------------------------------------------

# Each recall mode maps to a bounded list of memory tiers.
# No mode means "all tiers" — modes deliberately restrict scope.
# Tier names are informational; BrainCaseDB stores the tier field as-is.

RECALL_MODE_TIERS: dict[str, list[str]] = {
    "task": [
        "working_state",
        "project_state",
        "preference_constraint_memory",
        "procedural_memory",
    ],
    "project": [
        "project_state",
        "preference_constraint_memory",
        "procedural_memory",
        "artifact_memory",
    ],
    "procedure": [
        "procedural_memory",
        "preference_constraint_memory",
    ],
    "artifact": [
        "artifact_memory",
        "episodic_memory",
    ],
    "open_loops": [
        "working_state",
        "project_state",
    ],
}

# Stable insertion order from RECALL_MODE_TIERS (Python 3.7+ dict preserves order).
_VALID_RECALL_MODES: frozenset[str] = frozenset(RECALL_MODE_TIERS)
# Deterministic sequence used for the tool schema enum.
RECALL_MODE_ORDER: tuple[str, ...] = tuple(RECALL_MODE_TIERS.keys())


def tiers_for_recall_mode(recall_mode: str) -> list[str] | None:
    """Return the bounded tier list for a recall mode, or None if unknown.

    No recall mode means 'all tiers'. Unknown modes return None so callers
    can return a safe warning packet rather than defaulting to all memory.
    """
    return RECALL_MODE_TIERS.get(recall_mode)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

BRAINCASE_RENDER_TOOL_DEF: dict = {
    "type": "function",
    "name": "braincase.render",
    "description": (
        "Produce a bounded RenderPacket from explicitly stored BrainCase memory records. "
        "Use when exact record IDs or a narrow query are known. "
        "Returns bounded rendered_text and source_record_ids only. "
        "Does not expose raw StateRecords. "
        "Does not store anything. "
        "Does not ingest current conversation/request data. "
        "memory_domain must be supplied from configured context. "
        "If exact records are not known, use braincase.recall instead."
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

BRAINCASE_RECALL_TOOL_DEF: dict = {
    "type": "function",
    "name": "braincase.recall",
    "description": (
        "Produce a bounded RenderPacket from scoped BrainCase memory using predefined recall modes. "
        "Use when task continuity or project/domain memory would help and exact record IDs are not known. "
        "Returns rendered_text and source_record_ids only. "
        "Does not expose raw StateRecords. "
        "Does not store anything. "
        "Does not ingest current conversation/request data. "
        "memory_domain must be supplied from configured context. "
        "If exact records are known, use braincase.render instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "description": (
                    "What this recall is for, e.g. 'task_continuity', "
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
            "recall_mode": {
                "type": "string",
                "description": (
                    "Recall mode controlling which memory tiers are searched. "
                    "task: working/project state + procedures/preferences (default). "
                    "project: durable project state, constraints, artifacts. "
                    "procedure: how-to workflows and reusable procedures. "
                    "artifact: file/commit/doc references and episodic anchors. "
                    "open_loops: current unfinished work in working/project state."
                ),
                "enum": list(RECALL_MODE_ORDER),
                "default": "task",
            },
            "query": {
                "type": "string",
                "description": (
                    "Optional search query to filter records within the mode's tiers. "
                    "Omit for broad mode-scoped recall."
                ),
            },
            "tiers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional tier narrowing. Supplied tiers must be a subset of the "
                    "recall_mode's allowed tiers; out-of-mode tiers are dropped. "
                    "If the intersection is empty, a warning packet is returned."
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

BRAINCASE_WRITE_CANDIDATE_TOOL_DEF: dict = {
    "type": "function",
    "name": "braincase.write_candidate",
    "description": (
        "Creates a candidate-only BrainCase StateRecord for later operator review. "
        "Always stores status=candidate and visibility=internal. "
        "Does not create active or renderable memory. "
        "Does not ingest raw prompts, request bodies, telemetry, or logs. "
        "Does not expose raw StateRecords. "
        "Candidate records are not visible through braincase.render or braincase.recall. "
        "Use only for durable facts, project decisions, constraints, procedures, or reusable preferences. "
        "Do not use for ordinary chatter, transient observations, every turn, telemetry, or raw logs. "
        "memory_domain must be supplied from configured context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "description": "What this candidate record is for (e.g. 'project_constraint', 'procedure'). Required.",
            },
            "memory_domain": {
                "type": "string",
                "description": "Configured memory isolation domain. Must be from configured context. Do not guess.",
            },
            "tier": {
                "type": "string",
                "description": (
                    "Memory tier: working_state, session_state, project_state, "
                    "semantic_memory, procedural_memory, episodic_memory, "
                    "artifact_memory, perceptual_index, preference_constraint_memory."
                ),
                "enum": sorted(_VALID_TIERS),
            },
            "record_type": {
                "type": "string",
                "description": (
                    "Record type: constraint, preference, project_decision, project_state, "
                    "procedure, artifact_reference, diagnostic, open_question, "
                    "identity_note, correction, episode, recent_topic."
                ),
                "enum": sorted(_VALID_RECORD_TYPES),
            },
            "claim": {
                "type": "string",
                "description": "Durable claim or assertion. Must not include raw prompts, session logs, or request bodies.",
                "maxLength": 2000,
            },
            "summary": {
                "type": "string",
                "description": "Brief recall-readable summary. Must not include raw prompts or logs.",
                "maxLength": 1000,
            },
            "confidence": {
                "type": "number",
                "description": "Confidence 0.0–1.0. Default 0.5.",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            },
            "importance": {
                "type": "number",
                "description": "Importance for future ranking 0.0–1.0. Default 0.5.",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            },
            "retention": {
                "type": "string",
                "description": "Intended lifetime. Default project.",
                "enum": sorted(_VALID_RETENTIONS),
                "default": "project",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for search/retrieval.",
            },
            "source_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional source_ref_id strings providing provenance.",
            },
            "why_it_matters": {
                "type": "string",
                "description": "Optional explanation for the reviewer (max 500 chars).",
                "maxLength": 500,
            },
            "review_note": {
                "type": "string",
                "description": "Optional note for the operator reviewer (max 500 chars).",
                "maxLength": 500,
            },
        },
        "required": ["purpose", "memory_domain", "tier", "record_type", "claim", "summary"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Harness policy text
# ---------------------------------------------------------------------------

_HARNESS_READ_SECTION: str = """\
**braincase.recall** — use for scoped task/project memory when exact records are not known.
- Choose a recall_mode: task (default), project, procedure, artifact, open_loops.
- Supply memory_domain explicitly from configured session context.
- Optionally narrow with query for search-backed recall.
- Keep budget_tokens small (default 600).
- Do not use as a broad memory dump. Do not assume memory exists.
- If memory_domain is unknown, do not call the tool.

**braincase.render** — use when exact record_ids or a narrow query are known.
- Prefer when you know what specific records to render.
- Supply memory_domain explicitly.

Both return RenderPacket only. rendered_text and source_record_ids are the only
model-visible output. Raw records are never exposed."""

_HARNESS_WRITE_CANDIDATE_SECTION: str = """\
**braincase.write_candidate** — use only for durable facts worth operator review.
- Use for stable project constraints, decisions, reusable procedures, or preferences.
- Do NOT use for ordinary chatter, transient observations, every turn, raw logs, or telemetry.
- Supply memory_domain from configured session context.
- Do not try to set status or visibility — they are forced to candidate/internal.
- Returns WriteCandidateResult (not a RenderPacket). Candidate is NOT immediately recalled.
- Operator review is required before memory becomes active/renderable.

Not yet exposed: braincase.write, braincase.update, braincase.search,
braincase.inspect, braincase.promote_candidate."""

_HARNESS_READ_ONLY_FOOTER: str = """\
Not yet exposed: braincase.write, braincase.update, braincase.search,
braincase.inspect. These remain internal until future slices define their
semantics and operator exposure policies."""

BRAINCASE_HARNESS_POLICY: str = (
    "## BrainCase Memory Tools\n\n"
    "BrainCase memory is opt-in and tool-mediated. Use only when scoped project/domain\n"
    "memory would meaningfully help the current task.\n\n"
    + _HARNESS_READ_SECTION
    + "\n\n"
    + _HARNESS_READ_ONLY_FOOTER
)

BRAINCASE_HARNESS_POLICY_WITH_WRITE: str = (
    "## BrainCase Memory Tools\n\n"
    "BrainCase memory is opt-in and tool-mediated. Use only when scoped project/domain\n"
    "memory would meaningfully help the current task.\n\n"
    + _HARNESS_READ_SECTION
    + "\n\n"
    + _HARNESS_WRITE_CANDIDATE_SECTION
)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def _env_truthy(source: dict, key: str) -> bool:
    return str(source.get(key, "")).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def is_braincase_tools_enabled(env: dict | None = None) -> bool:
    """Return True if QZ_BRAINCASE_TOOLS_ENABLED is set to a truthy value."""
    return _env_truthy(os.environ if env is None else env, QZ_BRAINCASE_TOOLS_ENABLED_ENV)


def is_braincase_write_candidate_enabled(env: dict | None = None) -> bool:
    """Return True when BOTH QZ_BRAINCASE_TOOLS_ENABLED and
    QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED are set to truthy values.

    write_candidate requires both flags because write exposure is higher-risk
    than read. Operators enabling render/recall should not automatically enable
    candidate writes.
    """
    source = os.environ if env is None else env
    return (
        _env_truthy(source, QZ_BRAINCASE_TOOLS_ENABLED_ENV)
        and _env_truthy(source, QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV)
    )


def get_braincase_tool_definitions(env: dict | None = None) -> list[dict]:
    """Return braincase tool definitions based on enabled flags.

    Returns [] when QZ_BRAINCASE_TOOLS_ENABLED is disabled (default).
    Returns [render, recall] when only QZ_BRAINCASE_TOOLS_ENABLED is true.
    Returns [render, recall, write_candidate] when both flags are true.

    braincase.write, update, search, inspect, promote_candidate are never included.
    """
    if not is_braincase_tools_enabled(env):
        return []
    defs = [BRAINCASE_RENDER_TOOL_DEF, BRAINCASE_RECALL_TOOL_DEF]
    if is_braincase_write_candidate_enabled(env):
        defs.append(BRAINCASE_WRITE_CANDIDATE_TOOL_DEF)
    return defs


def get_braincase_harness_policy(env: dict | None = None) -> str | None:
    """Return the BrainCase harness policy text based on enabled flags.

    Returns None when QZ_BRAINCASE_TOOLS_ENABLED is not set (default).
    Returns read-only policy when only QZ_BRAINCASE_TOOLS_ENABLED is true.
    Returns read+write_candidate policy when both flags are true.
    """
    if not is_braincase_tools_enabled(env):
        return None
    if is_braincase_write_candidate_enabled(env):
        return BRAINCASE_HARNESS_POLICY_WITH_WRITE
    return BRAINCASE_HARNESS_POLICY

# ---------------------------------------------------------------------------
# braincase.render executor (Slice F, unchanged)
# ---------------------------------------------------------------------------

def braincase_render_tool(db: "BrainCaseDB", args: dict) -> dict:
    """Execute braincase.render for the given args dict.

    Returns a RenderPacket-shaped dict. Never raises.
    Returns a warning packet for missing required args or disabled DB.
    Does not write records. No automatic ingestion occurs.
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

    if not purpose or not isinstance(purpose, str) or not purpose.strip():
        return _warning_packet(
            "purpose_required",
            purpose=str(purpose) if purpose is not None else "unknown",
            memory_domain=str(memory_domain) if memory_domain else "",
            ts=ts,
        )

    budget_tokens = _clamp_budget(args.get("budget_tokens", 600))
    limit = _clamp_limit(args.get("limit", 12))
    query = args.get("query")
    tiers = args.get("tiers")
    record_ids = args.get("record_ids")

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

# ---------------------------------------------------------------------------
# braincase.recall — internal packet builder (Slice G)
# ---------------------------------------------------------------------------

def braincase_recall_packet(
    db: "BrainCaseDB",
    *,
    purpose: str,
    memory_domain: str | None = None,
    query: str | None = None,
    tiers: list[str] | None = None,
    recall_mode: str = "task",
    budget_tokens: int = 600,
    limit: int = 12,
    now_ms: int | None = None,
) -> dict:
    """Retrieve stored StateRecords via tier-routed recall and render a RenderPacket.

    Recall semantics:
      1. Validate purpose, memory_domain, and recall_mode.
      2. Resolve effective tiers from recall_mode (+ optional caller narrowing).
      3. Search or list records within memory_domain restricted to effective tiers.
      4. Call render_pack() to produce a bounded RenderPacket.

    Tier narrowing:
      - Caller-supplied tiers must be a subset of the recall_mode's allowed tiers.
      - Out-of-mode tiers are dropped silently; the intersection is used.
      - If the intersection is empty, a warning packet is returned.
      - Caller-supplied tiers cannot widen beyond the mode's allowed tiers.

    Returns a RenderPacket dict. Never raises.
    Does not write records. No automatic ingestion occurs.
    """
    try:
        from .qz_braincase_render import make_render_packet_id, render_pack
    except ImportError:
        from qz_braincase_render import make_render_packet_id, render_pack

    ts = now_ms if now_ms is not None else int(time.time() * 1000)

    # Validate purpose
    if not purpose or not isinstance(purpose, str) or not purpose.strip():
        return _warning_packet(
            "purpose_required",
            purpose=str(purpose) if purpose is not None else "unknown",
            memory_domain=str(memory_domain) if memory_domain else "",
            ts=ts,
        )

    # Validate memory_domain
    if not memory_domain or not isinstance(memory_domain, str) or not memory_domain.strip():
        return _warning_packet(
            "memory_domain_required",
            purpose=purpose,
            memory_domain="",
            ts=ts,
        )

    # Guard disabled DB before attempting any retrieval
    if not db.enabled:
        return _warning_packet(
            "braincase_db_disabled",
            purpose=purpose,
            memory_domain=memory_domain,
            ts=ts,
        )

    # Validate recall_mode
    mode_tiers = tiers_for_recall_mode(recall_mode)
    if mode_tiers is None:
        return _warning_packet(
            "unknown_recall_mode",
            purpose=purpose,
            memory_domain=memory_domain,
            ts=ts,
        )

    # Resolve effective tiers (caller narrowing — no widening allowed).
    # Out-of-mode tiers are dropped; a warning is appended to the final packet.
    # If the intersection is empty, a warning packet is returned immediately.
    dropped_out_of_mode: bool = False
    effective_tiers: list[str]
    if tiers is not None and isinstance(tiers, list):
        mode_tier_set = set(mode_tiers)
        intersection = [t for t in tiers if isinstance(t, str) and t in mode_tier_set]
        if not intersection:
            return _warning_packet(
                "tier_not_allowed_for_mode",
                purpose=purpose,
                memory_domain=memory_domain,
                ts=ts,
            )
        if len(intersection) < len([t for t in tiers if isinstance(t, str)]):
            dropped_out_of_mode = True
        effective_tiers = intersection
    else:
        effective_tiers = list(mode_tiers)

    budget_tokens = _clamp_budget(budget_tokens)
    limit = _clamp_limit(limit)

    # Tier-bounded retrieval: query/list each tier separately before the limit
    # so in-mode records are never starved by out-of-mode results.
    candidates = _recall_candidate_records(
        db,
        memory_domain=memory_domain,
        query=query if isinstance(query, str) else None,
        effective_tiers=effective_tiers,
        limit=limit,
    )

    packet = render_pack(
        candidates,
        purpose=purpose,
        memory_domain=memory_domain,
        budget_tokens=budget_tokens,
        tiers=effective_tiers,
        now_ms=ts,
    )

    if dropped_out_of_mode:
        packet["warnings"].append("tier_narrowing_dropped_out_of_mode")

    return packet

# ---------------------------------------------------------------------------
# braincase.recall executor (Slice G)
# ---------------------------------------------------------------------------

def braincase_recall_tool(db: "BrainCaseDB", args: dict) -> dict:
    """Execute braincase.recall for the given args dict.

    Returns a RenderPacket-shaped dict. Never raises.
    Returns a warning packet for missing required args, unknown recall_mode,
    empty tier intersection, or disabled/unavailable DB.
    Does not write records. No automatic ingestion occurs.

    Required args:
      purpose: str
      memory_domain: str

    Optional args:
      recall_mode: str (default "task")
      query: str
      tiers: list[str] — narrowing only; widening beyond mode tiers is disallowed
      budget_tokens: int (default 600, clamped 80–2000)
      limit: int (default 12, clamped 1–50)
    """
    if not isinstance(args, dict):
        args = {}

    return braincase_recall_packet(
        db,
        purpose=args.get("purpose"),
        memory_domain=args.get("memory_domain"),
        query=args.get("query") if isinstance(args.get("query"), str) else None,
        tiers=args.get("tiers") if isinstance(args.get("tiers"), list) else None,
        recall_mode=args.get("recall_mode", "task") if isinstance(args.get("recall_mode"), str) else "task",
        budget_tokens=_clamp_budget(args.get("budget_tokens", 600)),
        limit=_clamp_limit(args.get("limit", 12)),
    )

# ---------------------------------------------------------------------------
# Internal retrieval helper (Slice G.1)
# ---------------------------------------------------------------------------

def _recall_candidate_records(
    db: "BrainCaseDB",
    *,
    memory_domain: str,
    query: str | None,
    effective_tiers: list[str],
    limit: int,
) -> list[dict]:
    """Retrieve candidate StateRecords bounded to effective_tiers before the limit.

    Queries each tier separately so the per-call limit is applied within
    each tier, not across all tiers combined. Without this, a single
    list/search call would return `limit` records across all tiers and
    valid in-mode records beyond the limit would be silently starved.

    Deduplicates by record_id (preserving first-seen occurrence).
    Ranks by importance desc, updated_at_ms desc, created_at_ms desc.
    Returns at most `limit` records.

    Returns [] on DB error or disabled DB (callers should guard DB state first).
    No writes. No automatic ingestion.
    """
    seen: dict[str, dict] = {}
    per_tier_limit = max(limit, 1)

    for tier in effective_tiers:
        if query:
            records = db.search_state_records(
                query,
                memory_domain=memory_domain,
                tier=tier,
                limit=per_tier_limit,
            )
        else:
            records = db.list_state_records(
                memory_domain=memory_domain,
                tier=tier,
                limit=per_tier_limit,
            )
        for rec in records:
            rid = rec.get("record_id")
            if rid and rid not in seen:
                seen[rid] = rec

    candidates = sorted(
        seen.values(),
        key=lambda r: (
            -float(r.get("importance") or 0),
            -int(r.get("updated_at_ms") or 0),
            -int(r.get("created_at_ms") or 0),
        ),
    )
    return candidates[:limit]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clamp_budget(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 80:
        return 600
    return min(value, 2000)


def _clamp_limit(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return 12
    return min(value, 50)


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
# braincase.write_candidate executor (Slice H.2)
# ---------------------------------------------------------------------------

def braincase_write_candidate_tool(db: "BrainCaseDB", args: dict) -> dict:
    """Execute braincase.write_candidate for the given args dict.

    Returns a WriteCandidateResult dict. Never raises.
    Does not produce a RenderPacket.
    Forced: status=candidate, visibility=internal. Always.
    Reject-first: forbidden fields → error, no storage.
    Claim/summary raw log detection: hard error, no storage.
    No automatic ingestion.
    """
    try:
        from .qz_braincase_write import braincase_write_state_record
    except ImportError:
        from qz_braincase_write import braincase_write_state_record

    if not isinstance(args, dict):
        args = {}

    ts = int(time.time() * 1000)

    def _error_result(errors: list[str]) -> dict:
        return {
            "ok": False,
            "stored": False,
            "record_id": None,
            "status": "candidate",
            "visibility": "internal",
            "review_required": True,
            "warnings": [],
            "errors": errors,
            "dedup_hint": None,
            "conflict_hint": None,
        }

    # 1. Reject forbidden top-level fields (reject-first)
    forbidden_present = sorted(_WRITE_CANDIDATE_FORBIDDEN_ARGS & set(args.keys()))
    if forbidden_present:
        return _error_result([f"forbidden_field: {f}" for f in forbidden_present])

    # 2. Required field validation
    purpose = args.get("purpose")
    memory_domain = args.get("memory_domain")
    tier = args.get("tier")
    record_type = args.get("record_type")
    claim = args.get("claim")
    summary = args.get("summary")

    if not purpose or not isinstance(purpose, str) or not purpose.strip():
        return _error_result(["purpose_required"])
    if not memory_domain or not isinstance(memory_domain, str) or not memory_domain.strip():
        return _error_result(["memory_domain_required"])
    if not tier or not isinstance(tier, str) or not tier.strip():
        return _error_result(["tier_required"])
    if tier not in _VALID_TIERS:
        return _error_result([f"invalid_tier: {tier}"])
    if not record_type or not isinstance(record_type, str) or not record_type.strip():
        return _error_result(["record_type_required"])
    if record_type not in _VALID_RECORD_TYPES:
        return _error_result([f"invalid_record_type: {record_type}"])
    if not claim or not isinstance(claim, str) or not claim.strip():
        return _error_result(["claim_required"])
    if not summary or not isinstance(summary, str) or not summary.strip():
        return _error_result(["summary_required"])

    # 3. Raw log/prompt smuggling detection in claim and summary (hard error).
    # Case-insensitive: both the text and the marker are lower-cased for matching.
    def _contains_raw_marker(text: str) -> str | None:
        lower = text.lower()
        for marker in _RAW_CONTENT_MARKERS:
            if marker.lower() in lower:
                return marker
        return None

    marker = _contains_raw_marker(claim)
    if marker:
        return _error_result([
            f"claim_content_rejected: raw log/prompt content detected ({marker!r})"
        ])
    marker = _contains_raw_marker(summary)
    if marker:
        return _error_result([
            f"summary_content_rejected: raw log/prompt content detected ({marker!r})"
        ])

    # 4. Clamp / default optional fields
    confidence = args.get("confidence", 0.5)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
            or not (0.0 <= float(confidence) <= 1.0):
        confidence = 0.5
    confidence = float(confidence)

    importance = args.get("importance", 0.5)
    if not isinstance(importance, (int, float)) or isinstance(importance, bool) \
            or not (0.0 <= float(importance) <= 1.0):
        importance = 0.5
    importance = float(importance)

    retention = args.get("retention", "project")
    if not isinstance(retention, str) or retention not in _VALID_RETENTIONS:
        retention = "project"

    tags_raw = args.get("tags", [])
    tags = [t for t in (tags_raw if isinstance(tags_raw, list) else []) if isinstance(t, str)]

    srefs_raw = args.get("source_refs", [])
    source_refs = [s for s in (srefs_raw if isinstance(srefs_raw, list) else []) if isinstance(s, str)]

    # 5. Build bounded review metadata (no raw args, no prompt/request bodies)
    review_meta: dict = {}
    for key, maxlen in (("why_it_matters", 500), ("review_note", 500), ("purpose", 200)):
        val = args.get(key) if key != "purpose" else purpose
        if isinstance(val, str) and val.strip():
            review_meta[key] = val[:maxlen]

    # 6. Construct StateRecord with forced candidate/internal (defensive backstop)
    record_id = f"rec_cand_{ts}_{uuid.uuid4().hex[:8]}"
    record = {
        "record_id": record_id,
        "schema": "braincase/state-record@1",
        "memory_domain": memory_domain,
        "tier": tier,
        "record_type": record_type,
        "claim": claim[:2000],
        "summary": summary[:1000],
        "status": "candidate",    # FORCED — model cannot change this
        "visibility": "internal", # FORCED — model cannot change this
        "confidence": confidence,
        "importance": importance,
        "retention": retention,
        "created_at_ms": ts,
        "updated_at_ms": ts,
        "source_refs": source_refs,
        "tags": tags,
        "supersedes": None,
        "superseded_by": None,
        "metadata": review_meta if review_meta else None,
    }

    # 7. Write via existing helper path (handles redaction, scope, dedup, conflict)
    write_result = braincase_write_state_record(db, record, source_refs=None)

    # 8. Convert helper result to bounded WriteCandidateResult (not RenderPacket)
    stored = bool(write_result.get("stored"))
    errors = list(write_result.get("errors") or [])
    warnings = list(write_result.get("warnings") or [])

    dedup_hint: str | None = None
    conflict_hint: str | None = None
    if stored:
        dedup = write_result.get("dedup") or {}
        dedup_hint = "possible_duplicate" if dedup.get("duplicates") else "no_duplicates"
        conflicts = write_result.get("conflicts") or {}
        conflict_hint = "possible_conflict" if conflicts.get("conflicts") else "no_conflicts"

    return {
        "ok": write_result.get("ok", False),
        "stored": stored,
        "record_id": record_id if stored else None,
        "status": "candidate",
        "visibility": "internal",
        "review_required": True,
        "warnings": warnings,
        "errors": errors,
        "dedup_hint": dedup_hint,
        "conflict_hint": conflict_hint,
    }


# ---------------------------------------------------------------------------
# Proxy-local tool executors (Slice G.2)
# ---------------------------------------------------------------------------

class _BraincaseBaseExecutor:
    """Duck-typed ProxyLocalToolExecutor for BrainCase tools.

    Implements the same interface as ProxyLocalToolExecutor without inheriting
    from it to avoid a circular import with qz_proxy_tools.py.

    Does not expose write/update/search/inspect.
    Does not add automatic ingestion.
    Does not expose raw StateRecords.
    """
    function_name: str = ""
    lifecycle: "ToolLifecycleSpec | None" = None

    def __init__(self, db: "BrainCaseDB | None" = None) -> None:
        self._db = db  # None → created from env at first execute call

    def _get_db(self) -> "BrainCaseDB":
        if self._db is not None:
            return self._db
        try:
            from .qz_braincase_db import BrainCaseDB
        except ImportError:
            from qz_braincase_db import BrainCaseDB
        db = BrainCaseDB.from_env()
        db.init()
        return db

    @staticmethod
    def _parse_args(call: dict) -> dict:
        """Parse function_call arguments JSON string into a dict."""
        raw = call.get("arguments")
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass
        elif isinstance(raw, dict):
            return raw
        return {}

    def is_call(self, call: dict) -> bool:
        return (
            isinstance(call, dict)
            and call.get("type") == "function_call"
            and call.get("name") == self.function_name
        )

    def started_public_item(self, call: dict, public_index: int) -> dict:
        """Return an in-progress function_call_output placeholder for SSE events."""
        call_id = call.get("call_id") or call.get("id") or f"{self.function_name}_{public_index}"
        item_id = call.get("id") or call_id
        return {
            "id": item_id,
            "type": "function_call_output",
            "call_id": call_id,
            "output": "",
            "status": "in_progress",
        }

    def _make_result(self, call: dict, result_dict: dict) -> "ToolContinuationResult":
        """Wrap a tool result dict into a ToolContinuationResult.

        Handles both RenderPacket (render/recall) and WriteCandidateResult
        (write_candidate) — any JSON-serialisable dict works.

        public_item: the function_call_output emitted to the Codex client.
        upstream_items: (function_call, function_call_output) sent to the backend
            on the next continuation hop so the model sees the result.
        """
        call_id = call.get("call_id") or call.get("id") or ""
        item_id = call.get("id") or call_id
        output = json.dumps(result_dict, ensure_ascii=False)
        output_item: dict = {
            "id": item_id,
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
            "status": "completed",
        }
        return ToolContinuationResult(
            public_item=output_item,
            upstream_items=(call, output_item),
        )

    def execute(
        self,
        call: dict,
        context: "ProxyToolExecutionContext",
    ) -> "ToolContinuationResult":
        raise NotImplementedError


class BraincaseRenderProxyToolExecutor(_BraincaseBaseExecutor):
    """Proxy-local executor for braincase.render tool calls."""

    function_name = "braincase.render"
    lifecycle = ToolLifecycleSpec(
        name="braincase.render",
        execution="proxy_local",
        public_item_type="function_call_output",
        telemetry_name="braincase_render",
        continuation_hops=1,
    )

    def execute(
        self,
        call: dict,
        context: "ProxyToolExecutionContext",
    ) -> "ToolContinuationResult":
        db = self._get_db()
        args = self._parse_args(call)
        packet = braincase_render_tool(db, args)
        return self._make_result(call, packet)


class BraincaseRecallProxyToolExecutor(_BraincaseBaseExecutor):
    """Proxy-local executor for braincase.recall tool calls."""

    function_name = "braincase.recall"
    lifecycle = ToolLifecycleSpec(
        name="braincase.recall",
        execution="proxy_local",
        public_item_type="function_call_output",
        telemetry_name="braincase_recall",
        continuation_hops=1,
    )

    def execute(
        self,
        call: dict,
        context: "ProxyToolExecutionContext",
    ) -> "ToolContinuationResult":
        db = self._get_db()
        args = self._parse_args(call)
        packet = braincase_recall_tool(db, args)
        return self._make_result(call, packet)


class BraincaseWriteCandidateProxyToolExecutor(_BraincaseBaseExecutor):
    """Proxy-local executor for braincase.write_candidate tool calls.

    Returns WriteCandidateResult as function_call_output JSON.
    Forced status=candidate, visibility=internal.
    Reject-first for forbidden fields. Hard error for raw log in claim/summary.
    No raw StateRecords. No automatic ingestion.
    """

    function_name = "braincase.write_candidate"
    lifecycle = ToolLifecycleSpec(
        name="braincase.write_candidate",
        execution="proxy_local",
        public_item_type="function_call_output",
        telemetry_name="braincase_write_candidate",
        continuation_hops=1,
    )

    def execute(
        self,
        call: dict,
        context: "ProxyToolExecutionContext",
    ) -> "ToolContinuationResult":
        db = self._get_db()
        args = self._parse_args(call)
        result = braincase_write_candidate_tool(db, args)
        return self._make_result(call, result)


def make_braincase_tool_executors(
    db: "BrainCaseDB | None" = None,
    env: "dict | None" = None,
) -> list:
    """Return BrainCase proxy-local executors based on enabled flags.

    env: optional dict to check instead of os.environ (useful for tests).
         Omit or pass None to use os.environ (production default).

    Returns [] when QZ_BRAINCASE_TOOLS_ENABLED is not set.
    Returns [render, recall] when only QZ_BRAINCASE_TOOLS_ENABLED is true.
    Returns [render, recall, write_candidate] when both flags are true.

    write/update/search/inspect/promote_candidate are never included.
    No automatic ingestion. No raw StateRecord exposure.
    """
    if not is_braincase_tools_enabled(env):
        return []
    executors: list = [
        BraincaseRenderProxyToolExecutor(db=db),
        BraincaseRecallProxyToolExecutor(db=db),
    ]
    if is_braincase_write_candidate_enabled(env):
        executors.append(BraincaseWriteCandidateProxyToolExecutor(db=db))
    return executors


# ---------------------------------------------------------------------------
# Body injection helper
# ---------------------------------------------------------------------------

def inject_braincase_tools_to_body(body: dict, *, env: dict | None = None) -> dict:
    """Inject braincase tool definitions into body["tools"] if enabled.

    No-op when QZ_BRAINCASE_TOOLS_ENABLED is not set (default).
    Idempotent: does not add duplicates if tools are already present.

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
