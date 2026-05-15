#!/usr/bin/env python3
"""Slices F/G/G.1: BrainCase harness/tool plane — braincase.render + braincase.recall.

Feature flag: QZ_BRAINCASE_TOOLS_ENABLED (default: disabled).

When disabled (default):
  - No tool definitions are injected into body["tools"].
  - No harness policy text is added to the turn harness.
  - No runtime behaviour changes.
  - Forwarded /v1/responses bodies are not mutated.

When enabled:
  - braincase.render and braincase.recall are injected into body["tools"].
  - Compact harness policy text is added to the turn harness.
  - braincase_render_tool() dispatches to braincase_render_packet().
  - braincase_recall_tool() dispatches to braincase_recall_packet().
  - DB availability is checked at execution time; disabled DB returns a
    safe warning packet rather than failing the proxy request.

braincase.recall semantics (Slices G/G.1):
  Recall is tier-routed retrieval → scoped filtering → bounded RenderPacket.
  It is NOT a raw dump, not a search-all, not a cross-domain recall.
  Predefined recall modes control which memory tiers are searched.
  RenderPacket is the only model-visible memory output.

Not exposed (intentionally):
  braincase.search, braincase.inspect, braincase.write, braincase.update.
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

import json
import os
import time
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

_RENDER_PACKET_SCHEMA = "braincase/render-packet@1"

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

# ---------------------------------------------------------------------------
# Harness policy text (updated for Slice G)
# ---------------------------------------------------------------------------

BRAINCASE_HARNESS_POLICY: str = """\
## BrainCase Memory Tools

BrainCase memory is opt-in and tool-mediated. Use only when scoped project/domain
memory would meaningfully help the current task.

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

Both tools return RenderPacket only. rendered_text and source_record_ids are
the only model-visible output. Raw records are never exposed.

Not yet exposed: braincase.write, braincase.update, braincase.search,
braincase.inspect. These remain internal until future slices define their
semantics and operator exposure policies."""

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_braincase_tools_enabled(env: dict | None = None) -> bool:
    """Return True if QZ_BRAINCASE_TOOLS_ENABLED is set to a truthy value."""
    source = os.environ if env is None else env
    value = source.get(QZ_BRAINCASE_TOOLS_ENABLED_ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def get_braincase_tool_definitions(env: dict | None = None) -> list[dict]:
    """Return braincase tool definitions when the feature flag is enabled.

    Returns [] when disabled (default).
    When enabled, returns [BRAINCASE_RENDER_TOOL_DEF, BRAINCASE_RECALL_TOOL_DEF].

    braincase.search, inspect, write, update are never included.
    """
    if not is_braincase_tools_enabled(env):
        return []
    return [BRAINCASE_RENDER_TOOL_DEF, BRAINCASE_RECALL_TOOL_DEF]


def get_braincase_harness_policy(env: dict | None = None) -> str | None:
    """Return the compact BrainCase harness policy text when the flag is enabled.

    Returns None when disabled (default).
    """
    if not is_braincase_tools_enabled(env):
        return None
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

    def _make_result(self, call: dict, packet: dict) -> "ToolContinuationResult":
        """Wrap a RenderPacket dict into a ToolContinuationResult.

        public_item: the function_call_output emitted to the Codex client.
        upstream_items: (function_call, function_call_output) sent to the backend
            on the next continuation hop so the model sees the result.
        """
        call_id = call.get("call_id") or call.get("id") or ""
        item_id = call.get("id") or call_id
        output = json.dumps(packet, ensure_ascii=False)
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


def make_braincase_tool_executors(db: "BrainCaseDB | None" = None) -> list:
    """Return BrainCase proxy-local executors when QZ_BRAINCASE_TOOLS_ENABLED is set.

    Returns [] when disabled (default). When enabled, returns executors for
    braincase.render and braincase.recall.

    write/update/search/inspect are never included.
    No automatic ingestion. No raw StateRecord exposure.
    """
    if not is_braincase_tools_enabled():
        return []
    return [
        BraincaseRenderProxyToolExecutor(db=db),
        BraincaseRecallProxyToolExecutor(db=db),
    ]


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
