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

## Active — model-selection authority cleanup (post #65)

```text
Goal: make the proxy the single authority for active model selection;
remove split authority across env / state files / qz-codex / catalog /
backend observations.  No new shell scripts for model selection.

Design: docs/proxy-model-selection-authority.md

Slice A-design — ✅ audit + final design
Slice B-state — ✅ qz.model_state.v1 in proxy/qz_model_state.py;
  load_last_selected_model no longer reads loaded_model;
  _persist_model_state routed through new module; 32 new tests;
  2988 total pass
Slice C-endpoints — ✅ /qz/model/status, /qz/model/select,
  /qz/model/reload, /qz/model/select-and-restart;
  proxy/qz_model_status.py builds qz.model_status.v1;
  ModelCatalog.refresh() now enforces precedence
  (persisted > QZ_MODEL_KEY seed > catalog default);
  /qz/control-plane exposes configured_env_model, selected_source,
  selected_loaded_mismatch, load-failure surface, operator hints;
  44 new tests; 3032 total pass
Slice D-qz-codex — ✅ qz_codex_exec_preflight in scripts/qz-codex-common;
  GET /qz/model/status, compare active model, mismatch error includes
  literal curl /qz/model/select-and-restart; QZ_CODEX_AUTO_SELECT_MODEL=1
  opts into select+restart with source=qz_codex; old visibility-only
  qz-wait-ready preflight removed; 17 new tests; 3049 total pass.
  Note: qz-codex no longer treats /v1/models visibility as active backend selection.
Slice E-load-failure — ✅ proxy/qz_model_load_failure.py classifier;
  BackendManager.fetch_recent_logs(); /qz/model/reload and
  /qz/model/select-and-restart run classifier post-load and update
  qz.model_state.v1 last_load_* fields; failed loads return HTTP 409
  with classified payload (insufficient_vram / context_creation_failed
  / unknown); selection authority preserved on failure; successful
  reload clears previous error; 22 new tests; 3071 total pass
Slice F-smoke — ✅ Cold-start smoke on real hardware (kuato good /
  27B Q5_K_M too-large) exercised every endpoint and surface; uncovered
  4 bugs (classifier false-positives on benign retry/user-override
  lines, stale last_load_* across restart, selected_source downgrade
  by legacy _persist_model_state, qz-codex exit-code propagation);
  all fixed with regression tests; 3081 total pass; results recorded
  in docs/proxy-model-selection-authority.md §16.

## Model-selection authority cleanup — COMPLETE

All slices (A-design, B-state, C-endpoints, D-qz-codex, E-load-failure,
F-smoke) closed.  No new shell scripts added (`scripts/qz-model*`
invariant intact).

### Post-F polish (2026-05-22)

- **Failed-model recovery**: qz.model_state.v1 gains last_good_* and
  failed_candidate_* observation fields; mark_load_success /
  mark_load_failure helpers; /qz/model/select-and-restart accepts
  rollback_on_failure (default true); /qz/model/status surface adds
  rollback_performed / recovery_available / recommended_recovery_action;
  control-plane operator hints for rollback and no-last-good;
  qz-codex auto-select failure prints classified failure block.
- **Codex catalog freshness**: /qz/codex/client-config exposes
  model_catalog.freshness {catalog_mtime_ms, source_mtime_ms,
  catalog_age_seconds, stale, reason, remediation} plus refresh_url;
  new POST /qz/codex/model-catalog/refresh (metadata only — no backend
  mutation); qz-codex bootstrap auto-refreshes when stale via
  QZ_CODEX_REFRESH_CATALOG=1 (default; set 0 to suppress).
- 26 new tests; 3103 total pass.

Key hard rules from A-design:
  - QZ_MODEL_KEY is a one-shot seed; never overrides persisted operator selection.
  - loaded_model is observation only, never selection authority.
  - qz-codex visibility-only preflight is not enough; must check
    selected/loaded mismatch via /qz/model/status.
  - Backend HTTP /health is not enough; classify model load/fit failures.
  - No scripts/qz-model, qz-select-model, qz-model-status, qz-load-model.
```

## Recently completed (2026-05-21 run — #65 D.1 GPU regression fix)

```text
#65 D.1 — GPU offload regression in BackendManager (proxy-owned backend)
  - Root cause: BackendManager's docker run lacked explicit NVIDIA env vars;
    proxy-started containers silently fell back to CPU with no detection.
  - Fix: added NVIDIA_VISIBLE_DEVICES=all and NVIDIA_DRIVER_CAPABILITIES=compute,utility
    to every docker run invocation.
  - Added QZ_EXPLICIT_NVIDIA_DEVICES=1 fallback for hosts where --gpus all
    does not propagate CUDA context (explicit --device passthrough).
  - Added post-health GPU log check: _check_gpu_offload_from_logs() parses
    container logs for hard failure and success patterns; sets gpu_offload_state.
  - QZ_REQUIRE_GPU=1 (default): phase=failed if CPU fallback/CUDA init failure
    detected. HTTP health alone is no longer sufficient.
  - BackendState gains gpu_required, gpu_offload_state, gpu_error.
  - New env: QZ_REQUIRE_GPU (default 1), QZ_GPU_LOG_TAIL (default 1000),
    QZ_EXPLICIT_NVIDIA_DEVICES (default 0).
  - 27 new tests; 2953 total PASS.
  - docs/backend-lifecycle-control-plane.md updated (§15.8, §15.5, §14 roadmap).
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
  - recovery_state.py: in-memory backoff/attempt counts (sufficient as-is; #51 CLOSED not-planned)
  - Legacy catalog fallback removed (#45)

Stream watchdog and signal planning (#40, #42, now closed)
  - qz_stream_terminal.py classifies stream terminal outcomes
  - qz_stream_watchdog.py detects no-output and terminal-after-output timeouts
  - qz-thoughts renders non-ok stream_terminal_classified rows
  - qz_feedback.py exists for bounded future signal adoption
  - Runtime signal migration is deferred; needs a concrete bug or new scoped issue

Foundation audit before SQLite
  - docs/foundation-audit-before-sqlite.md maps gravity wells, signal paths,
    duplicated/underused signals, and Codex feedback gaps
  - #2 CLOSED not-planned; BrainCaseDB (#53/#54) and OperationalStore (#46) deliver the substrate
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

#2 CLOSED not-planned — original automatic-session/request-log shape rejected.
BrainCaseDB (explicit writes, model-facing memory) delivered by #53/#54.
OperationalStore (runtime events/facts) delivered by #46.
All existing doctrine still applies: no automatic ingestion, explicit writes only.
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

Do not implement persistent v2 without a new scoped issue defining session identity,
retention, non-goals, and explicit non-BrainCaseDB/non-OperationalStore boundaries.
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

**Close-out (this commit):** CLOSED. All acceptance criteria verified. Proxy docstrings
for `codex-home` path references updated. Artifact inventory table A2/A3 paths fixed.
`docs/config-data-path-audit.md` migration note extended to cover A2/A3. 2600 tests PASS.
#56 closed.

**#51/#46 Slice A-design (commit 69b2ba9) + A.2 correction (this commit):** CLOSED (design only).

OperationalStore: lightweight SQLite for runtime events and operational facts.
NOT BrainCaseDB. NOT model-visible memory. NOT recovery policy. NOT repeated-read v2.

Key decisions:
- Path: `$QZ_VAR_DIR/state/operational.sqlite3` (env: `QZ_OPERATIONAL_DB_PATH`)
- Module: `proxy/qz_operational_store.py` (future)
- Phase 1 tables: schema_meta, runtime_events, runtime_facts ONLY
- sessions/workspaces/recovery_state/repeated_read_state: removed — no Phase 1 consumer
- qz-write-runtime-state: dual-write in Slice C; JSON file stays for compatibility
- #51: CLOSED not-planned — backoff/cooldown persistence rejected; in-memory RecoveryState is sufficient
- #46: primary Phase 1 consumer (runtime_events replaces JSON trace)

Slices B-impl → B.1 → C-impl → C.1 complete. Close-out next.

**Slice C.1 audit (this commit):** CLOSED. Audit clean.
- JSON write always first: confirmed
- OperationalStore gated + non-fatal: confirmed
- No routing consumer of qz-runtime-state.json: confirmed
- qz-doctor does NOT read the JSON: confirmed
- /qz/control-plane is live authority: confirmed
- var/model-state.json + var/backend-state.json untouched: confirmed

**Slice D (this commit):** CLOSED. operational_store section added to
/qz/config/effective payload. Shows enabled, available, schema_version,
recent_events (limit 10), runtime_facts (5 standard keys). Disabled mode
is a clean no-op. 6 new tests. 2664 tests PASS.

All three #46 close-out conditions are now met:
  - /qz/config/effective shows OperationalStore runtime events ✅
  - qz-doctor does not read qz-runtime-state.json ✅
  - Zero routing consumers of qz-runtime-state.json ✅

**#46 close-out (this commit):** CLOSED. qz-runtime-state.json no longer written.
QZ_RUNTIME_STATE_PATH removed from qz-env. runtime_state_snapshot record removed
from /qz/config/effective. qz-write-runtime-state updated to write solely to
OperationalStore. Model carryover uses OperationalStore effective_model fact.
2660 tests PASS. #46 closed.

## #37 next stream seam: proxy-local terminal suppression (Slice 2G)

**#37 Slices 1–2F.1 complete. Post-#56 stocktake complete.**

Completed helpers:
- `StreamHopState` (Slice 1) — per-hop state bundling
- `StreamDecision` (Slice 2B) — vocabulary dataclass
- `_reasoning_only_abort_reason()` (Slice 2B) — 14 tests
- `_should_suppress_duplicate_response_start()` (Slice 2C) — 6 tests
- `_should_inject_hop_budget_signal()` (Slice 2D) — 11 tests
- `_should_inject_context_pressure_signal()` (Slice 2E) — 12 tests
- `stream_timeout_kind()` (Slice 2F) — 5 tests; in `qz_stream_watchdog.py`

No `decide_stream_event()`. No tool lifecycle, terminal, continuation/repair extraction.

Next safe seam: **proxy-local terminal suppression** (Slice 2G).

Selected in `docs/stream-reducer-boundary-design.md §9J`.

Key facts:
- 3-condition boolean check at `qz_responses_stream.py:~1779`
- Pure: `is_terminal_stream_event(event_type, payload) and completed_call is not None and is_proxy_local`
- No state mutation, no SSE rendering, no tool execution
- Test gap: 4 unit tests needed before or alongside extraction
- Do NOT touch outer-loop conditions at lines ~2001 and ~2016

Proposed helper:
```python
def _should_suppress_proxy_local_terminal(
    event_type, payload, completed_call, is_proxy_local
) -> bool:
```

**Slice 2G (this commit):** CLOSED. `_should_suppress_proxy_local_terminal()` extracted.
4 unit tests added in `ProxyLocalTerminalSuppressionHelperTests`. 2604 tests PASS.
Outer-loop conditions at ~2021/~2037 untouched. Side-effect block unchanged.

**Slice 2G.1 (this commit):** CLOSED. Audit found semantic drift: helper used
`completed_call is not None` but original used `and hs.completed_call` (truthiness).
Fixed to `bool(completed_call)`. Call-site guard updated from `is not None` to
truthiness; `is_proxy_local` extracted to local variable. 1 boundary test added.
2605 tests PASS. No behaviour change in real flow.

**Slice 2H-design (this commit):** CLOSED. Outer-loop state inventory complete.

Candidate assessment:
  StreamRunState (terminal flags only) — Low risk; **next**
  Terminal event seam — High risk; skip
  Tool lifecycle seam — High risk; skip
  Continuation/repair — Very high risk; skip

First safe StreamRunState cluster: `sent_response_start`, `sent_terminal`, `sent_done`.
Must NOT include: `sequence`, `public_trace`, `working_body`, `repair_hops_used`.

Coverage gaps to fill in 2H-impl: `StreamRunStateTests` (defaults + independence + persistence).

See `docs/stream-reducer-boundary-design.md §9M` for full inventory and acceptance criteria.

**Slice 2H-impl (this commit):** CLOSED. `StreamRunState` added with
`sent_response_start`, `sent_terminal`, `sent_done`. In `run()`, 3 locals
replaced with `rs = StreamRunState.fresh()`. All uses updated to `rs.*`.
3 unit tests in `StreamRunStateTests`. 2608 tests PASS. No behaviour change.

**Slice 2H.1 (this commit):** CLOSED. Audit clean. `rs` confirmed outside hop loop,
all three terminal flag reads/writes correct, timeout handlers receive values not rs object,
no extra fields moved. One identity test added (`test_fresh_returns_new_instance_each_time`).
2609 tests PASS. No behaviour change.

**Finish-plan (this commit):** Slices 2I → 2I.1 → 2J-close-out defined.

#37 finish line: per-hop state (StreamHopState ✓), cross-hop terminal flags
(StreamRunState ✓), cross-hop timing/index arithmetic (StreamRunState after 2I),
pure helpers (7 extracted ✓), remaining side-effects explicitly bounded in place.
No decide_stream_event() required.

**Slice 2I-impl (this commit):** CLOSED. `StreamRunState` expanded with
`started_at`, `first_output_at`, `final_usage`, `output_index_offset`.
`fresh()` now requires `started_at: float`. 62 lines in `run()` updated to `rs.*`.
`completed_at`/sequence/public_trace/summary_started/working_body/repair remain locals.
6 new tests (total 11 in `StreamRunStateTests`). 2615 tests PASS.

**Slice 2I.1 (this commit):** CLOSED. Audit clean. All 7 StreamRunState fields confirmed.
No bare `started_at`/`first_output_at`/`final_usage`/`output_index_offset` in `run()`.
Method signatures unchanged. `completed_at`/sequence/public_trace/summary_started/
working_body/repair all remain locals. No code changes needed. 2615 tests PASS.

**Slice 2J close-out (this commit):** CLOSED. All acceptance criteria PASS.
Remaining locals (sequence, public_trace, summary_started, working_body, repair state)
documented as intentionally bounded in place. No decide_stream_event() required.
#37 CLOSED.

**#39 Slice A-design (this commit):** CLOSED (design only). search-config-contract.md
defines: config/default/search.json schema, precedence rules (QZ_SEARCH_CONFIG_PATH
> user > default > legacy SEARXNG_POLICY), compatibility with all existing env vars,
/qz/config/effective exposure plan, qz_tool_web.py integration path, slice roadmap.

**#39 Slice B-impl (this commit):** CLOSED. config/default/search.json,
config/example/search.json, proxy/qz_search_config.py loader, 23 tests.
Loader not yet wired into proxy startup. All validation PASS.

**#39 Slice B.1:** CLOSED. Fixed QZ_SEARCH_CONFIG_PATH to not inherit tracked default profiles. 1 new test.

**#39 Slice C-impl (this commit):** CLOSED. Wired into proxy startup and /qz/config/effective.
- quantzhai_proxy.py: _initialize_proxy_state() calls load_search_config(); stores as ProxyHandler.search_config_result
- qz_request_router.py: passes search_config_profiles to WebSearchRuntime
- qz_tool_web.py: search_config_profiles param; _valid_profiles() + profile resolution use v1 profiles as fallback
- qz_config_report.py: active_search_config section via effective_summary(); never exposes base URL
- 7 new tests (ActiveSearchConfigReportTests). 2691 tests PASS.

**#39 Slice C.1 (this commit):** CLOSED. Audit clean with 5 new profile-precedence tests.
  - Legacy web_search_profiles wins when present: confirmed
  - v1 profiles are fallback only when legacy has no entry: confirmed and tested
  - Same-name profile: legacy wins (tested)
  - None/empty search_config_profiles: handled safely
  - active_search_config stable and base-URL-free: confirmed
  - SEARXNG_* env compat: confirmed
  2696 tests PASS.

**#39 Slice D (this commit):** CLOSED. qz.profiles.v1 bundle search.default_profile
wired into SearchPolicySelection. Precedence: per-model override > bundle default >
search.json defaults.profile > auto. Routing rules stay in search.json/legacy policy.
8 new tests. 2704 tests PASS.

**#39 close-out (this commit):** CLOSED. All acceptance criteria PASS.
QZ_SEARCH_CONFIG_PATH added to qz-env. .env.example updated.
search-policy.json deprecation note added — remains as legacy compat only.
search.json is primary. Future removal → new issue. 2704 tests PASS.

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

## Recently completed (2026-05-21 run — #63 retrieve action)

```text
#63  web_search retrieve action     — CLOSED (all slices delivered; live smoke passed)
     Slices: A-design, B-impl, B.1-audit, C-live-smoke
     Bug fixed: FSE freshness now uses fields.updated_at/published_at as fallback
     Known limit: max_results=8 and max_retrieved_chars=12000 ceilings → #64
```

## Active next chain: tool policy improvement

BrainCase work is paused until tool policy is improved and audited.

```text
#59  Tool coercion/advice policy audit   — Slice A-audit CLOSED; gaps documented
#60  web_search quality improvements     — CLOSED. All slices A–close-out delivered.
#61  Native exec/tool advisory policy    — OPEN; depends on #59
#62  apply_patch coercion audit          — OPEN; depends on #59
#63  web_search retrieve action          — CLOSED. All slices delivered.
#64  Research-grade web_search budgets   — OPEN; new. Depends on #63.
```

After tool policy chain:
- #8  RFC/research — later
- #52 Upstream-blocked on TurboQuant — no action needed

## #65 Backend lifecycle control plane

```text
Goal: move Docker/backend lifecycle out of qz-up shell script into
      proxy BackendManager; qz-up starts proxy only; proxy autostarts
      backend; /qz/backend/* endpoints expose control.

Design doc: docs/backend-lifecycle-control-plane.md

Slices:
  A-design  — ✅ COMPLETE. Full design in docs/backend-lifecycle-control-plane.md.
  B1-impl   — ✅ COMPLETE. BackendManager skeleton + Docker command builder; 49 tests; 2870 pass.
  B2-impl   — ✅ COMPLETE. Lifecycle + proxy integration + endpoints + control-plane. 2892 pass.
  B3-impl   — ✅ COMPLETE. qz-up stripped; qz-down graceful+force; qz-backend added. 2924 pass.
  B.1-audit — ✅ COMPLETE. 4 bugs fixed; docker_cmd documented; 2929 pass.
  C-doc     — ✅ COMPLETE. Operator guide added; QZ_DOCKER_CMD guidance; duplicates cleaned.
  D-smoke   — cold-start smoke (§12 of design doc)

Design doc: docs/backend-lifecycle-control-plane.md §15 (operator guide)
```

## #64 Research-grade web_search budgets and modes

```text
Goal: replace hard-coded ceilings (max_results=8, max_retrieved_chars=12000)
      with named budget modes and operator-configurable absolute limits.

Budget modes:
  quick:  max_results=8,  max_searches=4,  max_opens=3,  max_retrievals=2,  max_chars=6000
  normal: max_results=12, max_searches=8,  max_opens=8,  max_retrievals=4,  max_chars=12000
  deep:   max_results=25, max_searches=20, max_opens=20, max_retrievals=10, max_chars=30000
  audit:  max_results=50, max_searches=40, max_opens=40, max_retrievals=20, max_chars=60000

web_search gains budget_mode argument.
Flat routing.max_* fields remain as compatibility fallback.
Operator-configurable absolute_max_* replace hardcoded ceilings.
Telemetry budget-exceeded events include budget_mode.
Default when no mode given: normal (document this decision in Slice A-design).

Slices:
  A-design  — ✅ COMPLETE. docs/search-config-contract.md §64 written.
  B-impl    — ✅ COMPLETE. budget_mode wired; hard ceilings removed; 2804 tests pass.
  B.1-audit — ✅ COMPLETE. Dead code removed; 10 new edge-case tests; 2814 pass.
  C-doc     — ✅ COMPLETE. Tool description expanded; §64.8 updated; budget table added.
  D-live-smoke — ✅ COMPLETE. deep=25 results, audit=50, chars per-mode. docs/search-config-contract.md §64.12.

Key code targets:
  proxy/qz_tool_web.py line 798  — WEB_SEARCH_MAX_RESULTS hard ceiling to remove
  proxy/qz_tool_web.py line 515  — WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING to replace
  config/default/search.json     — add budget_modes + absolute_max_* to routing
  config/example/search.json     — document new fields
```

## Maintenance rule

When a task changes direction, update this file in the same commit as the doc or
implementation change that caused it.

A stale task DAG is just a roadmap wearing novelty glasses.
