# QuantZhai Progress Snapshot

Last updated: 2026-05-11.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **78% through stabilisation for the local Codex + Qwen goal**.

Config contract work is complete for the immediate tier. Streaming reliability
has concrete fixes. Observability has meaningful improvements. Profile eval
framework is planned and documented.

## Area Estimates

- **Core usable stack:** 88%
  Suite is 241/241 green. All known test failures fixed.
- **Config/model/profile correctness:** 84%
  All immediate audit findings resolved: F1 (state write failures emit
  telemetry), F7 (empty-prompt warning at launch and in /qz/status), F8
  (Codex catalog generator extracted from bash heredoc and wired to proxy
  modules, 41 tests), F9 (var/model-overrides.json fallback removed), F10
  (qz-capture-prune script). var/ layout restructure and broader config layering
  remain open but are not blocking anything.
- **Streaming reliability:** 79%
  Fallback messages on reasoning-only aborts now stream incrementally as proper
  SSE events before response.completed (was only in the terminal event).
  Live capture confirmed web_search in-progress relay is already correct — no
  code change needed. Long-running TUI validation and profile preset tuning
  remain open.
- **Tool handling:** 79%
  Compaction drop-type set now derived from the tool registry instead of
  hardcoded web_search_call strings. No new tools added.
- **Observability/status:** 70%
  /qz/status prompt fields added. qz-top TPS display overhauled: raw tok/s
  with no SI prefix, rolling 5-sample average for "latest", coherent
  latest-request TOKENS/TIME columns, corrected total_tokens computation.
  VRAM backend telemetry and profile eval framework remain open.
- **Docs/tests/replay:** 89%
  Known blind spots documented in proxy-capability-roadmap.md. Profile eval
  plan written (docs/profile-eval-plan.md). Config data-path audit complete.
- **Packaging/architecture:** 35%
  Unchanged. Python package split and backend adapter boundary not started.

## Remaining Big Rocks

1. ~~Fix pre-existing test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done.
3. ~~Config/user/runtime cleanup (immediate tier)~~ — done (F1/F7/F8/F9/F10).
4. Streaming reliability — **in progress**. Fallback streaming fixed. Still
   open: long-running TUI validation (streaming 3), profile preset tuning
   (streaming 4, blocked on observability 2).
5. Observability — **in progress**. TPS display fixed. Still open: VRAM
   backend telemetry, profile eval framework (docs/profile-eval-plan.md).
6. Split proxy into a conventional Python package.
7. Add backend adapter boundary.
8. Live smokes for long-running tool/progress/status behavior.
9. Later: MCP/app bridge, search packet mode, redaction, run grouping.
