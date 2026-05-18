# QuantZhai Post-Stabilisation Stocktake

Date: 2026-05-15
Status: post-VRAM/recovery/stream-watchdog stabilisation stocktake.

This document is a point-in-time snapshot for agents picking up after the
2026-05-15 stabilisation run. For the live execution order, read
`docs/current-task-hierarchy.md`. For architecture authority, read
`docs/current-architecture-authority.md`.

---

## 1. Current operational state

The local Codex + Qwen stack is usable, observable, and has a working recovery
system. VRAM telemetry is live in qz-top with provenance labels. The first
optional SQLite storage substrate skeleton exists and remains disabled by
default. A foundation audit constrains #2 to parser-boundary identity/scoping
facts and defers runtime signal unification.

```text
Full test suite:     1442 tests passing
Live smoke:          qz-live-smoke passes
Recovery system:     full trigger/plan/backoff/async-job API operational
VRAM panel (qz-top): live, provenance-labelled, calibrated
Stream watchdog:     terminal/no-output classifications operational
Docs doctrine:       docs/patterns/provenance-telemetry.md active
Agent rules:         AGENTS.md includes telemetry doctrine
```

---

## 2. Recently completed work

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
| #53 | BrainCase memory tool API | **CLOSED** (Slices A–I.1 complete; 2406 tests) |
| #54 | BrainCase retention/lifetime policy | **CLOSED** (Slices A–D complete; 2406 tests) |
| #37 | Architectural seam extraction plan | **NEXT** — stream state machine seam is Slice 1 target |
| #51 | Promote recovery/backoff runtime state to SQLite | **deferred** until operational-store decision |
| #46 | Replace qz-write-runtime-state launcher trace | **deferred** until startup-telemetry replacement |
| #5 | Config/var/script ownership cleanup | **optional-polish** (do not preempt #37) |
| #39 | Split search routing policy into search.json | **optional-polish** (resume when search work resumes) |
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
2406 tests. BrainCaseDB is the first concrete LimbiCore technology.

**#54** — CLOSED. BrainCase retention policy: multi-axis matrix, pure evaluator,
retention-report dry-run surface, prune --apply retire path. 2406 tests.

**#51** — Recovery backoff state is currently in-memory only. Should be persisted
once #2 exists. Do not implement before #2.

**#46** — qz-write-runtime-state is a launcher trace only, not live-status truth.
Removal blocked until a startup-telemetry or SQLite replacement path exists.

**#37** — **NEXT priority.** Architectural seam extraction, starting with the
stream state machine seam. See "Prompt A" below for Slice 1 plan.
Do not do a grand rewrite. One seam at a time, test-backed.

**#5** — Config/var cleanup is ongoing and never fully done. Do not preempt #2
for it.

**#39** — Useful when search work resumes. Not urgent.

**#52** — QuantZhai already handles the priority correctly. Waiting for TurboQuant
to expose `model_size_bytes` / `kv_cache_size_bytes`. Keep as a tracker only.

**#8** — Research RFC. No implementation target yet.

**#7** — Created before #1, #2 were started. Describes what #2 and related issues
now track. Keep as historical planning record; do not implement from it directly.

---

## 4. Dependency map

```
#53 / #54  BrainCase memory + retention          CLOSED
#37  stream seam extraction (Slice 1)            NEXT — no hard dependencies
  \-> #37 Slice 2+  further seam extraction
  \-> #51 recovery state persistence             deferred / operational-store decision
  \-> #46 launcher trace removal                 deferred / startup-telemetry
#5  config cleanup                               incremental; do not preempt #37
#39 search config split                          when search work resumes
#52 backend allocator metrics                    upstream-blocked
#8  compaction RFC                               research; no implementation dependency
#7  LimbiCore/SQLite planning                    deferred; superseded by #53/#54
```

---

## 5. Recommended next work order

```
A. #37  Stream state machine seam extraction (Slice 1) — NEXT
        BrainCase feature work is paused (#53/#54 closed).
        Start here: extract per-hop state from qz_responses_stream.py.
        See Prompt A below for the full Slice 1 plan.

B. #51  Recovery/backoff state persistence
        Deferred until operational-store decision.
        BrainCaseDB is NOT the target. Needs a separate lightweight store.

C. #46  Remove qz-write-runtime-state
        Deferred until startup-telemetry replacement exists.

D. #5   Config/var/script cleanup
        Ongoing. Do not preempt #37.

E. #39  Search config split
        When search work resumes.

F. #52  Backend allocator metrics
        Upstream-blocked. No action needed in QuantZhai.
```

---

## 6. Blocked / upstream-blocked work

| # | Blocked by | Action |
|---|---|---|
| #51 | #2 | Wait for SQLite substrate |
| #46 | #2 or startup-telemetry | Wait for replacement path |
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
  → CLOSED. render/recall/write_candidate + operator review CLI. 2406 tests.
  → BrainCaseDB is the first concrete LimbiCore technology.
  → Do not add new BrainCase model-facing tools without a new issue.

BrainCase retention/lifetime policy (#54)
  → CLOSED. Multi-axis policy, pure evaluator, dry-run report, prune --apply.
  → 2406 tests. Retention is operator-controlled, not automatic.
  → Do not add automatic ingestion or background jobs.
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
  Next: pause for top-level stocktake before touching more complex stream decisions.
  Remaining candidates (tool lifecycle, terminal events, watchdog, proxy-local suppression,
  continuation/repair flow) carry higher extraction risk and need a fresh design micro-slice first.

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
