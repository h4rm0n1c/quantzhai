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
    from .qz_braincase_retention import retention_decision_for_record
except ImportError:
    from qz_braincase_write import (
        conflict_check,
        dedup_check,
        redaction_check,
    )
    from qz_braincase_retention import retention_decision_for_record

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

    # Use status-filtered retrieval so candidate records cannot be hidden behind
    # newer active/retired records that would fill the limit before candidates.
    candidates = db.list_state_records_by_status(
        status="candidate",
        memory_domain=memory_domain,
        limit=limit,
    )

    return [
        {
            "record_id": r.get("record_id"),
            "status": r.get("status"),
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


# ---------------------------------------------------------------------------
# Retention report (Slice C) — dry-run only, no DB writes
# ---------------------------------------------------------------------------

def retention_report_records(
    db: "BrainCaseDB",
    *,
    now_ms: int,
    policy: dict,
    memory_domain: str | None = None,
    status: str | None = None,
    retention: str | None = None,
    action: str | None = None,
    limit: int = 100,
) -> dict:
    """Evaluate stored StateRecords against the retention policy and return a report.

    Pure reporting: no DB writes, no record mutation, no retire_state_record() calls.
    dry_run is always True.

    Filters applied:
      memory_domain — exact match at retrieval
      status        — exact match at retrieval (uses list_state_records_by_status)
      retention     — exact match post-retrieval
      action        — filter decisions by keep/stale/retire

    counts cover all retention-filtered records (before action filter).
    decisions contains only action-filtered records, up to limit.
    """
    if not db.enabled:
        return {
            "ok": False,
            "dry_run": True,
            "error": "braincase_db_disabled",
            "records_seen": 0,
            "records_returned": 0,
            "counts": {"keep": 0, "stale": 0, "retire": 0},
            "decisions": [],
            "warnings": [],
        }

    # Over-fetch for post-evaluation filtering; 4× provides useful headroom
    fetch_limit = max(limit * 4, 200)
    if status:
        records = db.list_state_records_by_status(
            status=status,
            memory_domain=memory_domain,
            limit=fetch_limit,
        )
    else:
        records = db.list_state_records(
            memory_domain=memory_domain,
            limit=fetch_limit,
        )

    records_seen = len(records)
    counts: dict = {"keep": 0, "stale": 0, "retire": 0}
    decisions: list = []
    report_warnings: list = []

    for rec in records:
        # Apply retention class filter before evaluation
        if retention is not None and rec.get("retention") != retention:
            continue

        decision = retention_decision_for_record(rec, now_ms=now_ms, policy=policy)
        action_val = decision["action"]
        counts[action_val] = counts.get(action_val, 0) + 1

        # Apply action filter
        if action is not None and action_val != action:
            continue

        if len(decisions) >= limit:
            continue  # Count but don't add to decisions

        # Bounded decision entry — no raw StateRecord dump
        decisions.append({
            "record_id": decision.get("record_id"),
            "action": action_val,
            "reason": decision.get("reason"),
            "matched_rule": decision.get("matched_rule"),
            "age_ms": decision.get("age_ms"),
            "age_since_update_ms": decision.get("age_since_update_ms"),
            "memory_domain": rec.get("memory_domain"),
            "tier": rec.get("tier"),
            "record_type": rec.get("record_type"),
            "status": rec.get("status"),
            "retention": rec.get("retention"),
            "visibility": rec.get("visibility"),
            "summary": (rec.get("summary") or "")[:100],
            "warnings": decision.get("warnings") or [],
        })

    return {
        "ok": True,
        "dry_run": True,
        "records_seen": records_seen,
        "records_returned": len(decisions),
        "counts": counts,
        "decisions": decisions,
        "warnings": report_warnings,
    }


# ---------------------------------------------------------------------------
# Retention apply prune (Slice D) — explicit operator action, uses retire_state_record()
# ---------------------------------------------------------------------------

def apply_retention_prune(
    db: "BrainCaseDB",
    *,
    now_ms: int,
    policy: dict,
    memory_domain: str | None = None,
    status: str | None = None,
    retention: str | None = None,
    limit: int = 100,
    reason: str = "operator retention prune",
    dry_run: bool = False,
) -> dict:
    """Evaluate retention policy and, when dry_run=False, retire eligible records.

    When dry_run=True:
      Delegates to retention_report_records(action="retire").
      No DB writes.

    When dry_run=False:
      1. Evaluates records using retention_report_records(action="retire").
      2. For each candidate retire decision, re-evaluates the record immediately.
      3. Only calls retire_state_record() when the re-evaluation still returns
         action="retire".
      4. Skips: changed decision, already-inactive, active+durable, missing.

    Safety rules (all enforced here, beyond evaluator):
      - Never retires active+durable records (hard override).
      - Never retires stale or keep records.
      - Re-evaluates each record before writing (prevents stale decisions).
      - All retirements use retire_state_record() — no raw DELETE, no SQL UPDATE.
      - No visibility change.
      - No promotion.
      - No automatic ingestion.

    Returns a bounded result dict. No raw StateRecord dumps.
    """
    if dry_run:
        return retention_report_records(
            db,
            now_ms=now_ms,
            policy=policy,
            memory_domain=memory_domain,
            status=status,
            retention=retention,
            action="retire",
            limit=limit,
        )

    if not db.enabled:
        return {
            "ok": False,
            "dry_run": False,
            "error": "braincase_db_disabled",
            "records_seen": 0,
            "records_returned": 0,
            "retired_count": 0,
            "skipped_count": 0,
            "retired": [],
            "skipped": [],
            "warnings": [],
            "errors": ["braincase_db_disabled"],
        }

    # Phase 1: get candidate retire decisions (dry-run report)
    report = retention_report_records(
        db,
        now_ms=now_ms,
        policy=policy,
        memory_domain=memory_domain,
        status=status,
        retention=retention,
        action="retire",
        limit=limit,
    )

    if not report.get("ok"):
        return {
            "ok": False,
            "dry_run": False,
            "error": report.get("error", "report_failed"),
            "records_seen": 0,
            "records_returned": 0,
            "retired_count": 0,
            "skipped_count": 0,
            "retired": [],
            "skipped": [],
            "warnings": [],
            "errors": [report.get("error", "report_failed")],
        }

    retired: list = []
    skipped: list = []
    errors: list = []
    warnings: list = list(report.get("warnings") or [])

    # Phase 2: re-evaluate and apply
    for decision in report.get("decisions", []):
        record_id = decision.get("record_id")
        if not record_id:
            skipped.append({"record_id": None, "reason": "missing_record_id"})
            continue

        # Fetch current record for re-evaluation
        current_rec = db.get_state_record(record_id)
        if current_rec is None:
            skipped.append({"record_id": record_id, "reason": "missing_record"})
            continue

        # Hard safety: never retire active+durable (belt-and-suspenders)
        if current_rec.get("status") == "active" and current_rec.get("retention") == "durable":
            skipped.append({"record_id": record_id, "reason": "active_durable_protected"})
            continue

        # Hard safety: never retire already-inactive records
        if current_rec.get("status") in ("retired", "superseded"):
            skipped.append({"record_id": record_id, "reason": "already_inactive"})
            continue

        # Re-evaluate immediately before applying (prevents stale decisions)
        re_decision = retention_decision_for_record(current_rec, now_ms=now_ms, policy=policy)
        if re_decision.get("action") != "retire":
            skipped.append({
                "record_id": record_id,
                "reason": f"decision_changed:{re_decision.get('action')}",
            })
            continue

        # Build retirement reason string
        retire_reason = (
            f"{reason} | rule:{re_decision.get('matched_rule') or 'none'} "
            f"| policy_reason:{re_decision.get('reason') or 'none'}"
        )

        ok = db.retire_state_record(record_id, retire_reason, now_ms=now_ms)
        if ok:
            retired.append({
                "record_id": record_id,
                "previous_status": current_rec.get("status"),
                "new_status": "retired",
                "reason": retire_reason,
                "matched_rule": re_decision.get("matched_rule"),
                "retention": current_rec.get("retention"),
                "status": current_rec.get("status"),
                "memory_domain": current_rec.get("memory_domain"),
            })
        else:
            err = db.last_error or "retire_state_record failed"
            errors.append(f"{record_id}: {err}")
            skipped.append({"record_id": record_id, "reason": f"db_error:{err}"})

    return {
        "ok": len(errors) == 0,
        "dry_run": False,
        "records_seen": report.get("records_seen", 0),
        "records_returned": report.get("records_returned", 0),
        "retired_count": len(retired),
        "skipped_count": len(skipped),
        "retired": retired,
        "skipped": skipped,
        "warnings": warnings,
        "errors": errors,
    }
