# QuantZhai Progress Snapshot

Last updated: 2026-05-12 (resource-planning / task-hierarchy pass).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without rereading every roadmap.

## Overall

Current estimate: **88% through stabilisation for the local Codex + Qwen goal**.

The local Codex/Qwen stack is usable and heavily tested. The proxy now has strong
contracts around profiles, tool lifecycle, streaming, telemetry, compaction, and
request mutation safety. The current risk is no longer one obvious bug. The risk
is planning drift: many docs now exist, and implementation needs a short active
task hierarchy so agents do not keep rediscovering old decisions.

New control sheet:

```text
docs/current-task-hierarchy.md
```

Current strategic direction:

```text
memory_domain config plumbing first
Phase 1 SQLite operational substrate second
repeated-read v1 signal third or parallel after terminology is stable
config/var/script ownership cleanup after the state spine is safe
```

## Area estimates

- **Core usable stack:** 88%
  Known-good local flow exists. Prior suite checkpoints were green; rerun before
  implementation agents make code changes.
- **Config/model/profile correctness:** 84%
  Profile/catalog safety has improved, but memory_domain is not yet wired from
  explicit config. Broad config/var cleanup remains deferred until the state
  spine is safer.
- **Streaming reliability:** 80%
  Streaming/tool lifecycle work is substantially improved. Remaining work is
  long-running TUI validation and edge-case relay polish.
- **Tool handling:** 87%
  Generic tool coercion/registry work is live. apply_patch and web_search paths
  are now much better bounded. Repeated-read signalling is planned but not yet
  implemented.
- **Observability/status:** 70%
  Shared telemetry exists. VRAM backend allocation telemetry and some first-status
  correctness checks remain open.
- **LLM signal system:** 68%
  Reasoning-budget message, compact reasoning summary carry-forward, hop/context
  pressure signals, compaction bridge, and profile eval work are delivered or
  partly delivered. Benchmarking showed redundant re-reads and workspace
  orientation waste are real practical problems. Repeated-read v1 now has an
  approved implementation plan.
- **State/memory substrate:** 25%
  Parser/context boundary exists and is source-grounded. `memory_domain` still
  resolves to isolated in code. SQLite Phase 1 is planned but not implemented.
- **Docs/tests/replay:** 94%
  The docs are now indexed better and the active task hierarchy exists. Keep
  docs/current-architecture-authority.md and docs/current-task-hierarchy.md in
  sync when direction changes.
- **Packaging/architecture:** 35%
  Unchanged. Split proxy/package/backend adapter work remains later.

## Current blockers and sequencing

### P1: memory_domain config plumbing

Status:

```text
Next implementation target.
```

Why first:

```text
SQLite needs explicit memory-domain boundaries before any durable same-scope
state can be trusted. Missing memory_domain must remain isolated, and no tool,
profile, model, client, or prompt inference may grant memory access.
```

### P2: Phase 1 SQLite operational substrate

Status:

```text
Planned, blocked on P1.
```

Scope:

```text
Store sessions, turns, requests, workspace candidates, resolved workspaces,
session/workspace bindings, identity conflicts, and request metadata summaries.
Optional/non-fatal only. No model-visible memory.
```

### P3: repeated-read signal

Status:

```text
V1 plan approved. Parser-only prep can start any time; integration is cleaner
after P1. V2 is blocked on P2 same-scope SQLite facts.
```

Scope:

```text
V1 is advisory, stateless, and input-history-seeded from body["input"]. It warns
about repeated file reads but does not suppress execution blindly.
```

### P4: config/var/script cleanup

Status:

```text
Still needed, but do not let it preempt the memory-domain/state spine unless a
live breakage demands it.
```

## Immediate next priorities

1. **Implement explicit memory_domain config plumbing.**
2. **Implement optional/non-fatal Phase 1 SQLite operational substrate.**
3. **Implement repeated-read v1 signal or at least its parser/state tests.**
4. **Continue config/var/script ownership cleanup after the state substrate is safe.**
5. **Add backend VRAM allocation telemetry and remaining monitor polish.**

## Resource plan

Tonight / low-credit mode:

```text
DeepSeek/OpenCode:
  doc inventory, stale terminology search, parser-test drafts, issue drafts.

Gemini 9%:
  one contradiction review only.

ChatGPT/GitHub API:
  repo-level triage, docs updates, task hierarchy, implementation prompts.
```

After Codex/Claude refresh:

```text
Codex/Claude:
  P1 memory_domain config patch,
  P2 SQLite substrate patch,
  P3 repeated-read integration.
```

Do not spend refreshed premium agent cycles rediscovering the plan.

## Remaining big rocks

1. ~~Fix pre-existing test failures~~ — done in prior passes.
2. ~~Tool lifecycle boundary cleanup~~ — largely done.
3. ~~Config/user/runtime cleanup immediate tier~~ — done in prior passes.
4. ~~Generic tool coercion system~~ — done in prior passes.
5. Streaming reliability — mostly improved; long-running TUI and edge cases remain.
6. LLM signal system — in progress; repeated-read v1 is next practical signal.
7. ~~Compaction bridge~~ — delivered in prior passes.
8. Profile eval framework — delivered; findings captured in benchmark docs.
9. memory_domain config plumbing — next.
10. Phase 1 SQLite substrate — next after memory_domain.
11. Split proxy into a conventional Python package — later.
12. Add backend adapter boundary — later.
13. Later: MCP/app bridge, search packet mode, redaction, run grouping, rendered
    state packets, roleplay/HSM-specific renderers.
