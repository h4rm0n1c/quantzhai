#!/usr/bin/env python3
"""BrainCase memory manager -- mechanical pipeline + management pressure.

Additional entry points:

  compute_management_pressure(db, now_ms)
    Returns a pressure dict: pending_candidates, stale_records, days_since_run,
    pressure_score, should_run.  Pure computation -- no DB writes.

  record_manager_run(root)
    Write var/state/manager-last-run.json after a successful run.

  maybe_run_manager_async(input_items, db, *, root, llm_base_url, llm_model)
    Check pressure and fire run_memory_manager() in a background thread
    if pressure threshold is exceeded.  Returns immediately; never blocks.
    Safe to call from the compaction hot path.

Original entry points:

  compact_for_context(input_items, llm_base_url, llm_model)
  dispatch_memory_tool_calls(tool_calls, db)
  run_memory_manager(input_items, db, ...)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_DAY_S = 86_400.0

# ---------------------------------------------------------------------------
# Management pressure
# ---------------------------------------------------------------------------

# Pressure weights -- tunable without code changes.
_CANDIDATE_WEIGHT     = 2   # each unreviewed candidate adds this to pressure
_STALE_WEIGHT         = 1   # each policy-stale/retire record adds this
_DAYS_SINCE_RUN_WEIGHT = 1  # each idle day adds this (capped at 14d)
_DEFAULT_THRESHOLD    = 10  # fire manager run when score >= this


def _last_run_path(root: str | Path | None = None) -> Path:
    r = Path(root or os.environ.get("QZ_ROOT", Path(__file__).resolve().parents[1]))
    return r / "var" / "state" / "manager-last-run.json"


def _load_last_run(root: str | Path | None = None) -> dict:
    try:
        return json.loads(_last_run_path(root).read_text())
    except Exception:
        return {}


def record_manager_run(root: str | Path | None = None, result: dict | None = None) -> None:
    """Write var/state/manager-last-run.json after a successful run. Best-effort."""
    try:
        path = _last_run_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ran_at_ms": int(time.time() * 1000),
            "ran_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "actions": len((result or {}).get("actions", [])) if result else 0,
            "errors": len((result or {}).get("errors", [])) if result else 0,
        }
        path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def compute_management_pressure(
    db: Any,
    now_ms: int | None = None,
    root: str | Path | None = None,
    threshold: int = _DEFAULT_THRESHOLD,
) -> dict:
    """Compute accumulated management debt. Pure computation -- no DB writes.

    Returns:
      pending_candidates  -- candidate records awaiting assessment
      stale_records       -- records retention policy wants to retire/mark stale
      days_since_run      -- days since last manager run (None if never run)
      pressure_score      -- weighted sum
      should_run          -- True when pressure_score >= threshold
      threshold           -- the threshold used
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    pending_candidates = 0
    stale_records = 0

    if db is not None and getattr(db, "available", False):
        try:
            candidates = db.list_state_records_by_status(status="candidate", limit=200) or []
            pending_candidates = len(candidates)
        except Exception:
            pass

        try:
            from .qz_braincase_metrics import compute_record_metrics
        except ImportError:
            try:
                from qz_braincase_metrics import compute_record_metrics
            except ImportError:
                compute_record_metrics = None  # type: ignore[assignment]

        if compute_record_metrics is not None:
            try:
                active = db.list_state_records(limit=200) or []
                for r in active:
                    if not isinstance(r, dict):
                        continue
                    m = compute_record_metrics(r, now_ms)
                    if m.get("retention_action") in ("stale", "retire"):
                        stale_records += 1
            except Exception:
                pass

    # Time since last run
    last_run = _load_last_run(root)
    days_since_run: float | None = None
    idle_days_score = 0
    if last_run.get("ran_at_ms"):
        days_since_run = (now_ms - last_run["ran_at_ms"]) / (_DAY_S * 1000)
        idle_days_score = min(14, int(days_since_run)) * _DAYS_SINCE_RUN_WEIGHT
    else:
        idle_days_score = 14 * _DAYS_SINCE_RUN_WEIGHT  # never run → max idle pressure

    pressure_score = (
        pending_candidates * _CANDIDATE_WEIGHT
        + stale_records * _STALE_WEIGHT
        + idle_days_score
    )

    return {
        "pending_candidates": pending_candidates,
        "stale_records": stale_records,
        "days_since_run": round(days_since_run, 1) if days_since_run is not None else None,
        "idle_days_score": idle_days_score,
        "pressure_score": pressure_score,
        "should_run": pressure_score >= threshold,
        "threshold": threshold,
    }


def maybe_run_manager_async(
    input_items: list[dict],
    db: Any,
    *,
    root: str | Path | None = None,
    llm_base_url: str = "",
    llm_model: str = "",
    memory_domain: str | None = None,
    threshold: int = _DEFAULT_THRESHOLD,
    prompt_override: str | None = None,
) -> dict:
    """Check management pressure and fire the manager in a background thread if needed.

    Returns the pressure dict immediately. Never blocks the caller.
    Safe to call from the compaction hot path -- fire-and-forget.

    The manager run:
      1. compact_for_context(input_items) → session summary
      2. compute_landscape_metrics(db) → scored records
      3. LLM call with placeholder prompt (or prompt_override)
      4. dispatch bc_* tool calls
      5. record_manager_run() -- resets idle pressure

    Backend occupancy: the manager LLM call uses the same inference backend
    as Codex.  While it runs, any incoming Codex /v1/responses request will
    wait in the backend queue (hold-open on the proxy side).  This is the
    intended behaviour -- the user sees a brief pause on their next turn.
    Manager calls are fired post-compaction at a natural session break where
    the user is reading the compacted context before composing a reply, so
    the timing typically works out.

    If db is unavailable or pressure is below threshold, returns immediately
    with should_run=False and no background thread is started.
    """
    import threading as _thr

    pressure = compute_management_pressure(db, root=root, threshold=threshold)

    if not pressure["should_run"]:
        pressure["fired"] = False
        return pressure

    if db is None or not getattr(db, "available", False):
        pressure["fired"] = False
        pressure["skip_reason"] = "db_unavailable"
        return pressure

    def _background():
        try:
            result = run_memory_manager(
                input_items,
                db,
                llm_base_url=llm_base_url,
                llm_model=llm_model,
                memory_domain=memory_domain,
                prompt_override=prompt_override,
            )
            if result.get("ok"):
                record_manager_run(root, result)
        except Exception:
            pass

    _thr.Thread(target=_background, daemon=True, name="qz-memory-manager").start()
    pressure["fired"] = True
    return pressure


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

    This is NOT a full compaction event -- no new context is written, no threshold
    is checked, no session state is modified.  The output is a survival-weighted
    anchored summary of whatever is in input_items.

    Used to give the memory manager a clean, Frieza-stripped view of the current
    session before it decides what to promote, retire, or merge.

    Returns (summary_text, reason):
      summary_text  -- the anchored summary string, or None on failure
      reason        -- "ok" on success, error code on failure
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

        # Resolve LLM backend URL -- same priority as normal compaction.
        effective_url = (llm_base_url or "").rstrip("/") or _active_backend_base_url()
        if not effective_url:
            return None, "no_backend"

        prompt = _build_survival_weighted_compaction_prompt(
            previous_summary="",   # no prior summary -- fresh pass over current session
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
                bc_read_tool,
                bc_search_tool,
                bc_challenge_tool,
            )
        except ImportError:
            from qz_braincase_tools import (
                bc_promote_tool,
                bc_retire_tool,
                bc_update_tier_tool,
                bc_tag_tool,
                bc_merge_tool,
                bc_read_tool,
                bc_search_tool,
                bc_challenge_tool,
            )
    except Exception as exc:
        return [{"ok": False, "error": f"import failed: {exc}"}]

    fn_map = {
        "bc_promote":     bc_promote_tool,
        "bc_retire":      bc_retire_tool,
        "bc_update_tier": bc_update_tier_tool,
        "bc_tag":         bc_tag_tool,
        "bc_merge":       bc_merge_tool,
        "bc_read":        bc_read_tool,
        "bc_search":      bc_search_tool,
        "bc_challenge":   bc_challenge_tool,  # adversarial review -- no writes
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

def _format_landscape_for_prompt(metrics: list[dict], records_by_id: dict | None = None) -> str:
    """Serialise compute_landscape_metrics() as compact one-liners for the prompt.

    Each record is one line -- enough to scan and identify which records need
    closer inspection. The LLM calls bc_read(record_ids=[...]) to pull full
    claim + summary + tags for any records it wants to examine before deciding.

    Sorted weakest-first (as compute_landscape_metrics returns).
    """
    lines = []
    for m in metrics:
        rid = str(m.get("record_id") or "?")[:20]
        tier = m.get("tier", "?")[:14]
        ret = m.get("retention", "?")[:8]
        age = f"{m.get('age_days', '?')}d"
        acc = m.get("access_count", 0)
        cls = m.get("temporal_class", "neutral")
        surv = m.get("survival", {})
        l1 = surv.get("l1", 0)
        l2 = surv.get("l2", 0)
        ret_action = m.get("retention_action", "keep")
        status = m.get("status", "")
        claim = m.get("claim_snippet", "")[:80]
        lines.append(
            f"[{rid}] tier={tier} ret={ret} age={age} acc={acc} "
            f"L1={l1} L2={l2} policy={ret_action} class={cls} status={status} | {claim}"
        )
    return "\n".join(lines) if lines else "(no records)"


# ---------------------------------------------------------------------------
# Orchestrator shell
# ---------------------------------------------------------------------------

def _load_manager_prompt() -> str:
    """Load the memory manager prompt from config. Falls back to inline stub."""
    import os as _os
    candidates = [
        _os.path.join(_os.environ.get("QZ_ROOT", ""), "config/default/prompts/memory-manager-v0.md"),
        _os.path.join(_os.path.dirname(__file__), "..", "config/default/prompts/memory-manager-v0.md"),
    ]
    for path in candidates:
        try:
            text = open(path, encoding="utf-8").read().strip()
            if text:
                return text
        except Exception:
            pass
    # Minimal fallback if file is missing
    return (
        "You are a memory arbitration agent. Output JSON tool calls only.\n"
        "Session: {session_summary}\nLandscape: {landscape}\n"
        "Retire frieza records (L1=0, L2=0, policy=retire). "
        "Read neutral records before deciding. Challenge risky retires."
    )


_MANAGER_PROMPT = _load_manager_prompt()


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
    prompt_template = prompt_override or _MANAGER_PROMPT
    try:
        prompt = prompt_template.replace("{{SESSION_SUMMARY}}", summary).replace(
            "{{LANDSCAPE}}", landscape_text
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

    # --- 5. Two-turn orchestration loop ---
    # Turn 1: LLM scans landscape, calls bc_read/bc_search to examine suspects.
    # Turn 2: LLM sees read results, outputs bc_promote/bc_retire/bc_merge decisions.
    # Read/search calls are non-destructive so executing them between turns is safe.
    _READ_TOOLS = {"bc_read", "bc_search", "bc_challenge"}
    _WRITE_TOOLS = {"bc_promote", "bc_retire", "bc_merge", "bc_update_tier", "bc_tag"}

    all_actions: list[dict] = []
    total_calls = 0

    for turn in range(2):
        tool_calls = _extract_tool_calls(llm_response)
        total_calls += len(tool_calls)

        if not tool_calls:
            break

        # Separate read vs write calls
        read_calls = [c for c in tool_calls if c.get("name") in _READ_TOOLS]
        write_calls = [c for c in tool_calls if c.get("name") in _WRITE_TOOLS]

        if turn == 0 and read_calls and db is not None:
            # Execute reads, collect results, then do a second LLM turn
            read_results = dispatch_memory_tool_calls(read_calls, db)
            all_actions.extend(read_results)
            # Only do second turn if there were reads and no writes yet
            if not write_calls:
                read_summary = json.dumps(read_results, indent=2)
                continuation = (
                    f"\n\nRead results:\n{read_summary}\n\n"
                    "Now output only bc_promote, bc_retire, bc_merge, bc_update_tier, "
                    "or bc_tag calls based on the content above. "
                    "No more reads. One JSON object per line."
                )
                try:
                    llm_response, llm_reason = _call_llm_compactor(
                        prompt + continuation,
                        llm_base_url=effective_url,
                        llm_model=llm_model or "",
                    )
                except Exception as exc:
                    result["errors"].append(f"turn-2 LLM call failed: {exc}")
                    break
                continue  # go to turn 1 (index 1) to dispatch write calls

        # Execute all remaining calls (writes on turn 0 if no reads, or turn 1)
        remaining = write_calls if turn == 0 else tool_calls
        if remaining and db is not None:
            write_results = dispatch_memory_tool_calls(remaining, db)
            all_actions.extend(write_results)
        break

    result["tool_calls_made"] = total_calls
    result["actions"] = all_actions
    errors = [a for a in all_actions if not a.get("ok")]
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
