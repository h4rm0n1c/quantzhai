#!/usr/bin/env python3
"""BrainCase retention policy evaluator — #54 Slice B.

Pure policy logic only. This module:
  - parses duration strings
  - loads the default retention policy fixture
  - evaluates a single StateRecord against a policy

This module does NOT:
  - import sqlite3
  - open a database
  - call retire_state_record() or any DB write method
  - mutate records or policy dicts
  - write files
  - expose model-facing tools
  - implement prune or retention-report CLI commands

Retention decisions are purely computed. The "operator hands" (prune CLI,
report generation, actual DB retirement) belong in a future Slice C/D.

No automatic ingestion. No raw DELETE. No model-facing prune.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLICY_SCHEMA = "braincase/retention-policy@1"

# Allowed actions a policy decision may produce.
_ALLOWED_ACTIONS: frozenset[str] = frozenset({"keep", "stale", "retire"})

# Actions that are never permitted — any rule returning these gets forced to keep.
_FORBIDDEN_ACTIONS: frozenset[str] = frozenset({
    "delete", "promote", "activate", "render",
})

_SAFE_FALLBACK_POLICY: dict = {
    "schema": _POLICY_SCHEMA,
    "default_action": "keep",
    "rules": [],
}

_MS_PER_UNIT: dict[str, int] = {
    "s": 1_000,
    "m": 60 * 1_000,
    "h": 3_600 * 1_000,
    "d": 86_400 * 1_000,
}

_DEFAULT_POLICY_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "docs" / "fixtures" / "braincase" / "retention" / "default-policy.json"
)

# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------

def parse_duration_ms(value: Any) -> int | None:
    """Parse a duration string into milliseconds.

    Accepts: None, "60s", "15m", "24h", "7d", "30d", "90d", etc.
    Units: s=seconds, m=minutes, h=hours, d=days.

    Returns the duration in milliseconds, or None for:
      - None input
      - empty or whitespace-only strings
      - unsupported units
      - non-numeric magnitudes
      - negative magnitudes

    Never raises for ordinary invalid input.
    """
    if value is None:
        return None

    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    unit = stripped[-1].lower()
    if unit not in _MS_PER_UNIT:
        return None

    magnitude_str = stripped[:-1].strip()
    try:
        magnitude = int(magnitude_str)
    except (ValueError, TypeError):
        return None

    if magnitude < 0:
        return None

    return magnitude * _MS_PER_UNIT[unit]


# ---------------------------------------------------------------------------
# Policy loader
# ---------------------------------------------------------------------------

def load_default_retention_policy() -> dict:
    """Load the default BrainCase retention policy fixture.

    Returns a fresh dict on each call. Caller mutation does not affect future
    loads. No global mutable policy object is held.

    If the fixture cannot be loaded (missing file, invalid JSON), returns a
    minimal safe fallback policy with default_action=keep and no rules.
    """
    try:
        raw = _DEFAULT_POLICY_FIXTURE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return copy.deepcopy(_SAFE_FALLBACK_POLICY)
        return data
    except Exception:
        return copy.deepcopy(_SAFE_FALLBACK_POLICY)


# ---------------------------------------------------------------------------
# Rule matcher
# ---------------------------------------------------------------------------

def _rule_matches(rule: dict, record: dict) -> bool:
    """Return True if all fields in rule["match"] match the record.

    Match fields supported:
      status, retention, tier, record_type, visibility, memory_domain
        — exact string equality
      min_importance  — record.importance >= value
      max_importance  — record.importance <= value
      source_refs_required — True requires non-empty source_refs list

    memory_domain is exact string match only; never treated as enum/registry.
    Returns False if rule["match"] is absent or not a dict.
    """
    match = rule.get("match")
    if not isinstance(match, dict):
        return False

    for field, expected in match.items():
        if field in ("status", "retention", "tier", "record_type", "visibility", "memory_domain"):
            if record.get(field) != expected:
                return False

        elif field == "min_importance":
            importance = record.get("importance")
            if importance is None:
                return False
            try:
                if float(importance) < float(expected):
                    return False
            except (TypeError, ValueError):
                return False

        elif field == "max_importance":
            importance = record.get("importance")
            if importance is None:
                return False
            try:
                if float(importance) > float(expected):
                    return False
            except (TypeError, ValueError):
                return False

        elif field == "source_refs_required":
            if expected:
                srefs = record.get("source_refs")
                if not isinstance(srefs, list) or not srefs:
                    return False

        # Unknown match fields: ignore (forward-compatible)

    return True


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def retention_decision_for_record(
    record: dict,
    *,
    now_ms: int,
    policy: dict,
) -> dict:
    """Evaluate a single StateRecord against a retention policy.

    Pure function: no DB writes, no side effects, no mutations.
    Deterministic: same inputs always produce the same output.
    Not model-facing.

    Returns:
      {
        "record_id": str | None,
        "action": "keep" | "stale" | "retire",
        "reason": str,
        "age_ms": int | None,
        "age_since_update_ms": int | None,
        "matched_rule": str | None,
        "dry_run": True,
        "warnings": list[str],
      }

    action is always one of: keep, stale, retire.
    delete/promote/activate/render are never returned; malicious policies are
    silently forced to keep with a warning.
    """
    warnings: list[str] = []

    def _result(
        action: str,
        reason: str,
        *,
        age_ms: int | None = None,
        age_since_update_ms: int | None = None,
        matched_rule: str | None = None,
    ) -> dict:
        # Final safety: ensure action is in allowed set
        safe_action = action if action in _ALLOWED_ACTIONS else "keep"
        if safe_action != action:
            warnings.append(f"forbidden_action:{action}")
            safe_action = "keep"
        return {
            "record_id": record.get("record_id") if isinstance(record, dict) else None,
            "action": safe_action,
            "reason": reason,
            "age_ms": age_ms,
            "age_since_update_ms": age_since_update_ms,
            "matched_rule": matched_rule,
            "dry_run": True,
            "warnings": list(warnings),
        }

    # 1. Validate inputs
    if not isinstance(record, dict):
        warnings.append("invalid_record")
        return _result("keep", "invalid_record")

    if not isinstance(policy, dict):
        warnings.append("invalid_policy")
        return _result("keep", "invalid_policy")

    record_id = record.get("record_id")
    status = record.get("status", "")

    # 2. Early-out for already-inactive records
    if status in ("retired", "superseded"):
        return _result("keep", "already_inactive")

    # 3. Compute ages
    created_at_ms = record.get("created_at_ms")
    updated_at_ms = record.get("updated_at_ms")

    age_ms: int | None = None
    age_since_update_ms: int | None = None

    if created_at_ms is not None and isinstance(created_at_ms, int) \
            and not isinstance(created_at_ms, bool):
        computed_age = now_ms - created_at_ms
        if computed_age < 0:
            warnings.append("future_created_at_ms")
        else:
            age_ms = computed_age
    else:
        warnings.append("missing_or_invalid_created_at_ms")

    if updated_at_ms is not None and isinstance(updated_at_ms, int) \
            and not isinstance(updated_at_ms, bool):
        computed_update_age = now_ms - updated_at_ms
        if computed_update_age < 0:
            warnings.append("future_updated_at_ms")
        else:
            age_since_update_ms = computed_update_age
    else:
        warnings.append("missing_or_invalid_updated_at_ms")

    # If we have no usable age, we cannot evaluate retention thresholds
    if age_since_update_ms is None:
        return _result("keep", "missing_or_invalid_timestamp",
                       age_ms=age_ms, age_since_update_ms=None)

    # 4. Hard safety: active + durable always kept (regardless of rules)
    if status == "active" and record.get("retention") == "durable":
        return _result("keep", "durable_keep",
                       age_ms=age_ms, age_since_update_ms=age_since_update_ms,
                       matched_rule="active_durable_keep")

    # 5. Find first matching rule
    rules = policy.get("rules", [])
    if not isinstance(rules, list):
        rules = []

    matched: dict | None = None
    for rule in rules:
        if isinstance(rule, dict) and _rule_matches(rule, record):
            matched = rule
            break

    # 6. No matching rule — use default action
    if matched is None:
        raw_default = policy.get("default_action", "keep")
        if raw_default not in _ALLOWED_ACTIONS:
            warnings.append(f"invalid_default_action:{raw_default}")
            raw_default = "keep"
        return _result(raw_default, "no_matching_rule",
                       age_ms=age_ms, age_since_update_ms=age_since_update_ms)

    matched_rule_name = matched.get("name")
    rule_action = matched.get("action", "keep")
    rule_reason = matched.get("reason") or matched_rule_name or "retention_policy"

    # 7. Parse thresholds
    stale_after_ms = parse_duration_ms(matched.get("stale_after"))
    expire_after_ms = parse_duration_ms(matched.get("expire_after"))

    if matched.get("stale_after") is not None and stale_after_ms is None:
        warnings.append(f"invalid_duration:stale_after:{matched.get('stale_after')}")

    if matched.get("expire_after") is not None and expire_after_ms is None:
        warnings.append(f"invalid_duration:expire_after:{matched.get('expire_after')}")

    # 8. Threshold evaluation
    # Priority: expire_after > stale_after > keep
    if expire_after_ms is not None and age_since_update_ms >= expire_after_ms:
        # Past expiry threshold: use the rule's action
        intended_action = rule_action
        intended_reason = rule_reason
    elif stale_after_ms is not None and age_since_update_ms >= stale_after_ms:
        # Past stale threshold but not expired: report stale
        intended_action = "stale"
        if rule_action == "stale":
            intended_reason = rule_reason
        else:
            intended_reason = f"{matched_rule_name}_stale" if matched_rule_name else "retention_stale"
    else:
        # Within both thresholds: keep
        intended_action = "keep"
        intended_reason = "within_retention_window"

    # 9. Safety overrides
    if intended_action in _FORBIDDEN_ACTIONS:
        warnings.append(f"forbidden_action:{intended_action}")
        intended_action = "keep"
        intended_reason = f"forbidden_action_forced_keep"

    # Candidate cannot be promoted/activated/rendered by retention policy
    if status == "candidate" and intended_action in ("promote", "activate", "render"):
        warnings.append("candidate_cannot_be_promoted_by_retention")
        intended_action = "keep"
        intended_reason = "candidate_cannot_be_promoted_by_retention"

    # Final action must be in allowed set (belt-and-suspenders)
    if intended_action not in _ALLOWED_ACTIONS:
        warnings.append(f"invalid_action_fallback:{intended_action}")
        intended_action = "keep"
        intended_reason = "invalid_action_forced_keep"

    return _result(
        intended_action, intended_reason,
        age_ms=age_ms,
        age_since_update_ms=age_since_update_ms,
        matched_rule=matched_rule_name,
    )
