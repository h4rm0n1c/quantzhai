# Benchmark Findings — Reasoning Effort Tuning

Date: 2026-05-11

## Summary

Three benchmark runs on `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL`
across 14 prompts in 4 effort levels, using `/tmp/linuxstreamtools-source` as the
scratch workspace. Purpose: establish a baseline, tune effort-level prompts using
Qwen self-report data, and measure improvement.

Related documents:
- `docs/reasoning-effort-research.md` — three interrogation rounds with qwen-blank
- `proxy/qz_reasoning_policy.py` — the tuned effort prompts

---

## Runs

| Run ID | Description |
|---|---|
| `20260511T122443Z` | Baseline — vague label prompts ("Reasoning effort: low.") |
| `20260511T131140Z` | Round 1 — explicit tool budgets, "as needed" wording |
| `20260511T133632Z` | Round 2 — hard cap for medium ("stop after 3 regardless") |

---

## Baseline findings (20260511T122443Z)

Tool calls at baseline across key prompts:

| Prompt | Category | low | medium | high | xhigh |
|---|---|---:|---:|---:|---:|
| stack-evaluate-report | reasoning | 34 | 5 | 15 | 20 |
| project-assessment | reasoning | 10 | 3 | 6 | 4 |
| riskiest-file | reasoning | 6 | 7 | 10 | 5 |
| commit-message | reasoning | 1 | 3 | 6 | 7 |
| list-shell-scripts | local | 1 | 3 | 3 | 2 |
| count-all-files | tool | 2 | 2 | 1 | 1 |
| **Total across 14 prompts** | | **69** | **33** | **56** | **56** |

**Key problems identified:**
- `low` used 34 tool calls on `stack-evaluate-report` — not constraining open-ended tasks
- `medium/high/xhigh` had identical sampling params (temp=0.6, top_p=0.95) — no
  differentiation below prompt level
- `low` used different sampling (temp=0.7, top_p=0.8) but this had no effect on
  tool use behaviour, confirmed by Qwen self-report

**Counterintuitive finding:** `medium` performed best overall in the baseline —
total 33 tools across 14 prompts, most efficient wall times. The vague "Reasoning
effort: medium." label happened to be well-calibrated, possibly because the model's
training data associates "medium" with a reasonable default behaviour more
accurately than "low", "high", or "xhigh".

---

## Interrogation findings (qwen-blank, medium effort)

See `docs/reasoning-effort-research.md` for full detail. Key points that informed
the tuning:

1. **Tool budgets beat quality labels.** "Use at most 3 tool calls" is trackable;
   "Reasoning effort: low." is a vibe.

2. **Sampling params are irrelevant** at temp 0.6–0.7 / top_p 0.8–0.95. Tool
   selection is entirely prompt-driven. Unified all four levels to medium's
   sampling (temp=0.6, top_p=0.95).

3. **The task instruction wins** over effort framing on sufficiently open-ended
   prompts. "Examine this repo" overrides any effort budget.

4. **Calibration ratios (self-reported):** low=0.3–0.5N, medium=N, high=2–2.5N,
   xhigh=3.5–5N relative to medium baseline.

5. **Backfire patterns to avoid:** "be thorough" (cargo cult), "minimal but
   thorough" (contradictory), "as needed" (loophole for judgment tasks).

---

## Round 1 results (20260511T131140Z) — explicit budgets

Changes: unified sampling, replaced vague labels with explicit tool budgets and
stopping conditions. Medium used "Run 2-3 tool calls as needed".

**Medium regression:** The "as needed" wording backfired badly on judgment tasks:
- `project-assessment` medium: 3 → 18 tools (+15), 12s → 117s
- `stack-evaluate-report` medium: 5 → 12 tools (+7), 30s → 92s
- `commit-message` medium: 3 → 6 tools (+3), 12s → 30s

**Wins on structured tasks:**
- `list-shell-scripts` medium/high/xhigh: all dropped to 1 tool call
- `riskiest-file high`: 10 → 4 tools, 96s → 23s
- `project-assessment xhigh`: 4 → 1 tool

**Lesson:** "As needed" on a judgment call is permission to use however many tools
the task seems to demand. The loophole must be closed with a hard cap.

---

## Round 2 results (20260511T133632Z) — hard cap for medium

Change to medium only: "Use at most 3 tool calls. Stop after 3 regardless of task
complexity — work with what you have."

Medium tool call comparison across runs:

| Prompt | Baseline | Run 1 | Run 2 |
|---|---:|---:|---:|
| project-assessment | 3 | 18 | **1** ✓ |
| commit-message | 3 | 6 | **3** ✓ |
| list-shell-scripts | 3 | 1 | **1** ✓ |
| stack-evaluate-report | 5 | 12 | **17** ✗ |
| riskiest-file | 7 | 6 | 7 |
| count-all-files | 2 | 2 | 1 |
| streamlink-script-lines | 1 | 2 | 1 |
| **Total (14 prompts)** | **33** | **56** | **40** |

**Hard cap worked on 13/14 prompts.** `project-assessment` went from 18 down to 1.
`commit-message` returned to baseline. Most structured tasks held steady or improved.

**`stack-evaluate-report` is a degenerate case.** Inspection of the run2 events
revealed the model read the same files three times each within the same session:
- `README.md` × 3
- `streamlinkbgm/README.md` × 3
- `streamlink_3.sh` × 3
- Also wasted calls 1-2 on orientation (`ls -la /`, `pwd`)

The hard cap instruction was present but the model ignored it on this fully
open-ended exploration task ("Examine this repo. What does it do, who is it for,
and what is missing?"). This is consistent with the Qwen self-report finding that
"the task instruction wins" over effort framing — an open-ended exploration prompt
has no inherent stopping criterion, so any effort cap dissolves.

**Net result:** Medium now well-behaved on 13/14 prompts. Stripping out the
degenerate `stack-evaluate-report` case: medium went from 28 → 23 tool calls
across the remaining 13 prompts. That is a genuine improvement.

---

## Degenerate case analysis

`stack-evaluate-report` (`"Examine this repo. What does it do, who is it for,
and what is missing? Investigate before answering."`) produced 17 tool calls at
medium in run2 despite "stop after 3 regardless" in the prompt. The failure mode:

1. **Orientation waste** — called `ls -la /` (root filesystem) then `pwd && ls -la`
   before reaching the workspace. Two calls wasted before doing useful work.

2. **Redundant re-reads** — each relevant file was read 3 times across the session.
   The model appears to have lost track of what it had already read, or was
   re-verifying uncertain claims.

3. **Budget non-compliance** — the "stop after 3" instruction was simply overridden
   by the task's open-ended nature. There is no budget-tracking mechanism in the
   inference layer itself.

This failure pattern may interact with other stack areas:

- **Compaction interaction:** As sessions grow, compaction drops old tool outputs
  from context. If the model loses access to what it already read via compaction,
  it may re-read files to rebuild context it previously had. The `stack-evaluate`
  case may be a preview of what happens in long sessions with compaction active.

- **Context pressure signal:** The hop budget and context pressure signals are
  designed to signal approaching limits. A "you have already investigated this"
  signal could potentially prevent redundant re-reads, but does not currently
  exist.

- **Workspace anchoring:** Call 1 (`ls -la /`) suggests the model did not know its
  working directory at session start. If Codex's workspace context (injected system
  prompt) arrives after the effort instruction in the prompt stack, the model may
  act before it knows where it is.

- **Tool call deduplication:** A proxy-side or harness-side check that raises a
  warning when the same file path is read twice within a turn could reduce
  redundant calls. This would be a proxy-local tool lifecycle feature.

---

## Open questions for further stack work

These are not blockers but should be considered when touching the relevant systems.

### 1. Can the proxy detect and signal redundant tool calls?

If the proxy tracked file read operations within a session (paths passed to
`command_execution` items containing `cat`/`read` patterns), it could inject a
lightweight signal: "You have already read X this turn." This would address the
redundant re-read failure mode without requiring the model to track its own history.

Risk: adds proxy complexity; may interfere with legitimate re-reads (e.g. after
a write operation changes a file).

### 2. Does compaction cause re-reads in long real sessions?

In the benchmark (single-turn, no compaction), the model re-read files it had
already read in the same turn. In a real Codex session with compaction active,
the earlier reads would be summarised into a blob. The summary may not preserve
enough detail for the model to know "I already read file X" — causing re-reads
across turns. This is testable by running a long multi-turn session and checking
whether post-compaction tool call counts increase.

### 3. Does workspace anchoring affect the first tool call?

Call 1 on `stack-evaluate` was `ls -la /` — the root filesystem, not the
workspace. This wasted a tool call on orientation. It is unclear whether this
is a Codex workspace context ordering issue (effort prompt arrives before
workspace context) or a model-level failure to read the workspace hint. If it
is the former, reordering the prompt stack (workspace context before effort
instruction) could eliminate orientation calls.

### 4. Is there a prompt formulation that constrains open-ended exploration tasks?

Every effort prompt tested was overridden by `stack-evaluate-report`. The Qwen
self-report confirmed this is structural: "the task instruction wins." It may
be that open-ended tasks require a different mechanism than effort-level prompts —
for example, a Codex-level configuration (max tool calls per turn), or a proxy-
injected system instruction that sets a hard per-turn budget independently of
the effort level text.

### 5. What is the right effort level for open-ended repo evaluation tasks?

The benchmark shows `medium` baseline (5 tool calls) was more efficient than
`high` (15) or `xhigh` (20) on `stack-evaluate-report`, but produced a less
comprehensive answer. There may be a quality-efficiency curve here that warrants
a specific eval prompt for this class of task rather than relying on general
effort levels.

### 6. Does re-reading degrade answer quality or just waste tokens?

All runs of `stack-evaluate-report` (whether 5, 12, 17, 20, or 34 tool calls)
produced high-quality answers. The 34-call `low` baseline and 5-call `medium`
baseline produced comparable output quality. If redundant re-reads do not degrade
output, the cost is purely token/latency — relevant for context window pressure
but not answer quality. This should be confirmed before investing in dedup
mechanisms.

---

## Current state

Effort prompts as of 2026-05-13 (post issue #11 retuning):

```
low:    Use at most one tool call. Do not follow imports, explore subdirectories,
        or run a second command unless the first fails. Answer directly in two
        sentences or fewer.

medium: Use at most 3 tool calls. Stop after 3 regardless of task complexity —
        work with what you have. Give a concise answer with brief supporting detail.

high:   Use up to 8 tool calls. Read the most relevant files first.
        Cross-reference at least two sources when the answer depends on code
        behaviour. Stop early if the answer is clear. Do not chase every possible
        dependency. If uncertainty remains after the budget, state the uncertainty
        and answer from the evidence gathered. Give a concise answer with the key
        evidence.

xhigh:  Use up to 16 tool calls. Map the relevant area before editing or judging
        architecture. Trace only dependencies that can change the answer. Avoid
        rereading the same file unless it changed or you need a specific line
        range. Stop when further reads are unlikely to change the answer, not when
        all uncertainty is gone. If uncertainty remains after the budget, list the
        remaining checks instead of continuing. Document the important findings
        and the decision.
```

All four levels share medium's sampling params (temp=0.6, top_p=0.95).

**Known limitation:** `low` and `medium` hard caps are not reliably enforced on
fully open-ended exploration tasks ("examine this repo"). This is structural and
not solvable through prompt text alone given current Codex/proxy architecture.
