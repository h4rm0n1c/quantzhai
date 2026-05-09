# QuantZhai Progress Snapshot

Last updated: 2026-05-09.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **67% through stabilisation for the local Codex + Qwen goal**.

QuantZhai is past fragile prototype state. It is a usable beta with hardening in
progress.

## Area Estimates

- **Core usable stack:** 85%
  `qz-up`, proxy, model routing, profiles, prompt policy, and local Codex path
  are functional.
- **Config/model/profile correctness:** 75%
  Invalid profile/backend handling is fixed. Config layout is partly cleaned.
  Some legacy/user/config cleanup remains.
- **Streaming reliability:** 76%
  SSE timing, terminal handling, reasoning-only/artifact aborts, client
  disconnects, request captures, and tool-call buffering are in place. Remaining
  risk is hard edge cases and live long-running TUI behavior. Answer-delta
  ordering and private tool-call timeout aborts now have direct regression
  coverage.
- **Tool handling:** 68%
  `web_search` proxy-local path works. `apply_patch` protocol adapter works,
  including move/rename. Generic registry/lifecycle boundary is much better.
  Proxy-local lifecycle event chunks are now registry-owned, and a test-only
  proxy-local executor proves the streaming and non-streaming registry path is
  not web_search-only. Still not one complete state machine; no
  MCP/shell/code/computer proxy runtime.
- **Observability/status:** 65%
  `/qz/status`, telemetry, `qz-top`, and `qz-thoughts` are much better. Codex
  `/status` has catalog metadata plus terminal usage working. Live progress
  richness is not yet fully comparable to hosted Codex.
- **Docs/tests/replay:** 82%
  Strong docs, golden fixtures, smokes, request captures, and an
  evidence-backed Responses stream/tool state table with coverage matrix. Keep
  reconciling docs as code moves.
- **Packaging/architecture:** 35%
  Still script/proxy-file shaped. Conventional Python package split and backend
  adapter boundary are not done.

## Remaining Big Rocks

1. Keep tightening generic tool lifecycle/state-machine boundary.
2. Split proxy into a conventional Python package.
3. Add backend adapter boundary.
4. Tighten config/user/runtime cleanup.
5. Add more live smokes for long-running tool/progress/status behavior.
6. Later: MCP/app bridge, search packet mode, stronger redaction/run grouping.
