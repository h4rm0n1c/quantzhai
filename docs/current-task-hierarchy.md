# QuantZhai Current Task Hierarchy

Date: 2026-05-15
Status: active control sheet — post-VRAM/recovery/stream-watchdog stabilisation.

See `docs/current-stocktake.md` for the full point-in-time state summary.

This document turns the current planning docs into an execution order. It does
not replace the architecture contracts. If this file conflicts with
`docs/current-architecture-authority.md` or
`docs/codex-context-memory-contract.md`, those documents win.

## Current hard rules

```text
Use memory_domain, not profile_family.
Missing memory_domain means isolated.
Capability detection from tools never grants durable memory access.
QuantZhai-owned qz_* context stays internal and must not be injected into
forwarded /v1/responses request bodies.
Phase 1 SQLite stores parser-boundary identity/scoping facts only.
SQLite is not a telemetry warehouse, config authority, or memory_domain registry.
No clever memory, active memory tools, learned preferences, roleplay memory,
HSM/archive memory, automatic promotion, or cross-domain sharing in Phase 1.
```

## Recently completed (2026-05-15 run — VRAM/recovery/docs)

```text
VRAM telemetry (#6, now closed)
  - provenance-labelled component panel live in qz-top
  - MODEL_RUNTIME calibrated from process_used − KV_ALLOC
  - MODEL_FILE (GGUF size) retained as non-subtractive provenance
  - KV_ALLOC from QZ_CACHE_RAM (runtime budget) > GGUF formula > unknown
  - Quant registry with documented effective bytes/element (35+ types)
  - docs/patterns/provenance-telemetry.md: doctrine locked
  - AGENTS.md: telemetry/status doctrine added
  - #52 opened: upstream-blocked follow-up for allocator metrics

Backend control-plane and recovery (#44, #47-#50, #45, now closed)
  - /qz/control-plane is the live status authority
  - Full recovery trigger/plan/backoff/async-job API (six actions)
  - recovery_state.py: in-memory backoff/attempt counts (to be SQLite later)
  - #51 opened: SQLite persistence of recovery state (blocked by #2)
  - Legacy catalog fallback removed (#45)

Stream watchdog and signal planning (#40, #42, now closed)
  - qz_stream_terminal.py classifies stream terminal outcomes
  - qz_stream_watchdog.py detects no-output and terminal-after-output timeouts
  - qz-thoughts renders non-ok stream_terminal_classified rows
  - qz_feedback.py exists for bounded future signal adoption
  - Runtime signal migration is deferred until after #2 unless a concrete bug
    requires touching that path

Foundation audit before SQLite
  - docs/foundation-audit-before-sqlite.md maps gravity wells, signal paths,
    duplicated/underused signals, Codex feedback gaps, and #2 prerequisites
  - #2 may start as parser-boundary SQLite only; do not turn it into a broad
    runtime signal store or model-visible memory path
```

## Recently completed (2026-05-14 run — profiles/sandbox/smoke)

```text
qz.profiles.v1 active config + split default/example profiles (#26/PR#27)
  - config/default/profiles.json + profiles/*.json  (shipped defaults)
  - config/user/profiles.json + profiles/*.json     (local user config)
  - model-overrides.json preserved as legacy fallback per layer

memory_domain config plumbing (#23/PR#24, PR#25)
  - memory_domain read from profile overrides, stored on catalog entries
  - exposed in /v1/models, /qz/status, /qz/config/effective
  - memory.domain in qz.profiles.v1 maps to memory_domain internally
  - missing memory_domain resolves to isolated at request time
  - no inference from model/profile/client/tool names

Simplified reasoning-effort prompts (#29/PR#30)
  - short depth-only prompts; removed hard tool-call caps and cross-file mandates
  - high/xhigh preserve final-answer obligation

Sandbox/tool-failure telemetry and guidance (#28/PRs#31-34)
  - Slice 1: tool_escalation_requested on outgoing require_escalated calls
  - Slice 2: native tool-output classifier before normalization
    - tool_sandbox_denied  (Read-only file system)
    - tool_connection_failed  (Connection refused)
  - Slice 3: harness guidance in codex-core-qwenified.md
  - qz-thoughts renders denied/conn-fail/escalation activity rows

Live stack smoke test (#35/PR#36)
  - scripts/qz-live-smoke validates proxy health, config, qz-thoughts,
    unit guards, normal Codex path, and sandbox-denied telemetry end-to-end
```

## Dependency chain

```text
authority/docs cleanup (ongoing)
  -> [DONE] explicit memory_domain config plumbing
    -> [DONE] stream watchdog and foundation audit before SQLite
      -> optional/non-fatal Phase 1 SQLite storage substrate  ← current P1
      -> same-scope parser-boundary state facts, starting with repeated-read v1/v2
        -> rendered state packets / LimbiCore recall later
```

---

## P0: Authority and task cleanup (ongoing)

Goal: stop agents from re-planning old decisions or following stale language.

Tasks:

```text
1. Keep this file updated as the short task DAG.
2. Update docs/README.md when new active docs are added.
3. Update docs/progress-snapshot.md after major implementation passes.
4. Mark old profile_family language as historical when encountered.
5. Keep docs/current-architecture-authority.md as the final conflict resolver.
```

Acceptance:

```text
A new agent can read docs/README.md, current-architecture-authority.md,
codex-context-memory-contract.md, and this file, then know what to do next.
```

---

## P1: BrainCase memory tool API and storage substrate

Goal: design and build the tool-mediated memory plane above BrainCaseDB.
See `docs/braincase-memory-tool-api.md` for the architecture and slice plan.

The goal is a superhuman memory console:

```text
LLM thinks -> uses memory tools -> helpers accelerate/constrain mechanics
-> storage/indexes return exact evidence -> LLM reasons again
```

Not automatic request logging. Not a DB-of-everything.

Current slice status:

```text
Slice 1 landed: optional/non-fatal SQLite state/memory storage substrate skeleton only.
Module: proxy/qz_braincase_db.py
BrainCaseDB is the low-level storage case — not a policy layer.
It stores parser-boundary identity/scoping facts, not runtime telemetry.
Env: QZ_STATE_DB_ENABLED, QZ_STATE_DB_PATH
Default: disabled; enabling is explicit via QZ_STATE_DB_ENABLED.
Schema: version metadata only, PRAGMA user_version = 1.
No parser facts, runtime signal history, stream telemetry, recovery/backoff
state, or model-visible memory are persisted yet.

#2 PARKED — waiting for StateRecord / memory-write API design.
Next implementation slice is NOT automatic parser-fact ingestion.
BrainCaseDB must not store data merely because QuantZhai observed it.
All write paths must be explicit (StateRecord, promotion, user-approved save,
or provenance attached to an actual stored memory/state record).
Do not proceed with sessions/turns/requests tables until the explicit
memory/state write API design is settled.
```

Scope:

```text
Optional/non-fatal DB open.
Parser-boundary only.
Consume extract_codex_request_context().
Store parser-boundary scoping facts and summaries, not giant raw request bodies.
DB write failure logs/telemeters but does not break proxy responses.
Follow docs/foundation-audit-before-sqlite.md.
```

Memory-domain authority:

```text
memory_domain definitions stay in config/profile policy.
SQLite may later record which configured memory_domain applied to a stored fact.
SQLite is not the memory_domain registry or policy authority.
SQLite must not infer or create domains.
```

Substrate file:

```text
proxy/qz_braincase_db.py
```

Likely tests:

```text
tests/test_qz_state_db.py
tests/test_qz_request_state_integration.py
```

Phase 1 tables:

```text
sessions
turns
requests
workspace_candidates
resolved_workspaces
session_workspace_bindings
identity_conflicts
```

Must not implement:

```text
model-visible durable memory
learned global preferences
roleplay/profile-private memory
HSM/archive memory
automatic promotion
cross-domain sharing
repeated-read v2 persistence
forwarded qz_* request-body metadata injection
```

Current acceptance (slice 1 only):

```text
test_qz_braincase_db.py: 11 tests pass
BrainCaseDB skeleton exists, disabled by default, schema metadata initialised.
No automatic ingestion.
```

Slice A acceptance (COMPLETE):

```text
docs/schemas/braincase/source-ref.schema.json    — SourceRef schema
docs/schemas/braincase/state-record.schema.json  — StateRecord schema (memory_domain=string, no enum)
docs/schemas/braincase/render-packet.schema.json — RenderPacket schema
docs/fixtures/braincase/source-refs/             — 4 source ref fixtures
docs/fixtures/braincase/state-records/           — 7 state records (all mandatory tiers covered)
docs/fixtures/braincase/render-packets/          — 1 render packet fixture
tests/test_braincase_schema_fixtures.py          — 44 tests, all passing
```

Slice B acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — schema v2, 5 new tables, 7 new methods
tests/test_qz_braincase_db.py — BrainCaseDBSliceBTests: 33 new tests (44 total)
All 1645 tests passing.
```

Blocked by for Slice C:

```text
Slice B is complete. Slice C (braincase.search + inspect) may now start.
FTS index design to be settled in Slice C.
No model-facing tools yet.
```

Best resource:

```text
docs/braincase-memory-tool-api.md
docs/model-state-signal-contract.md
```

---

## P2: Repeated-read signal

Goal: reduce wasted tool calls from redundant file reads without suppressing
legitimate re-reads.

Current rule:

```text
Implement repeated-read v1 as advisory, stateless, and input-history-seeded.
Do not implement persistent v2 until Phase 1 SQLite scope is available.
```

V1 likely files:

```text
proxy/qz_file_signal.py
proxy/qz_tool_lifecycle.py
proxy/qz_proxy_tools.py
proxy/qz_responses_stream.py
proxy/qz_request_router.py
tests/test_qz_file_signal.py
tests/test_qz_proxy_tools.py
```

V1 behaviour:

```text
Seed read/write state from body["input"] function_call/function_call_output items.
Detect conservative read commands such as cat/head/tail/sed/nl/rg/grep/wc.
Do not scan normal message text.
Do not parse ls/find as file reads.
Signal once per path per request/run.
After a warning in the same run, allow the next repeat through.
Suppress warning after a write to that path.
```

V2 behaviour:

```text
Use same-scope file read/write/signal facts from SQLite.
Scope by qz_session_id, qz_turn_id/codex_turn_id, qz_request_id, workspace_id,
and memory_domain.
Never cross workspace or memory_domain.
```

Blocked by:

```text
V1: not blocked. Start any time; integration is cleaner after P1 SQLite.
V2: blocked by P1 SQLite substrate and scope queries.
```

Best resource:

```text
DeepSeek can draft parser tests. Codex/Claude should do integration.
```

---

## P3: Telemetry filter ergonomics / qz-live-smoke refinements

Goal: reduce friction when diagnosing tool-failure events in a noisy telemetry
stream.

Scope:

```text
Optional /qz/telemetry/recent?type=tool_sandbox_denied query parameter.
Optional /qz/telemetry/recent?types=A,B filter for multiple event types.
qz-live-smoke --model flag default confirmation.
Consider a per-request telemetry endpoint (/qz/telemetry/request?request_id=...).
```

Blocked by:

```text
Not blocked. Low priority; implement when the noisy-window problem recurs.
```

---

## P4: Config/var/script ownership cleanup

Goal: reduce second truths and script-owned policy.

Current problem:

```text
source defaults, examples, user overrides, generated Codex config, runtime state,
captures, logs, cache, model inventory, and profile symlinks are still too easy
to confuse.
```

Rules:

```text
Do not move model files, profile symlinks, or Codex-visible slugs casually.
Do not add new one-off shell scripts unless strongly justified.
Do not make generated Codex catalog files into routing authority.
Proxy policy remains the source of truth.
```

Likely tasks:

```text
1. Improve /qz/config/effective coverage.
2. Surface missing prompt files and override warnings in one place.
3. Make /qz/models/refresh regenerate the Codex catalog file too.
4. Move more generated output toward var/generated/ only after report coverage.
5. Thin qz-codex-common after proxy owns catalog generation.
```

Blocked by:

```text
Not strictly blocked, but avoid broad refactor before P1/P2 unless fixing a
specific live breakage.
```

Best resource:

```text
Codex/Claude, after tests are strong.
```

---

## P5: Observability polish — VRAM DONE; remaining items

VRAM telemetry (#6) and the stream/compaction hang watchdog (#40) are closed.
The VRAM panel is live and provenance-labelled. Stream terminal/no-output
classifications are live. See docs/patterns/provenance-telemetry.md for
telemetry doctrine and docs/runtime-observability-notes.md for stream/VRAM
runtime notes.

Remaining observability work:

```text
#52  backend allocator metrics (upstream-blocked; QuantZhai already wired)
first-status correctness tests
long-running TUI validation
profile prompt/config ownership review
fixed profile-eval prompt set in benchmark harness
```

Blocked by:

```text
#52: blocked by TurboQuant emitting allocator metrics; no QuantZhai action.
```

---

## P6: LimbiCore rendered state packets and future memory

Goal: eventually render small purpose-specific state packets or recall results
from scoped records.

Not now.

Future work includes:

```text
StateRecord envelope
rendered coding state packets
utility LLM proposal jobs
memory.search / memory.propose_write tools
roleplay specialised renderers
HSM evidence/provenance renderers
```

Blocked by:

```text
P1 memory_domain config
P2 SQLite substrate
explicit render policy
cross-domain isolation tests
```

---

## Next implementation prompt: BrainCase Slice C (braincase.search + inspect)

**Slices A and B are complete. Slice C may now start.**

Read `docs/braincase-memory-tool-api.md` before starting Slice C.

```text
Slice A: COMPLETE — schemas + fixtures + 44 tests
Slice B: COMPLETE — BrainCaseDB schema v2 + put/get/list/retire/supersede methods + 33 new tests
Slice C: NEXT — braincase.search + inspect over stored fixture records
Slice D: braincase.write/update with explicit tool path
Slice E: braincase.render bounded packet builder
Slice F: harness injection for memory tool-use policy
```

Slice C scope:
- Implement search helpers (query_plan, FTS, exact, tag).
- Implement inspect (fetch record + source_ref by ID).
- Add FTS5 virtual table for claim/summary/tags (if not already added).
- No model-facing tool yet — search/inspect are internal helpers only.
- No automatic ingestion.
- Tests against Slice B fixture records.

Full reference below for Slice C context:

```text
PARKED REFERENCE — do not implement until memory-write API design exists.

Previous slice 2 intent (for design reference only):
- consume extract_codex_request_context(); do not add another parser
- store sessions, turns, requests, workspace candidates, resolved workspaces,
  session workspace bindings, and identity conflicts as provenance/scoping
  references attached to explicit memory/state records — not as a request log
- store structured metadata/digests/summaries, not giant raw request bodies
- DB open/write failures must not break proxy request handling
- record which configured memory_domain applied to stored facts, but do not
  infer/create domains or treat SQLite as the memory_domain registry
- do not change model-visible behaviour
- do not persist broad runtime signal history, stream telemetry, or recovery
  backoff state
- do not implement learned preferences, durable memory, profile-private memory,
  HSM/archive memory, promotion, recall, renderers, or repeated-read v2

Add tests for inserts, workspace resolution, unknown workspace, identity
conflict storage, request-body non-mutation, and non-fatal DB failure.

Keep the patch boring.
```

## Maintenance rule

When a task changes direction, update this file in the same commit as the doc or
implementation change that caused it.

A stale task DAG is just a roadmap wearing novelty glasses.
