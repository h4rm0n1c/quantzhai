#!/usr/bin/env python3
"""BrainCase memory manager — mechanical pipeline (steps 1-3 of the orchestrator).

This module provides:

  compact_for_context(input_items, llm_base_url, llm_model)
    Run the survival-weighted compactor over a conversation input array.
    Returns the summary string only — no side effects on session context.
    Used to give the memory manager a filtered view of the current session
    without requiring a full threshold-triggered compaction event.

  dispatch_memory_tool_calls(tool_calls, db)
    Parse and execute bc_* tool calls returned by the memory manager LLM.
    Routes to the bc_promote/retire/merge/update_tier/tag functions.
    Returns a list of result dicts.

  run_memory_manager(input_items, db, llm_base_url, llm_model, *, prompt_override)
    Orchestrator shell: compact_for_context → compute_landscape → build prompt →
    LLM call → dispatch tool calls → return action summary.
    The prompt_override parameter is the step-4 placeholder — pass the final
    prompt once it exists; the shell works with any prompt injected here.

All functions are best-effort and never raise. Failures are returned as
structured error dicts so callers can log and continue.
"""
from __future__ import annotations

import json
import time
from typing import Any

# ---------------------------------------------------------------------------
# compact_for_context
# ---------------------------------------------------------------------------

def compact_for_context(
    input_items: list[dict],
    llm_base_url: str = "",
    llm_model: str = "",
    max_input_chars: int | None = None,
    timeout_sec: int | None = None,
    max_output_tokens: int | None = None,
) -> tuple[str | None, str]:
    """Run the survival-weighted compactor over input_items and return the summary.

    This is NOT a full compaction event — no new context is written, no threshold
    is checked, no session state is modified.  The output is a survival-weighted
    anchored summary of whatever is in input_items.

    Used to give the memory manager a clean, Frieza-stripped view of the current
    session before it decides what to promote, retire, or merge.

    Returns (summary_text, reason):
      summary_text  — the anchored summary string, or None on failure
      reason        — "ok" on success, error code on failure
    """
    try:
        try:
            from .qz_responses import (
                _build_survival_weighted_compaction_prompt,
                _call_llm_compactor,
                _active_backend_base_url,
            )
        except ImportError:
            from qz_responses import (
                _build_survival_weighted_compaction_prompt,
                _call_llm_compactor,
                _active_backend_base_url,
            )

        if not input_items:
            return None, "empty_input"

        # Resolve LLM backend URL — same priority as normal compaction.
        effective_url = (llm_base_url or "").rstrip("/") or _active_backend_base_url()
        if not effective_url:
            return None, "no_backend"

        prompt = _build_survival_weighted_compaction_prompt(
            previous_summary="",   # no prior summary — fresh pass over current session
            new_items=input_items,
            max_input_chars=max_input_chars,
        )

        summary, reason = _call_llm_compactor(
            prompt,
            timeout_sec=timeout_sec,
            max_output_tokens=max_output_tokens,
            llm_base_url=effective_url,
            llm_model=llm_model or "",
        )
        return summary, reason

    except Exception as exc:
        return None, f"exception:{type(exc).__name__}:{exc}"


# ---------------------------------------------------------------------------
# dispatch_memory_tool_calls
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, str] = {
    "bc_promote":     "bc_promote_tool",
    "bc_retire":      "bc_retire_tool",
    "bc_update_tier": "bc_update_tier_tool",
    "bc_tag":         "bc_tag_tool",
    "bc_merge":       "bc_merge_tool",
}


def dispatch_memory_tool_calls(
    tool_calls: list[dict],
    db: Any,
) -> list[dict]:
    """Execute a list of bc_* tool calls against the DB.

    tool_calls: list of {"name": "bc_promote", "arguments": {...}} dicts,
                as returned by the LLM in a /v1/chat/completions response.

    Returns a list of result dicts, one per tool call, including "name",
    "ok", and any fields returned by the individual tool function.
    """
    try:
        try:
            from .qz_braincase_tools import (
                bc_promote_tool,
                bc_retire_tool,
                bc_update_tier_tool,
                bc_tag_tool,
                bc_merge_tool,
            )
        except ImportError:
            from qz_braincase_tools import (
                bc_promote_tool,
                bc_retire_tool,
                bc_update_tier_tool,
                bc_tag_tool,
                bc_merge_tool,
            )
    except Exception as exc:
        return [{"ok": False, "error": f"import failed: {exc}"}]

    fn_map = {
        "bc_promote":     bc_promote_tool,
        "bc_retire":      bc_retire_tool,
        "bc_update_tier": bc_update_tier_tool,
        "bc_tag":         bc_tag_tool,
        "bc_merge":       bc_merge_tool,
    }

    results = []
    for call in (tool_calls or []):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or "").strip()
        raw_args = call.get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except Exception:
                raw_args = {}
        if not isinstance(raw_args, dict):
            raw_args = {}

        fn = fn_map.get(name)
        if fn is None:
            results.append({"name": name, "ok": False, "error": f"unknown tool: {name!r}"})
            continue

        try:
            result = fn(db, raw_args)
            result["name"] = name
            results.append(result)
        except Exception as exc:
            results.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return results


# ---------------------------------------------------------------------------
# Landscape serialisation (text format for prompt injection)
# ---------------------------------------------------------------------------

def _format_landscape_for_prompt(metrics: list[dict]) -> str:
    """Serialise compute_landscape_metrics() output as compact prompt text.

    Each record becomes one line:
      [id] tier=X ret=Y age=Zd acc=N class=CLASS | claim_snippet
    Sorted weakest-first (as compute_landscape_metrics returns).
    """
    lines = []
    for m in metrics:
        rid = m.get("record_id", "?")[:16]
        tier = m.get("tier", "?")[:12]
        ret = m.get("retention", "?")[:8]
        age = f"{m.get('age_days', '?')}d"
        acc = m.get("access_count", 0)
        cls = m.get("temporal_class", "neutral")
        surv = m.get("survival", {})
        l1 = surv.get("l1", 0)
        l2 = surv.get("l2", 0)
        ret_action = m.get("retention_action", "keep")
        claim = m.get("claim_snippet", "")[:80]
        lines.append(
            f"[{rid}] tier={tier} ret={ret} age={age} acc={acc} "
            f"L1={l1} L2={l2} policy={ret_action} class={cls} | {claim}"
        )
    return "\n".join(lines) if lines else "(no records)"


# ---------------------------------------------------------------------------
# Orchestrator shell
# ---------------------------------------------------------------------------

_PROMPT_PLACEHOLDER = """\
[MEMORY MANAGER PROMPT — NOT YET FINALISED]

You are a memory arbitration agent. You do NOT respond to the user.
You make structured tool calls to manage a persistent memory store.

Session context (survival-weighted summary of current session):
{session_summary}

Memory landscape (sorted weakest-first):
{landscape}

Available tools: bc_promote, bc_retire, bc_update_tier, bc_tag, bc_merge.

Make tool calls based on temporal survival scoring:
- Temporal Saiyan (class=saiyan): leave alone or promote
- Temporal Frieza (class=frieza): retire unless survival score is high
- Neutral with L2 signals: review — promote if claim is a constraint or lesson
- Redundant records with similar claims: merge

Use only tool calls. No prose.
"""


def run_memory_manager(
    input_items: list[dict],
    db: Any,
    *,
    llm_base_url: str = "",
    llm_model: str = "",
    memory_domain: str | None = None,
    prompt_override: str | None = None,
    now_ms: int | None = None,
) -> dict:
    """Orchestrator: compact → metrics → prompt → LLM call → dispatch → summary.

    prompt_override: inject the real step-4 prompt when ready.
                     Falls back to _PROMPT_PLACEHOLDER in the meantime.

    Returns a summary dict:
      ok, session_summary, record_count, tool_calls_made, actions, errors, elapsed_ms
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    t0 = time.time()

    result: dict = {
        "ok": False,
        "session_summary": None,
        "record_count": 0,
        "tool_calls_made": 0,
        "actions": [],
        "errors": [],
        "elapsed_ms": 0,
    }

    # --- 1. compact_for_context ---
    summary, compact_reason = compact_for_context(
        input_items,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
    )
    if not summary:
        result["errors"].append(f"compact_for_context failed: {compact_reason}")
        # Non-fatal: proceed with empty summary so the manager still sees the landscape
        summary = "(session summary unavailable)"
    result["session_summary"] = summary

    # --- 2. Build memory landscape ---
    try:
        try:
            from .qz_braincase_metrics import compute_landscape_metrics
        except ImportError:
            from qz_braincase_metrics import compute_landscape_metrics

        domain_filter = memory_domain
        records: list[dict] = []
        if db is not None and db.available:
            try:
                records = db.list_state_records(
                    memory_domain=domain_filter,
                    limit=50,
                ) or []
                # Also include candidates
                candidates = db.list_state_records_by_status(
                    status="candidate",
                    memory_domain=domain_filter,
                    limit=20,
                ) or []
                seen = {r.get("record_id") for r in records}
                for c in candidates:
                    if c.get("record_id") not in seen:
                        records.append(c)
            except Exception as exc:
                result["errors"].append(f"db.list_state_records failed: {exc}")

        metrics = compute_landscape_metrics(records, now_ms)
        result["record_count"] = len(metrics)
        landscape_text = _format_landscape_for_prompt(metrics)

    except Exception as exc:
        result["errors"].append(f"landscape build failed: {exc}")
        landscape_text = "(landscape unavailable)"

    # --- 3. Build prompt ---
    prompt_template = prompt_override or _PROMPT_PLACEHOLDER
    try:
        prompt = prompt_template.format(
            session_summary=summary,
            landscape=landscape_text,
        )
    except Exception as exc:
        result["errors"].append(f"prompt format failed: {exc}")
        prompt = f"{prompt_template}\n\nSESSION:\n{summary}\n\nLANDSCAPE:\n{landscape_text}"

    # --- 4. LLM call ---
    try:
        try:
            from .qz_responses import _call_llm_compactor, _active_backend_base_url
        except ImportError:
            from qz_responses import _call_llm_compactor, _active_backend_base_url

        effective_url = (llm_base_url or "").rstrip("/") or _active_backend_base_url()
        if not effective_url:
            result["errors"].append("no_backend: LLM URL not available")
            result["elapsed_ms"] = round((time.time() - t0) * 1000)
            return result

        llm_response, llm_reason = _call_llm_compactor(
            prompt,
            llm_base_url=effective_url,
            llm_model=llm_model or "",
        )
    except Exception as exc:
        result["errors"].append(f"LLM call failed: {exc}")
        result["elapsed_ms"] = round((time.time() - t0) * 1000)
        return result

    if not llm_response:
        result["errors"].append(f"LLM returned nothing: {llm_reason}")
        result["elapsed_ms"] = round((time.time() - t0) * 1000)
        return result

    # --- 5. Parse tool calls from LLM response ---
    # The LLM should return JSON tool calls. Extract them from the response text.
    tool_calls = _extract_tool_calls(llm_response)
    result["tool_calls_made"] = len(tool_calls)

    # --- 6. Dispatch tool calls ---
    if tool_calls and db is not None:
        actions = dispatch_memory_tool_calls(tool_calls, db)
        result["actions"] = actions
        errors = [a for a in actions if not a.get("ok")]
        for e in errors:
            result["errors"].append(f"tool {e.get('name')}: {e.get('error', 'failed')}")

    result["ok"] = True
    result["elapsed_ms"] = round((time.time() - t0) * 1000)
    return result


def _extract_tool_calls(llm_response: str) -> list[dict]:
    """Extract bc_* tool calls from the LLM response text.

    The LLM is expected to return JSON tool calls, either as a JSON array
    or as newline-separated JSON objects. Best-effort parsing.
    """
    calls = []
    text = (llm_response or "").strip()

    # Try parsing the whole response as a JSON array
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [c for c in parsed if isinstance(c, dict) and c.get("name")]
        if isinstance(parsed, dict) and parsed.get("name"):
            return [parsed]
    except Exception:
        pass

    # Try line-by-line JSON objects
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("name"):
                calls.append(obj)
        except Exception:
            pass

    return calls
