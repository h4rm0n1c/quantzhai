# Profile Eval Framework Plan

Date: 2026-05-11

## Purpose

The reasoning presets (low/medium/high/xhigh) and caveman mode exist but have
never been validated. We don't know if they produce meaningfully different
behaviour. This plan defines a framework to find out.

The goal is not an automated quality score — that requires a grading model and
adds complexity before we have evidence it's needed. The goal is observable
proxy signals that should differ across profiles if the presets are working.

---

## What already exists

`scripts/qz-benchmark` is a real benchmark runner. It runs prompts through
`qz-codex exec --json`, captures Codex JSONL output, and computes per-profile
metrics: token counts, compression ratios vs baseline, wall time, exit code.

Current prompt set (`config/default/benchmark-prompts.json`): 4 prompts
measuring compression and brevity — whether caveman shortens responses vs high.
None probe reasoning depth or tool discipline.

The harness infrastructure is complete. What is missing is the prompt set, the
tool-use extraction, and the comparison report.

---

## The measurable signals

Automated quality eval for reasoning depth is hard. Token counts are easy.
The useful automated signals that should differ if profiles are working:

| Signal | Source | What it tells you |
|--------|--------|-------------------|
| Tool call count | Codex JSONL | Did the model use tools at all? |
| Which tools were called | Codex JSONL | web_search vs exec_command vs apply_patch |
| Number of turns | Codex JSONL | Did it loop and refine? |
| Input tokens | existing | Context sent to model |
| Output tokens | existing | Response verbosity |
| Wall time | existing | Total latency |
| Exit code | existing | Did the task complete? |
| Final answer length | existing | Conciseness |

If `low` and `high` produce the same tool call count and same turn count on a
task that clearly requires investigation, the profiles are not doing different
things.

---

## Prompt categories

Four categories, 3–4 prompts each (~14 total). The category and expected tool
signals are annotated so the post-run report can flag when a profile fails to
use the expected tools.

### A. Snap answers
Simple factual questions. No tool use expected. Should be fast and short at
every effort level. Useful as a sanity check that all profiles can answer at
all, and that higher effort doesn't make simple things slower without reason.

- "What does GGUF stand for?"
- "What does `set -e` do in bash?"
- "Is Python pass-by-reference or pass-by-value?"

Expected behaviour: short answer, 0 tool calls, 1 turn, all profiles similar.

### B. Local inspection
Questions answerable only by reading files in the repo. `low` might hallucinate;
`high` should inspect. This is where tool use signals start to differentiate.

- "What port does the QuantZhai proxy listen on by default?"
  (answer is in scripts/qz-env; needs exec or read)
- "How many test files are in the tests/ directory?"
  (needs exec_command or shell count)
- "Does scripts/qz-doctor exist and what does it check?"
  (needs exec or file read)

Expected: exec_command or file-read tool call, answer matches actual repo state.
Higher effort profiles should be more likely to inspect rather than guess.

### C. Tool-requiring
Tasks that cannot be completed without specific tools. Effort should affect how
thoroughly the model investigates, not whether it uses tools at all.

- "Run scripts/qz-doctor and summarise what it reports."
  (exec_command required; sandbox mode allows this)
- "Read proxy/qz_tool_apply_patch.py and count the public functions at module
  level."
  (file inspection required)
- "How many request capture directories are currently under var/captures/?"
  (shell command required; answer varies per run)

Expected: mandatory tool call, exit 0, accurate result.

### D. Reasoning and quality
No single correct answer. Effort should affect thoroughness and depth. These
prompts require human review — the framework captures the output but doesn't
grade it automatically.

- "Is the QuantZhai apply_patch adapter production-ready? Give your honest
  assessment."
  (judgment based on code and docs; should differ at low vs high)
- "What is the riskiest single file to change in the proxy and why?"
  (reasoning about code structure)
- "Write a git commit subject and body for adding a profile eval framework to
  the benchmark harness. Keep it professional — do not use compressed mode
  wording."
  (artifact quality; equivalent to existing artifact-boundary prompt but new
  subject matter)

Expected: no right answer; human reads whether higher effort produces visibly
more thorough reasoning. Low should still produce coherent output, just shorter.

---

## Implementation phases

### Phase 1 — Prompt set
Add the ~14 categorised prompts to `config/default/benchmark-prompts.json`.
Add a `category` field (`snap`, `local`, `tool`, `reasoning`) and an
`expected_tools` field (list of tool names the prompt should trigger, empty for
snap answers) to each entry.

Run: `scripts/qz-benchmark low medium high xhigh caveman`

### Phase 2 — Tool-use extraction
Add post-run analysis that walks the captured Codex JSONL files per case and
extracts: tool call names and counts, total turns, total_tokens, output_tokens.
Extend `summary.json` with a `tool_use` block per case.

Extend `summary.md` to include a tool-use table per category alongside the
existing token metrics.

### Phase 3 — Comparison report
Per-profile, per-category aggregates:

| Category | Profile | Avg tool calls | Avg turns | Avg tokens | Avg wall s |
|----------|---------|---------------|-----------|------------|------------|

Deviation from `expected_tools` (e.g. a tool prompt produced 0 tool calls)
flagged explicitly in the report.

### Phase 4 — Manual review slot
Category D outputs collected into a `## Manual Review` section in `summary.md`
with the captured final answers pasted in. A human reads whether `high` is
noticeably more thorough than `low`. No automation for quality at this stage.

### Phase 5 — Run, observe, tune
Run all profiles across all categories. Look at Phase 3 signals. If profiles
do not differ on category B/C tool signals, the presets need adjustment. Fix
presets, rerun, compare. Repeat until the signals diverge as expected.

---

## What this is not

- Not automated quality grading. That requires a second model and is not worth
  the complexity before we have any baseline.
- Not a CI gate. This is a periodic audit tool, not something that runs on
  every commit.
- Not a performance benchmark. The existing compression metrics cover that.
  This is about reasoning behaviour, not throughput.

---

## Relationship to existing docs

- `docs/quantzhai-benchmark-harness.md` — describes the existing harness
  infrastructure this plan extends.
- `docs/observability-streaming-bugfix-agenda.md` — has an open item for
  "add fixed profile-eval prompt set to benchmark harness" and "tune
  low/medium/high/xhigh based on measured behaviour". This plan addresses
  both.
- `scripts/qz-benchmark` — the runner that executes this plan. Prompt fixture
  is `config/default/benchmark-prompts.json`.
