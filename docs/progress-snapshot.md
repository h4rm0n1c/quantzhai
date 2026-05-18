# QuantZhai Progress Snapshot

Last updated: 2026-05-18 (post-BrainCase/#37-helper-run stocktake).

See `docs/current-stocktake.md` for the full point-in-time state summary.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without rereading every roadmap.

## Overall

Current estimate: **93% through stabilisation for the local Codex + Qwen goal**.

The 2026-05-15 run added: VRAM telemetry panel live in qz-top with provenance
labels (#6, closed), full backend recovery system (#47-#50, closed), control-plane
audit (#44, closed), telemetry doctrine (docs/patterns/provenance-telemetry.md).

The 2026-05-14 run added: qz.profiles.v1, memory_domain plumbing, simplified
prompts, sandbox/tool-failure telemetry, live stack smoke.

The current risk remains the state substrate: SQLite Phase 1 has not been
implemented. The VRAM and recovery work is complete from QuantZhai's side.

Control sheet:

```text
docs/current-task-hierarchy.md
docs/current-stocktake.md
```

Current strategic direction:

```text
P1 #37 Stream seam extraction (Slices 1–2F.1 complete) — pause before next seam
P2 Config/var/script cleanup (#5) — Slices A–B.1 complete; staleness design slice next
P3 #51/#46 operational-state persistence (deferred until store decision)
P4 Search config split (#39) — when search work resumes
```

BrainCase feature work paused: #53 and #54 are closed.
Repeated-read v1: COMPLETE (#3/#4/#43 closed; qz_file_signal.py live).
2465 tests passing.

## Area estimates

- **Core usable stack:** 94%
  Known-good local flow. Suite is green at 2465 tests.
  Live smoke (`scripts/qz-live-smoke`) validates the end-to-end path reliably.
- **Config/model/profile correctness:** 89%
  qz.profiles.v1 is the active format. memory_domain is wired from profile
  config through to request context. Broad config/var cleanup is still deferred.
- **Streaming reliability:** 81%
  Streaming/tool lifecycle work is substantially improved. Remaining work is
  long-running TUI validation and edge-case relay polish.
- **Tool handling:** 91%
  Sandbox/tool-failure telemetry landed (Slice 1 escalation, Slice 2 native
  tool-output classifier, harness guidance). Repeated-read signalling is planned
  but not yet implemented.
- **Observability/status:** 90%
  VRAM telemetry live in qz-top (#6 closed). Provenance-labelled panel with
  calibrated MODEL_RUNTIME, MODEL_FILE provenance, KV_ALLOC from runtime budget.
  Recovery system fully operational. Compaction/stream hang watchdog (#40) and
  backend allocator metrics (#52, upstream-blocked) remain.
- **LLM signal system:** 72%
  Reasoning-effort prompts simplified. Hop/context pressure signals, compaction
  bridge, and profile eval work are delivered. Repeated-read v1 plan is approved;
  not yet implemented.
- **State/memory substrate:** 75%
  BrainCaseDB is the first concrete LimbiCore technology (#53/#54 closed).
  render/recall/write_candidate tools live; operator review and retention CLI live.
  Retention policy enforced via operator prune. No automatic ingestion.
  Remaining: operational-state store (#51/#46 deferred), #37 stream seam.
- **Docs/tests/replay:** 95%
  Docs refreshed post-stabilisation. Active task hierarchy and progress snapshot
  are current. Runtime observability notes describe the live stack smoke and
  sandbox telemetry paths.
- **Packaging/architecture:** 35%
  Unchanged. Split proxy/package/backend adapter work remains later.

## Current blockers and sequencing

### P1: BrainCase memory tool API

Status:

```text
Slices A–I.1 complete. #53 CLOSED after close-out audit passed.
  BrainCaseDB is the first concrete LimbiCore memory substrate, proven in QuantZhai.
  LimbiCore is the broader umbrella; BrainCaseDB is its first component.
  Exposed tools: braincase.render, braincase.recall, braincase.write_candidate.
  Unexposed: braincase.write/update/search/inspect/promote_candidate.
  Operator CLI: scripts/qz-braincase-review (list/inspect/promote/reject).
  Smoke: scripts/qz-braincase-smoke (12/12 PASS). 2239 tests passing.
  #54 retention/lifetime policy — Slices A–D complete. CLOSED after audit.
  See docs/braincase-memory-tool-api.md and docs/braincase-architecture-landscape-and-scope.md.
```

Scope:

```text
Tool-mediated memory plane: LLM + harness -> memory tools -> deterministic helpers
-> BrainCaseDB / indexes -> renderers -> scoped model-visible memory packets.
Do not add automatic ingestion. No request/session/turn logging.
No model-visible memory by default.
```

### P2: config/var/script cleanup (#5)

Status:

```text
Safe any time. Good incremental work between larger features.
Focus: /qz/config/effective coverage, prompt-file warnings, catalog generation.
```

### P3: telemetry filter ergonomics

Status:

```text
Low priority. Implement when the noisy-window problem recurs in practice.
```

### P4: operational-state persistence (#51/#46)

Status:

```text
Deferred. BrainCaseDB is NOT the target. Needs operational-store decision first.
```

## Immediate next priorities

1. **#37 design micro-slice** — define boundary for next delicate stream seam before coding.
2. **Config/var cleanup (#5)** — safe any time; good between feature sprints.
3. **#51/#46** — deferred until operational-store decision.

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
17. ~~Stream seam extraction Slices 1–2E.1~~ — done (#37 paused); 4 pure helpers extracted.
18. Streaming reliability — mostly improved; #37 delicate seams remain (design-first).
19. LLM signal system — repeated-read v1 done; repeated-read v2 blocked on SQLite.
20. Phase 1 SQLite substrate — parked (#2); BrainCaseDB proven but operational store TBD.
21. Recovery/backoff state persistence (#51) — after operational-store decision.
22. Split proxy into a conventional Python package — later.
23. Add backend adapter boundary — later.
24. Later: MCP/app bridge, search packet mode, redaction, run grouping, rendered
    state packets, roleplay/HSM-specific renderers.
