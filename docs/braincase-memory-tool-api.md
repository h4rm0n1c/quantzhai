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

HSM (Human State Machine / archive) is a specific memory_domain, not a separate
storage system. HSM records use the same StateRecord envelope with
memory_domain=hsm. HSM requires explicit provenance, confidence scoring, and
import/export boundaries — it must not silently absorb coding or session state.

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
```

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

### Slice C: braincase.search and inspect over fixture records

After Slice B schema exists:

```text
Implement search helpers (query_plan, FTS, exact, tag).
Implement inspect (fetch record + source_ref).
Tests against Slice B fixture records.
No model-visible output yet.
```

### Slice D: braincase.write and update

After Slice C search works:

```text
Implement write tool path with scope_resolve, dedup_check, conflict_check, source_link.
Implement update (supersede, correct, retire, link).
Tests: explicit write round-trips, dedup detection, conflict surfacing.
Important: DB writes only through explicit tool path.
```

### Slice E: braincase.render bounded packet builder

After Slice D write path works:

```text
Implement render_pack and redaction_check.
Implement braincase.render tool.
Tests: bounded packet assembly, redaction enforcement, cross-domain exclusion.
```

### Slice F: harness injection for memory tool-use policy

After Slice E render works:

```text
Add memory tool-use policy to the harness/prompt stack.
Teach the LLM when to use recall vs search vs inspect vs write.
Tests: harness policy injection, tool-use guidance.
```

---

## Open questions

```text
1. RESOLVED: Slice A fixtures live in docs/schemas/braincase/ and docs/fixtures/braincase/.
2. Should braincase.recall return raw records or pre-rendered summaries?
3. Does render always require a separate call, or can recall/inspect render on request?
4. What is the right lifetime for working_state — ephemeral in-memory, or short-lived BrainCaseDB rows?
5. RESOLVED: artifact_memory source refs use SourceRef with locator (file path, URL, commit hash,
   capture path as appropriate). See docs/fixtures/braincase/source-refs/ for examples.
6. What is the harness injection mechanism — system prompt prefix, or injected tool response?
7. Does HSM work need a separate BrainCaseDB instance, or does memory_domain=hsm isolation suffice?
   (Current working assumption: memory_domain isolation suffices; use memory_domain="hsm".)
8. Should braincase.search expose SQL mode directly, or keep SQL fully behind query_plan?
9. What is the right conflict_check strategy — exact field match, semantic similarity, or LLM pass?
10. Does retention_hint need a decay function, or is lifetime explicit enough?
```
