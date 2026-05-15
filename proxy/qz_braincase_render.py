#!/usr/bin/env python3
"""Internal BrainCase render packet builder.

Slice E: renders stored StateRecords into bounded RenderPacket-shaped output.

The RenderPacket is the model-facing boundary in the BrainCase memory plane.
Raw StateRecords remain internal until explicitly rendered here.

This module does NOT:
- inject RenderPackets into prompts (deferred to Slice F)
- expose model-facing tool APIs (deferred to Slice F)
- add automatic ingestion
- make raw StateRecords model-visible

Eligibility rules (enforced by eligible_for_render):
  - status == "active"
  - visibility == "renderable"
  - memory_domain == requested memory_domain
  - tier in requested tiers, if tiers specified

Records with visibility "internal" or "never_model_visible" are excluded.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proxy.qz_braincase_db import BrainCaseDB

_SCHEMA = "braincase/render-packet@1"

# Forbidden raw-content field names that must never appear in rendered output.
_FORBIDDEN_OUTPUT_FIELDS = frozenset({
    "raw_prompt",
    "raw_request_body",
    "request_body",
    "full_log",
    "telemetry_event",
    "stream_event",
    "metadata_json",
})


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def render_budget_chars(budget_tokens: int) -> int:
    """Conservative character budget from a token budget (4 chars per token)."""
    return max(80, budget_tokens * 4)


def make_render_packet_id(now_ms: int, purpose: str, memory_domain: str) -> str:
    """Generate a deterministic packet ID from timestamp, purpose, and domain."""
    safe_p = re.sub(r"[^a-z0-9_]", "_", purpose.lower())[:12]
    safe_d = re.sub(r"[^a-z0-9_]", "_", memory_domain.lower())[:8]
    return f"pkt_{now_ms}_{safe_p}_{safe_d}"


def eligible_for_render(
    record: dict,
    memory_domain: str,
    tiers: list[str] | None = None,
) -> tuple[bool, str | None]:
    """Return (True, None) if record may be rendered, (False, reason) if not.

    Criteria:
      status == "active"
      visibility == "renderable"
      record.memory_domain == memory_domain
      tier in tiers (if tiers specified)
    """
    if record.get("status") != "active":
        return False, f"status={record.get('status')}"
    if record.get("visibility") != "renderable":
        return False, f"visibility={record.get('visibility')}"
    if record.get("memory_domain") != memory_domain:
        return False, f"memory_domain mismatch: {record.get('memory_domain')} != {memory_domain}"
    if tiers is not None and record.get("tier") not in tiers:
        return False, f"tier={record.get('tier')} not in {tiers}"
    return True, None


def render_record_line(record: dict) -> str:
    """Format a single StateRecord for inclusion in rendered_text.

    Includes: tier/record_type classification, claim (truncated if long),
    summary (if present and fits), source record_id marker.

    Does NOT include: raw metadata JSON, forbidden raw fields, full SourceRef details.
    """
    tier = record.get("tier", "unknown")
    record_type = record.get("record_type", "unknown")
    claim = str(record.get("claim") or "")
    summary = str(record.get("summary") or "")
    record_id = str(record.get("record_id") or "")

    # Truncate long claims
    if len(claim) > 200:
        claim = claim[:197] + "..."
    # Truncate long summaries
    if len(summary) > 150:
        summary = summary[:147] + "..."

    parts = [f"[{tier}/{record_type}] {claim}"]
    if summary and summary != claim:
        parts.append(f"   Summary: {summary}")
    parts.append(f"   Source: {record_id}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core renderer
# ---------------------------------------------------------------------------

def render_pack(
    records: list[dict],
    *,
    purpose: str,
    memory_domain: str,
    budget_tokens: int = 600,
    tiers: list[str] | None = None,
    now_ms: int | None = None,
    packet_id: str | None = None,
) -> dict:
    """Produce a RenderPacket dict from a list of candidate StateRecords.

    Filters ineligible records, ranks eligible ones, assembles bounded text,
    and returns a RenderPacket-shaped dict.

    Raw StateRecords remain internal; only the rendered text is model-facing.

    Returns:
        {packet_id, schema, purpose, memory_domain, generated_at_ms,
         budget_tokens, rendered_text, source_record_ids, omitted_count,
         warnings, metadata}
    """
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    pid = packet_id or make_render_packet_id(ts, purpose, memory_domain)
    chars_budget = render_budget_chars(budget_tokens)

    # 1. Filter eligible records
    eligible: list[dict] = []
    for rec in records:
        ok, _ = eligible_for_render(rec, memory_domain, tiers)
        if ok:
            eligible.append(rec)

    # 2. Rank: importance desc, updated_at_ms desc, created_at_ms desc
    eligible.sort(
        key=lambda r: (
            -float(r.get("importance") or 0),
            -int(r.get("updated_at_ms") or 0),
            -int(r.get("created_at_ms") or 0),
        )
    )

    # 3. Build rendered text within budget
    header = f"== BrainCase Memory Packet: {purpose} ({memory_domain}) =="
    body_parts: list[str] = []
    source_ids: list[str] = []
    omitted = 0
    warnings: list[str] = []

    # Reserve overhead for header, blank line, and footer estimate
    footer_est = len("[records=99 omitted=99]")
    overhead = len(header) + 1 + footer_est + 4
    body_budget = max(0, chars_budget - overhead)
    body_used = 0
    item_num = 0

    for rec in eligible:
        item_text = render_record_line(rec)
        numbered = f"{item_num + 1}. {item_text}\n"
        # Always include at least one record even if it marginally exceeds budget.
        if body_used + len(numbered) > body_budget and item_num > 0:
            omitted += 1
            if "budget_exhausted" not in warnings:
                warnings.append("budget_exhausted")
            continue
        item_num += 1
        body_parts.append(f"{item_num}. {item_text}")
        body_parts.append("")
        source_ids.append(rec["record_id"])
        body_used += len(numbered)

    footer = f"[records={item_num} omitted={omitted}]"
    all_parts = [header, ""] + body_parts + [footer]
    rendered_text = "\n".join(all_parts)

    return {
        "packet_id": pid,
        "schema": _SCHEMA,
        "purpose": purpose,
        "memory_domain": memory_domain,
        "generated_at_ms": ts,
        "budget_tokens": budget_tokens,
        "rendered_text": rendered_text,
        "source_record_ids": source_ids,
        "omitted_count": omitted,
        "warnings": warnings,
        "metadata": None,
    }


# ---------------------------------------------------------------------------
# DB-backed render entry point
# ---------------------------------------------------------------------------

def braincase_render_packet(
    db: "BrainCaseDB",
    *,
    purpose: str,
    memory_domain: str | None = None,
    query: str | None = None,
    tiers: list[str] | None = None,
    record_ids: list[str] | None = None,
    budget_tokens: int = 600,
    limit: int = 12,
    now_ms: int | None = None,
) -> dict:
    """Retrieve stored StateRecords and render a bounded RenderPacket.

    Retrieval priority:
      1. record_ids supplied -> inspect those specific records
      2. query supplied -> search records by query within memory_domain
      3. neither -> list records by memory_domain

    memory_domain is required. Returns a warning packet if missing.
    Disabled DB returns a warning packet with no exception raised.

    Does not inject the packet into prompts or expose it to the model.
    No automatic ingestion occurs.
    """
    ts = now_ms if now_ms is not None else int(time.time() * 1000)

    def _empty_packet(warning: str) -> dict[str, Any]:
        return {
            "packet_id": make_render_packet_id(ts, purpose, memory_domain or "unknown"),
            "schema": _SCHEMA,
            "purpose": purpose,
            "memory_domain": memory_domain or "",
            "generated_at_ms": ts,
            "budget_tokens": budget_tokens,
            "rendered_text": "",
            "source_record_ids": [],
            "omitted_count": 0,
            "warnings": [warning],
            "metadata": None,
        }

    if not memory_domain:
        return _empty_packet("memory_domain_required")

    if not db.enabled:
        return _empty_packet("braincase_db_disabled")

    # Retrieve candidate records
    records: list[dict] = []
    if record_ids:
        entries = db.inspect_state_records(record_ids, include_source_refs=False)
        records = [e["record"] for e in entries if e.get("record") is not None]
    elif query:
        records = db.search_state_records(
            query,
            memory_domain=memory_domain,
            tier=None,
            limit=limit,
        )
    else:
        records = db.list_state_records(memory_domain=memory_domain, limit=limit)

    return render_pack(
        records,
        purpose=purpose,
        memory_domain=memory_domain,
        budget_tokens=budget_tokens,
        tiers=tiers,
        now_ms=ts,
    )
