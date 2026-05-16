# BrainCase Memory Tool API

Date: 2026-05-15

Status: design/doctrine doc. No implementation yet.

---

## Purpose

Define the architecture for a tool-mediated memory plane above BrainCaseDB.
This is not a schema design or implementation guide. It is the conceptual spine
that prevents future implementation from collapsing into a DB-of-everything.

---

## Core doctrine

```text
The LLM is the memory operator.
BrainCaseDB is the storage case.
The memory tool API gives the LLM fast exact access to structured memory,
source indexes, and artifacts.
Deterministic helpers accelerate routing, search, validation, dedup,
conflict surfacing, and rendering.
The harness teaches when to use each tool.
memory_domain defines isolation.
Memory tiers define purpose.
Renderers define what becomes model-visible.

Do not store context because it happened.
Store/update memory only through explicit memory tool actions or future
memory-write paths.
SQL is an implementation accelerator behind tools, not the architecture.
```

---

## What changed in the architecture

Earlier design notes described a "proposal to gatekeeper" flow:

```text
LLM proposes -> deterministic judge decides -> DB stores
```

That framing is too single-tiered and implies the deterministic layer is the
final philosophical judge of what is worth remembering. It is not.

The corrected framing:

```text
LLM thinks
  -> uses memory tools (recall, search, inspect, write, update, render)
  -> helpers accelerate/constrain mechanics
     (scope routing, dedup, conflict surfacing, provenance linking, render packing)
  -> storage/indexes return exact evidence
  -> LLM reasons again
```

The LLM still performs much of the cognitive work through tool use and
reasoning. Helpers provide speed, exactness, scope bounds, dedup hints,
conflict surfacing, and provenance linkage — not final judgement.

---

## The memory system is not BrainCaseDB alone

BrainCaseDB is one layer in a larger system:

```text
LLM + harness
  -> memory tools (explicit API surface)
  -> deterministic helpers (scope, route, search, validate, pack)
  -> BrainCaseDB / source indexes / artifact refs
  -> renderers
  -> scoped model-visible memory packets
```

BrainCaseDB is a storage case. It does not decide policy. It does not invent
memory domains. It does not make anything model-visible on its own.

The render layer decides what the LLM sees. Until rendered, stored records are
internal state only.

---

## High-level loop

A complete memory interaction cycle:

```text
1. Task arrives. Harness signals: use recall if task continuity matters.
2. LLM calls braincase.recall or braincase.search.
3. helpers: scope_resolve (memory_domain + workspace), query_plan (exact/FTS/SQL/grep).
4. BrainCaseDB / indexes return matching records.
5. LLM calls braincase.inspect on likely hits.
6. LLM reasons with evidence.
7. If durable insight: LLM calls braincase.write or braincase.update.
8. helpers: dedup_check, conflict_check, source_link, retention_hint.
9. BrainCaseDB stores the explicit state/memory record.
10. If model-visible output needed: LLM calls braincase.render.
11. helpers: render_pack, redaction_check.
12. Renderer produces bounded model-facing packet.
```

Key constraints at step 9: the store only fires because the LLM called
braincase.write. It does not fire automatically for every request, turn,
session, or telemetry event observed.

---

## LLM-facing memory tool plane

Six tools form the minimal surface. None are implemented yet.

### braincase.recall

Purpose: broad scoped recall by tier or purpose.
Use when: task continuity matters; LLM needs recent decisions, open loops, or project state.
Do not use for: known exact phrases, paths, or source evidence (use search instead).
Input sketch: `{scope, tiers, purpose, query, limit}`
Output sketch: `[{record_id, tier, summary, source_ref, confidence, age}]`
Helpers behind it: scope_resolve, tier_route, render_pack.
Safety: scope must be explicit; no cross-domain bleed by default.

### braincase.search

Purpose: exact/structured search over memory and indexes.
Modes: exact, fts, tag, field, timeline, source_grep.
Use when: looking for a known phrase, file path, issue number, command, or prior exact wording.
Do not use for: vague "what was happening" questions (use recall instead).
Input sketch: `{scope, mode, query, fields, filters, limit}`
Output sketch: `[{record_id, excerpt, match_location, tier, confidence}]`
Helpers behind it: scope_resolve, query_plan, exact_match / FTS / SQL / grep adapters.
Safety: query_plan must not alter the requested memory_domain.
Superpower note: a targeted FTS/grep search is often faster and more accurate
than biological-style recall. Prefer it when exact evidence matters.

### braincase.inspect

Purpose: fetch full content of selected records or source refs after search.
Use when: braincase.search returned likely hits and full detail is needed.
Do not use for: fishing — inspect after search narrows the candidate list.
Input sketch: `{record_ids, include_source_ref, include_derived_from}`
Output sketch: `[{record_id, kind, scope, body, source, provenance, visibility}]`
Helpers behind it: source_link.
Safety: visibility check — internal records are not returned unless caller has scope.

### braincase.write

Purpose: explicit write of a durable memory/state record.
Use when: user gives a stable instruction; a durable project decision is made;
a reusable procedure is established; a fact needs anchoring.
Do not use for: ordinary conversation, transient tool results, raw log lines,
or every request observation.
Input sketch: `{scope, kind, tier, body, source_ref, confidence, importance, lifetime}`
Output sketch: `{record_id, status, dedup_hint, conflict_refs}`
Helpers behind it: scope_resolve, dedup_check, conflict_check, source_link, retention_hint.
Important: write is not "LLM proposes and waits for approval". The LLM writes
through explicit tool paths with deterministic bounds/scoping/provenance helpers.
The point is explicit intent, not gatekeeping.

### braincase.update

Purpose: correct, supersede, retire, or link existing records.
Use when: current context corrects or supersedes old memory; a record is stale or contradicted.
Input sketch: `{record_id, operation, patch, reason, derived_from}`
Operations: correct, supersede, retire, link.
Output sketch: `{record_id, previous_id, status}`
Helpers behind it: conflict_check, source_link, retention_hint.

### braincase.render

Purpose: produce a bounded model-facing memory packet.
Use when: summarised context for a specific purpose must be surfaced to the model.
Do not use for: dumping raw storage — render is scoped and bounded.
Input sketch: `{scope, purpose, tiers, format, token_budget}`
Output sketch: `{packet, source_records, token_count, redacted_count}`
Helpers behind it: render_pack, redaction_check.
Safety: renderers enforce scope; cross-domain content is never included.

---

## Harness/tool-use policy

The harness teaches the LLM when to use each tool. Draft policy:

```text
Use recall when task continuity matters: open loops, recent decisions, project state.
Use search when exact prior wording, issue state, command, path, or source evidence matters.
Use inspect after search returns likely hits — narrow before fetching full content.
Use write when:
  - the user gives a stable instruction
  - a durable project decision is made
  - a reusable procedure is established
  - a fact needs explicit anchoring with provenance
Use update when current context corrects or supersedes old memory.
Do not write ordinary chatter, transient telemetry, raw logs, or every tool result.
Prefer narrow exact search before broad recall when looking for known phrases,
paths, or issue numbers.
```

The superpower this unlocks: targeted SQL/FTS/grep search can be faster and
more accurate than biological-style recall. The LLM should be encouraged to
think, search, inspect, then think again — not to recall everything and filter
in-context.

---

## Deterministic helper / accelerator layer

Helpers are accelerators and invariants, not policy monarchs or philosophical
judges. The LLM still reasons; helpers handle mechanics.

### scope_resolve

What it accelerates: resolves configured memory_domain + workspace/project scope from request context.
What it must not decide: whether a record is worth keeping.
Invariant: must not invent or infer memory_domain values; may return isolated/no-scope.

### tier_route

What it accelerates: routes a recall/search request to the correct memory tier(s).
What it must not decide: cross-domain access.
Invariant: tier boundaries respect memory_domain isolation.

### query_plan

What it accelerates: chooses exact/FTS/SQL/grep path based on query shape and available indexes.
What it must not decide: alter the user's requested memory_domain.
Invariant: query strategy must not change the scope.

### dedup_check

What it accelerates: detects near-duplicate records before write.
What it must not decide: whether the user intends a correction vs a new record.
Returns: similarity score, candidate dedup targets.

### conflict_check

What it accelerates: surfaces contradictions between candidate record and existing records.
What it must not decide: which version is correct.
Returns: conflict refs, contradiction summary.

### source_link

What it accelerates: attaches provenance to stored records (request_id, turn_id, artifact ref, capture path).
What it must not decide: whether a record is valid.
Invariant: provenance links are nullable; missing provenance does not block storage.

### render_pack

What it accelerates: assembles bounded model-facing packet from selected records.
What it must not decide: which records are semantically important (that's LLM or importance scores).
Invariant: token budget is enforced; cross-domain content is excluded.

### retention_hint

What it accelerates: suggests lifetime/tier based on record kind and importance.
What it must not decide: final lifetime — that is the LLM's explicit input.

### redaction_check

What it accelerates: flags records with visibility=never_model_visible before render.
What it must not decide: whether to store the record in the first place.
Invariant: never_model_visible records do not appear in render output.

### exact_match / FTS / SQL / grep adapters

Implementation accelerators behind braincase.search. Not tools in themselves.
The query_plan helper selects the adapter; the LLM sees only the search result.

---

## BrainCaseDB storage role

BrainCaseDB is storage and indexes. Its role is bounded:

```text
Stores state/memory records (explicit write paths only).
Stores source refs and provenance links.
Stores record revisions and supersession chains.
May store search indexes (FTS, tag indexes) later.
May store tier metadata and retention hints later.
```

What BrainCaseDB must never become:

```text
Automatic request/session/turn log.
Telemetry warehouse.
Runtime event store.
memory_domain registry or policy authority.
Recovery/backoff state store (that is #51, after BrainCaseDB write paths exist).
```

Sessions, turns, and requests may appear in BrainCaseDB only as source refs
or provenance attached to actual stored memory/state records. They must not be
logged automatically for every observed request.

This is aligned with AGENTS.md BrainCaseDB / Memory Storage Doctrine and the
#2 parked status.

---

## Memory domains vs memory tiers

### memory_domain — isolation boundary, config-owned

memory_domain is an explicit configuration-owned isolation boundary. BrainCaseDB
records the configured value only. BrainCaseDB must not infer, create, normalize,
grant, or authorize memory_domain values.

Examples:

```text
coding      — coding-agent operational state and project knowledge
hsm         — Human State Machine / archive provenance and evidence
roleplay    — private roleplay/fiction state, must not bleed into other domains
personal    — personal preferences and stable user facts
utility     — bounded utility LLM job outputs
isolated    — default when no memory_domain is configured
```

Missing memory_domain means isolated. No cross-domain access by default.

### Memory tiers — purpose within a domain

Memory tiers are nested within a memory_domain. They define purpose, retention
style, and default visibility. They are not separate isolation boundaries.

| Tier | Purpose | Retention | Default visible | Likely storage |
|---|---|---|---|---|
| working_state | recent topics, current task, open loops | recency-heavy, short-lived | bounded packet | in-memory or BrainCaseDB ephemeral |
| session_state | per-session facts and decisions | session-scoped | on request | BrainCaseDB session-lifetime records |
| project_state | durable project decisions, procedures, constraints | long-lived | on request | BrainCaseDB project-lifetime records |
| semantic_memory | generalised knowledge about the domain/project | durable | search/recall | BrainCaseDB + FTS index |
| procedural_memory | reusable procedures, workflows, tool strategies | durable | search/recall | BrainCaseDB + tag index |
| episodic_memory | notable past events with context and provenance | durable | search/inspect | BrainCaseDB + timeline index |
| artifact_memory | files, commits, docs, captures, archive references | mostly refs and indexes | exact search / inspect | BrainCaseDB source refs + grep index |
| perceptual_index | signal summaries: repeated-read, tool patterns, errors | session/project | operator telemetry | BrainCaseDB or telemetry |
| preference_constraint_memory | stable user preferences, declared constraints | durable | bounded packet | BrainCaseDB project/durable |

Tier guidance:

- working_state is comparable to "recent discussion state" — small, fast, recency-weighted.
- artifact_memory is mostly references and indexes, retrieved through exact search or inspect, not dumped into context by default.
- semantic_memory and procedural_memory are the long-tail knowledge base — search before recall.
- episodic_memory is the provenance anchor for HSM-style evidence chains.

---

## Default recall path

When task continuity matters:

```text
1. braincase.recall(scope, tiers=[working_state, session_state], purpose="task_continuity")
2. tier_route selects appropriate tiers
3. BrainCaseDB returns records sorted by recency and importance
4. render_pack produces bounded summary
5. LLM receives packet and continues task
```

This does not trigger for every request. The harness policy determines when to call recall.

---

## Exact search / SQL / FTS / grep path

When a known phrase, path, issue number, or command must be found:

```text
1. braincase.search(scope, mode=fts|exact|source_grep, query="...")
2. query_plan selects adapter (FTS for natural language, exact for literals, grep for source files)
3. index returns ranked hits
4. LLM calls braincase.inspect(record_ids=[...]) on likely hits
5. LLM reasons with full evidence
```

SQL is only ever an implementation accelerator behind query_plan and the
storage adapters. It is not the API surface the LLM touches.

---

## Source inspection path

After search narrows to likely hits:

```text
1. braincase.inspect(record_ids=[...], include_source_ref=true)
2. source_link resolves provenance: capture path, request_id, commit, artifact path
3. LLM receives full record body + source linkage
4. LLM reasons: is this the right evidence?
```

Source refs allow tracing any stored record back to its origin without storing
raw request bodies or full capture blobs in BrainCaseDB.

---

## Write/update path

When the LLM has a durable insight to store:

```text
1. LLM calls braincase.write({scope, kind, tier, body, source_ref, confidence, lifetime})
2. scope_resolve confirms memory_domain and workspace
3. dedup_check detects near-duplicates; returns hints, does not block
4. conflict_check surfaces contradictions; returns refs, does not block
5. source_link attaches provenance
6. BrainCaseDB stores the record
7. Returns {record_id, status, dedup_hint, conflict_refs}
```

For corrections and supersessions:

```text
1. LLM calls braincase.update({record_id, operation=supersede, patch, reason})
2. BrainCaseDB stores new revision, links to previous
3. Previous record is marked superseded
```

The write path is explicit by design. The LLM decides what is worth anchoring;
helpers constrain mechanics. This is not approval-gating.

---

## Render packet path

When the LLM needs to surface a bounded packet to itself or another model:

```text
1. LLM calls braincase.render({scope, purpose, tiers, token_budget})
2. render_pack selects and ranks records within scope and tiers
3. redaction_check filters never_model_visible records
4. Packet is assembled within token_budget
5. LLM receives bounded model-facing summary
```

Renderers are the critical boundary: raw stored records are never automatically
model-visible. They become visible only when explicitly rendered.

---

## HSM / LimbiCore mapping

LimbiCore is the broader memory and state interface. BrainCaseDB is one storage
layer beneath it. These are future conceptual layers — do not implement lobes
now. Do not name the low-level DB after lobes.

Conceptual lobe mapping:

```text
Perception
  Identifies candidate signals and facts from artifacts, conversations, and tool events.
  Feeds candidates to Judgement and Record paths.
  Does not automatically write to BrainCaseDB.

Recall
  Uses braincase.recall, braincase.search, and braincase.inspect.
  Returns evidence to the LLM for reasoning.

Judgement
  LLM + helpers reason about relevance, conflicts, and updates.
  Deterministic helpers (conflict_check, dedup_check) accelerate mechanics.
  The LLM remains the reasoner.

Expression
  Uses braincase.render to produce bounded model-facing packets.
  Renderers enforce scope, tier, and token budgets.

Homeostasis
  Manages decay, saturation, privacy constraints, and cross-domain boundaries.
  Likely implemented through retention_hint, redaction_check, and lifetime policies.
  Not implemented now.
```

HSM (Human State Machine / archive) may be represented as a configured
memory_domain value such as "hsm". It is not a separate storage system and is
not hard-coded into BrainCaseDB. HSM records use the same StateRecord envelope.
BrainCaseDB does not grant HSM any special authority — "hsm" is only an example
configured memory_domain value. HSM work requires explicit provenance, confidence
scoring, and import/export boundaries — it must not silently absorb coding or
session state. The memory_domain isolation suffices for HSM separation; no
separate BrainCaseDB instance is needed.

---

## Minimal future tool set

Summary table:

| Tool | Mode | When to use | When not to |
|---|---|---|---|
| braincase.recall | broad | task continuity, open loops, project state | known exact phrases/paths |
| braincase.search | exact/FTS/grep | known phrase, path, issue, command | vague "what was happening" |
| braincase.inspect | fetch | after search narrows candidates | blind fishing |
| braincase.write | explicit write | stable instruction, durable decision, reusable procedure | chatter, transient tool results, logs |
| braincase.update | correct/supersede/retire | current context corrects old memory | first-time writes |
| braincase.render | render packet | need bounded model-facing summary | raw storage dump |

---

## Non-goals

```text
Automatic request logging — do not store every request because it happened.
Automatic session/turn logging — sessions/turns are provenance refs, not logs.
Telemetry warehouse — telemetry stays in qz_telemetry.py and captures.
DB-of-everything — BrainCaseDB is not a journal.
Vector slop bucket — no embedding store without a specific retrieval design.
Single-tier recall — memory is tiered, not a flat prompt dump.
Deterministic gatekeeper as final judge — helpers accelerate, LLM reasons.
model-visible memory by default — storage is internal until rendered.
SQL-first design — SQL is behind helpers, not the API surface.
One-shot write-and-forget — records have provenance, revision, and lifetime.
Operational-fact persistence — recovery/backoff/runtime state is not BrainCaseDB.
```

**Signal-map boundary:** The bidirectional signal map
(`docs/codex-quantzhai-bidirectional-signal-map.md`) classifies signals as
"safe to observe" or "safe to store in telemetry/captures." Neither classification
means "store in BrainCaseDB." BrainCaseDB receives only explicit StateRecord writes.
Operational signals (session_id, turn_id, request_id) may appear in BrainCaseDB
only as SourceRef provenance attached to an actual stored StateRecord.

---

## First implementation slices

### Slice A: tool API design and schema fixtures — COMPLETE

Schemas and fixtures in `docs/schemas/braincase/` and `docs/fixtures/braincase/`.
Tests in `tests/test_braincase_schema_fixtures.py` (44 tests, all passing).

```text
docs/schemas/braincase/source-ref.schema.json    — SourceRef schema (JSON Schema Draft 7)
docs/schemas/braincase/state-record.schema.json  — StateRecord schema
docs/schemas/braincase/render-packet.schema.json — RenderPacket schema

docs/fixtures/braincase/source-refs/             — 4 source ref fixtures
docs/fixtures/braincase/state-records/           — 7 state record fixtures (all tiers covered)
docs/fixtures/braincase/render-packets/          — 1 render packet fixture

Key schema decisions:
- memory_domain is type: string with no enum (config-owned invariant)
- StateRecord.visibility: internal | renderable | never_model_visible
- Records internal until explicitly rendered
- Schemas do not define a memory_domain registry
- HSM fixture uses memory_domain="hsm" as a configured example, not a built-in domain
```

### Slice B: BrainCaseDB schema for state records — COMPLETE

```text
Tables added to proxy/qz_braincase_db.py:
  qz_braincase_source_refs         — SourceRef storage
  qz_braincase_state_records       — StateRecord storage (memory_domain stored as-is)
  qz_braincase_record_sources      — record <-> source_ref join table
  qz_braincase_record_revisions    — retire/supersede revision log
  qz_braincase_record_links        — record-to-record links

Schema version bumped: QZ_BRAINCASE_DB_SCHEMA_VERSION = 2

Methods added to BrainCaseDB:
  put_source_ref / get_source_ref
  put_state_record / get_state_record
  list_state_records(memory_domain, tier, limit)
  retire_state_record / supersede_state_record

Tests added to tests/test_qz_braincase_db.py:
  BrainCaseDBSliceBTests — 33 new tests (44 total in file)

Key invariants enforced by tests:
  memory_domain stored as-is; no enum; no registry table
  HSM fixture stored as plain string; no special HSM treatment
  Forbidden fields (raw_prompt, raw_request_body, etc.) rejected by put_* methods
  Input dicts never mutated
  Disabled DB returns False/None/[] without creating any file
  RenderPackets cannot be stored as StateRecords
```

### Slice C: braincase.search and inspect over fixture records — COMPLETE

```text
Added to proxy/qz_braincase_db.py:
  QZ_BRAINCASE_DB_SCHEMA_VERSION = 3 (FTS5 table added)
  fts_available property (bool; False if FTS5 unavailable)
  health() now includes fts_available
  qz_braincase_state_records_fts — FTS5 virtual table (optional, graceful fallback)
  put_state_record now syncs FTS index on write
  query_plan(query, mode="auto") -> dict  — routing: auto/exact/fts/tag
  search_state_records(query, *, memory_domain, tier, mode, limit) -> list[dict]
  inspect_state_records(record_ids, *, include_source_refs) -> list[dict]
  _search_exact, _search_fts, _search_by_tag, _rows_to_records helpers

Search modes:
  exact  — LIKE on claim/summary/tags_json
  fts    — FTS5 MATCH (fallback to exact if FTS5 unavailable or query fails)
  tag    — exact JSON tag match on tags_json
  auto   — tag: prefix -> tag; quoted/path/issue-ref -> exact; else fts or exact

Inspect result shape:
  {"record_id": str, "record": dict|None, "source_refs": list, "error": str|None}

Tests: BrainCaseDBSliceCTests — 36 new tests (80 total in file), all passing
Full suite: 1681 tests passing

Not added: model-facing tools, HTTP routes, RenderPackets, automatic ingestion.
```

### Slice C.1: FTS reindex/backfill — COMPLETE

```text
Added to proxy/qz_braincase_db.py:
  rebuild_fts_index() -> bool
    Clears and repopulates qz_braincase_state_records_fts from stored records.
    Indexes already-stored StateRecords only. Not automatic ingestion.
    Returns False if disabled, FTS unavailable, or error.
    Idempotent. Catches exceptions, sets last_error on failure.

  _sync_fts_for_record(record_id, claim, summary, tags_text) -> None
    Shared helper used by put_state_record and rebuild_fts_index.

  _maybe_backfill_fts_index() -> None
    Called from init() after FTS is confirmed available.
    Auto-backfills if state_records has rows and FTS index is empty.
    Best-effort: failures are swallowed to preserve init() reliability.

  init() updated: calls _maybe_backfill_fts_index() after FTS is available.

Tests: BrainCaseDBSliceC1Tests — 12 new tests (92 total in file), all passing
Full suite: 1693 tests passing
```

### Slice D: braincase.write and update — COMPLETE

```text
New module: proxy/qz_braincase_write.py

Helpers (deterministic accelerators — not policy monarchs):
  scope_resolve()      — confirms memory_domain present and in allowed scope.
                         Must not infer/create/normalize/grant domain values.
  redaction_check()    — rejects forbidden raw fields before storage.
                         Aligned with BrainCaseDB._FORBIDDEN_RECORD_FIELDS.
  dedup_check()        — finds same normalized claim in same domain/tier.
                         v1 exact match; hint only — does not block.
  conflict_check()     — surfaces opposing constraint markers (crude substring, v1).
                         Hint only — does not block.
  source_link()        — stores supplied SourceRefs; reports missing refs as warnings.
                         Missing refs warn, not error.

Write/update entry points:
  braincase_write_state_record(db, record, *, source_refs, allowed_memory_domains, ...)
    -> {ok, record_id, stored, errors, warnings, dedup, conflicts, source_link}
    Flow: redaction -> scope -> source_link -> dedup (hint) -> conflict (hint) -> put_state_record
  braincase_update_state_record(db, record_id, operation, *, new_record, reason, ...)
    -> {ok, record_id, operation, stored, errors, warnings, new_record_id?}
    Supported: retire, supersede.
    Deferred (returns unsupported result): correct, link.

Tests: tests/test_qz_braincase_write.py — 51 tests, all passing
Full suite: 1744 tests passing

Not added: model-facing tools, HTTP routes, harness injection, RenderPackets, automatic ingestion.
```

### Slice E: braincase.render bounded packet builder — COMPLETE

```text
New module: proxy/qz_braincase_render.py

Functions:
  render_budget_chars(budget_tokens) -> int
    Conservative char budget: max(80, budget_tokens * 4). No tokenizer dep.
  make_render_packet_id(now_ms, purpose, memory_domain) -> str
    Deterministic packet ID.
  eligible_for_render(record, memory_domain, tiers) -> (bool, reason | None)
    Eligibility: status=active, visibility=renderable, domain match, tier match.
    Excludes: internal, never_model_visible, superseded, retired, candidate.
  render_record_line(record) -> str
    Formats one record: [tier/type] claim / Summary / Source: record_id.
    No metadata JSON, no forbidden fields.
  render_pack(records, *, purpose, memory_domain, budget_tokens, tiers, ...) -> dict
    Filters eligible records, ranks by importance desc / updated_at_ms desc,
    assembles bounded text, returns RenderPacket dict.
    Budget is hard: rendered_text is always <= render_budget_chars(budget_tokens).
    Records that cannot fit (even truncated) are omitted; omitted_count incremented.
    Adds budget_exhausted warning and increments omitted_count when records skip.
  braincase_render_packet(db, *, purpose, memory_domain, query, tiers, record_ids, ...) -> dict
    Retrieves records from DB (by record_ids, query, or list), calls render_pack.
    Returns warning packet for missing domain or disabled DB.

RenderPacket shape (matches docs/schemas/braincase/render-packet.schema.json):
  {packet_id, schema="braincase/render-packet@1", purpose, memory_domain,
   generated_at_ms, budget_tokens, rendered_text, source_record_ids,
   omitted_count, warnings, metadata}

Eligibility key point:
  All Slice A fixtures use visibility="internal" and are NOT rendered by default.
  Tests create in-memory copies with visibility="renderable". Fixture files unchanged.

Tests: tests/test_qz_braincase_render.py — 53 tests, all passing
Full suite: 1819 tests passing

Not added: model-facing tool wiring, HTTP routes, harness injection, prompt injection,
automatic ingestion, recall tool.
```

### Slice F: braincase.render tool surface — COMPLETE

```text
New module: proxy/qz_braincase_tools.py

Feature flag: QZ_BRAINCASE_TOOLS_ENABLED (default: disabled)
When enabled:
  - braincase.render and braincase.recall injected into body["tools"].
  - BRAINCASE_HARNESS_POLICY added to turn harness.
  - Executors dispatch to braincase_render_packet() / braincase_recall_packet().
  - Disabled DB returns safe warning packets.

Exposed at Slice F: braincase.render only.
Not exposed: braincase.recall, write, update, search, inspect.

RenderPacket is the only model-visible memory output.
No automatic ingestion.

Tests: tests/test_qz_braincase_tools.py — 64 tests, 1906 total
```

### Slice G: braincase.recall semantics — COMPLETE

```text
Added to proxy/qz_braincase_tools.py:

RECALL_MODE_TIERS — bounded tier lists per recall mode:
  task:       working_state, project_state, preference_constraint_memory, procedural_memory
  project:    project_state, preference_constraint_memory, procedural_memory, artifact_memory
  procedure:  procedural_memory, preference_constraint_memory
  artifact:   artifact_memory, episodic_memory
  open_loops: working_state, project_state

tiers_for_recall_mode(mode) -> list[str] | None
  Returns mode's tier list, or None for unknown modes.
  Unknown modes return None so callers return a safe warning packet
  rather than defaulting to all memory.

braincase_recall_packet(db, *, purpose, memory_domain, query, tiers,
                        recall_mode, budget_tokens, limit, now_ms) -> dict
  Internal entry point. Validates mode, resolves effective tiers (caller
  narrowing: intersection only, no widening), calls braincase_render_packet().
  Returns warning packet for unknown mode, empty intersection, or disabled DB.

Tier narrowing rules:
  - Caller-supplied tiers narrow the mode's default tiers (intersection).
  - Out-of-mode tiers are dropped silently; the intersection is used.
  - If intersection is empty (all caller tiers outside mode) → warning packet
    with "tier_not_allowed_for_mode". No silent fallback to mode defaults.
  - Caller tiers cannot widen beyond the mode's allowed tiers.

BRAINCASE_RECALL_TOOL_DEF — function tool definition:
  name: braincase.recall
  required: purpose, memory_domain
  optional: recall_mode (enum, default "task"), query, tiers, budget_tokens, limit
  description: says to use render for exact records, recall for scoped memory

braincase_recall_tool(db, args) -> dict — executor (never raises)

BRAINCASE_HARNESS_POLICY updated:
  Now covers both braincase.render and braincase.recall.
  render: use when exact record_ids or narrow query known.
  recall: use for scoped task/project memory; choose recall_mode.
  write/update/search/inspect: not yet exposed.

get_braincase_tool_definitions() now returns [render_def, recall_def] when enabled.

Exposed: braincase.render, braincase.recall.
Not exposed: braincase.write, braincase.update, braincase.search, braincase.inspect.

RenderPacket is the only model-visible memory output.
No raw StateRecords. No automatic ingestion.

Tests: tests/test_qz_braincase_tools.py — 124 tests, 1966 total
```

### Slice G.1: tier-bounded retrieval + deterministic enum order — COMPLETE

```text
Fixed in proxy/qz_braincase_tools.py:

_recall_candidate_records(db, *, memory_domain, query, effective_tiers, limit)
  New internal helper. Queries each effective tier separately (not all at once),
  deduplicates by record_id, ranks by importance/updated_at/created_at desc,
  returns at most limit records. Ensures in-mode records are never starved by
  out-of-mode records that happen to match a shared limit.

braincase_recall_packet() updated:
  - Now calls _recall_candidate_records() + render_pack() directly instead of
    delegating to braincase_render_packet(). No duplicate render logic.
  - DB disabled check moved before retrieval (returns braincase_db_disabled warning).
  - When caller-supplied tiers are partially out-of-mode (non-empty intersection):
    the intersection is used AND "tier_narrowing_dropped_out_of_mode" warning is
    appended to the returned packet (previously silently dropped).

RECALL_MODE_ORDER constant added:
  tuple(RECALL_MODE_TIERS.keys()) — stable insertion order.
BRAINCASE_RECALL_TOOL_DEF enum now uses list(RECALL_MODE_ORDER) instead of
  list(_VALID_RECALL_MODES) (frozenset — previously non-deterministic order).

Tests: tests/test_qz_braincase_tools.py — 141 tests, 1983 total
```

### Slice G.2: proxy-local tool dispatch — COMPLETE

```text
Added to proxy/qz_braincase_tools.py:

_BraincaseBaseExecutor — duck-typed base class (no inheritance from
  ProxyLocalToolExecutor to avoid circular imports with qz_proxy_tools.py).
  Implements is_call, started_public_item, _parse_args, _get_db, _make_result.
  _get_db() uses injected db or BrainCaseDB.from_env() at first call.
  _make_result() wraps a RenderPacket into a ToolContinuationResult with:
    public_item: function_call_output containing RenderPacket JSON
    upstream_items: (function_call, function_call_output) for continuation hop

BraincaseRenderProxyToolExecutor(function_name="braincase.render")
  lifecycle: proxy_local, continuation_hops=1, telemetry_name=braincase_render
BraincaseRecallProxyToolExecutor(function_name="braincase.recall")
  lifecycle: proxy_local, continuation_hops=1, telemetry_name=braincase_recall

make_braincase_tool_executors(db=None) → list
  Returns [] when QZ_BRAINCASE_TOOLS_ENABLED is not set (default).
  Returns [render_executor, recall_executor] when enabled.
  write/update/search/inspect never included.

Modified proxy/qz_proxy_tools.py:
  make_proxy_local_tool_registry(web_runtime, db=None) now calls
  make_braincase_tool_executors(db=db) when the flag is enabled.
  Existing web_search/apply_patch behaviour unchanged.

Feature flag: QZ_BRAINCASE_TOOLS_ENABLED (default: disabled).
DB disabled returns braincase_db_disabled warning packet, no exception.
No automatic ingestion. No raw StateRecords. No write/update/search/inspect.

function_call_output shape:
  {"type": "function_call_output", "call_id": ..., "output": "<RenderPacket JSON>"}

Tests: tests/test_qz_braincase_tools.py — 176 tests, 2018 total
```

### Slice G.3: dispatch test hardening — COMPLETE

```text
make_braincase_tool_executors(db=None, env=None) — env parameter added so
  tests call the real production function without patch.dict.
Module-level test shadow removed from test_qz_braincase_tools.py.
test_production_factory_not_shadowed() guards against re-shadowing.
Continuation regression tests: test_continuation_path_render_with_seeded_record
  and test_continuation_path_recall_with_seeded_record walk the full
  completed_call_decision → continuation_result path.

Tests: 179 tests, 2021 total
```

### Slice H: candidate-only write exposure design — COMPLETE

```text
Design only. No runtime exposure yet.

Proposed model-facing write tool: braincase.write_candidate
  NOT braincase.write — direct active/renderable writes are forbidden from
  the model-facing tool plane.

Rationale:
  Candidate-only writes allow the model to help curate memory without
  risking poisoned recall or self-authored doctrine. Active/durable memory
  still requires explicit operator review/promotion. This preserves the
  explicit memory discipline.

Forced policy (enforced by proxy, not by model):
  status     = "candidate"    always, regardless of model intent
  visibility = "internal"     always, regardless of model intent
  Candidate records are stored but not model-visible via render/recall.
  Existing render eligibility (status=active, visibility=renderable) already
  excludes candidates without any schema change.

Tool input schema (model-facing fields):
  Required:
    purpose           string    what this candidate record is for
    memory_domain     string    configured isolation domain (from session context)
    tier              string    one of the defined memory tiers
    record_type       string    semantic classification
    claim             string    durable assertion (maxLength 2000)
    summary           string    brief recall-readable summary (maxLength 1000)

  Optional:
    confidence        number    0.0–1.0, default 0.5
    importance        number    0.0–1.0, default 0.5
    retention         string    ephemeral|session|project|durable, default "project"
    tags              array     string tags for FTS/tag-index
    source_refs       array     source_ref_id strings (looked up from DB)
    why_it_matters    string    model's explanation for reviewer (maxLength 500)
    review_note       string    note for operator reviewer (maxLength 500)

  Forbidden (additionalProperties: false + executor defence):
    status            not a valid tool input field
    visibility        not a valid tool input field
    raw_prompt, raw_request_body, request_body,
    full_log, telemetry_event, stream_event

  If model supplies status/visibility:
    Primary behaviour: schema rejects them (additionalProperties:false →
      error, no storage). The write is rejected before any record is built.
    Defensive invariant: if execution reaches record construction despite
      hostile/malformed input, status and visibility are forced to
      "candidate" / "internal" before any DB write. This is a backstop
      that prevents poisoning — it is NOT a permissive acceptance path.
    There is no successful write with override-and-warning for these fields.

  claim / summary content policy:
    claim and summary must not contain raw prompt blobs, raw request/session
    logs, or tool-output bodies. Obvious raw log markers in claim or summary
    are a hard error, no storage. This is not a warning path.
    Future operator-only import tooling may allow vetted external content,
    but the model-facing tool does not.

WriteCandidateResult (not a RenderPacket):
  {
    "ok": bool,
    "stored": bool,
    "record_id": str | null,
    "status": "candidate",           always
    "visibility": "internal",        always
    "review_required": true,         always
    "warnings": list[str],
    "errors": list[str],
    "dedup_hint": "no_duplicates" | "possible_duplicate" | null,
    "conflict_hint": "no_conflicts"  | "possible_conflict"  | null
  }
  No raw StateRecord dump. No source blob. No prompt/request body.
  dedup_hint and conflict_hint are string codes, not raw DB dicts.

Execution helper path (future implementation):
  parse tool args
  → construct candidate StateRecord (force status=candidate, visibility=internal)
  → redaction_check()     blocks forbidden raw fields
  → scope_resolve()       blocks missing/invalid memory_domain
  → source_link()         links source_refs; missing refs are warnings only
  → dedup_check()         hint only, does not block
  → conflict_check()      hint only, does not block
  → braincase_write_state_record()
  → return WriteCandidateResult

Review/promotion path (future, not Slice H):
  Candidate → active/renderable requires explicit operator review.
  Future tooling: braincase.review_candidates, braincase.promote_candidate,
                  or CLI: qz-braincase-review
  Promotion must: verify memory_domain, re-run redaction_check,
    surface dedup/conflict warnings, optionally allow editing,
    set status=active, set visibility=renderable/internal by operator choice,
    record revision/provenance.
  Promotion is NOT accessible to the model in Slice H.

Feature flag:
  QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED (separate from QZ_BRAINCASE_TOOLS_ENABLED)
  Default disabled even when render/recall are enabled.
  Rationale: write exposure is higher-risk than read. Operators enabling
    render/recall for a session may not want to enable candidate writes.
    Separate explicit opt-in prevents accidental write exposure.

Candidate isolation:
  braincase.render and braincase.recall must exclude candidates.
  eligible_for_render() already requires status=active and visibility=renderable,
  so no schema change is needed to isolate candidates.
  A model cannot write a candidate then immediately recall it as memory.

memory_domain authority:
  memory_domain is config/caller-owned, not BrainCaseDB-owned.
  BrainCaseDB stores the supplied configured value as-is.
  BrainCaseDB does not define, infer, normalize, grant, or authorize
  memory_domain values. It has no memory_domain registry.
  scope_resolve() checks whether the supplied domain is within the
  caller-supplied allowed domain context. "Outside allowed domain" means
  outside the operator/proxy-configured allowed set, NOT "unknown to
  BrainCaseDB" — BrainCaseDB has no such registry.

Abuse/failure cases:
  missing memory_domain          → error, no storage
  memory_domain outside caller-supplied
  configured/allowed domain set  → error (scope_resolve), no storage
  missing claim/summary          → error, no storage
  claim/summary with raw log/prompt/session content
                                 → hard error (claim content check), no storage
  forbidden raw top-level fields → hard error (redaction_check), no storage
  model tries status=active or visibility=renderable
                                 → schema rejects (primary); defensive force
                                   to candidate/internal applies as backstop
                                   but is not an acceptance path
  duplicate candidate            → warning (dedup_hint), write still proceeds
  conflict with active record    → warning (conflict_hint), write still proceeds
  source_refs missing            → warning (source_link), write still proceeds
  DB disabled                    → error, no storage, no exception
  malformed JSON args            → error before execution
  model tries to store every turn → harness teaches: write_candidate is for
    durable facts only, not observations, transient results, or session logs

Fixtures:
  docs/fixtures/braincase/write-candidate/tool-input-valid.json
  docs/fixtures/braincase/write-candidate/result-valid.json
  docs/fixtures/braincase/write-candidate/tool-input-forbidden-active.json
  docs/fixtures/braincase/write-candidate/tool-input-forbidden-raw-prompt.json
  docs/fixtures/braincase/write-candidate/tool-input-forbidden-raw-log-in-claim.json  (Slice H.1)

Tests: tests/test_braincase_write_candidate_design.py — 41 structural tests (Slice H)
  updated to 55+ tests in Slice H.1
Full suite: 2062+ tests
```

### Slice H.1: candidate-write doctrine polished — COMPLETE

```text
Docs/fixtures/tests only. No runtime write tool implementation.

Fix 1 — Reject-first status/visibility policy:
  Clarified: model supplying status/visibility is REJECTED (ok=false, stored=false).
  Defensive force of candidate/internal is a backstop that fires before any
  DB write, but is not an acceptance path. No "successful write with override".
  Removed "status_overridden_to_candidate" / "visibility_overridden_to_internal"
  warning names — these implied a permissive path that does not exist.

Fix 2 — memory_domain authority:
  memory_domain is config/caller-owned. BrainCaseDB has no registry.
  "Outside allowed domain" means outside the caller-supplied configured set.
  scope_resolve() checks against caller-supplied allowed domains, not a DB list.
  Added memory_domain authority block to Abuse/failure cases.

Fix 3 — HSM wording:
  "HSM is a specific memory_domain" replaced with:
  "HSM may be represented as a configured memory_domain value such as 'hsm'".
  BrainCaseDB has no special HSM handling. "hsm" is only an example value.
  Open question 7 resolved.

Fix 4 — Raw log/prompt smuggling in claim/summary:
  "claim content policy warns" replaced with hard error doctrine.
  Obvious raw log/prompt markers in claim or summary → hard error, no storage.
  New fixture: tool-input-forbidden-raw-log-in-claim.json

Fix 5 — WriteCandidateResult clarifications:
  Confirmed not a RenderPacket. Not memory recall. No raw StateRecord dump.
  dedup_hint / conflict_hint remain string codes (not raw DB dicts).

Tests: tests/test_braincase_write_candidate_design.py updated.
  New test classes: RejectFirstPolicyTests, MemoryDomainAuthorityTests,
  HsmWordingTests, RawLogInClaimFixtureTests, WriteCandidateResultBoundsTests.
Full suite: 2089 tests
```

### Slice H.2: braincase.write_candidate runtime implementation — COMPLETE

```text
New in proxy/qz_braincase_tools.py:

QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED_ENV constant.
is_braincase_write_candidate_enabled(env) — True only when BOTH
  QZ_BRAINCASE_TOOLS_ENABLED and QZ_BRAINCASE_WRITE_CANDIDATE_ENABLED are set.
  Rationale: write is higher-risk than read; separate explicit opt-in.

BRAINCASE_WRITE_CANDIDATE_TOOL_DEF — function tool definition:
  name: braincase.write_candidate
  required: purpose, memory_domain, tier, record_type, claim, summary
  optional: confidence, importance, retention, tags, source_refs,
            why_it_matters, review_note
  forbidden by schema: status, visibility, raw_prompt, raw_request_body,
                       request_body, full_log, telemetry_event, stream_event
  additionalProperties: false

braincase_write_candidate_tool(db, args) -> WriteCandidateResult:
  1. Reject forbidden top-level fields (reject-first, error no storage).
  2. Validate required fields.
  3. Detect raw log/prompt markers in claim/summary (hard error no storage).
     Markers: raw_request_body, raw_prompt, User:, Assistant:, [Turn,
              tool_call, function_call, telemetry_event, stream_event
  4. Clamp/default confidence, importance, retention, tags, source_refs.
  5. Build bounded review metadata from purpose/why_it_matters/review_note.
  6. Construct StateRecord with FORCED status=candidate, visibility=internal.
  7. Call braincase_write_state_record() (existing helpers: redaction_check,
     scope_resolve, source_link, dedup_check, conflict_check).
  8. Return WriteCandidateResult (not RenderPacket).

WriteCandidateResult shape:
  ok, stored, record_id, status="candidate", visibility="internal",
  review_required=True, warnings, errors, dedup_hint, conflict_hint.
  No rendered_text, packet_id, raw StateRecord, raw SourceRefs.
  dedup_hint/conflict_hint are string codes, not raw DB dicts.

BraincaseWriteCandidateProxyToolExecutor:
  function_name: braincase.write_candidate
  lifecycle: proxy_local, continuation_hops=1

get_braincase_tool_definitions() now returns:
  []                         when QZ_BRAINCASE_TOOLS_ENABLED=false
  [render, recall]           when only QZ_BRAINCASE_TOOLS_ENABLED=true
  [render, recall, wc]       when both flags are true

get_braincase_harness_policy() now returns:
  None                       when QZ_BRAINCASE_TOOLS_ENABLED=false
  read-only policy           when only QZ_BRAINCASE_TOOLS_ENABLED=true
  read+write_candidate policy when both flags are true

make_braincase_tool_executors() now includes write_candidate executor
  only when both flags are enabled.

Candidate isolation:
  braincase.render and braincase.recall exclude candidates because
  eligible_for_render() requires status=active, visibility=renderable.
  No schema change needed.

Exposed (Slice H.2): braincase.render, braincase.recall, braincase.write_candidate.
Not exposed: braincase.write, braincase.update, braincase.search,
             braincase.inspect, braincase.promote_candidate.

New test file: tests/test_qz_braincase_write_candidate.py — 57 tests.
Full suite: 2146 tests.
```

---

## Open questions

```text
1. RESOLVED: Slice A fixtures live in docs/schemas/braincase/ and docs/fixtures/braincase/.
2. RESOLVED: braincase.recall returns RenderPacket only (no raw records). Slice G defines modes.
3. RESOLVED: recall and render are separate calls; render for exact IDs, recall for scoped mode.
4. What is the right lifetime for working_state — ephemeral in-memory, or short-lived BrainCaseDB rows?
5. RESOLVED: artifact_memory source refs use SourceRef with locator (file path, URL, commit hash,
   capture path as appropriate). See docs/fixtures/braincase/source-refs/ for examples.
6. RESOLVED: Harness injection mechanism: braincase policy text appended to harness_blocks
   in normalize_responses_input_for_qwen() (same as model-profile turn harnesses).
   Tool definition is injected into body["tools"] in the same function.
7. RESOLVED: HSM uses memory_domain isolation, not a separate BrainCaseDB instance.
   "hsm" is a configured memory_domain example value. BrainCaseDB has no special
   HSM handling. See Slice H.1 doctrine in the HSM/LimbiCore mapping section.
8. Should braincase.search expose SQL mode directly, or keep SQL fully behind query_plan?
9. What is the right conflict_check strategy — exact field match, semantic similarity, or LLM pass?
10. Does retention_hint need a decay function, or is lifetime explicit enough?
```
