#!/usr/bin/env python3
"""Explicit write/update path for BrainCase StateRecords.

Slice D: deterministic helper layer for the future braincase.write / braincase.update
tool plane. Not exposed as model-facing tools yet.

The LLM is the memory operator. Helpers accelerate mechanics and preserve
invariants — they are not a policy monarchy or philosophical gatekeeper.

Correct framing:

    Future LLM/tool caller supplies explicit write/update intent
      -> helpers check scope, dedup, conflicts, provenance, redaction/bounds
      -> BrainCaseDB stores or updates the record
      -> result reports warnings/hints (dedup/conflict do not block writes)

Hard rules:
- No automatic ingestion — callers must supply explicit write intent.
- memory_domain is the configured value supplied by the caller; helpers must
  not infer, create, normalize, or grant domain values.
- Forbidden fields are rejected before storage.
- Dedup and conflict results are hints only; they never block writes alone.
- No model-facing rendered output is produced here.
- Does not mutate caller input dicts.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from proxy.qz_braincase_db import _FORBIDDEN_RECORD_FIELDS

if TYPE_CHECKING:
    from proxy.qz_braincase_db import BrainCaseDB

# ---------------------------------------------------------------------------
# Opposing-marker pairs for conflict_check (v1 — crude substring matching).
# Keep simple; do not overfit. These are hints only.
# ---------------------------------------------------------------------------
_OPPOSING_PAIRS = (
    ("must not", "must"),
    ("must", "must not"),
    ("do not", "do "),
    ("allowed", "forbidden"),
    ("forbidden", "allowed"),
    ("enabled", "disabled"),
    ("disabled", "enabled"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def scope_resolve(
    record: dict,
    *,
    allowed_memory_domains: list[str] | None = None,
    current_memory_domain: str | None = None,
) -> dict:
    """Confirm memory_domain is present and within allowed scope.

    Must not infer, create, normalize, or grant memory_domain values.
    If allowed_memory_domains is None, any non-empty domain is accepted.

    Returns {"ok", "memory_domain", "warnings", "errors"}.
    """
    warnings: list[str] = []
    errors: list[str] = []
    memory_domain = record.get("memory_domain")

    if not memory_domain:
        errors.append("memory_domain is required and must not be empty")
        return {
            "ok": False,
            "memory_domain": memory_domain,
            "warnings": warnings,
            "errors": errors,
        }

    if allowed_memory_domains is not None and memory_domain not in allowed_memory_domains:
        errors.append(
            f"memory_domain '{memory_domain}' is not in allowed domains: {allowed_memory_domains}"
        )
        return {
            "ok": False,
            "memory_domain": memory_domain,
            "warnings": warnings,
            "errors": errors,
        }

    if current_memory_domain is not None and memory_domain != current_memory_domain:
        warnings.append(
            f"memory_domain '{memory_domain}' differs from current context '{current_memory_domain}'"
        )

    return {
        "ok": True,
        "memory_domain": memory_domain,
        "warnings": warnings,
        "errors": errors,
    }


def redaction_check(record: dict) -> dict:
    """Reject forbidden raw/ingestion fields before storage.

    Aligned with BrainCaseDB._FORBIDDEN_RECORD_FIELDS.
    Returns {"ok", "forbidden_fields", "errors"}.
    """
    forbidden = sorted(_FORBIDDEN_RECORD_FIELDS & record.keys())
    if forbidden:
        return {
            "ok": False,
            "forbidden_fields": forbidden,
            "errors": [f"Forbidden fields present: {forbidden}"],
        }
    return {"ok": True, "forbidden_fields": [], "errors": []}


def dedup_check(
    db: "BrainCaseDB",
    record: dict,
    *,
    memory_domain: str | None = None,
    tier: str | None = None,
    limit: int = 5,
) -> dict:
    """Find near-duplicate records before write. Returns hints only — does not block.

    v1: exact normalized-claim match within the same memory_domain and tier.
    Returns {"duplicates", "warning"}.
    """
    domain = memory_domain or record.get("memory_domain")
    tier_filter = tier if tier is not None else record.get("tier")
    claim = (record.get("claim") or "").strip().lower()
    record_id = record.get("record_id")

    if not claim or not domain:
        return {"duplicates": [], "warning": None}

    candidates = db.search_state_records(
        claim,
        mode="exact",
        memory_domain=domain,
        tier=tier_filter,
        limit=limit,
    )

    duplicates = []
    for candidate in candidates:
        if candidate.get("record_id") == record_id:
            continue
        candidate_claim = (candidate.get("claim") or "").strip().lower()
        if candidate_claim == claim:
            duplicates.append({
                "record_id": candidate["record_id"],
                "reason": "same normalized claim",
            })

    return {
        "duplicates": duplicates,
        "warning": "possible_duplicate" if duplicates else None,
    }


def conflict_check(
    db: "BrainCaseDB",
    record: dict,
    *,
    memory_domain: str | None = None,
    tier: str | None = None,
    limit: int = 5,
) -> dict:
    """Surface likely contradictions before write. Returns hints only — does not block.

    v1: crude substring matching for opposing constraint markers.
    Checks: same memory_domain, overlapping tags, same record_type, active status.
    Returns {"conflicts", "warning"}.
    """
    domain = memory_domain or record.get("memory_domain")
    claim = (record.get("claim") or "").strip().lower()
    tags = set(record.get("tags") or [])
    record_type = record.get("record_type")
    record_id = record.get("record_id")

    if not domain or not claim or not record_type:
        return {"conflicts": [], "warning": None}

    candidates = db.list_state_records(memory_domain=domain, tier=tier, limit=50)

    conflicts = []
    for candidate in candidates:
        if candidate.get("status") != "active":
            continue
        if candidate.get("record_id") == record_id:
            continue
        if candidate.get("record_type") != record_type:
            continue
        candidate_tags = set(candidate.get("tags") or [])
        if not (tags & candidate_tags):
            continue
        candidate_claim = (candidate.get("claim") or "").strip().lower()
        for marker, opposing in _OPPOSING_PAIRS:
            if marker in claim and opposing in candidate_claim:
                conflicts.append({
                    "record_id": candidate["record_id"],
                    "reason": f"opposing constraint marker: '{marker}' vs '{opposing}'",
                })
                break

    return {
        "conflicts": conflicts,
        "warning": "possible_conflict" if conflicts else None,
    }


def source_link(
    db: "BrainCaseDB",
    record: dict,
    source_refs: list[dict] | None = None,
) -> dict:
    """Store supplied SourceRefs and verify referenced source_ref_ids exist in DB.

    Supplied source_refs are stored via put_source_ref(). If a referenced
    source_ref_id is absent from both the supplied list and the DB, it is
    reported as a warning (not an error) — the write is not blocked by missing refs.

    Returns {"ok", "linked_source_refs", "missing_source_refs", "warnings", "errors"}.
    """
    linked: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []

    record_sref_ids = list(record.get("source_refs") or [])

    # Store supplied SourceRef objects
    supplied_ids: set[str] = set()
    if source_refs:
        for sref in source_refs:
            sref_id = sref.get("source_ref_id")
            if not sref_id:
                warnings.append("SourceRef missing source_ref_id, skipped")
                continue
            if db.put_source_ref(sref):
                supplied_ids.add(sref_id)
                if sref_id not in linked:
                    linked.append(sref_id)
            else:
                errors.append(f"Failed to store source_ref '{sref_id}': {db.last_error}")

    # Verify referenced source_ref_ids exist in DB
    for sref_id in record_sref_ids:
        if sref_id in supplied_ids:
            continue  # Already stored and linked above
        existing = db.get_source_ref(sref_id)
        if existing is not None:
            if sref_id not in linked:
                linked.append(sref_id)
        else:
            if sref_id not in missing:
                missing.append(sref_id)
            warnings.append(f"Referenced source_ref '{sref_id}' not found in DB")

    return {
        "ok": len(errors) == 0,
        "linked_source_refs": linked,
        "missing_source_refs": missing,
        "warnings": warnings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Write / update entry points
# ---------------------------------------------------------------------------

def braincase_write_state_record(
    db: "BrainCaseDB",
    record: dict,
    *,
    source_refs: list[dict] | None = None,
    allowed_memory_domains: list[str] | None = None,
    current_memory_domain: str | None = None,
) -> dict:
    """Explicit internal write path for a StateRecord.

    Runs helper checks then stores the record via BrainCaseDB.

    Dedup and conflict results are hints only — they do not block the write.
    Forbidden fields and invalid scope block the write before any storage.

    Does not mutate the input record or source_refs.
    Does not produce model-facing rendered output.
    Not automatic ingestion — caller must supply explicit write intent.

    Returns:
        {
          "ok": bool,
          "record_id": str | None,
          "stored": bool,
          "errors": list[str],
          "warnings": list[str],
          "dedup": dict | None,
          "conflicts": dict | None,
          "source_link": dict | None,
        }
    """
    rec = dict(record)  # never mutate caller input

    result: dict[str, Any] = {
        "ok": False,
        "record_id": rec.get("record_id"),
        "stored": False,
        "errors": [],
        "warnings": [],
        "dedup": None,
        "conflicts": None,
        "source_link": None,
    }

    # DB availability check
    if not db.enabled:
        result["errors"].append("BrainCaseDB is disabled")
        return result

    # 1. Redaction: forbidden raw fields block
    redaction = redaction_check(rec)
    if not redaction["ok"]:
        result["errors"].extend(redaction["errors"])
        return result

    # 2. Scope: missing/invalid memory_domain blocks
    scope = scope_resolve(
        rec,
        allowed_memory_domains=allowed_memory_domains,
        current_memory_domain=current_memory_domain,
    )
    result["warnings"].extend(scope.get("warnings") or [])
    if not scope["ok"]:
        result["errors"].extend(scope["errors"])
        return result

    # 3. Source link: store supplied SourceRefs; missing refs are warnings only
    slink = source_link(db, rec, source_refs)
    result["source_link"] = slink
    result["warnings"].extend(slink.get("warnings") or [])
    if not slink["ok"]:
        result["errors"].extend(slink["errors"])
        return result

    # 4. Dedup (hint only — does not block)
    dedup = dedup_check(db, rec)
    result["dedup"] = dedup
    if dedup.get("warning"):
        result["warnings"].append(dedup["warning"])

    # 5. Conflict check (hint only — does not block)
    conflicts = conflict_check(db, rec)
    result["conflicts"] = conflicts
    if conflicts.get("warning"):
        result["warnings"].append(conflicts["warning"])

    # 6. Store the record
    if not db.put_state_record(rec):
        result["errors"].append(f"DB write failed: {db.last_error}")
        return result

    result["ok"] = True
    result["stored"] = True
    return result


def braincase_update_state_record(
    db: "BrainCaseDB",
    record_id: str,
    operation: str,
    *,
    patch: dict | None = None,
    new_record: dict | None = None,
    reason: str | None = None,
    now_ms: int | None = None,
    allowed_memory_domains: list[str] | None = None,
    current_memory_domain: str | None = None,
) -> dict:
    """Explicit internal update path for an existing StateRecord.

    Supported operations: retire, supersede.
    Deferred (correct, link) return a structured unsupported result.

    Does not mutate input dicts.
    Not automatic ingestion — caller must supply explicit update intent.

    Returns:
        {
          "ok": bool,
          "record_id": str,
          "operation": str,
          "stored": bool,
          "errors": list[str],
          "warnings": list[str],
          "new_record_id": str | None,  # only for supersede
        }
    """
    result: dict[str, Any] = {
        "ok": False,
        "record_id": record_id,
        "operation": operation,
        "stored": False,
        "errors": [],
        "warnings": [],
    }

    if not db.enabled:
        result["errors"].append("BrainCaseDB is disabled")
        return result

    if operation == "retire":
        retire_reason = reason or "retired via braincase_update_state_record"
        if db.retire_state_record(record_id, retire_reason, now_ms=now_ms):
            result["ok"] = True
            result["stored"] = True
        else:
            result["errors"].append(db.last_error or "retire_state_record failed")
        return result

    elif operation == "supersede":
        if not new_record:
            result["errors"].append("supersede operation requires new_record")
            return result

        new_rec = dict(new_record)  # never mutate caller input

        redaction = redaction_check(new_rec)
        if not redaction["ok"]:
            result["errors"].extend(redaction["errors"])
            return result

        scope = scope_resolve(
            new_rec,
            allowed_memory_domains=allowed_memory_domains,
            current_memory_domain=current_memory_domain,
        )
        result["warnings"].extend(scope.get("warnings") or [])
        if not scope["ok"]:
            result["errors"].extend(scope["errors"])
            return result

        supersede_reason = reason or "superseded via braincase_update_state_record"
        if db.supersede_state_record(record_id, new_rec, supersede_reason, now_ms=now_ms):
            result["ok"] = True
            result["stored"] = True
            result["new_record_id"] = new_rec.get("record_id")
        else:
            result["errors"].append(db.last_error or "supersede_state_record failed")
        return result

    elif operation in ("correct", "link"):
        result["errors"].append(
            f"Operation '{operation}' is not yet implemented in Slice D"
        )
        return result

    else:
        result["errors"].append(f"Unknown update operation: '{operation}'")
        return result
