#!/usr/bin/env python3
"""Operator review helpers for BrainCase candidate StateRecords — Slice I.

Allows operators to list, inspect, promote, and reject candidate records
created by braincase.write_candidate.

Promotion is NOT model-facing. No model tool can call these functions.
Only the operator CLI (scripts/qz-braincase-review) uses this module.

Promotion path:
  candidate/internal → operator review → active/renderable or active/internal

Rejection path:
  candidate/internal → operator reject → retired

Critical invariants:
  - Only status=candidate records can be promoted or rejected here.
  - Promotion re-runs redaction_check (hard block on failure).
  - Promotion re-runs dedup_check and conflict_check (hints, non-blocking).
  - Promoted renderable records become eligible for braincase.render/recall.
  - Promoted internal records remain excluded from braincase.render/recall.
  - Rejected (retired) records remain excluded from braincase.render/recall.
  - No automatic ingestion. No model-facing exposure.
  - Dry-run returns what would happen without writing.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from proxy.qz_braincase_db import BrainCaseDB

try:
    from .qz_braincase_write import (
        conflict_check,
        dedup_check,
        redaction_check,
    )
except ImportError:
    from qz_braincase_write import (
        conflict_check,
        dedup_check,
        redaction_check,
    )

_SAFE_INSPECT_FIELDS = frozenset({
    "record_id", "schema", "memory_domain", "tier", "record_type",
    "claim", "summary", "status", "visibility", "confidence", "importance",
    "retention", "created_at_ms", "updated_at_ms", "tags",
    "source_refs", "supersedes", "superseded_by", "metadata",
})


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def list_candidate_records(
    db: "BrainCaseDB",
    *,
    memory_domain: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return bounded summaries of candidate StateRecords.

    Returns only status=candidate records.
    Does not return active, retired, or superseded records.
    Does not return raw source blobs or prompt/request bodies.
    No automatic ingestion.
    """
    if not db.enabled:
        return []

    all_recs = db.list_state_records(
        memory_domain=memory_domain,
        limit=max(limit * 4, 200),  # over-fetch to filter by candidate status
    )

    candidates = [r for r in all_recs if r.get("status") == "candidate"][:limit]

    return [
        {
            "record_id": r.get("record_id"),
            "memory_domain": r.get("memory_domain"),
            "tier": r.get("tier"),
            "record_type": r.get("record_type"),
            "importance": r.get("importance"),
            "confidence": r.get("confidence"),
            "tags": r.get("tags") or [],
            "claim": (r.get("claim") or "")[:200],
            "summary": (r.get("summary") or "")[:100],
            "created_at_ms": r.get("created_at_ms"),
        }
        for r in candidates
    ]


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------

def inspect_candidate_record(
    db: "BrainCaseDB",
    record_id: str,
) -> dict:
    """Return operator-facing details for a StateRecord.

    Returns a structured error if the record is not found or DB is disabled.
    Allows inspecting any status (not just candidate), because operators may
    need to inspect promoted or rejected records too.
    Filters out forbidden raw fields.
    No automatic ingestion.
    """
    if not db.enabled:
        return {"ok": False, "error": "braincase_db_disabled", "record_id": record_id}

    rec = db.get_state_record(record_id)
    if rec is None:
        return {"ok": False, "error": "not_found", "record_id": record_id}

    # Return only the safe operator-visible fields
    safe = {k: v for k, v in rec.items() if k in _SAFE_INSPECT_FIELDS}
    return {"ok": True, "record_id": record_id, "record": safe}


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def promote_candidate_record(
    db: "BrainCaseDB",
    record_id: str,
    *,
    visibility: str = "renderable",
    reason: str = "operator promotion",
    now_ms: int | None = None,
    allowed_memory_domains: list[str] | None = None,
    current_memory_domain: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Promote a candidate StateRecord to active/renderable or active/internal.

    Promotion policy:
      - Only status=candidate records can be promoted.
      - Re-runs redaction_check (hard block).
      - Re-runs dedup_check and conflict_check (hints, non-blocking).
      - allowed_memory_domains guard if supplied.
      - Dry-run: returns what would happen, no DB write.

    Returns a bounded PromotionResult dict.
    Not model-facing. Operator CLI only.
    No automatic ingestion.
    """
    ts = now_ms if now_ms is not None else int(time.time() * 1000)

    def _err(errors: list[str], warnings: list[str] | None = None) -> dict:
        return {
            "ok": False,
            "record_id": record_id,
            "promoted": False,
            "dry_run": dry_run,
            "status": None,
            "visibility": None,
            "warnings": list(warnings or []),
            "errors": errors,
            "dedup_hint": None,
            "conflict_hint": None,
        }

    if not db.enabled:
        return _err(["braincase_db_disabled"])

    if visibility not in ("renderable", "internal"):
        return _err([f"invalid_visibility: {visibility!r} (must be renderable or internal)"])

    rec = db.get_state_record(record_id)
    if rec is None:
        return _err(["record_not_found"])

    if rec.get("status") != "candidate":
        return _err([f"not_candidate: record status is {rec.get('status')!r}"])

    # Domain guard
    warnings: list[str] = []
    if allowed_memory_domains is not None:
        domain = rec.get("memory_domain")
        if domain not in allowed_memory_domains:
            return _err([
                f"memory_domain_mismatch: record domain {domain!r} "
                f"not in allowed {allowed_memory_domains}"
            ])
    if current_memory_domain is not None:
        domain = rec.get("memory_domain")
        if domain != current_memory_domain:
            warnings.append(
                f"memory_domain_differs: record {domain!r} vs current {current_memory_domain!r}"
            )

    # Redaction check (hard block)
    redaction = redaction_check(rec)
    if not redaction["ok"]:
        return _err(redaction["errors"], warnings)

    # Dedup hint (non-blocking)
    dedup = dedup_check(db, rec)
    dedup_hint = dedup.get("warning") or "no_duplicates"
    if dedup.get("warning"):
        warnings.append(f"dedup: {dedup['warning']}")

    # Conflict hint (non-blocking)
    conflicts = conflict_check(db, rec)
    conflict_hint = conflicts.get("warning") or "no_conflicts"
    if conflicts.get("warning"):
        warnings.append(f"conflict: {conflicts['warning']}")

    if dry_run:
        return {
            "ok": True,
            "record_id": record_id,
            "promoted": False,
            "dry_run": True,
            "status": "active",
            "visibility": visibility,
            "warnings": warnings,
            "errors": [],
            "dedup_hint": dedup_hint,
            "conflict_hint": conflict_hint,
        }

    # Execute promotion
    ok = db.promote_state_record(
        record_id,
        new_status="active",
        new_visibility=visibility,
        reason=reason,
        now_ms=ts,
    )
    if not ok:
        return _err([db.last_error or "promote_state_record failed"], warnings)

    return {
        "ok": True,
        "record_id": record_id,
        "promoted": True,
        "dry_run": False,
        "status": "active",
        "visibility": visibility,
        "warnings": warnings,
        "errors": [],
        "dedup_hint": dedup_hint,
        "conflict_hint": conflict_hint,
    }


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------

def reject_candidate_record(
    db: "BrainCaseDB",
    record_id: str,
    *,
    reason: str = "operator rejected candidate",
    now_ms: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Retire a candidate StateRecord as operator-rejected.

    Only status=candidate records can be rejected via this function.
    Rejection uses the existing retire_state_record() path.
    Retired records remain excluded from braincase.render/recall.
    Dry-run returns what would happen without writing.

    Not model-facing. Operator CLI only.
    No automatic ingestion.
    """
    def _err(errors: list[str]) -> dict:
        return {
            "ok": False,
            "record_id": record_id,
            "rejected": False,
            "dry_run": dry_run,
            "status": None,
            "warnings": [],
            "errors": errors,
        }

    if not db.enabled:
        return _err(["braincase_db_disabled"])

    rec = db.get_state_record(record_id)
    if rec is None:
        return _err(["record_not_found"])

    if rec.get("status") != "candidate":
        return _err([f"not_candidate: record status is {rec.get('status')!r}"])

    if dry_run:
        return {
            "ok": True,
            "record_id": record_id,
            "rejected": False,
            "dry_run": True,
            "status": "retired",
            "warnings": [],
            "errors": [],
        }

    ok = db.retire_state_record(record_id, reason, now_ms=now_ms)
    if not ok:
        return _err([db.last_error or "retire_state_record failed"])

    return {
        "ok": True,
        "record_id": record_id,
        "rejected": True,
        "dry_run": False,
        "status": "retired",
        "warnings": [],
        "errors": [],
    }
