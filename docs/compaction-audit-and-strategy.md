# Compaction Audit and Strategy

Date: 2026-05-27
Status: **Planning doc** — Stage 0. No runtime changes made.

---

## 1. Purpose

Answer six questions as concisely as possible, then propose a staged plan:

1. What does Codex currently do for compaction?
2. What does QuantZhai currently do for compaction?
3. What should QuantZhai do better?
4. What parts of issue #8 are useful and should be promoted into the plan?
5. What should the proper LLM compaction prompt / contract look like?
6. What proxy shape is safest, given OpenAI Responses compaction, local
   Qwen/llama.cpp behaviour, prompt caching, and future Codex changes?

---

## 2. Current Codex compaction behaviour

Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`
Codex repo: `/tmp/qz-audit/codex`

### 2.1 Trigger: `model_auto_compact_token_limit`

Source: `codex-rs/core/src/session/turn.rs:152`, `config_toml.rs:105`.

Auto-compaction fires when:

```text
total_usage_tokens >= model_auto_compact_token_limit
```

The limit comes from the model catalog entry for the selected model. If the
catalog does not set it, the default is `None` → `i64::MAX` → never fires.

**QuantZhai implication:** the current qz-codex catalog does not set
`auto_compact_token_limit`. Auto-compaction through this path does not fire
for qz-codex sessions. The proxy's own `context_management.compact_threshold`
path is a separate mechanism that has no knowledge of the Codex limit.

### 2.2 Provider routing: inline vs remote

Source: `codex-rs/model-provider-info/src/lib.rs:392`.

```rust
pub fn supports_remote_compaction(&self) -> bool {
    self.is_openai() || is_azure_responses_provider(...)
}
```

QuantZhai is a local provider (not OpenAI, not Azure). Therefore Codex
**always uses inline (local) compaction** for qz-codex sessions. Codex
never calls `responses/compact` as a dedicated endpoint for the local
provider path.

### 2.3 Inline compaction flow

Source: `codex-rs/core/src/compact.rs`.

1. Triggered by turn.rs when token limit exceeded, or manually via `/compact`.
2. `run_inline_auto_compact_task` sends the `compact_prompt` as `UserInput::Text`
   to the regular `/v1/responses` inference endpoint.
3. The model generates a summary. That summary is extracted from the last
   assistant message in the turn.
4. Replacement history is built:
   ```text
   SUMMARY_PREFIX message (fixed "Another LLM started..." text)
   + summary text
   + user messages from original history (preserved)
   ```
5. Original conversation history is replaced with the compacted history.
6. A `CompactedItem` marker is installed in the thread, emitting a
   `context compacted` message to the TUI.

### 2.4 Default compact prompt

Source: `codex-rs/core/templates/compact/prompt.md`.

```text
You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary
for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly
continue the work.
```

### 2.5 CRITICAL: compact_prompt is configurable

Source: `codex-rs/config/src/config_toml.rs:175`, `profile_toml.rs:56`.

```toml
# config.toml
compact_prompt = "..."

[experimental]
compact_prompt_file = "/path/to/prompt.md"
```

QuantZhai can influence Codex's inline compaction prompt by setting
`compact_prompt` in the Codex user config without any proxy changes.
This is the safest, most reversible intervention point.

### 2.6 Pre/post compact hooks

Source: `codex-rs/config/src/hook_config.rs:41,43`.

Codex supports `pre_compact` and `post_compact` hook groups. These run
around the compaction turn. Not currently used by QuantZhai.

### 2.7 Item handling during inline compact

Source: `codex-rs/core/src/compact.rs:389–404`.

- User messages are preserved.
- Summary is extracted from the last assistant message.
- Items are not individually scored or weighted.
- No survival weighting or exactness-risk classification exists in Codex source.

### 2.8 Remote compaction v2 (OpenAI only, not used for QuantZhai)

Source: `codex-rs/core/src/compact_remote_v2.rs`.

For OpenAI/Azure providers, Codex uses a v2 remote path:
- Appends `ResponseItem::ContextCompaction { encrypted_content: None }` to input.
- Sends to the regular stream endpoint (not `/responses/compact`).
- The OpenAI server returns opaque compaction output.
- Result is installed as replacement history.

This path is **not active** for QuantZhai local provider sessions.

### 2.9 What Codex does NOT expose (in audited source)

- No per-item survival scoring.
- No exactness-risk classification.
- No evidence-to-decision retention.
- No schema-driven anchored summary sections.
- No `context_management.compact_threshold` in outbound request bodies
  (that field is a QuantZhai proxy extension, not a Codex wire field).
- `previous_response_id` is present in the request struct but was absent
  in the live Codex 0.130 capture (see `codex-context-memory-contract.md`).

---

## 3. Current QuantZhai compaction behaviour

Source: `proxy/qz_responses.py`, `proxy/qz_request_router.py`.
Tests: `tests/test_qz_compaction.py`, `tests/smoke_compaction_live.py`.

### 3.1 Blob formats

```text
localcmp:v2: (active) — base64-encoded JSON payload
localcmp:v1: (compat) — accepted on decode, not written
```

v2 payload shape:
```json
{
  "version": 2,
  "source": "turboquant-local",
  "depth": 1,
  "created_at": 1234567890,
  "summary_text": "<|history_summary|>\nPrior turn summary:\n- ...\n<|end_history_summary|>",
  "preserved_items": 8,
  "metadata": {
    "engine": "qwen3.6-bridge",
    "format": "structured-markers-v2"
  }
}
```

### 3.2 Current `COMPACTION_CONFIG` (as of 2026-05-27)

```python
COMPACTION_CONFIG = {
    "keep_recent_items": 20,
    "min_preserve_items": 6,
    "max_summary_chars": 48000,
    "max_tool_output_chars": 3200,
    "max_item_summary_chars": 2000,
    "max_compaction_depth": 8,
    "target_output_tokens": 56000,
}
```

Note: these values are larger than those shown in `docs/compaction-bridge-plan.md`
(which documented the initial delivery). The current config is the live values.

### 3.3 `_estimate_items_tokens`

```python
_approx_tokens(text) = max(1, len(text) // 4)
```

Char-based approximation. Sufficient for threshold gating. Will drift on
non-ASCII (Japanese/Chinese) content since CJK chars tokenize at higher
density than 4 chars/token.

### 3.4 `_summarize_items_for_compaction`

**Pure heuristic — no LLM inference.**

- Calls `_item_text(item)` on each item in the older history.
- Drops: checkpoint markers, harness text, meta-user/assistant text, reasoning
  items, proxy-local tool types.
- For tool calls: extracts name + arguments (truncated).
- For tool results: extracts name + output signal (success/fail + first line).
- For compaction items: extracts prior summary text.
- Wraps in `<|history_summary|>...<|end_history_summary|>`.
- Deduplicates adjacent identical lines.
- Truncates to `max_summary_chars`.

Quality: each item becomes one bullet line. No ranking, no survival weighting,
no preservation of evidence chains or decision boundaries. The summary is a
flat concatenation of normalized text.

### 3.5 `_microcompact_old_tool_results`

- Identifies the "tail" (recent) window using `_tail_start_for_compaction`.
- Replaces tool result items older than the tail with one-line signal messages
  (success/fail + first line or first error line).
- Preserves error signals even when the full output is dropped.

This is the most useful existing mechanism: it retains error/success signals
for old tool calls rather than silently dropping them.

### 3.6 `_build_local_compaction_response`

- Filters checkpoint/harness/meta items.
- Calls `_microcompact_old_tool_results`.
- Identifies the tail window.
- Calls `_summarize_items_for_compaction` on older items.
- Encodes into `localcmp:v2:` blob.
- Returns `response.compaction` object with `[compaction_item, ...recent_items]`.
- Post-build: trims recent items if output exceeds `target_output_tokens`.
- Tracks `depth` (increments on re-compaction, capped at `max_compaction_depth`).

### 3.7 `_expand_local_compaction_items`

On inbound request:
- Decodes `localcmp:v2:` or `localcmp:v1:` blobs → expands to a user message
  containing the `summary_text`.
- Passes native/unknown compaction items through unchanged (safety valve for
  future Codex compaction schemes).

### 3.8 Auto-compaction trigger

In `qz_request_router.py`:
- Checks `context_management.compact_threshold` in the request body.
- If set and `_estimate_items_tokens(input) > threshold`: fires local
  compaction immediately, returns `response.compaction` to Codex without
  hitting the upstream model.

This is a QuantZhai-only field. Codex sends it only when the model catalog
entry has `truncation_policy.limit` set. Because the QuantZhai catalog uses
a 249K context window and does not set `truncation_policy.limit`, this path
does not fire for current default sessions.

### 3.9 Test coverage

| Test class | What it proves |
|---|---|
| `EncodeDecodeTests` (6 tests) | Roundtrip, v1 compat, unknown prefix, non-string, corrupt, non-dict |
| `EstimateTokensTests` (4 tests) | Empty, single message, additive, None |
| `ExpandCompactionItemsTests` (5 tests) | Local expand, v1 expand, native passthrough, non-compaction passthrough, empty |
| `BuildCompactionResponseTests` (10 tests) | Response shape, depth tracking, depth cap, preserved items, usage keys, edge inputs |
| `SummarizeItemsTests` (3 tests) | Returns string, structured markers, empty |

**Not tested:**
- Deep compaction (depth > 2) quality
- Non-ASCII token estimation drift
- What happens when Codex sends auto-compact via inline flow (because Codex's
  `model_auto_compact_token_limit` is not set in the QuantZhai catalog)
- Summary quality: does the output actually help the model recover context?
- Survival weighting: do exact technical facts survive better than filler?
- Evidence-to-decision retention
- Stale fact correction

### 3.10 Current quality verdict

QuantZhai compaction is **mechanically sound but semantically shallow**.

- The blob encoding, passthrough, expansion, and auto-trigger are all working
  (confirmed by the live smoke: 10/10).
- The summary is a flat list of normalized text snippets. It does not rank
  importance, does not preserve decision chains, does not correct stale facts,
  and does not survive-weight exact technical atoms.
- A model reading a deep-compacted history sees increasingly diluted context.

---

## 4. External references and implications

Sources drawn from issue #8 third comment (research pile with weights).

### 4.1 OpenAI official docs (external, informational)

- **Responses Compaction**: server-side compaction reduces context size while
  preserving conversation state. Returned compaction items are opaque and not
  human-interpretable. Safe to drop items before the latest compaction item
  for stateless input-array chaining; do not manually prune when using
  `previous_response_id`.
- **Prompt caching**: benefits exact prefix matches. Static content at the
  beginning; dynamic content later. Tools and messages can be cached if
  identical. QuantZhai currently uses `thread_id` as `prompt_cache_key` (source:
  `codex-context-memory-contract.md`). Stable instruction prefixes benefit most.

### 4.2 Research references (promoted from issue #8)

**ReSum** (arXiv 2509.13313, weight 5):
- Treat long agent trajectories as growing histories periodically compacted
  into compact reasoning states.
- Matches observed opencode behaviour: update an anchored state, do not just
  summarize prose.
- Design pull: compaction should preserve active goals, discoveries, uncertainty,
  and next actions as agent-continuity state.

**MemGPT / virtual context management** (arXiv 2310.08560, weight 5):
- Context as managed memory tiers: hot / active-state / archival.
- For QuantZhai: survival-weighted compaction decides what stays hot.

**AnchorMem / MemMachine** (arXiv 2604.17377, 2604.04853, weight 5):
- Separate retrieval anchors from full immutable context.
- Avoid lossy extraction by preserving raw evidence handles alongside summaries.
- Borrow: `summary + exact anchors + raw evidence handles`.

**LongMemEval** (arXiv 2410.10813, weight 5):
- Evaluation dimensions: information extraction, multi-session reasoning,
  temporal reasoning, knowledge updates, abstention.
- QuantZhai should not evaluate compaction by token ratio alone.

**MemGovern** (arXiv 2601.06789, weight 5):
- Issue/PR/test/commit data → governed experience cards.
- Maps to QuantZhai coding-agent handoff memory.

**LLMLingua / LongLLMLingua** (arXiv 2310.05736, 2310.06839, weight 4):
- Budget controller, importance scoring, iterative compression.
- Borrow cautiously: for coding agents, losing one exact flag can wreck the
  next turn. Use budget controller; do not use semantic-only compression.

**Lost in the Middle** (arXiv 2307.03172, weight 3):
- Relevant information can be ignored depending on placement.
- Put active state and high-risk constraints in predictable positions in the
  compacted history.

### 4.3 Key implications for QuantZhai

1. OpenAI compaction is opaque; QuantZhai must not corrupt or blindly prune it.
2. Codex inline compaction for QuantZhai uses the regular `/v1/responses` path.
3. Codex's `compact_prompt` is configurable — QuantZhai can influence quality
   without proxy changes.
4. Prompt caching favours stable instruction prefixes. Fixed schema sections
   in the anchored summary template should remain stable across turns.
5. Summary quality research unanimously points toward: structured anchored
   update over flat narrative summary.

---

## 5. Issue #8 promoted ideas

All of the following are sound and should be in the plan.

### Survival-weighted compaction

Estimate two quantities per span:

```text
token_cost:    how expensive is this text?
meaning_weight: how dangerous is it to compress/delete/paraphrase this text?
```

Then:
- Preserve exact heavy spans verbatim.
- Summarize medium spans.
- Delete or crush light connective tissue.

### High-value atoms (always high survival weight)

```text
file paths
shell commands and flags
env vars
error strings and exit codes
function/class/test names
commit SHAs
issue/PR IDs
user constraints and corrections
explicit negations (not / never / no / without / unless)
model/profile names
security/privacy boundaries
source-proven facts
stale-fact corrections
blocked/deferred reasons
```

### Anchored summary update beats generic summary

Observed in opencode samples (see issue #8 first and second comments):
- The compactor updated an existing anchored summary with new facts.
- It preserved section structure, integrated discoveries, corrected stale facts.
- It preserved negative constraints and behavioral guardrails as first-class state.
- It preserved evidence provenance: which rg search produced which finding.
- It preserved decision boundaries: "no output shape found → do not implement
  fuzzy denial detection → Pattern E is the viable path."

**Guardrails are project state.** A useful compactor must retain them as
first-class information, not discard them as "meta".

### Evidence-to-decision retention (most important new idea)

From issue #8 second comment:
> Good compaction preserves conclusions.
> Better compaction preserves conclusions plus the evidence boundary that made them safe.

If the compactor loses the negative evidence, the next agent may re-open
already-settled paths and waste context or introduce unsafe guesses.

Scoring dimension to add: does the compacted output preserve
`evidence → inference → decision → deferred alternative`?

### Fixed schema carries instruction

Short compaction prompts work if the schema is rigid and semantically loaded.
The section layout tells the model what kind of state must survive.

From issue #8 first comment:
> Tiny cattle prod, not lecture theatre.

### Deterministic v0 scorer before embeddings

Start with pattern-based survival scoring:
- regex/heuristic detection of paths, commands, SHAs, errors, negations.
- No embeddings, no external calls for v0.
- Emit `span.weight: light|medium|heavy` and `span.exactness_risk: low|medium|high`.

### Scoring dimensions

For compaction evaluation:
- exact path retention
- exact command/flag retention
- error string retention
- negative constraint retention
- evidence-to-decision retention
- stale fact correction
- deferred path preservation
- hallucinated fact rate
- downstream agent recovery quality
- token budget vs retention quality

---

## 6. Evaluation of the user-provided Qwen compaction prompt

The candidate prompt supplied:

```text
You are a conversation summarizer. Your job is to compress the ongoing
discussion into a concise summary that preserves everything important for
continuing the task correctly.
```

With preservation requirements for goals, decisions, constraints, technical
details, status, open questions, ambiguity, next steps, and no invention.

### What is good

- Covers the basic categories (goals, decisions, constraints, status, next steps).
- The "no invention" rule is important and should stay.
- Reasonable as a generic fallback.

### What is too generic

- "Compress into a concise summary" invites loss of exact technical atoms.
- "Everything important" gives the model no survival weighting.
- No schema → output structure varies per run → unstable prefix → worse prompt
  cache reuse.
- No instruction to correct stale facts.
- No instruction to preserve evidence-to-decision chains.
- No instruction to preserve negative constraints and rejected approaches.
- No instruction to preserve exact strings (SHAs, paths, commands).

### What it misses for QuantZhai

- No concept of an existing anchored summary to update.
- No section layout to carry instruction.
- No handling of tool/capture outputs.
- No handling of uncertainty markers.
- No handling of project-specific negative evidence ("not found in source").
- No instruction about behavioral guardrails as first-class project state.

### Verdict

**Do not use this prompt unmodified.** It is a decent generic baseline for
one-shot summarization of chat sessions. It is inadequate for coding-agent
session compaction where exact technical facts, evidence chains, and negative
constraints are the primary survival targets.

Use the anchored update approach instead (see section 9).

---

## 7. Proposed QuantZhai compaction architecture

### Architecture constraints

- Do not replace OpenAI/Codex compaction semantics blindly.
- Preserve unknown native compaction passthrough.
- Keep local compaction versioned (localcmp:v3: for next iteration).
- Do not make compaction create durable memory by itself.
- Do not cross memory_domain/workspace boundaries.
- Preserve exact source/provenance where possible.
- Put static prompt/schema content stable for prompt-cache friendliness.
- Put dynamic conversation-specific content later in the prompt.
- Prefer small rigid schema over giant prompt.
- Treat compaction as a state update operation, not a generic summary.

### Staged plan

---

#### Stage 0: Audit and planning doc (this doc)

**Goal**: Ground the architecture in source evidence.

**Files**: `docs/compaction-audit-and-strategy.md` (this doc).
          `docs/README.md` (index entry).

**Tests/fixtures**: None.

**Acceptance**: Doc exists, contains Codex audit SHA, no runtime changes.

**Failure modes**: None (docs only).

**Why safe**: No code changed.

---

#### Stage 1: Anchored summary schema and prompt

**Goal**: Define the anchored summary schema and the LLM compaction prompt.
No integration yet.

**Files**:
```text
docs/compaction-anchored-schema-v0.md   — schema spec
config/default/prompts/compact-v0.md   — compaction prompt template
```

**Tests/fixtures**:
```text
docs/fixtures/compaction/                — example input/output pairs
  - example-01-basic-coding-session.md  — shows what survives
  - example-02-tool-heavy-session.md    — tool outputs and signals
  - example-03-rejected-approaches.md  — negative constraints preserved
```

**Acceptance**:
- Schema is defined with required sections.
- Prompt is reviewed against issue #8 scoring dimensions.
- At least two fixture examples show expected compaction output.
- Prompt fits in one page.

**Failure modes**:
- Schema too rigid → rejects useful content.
- Schema too loose → model ignores it.
- Prompt too long → hurts token budget.

**Why safe**: Config/docs only. No proxy code changed.

---

#### Stage 2: Deterministic survival-weight span scorer

**Goal**: Build a deterministic Python scorer that classifies spans as
light/medium/heavy and emits `exactness_risk` features.

**Files**:
```text
proxy/qz_survival_weight.py   — span scorer
tests/test_qz_survival_weight.py
```

**Inputs**: Plain text or item list.

**Output**:
```json
{
  "spans": [
    {
      "text": "DOCKER_BUILDKIT=1",
      "weight": "heavy",
      "exactness_risk": "high",
      "features": ["env_var", "exact_token"]
    }
  ]
}
```

**v0 features to detect** (regex/heuristic, no embeddings):
```text
path            — starts with / or ./; contains /
command         — shell command pattern
env_var         — UPPER_CASE= or $VAR pattern
sha             — 7–64 hex chars in context
issue_ref       — #NNN pattern
error_string    — "error:", "failed:", "exception:", "traceback"
negation        — not / never / no / without / unless
version         — semver, vX.Y.Z
test_name       — function/class name pattern in test context
flag            — --flag or -f pattern
```

**Tests**: At least one test per feature class. Test that heavy spans survive
and light filler does not.

**Acceptance**:
- All listed features detected in test corpus.
- Scorer runs in < 5ms on 1000-item history.
- No external dependencies added.

**Failure modes**:
- Over-scores every long word.
- Under-scores rare project-specific terms.

**Why safe**: New module, no proxy integration. Can be tested standalone.

---

#### Stage 3: Fixture/eval harness comparing compaction strategies

**Goal**: Build a fixture harness that evaluates compaction quality against
known inputs and expected outputs.

**Files**:
```text
tests/test_qz_compaction_eval.py
docs/fixtures/compaction/*.md (from Stage 1)
scripts/qz-compaction-eval          — CLI runner
```

**Strategies to compare**:
1. Current heuristic (baseline)
2. Anchored template update (Stage 1 prompt)
3. Survival-weighted anchored update (Stage 2 scorer + Stage 1 prompt)

**Scoring**:
```text
exact_path_retention      — paths preserved verbatim?
exact_command_retention   — commands/flags preserved?
error_retention           — error strings preserved?
negation_retention        — negative constraints preserved?
evidence_decision_chain   — did evidence lead to preserved decision?
stale_fact_correction     — were outdated facts updated?
hallucinated_fact_rate    — new facts invented?
token_budget              — chars in vs chars out ratio
downstream_recovery       — can the model answer correctly from compacted history?
```

**Acceptance**:
- Harness runs offline against fixtures (no live model needed).
- At least 5 fixture cases covering the scoring dimensions above.
- Baseline scores are recorded for comparison.

**Failure modes**:
- Fixture evaluation doesn't correlate with live model quality.
- Scoring dimensions are too subjective.

**Why safe**: Test-only code. No proxy integration.

---

#### Stage 4: Model-generated anchored update using local provider

**Goal**: Implement a real LLM-generated compaction using the local Qwen model
via an internal proxy call. Produce `localcmp:v3:` blobs.

**Files**:
```text
proxy/qz_responses.py   — new _summarize_items_for_compaction_llm()
                         new _build_local_compaction_response_v3()
```

**Flow**:
1. Extract older items from history.
2. Run survival-weight scorer on them.
3. Prepare anchored-update prompt (schema + prev summary + new items).
4. Fire a local inference call to `http://127.0.0.1:LCP_PORT/v1/chat/completions`
   (not Codex path; direct to llama.cpp to avoid recursion).
5. Parse structured response.
6. Encode as `localcmp:v3:` blob with anchored summary sections.

**Token budget**: Target 512-1024 output tokens for the summary. This is a
deliberate tradeoff: high quality within budget.

**Latency budget**: Compaction should complete in < 30s for 95% of sessions.
Heuristic fallback if LLM call fails.

**Tests**:
- Unit test `_build_local_compaction_response_v3` with mock LLM call.
- Integration smoke test against live stack.
- Eval harness fixture scores should be higher than baseline.

**Acceptance**:
- v3 blob format defined and tested.
- LLM compaction produces anchored summary with all required sections.
- Scorer-informed survival hints visible in prompt.
- Heuristic fallback if LLM call errors.
- Live smoke: 10/10 recovery from v3 blob.
- Eval harness: v3 scores >= v0 baseline on path/command/negation retention.

**Failure modes**:
- LLM generates garbage summary for very long histories.
- Latency budget exceeded on deep tool-output histories.
- Prompt is too long for local model context.
- LLM hallucinates facts not in the history.

**Why safe**:
- Heuristic fallback preserves v0 behaviour on failure.
- localcmp:v3: is a new versioned format; v1/v2 compat remains.
- Direct llama.cpp call (not via proxy) avoids recursive compaction.
- No Codex-facing wire changes.

---

#### Stage 5: Hybrid proxy integration with localcmp:v3

**Goal**: Wire v3 compaction into the proxy routing path. Add `localcmp:v3:`
decode support to `_expand_local_compaction_items`.

**Files**:
```text
proxy/qz_responses.py   — v3 expand path, version routing
proxy/qz_request_router.py  — route to v3 when available
tests/test_qz_compaction.py — v3 roundtrip tests
```

**Also**: Update `COMPACTION_CONFIG` with `use_llm_compaction: bool` flag
(off by default; enable via env or profile config).

**Config path**:
```text
QZCOMPACT=llm   → use LLM compaction (v3 blobs)
QZCOMPACT=heuristic → force heuristic (v2 blobs, current default)
QZCOMPACT=auto → use LLM if available, heuristic fallback
```

**Acceptance**:
- LLM compaction enabled/disabled via config without code changes.
- v3 blobs survive encode/decode roundtrip in tests.
- v3 expand preserves all anchored summary sections.
- v0/v1/v2 blobs still decode correctly (no regressions).
- All existing compaction tests pass.

**Failure modes**:
- Config flag ignored.
- v3 decode corrupts history on malformed blob.
- Old v2 sessions fail when proxy updated to v3.

**Why safe**:
- Versioned format: v2 sessions continue to work.
- LLM compaction is opt-in via config.
- Heuristic path unchanged.

---

#### Stage 6: Dogfood/live capture and threshold tuning

**Goal**: Run live qz-codex sessions with LLM compaction enabled, capture
telemetry, and tune thresholds.

**Files**:
```text
var/captures/requests/<request_id>/compact-summary.txt
scripts/qz-benchmark (add compaction quality cases)
```

**Metrics to track**:
- Compaction latency (P50, P95)
- Summary token cost (input vs output)
- Model recovery quality from compacted history
- Hallucinated fact rate (manual inspection of captures)

**Acceptance**:
- At least 10 live sessions captured with LLM compaction.
- Latency P95 < 30s.
- No regressions vs heuristic on context recovery.
- Hallucinated fact rate < 5% on manually inspected captures.

**Also at this stage**: Consider setting `model_auto_compact_token_limit` in
the QuantZhai model catalog so Codex fires auto-compaction itself. Weigh
against the risk of Codex inline compaction using a default prompt unless
`compact_prompt` is also set in the Codex config.

---

## 8. Anchored compaction schema v0

The schema itself carries much of the instruction. Keep sections stable across
runs for prompt-cache friendliness. Put dynamic content (new facts) at the end.

```text
## Goal
One sentence. What is the task/objective?

## Active Constraints & Guardrails
- User corrections (e.g. "never set presence_penalty to 0")
- Negative constraints (e.g. "do not guess Codex contracts")
- Security/privacy boundaries
- Approach restrictions (e.g. "no Docker builds unless asked")

## Current Status
### Done
- Completed items with brief outcome note

### In Progress
- Active items with current state

### Blocked / Deferred
- What is blocked and why
- Deferred approaches and why they were deferred

## Key Decisions
- Decision, plus what evidence made it safe
- Rejected approaches and why

## Evidence Boundaries
- Source: where each key fact came from (rg result, live capture, Codex source)
- Confirmed findings vs inferences (label inferences)
- Negative evidence: what was NOT found and why that matters

## Technical State
### Files / Paths
### Commands / Flags / Env Vars
### SHAs / Versions / Model Names
### Tests / Results
### Tool / Capture Outputs (signals only, not raw)

## Rejected / Abandoned Approaches
- What was tried, why it failed or was abandoned
- Explicit "do not re-attempt" notes

## Open Questions / Uncertainties
- Unresolved questions
- Things that need verification

## Next Actions
- Ordered by priority
- Each action is concrete and actionable

## Provenance / Source Pointers
- Links to captures, commits, issues, test files that support the above
```

### Rules for populating the schema

- **Exact atoms**: paths, commands, SHAs, versions, issue IDs, test names —
  copy verbatim. Never paraphrase.
- **Negative constraints**: preserve the exact wording. "Do not set X" is
  not equivalent to "X is not recommended."
- **Evidence boundaries**: label what was source-confirmed vs inferred.
  "rg found no output shape → inferred" not "confirmed: no output shape."
- **Stale facts**: if new evidence contradicts an older section entry,
  update or delete the old entry. Do not let stale facts survive.
- **Uncertainty**: if you are not sure, say so. Prefer a section entry marked
  "uncertain" over a confident wrong entry.
- **Do not invent**: if a fact was not in the conversation, do not add it.

---

## 9. Recommended compaction prompt v0

This prompt is designed for the anchored update operation. It is short,
schema-driven, and explicit. It assumes there is a prior anchored summary
to update.

```markdown
You are updating an anchored project summary.

Previous summary:
{PREVIOUS_ANCHORED_SUMMARY}

New conversation since the last summary:
{NEW_ITEMS}

Task:
Update the summary to reflect the new conversation.

Rules:
- Preserve the exact section structure below.
- Integrate new facts into the relevant sections.
- Correct stale facts if new evidence contradicts them.
- Preserve exact strings: paths, commands, flags, env vars, SHAs, test names,
  error strings, issue/PR IDs, version numbers, model names.
- Preserve negative constraints verbatim (e.g. "do not set X to Y").
- Preserve behavioral guardrails and user corrections.
- Preserve evidence-to-decision chains: what was found, what was inferred,
  what decision it produced, why it became safe.
- Mark uncertain items as "uncertain" or "not confirmed".
- Do not invent facts not present in the conversation.
- Do not include markdown wrappers, code blocks, or commentary.
- Output only the updated summary in the exact schema format below.

Schema:
## Goal
## Active Constraints & Guardrails
## Current Status
### Done
### In Progress
### Blocked / Deferred
## Key Decisions
## Evidence Boundaries
## Technical State
### Files / Paths
### Commands / Flags / Env Vars
### SHAs / Versions / Model Names
### Tests / Results
### Tool / Capture Outputs
## Rejected / Abandoned Approaches
## Open Questions / Uncertainties
## Next Actions
## Provenance / Source Pointers
```

**Notes on using this prompt**:

1. `{PREVIOUS_ANCHORED_SUMMARY}` is the last compacted summary, or empty
   for first compaction.
2. `{NEW_ITEMS}` is the normalized text of new conversation items, with
   survival-weight hints added by the scorer.
3. Keep the schema block static across all compaction calls. This allows
   prompt caching to apply to the instruction portion.
4. The dynamic `{PREVIOUS_ANCHORED_SUMMARY}` and `{NEW_ITEMS}` sections
   appear after the schema — consistent with prompt caching best practice.

**On the user-provided Qwen prompt**: It is a decent generic baseline for
one-shot chat summarization. Do not use it unmodified for QuantZhai coding-agent
compaction. The anchored update schema above addresses its gaps.

---

## 10. Evaluation plan and fixtures

### Scoring dimensions

| Dimension | Description | Test method |
|---|---|---|
| `exact_path_retention` | File paths in input survive verbatim in output | String match |
| `exact_command_retention` | Commands, flags, env vars preserved | String match |
| `error_retention` | Error strings, exit codes preserved | String match |
| `negation_retention` | "not/never/without" constraints preserved | String match |
| `evidence_decision_chain` | "rg found X → decided Y" link preserved | Manual review |
| `stale_fact_correction` | Outdated fact updated or removed | Fixture annotation |
| `hallucinated_fact_rate` | Facts invented that were not in input | Manual review |
| `token_budget` | chars-in vs chars-out ratio | Measurement |
| `downstream_recovery` | Model answers correctly from compacted history | Live smoke |

### Three compaction strategies for evaluation

1. **Freeform summary** (`"Summarize the conversation."`): expected to lose
   exact constraints and file/test atoms.
2. **Anchored template update** (section 9 prompt, no survival scorer): should
   preserve structure; some exact atoms may be missed.
3. **Survival-weighted anchored update** (section 9 prompt + Stage 2 scorer):
   explicit preservation hints for high-weight spans.

### Minimum fixture set (Stage 3)

```text
fixture-01: coding session with 5 file paths, 3 commands — test path/command retention
fixture-02: tool-heavy session with errors — test error/failure signal retention
fixture-03: session with rejected approaches — test negative constraint retention
fixture-04: multi-decision session — test evidence-to-decision chain retention
fixture-05: session with stale facts — test stale fact correction
```

---

## 11. Risks and non-goals

### Risks

| Risk | Mitigation |
|---|---|
| LLM compaction adds latency | Hard fallback to heuristic on timeout/error |
| LLM hallucination of session facts | Survival scorer identifies high-risk atoms; prompt says "do not invent" |
| Recursive compaction (proxy calls itself) | v3 compaction calls llama.cpp directly, not through proxy |
| Prompt caching disrupted by variable prefix | Static schema in prompt before dynamic content |
| Codex changes compaction protocol | Native passthrough already in place |
| v3 blobs corrupt older proxy versions | Versioned format; older proxy ignores unknown prefix |

### Non-goals

- Do not replace tokenizer accounting.
- Do not claim word length alone equals semantic importance.
- Do not build a full memory system.
- Do not add embeddings/vector search for v0.
- Do not grant memory_domain access or cross workspace boundaries.
- Do not let compaction create durable BrainCaseDB records automatically.
- Do not replace OpenAI/Codex compaction semantics blindly.

---

## 12. Implementation roadmap

```text
Stage 0 (this doc)    — 2026-05-27     — planning doc only
Stage 1               — next available  — anchored schema + prompt template
Stage 2               — after Stage 1   — deterministic survival-weight scorer
Stage 3               — after Stage 2   — fixture/eval harness
Stage 4               — after Stage 3   — LLM-generated v3 blobs
Stage 5               — after Stage 4   — proxy integration, QZCOMPACT config
Stage 6               — after Stage 5   — dogfood/live tuning
```

Priority note: Stages 0–3 are pure docs/tests. No proxy changes.
Stages 4–6 touch production code and require the live stack.

Stages 1–3 are safe to work in parallel with other QuantZhai priorities
(operational store, Slice B-impl, repeated-read v2) because they have no
code dependencies.

---

## 13. Open questions

1. **Should QuantZhai set `model_auto_compact_token_limit` in the Qwen model
   catalog?** If yes, Codex will auto-compact using its inline path. But then
   QuantZhai's `compact_prompt` config also needs to be set, or Codex uses
   its default generic prompt. Risk: two compaction paths active simultaneously.

2. **Should Stage 4 use a separate llama.cpp slot or the same model?** If the
   same slot is used mid-session, the compaction call will interrupt streaming.
   If a separate slot, it requires a second llama.cpp instance or a queue.

3. **What is the right `target_output_tokens` for LLM compaction?** Current
   heuristic target is 56000. A model-generated anchored summary at 512-1024
   output tokens would be much smaller. Does this cause Codex context pressure?

4. **How do we handle the QZSTATE block during compaction?** The QZSTATE block
   is injected into instructions, not into input items. It should not appear
   in the compacted summary. Currently this is handled by the harness/marker
   filtering in `_build_local_compaction_response`.

5. **Should `pre_compact` / `post_compact` Codex hooks be used for Stage 5?**
   The hooks exist but their utility for QuantZhai is unclear. Worth exploring
   for capture/logging at compaction boundaries.

6. **Can the anchored summary schema be versioned independently of the blob
   format?** If the schema changes between runs, stale summaries may not parse
   correctly. Consider embedding a `schema_version` field in the v3 blob.

7. **How do we bootstrap `PREVIOUS_ANCHORED_SUMMARY` for first compaction in
   a session?** Option A: empty previous summary (compactor starts from scratch).
   Option B: inject a stub with just the Goal section pre-populated from the
   system prompt/instructions. Option A is simpler and correct for v0.
