# QuantZhai Post-Stabilisation Stocktake

Date: 2026-05-19 (updated — post-#5/#57 close-out stocktake)
Status: post-BrainCase/repeated-read/stream-seam/config-cleanup/qz-codex-remote stocktake.

This document is a rolling point-in-time snapshot. For the live execution order,
read `docs/current-task-hierarchy.md`. For architecture authority, read
`docs/current-architecture-authority.md`.

---

## 1. Current operational state

The local Codex + Qwen stack is usable, observable, has a working recovery
system, a tool-mediated BrainCase memory layer, a live repeated-read advisory
signal, and a partial stream-seam extraction.

```text
Full test suite:         2615 tests passing
Live smoke:              qz-live-smoke passes
Repeated-read smoke:     qz-smoke-repeated-read passes
BrainCase smoke:         qz-braincase-smoke 12/12 passes
Recovery system:         full trigger/plan/backoff/async-job API operational
VRAM panel (qz-top):     live, provenance-labelled, calibrated
Stream watchdog:         terminal/no-output classifications operational
BrainCase memory:        braincase.render/recall/write_candidate (#53 closed)
Repeated-read signal v1: advisory, stateless, input-history-seeded (#3/#4/#43 closed)
Stream seam (#37):       CLOSED — StreamHopState + StreamRunState + 6 pure helpers
Config observability:    /qz/config/effective full source/staleness coverage (#5 closed)
Generated artifacts:     all A1/A2/A3 under var/generated/ (#56 closed)
qz-codex bootstrap:      always HTTP via /qz/codex/client-config (#58 closed)
Docs doctrine:           docs/patterns/provenance-telemetry.md active
Agent rules:             AGENTS.md includes telemetry and BrainCase doctrine
```

---

## 2. Recently completed work

### 2026-05-20 run (#37 close-out + #56 close-out + stocktake)

| Item | What shipped |
|---|---|
| #37 Stream seam extraction | CLOSED. Slices 1–2J. StreamHopState, StreamRunState (7 fields), 6 pure helpers. |
| #56 Generated artifact migration | CLOSED. A1/A2/A3 under var/generated/. codex_home_dir removed. All tests PASS. |
| #51/#46 operational-state | Next priority: Slice A-design — define operational store boundary and session identity. |

### 2026-05-19 run (#5 close-out + #57 qz-codex remote bootstrap)

| Item | What shipped |
|---|---|
| #5 Config/var/script cleanup | CLOSED. /qz/config/effective: file metadata, source labelling, generated artifact staleness warnings. |
| #57 qz-codex-common thinning | CLOSED. /qz/codex/client-config + /qz/codex/model-catalog server endpoints. QZ_CODEX_REMOTE=1 launcher remote mode writes local CODEX_HOME atomically with TOML escaping. |
| #37 Slices 2F + 2F.1 | Stream timeout-kind combiner extracted; co-located helper audit polish. |
| #56 opened | Generated artifact path migration design (var/generated/) — follow-up from #5. |

### 2026-05-18 run (BrainCase + repeated-read + #37 stream seam)

| Item | What shipped |
|---|---|
| #53 BrainCase memory tool API | CLOSED. Slices A–I.1. render/recall/write_candidate tools, operator review CLI, retention policy. |
| #54 BrainCase retention/lifetime policy | CLOSED. Slices A–D. Multi-axis policy evaluator, dry-run report, prune --apply. |
| #3/#4/#43 Repeated-read v1 | CLOSED. Parser + state + integration + live smoke. Advisory, stateless, input-history-seeded. |
| #37 Slices 1–2E.1 | Stream seam helpers extracted (StreamHopState, StreamDecision, 4 pure helpers). Paused before delicate seams. |

### 2026-05-15 run (VRAM + recovery + docs lock)

| Item | What shipped |
|---|---|
| #6 VRAM telemetry | Closed. Full provenance-labelled VRAM panel live in qz-top. |
| #44 Backend control-plane audit | Closed. /qz/control-plane is the live status authority. |
| #47 Backend service/recovery semantics | Closed. Full service taxonomy, status strings, recovery classification matrix. |
| #48 start_backend trigger | Closed. Non-destructive; skips if already running. |
| #49 select_model trigger | Closed. All 6 recovery actions now implemented. |
| #50 Async recovery job model | Closed. reload_selected_model supports async=true; job state in RecoveryJobStore. |
| #40 Stream/compaction hang watchdog | Closed. Stream terminal classifications and watchdog fallbacks live. |
| #42 Signal/feedback subsystem | Closed as design/mapping/core wrapper work; runtime signal migration deferred. |
| Foundation audit before SQLite | New. `docs/foundation-audit-before-sqlite.md` is the pre-#2 signal/data-path audit. |
| #45 Remove legacy catalog fallback | Closed. Proxy is sole model catalog path. |
| docs/patterns/provenance-telemetry.md | New. Confidence vocabulary, arithmetic rule, anti-patterns, VRAM example. |
| #52 Backend VRAM metrics follow-up | Opened. Upstream-blocked; QuantZhai side already wired. |

### 2026-05-14 run (profiles + sandbox + smoke)

| Item | What shipped |
|---|---|
| #26/PR#27 qz.profiles.v1 | Active config format. Profiles/*.json split. |
| #23/PR#24/#25 memory_domain | Wired from profile config through to request context. |
| #29/PR#30 Simplified prompts | Short depth-only prompts; hard tool-call caps removed. |
| #28/PR#31-34 Sandbox telemetry | tool_sandbox_denied, tool_escalation_requested classifiers + qz-thoughts render. |
| #35/PR#36 Live stack smoke | scripts/qz-live-smoke validates end-to-end path. |
| #38 Docs refresh | Auth map, hierarchy, README updated post-stabilisation. |
| #41-#43 Signal system | Bidirectional signal map, generic feedback subsystem, repeated-read smoke. |

---

## 3. Current open issues

| # | Title | Classification |
|---|---|---|
| #2 | Add optional Phase 1 SQLite storage substrate | **parked** (BrainCaseDB done via #53/#54; #2 may be closed or refocused) |
| #53 | BrainCase memory tool API | **CLOSED** (Slices A–I.1 complete) |
| #54 | BrainCase retention/lifetime policy | **CLOSED** (Slices A–D complete) |
| #3/#4/#43 | Repeated-read v1 (parser, integration, smoke) | **CLOSED** (complete; advisory stateless v1 live) |
| #37 | Architectural seam extraction plan | **CLOSED** — Slices 1–2J complete; StreamHopState + StreamRunState + 6 pure helpers |
| #56 | Generated artifact path migration design (var/generated/) | **CLOSED** — A1/A2/A3 under var/generated/; helpers clean; all acceptance criteria PASS |
| #51 | Promote recovery/backoff runtime state to SQLite | **CLOSED (not planned)** — backoff/cooldown persistence rejected; in-memory RecoveryState is sufficient |
| #46 | Replace qz-write-runtime-state launcher trace | **CLOSED** — JSON retired; OperationalStore + /qz/config/effective are the authority |
| #5 | Config/var/script ownership cleanup | **CLOSED** (#56, #57 opened for migration/thinning follow-ups) |
| #57 | qz-codex-common thinning | **CLOSED** (Slices A–C2.1 complete; remote bootstrap endpoints delivered; superseded by #58) |
| #58 | Always-HTTP qz-codex bootstrap | **CLOSED** (D2/D2.1/D3 complete; qz-codex always uses HTTP; #56 remains separate) |
| #39 | Split search routing policy into search.json | **Slice A-design complete** — contract defined; Slice B-impl next |
| #52 | Backend-confirmed VRAM allocator metrics | **upstream-blocked** (TurboQuant side) |
| #8 | RFC: NetTTS survival-weighted compaction | **research/later** |
| #7 | What next: LimbiCore seam, memory_domain, SQLite | **deferred** — superseded by #53/#54; informational only |

### Notes on each

**#2** — BrainCaseDB storage substrate. #53 and #54 delivered the full BrainCase
tool plane (render/recall/write_candidate) and retention policy on top of it.
#2 may be closed or converted to a tracking issue; BrainCaseDB is stable and
proven. Do not add new BrainCase features here. BrainCase work is paused.
See `docs/braincase-memory-tool-api.md`, `docs/braincase-retention-policy.md`.

**#53** — CLOSED. BrainCase memory tool API: render/recall/write_candidate,
operator review CLI, candidate write, retention policy evaluator, prune CLI.
BrainCaseDB is the first concrete LimbiCore technology.

**#54** — CLOSED. BrainCase retention policy: multi-axis matrix, pure evaluator,
retention-report dry-run surface, prune --apply retire path.

**#3/#4/#43** — CLOSED. Repeated-read v1: parser/state (qz_file_signal.py),
integration (qz_proxy_tools.py, qz_responses_stream.py, qz_request_router.py),
and live smoke (scripts/qz-smoke-repeated-read). Advisory, stateless,
input-history-seeded. V2 is blocked on SQLite/session identity.

**#37** — CLOSED. Stream seam extraction complete. Slices 1–2J.

StreamHopState (per-hop state), StreamRunState (7 cross-hop fields), 6 pure
decision helpers. No decide_stream_event() required. Remaining side-effect
locals (sequence, public_trace, working_body, repair) intentionally bounded in
place. 2615 tests passing. See `docs/stream-reducer-boundary-design.md §9S`.

**#56** — CLOSED. Generated artifact path migration (var/generated/). Slices A–E + close-out complete.

- A1 at `var/generated/model-inventory.json` (Slice C); QZ_MODEL_INVENTORY_CACHE override preserved.
- A2 at `var/generated/codex/qwenzhai-models.json`; A3 at `var/generated/codex/config.toml` (Slice D).
- CODEX_HOME is client-local (qz-codex); server paths are pure QZ_VAR_DIR (Slice B.1).
- Stale helpers `codex_home_dir()` / `codex_model_catalog_dir()` removed; regression guard added (Slice E).
- No symlink shim. No old-path deletion. No qz-codex-common changes.
- Close-out audit fixed 3 stale proxy docstrings and 2 doc path references.
- All acceptance criteria PASS.

**#51** — CLOSED (not planned). Backoff/cooldown persistence rejected in Slice A.2.
In-memory `RecoveryState` is sufficient. Future recovery diagnostics need a new
issue with concrete requirements.

**#46** — CLOSED. qz-runtime-state.json retired. QZ_RUNTIME_STATE_PATH removed.
OperationalStore + /qz/config/effective are the authority for launcher events.

**#5** — Config/var cleanup is ongoing and never fully done. Safe to do any time
without blocking other work. Good incremental choice between larger features.

**#39** — Slice A-design complete. `search-config-contract.md` defines the v1 contract:
`config/default/search.json` + `config/user/search.json`, precedence rules,
`SEARXNG_*` compat, `/qz/config/effective` exposure. Slice B creates the files
and `proxy/qz_search_config.py` loader. See `docs/search-config-contract.md`.

**#52** — QuantZhai already handles the priority correctly. Waiting for TurboQuant
to expose `model_size_bytes` / `kv_cache_size_bytes`. Keep as a tracker only.

**#8** — Research RFC. No implementation target yet.

**#7** — Created before #1, #2 were started. Describes what #2 and related issues
now track. Keep as historical planning record; do not implement from it directly.

---

## 4. Dependency map

```
#53 / #54   BrainCase memory + retention                 CLOSED
#3/#4/#43   Repeated-read v1                             CLOSED
#37  stream seam extraction Slices 1–2J                  CLOSED
#56  generated artifact migration                        CLOSED
#57  qz-codex remote bootstrap                           CLOSED
#58  always-HTTP qz-codex bootstrap                      CLOSED
#5   config/var/script cleanup                           CLOSED
#46  launcher trace removal                              Slice B+C → OperationalStore runtime_events
#51  recovery state persistence                          CLOSED not-planned; in-memory is sufficient
#39  search config split                                 Slice A-design done; B-impl creates files + loader
#52  backend allocator metrics                           upstream-blocked
#8   compaction RFC                                      research; no implementation dependency
#7   LimbiCore/SQLite planning                           deferred; superseded by #53/#54
Repeated-read v2                                         blocked on SQLite/session identity
```

---

## 5. Recommended next work order

```
A. #58  Always-HTTP qz-codex bootstrap — CLOSED
B. #56  Generated artifact migration — CLOSED
C. #37  Stream seam extraction — CLOSED (Slices 1–2J; StreamHopState + StreamRunState + 6 pure helpers)
D. #53  BrainCase memory tool API — CLOSED
E. #54  BrainCase retention policy — CLOSED
F. #5   Config/var/script cleanup — CLOSED
G. #3/#4/#43  Repeated-read v1 — CLOSED

NEXT:
  #46 close-out — wire /qz/config/effective to show OperationalStore events, then remove JSON

  Slices B–C.1 complete:
    - qz_operational_store.py skeleton (runtime_events/runtime_facts)
    - qz-write-runtime-state dual-write live
    - C.1 audit confirmed: qz-doctor not a consumer; no routing consumers

  Remaining close-out condition:
    /qz/config/effective must surface OperationalStore runtime events

  After #46:
    #51 CLOSED not-planned — in-memory RecoveryState is sufficient
    #39  Search config split — Slice A-design done; Slice B-impl next
    #52  Backend allocator metrics — upstream-blocked
```

---

## 6. Blocked / upstream-blocked work

| # | Blocked by | Action |
|---|---|---|
| #46 | OperationalStore close-out | Wire /qz/config/effective; then remove JSON |
| #51 | CLOSED not-planned | In-memory RecoveryState is sufficient; no implementation planned |
| #52 | TurboQuant | Wait; no QuantZhai action needed |
| #8 | Research decision | Keep as RFC, no implementation |

---

## 7. Do-not-reopen solved problems

These are done. Do not re-implement, second-guess, or re-plan them.

```text
memory_domain config plumbing (#1, #23/PR#24/#25)
  → Use memory_domain, not profile_family.

qz.profiles.v1 active config format (#26/PR#27)
  → profiles.json + profiles/*.json is the active format.

Sandbox/tool-failure telemetry (#28/PR#31-34)
  → tool_sandbox_denied / tool_escalation_requested classifiers are live.

Live stack smoke script (#35/PR#36)
  → scripts/qz-live-smoke is the integration test.

Backend control-plane audit (#44)
  → GET /qz/control-plane is the live status authority.

Recovery system (#47-#50)
  → Full trigger/plan/backoff/async-job API operational.
  → Six recovery actions all implemented.

VRAM telemetry (#6)
  → Closed. Provenance-labelled panel live in qz-top.
  → Model calibration, file provenance, KV budget all working.
  → Doctrine locked in docs/patterns/provenance-telemetry.md.

Legacy catalog fallback removal (#45)
  → Proxy is sole model catalog path.

Stream/compaction hang watchdog (#40)
  → Closed. Stream terminal classifier/watchdog fallbacks live.

Signal/feedback subsystem design (#42)
  → Closed. Use qz_feedback for future bounded adoption, but do not migrate
    stream/runtime signals before #2.

BrainCase memory tool API (#53)
  → CLOSED. render/recall/write_candidate + operator review CLI.
  → BrainCaseDB is the first concrete LimbiCore technology.
  → Do not add new BrainCase model-facing tools without a new issue.

BrainCase retention/lifetime policy (#54)
  → CLOSED. Multi-axis policy, pure evaluator, dry-run report, prune --apply.
  → Retention is operator-controlled, not automatic.
  → Do not add automatic ingestion or background jobs.

Repeated-read v1 signal (#3/#4/#43)
  → CLOSED. Parser/state, integration, live smoke all complete.
  → Advisory, stateless, input-history-seeded. qz_file_signal.py.
  → Do not add persistence, session state, or v2 features without a new issue.
  → V2 is blocked on SQLite/session identity.

#37 stream seam extraction
  → CLOSED. Slices 1–2J complete.
  → StreamHopState (per-hop), StreamRunState (7 cross-hop fields), 6 pure helpers.
  → Remaining side-effect locals (sequence, public_trace, working_body, repair)
    intentionally bounded in place by design. See §9S in stream-reducer-boundary-design.md.
  → Do not reopen. No decide_stream_event() required.

Config/var/script ownership cleanup (#5)
  → CLOSED. /qz/config/effective: file metadata, source layer/path
    classification, prompt-file source labelling, generated artifact
    staleness warnings (stale_model_inventory_cache, stale_codex_catalog,
    stale_codex_config), stale_against precision.
  → Follow-ups: #56 (path migration design), #57 (qz-codex thinning).
  → Do not reopen for var/generated migration — that is #56.

qz-codex remote bootstrap (#57)
  → CLOSED. Slices A–C2.1. GET /qz/codex/client-config and
    GET /qz/codex/model-catalog server endpoints. QZ_CODEX_REMOTE=1
    launcher remote mode writes local CODEX_HOME atomically with
    TOML-escaped values. Co-located mode unchanged.
  → Do not reopen unless a real production bug appears.
  → Optional follow-up: live two-host LAN smoke test (manual).
```

---

## 8. Active doctrine and patterns

| Doctrine | Location | Applies to |
|---|---|---|
| Telemetry provenance | `docs/patterns/provenance-telemetry.md` | All new telemetry/status fields |
| VRAM component semantics | `docs/runtime-observability-notes.md` | qz.vram.snapshot.v1 |
| Recovery semantics | `docs/backend-service-recovery-semantics.md` | Recovery triggers, backoff, jobs |
| Memory/state scope | `docs/codex-context-memory-contract.md` | SQLite, memory_domain, workspace |
| Foundation audit | `docs/foundation-audit-before-sqlite.md` | Pre-#2 gravity wells, signal paths, feedback gaps |
| Agent behaviour rules | `AGENTS.md` | All agents working in the repo |
| Architecture authority | `docs/current-architecture-authority.md` | Supersedes older planning docs |

**Key doctrine rules:**
- Never mark an estimate as `backend_confirmed`.
- Never label residual as scratch.
- Never inject qz_* context into forwarded /v1/responses bodies.
- memory_domain defaults to isolated; never infer it from model/profile/tool names.
- BrainCaseDB stores explicit memory/state records only — not operational facts,
  not telemetry, not a session/request log, not a memory_domain registry.

---

## 9. Docs authority map

### Current / authoritative

| Document | Authority for |
|---|---|
| `docs/current-task-hierarchy.md` | Execution order and task DAG |
| `docs/current-architecture-authority.md` | Final conflict resolver |
| `docs/current-stocktake.md` (this file) | Point-in-time state summary |
| `docs/codex-context-memory-contract.md` | Codex identity/thread/workspace/memory-domain |
| `docs/patterns/provenance-telemetry.md` | Telemetry field doctrine |
| `docs/runtime-observability-notes.md` | VRAM component semantics, qz-top |
| `docs/backend-service-recovery-semantics.md` | Recovery taxonomy and status |
| `docs/responses-stream-tool-state-contract.md` | Streaming event/tool lifecycle |
| `docs/foundation-audit-before-sqlite.md` | Pre-#2 gravity wells, signal paths, feedback gaps |
| `proxy/qz_codex_metadata.py` (+ tests) | Parser boundary implementation |
| `AGENTS.md` | Agent rules |

### Historical (useful but not authoritative for new decisions)

| Document | Read as |
|---|---|
| `docs/state-and-memory-architecture-plan.md` | Typed-memory taxonomy; superseded by codex-context-memory-contract for scope |
| `docs/model-state-signal-contract.md` | LimbiCore future envelope; not Phase 1 scope |
| `docs/codex-request-signal-inventory.md` | Historical inventory; use parser/tests for state |
| `docs/master-stabilisation-plan.md` | Broader map; current-task-hierarchy wins for execution order |
| `#7` issue | Planning tracker from before #2 was prioritised; informational only |

---

## 11. Post-#53/#54/#37-helper-run stocktake (2026-05-18)

### 11.1 What is now complete

```text
BrainCase memory tool API (#53):
  braincase.render, braincase.recall, braincase.write_candidate tools live.
  Operator review CLI (qz-braincase-review), retention policy, prune --apply.
  Slices A–I.1 complete. BrainCaseDB is the first concrete LimbiCore technology.

BrainCase retention/lifetime policy (#54):
  Multi-axis policy matrix, pure evaluator, dry-run report, prune --apply retire path.
  Slices A–D complete. Retention is operator-controlled, not automatic.

Repeated-read v1 advisory signal (#3/#4/#43):
  Parser/state (proxy/qz_file_signal.py), integration into tool lifecycle and
  stream/non-stream paths, live smoke (scripts/qz-smoke-repeated-read).
  Advisory, stateless, input-history-seeded. No BrainCase writes. No persistence.

#37 stream seam Slices 1–2F.1:
  StreamHopState (per-hop state object).
  StreamDecision (vocabulary dataclass).
  _reasoning_only_abort_reason() — pure helper, keyword-only params.
  _should_suppress_duplicate_response_start() — pure helper.
  _should_inject_hop_budget_signal() — pure helper.
  _should_inject_context_pressure_signal() — pure helper.
  stream_timeout_kind() — pure helper (no_output/terminal/None combiner).
  No decide_stream_event() exists. qz_responses_stream.py remains the sole
  side-effect owner.

#5 config/var/script cleanup (CLOSED):
  /qz/config/effective: file metadata, source layer/path classification,
  prompt-file source labelling, staleness warnings, stale_against precision.
  Follow-ups: #56 (path migration), #57 (qz-codex thinning).

#57 qz-codex remote bootstrap (CLOSED):
  GET /qz/codex/client-config — provider/base_url/catalog metadata.
  GET /qz/codex/model-catalog — generated catalog served to remote clients.
  QZ_CODEX_REMOTE=1 launcher mode writes local CODEX_HOME atomically with
  TOML-escaped values. Co-located mode unchanged.

Full suite: 2566 tests passing.
```

### 11.2 What must not be touched without a design micro-slice

```text
#37 delicate seam extraction:
  tool lifecycle stream extraction
  terminal event extraction
  watchdog/no-output timeout extraction
  proxy-local suppression extraction
  continuation/repair flow extraction

These all carry higher extraction risk than Slices 2B–2E:
  timing-sensitive, multi-hop state mutation, hard-to-separate rendering
  and decision concerns, subtle telemetry dependencies.

Other hold-off:
  more BrainCase model-facing tools (new issue required)
  automatic ingestion of any kind
  repeated-read v2 (blocked on SQLite/session identity)
  operational SQLite persistence (#51/#46 blocked on #2)
  HSM/LimbiCore expansion (research phase)
```

### 11.3 Next practical options (ranked by risk)

```text
A. #37 design micro-slice for next delicate seam — viable but needs design first
   Remaining stream seams are higher-risk. Define the boundary, confirm coverage
   gaps, write design notes, then code in a later slice. Do not jump to code.
   See docs/stream-reducer-boundary-design.md §9F for the candidate inventory.

B. Config/var/script cleanup (#5) — low risk, always useful
   Ongoing. Does not block or preempt #37. Good incremental work between features.
   Focus: /qz/config/effective coverage, prompt-file warnings, catalog generation.

C. Telemetry filter ergonomics (P3) — low risk, low priority
   Optional /qz/telemetry/recent?type= filtering. Implement when noisy-window
   problem recurs in practice.

D. Recovery/backoff state persistence (#51) — deferred
   BrainCaseDB is NOT the target. Needs an operational-store decision first.
   Do not implement before that decision is made.

E. Repeated-read v2 — blocked
   Requires SQLite substrate with session/turn scope and proven session identity.
   Do not implement without a new design issue first.
```

### 11.4 Do-not-touch list (no coding without a new design issue)

```text
- Any new BrainCase model-facing tool
- Automatic ingestion of any kind
- #37 tool lifecycle / terminal / watchdog extraction (need design micro-slice)
- Repeated-read v2 persistent state
- Operational SQLite storage (#51/#46)
- HSM/LimbiCore memory expansion
- decide_stream_event() (full reducer — need design phase first)
```

---

## 10. Suggested next agent prompts

### Prompt A: #37 Slices 1, 1.1, 2A — Stream seam extraction and reducer boundary design — COMPLETE

**Slice 2A added:** `docs/stream-reducer-boundary-design.md`

Key decisions from Slice 2A:
- Reducer returns decisions; `qz_responses_stream.py` performs side effects.
- 16 decision types inventoried; reasoning-only abort is the best first extraction.
- `StreamDecision` dataclass + pure `_reasoning_only_abort_reason()` helper recommended for Slice 2B.
- Existing 121 stream tests cover most decisions; carry_forward_reasoning has a coverage gap.

**Slice 2B complete:** `StreamDecision` dataclass + `_reasoning_only_abort_reason()` helper extracted.
  No decide_stream_event() added. No stream side effects moved. 2437 tests passing.

**Slice 2C complete:** `_should_suppress_duplicate_response_start(event_type, sent_response_start) -> bool` extracted.
  6 unit tests added. 2443 tests passing. No behaviour change.

**Slice 2D complete:** `_should_inject_hop_budget_signal(hops_remaining, threshold) -> bool` extracted.
  Condition moved out of `_hop_budget_signal_message()`. 11 unit tests added. No behaviour change.

**Slice 2D.1 complete:** Audit of Slices 2B–2D helpers. All helpers confirmed pure and behaviour-preserving.
  No runtime code changes. 2453 tests passing.

**Slice 2E complete:** `_should_inject_context_pressure_signal(input_tokens, context_length, threshold) -> bool` extracted.
  Condition moved out of `_context_pressure_signal_message()`. 12 unit tests added. No behaviour change.
  Message construction and telemetry unchanged. 2465 tests passing.

**Slice 2E.1 complete:** Audit of Slices 2B–2E helpers. All helpers confirmed module-level, pure, and
  side-effect free. No runtime code changes required. StreamDecision remains vocabulary-only.
  No `decide_stream_event()` exists. 2465 tests passing.

**Slice 2F-design complete:** Compared all remaining #37 seam candidates; chose
  watchdog timeout-kind combiner as the next safe seam.

**Slice 2F complete:** `stream_timeout_kind(watchdog_state, now) -> str | None` added to
  `proxy/qz_stream_watchdog.py`. Two duplicated two-check patterns replaced at both call
  sites in `qz_responses_stream.py`. 5 unit tests added (`StreamTimeoutKindHelperTests`).
  No behaviour change. No side effects moved. 2470 tests passing.

**Slice 2F.1 complete:** Audit of `stream_timeout_kind()` purity and call-site behaviour.
  Priority test strengthened: `test_no_output_takes_priority_over_terminal` now patches both
  predicates via `unittest.mock.patch` to prove combiner priority directly. No runtime code
  changes. 2470 tests passing.
  Next: pause before further stream seam extraction. Config/var cleanup #5 is the safe alternative.

### Prompt A (archived): #37 Slice 1 + 1.1 — Stream state machine seam extraction — COMPLETE

**BrainCase feature work is complete (#53/#54 closed). Next: #37 stream seam.**

This is the first slice of architectural seam extraction. It does not change
external behaviour. It extracts mutable per-hop state from the streaming loop
into a named struct so the state machine is explicit and independently testable.

#### Background

`proxy/qz_responses_stream.py` is 1975 lines with 53 functions. The streaming
loop in `ResponsesStreamRuntime.run()` manages two layers of mutable state:

**Outer-loop state** (persists across continuation hops):
- `continuation_hop` — hop counter
- `public_trace` — accumulated model-visible output items
- `summary_started` — set of reasoning summary IDs already emitted
- `sequence` — SSE sequence counter
- `hop_body` — current request body for this hop
- `first_output_at`, `final_usage`, `sent_terminal`, `sent_done`

**Per-hop state** (reset at each hop start, ~lines 1150–1166):
- `tool_call_state` — StreamToolCallState() accumulator
- `event_lines`, `event_started_at` — SSE frame accumulator
- `next_input` — items to carry to next hop
- `completed_call` — current function_call being executed
- `error_injected`, `signal_injected`, `repair_injected` — injection flags
- `reasoning_only_*` vars — reasoning-only detection state
- `output_text_chars`, `visible_output_text_seen` — output character accounting
- `assistant_item_seen`, `public_item_seen` — output presence flags
- `max_output_index` — highest output_index seen
- `stream_obs_acc` — stream observation accumulator
- `watchdog_state` — StreamWatchdogState for timeouts

#### Target seam

Extract per-hop state into a dataclass: `StreamHopState` (or `_HopState`).

Benefits:
- Makes the state machine explicit without changing behaviour
- Allows unit tests that set up a hop state directly and verify transitions
- Makes `_run_responses_streaming_locally` easier to read
- Clarifies what resets between hops vs. what persists

#### Existing tests protecting behaviour

```text
tests/test_qz_responses_stream.py   — primary stream behaviour coverage
tests/test_qz_streaming.py          — SSE event helpers
tests/test_qz_sse.py                — SSE transform coverage
tests/test_qz_request_mutation_regression.py  — body mutation boundary
```

Run all four before and after the extraction.

#### Behaviours that must remain unchanged

- Continuation hop count and budget enforcement
- Tool call detection and execution (proxy-local and native)
- Error/signal/repair injection flags and their effects
- Reasoning-only detection and fallback
- Watchdog timeout triggering (no-output and terminal)
- SSE event sequence numbering
- public_trace assembly and final response emission
- Stream terminal classification
- Output text accounting and context pressure detection

#### Slice 1 implementation plan

```text
1. Add StreamHopState dataclass to qz_responses_stream.py:
   - fields: all per-hop local variables listed above
   - factory method: StreamHopState.fresh(hop_body) that initialises defaults
   - no logic, just state

2. Replace the ~15 local variable declarations at hop start with:
   hop_state = StreamHopState.fresh(hop_body)

3. Replace references to local vars with hop_state.* within the hop scope.
   Keep outer-loop vars (public_trace, sequence, etc.) unchanged.

4. Tests: add one test that instantiates StreamHopState directly and verifies
   fresh() produces expected defaults.

5. Run all four test files above. Zero behaviour change permitted.
```

Do not move logic. Do not rename functions. Do not change the streaming protocol.
This is a pure state-bundling refactor.

Read first:
- proxy/qz_responses_stream.py lines 1084–1175 (streaming loop outer + hop start)
- tests/test_qz_responses_stream.py
- #37 issue body

### Prompt B: Recovery state persistence (#51)

```text
Deferred until an operational-store decision is made.
BrainCaseDB is NOT the target.
Read docs/backend-service-recovery-semantics.md and #51 when resuming.
```
