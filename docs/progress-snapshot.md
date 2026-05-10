# QuantZhai Progress Snapshot

Last updated: 2026-05-11.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **76% through stabilisation for the local Codex + Qwen goal**.

A full config/data-path audit was completed this session and four concrete fixes
landed from it. The Codex catalog generator was extracted from an untestable bash
heredoc into a proper Python module with 41 tests. Config contract work is in
meaningful progress.

## Area Estimates

- **Core usable stack:** 88%
  `qz-up`, proxy, model routing, profiles, prompt policy, and local Codex path
  are functional. All known test failures are fixed; suite is 250/250 green.
- **Config/model/profile correctness:** 82%
  Audit of all 10 data paths complete (`docs/config-data-path-audit.md`).
  State write failures now emit telemetry (F1). `var/model-overrides.json`
  fallback removed; `config/user/` is the sole user override location (F9).
  Codex catalog generation extracted from bash heredoc to `proxy/qz_codex_catalog.py`
  with full test coverage (F8). Empty-system-prompt warning added to
  `/qz/status` and catalog launch (F7). Capture pruning (F10) and the broader
  config layout restructure from `docs/edge-case-config-contract-plan.md` remain
  open. Catalog generator still has its own copy of manifest/prompt loading logic
  (known second-truth; deferred to Phase 2 of F8).
- **Streaming reliability:** 76%
  Unchanged. Hard edge cases and live long-running TUI behaviour remain the risk.
- **Tool handling:** 78%
  Unchanged. Compaction drop-type set now derived from the tool registry rather
  than hardcoded. No new tools added.
- **Observability/status:** 67%
  `/qz/status` now includes `prompt.prompt_empty` and `prompt.disabled` fields.
  Remaining gap: live progress richness not yet comparable to hosted Codex.
- **Docs/tests/replay:** 88%
  250 unit tests, all green. 41 new tests for `qz_codex_catalog.py`. Config
  data-path audit doc added. Remaining gap: no tests for the Codex catalog
  generator's prompt-loading logic against real config fixtures.
- **Packaging/architecture:** 35%
  Unchanged. Python package split and backend adapter boundary not started.

## Remaining Big Rocks

1. ~~Fix the two pre-existing turn-harness test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done (registry drop-types).
3. Config/user/runtime cleanup — **in progress**. Audit done, F1/F7/F8/F9
   landed. Still open: F10 (capture pruning), the broader config layout
   restructure, and Phase 2 of F8 (wire catalog generator to proxy modules).
4. Split proxy into a conventional Python package.
5. Add backend adapter boundary.
6. Add more live smokes for long-running tool/progress/status behavior.
7. Later: MCP/app bridge, search packet mode, stronger redaction/run grouping.
