# Anchored Compaction Schema v0

Date: 2026-05-27
Status: **Stage 2** — deterministic survival-weight scorer complete. No proxy integration.
Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`
Codex audit repo: `/tmp/qz-audit/codex`

---

## Purpose

This document defines the canonical anchored summary schema v0 for QuantZhai
compaction. It is the specification backing:

- `config/default/prompts/compact-v0.md` — the LLM compaction prompt
- `docs/fixtures/compaction/` — fixture examples that test schema compliance
- Stage 2: the deterministic survival-weight span scorer
- Stage 3: the fixture/eval harness

The schema is designed for **coding-agent context compaction**, not generic
chat summarization. Its primary survival targets are exact technical atoms,
evidence-to-decision chains, and negative constraints — the facts a model
needs to continue work without re-investigating already-settled questions.

---

## Design Principles

1. **Anchored update, not generic summary.** Each compaction call updates an
   existing anchored summary. It does not produce a new freeform summary from
   scratch. This allows stable section structure across compaction turns and
   benefits prompt-cache reuse.

2. **Schema carries instruction.** Fixed section names tell the model what kind
   of state must survive. The compaction prompt can be short precisely because
   the schema is semantically loaded.

3. **Exact atoms are non-negotiable.** Paths, commands, flags, env vars, SHAs,
   test names, error strings, issue/PR IDs, version numbers, and model names
   must be copied verbatim. Paraphrasing any of these is a compaction failure.

4. **Negative constraints are project state.** "Do not set X to Y" is not a
   meta-comment about the session. It is a guardrail with ongoing operational
   effect. It survives compaction as a first-class section entry.

5. **Evidence boundaries are preserved.** The chain `evidence → inference →
   decision → deferred alternative` must survive intact. Losing the negative
   evidence causes the next agent to re-open already-settled paths.

6. **Static structure before dynamic content.** Schema sections are the same
   every turn. Dynamic content (previous summary, new items) appears after the
   static schema in the prompt. This maximises prompt-cache coverage of the
   instruction and schema blocks.

7. **Stale facts are corrected, not accumulated.** If new evidence contradicts
   an older entry, the old entry is updated or deleted. Compacted summaries do
   not accumulate contradictions.

8. **Uncertainty is explicit.** If a fact is inferred rather than confirmed,
   it is marked as such. A confident wrong entry is worse than an honest
   uncertain one.

---

## Schema v0

The anchored summary uses the following fixed section structure. Section names
are exact — do not rename, merge, or reorder them.

```markdown
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

---

## Field and Section Rules

### `## Goal`

One sentence. What is the task or objective for this session or slice of work?
Updated if the goal changes. Not used for sub-task status.

### `## Active Constraints & Guardrails`

Bullet list. Each bullet is one constraint. Constraints include:

- User corrections (e.g. `do not set presence_penalty to 0`)
- Explicit negative constraints (e.g. `do not guess Codex contracts`)
- Security or privacy boundaries
- Approach restrictions (e.g. `no Docker builds unless the user asks`)
- Behavioral guardrails carried from AGENTS.md or docs

**These are never summarized or softened.** Copy the exact wording. "Do not
set X" is not equivalent to "X is not recommended."

### `## Current Status`

Three sub-sections. Do not add others.

#### `### Done`
Bullet list of completed items. Each bullet includes the outcome or the commit
SHA if the work was committed. Example: `Fix import-mode regression — commit
0627f39`.

#### `### In Progress`
Bullet list of active items and their current state. Enough to resume work
without re-reading the full history.

#### `### Blocked / Deferred`
Bullet list of blocked items (what they are blocked on) and deferred items
(what they are deferred on and why).

### `## Key Decisions`

Bullet list. Each bullet is one decision, the evidence or reasoning that made
it safe, and (where relevant) the alternative that was rejected.

Format: `Decision: <what>. Evidence: <source>. Rejected: <alternative and why>.`

Do not summarize a decision in a way that loses the evidence basis. A decision
without evidence basis is not a key decision — it is folklore.

### `## Evidence Boundaries`

Bullet list. Distinguishes confirmed source findings from inferences.

- **Confirmed** (source-cited): `rg found no output shape in source: inferred
  fuzzy denial detection is not needed.`
- **Confirmed** (live capture): `capture qz_req_1778487003912_1d90 shows
  type: compaction in incoming-request.json.`
- **Negative evidence**: `compact_threshold not found in Codex outbound requests
  (audited codex-rs/core/src/compact.rs). This field is QuantZhai-only.`
- **Inference** (label explicitly): `Inference: inline compaction always active
  for local provider because supports_remote_compaction() returns false for
  non-OpenAI providers.`

Do not promote inferences to confirmed facts. Do not drop negative evidence.

### `## Technical State`

Five sub-sections. Do not add others.

#### `### Files / Paths`
List of relevant file paths. Copy exact. Example:
```
proxy/qz_responses.py
config/default/prompts/compact-v0.md
/tmp/qz-audit/codex/codex-rs/core/src/compact.rs
```

#### `### Commands / Flags / Env Vars`
Exact commands, flags, and env vars relevant to the current work. Example:
```
bash -n scripts/qz-env
python3 -m py_compile proxy/quantzhai_proxy.py
QZCOMPACT=llm
--compact_prompt_file
```

#### `### SHAs / Versions / Model Names`
Exact commit SHAs (7+ chars), version strings, and model/profile names. Example:
```
095b12c — Add compaction audit and strategy plan
46f30d02828bd4c52827e5f0482a6f2a982cce5b — Codex audit SHA
Qwen3.6Turbo-IQ4_XS
```

#### `### Tests / Results`
Test names, test files, and their outcomes. Example:
```
tests/test_qz_compaction.py — 29 unit tests PASS
tests/smoke_compaction_live.py — 10/10 live smoke PASS (2026-05-11)
```

#### `### Tool / Capture Outputs`
Signals only — not raw output. One line per tool or capture result.
Example:
```
rg "compact_threshold" proxy — found: qz_request_router.py:950–963
curl /qz/status — backend.selected_backend_id: Qwen3.6Turbo-IQ4_XS
```

### `## Rejected / Abandoned Approaches`

Bullet list. Each entry includes:
- What was tried or proposed
- Why it failed or was explicitly rejected
- Whether it should not be re-attempted (explicit note if so)

Example:
```
- Fuzzy denial detection for Codex request routing: no output shape found in
  source (codex-rs/core/src/compact.rs). Do not implement. Pattern E is the
  viable path.
- sed for doc edits: disallowed by agent rules. Use read/write tools instead.
```

### `## Open Questions / Uncertainties`

Bullet list of unresolved questions and items needing verification. Each
entry should note what would resolve it and what the risk is if unresolved.

### `## Next Actions`

Ordered bullet list. Each action is concrete, actionable, and includes enough
context to start without re-reading the full history. Example:
```
1. Write config/default/prompts/compact-v0.md (Stage 1 prompt file)
2. Create docs/fixtures/compaction/ fixture examples (Stage 1)
3. Implement proxy/qz_survival_weight.py (Stage 2)
```

### `## Provenance / Source Pointers`

Links and references to captures, commits, issues, and test files that support
the state above. This section is the audit trail — it must not be emptied. Old
pointers are kept (they are the evidence record); new pointers are added.

---

## Exact Atom Preservation Rules

The following must be copied verbatim whenever they appear in the conversation:

| Atom type | Examples |
|---|---|
| File paths | `proxy/qz_responses.py`, `/tmp/qz-audit/codex/codex-rs/core/src/compact.rs` |
| Shell commands | `bash -n scripts/qz-env`, `python3 -m py_compile proxy/quantzhai_proxy.py` |
| Flags and options | `--compact_prompt_file`, `-n`, `--ff-only` |
| Environment variables | `QZCOMPACT`, `QZ_DOCKER_CMD`, `DOCKER_BUILDKIT` |
| Commit SHAs | `095b12c`, `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Issue/PR IDs | `#8`, `#66`, `#74` |
| Version strings | `localcmp:v2:`, `Codex 0.130`, `v3` |
| Model/profile names | `Qwen3.6Turbo-IQ4_XS`, `caveman/HauhauCS-Aggressive` |
| Error strings | `"error: profile symlink target not found"` |
| Test names | `EncodeDecodeTests`, `BuildCompactionResponseTests` |
| Config keys | `compact_prompt`, `experimental_compact_prompt_file`, `keep_recent_items` |
| Marker strings | `<\|history_summary\|>`, `localcmp:v2:` |

**Paraphrasing any of these is a compaction failure.**

---

## Evidence-to-Decision Retention Rules

A decision entry in `## Key Decisions` is only valid if the evidence basis
is traceable. The compactor must preserve:

1. **The evidence**: what was found (rg result, capture, Codex source line).
2. **The inference**: what the evidence implies.
3. **The decision**: what was concluded or chosen.
4. **The deferred alternative**: what was considered but not taken, and why.

Example of a well-preserved chain:

```
Decision: QuantZhai always uses inline compaction for local provider sessions.
Evidence: codex-rs/model-provider-info/src/lib.rs:392 — supports_remote_compaction()
  returns true only for OpenAI and Azure providers. QuantZhai is neither.
Inference: Codex never calls responses/compact as a dedicated endpoint for
  the local provider path.
Deferred: Remote compaction v2 path (codex-rs/core/src/compact_remote_v2.rs)
  — exists but only active for OpenAI/Azure sessions.
```

A compacted entry that loses the evidence source or the deferred alternative
fails this rule.

---

## Stale Fact Correction Rules

When new evidence contradicts an existing section entry:

1. **Update the entry** with the new fact and note the correction.
2. **Delete** the old incorrect entry if it is fully superseded.
3. **Do not accumulate contradictions.** Both the old and new fact must not
   appear side by side without an explicit resolution note.

Stale fact example:
```
Old: COMPACTION_CONFIG max_summary_chars: 16000 (docs/compaction-bridge-plan.md)
New: COMPACTION_CONFIG max_summary_chars: 48000 (live proxy/qz_responses.py, 2026-05-27)
Resolution: compaction-bridge-plan.md documented initial delivery values.
  Current live values are larger. Use proxy source as authoritative.
```

---

## Uncertainty Rules

Mark items explicitly when:

- A finding is an inference, not a source-confirmed fact.
- A value was not verified against live stack (only against source or docs).
- A state may have changed since the last capture.

Use these labels:
- `(uncertain)` — fact is plausible but not confirmed
- `(inferred)` — derived from evidence, not directly observed
- `(not confirmed)` — the expected fact was not found; its absence is the finding

Do not upgrade an uncertain entry to confirmed without new evidence. Do not
leave uncertain entries unmarked.

---

## Non-Goals

- **Not a general conversation summary.** This schema is for coding-agent
  session state. It does not summarize chat discussion or general knowledge.
- **Not a memory record.** This schema does not create durable BrainCaseDB
  records. It is transient compaction state for the current session only.
- **Not a tokenizer replacement.** The schema does not account for token costs
  of individual spans. That is the job of the Stage 2 survival-weight scorer.
- **Not a proxy config.** The schema does not affect QuantZhai proxy behavior
  directly. It is a prompt/config artifact consumed by Codex inline compaction.
- **Not a complete session log.** The schema does not preserve raw tool output
  or full conversation text. It preserves signals, facts, and decision chains.

---

## Relationship to Stage 2 Survival-Weight Scorer

Stage 2 (`proxy/qz_survival_weight.py`, planned) will classify spans of
conversation text as `light`, `medium`, or `heavy` based on heuristic features
(paths, commands, SHAs, negations, errors, etc.). The scorer is intended to:

1. Identify heavy spans that must be preserved verbatim in the `## Technical
   State` sub-sections.
2. Identify medium spans (decisions, evidence) that should be compressed but
   not dropped.
3. Identify light spans (connective prose, filler) that can be dropped.

Stage 2 does not change the schema itself. It adds survival-weight hints to
the conversation items fed into the compaction prompt, helping the model follow
the exact-atom rules above without relying solely on prompt instruction.

---

## Relationship to Codex `compact_prompt_file`

Codex supports configuring the inline compaction prompt via:

```toml
# config.toml — global setting
compact_prompt = "..."

# config.toml [experimental] section or profile TOML
experimental_compact_prompt_file = "/path/to/prompt.md"
```

Source (audited):
- `compact_prompt` field: `codex-rs/config/src/config_toml.rs:175`
- `experimental_compact_prompt_file` field (top-level): `codex-rs/config/src/config_toml.rs:449`
- `experimental_compact_prompt_file` field (profile): `codex-rs/config/src/profile_toml.rs:56`

The file `config/default/prompts/compact-v0.md` is the QuantZhai candidate
prompt file for this field. It contains the task instruction, schema listing,
and placeholders for the previous summary and new conversation items. See
`docs/compaction-codex-setup.md` for wiring instructions.

**Important**: Codex's inline compaction sends the `compact_prompt` as a
`UserInput::Text` turn to the regular `/v1/responses` endpoint. It does not
template `{{PREVIOUS_ANCHORED_SUMMARY}}` or `{{NEW_CONVERSATION}}` placeholders
automatically. Those placeholders in `compact-v0.md` document the intended
QuantZhai integration (Stage 4+), where the proxy or a pre-pass will populate
them before the compaction turn. For current Codex inline use, the prompt is
sent as-is, and Codex appends the conversation context from the session.

---

## Versioning Notes

- **Schema version**: `v0`. This is the initial schema. Breaking changes
  (renamed sections, new required sections) will increment to `v1`.
- **Non-breaking additions**: new optional sub-sections or new field guidance
  notes do not require a version bump, but should be noted in this doc's date
  and status line.
- **Prompt file versioning**: `compact-v0.md` is pinned to schema v0. If the
  schema changes to `v1`, a new `compact-v1.md` is created. Both may coexist
  during transition.
- **Blob format versioning**: the schema version is independent of the
  `localcmp:v2:` blob format version. The blob format version (v2, v3) tracks
  QuantZhai wire encoding. The schema version tracks the content structure.
  Stage 4 should embed `schema_version: "v0"` in `localcmp:v3:` blobs to
  allow migration detection.

---

## Related Documents

- `docs/compaction-audit-and-strategy.md` — Stage 0 planning doc; Codex audit
  findings; QuantZhai current behaviour; issue #8 promoted ideas.
- `config/default/prompts/compact-v0.md` — the compaction prompt file.
- `docs/compaction-codex-setup.md` — how to wire Codex to use the prompt file.
- `docs/fixtures/compaction/` — example input/output pairs for schema
  validation.
- `docs/compaction-bridge-plan.md` — localcmp:v1/v2 blob format and current
  heuristic compaction delivery.
