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
| #2 | Add optional Phase 1 SQLite storage substrate | **parked** (slice 1 skeleton landed; waiting for StateRecord/memory-write design) |
| #51 | Promote recovery/backoff runtime state to SQLite | **blocked-by-#2** |
| #46 | Replace qz-write-runtime-state launcher trace | **blocked-by-#2** (or startup-telemetry) |
| #37 | Architectural seam extraction plan | **architectural/refactor** (small targeted seams only) |
| #5 | Config/var/script ownership cleanup | **optional-polish** (do not preempt state work) |
| #39 | Split search routing policy into search.json | **optional-polish** (resume when search work resumes) |
| #52 | Backend-confirmed VRAM allocator metrics | **upstream-blocked** (TurboQuant side) |
| #8 | RFC: NetTTS survival-weighted compaction | **research/later** |
| #7 | What next: LimbiCore seam, memory_domain, SQLite | **planning/stale** (mostly superseded by #2) |

### Notes on each

**#2** — Storage substrate. Slices A–F (and C.1, D.1) are complete. #2
remains the storage substrate only. Details:
(schemas/fixtures; BrainCaseDB v3; search/inspect/FTS5; FTS reindex; explicit
write/update helpers in qz_braincase_write.py; internal render packet builder
in qz_braincase_render.py; braincase.render tool surface in qz_braincase_tools.py).
Slice F done: braincase.render is the first model-visible tool
(QZ_BRAINCASE_TOOLS_ENABLED, default disabled). Next slice: define recall semantics
or operator-reviewed write exposure. Unlocks #51 and #46 when more slices land.
See `docs/braincase-memory-tool-api.md`.

**#51** — Recovery backoff state is currently in-memory only. Should be persisted
once #2 exists. Do not implement before #2.

**#46** — qz-write-runtime-state is a launcher trace only, not live-status truth.
Removal blocked until a startup-telemetry or SQLite replacement path exists.

**#37** — Large architectural description. Do not treat as a mandate for a big
rewrite. Extract small targeted seams when touching relevant modules. Never
rewrite behaviour purely for structure.

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
#2 Phase 1 SQLite                   → no hard dependencies; unblocked
  \
   -> #51 recovery state persistence
   -> #46 launcher trace removal
   -> #4 (repeated-read v2 persistence, later)
   -> #P6 LimbiCore rendered state packets (much later)

#37 seam extraction                 → guide it from active work, not upfront
#5  config cleanup                  → incremental; do not preempt #2
#39 search config split             → when search work resumes
#52 backend allocator metrics       → upstream-blocked; no action needed now
#8  compaction RFC                  → research; no implementation dependency
```

---

## 5. Recommended next work order

```
A. #2   BrainCase memory/state records (next slice)
        Slices A–F complete. BrainCaseDB exists; braincase.render is exposed.
        Next: define braincase.recall semantics or operator write exposure.
        BrainCaseDB stores StateRecords/SourceRefs only — NOT sessions/turns/
        requests as operational logs. See docs/braincase-memory-tool-api.md.

B. #51  Recovery/backoff state persistence
        IMPORTANT: BrainCaseDB is NOT the target. Recovery/backoff state is
        operational runtime state and needs a separate persistence decision.
        Do not implement into BrainCaseDB tables.

C. #46  Remove qz-write-runtime-state
        Clean once startup telemetry or #2 replacement is established.

D. #37  Seam extraction (incremental slices only)
        Extract seams when touching relevant modules above.
        Do not do a grand rewrite.

E. #5   Config/var/script cleanup
        Ongoing. Do not preempt the state spine.

F. #39  Search config split
        When search work resumes.

G. #52  Backend allocator metrics
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

### Prompt A: BrainCase Slice F — harness/tool exposure (#53)

**Slices A through E (and C.1, D.1) are complete. Slice F may now start.**

Read `docs/braincase-memory-tool-api.md` before starting.

```text
Slice A   COMPLETE: schemas + fixtures + 44 tests
Slice B   COMPLETE: BrainCaseDB schema v3, put/get/list/retire/supersede
Slice C   COMPLETE: query_plan/search/inspect + FTS5 (80 total)
Slice C.1 COMPLETE: rebuild_fts_index / FTS backfill (92 total)
Slice D   COMPLETE: qz_braincase_write.py helpers + write/update paths (1744 total)
Slice D.1 COMPLETE: conflict marker detection tightened
Slice E   COMPLETE: qz_braincase_render.py + render_pack/braincase_render_packet + 53 tests (1819 total)
Slice F   COMPLETE: qz_braincase_tools.py + braincase.render tool surface + 64 tests (1906 total)
Slice G   COMPLETE: braincase.recall semantics + 5 recall modes + tier routing (1966 total)\nSlice G.1 COMPLETE: tier-bounded retrieval + deterministic enum order (1983 total)
            Exposed: braincase.render + braincase.recall when QZ_BRAINCASE_TOOLS_ENABLED.
            write/update/search/inspect remain unexposed.
            No automatic ingestion.

Next slice: operator-reviewed write exposure (braincase.write) or recall policy polish.
- No automatic ingestion at any step.
```

### Prompt B: Recovery state persistence (#51, after #2)

```text
Promote #47 in-memory recovery/backoff state to a persistent store.
NOTE: BrainCaseDB is NOT the target. Recovery/backoff is operational runtime
state; it needs a separate persistence decision, not BrainCaseDB tables.

Read first:
- docs/backend-service-recovery-semantics.md
- proxy/qz_recovery_state.py
- #51 issue body
- #2 implementation (must be done first)
```
