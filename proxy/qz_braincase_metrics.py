#!/usr/bin/env python3
"""BrainCase record quality metrics for the memory management system.

compute_record_metrics(record, now_ms) returns a pre-computed quality payload
that the memory management LLM receives instead of raw records.  The same
principle as compaction: run the scorer before calling the LLM, pass scored
hints as context so the LLM makes semantic decisions, not mechanical ones.

Metric vocabulary:
  survival_score   L1 atom count × 2 + L2 semantic hit count × 1
  temporal_value   survival_score × recency_factor × access_factor
  recency_factor   1.0 (≤7d) / 0.5 (≤30d) / 0.2 (≤90d) / 0.0 (>90d or never)
  access_factor    log(1 + access_count) / log(10)  — diminishing returns
  retention_action output of existing retention evaluator (keep/stale/retire)
  temporal_class   saiyan | neutral | frieza  (derived from temporal_value)
"""
from __future__ import annotations

import math
import time
from typing import Any


# ---------------------------------------------------------------------------
# Survival scoring (re-uses the existing scorer)
# ---------------------------------------------------------------------------

def _survival_score(claim: str, summary: str) -> dict:
    """Run score_text over claim+summary. Returns {l1, l2, l3, atoms}."""
    try:
        try:
            from .qz_survival_weight import score_text
        except ImportError:
            from qz_survival_weight import score_text
        text = f"{claim}\n{summary}".strip()
        spans = score_text(text)
        l1 = sum(1 for s in spans if s.level == 1)
        l2 = sum(1 for s in spans if s.level == 2)
        l3 = sum(1 for s in spans if s.level == 3)
        atoms = [s.text for s in spans if s.level == 1][:8]
        return {"l1": l1, "l2": l2, "l3": l3, "atoms": atoms,
                "score": l1 * 2 + l2 * 1}
    except Exception:
        return {"l1": 0, "l2": 0, "l3": 0, "atoms": [], "score": 0}


# ---------------------------------------------------------------------------
# Temporal factors
# ---------------------------------------------------------------------------

_DAY_MS = 86_400_000

def _recency_factor(last_accessed_at_ms: int | None, now_ms: int) -> float:
    if last_accessed_at_ms is None:
        return 0.0
    age_days = (now_ms - last_accessed_at_ms) / _DAY_MS
    if age_days <= 7:
        return 1.0
    if age_days <= 30:
        return 0.5
    if age_days <= 90:
        return 0.2
    return 0.0


def _access_factor(access_count: int) -> float:
    return math.log(1 + max(0, access_count)) / math.log(10)


def _temporal_class(temporal_value: float, retention_action: str, survival: dict) -> str:
    """Classify as saiyan / neutral / frieza.

    Temporal Saiyan: high temporal value — proven useful across sessions.
    Neutral: moderate value or unaccessed-but-quality — LLM reviews.
    Temporal Frieza: low value + stale policy + low content quality.

    Records with L2 semantic hits (constraints, causal) are never auto-Frieza
    even if never accessed — they may be valuable candidates not yet surfaced.
    """
    if temporal_value >= 2.0:
        return "saiyan"
    # Unaccessed but has L2 semantic quality → neutral, let LLM decide
    if temporal_value == 0.0 and survival.get("l2", 0) >= 1:
        return "neutral"
    # Low value + policy says retire → Frieza
    if retention_action == "retire" and temporal_value < 0.5:
        return "frieza"
    return "neutral"


# ---------------------------------------------------------------------------
# Retention decision (re-uses existing evaluator)
# ---------------------------------------------------------------------------

def _retention_action(record: dict, now_ms: int) -> str:
    try:
        try:
            from .qz_braincase_retention import (
                retention_decision_for_record,
                load_default_retention_policy,
            )
        except ImportError:
            from qz_braincase_retention import (
                retention_decision_for_record,
                load_default_retention_policy,
            )
        policy = load_default_retention_policy()
        decision = retention_decision_for_record(record, now_ms=now_ms, policy=policy)
        return str(decision.get("action") or "keep")
    except Exception:
        return "keep"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_record_metrics(record: dict, now_ms: int | None = None) -> dict:
    """Return a quality payload for a single StateRecord.

    The payload is what the memory management LLM receives — pre-computed
    scores so the model makes semantic judgements, not mechanical ones.

    Keys:
      record_id, tier, retention, status, claim_snippet
      age_days, last_accessed_days_ago, access_count
      survival (l1, l2, l3, atoms, score)
      recency_factor, access_factor, temporal_value
      retention_action (keep | stale | retire)
      temporal_class (saiyan | neutral | frieza)
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    record_id = record.get("record_id", "")
    claim = str(record.get("claim") or "")
    summary = str(record.get("summary") or "")
    tier = str(record.get("tier") or "")
    retention = str(record.get("retention") or "")
    status = str(record.get("status") or "")
    created_at_ms = record.get("created_at_ms") or 0
    last_accessed_at_ms = record.get("last_accessed_at_ms")
    access_count = int(record.get("access_count") or 0)

    age_days = round((now_ms - created_at_ms) / _DAY_MS, 1) if created_at_ms else None
    last_accessed_days = (
        round((now_ms - last_accessed_at_ms) / _DAY_MS, 1)
        if last_accessed_at_ms else None
    )

    survival = _survival_score(claim, summary)
    recency = _recency_factor(last_accessed_at_ms, now_ms)
    access = _access_factor(access_count)
    temporal_value = round(survival["score"] * recency * access, 3)
    ret_action = _retention_action(record, now_ms)
    t_class = _temporal_class(temporal_value, ret_action, survival)

    return {
        "record_id": record_id,
        "tier": tier,
        "retention": retention,
        "status": status,
        "claim_snippet": claim[:120],
        "age_days": age_days,
        "last_accessed_days_ago": last_accessed_days,
        "access_count": access_count,
        "survival": survival,
        "recency_factor": recency,
        "access_factor": round(access, 3),
        "temporal_value": temporal_value,
        "retention_action": ret_action,
        "temporal_class": t_class,
    }


def compute_landscape_metrics(records: list[dict], now_ms: int | None = None) -> list[dict]:
    """Compute metrics for a list of records, sorted weakest-first.

    Weakest-first ordering means the LLM sees the most at-risk records
    before the ones it should leave alone.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    metrics = [compute_record_metrics(r, now_ms) for r in records]
    return sorted(metrics, key=lambda m: (m["temporal_value"], -m["age_days"] if m["age_days"] else 0))
