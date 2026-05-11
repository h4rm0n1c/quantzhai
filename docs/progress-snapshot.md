# QuantZhai Progress Snapshot

Last updated: 2026-05-11 (session 4, end).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **88% through stabilisation for the local Codex + Qwen goal**.

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
- **LLM signal system:** 65%
  Coercion error feedback, informative compaction placeholders, reasoning-budget-
  message, carry-forward all live. Hop budget and context pressure signals live.
  Compaction bridge delivered. Telemetry bus capacity fixed.
  Profile eval framework complete: 14-prompt benchmark across 4 effort levels,
  Qwen self-report interrogation (3 rounds, including negative space), effort
  prompts rewritten with explicit tool budgets and unified sampling. Medium
  well-behaved on 13/14 prompts. Open-ended exploration tasks structurally
  resist effort caps — documented in `docs/benchmark-findings-effort-tuning.md`
  with open questions for future stack work.
  Next: config/var layout cleanup; redundant re-read and workspace anchoring
  issues documented as future considerations.
- **Docs/tests/replay:** 93%
  Compaction bridge plan and telemetry bus bug note updated to reflect delivery.
  293/293 tests green.
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

1. **Config/var layout + script cleanup** — Phase 3 of master plan. `var/`
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
