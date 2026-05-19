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
BrainCaseDB stores explicit memory/state records only (not operational facts).
SQLite is not a telemetry warehouse, config authority, or memory_domain registry.
Sessions/turns/requests are SourceRef/provenance only when attached to a StateRecord;
they are not automatic operational logs.
No automatic ingestion. No clever memory. No cross-domain sharing.
```

## Recently completed (2026-05-19 run — #5/#57 close-out + #58 always-HTTP + #56 Slices B/B.1)

```text
#56 Slice B — path-helper abstraction (commit eff2555)
  - proxy/qz_paths.py: qz_root, qz_var_dir, model_inventory_path,
    codex_home_dir, codex_model_catalog_path, codex_config_path
  - Replaced inline generated artifact paths in 5 modules
  - 11 tests; py_compile/shell syntax PASS

#56 Slice B.1 — CODEX_HOME/server-path audit
  - Removed 3 stale CODEX_HOME overrides from server/proxy runtime code:
    qz_request_router.py:_refresh_codex_catalog(),
    qz_request_router.py:/qz/models/refresh,
    qz_control_plane.py:_codex_catalog_info()
  - All server Codex artifact paths now use qz_paths (QZ_VAR_DIR), not CODEX_HOME
  - 6 new tests for CODEX_HOME independence
  - 2592 tests PASS
```

## Recently completed (2026-05-19 run — #5/#57 close-out + #58 always-HTTP bootstrap)

```text
Config/var/script cleanup (#5, now closed)
  - /qz/config/effective: file metadata (mtime/size/sha256_12/hash_skipped)
  - prompt-file source labelling (referenced list, source_layers, referenced_by)
  - generated artifact staleness warnings:
      stale_model_inventory_cache, stale_codex_catalog, stale_codex_config
  - stale_against precision; close-out audit verdict: CLOSED
  - Follow-ups: #56 (path migration design), #57 (qz-codex thinning)

qz-codex remote bootstrap (#57, now closed; superseded by #58)
  - GET /qz/codex/client-config — Codex client bootstrap metadata
  - GET /qz/codex/model-catalog — generated catalog served to remote clients
  - QZ_CODEX_REMOTE=1 explicit launcher remote mode in qz-codex-common
  - Writes local CODEX_HOME catalog/config.toml atomically with TOML escaping
  - Co-located mode unchanged; no API key values written or printed
  - Verified Codex CLI 0.130.0 model_catalog_json is local-file-only

Always-HTTP qz-codex bootstrap (#58, now closed)
  - qz-codex always uses HTTP; QZ_CODEX_REMOTE removed as branch/gate
  - Server-local CODEX_HOME, config template copy, TOML provider parse removed
  - POST /qz/models/refresh removed from launcher (client is read-only)
  - qz-up recovery coupling removed; error messages bounded
  - CODEX_HOME default: $HOME/.qz-codex/codex-home
  - 28 focused tests; 2576 total; py_compile/shell syntax PASS

#37 stream seam Slices 2F + 2F.1 (paused, not closed)
  - stream_timeout_kind() combiner extracted to qz_stream_watchdog.py
  - 2566 tests total
```

## Recently completed (2026-05-18 run — BrainCase + repeated-read + #37)

```text
BrainCase memory tool API (#53, now closed)
  - render/recall/write_candidate tools (feature-flagged, default disabled)
  - Operator review CLI (qz-braincase-review), retention policy, prune --apply
  - BrainCaseDB is the first concrete LimbiCore technology
  - Slices A–I.1 complete; no automatic ingestion; 2465 tests

BrainCase retention/lifetime policy (#54, now closed)
  - Multi-axis policy matrix, pure evaluator, dry-run report, prune --apply
  - Slices A–D complete; operator-controlled, not automatic

Repeated-read v1 advisory signal (#3/#4/#43, now closed)
  - proxy/qz_file_signal.py: parser, state, RepeatedReadState
  - Integration: qz_proxy_tools.py, qz_responses_stream.py, qz_request_router.py
  - Live smoke: scripts/qz-smoke-repeated-read
  - Advisory, stateless, input-history-seeded; no BrainCase writes; no persistence

#37 stream seam Slices 1–2E.1 (paused, not closed)
  - StreamHopState (per-hop mutable state object)
  - StreamDecision (vocabulary dataclass, not yet broadly consumed)
  - 4 pure module-level decision helpers extracted
  - No decide_stream_event() exists; qz_responses_stream.py remains side-effect owner
  - 2465 tests; paused before delicate seams (tool lifecycle, terminal, watchdog)
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
      -> [DONE] BrainCaseDB + StateRecord/write/render slices A–F
      -> next: braincase.recall semantics / operator write exposure
        -> rendered state packets / LimbiCore recall (future)
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

Historical / superseded — do NOT implement as BrainCaseDB tables:

```text
The earlier "Phase 1 SQLite operational facts" plan listed:
  sessions, turns, requests, workspace_candidates,
  resolved_workspaces, session_workspace_bindings, identity_conflicts

This framing is superseded by BrainCase doctrine.
BrainCaseDB stores StateRecords and SourceRefs only.
If sessions/turns/requests ever appear in BrainCaseDB, they must be
SourceRef provenance attached to an actual stored StateRecord — never
as automatic session/request logs.
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
proxy/qz_braincase_db.py — schema v3, 5 new tables, 7 new methods
tests/test_qz_braincase_db.py — BrainCaseDBSliceBTests: 33 new tests (44 total)
All 1645 tests passing.
```

Slice C acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — schema v3, FTS5 table, query_plan/search/inspect helpers
tests/test_qz_braincase_db.py — BrainCaseDBSliceCTests: 36 new tests (80 total)
All 1681 tests passing.
```

Slice C.1 acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — rebuild_fts_index, _sync_fts_for_record, _maybe_backfill_fts_index
  init() auto-backfills FTS when state_records has rows and FTS is empty
tests/test_qz_braincase_db.py — BrainCaseDBSliceC1Tests: 12 new tests (92 total)
All 1693 tests passing.
```

Slice D acceptance (COMPLETE):

```text
proxy/qz_braincase_write.py — new module:
  scope_resolve, redaction_check, dedup_check, conflict_check, source_link
  braincase_write_state_record, braincase_update_state_record (retire + supersede)
tests/test_qz_braincase_write.py — 51 tests, all passing
Full suite: 1744 tests passing
```

Slice E acceptance (COMPLETE):

```text
proxy/qz_braincase_render.py — new module:
  render_budget_chars, make_render_packet_id, eligible_for_render,
  render_record_line, render_pack, braincase_render_packet
tests/test_qz_braincase_render.py — 53 tests, all passing
Full suite: 1819 tests passing
```

Blocked by for Slice F:

```text
Slice E is complete. Slice F (harness/tool exposure) may now start.
Renders exist internally; they are not yet wired to harness or model tools.
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
V1 COMPLETE (#3/#4/#43 closed).
  proxy/qz_file_signal.py — parser, state, RepeatedReadState
  Integration live in qz_proxy_tools.py, qz_responses_stream.py, qz_request_router.py
  Live smoke: scripts/qz-smoke-repeated-read
  Advisory, stateless, input-history-seeded. No BrainCase writes. No persistence.

Do not implement persistent v2 until SQLite substrate and session identity scope
are proven. V2 is blocked on #2 / session key design.
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

## #58 completed: always-HTTP qz-codex bootstrap (D2/D2.1/D3)

**CLOSED.** qz-codex now always uses HTTP bootstrap via `/qz/codex/client-config`.

Implementation (Slices D2/D2.1/D3):
- `QZ_CODEX_REMOTE` branching removed — HTTP is the only path
- Server-local `CODEX_HOME` removed; default is `$HOME/.qz-codex/codex-home`
- `config/example/codex-config.toml` copy removed from launcher
- TOML provider parse removed; provider comes from HTTP client-config
- `POST /qz/models/refresh` removed from launcher (client is read-only)
- qz-up reference removed from error messages
- Proxy-down and missing-catalog errors bounded with no traceback/qz-up
- Atomic writes, TOML escaping, no-secret guarantees preserved
- No #56 path migration mixed in

See `docs/edge-case-config-contract-plan.md` §qz-codex always-HTTP bootstrap design.

## #56 D-design complete: generated artifact path migration (Slices A–D-design)

**Slice A-design:** CLOSED. Inventory/plan complete. No path moves yet.

Key finding: After #58, qz-codex clients do NOT read server-local generated
paths. The `/qz/codex/model-catalog` endpoint is the stable boundary. This
makes server-side path migration safe — no client changes needed.

**Slice B (commit eff2555):** CLOSED. `proxy/qz_paths.py` added. Replaced
inline generated artifact paths with helpers. 11 tests. No physical moves.

**Slice B.1 (audit/polish):** CLOSED. Audited all server/proxy CODEX_HOME
usage after #58. Three fixes:
- `qz_request_router.py:_refresh_codex_catalog()` — removed CODEX_HOME override
- `qz_request_router.py:/qz/models/refresh` — removed CODEX_HOME override
- `qz_control_plane.py:_codex_catalog_info()` — replaced inline CODEX_HOME path
  with `codex_model_catalog_path()` from qz_paths
- Removed unused `_codex_home_dir` imports from qz_config_report.py,
  qz_control_plane.py, qz_request_router.py
- Added 6 tests for CODEX_HOME independence
- Updated control plane test for new behaviour
- 2592 tests PASS. py_compile/shell syntax PASS.

Doctrine confirmed: CODEX_HOME is client-local qz-codex state.
Server/proxy generated artifact paths come from qz_paths / QZ_VAR_DIR.
No physical file moves. No var/generated/ created.

**Slice C-design:** CLOSED. First migration target selected and compatibility
plan defined. See `docs/edge-case-config-contract-plan.md` §Slice C-design.

Key decisions:
- **Chosen target:** A1 (`var/model-inventory.json`) → `var/generated/model-inventory.json`
- Strategy: change one helper return value, all consumers follow
- No symlink shim, no old-path deletion, QZ_MODEL_INVENTORY_CACHE preserved
- A2/A3 migration deferred to Slice D-design (coupled artifacts)

**Slice C-impl (commit fb28945):** CLOSED. `model_inventory_path()` now returns
`qz_var_dir() / "generated" / "model-inventory.json"`. QZ_MODEL_INVENTORY_CACHE
override preserved. Staleness warnings and write_cache() follow the helper.
No A2/A3 changes. Full suite 2592 tests PASS.

**Slice C.1 (commit 3ec719e):** CLOSED. A1 migration audit/polish. Table separator
fix in current-stocktake.md. Stale docs updated. Two focused tests added
(generated path default, QZ_MODEL_INVENTORY_CACHE override).

**Slice D-design (this commit):** CLOSED. A2/A3 coupling audited. Chosen target: D2.

Key decisions:
- **A2 target:** `var/generated/codex/qwenzhai-models.json`
- **A3 target:** `var/generated/codex/config.toml`
- **Move together** — generate() writes A3 with A2's absolute path; splitting breaks consistency
- **New helper:** `codex_generated_dir()` → `qz_var_dir() / "generated" / "codex"`
- **Changed helpers:** `codex_model_catalog_path()` and `codex_config_path()` route through it
- **Kept deprecated:** `codex_home_dir()` and `codex_model_catalog_dir()` (unchanged return values)
- **No shim** — after #58, no client reads server A2/A3 paths
- **No old-path deletion** — `var/codex-home/` stays until user cleans up
- **No script changes** — qz-codex-common uses client-local CODEX_HOME, not server paths
- **Staleness names unchanged** — `stale_codex_catalog`, `stale_codex_config`

See `docs/edge-case-config-contract-plan.md` §Slice D-design for full analysis.

**Slice D-impl (this commit):** CLOSED. `codex_generated_dir()` added.
`codex_model_catalog_path()` → `var/generated/codex/qwenzhai-models.json`.
`codex_config_path()` → `var/generated/codex/config.toml`.
`codex_home_dir()` and `codex_model_catalog_dir()` deprecated (unchanged return values).
No shim. No old-path deletion. No qz-codex-common changes.
qz-doctor and config/example/codex-config.toml updated.
6 new path tests. All A2/A3 test fixtures updated. 2600 tests PASS.

**Slice D.1 (this commit):** CLOSED. A2/A3 migration audited. Stale paths fixed in
`docs/edge-case-config-contract-plan.md` (generated files list, current path map, contract
risks). Stale path in `scripts/qz-codex` exec error message fixed. 3 new tests confirm
`/qz/config/effective` reports new A2/A3 paths and old `codex-home` path not present.
2603 tests PASS.

**Slice E (this commit):** CLOSED. Audited runtime callers — none found.
`codex_home_dir()` and `codex_model_catalog_dir()` removed from `proxy/qz_paths.py`.
4 deprecated tests removed; 1 negative regression test added
(`test_no_deprecated_codex_home_helpers_exported`). 2600 tests PASS.

Next: #56 close-out audit — confirm acceptance criteria, then close.

## Next implementation prompt: #37 design micro-slice for next delicate stream seam

**BrainCase #53/#54 closed. Repeated-read v1 complete. #37 Slices 1–2E.1 complete.**

Next: design micro-slice for the next #37 seam. Do not code before defining:
- Which seam (tool lifecycle? terminal? watchdog?)
- Extraction boundary and purity rules
- Test coverage gaps to fill before extraction
- Acceptance criteria

Read `docs/stream-reducer-boundary-design.md` §9F for the candidate inventory
and risk rationale. Slices 2F+ must not start from code — start from design.

Alternative safe next: config/var/script cleanup (#5).

## Reference: BrainCase Slice completion history

**Slices A through I.1 are complete (#53 CLOSED). #54 CLOSED.**

Read `docs/braincase-memory-tool-api.md` for the full slice history.

```text
Slice A:   COMPLETE — schemas + fixtures + 44 tests
Slice B:   COMPLETE — BrainCaseDB schema v3 + put/get/list/retire/supersede
Slice C:   COMPLETE — query_plan/search/inspect + FTS5 (80 total)
Slice C.1: COMPLETE — rebuild_fts_index / FTS backfill (92 total)
Slice D:   COMPLETE — qz_braincase_write.py helpers + write/update paths (1744 total)
Slice D.1: COMPLETE — conflict marker detection tightened
Slice E:   COMPLETE — qz_braincase_render.py + render_pack/braincase_render_packet + 53 tests (1819 total)
Slice F:   COMPLETE — qz_braincase_tools.py + braincase.render tool surface + 64 tests (1906 total)
Slice G:   COMPLETE — braincase.recall semantics + tier routing + 124 tests (1966 total)
Slice G.1: COMPLETE — tier-bounded retrieval + deterministic enum + 141 tests (1983 total)
Slice G.2: COMPLETE — proxy-local dispatch for render+recall + 176 tests (2018 total)
Slice G.3: COMPLETE — dispatch test hardening + env param for factory (2021 total)
Slice H:   COMPLETE — candidate-only write exposure design (41 structural tests, 2062 total)
Slice H.1: COMPLETE — doctrine polished (68 design tests, 2089 total)
Slice H.2: COMPLETE — braincase.write_candidate runtime (57 runtime tests, 2146 total)
Slice H.3: COMPLETE — runtime polish: tier/record_type validation, case-insensitive markers (2164 total)
Slice H.4: COMPLETE — BrainCase smoke-test script scripts/qz-braincase-smoke (2183 total)
Slice I:   COMPLETE — operator review/promote CLI scripts/qz-braincase-review (2223 total)
Slice I.1: COMPLETE — status-filtered candidate listing; hidden candidates now surface (2239 total)
```

Slice G: what was done
- RECALL_MODE_TIERS dict: 5 predefined modes (task/project/procedure/artifact/open_loops)
- tiers_for_recall_mode() → returns bounded tier list or None for unknown modes
- BRAINCASE_RECALL_TOOL_DEF: recall_mode enum, required purpose/memory_domain
- braincase_recall_packet(): validates mode, resolves tiers (intersection-only narrowing),
  calls braincase_render_packet() — no raw records, no duplicate render logic
- braincase_recall_tool(): executor dispatching to braincase_recall_packet()
- BRAINCASE_HARNESS_POLICY: updated for both render and recall
- get_braincase_tool_definitions(): now returns [render_def, recall_def] when enabled
- Unknown mode → warning packet; empty tier intersection → warning packet; no fallback to all memory
- No automatic ingestion. No raw StateRecords. Disabled DB → safe warning.
- Tests: 124 tests in test_qz_braincase_tools.py, all passing

Slice H.4 adds the smoke-test script.

#54 BrainCase retention/lifetime policy — CLOSED after audit:
  Slice A: COMPLETE — policy matrix design + fixtures + 40 tests (2279 total)
  Slice B: COMPLETE — pure evaluator (2341 total)
  Slice B.1: COMPLETE — fail-closed rule matching (2346 total)
  Slice C: COMPLETE — retention-report + prune --dry-run (2372 total)
  Slice D: COMPLETE — prune --apply retire path (2404 total)
Do not expose braincase.write/update/search/inspect/promote_candidate directly.
No automatic ingestion at any step.

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
