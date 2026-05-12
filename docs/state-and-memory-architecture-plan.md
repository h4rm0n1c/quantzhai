# State and Memory Architecture Plan

Date: 2026-05-12

Status: planning document. Do not implement before the scope model, storage
roles, privacy boundaries, and migration path are reviewed.

This document generalises the persistence gap exposed by the repeated-read
signal work. QuantZhai does not only need a stateful runtime store. It needs a
**typed memory architecture**: different kinds of state/memory serving different
roles, with explicit scopes and promotion rules.

A database is only the substrate. The important design question is:

```text
What kind of memory is this, who may read it, how long does it live, and what
can it influence?
```

---

## 1. Why this exists

The repeated-read signal revealed a concrete gap:

```text
Manual-history path:
  Codex/local client sends prior items in body["input"].
  The proxy can reconstruct recent tool/file history from the request.

Stateful previous_response_id path:
  Client may send only incremental input plus previous_response_id.
  Server/proxy must resolve prior history.

Compaction path:
  Older messages, tool calls, tool results, and reasoning may be replaced by a
  compacted item. Prior function_call items may no longer be visible.
```

That makes repeated-read v1 possible with visible request history, but repeated-
read v2 and future convergence signals require durable state.

However, this is larger than repeated reads. QuantZhai also needs long-lived
state for:

```text
runtime truth
response chains
session identity
workspace facts
coding-agent skills/preferences
profile-private memory
roleplay/private isolation
HSM / Holstrom archive integration boundaries
telemetry and debugging
compaction-aware structured facts
```

Those are not the same kind of memory. Treating them as one global bag of facts
would be dangerous and wrong.

---

## 2. Core principle

```text
Every persisted fact must have a type, a scope, a retention policy, and an
allowed influence surface.
```

If that cannot be answered, the fact should not be persisted yet.

The database should answer:

```text
What happened?
Where did it happen?
Which profile/session/workspace did it belong to?
Who may use it later?
Can it influence model behaviour, or is it only diagnostic?
Can it be promoted to a wider scope?
Can it be exported to HSM/Holstrom work?
When should it expire?
```

---

## 3. Memory classes

### 3.1 Runtime state

Purpose:

```text
Proxy/backend/process truth.
```

Examples:

```text
active model/profile
backend context size
request ids
response ids
previous_response_id chain
session/thread headers
backend load state
restart-required flags
```

Allowed readers:

```text
proxy internals
/qz/status
qz-top
qz-thoughts
qz-doctor
```

Default model influence:

```text
None, unless deliberately surfaced as a runtime/status signal.
```

Storage direction:

```text
SQLite live truth; optional JSON compatibility exports.
```

---

### 3.2 Operational tool memory

Purpose:

```text
Make agents converge better during tool use.
```

Examples:

```text
file reads
file writes
repeated-read warnings
repeated directory probes
tool-call counts
backend-error vs task-error provenance
search quality observations
compaction boundaries
```

Allowed readers:

```text
same session/workspace/profile-family unless policy widens it
```

Default model influence:

```text
Allowed as small advisory signals inside the same permitted scope.
```

Storage direction:

```text
SQLite structured facts, not full transcripts by default.
```

---

### 3.3 Conversation/session memory

Purpose:

```text
Remember what happened inside one conversation/session or response chain.
```

Examples:

```text
response chain
function_call/function_call_output metadata
compaction events
session-local warnings
session-local decisions
```

Allowed readers:

```text
same session/response chain by default
```

Default model influence:

```text
Allowed only inside the same session/chain unless explicitly promoted.
```

Storage direction:

```text
SQLite metadata and structured facts. Full text only by explicit policy.
```

---

### 3.4 Workspace/project memory

Purpose:

```text
Remember project-specific facts for coding agents.
```

Examples:

```text
repo paths
build commands
test commands
known local conventions
project-specific pitfalls
project-specific tool usage patterns
```

Allowed readers:

```text
coding profiles operating in the same workspace/project
```

Default model influence:

```text
Allowed for coding-agent prompts/signals in that workspace.
```

Storage direction:

```text
SQLite for structured facts; optional generated project note files if useful.
```

---

### 3.5 Coding preference memory

Purpose:

```text
Share stable user preferences and coding-agent behaviours across coding
profiles.
```

Examples:

```text
Prefer Devuan/Debian/sysvinit commands.
Use DOCKER_BUILDKIT=1 by default.
Prefer pushd/popd build instructions.
Prefer small targeted patches.
Avoid hallucinated file names.
Treat repo source as truth.
```

Allowed readers:

```text
coding profile family only
```

Default model influence:

```text
Allowed for coding profiles after explicit promotion or trusted rule entry.
```

Storage direction:

```text
SQLite typed preferences/skills, possibly exported to generated prompt snippets.
```

Important:

```text
Do not infer global coding preferences from private/roleplay sessions.
Do not apply coding preferences to roleplay unless a profile explicitly opts in.
```

---

### 3.6 Profile-private memory

Purpose:

```text
Remember state that belongs only to one profile or persona.
```

Examples:

```text
roleplay continuity
character-specific preferences
private session facts
persona tone/cadence state
```

Allowed readers:

```text
originating profile/session only, unless explicitly exported or promoted
```

Default model influence:

```text
Allowed only for that profile/private scope.
```

Storage direction:

```text
Separate scope rows/tables in SQLite, or separate DB later if policy requires.
```

Hard rule:

```text
Private/roleplay/intimate facts must never bleed into coding sessions by default.
```

---

### 3.7 HSM / Holstrom / archive memory

Purpose:

```text
Support source-grounded archival, emulation, and human-state modelling work.
```

Examples:

```text
curated life artifacts
Holstrom transcripts/indexes
HSM candidate memories
source-grounded testimony
artifact metadata
confidence/provenance scores
```

Allowed readers:

```text
HSM/archive profiles and explicit import/export tools only
```

Default model influence:

```text
No influence on normal coding or private sessions unless deliberately imported
into that scope.
```

Storage direction:

```text
Possibly separate store later. QuantZhai should define connector/import/export
boundaries rather than silently absorbing everything.
```

Hard rule:

```text
HSM/Holstrom memory is not a trash compactor for all session data. It must be
curated, source-grounded, and scope-labelled.
```

---

### 3.8 Debug/capture memory

Purpose:

```text
Reproduce and debug proxy/client/backend behaviour.
```

Examples:

```text
raw request captures
raw SSE captures
latest-forwarded JSON
logs
benchmark artifacts
```

Allowed readers:

```text
humans, diagnostics, tests, replay tools
```

Default model influence:

```text
None. Captures are not memory just because they exist.
```

Storage direction:

```text
Keep raw artifacts as files. Optionally add SQLite indexes later.
```

---

## 4. Scope boundaries

Minimum scope dimensions:

```text
tenant_id
  local user/install identity. For now probably one local user.

profile_family
  coding, roleplay, research, hsm, admin, unknown.

profile_id
  Codex-visible profile/model slug or QuantZhai profile identity.

workspace_id
  repo/worktree/project identity for coding-agent state.

session_id
  client/proxy session id if present.

thread_id
  Codex/OpenAI thread id header if present and captured.

response_chain_id
  chain rooted at first response or previous_response_id lineage.

state_class
  runtime, operational_tool, session, workspace, coding_preference,
  profile_private, hsm_archive, debug_capture.

visibility
  session, profile_private, workspace, profile_family, global_coding,
  hsm_archive, explicit_export, diagnostic_only.
```

Default policy:

```text
No cross-scope read unless a policy explicitly allows it.
```

---

## 5. Bleed-prevention policy

### Forbidden by default

```text
roleplay/private/intimate session state -> coding session
coding project state -> unrelated repo/project
HSM/archive imports -> normal sessions without explicit import
raw captures/logs -> model memory
private profile facts -> global preference store
```

Concrete example:

```text
A roleplay session where the user had sex with a fictional character must not
influence a coding-agent session. It must not become a global user preference,
a coding style hint, or a reusable assistant memory. It belongs to the private
profile/session scope unless the user explicitly exports something from it.
```

### Allowed by default

```text
same-session operational tool facts -> same-session convergence signals
same-workspace coding facts -> coding profiles in that workspace
global coding preferences -> coding profiles only
admin/runtime state -> status/monitor tools
```

### Allowed only after promotion

```text
workspace coding lesson -> global coding skill
session observation -> profile preference
curated artifact -> HSM candidate memory
private/profile fact -> explicit export
```

Promotion must record:

```text
source_scope
target_scope
source_kind
target_kind
reason
created_ts
approved_by_user or approved_by_policy
confidence
redaction_state
```

---

## 6. Storage design

Start with one SQLite DB as the substrate:

```text
var/state/qz-state.sqlite
```

Enable:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

Do not start with many DB files. Use one database with strong logical scopes.
Split later only when retention/privacy/lifecycle requires it.

Possible future split:

```text
var/state/qz-runtime.sqlite      runtime/session/tool state
var/state/qz-memory.sqlite       preferences/skills/profile memory
var/state/qz-hsm.sqlite          only if HSM work should live inside QZ
var/cache/qz-cache.sqlite        disposable cache
```

But v1 of this architecture should prefer one DB and strict scope fields.

---

## 7. Minimal table families

This is a planning sketch, not final SQL.

```text
schema_migrations
  database versioning

scopes
  tenant/profile_family/profile_id/workspace/session visibility boundary

sessions
  session_id/thread_id/request source, linked to scope

responses
  response_id, previous_response_id, request_id, input mode, compaction flags

tool_calls / tool_outputs
  function calls and result metadata, not full giant output by default

file_reads / file_writes
  normalized path facts for convergence signals

signals
  repeated_read_signal, hop_budget_signal, future orientation/tool-loop signals

compaction_events
  boundary markers when visible history may have been replaced

preferences
  typed preferences with explicit scope

skills
  reusable procedures/instructions with explicit scope

promotion_events
  audit trail for cross-scope promotion

artifacts
  pointers to external/raw files, captures, HSM/Holstrom sources, not raw blobs by default
```

---

## 8. What should stay as files

Keep these as files for now:

```text
config/default/*.json
config/example/*.json
config/user/*.json
var/codex-home/config.toml
var/codex-home/model-catalogs/*.json
var/model-inventory.json until catalog ownership is fully settled
var/captures/*
var/logs/*
var/benchmarks/*
```

Reason:

```text
Config should stay human-editable.
Codex still expects generated catalog files.
Captures/logs/benchmarks are artifacts, not live memory.
Model inventory is generated state, but may remain JSON until proxy ownership is
fully settled.
```

Move these first if/when implementing persistence:

```text
response/session/request identity
previous_response_id chains
runtime truth currently duplicated in JSON
tool lifecycle facts
file read/write/signal facts
compaction boundaries
promoted coding preferences/skills
```

---

## 9. Relationship to repeated-read signal

Repeated-read v1 remains scoped and mostly stateless:

```text
seed from visible body["input"]
warn once per run
no persistent session cache
```

Repeated-read v2 depends on this architecture:

```text
query same-scope file_reads/file_writes/signals
handle previous_response_id/minimal-input mode
survive compaction hiding old function_call items
avoid cross-profile/state bleed
```

Repeated-read v2 must not query roleplay/private/HSM scopes from coding sessions.

---

## 10. Relationship to HSM and Holstrom work

QuantZhai may become a useful tool for extracting, scoring, and transforming
state for HSM/Holstrom-style projects, but it must not silently become the HSM
store.

Rules:

```text
HSM/Holstrom state requires explicit artifact provenance.
HSM/Holstrom state requires explicit import/export boundaries.
Normal QuantZhai session state is not automatically HSM source material.
Private/roleplay state is not automatically HSM source material.
Coding-agent operational state is not automatically HSM source material.
```

Possible future integration:

```text
QuantZhai can export selected, redacted, source-grounded memories to an HSM
candidate artifact queue.

QuantZhai can query an HSM store through an explicit connector/profile when the
user selects that mode.
```

Do not build that in the first DB layer.

---

## 11. Required research before implementation

Before database code, answer:

```text
1. Which headers/fields are available in real local Codex -> QuantZhai traffic?
   session_id, session-id, thread_id, thread-id, previous_response_id, etc.

2. Which current JSON files are live truth, compatibility exports, generated
   files, debug artifacts, or stale leftovers?

3. Which profile metadata can reliably identify profile_family?

4. How should workspace_id be derived for coding sessions?
   cwd, git root, repo URL, config override, or generated profile metadata?

5. What is the default retention for each memory class?

6. What is the promotion mechanism for global coding preferences/skills?

7. What tests prove private/roleplay memory cannot influence coding sessions?

8. What tests prove coding preferences can be safely shared across coding
   profiles when explicitly scoped as global_coding?
```

Suggested capture audit:

```bash
rg -n '"previous_response_id"|session_id|session-id|thread_id|thread-id|"function_call"|"local_shell_call"|"shell_call"|"compaction"' var/captures proxy tests docs
```

Suggested JSON ownership audit:

```bash
rg -n 'model-state\.json|backend-state\.json|qz-runtime-state\.json|model-inventory\.json|latest-.*\.json|var/captures|var/logs|var/benchmarks' .
```

---

## 12. Implementation phases

### Phase 0 — planning only

```text
Review this document.
Update repeated-read plan to point v2 at typed memory architecture.
Audit existing JSON state ownership.
Do not implement DB code yet.
```

### Phase 1 — minimal DB substrate

```text
Add proxy/qz_state_store.py.
Open var/state/qz-state.sqlite.
Enable WAL and foreign keys.
Add migrations and tests.
Persist only session/request/response identity at first.
Do not change model behaviour.
```

### Phase 2 — scopes and isolation

```text
Add scope records for coding/workspace/profile/private/admin.
Add tests proving cross-scope reads are denied by default.
Add tests for private/roleplay -> coding isolation.
```

### Phase 3 — operational tool memory

```text
Persist tool calls, file reads/writes, repeated-read signals, and compaction
boundaries.
Repeated-read v2 can then query same-scope durable facts.
```

### Phase 4 — runtime JSON migration

```text
Move selected live runtime truth from JSON into SQLite.
Keep compatibility JSON exports if needed.
Mark JSON exports as generated/fallback, not truth.
```

### Phase 5 — coding preferences/skills

```text
Add explicit promotion for global_coding preferences and skills.
Generate prompt snippets or model-visible signals only from approved scopes.
```

### Phase 6 — HSM/Holstrom boundary

```text
Design explicit import/export surfaces.
Do not silently merge HSM/archive memory into normal QuantZhai state.
```

---

## 13. Acceptance criteria

The architecture groundwork is acceptable when:

```text
1. Every stored fact has memory_class, scope, visibility, and retention policy.
2. Private/roleplay state cannot be read by coding profiles by default.
3. Coding workspace state is shared only inside the same workspace unless
   promoted.
4. Global coding preferences/skills require explicit promotion or trusted config.
5. HSM/Holstrom/archive memory has explicit import/export boundaries.
6. Runtime/admin state can feed monitors/status without becoming model memory.
7. Repeated-read v2 can use same-scope durable read/write/signal facts.
8. previous_response_id/minimal-input support has a clear state lookup path.
9. Compaction-hidden tool history can be represented as structured facts without
   reconstructing private transcripts.
10. Config files, generated Codex catalog files, captures, logs, and benchmark
    artifacts keep their existing contracts until explicitly migrated.
```

---

## 14. Open questions

```text
Should profile_family be declared in model overrides, inferred from profile
name, or stored in a separate profile registry?

Should private/roleplay memory live in the same SQLite database with hard scope
barriers, or a separate DB file for belt-and-braces isolation?

How does the user approve/promote a learned coding preference?

Can workspace_id be safely derived from git root, or should profiles provide it?

Should HSM/Holstrom integration live inside QuantZhai or as an explicit external
store/connector?

How long should operational tool memory live?

How much raw output should be stored versus digests/previews only?
```

---

## 15. Immediate next step

Ask an agent to review this document against the repo and produce a concrete
planning report:

```text
Read docs/state-and-memory-architecture-plan.md,
docs/repeated-read-dedup-plan.md, docs/master-stabilisation-plan.md,
docs/edge-case-config-contract-plan.md, proxy/qz_request_router.py,
proxy/qz_responses_stream.py, proxy/qz_telemetry.py, proxy/qz_runtime_io.py,
and all code touching var/*.json runtime files.

Do not implement.

Report:
- which memory classes are already implicitly present
- which JSON files are live truth vs generated/debug artifacts
- where scope metadata can be sourced today
- smallest safe DB substrate
- tests required for private/coding/HSM isolation
- migration order
- risks and deferred work
```
