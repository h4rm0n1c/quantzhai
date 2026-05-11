# QuantZhai Progress Snapshot

Last updated: 2026-05-11 (session 4, end).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **86% through stabilisation for the local Codex + Qwen goal**.

Three long sessions plus a fourth. The proxy is substantially more capable and
more honest about what it knows. The compaction bridge is now shipped and live-
smoked — Qwen correctly recalls compacted context in follow-up turns.

## Area Estimates

- **Core usable stack:** 88%
  Suite is 256/256 green.
- **Config/model/profile correctness:** 84%
  Unchanged. var/ layout restructure deferred.
- **Streaming reliability:** 80%
  Unchanged from prior.
- **Tool handling:** 87%
  Unchanged from prior.
- **Observability/status:** 70%
  Unchanged. VRAM backend telemetry still open.
- **LLM signal system:** 55%
  Coercion error feedback, informative compaction placeholders, reasoning-budget-
  message, carry-forward all live. Hop budget and context pressure signals live.
  Compaction bridge delivered: v2 blob format, auto-compaction trigger via
  `compact_threshold`, native blob passthrough, improved limits, 29 unit tests,
  live smoke (10/10, model recalled file names from compacted context).
  Next: empirical A/B format testing for signal formats; profile eval framework.
- **Docs/tests/replay:** 93%
  Compaction bridge plan updated to reflect delivery. 29 new unit tests +
  live integration smoke. Test suite: 292/292 green.
- **Packaging/architecture:** 35%
  Unchanged.

## What the research changed

The original signal priority was: hop budget → context pressure → backend errors.

Revised after research:
1. **Reasoning budget message** (done — `--reasoning-budget-message`)
2. **Compact reasoning summary carry-forward** (done — experimental flag)
3. **Hop budget as ephemeral in-turn message** — next implementation target
4. **Context pressure** — same pattern as hop budget
5. **Empirical validation** — fuzz the signal format question before building more

## Immediate next priorities (in order)

1. **Telemetry bus capacity** — per-request buffer (200 events) fills with
   `stream_event_timing` events and pushes out meaningful lifecycle events
   (`hop_budget_signal`, `tool_call_started`, etc.). Fix: raise capacity or
   give timing events a separate lower-priority buffer.
2. **Profile eval framework** — build the prompt test battery from
   `docs/profile-eval-plan.md`. Prerequisite for A/B testing signal formats
   and profile preset tuning.
3. **Config/var layout + script cleanup** — Phase 3 of master plan. `var/`
   restructure and script sprawl reduction. Housekeeping, long deferred.

## Remaining Big Rocks

1. ~~Fix pre-existing test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done.
3. ~~Config/user/runtime cleanup (immediate tier)~~ — done.
4. ~~Generic tool coercion system~~ — done.
5. Streaming reliability — in progress. Still open: long-running TUI
   validation, profile preset tuning (blocked on profile eval framework).
6. LLM signal system — **in progress**. Hop budget + context pressure signals
   live. Next: empirical A/B format testing; compaction bridge.
7. ~~Compaction bridge~~ — done. v2 format, auto-compaction trigger, live smoke.
8. Profile eval framework — prompt set (docs/profile-eval-plan.md).
9. Split proxy into a conventional Python package.
10. Add backend adapter boundary.
11. Later: MCP/app bridge, search packet mode, redaction, run grouping.
