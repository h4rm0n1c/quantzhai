# QuantZhai Progress Snapshot

Last updated: 2026-05-10.

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **72% through stabilisation for the local Codex + Qwen goal**.

QuantZhai is a usable beta with meaningful hardening completed since the last
snapshot. The apply_patch protocol adapter is now reliable against real Qwen
output (evidence-backed: 8/8 create prompts succeed vs 0/5 before), and the
context default has been raised to 256k.

## Area Estimates

- **Core usable stack:** 87%
  `qz-up`, proxy, model routing, profiles, prompt policy, and local Codex path
  are functional. Default context raised to 256k. Default system prompt switched
  to `codex-core-qwenified.md`. Two pre-existing test failures in the turn-harness
  path are known but uninvestigated.
- **Config/model/profile correctness:** 75%
  Invalid profile/backend handling is fixed. Config layout is partly cleaned.
  Some legacy/user/config cleanup remains.
- **Streaming reliability:** 76%
  SSE timing, terminal handling, reasoning-only/artifact aborts, client
  disconnects, request captures, and tool-call buffering are in place. Remaining
  risk is hard edge cases and live long-running TUI behavior.
- **Tool handling:** 78%
  `web_search` proxy-local path works. `apply_patch` protocol adapter is now
  robust to the two dominant Qwen failure shapes (sibling `patch` field and bare
  operation), confirmed by 40-prompt two-session fuzz and 8/8 post-fix
  revalidation. 8 new golden fixtures from real Qwen output. Error feedback path
  replaced: broken dead-end assistant messages → partial Codex envelopes that
  surface specific V4A verifier errors the model can act on. Still not one
  complete tool state machine; no MCP/shell/code/computer proxy runtime.
- **Observability/status:** 65%
  `/qz/status`, telemetry, `qz-top`, and `qz-thoughts` are much better. Codex
  `/status` has catalog metadata plus terminal usage working. Live progress
  richness is not yet fully comparable to hosted Codex.
- **Docs/tests/replay:** 85%
  Strong docs, golden fixtures, smokes, request captures, and an
  evidence-backed Responses stream/tool state table. Added fuzz-driven methodology:
  capture-aware runner + extractor for apply_patch shapes, reusable for future
  tool-compatibility work. Two pre-existing test failures remain as known noise.
- **Packaging/architecture:** 35%
  Still script/proxy-file shaped. Conventional Python package split and backend
  adapter boundary are not done.

## Remaining Big Rocks

1. Fix the two pre-existing turn-harness test failures — they mask real regressions.
2. Keep tightening generic tool lifecycle/state-machine boundary.
3. Tighten config/user/runtime cleanup (see `docs/edge-case-config-contract-plan.md`).
4. Split proxy into a conventional Python package.
5. Add backend adapter boundary.
6. Add more live smokes for long-running tool/progress/status behavior.
7. Later: MCP/app bridge, search packet mode, stronger redaction/run grouping.
