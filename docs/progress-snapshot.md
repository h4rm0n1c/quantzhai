# QuantZhai Progress Snapshot

Last updated: 2026-05-15 (post-VRAM/recovery stabilisation).

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
P1 BrainCase memory tool API — Slice F harness/tool exposure (next)
P2 repeated-read v1 advisory signal
P3 telemetry filter ergonomics / qz-live-smoke refinements
P4 config/var/script ownership cleanup
```

## Area estimates

- **Core usable stack:** 93%
  Known-good local flow exists. Suite is green at 1442 tests.
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
- **State/memory substrate:** 30%
  Parser/context boundary exists and is source-grounded. `memory_domain` is
  wired from profile config and resolves to isolated when not explicitly set.
  SQLite Phase 1 is planned but not implemented.
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
  Follow-up: #54 retention/lifetime policy (QuantZhai scope, not HSM/LimbiCore expansion).
  See docs/braincase-memory-tool-api.md and docs/braincase-architecture-landscape-and-scope.md.
```

Scope:

```text
Tool-mediated memory plane: LLM + harness -> memory tools -> deterministic helpers
-> BrainCaseDB / indexes -> renderers -> scoped model-visible memory packets.
Do not add automatic ingestion. No request/session/turn logging.
No model-visible memory by default.
```

### P2: repeated-read signal

Status:

```text
V1 plan approved. Not blocked; integration cleaner after P1 SQLite.
V2 blocked on P1 same-scope SQLite facts.
```

Scope:

```text
V1 is advisory, stateless, and input-history-seeded from body["input"].
```

### P3: telemetry filter ergonomics

Status:

```text
Low priority. Implement when the noisy-window problem recurs in practice.
```

### P4: config/var/script cleanup

Status:

```text
Still needed, but do not preempt the state spine unless a live breakage demands it.
```

## Immediate next priorities

1. **#40: Compaction/stream hang watchdog** — prevents client stuck states.
2. **#2/#53: BrainCase memory tool API — Slice F harness wiring** (see docs/braincase-memory-tool-api.md).
3. **#51: Promote recovery/backoff state to SQLite** (after #2).
4. **Implement repeated-read v1 advisory signal** (can start any time).

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
13. Compaction/stream hang watchdog — next (#40).
14. Streaming reliability — mostly improved; long-running TUI and edge cases remain.
15. LLM signal system — in progress; repeated-read v1 is next practical signal.
16. Phase 1 SQLite substrate — high priority (#2).
17. Recovery/backoff state persistence (#51) — after #2.
18. Split proxy into a conventional Python package — later.
19. Add backend adapter boundary — later.
20. Later: MCP/app bridge, search packet mode, redaction, run grouping, rendered
    state packets, roleplay/HSM-specific renderers.
