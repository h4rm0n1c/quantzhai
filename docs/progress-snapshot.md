# QuantZhai Progress Snapshot

Last updated: 2026-05-11 (session 2).

This is a periodic high-level progress note. Use it when someone asks "where are
we overall?" without needing to reread every roadmap.

## Overall

Current estimate: **82% through stabilisation for the local Codex + Qwen goal**.

A large session. The tool handling layer is substantially more reliable and
self-correcting. A new signal system concept is underway that changes how the
proxy thinks about its relationship to the model.

## Area Estimates

- **Core usable stack:** 88%
  Suite is 256/256 green. All known test failures fixed.
- **Config/model/profile correctness:** 84%
  Unchanged from prior snapshot. var/ layout restructure deferred.
- **Streaming reliability:** 80%
  Fallback message streaming fixed (was terminal-event-only, now incremental
  SSE before response.completed). Web_search in-progress relay confirmed
  correct via live capture — no gap there. TUI long-running validation and
  profile preset tuning remain open.
- **Tool handling:** 87%
  Major session. Generic tool coercion system implemented: ToolCoercionResult
  interface, coerce() on all adapters, dropped-tool error injection, unknown-
  tool error injection, registry-level dispatch. apply_patch and web_search
  both implement coerce(). Informative microcompaction placeholders preserve
  success/failure signal for old tool outputs (was opaque "dropped" message).
  Live smoke confirmed coercion fires and model recovers on real Qwen output.
  Remaining: compaction bridge (what Codex sends vs what Qwen on llama.cpp
  can use), conversation history integrity audit.
- **Observability/status:** 70%
  Unchanged. VRAM backend telemetry still open.
- **Docs/tests/replay:** 91%
  Tool coercion design doc. LLM signal system doc (exploratory). Compaction
  bridge plan. Conversation history audit plan. 256 tests green.
- **Packaging/architecture:** 35%
  Unchanged.

## New concept: LLM Signal System

A new area taking shape. The coercion system is its first concrete piece. The
broader idea: the proxy should be a faithful information relay — preserving and
injecting signals the model needs to self-correct and self-regulate — rather
than a selective filter that silently discards information.

Two signal categories:
- **Quality signals** (reactive): tool errors, execution failures, compaction
  context. Coercion system and informative placeholders are the first two.
- **Self-management signals** (proactive): hop budget, context pressure,
  backend health. Designed but not yet implemented.

Signal format (how to inject — system prompt, in-turn message, function_call_
output-style) and model-specific response characteristics (Qwen3.6 MoE,
reasoning channel, meta-instruction following) need empirical validation
before implementation. See `docs/llm-signal-system.md`.

QZSTATE is a separate experiment and is not part of this system.

## Remaining Big Rocks

1. ~~Fix pre-existing test failures~~ — done.
2. ~~Tool lifecycle boundary cleanup~~ — done.
3. ~~Config/user/runtime cleanup (immediate tier)~~ — done.
4. ~~Generic tool coercion system~~ — done.
5. Streaming reliability — in progress. Fallback streaming fixed.
   Open: long-running TUI validation, profile preset tuning.
6. LLM signal system — **new, in progress**. Coercion and informative
   compaction done. Next: conversation history audit (captures needed),
   research pass (OpenClaude, ReAct, Qwen-specific), then hop budget
   and context pressure implementation.
7. Compaction bridge — needs audit and research before design.
   See `docs/compaction-bridge-plan.md`.
8. Profile eval framework — prompt set and tool-use extraction.
   See `docs/profile-eval-plan.md`.
9. Split proxy into a conventional Python package.
10. Add backend adapter boundary.
11. Later: MCP/app bridge, search packet mode, redaction, run grouping.
