# QuantZhai Progress Snapshot

Last updated: 2026-05-14 (post-stabilisation pass).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without rereading every roadmap.

## Overall

Current estimate: **91% through stabilisation for the local Codex + Qwen goal**.

The stabilisation run landed qz.profiles.v1 active config, memory_domain
plumbing, simplified reasoning-effort prompts, sandbox/tool-failure telemetry
with harness guidance, and a maintained live stack smoke script. The stack is
more testable and observable than before, and the agent guidance is tighter.

The current risk is no longer configuration or profile correctness — it is the
state substrate: SQLite Phase 1 has not been implemented, so memory_domain
resolves to isolated for every request and no durable operational facts are
stored. Everything else now depends on getting that substrate in cleanly.

Control sheet:

```text
docs/current-task-hierarchy.md
```

Current strategic direction:

```text
P1 Phase 1 SQLite operational substrate (next)
P2 repeated-read v1 advisory signal
P3 telemetry filter ergonomics / qz-live-smoke refinements
P4 config/var/script ownership cleanup
```

## Area estimates

- **Core usable stack:** 92%
  Known-good local flow exists. Suite is green at 545 tests.
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
- **Observability/status:** 78%
  Shared telemetry exists. sandbox/tool failure events are classified and rendered
  in qz-thoughts. VRAM backend allocation telemetry and some first-status
  correctness checks remain open.
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

### P1: Phase 1 SQLite operational substrate

Status:

```text
Next implementation target. memory_domain plumbing is done and unblocks this.
```

Scope:

```text
Store sessions, turns, requests, workspace candidates, resolved workspaces,
session/workspace bindings, identity conflicts, and request metadata summaries.
Optional/non-fatal only. No model-visible memory.
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

1. **Implement optional/non-fatal Phase 1 SQLite operational substrate.**
2. **Implement repeated-read v1 advisory signal (can start any time).**
3. **Continue config/var/script ownership cleanup after the state substrate is safe.**
4. **Add backend VRAM allocation telemetry and remaining monitor polish.**

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
10. Streaming reliability — mostly improved; long-running TUI and edge cases remain.
11. LLM signal system — in progress; repeated-read v1 is next practical signal.
12. Phase 1 SQLite substrate — next.
13. Split proxy into a conventional Python package — later.
14. Add backend adapter boundary — later.
15. Later: MCP/app bridge, search packet mode, redaction, run grouping, rendered
    state packets, roleplay/HSM-specific renderers.
