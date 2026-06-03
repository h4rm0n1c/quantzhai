# QuantZhai Progress Snapshot

Last updated: 2026-06-03 (CUDA peer access fix, qwen3moe/mistral3 validation, benchmark corrections).

See `docs/current-stocktake.md` for the full point-in-time state summary.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without rereading every roadmap.

## Overall

Current estimate: **99% through stabilisation for the local Codex + Qwen goal**.

The 2026-05-31 session delivered:

**BrainCase memory manager (#82)** — full Limbicore session stack now live:
- Session interface: `braincase.impaction` (LLM-initiated claim ingestion) and `braincase.percolate` (FTS search + render).
- Memory manager orchestrator: compact-for-context → temporal metrics → LLM call → dispatch (bc_promote/bc_retire/bc_update_tier/bc_tag/bc_merge).
- Management pressure: `_should_inject_context_pressure_signal()` tracks token headroom; auto-triggers memory manager when compaction fires or pressure threshold crossed.
- `braincase.powernap` (intra-session lightweight recall) injected into system prompt when pressure is active.
- `bc_read` + `bc_search` for manager content review; `bc_challenge` for adversarial snap-judgement review before writes.
- BrainCase always on (gate removed); `memory_domain` auto-resolved from model config; `limbicore` on by default.
- End-to-end tests fixed for tier, visibility, two-turn, and FTS fallback paths. 

**Deterministic intercept layer (#83)** — proxy saves LLM turns on known-fixable failures:
- Sandbox escalation: exec sandbox denial → `SandboxEscalationManager` two-phase intercept, model receives success + plain-English note.
- Network proxy escalation: network denial blocks handled by the same escalation manager.
- E-1 fix: exec_command `command` field → `cmd` field correction before the call reaches the sandbox.
- AP-1/AP-1b: apply_patch empty diff / empty trailing hunk → precise coercion error returned immediately.
- AP-4: apply_patch context mismatch → advisory injection via `qz_native_tool_output.py` CLASSIFIERS; halved context-mismatch retries in live session.
- `CorrectionTracker`: silent coercions inject a plain-English note into the next tool result.
- apply_patch delta_limit = -1 (unlimited); AP-2/AP-3 probe-only (zero live occurrences).

**System prompt v2** — HSM-research-informed additions: working memory framing, tool discipline, self-correction patterns.

The 2026-05-30 session delivered: router-mode contract violations all closed (profile alias
routing, hold-open 503 elimination, dual-load prevention, same-GGUF instant switch); VRAM
backend metrics live (#52 closed); SSE visibility and async proxy-local tool executor.

The 2026-05-29 session delivered: router mode fully live, GPU/crash reliability fixes
(#79 and #80), LLM v3 compaction, survival scorer generalised to 10-repo corpus, MTP
draft speculation confirmed, tensor split corrected to 10,16.

Control sheet:

```text
docs/current-task-hierarchy.md
docs/current-stocktake.md
```

Current strategic direction:

```text
P1 #51/#46 Slice B-impl — create qz_operational_store.py (schema_meta/runtime_events/runtime_facts)
P2 #39 Search config split — when search work resumes
P3 Repeated-read v2 — after operational-store session identity is defined
P4 Issue #82/#83 close-out — both near-complete; AP-2/AP-3 probe-only, operational store next
```

Recently closed: #5, #37, #53, #54, #56, #57, #58, #3/#4/#43.
4266 tests passing.

## Area estimates

- **Core usable stack:** 99%
  Router mode fully correct: profile alias routing fixed, hold-open 503 eliminated,
  same-GGUF instant switch confirmed (<50ms), real model switch verified (26-45s).
  4266 tests pass. GPU detection, runtime crash detection, and compaction all working.
  Live smoke (`scripts/qz-live-smoke`) validates the end-to-end path reliably.
- **Config/model/profile correctness:** 95%
  qz.profiles.v1 is the active format. memory_domain is wired. Profile alias
  routing fully correct — symlinks load by real GGUF, forward by backend_id.
  Broad config/var cleanup still deferred.
- **Streaming reliability:** 85%
  #37 stream seam closed. StreamHopState + StreamRunState + 6 pure helpers.
  Remaining side-effect code bounded in place by design.
- **Tool handling:** 96%
  Deterministic intercept layer live: sandbox escalation, network escalation, E-1 field
  correction, AP-1/AP-1b coercion, AP-4 advisory, CorrectionTracker. Live-tested and
  measurably reducing Codex error counts. Repeated-read v2 blocked on SQLite.
- **Observability/status:** 90%
  VRAM telemetry live in qz-top (#6 closed). Provenance-labelled panel with
  calibrated MODEL_RUNTIME, MODEL_FILE provenance, KV_ALLOC from runtime budget.
  Recovery system fully operational. Backend allocator metrics (#52, upstream-blocked) remain.
- **LLM signal system:** 78%
  Reasoning-effort prompts simplified. Compaction bridge delivered and now
  working end-to-end: LLM v3 (anchored summary, survival-weighted) confirmed
  live; survival scorer generalised to 10-repo corpus; schema redesigned.
  Repeated-read v1 done; repeated-read v2 blocked on SQLite.
- **State/memory substrate:** 88%
  BrainCaseDB proven (#53/#54 closed). Full Limbicore session stack now live:
  impaction/percolate session tools, memory manager orchestrator, management pressure
  auto-trigger, powernap intra-session recall. No automatic ingestion; all writes
  are explicit tool calls.
  Remaining: operational-state store (#51/#46 — Slice B-impl next).
- **Docs/tests/replay:** 97%
  Docs refreshed post-stabilisation. Active task hierarchy and progress snapshot
  are current. Intercept contract and research docs are current.
- **Packaging/architecture:** 35%
  Unchanged. Split proxy/package/backend adapter work remains later.

## Current blockers and sequencing

### P1: BrainCase Limbicore session stack (#82)

Status:

```text
Near-complete. Steps 1–5 all shipped:
  Step 1-3: DB access tracking, temporal metrics, write executors — done.
  Session interface: braincase.impaction + braincase.percolate — done.
  Orchestrator shell + pressure management: management pressure, powernap,
    post-compaction auto-trigger — done.
  Memory manager v0 prompt: HSM-informed — done.
  bc_challenge, bc_read, bc_search: review interface — done.
  BrainCase always on, memory_domain from model config — done.
Issue #82 remains open; close after a live session confirms the full path
works end-to-end without regression.
```

### P2: Deterministic intercept (#83)

Status:

```text
Core patterns all implemented. Live-tested, error counts measurably reduced.
  I1 exec sandbox: ✅  I2 JSON fence: ✅  I3 diff headers: ✅
  I4 coerce paths: ✅  I5 CorrectionTracker: ✅
  AP-1/AP-1b: ✅  AP-4 advisory: ✅  E-1 field correction: ✅
  Network proxy blocks: ✅
  AP-2/AP-3: probe-only; zero live occurrences — no intercept yet.
Issue #83 remains open for AP-2/AP-3 monitoring.
```

### P3: config/var/script cleanup (#5)

Status:

```text
Safe any time. Good incremental work between larger features.
Focus: /qz/config/effective coverage, prompt-file warnings, catalog generation.
```

### P4: operational-state persistence (#51/#46)

Status:

```text
Slice A-design + A.2-correction complete. Slice B-impl: create
qz_operational_store.py (schema_meta/runtime_events/runtime_facts only).
BrainCaseDB is NOT the target for operational state.
```

## Immediate next priorities

1. **Live session validation of Limbicore stack** — run a real Codex session with BrainCase always-on; confirm impaction/percolate/powernap work end-to-end without regression. Close #82 if clean.
2. **#46 Slice B-impl** — create `qz_operational_store.py`. A-design + A.2-correction complete.
3. **#83 AP-2/AP-3 monitoring** — keep probes live; implement only when live evidence shows frequency warrants it.

## Remaining big rocks

1. ~~Fix pre-existing test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done.
3. ~~Config/user/runtime cleanup immediate tier~~ — done.
4. ~~Generic tool coercion system~~ — done.
5. ~~memory_domain config plumbing~~ — done (PRs #24/#25).
6. ~~qz.profiles.v1 active config + profiles/*.json loader~~ — done (PR #27).
7. ~~Simplified reasoning-effort prompts~~ — done (PR #30).
8. ~~Sandbox/tool-failure telemetry and harness guidance~~ — done (PRs #31–#34).
9. ~~Live stack smoke script~~ — done (PR #36).
10. ~~Backend control-plane audit~~ — done (#44).
11. ~~Backend service/recovery system~~ — done (#47-#50); six trigger actions operational.
12. ~~VRAM telemetry~~ — done (#6 closed); provenance-labelled panel live.
13. ~~Compaction/stream hang watchdog~~ — done (#40 closed); watchdog fallbacks live.
14. ~~BrainCase memory tool API~~ — done (#53 closed); render/recall/write_candidate live.
15. ~~BrainCase retention/lifetime policy~~ — done (#54 closed); operator prune live.
16. ~~Repeated-read v1 advisory signal~~ — done (#3/#4/#43 closed); qz_file_signal.py live.
17. ~~Stream seam extraction~~ — done (#37 closed, Slices 1–2J); StreamHopState + StreamRunState + 6 pure helpers.
18. ~~Generated artifact migration~~ — done (#56 closed); A1/A2/A3 under var/generated/.
19. ~~Router mode model switching~~ — done (2026-05-29); unload→load HTTP, one model at a time, selection persists.
20. ~~GPU detection false positive (#79)~~ — done (2026-05-29); child log filtering, port-scoped post-load check.
21. ~~Runtime crash detection (#80)~~ — done (2026-05-29); inventory delta tracking, 120s backoff, actionable messages.
22. ~~LLM v3 compaction~~ — done (2026-05-29); correct model backend_id, survival scorer generalised, schema redesigned.
23. ~~MTP draft speculation~~ — done (2026-05-29); models-preset.ini, confirmed on IQ4_XS at 10,16 split.
24. ~~BrainCase Limbicore session stack (#82)~~ — done (2026-05-31); impaction/percolate, orchestrator, management pressure, powernap.
25. ~~Deterministic intercept layer (#83)~~ — done (2026-05-31); sandbox/network escalation, AP-1/AP-1b/AP-4, E-1, CorrectionTracker.
26. ~~CUDA peer access fallback~~ — done (2026-06-03); host-staged cross-GPU copy for non-NVLink RTX 3080 + V100. Fixes `cudaMemcpyPeerAsync`→abort during MUL_MAT with tensor split.
27. ~~qwen3moe + mistral3 architecture validation~~ — done (2026-06-03); both load at 256K with turbo3 on dual-GPU 10,16. Previous "KV cache allocation fails" was testing without turbo3.
28. Streaming reliability — structurally improved; side-effect residual bounded in place by design.
27. LLM signal system — repeated-read v1 done; repeated-read v2 blocked on SQLite.
28. Phase 1 SQLite substrate — parked (#2); BrainCaseDB proven but operational store TBD.
29. qz-write-runtime-state replacement (#46) — OperationalStore Slice B+C (next concrete work).
30. Recovery/backoff state persistence (#51) — needs reframing; backoff/cooldown persistence NOT wanted.
31. Split proxy into a conventional Python package — later.
32. Add backend adapter boundary — later.
33. Later: MCP/app bridge, search packet mode, redaction, run grouping, rendered
    state packets, roleplay/HSM-specific renderers.
