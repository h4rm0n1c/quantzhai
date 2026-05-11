# QuantZhai Progress Snapshot

Last updated: 2026-05-11 (session 3, end).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **84% through stabilisation for the local Codex + Qwen goal**.

Two long sessions. The proxy is substantially more capable and more honest about
what it knows. The LLM signal system is a new area taking shape — coercion,
informative history, and early reasoning signals are all live.

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
- **LLM signal system:** 40%
  Coercion error feedback, informative compaction placeholders, reasoning-budget-
  message, carry-forward all live. Hop budget signal and context pressure signal
  now implemented (QZ_HOP_BUDGET_SIGNAL_THRESHOLD=3,
  QZ_CONTEXT_PRESSURE_SIGNAL_THRESHOLD=0.8). Both are ephemeral per-hop plain-
  instruction messages injected only when relevant; apply_patch and public tools
  are unaffected. Usage drain added so context pressure has accurate token counts
  from proxy-local hop completions. Live-smoked: single web_search hop correctly
  emits no signal (5 hops remaining > threshold). 263/263 tests green.
  Next: empirical A/B format testing; compaction bridge research.
- **Docs/tests/replay:** 92%
  Signal system doc, research findings, conversation history audit plan,
  compaction bridge plan all committed. Conversation history audit complete —
  tool filter has no bugs; reasoning correctly dropped per ReAct.
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

## Remaining Big Rocks

1. ~~Fix pre-existing test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done.
3. ~~Config/user/runtime cleanup (immediate tier)~~ — done.
4. ~~Generic tool coercion system~~ — done.
5. Streaming reliability — in progress. Still open: long-running TUI
   validation, profile preset tuning (blocked on profile eval framework).
6. LLM signal system — **in progress**. Hop budget + context pressure signals
   live. Next: empirical A/B format testing; compaction bridge.
7. Compaction bridge — needs capture audit + OpenAI format research first.
8. Profile eval framework — prompt set (docs/profile-eval-plan.md).
9. Split proxy into a conventional Python package.
10. Add backend adapter boundary.
11. Later: MCP/app bridge, search packet mode, redaction, run grouping.
