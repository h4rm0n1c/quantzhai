# Proxy Capability Roadmap

Date: 2026-04-28

## Purpose

Map what the QuantZhai proxy currently covers for local Codex use, what is missing, and what needs to improve before the proxy becomes a more general local agent adapter.

This document is about the proxy specifically, not the Docker launcher, model build, or repo packaging.

## Current Job

The proxy sits between Codex and a local llama.cpp-compatible server. Its job is to make a local Qwen/TurboQuant backend look enough like the APIs Codex expects.

It currently covers:

- OpenAI-ish chat completions.
- OpenAI Responses-style requests.
- Local model aliases and reasoning budgets.
- Basic Ollama-compatible discovery endpoints used by Codex setup paths.
- Streaming adaptation.
- Local compaction.
- Profile-aware `web_search`.
- Capture files for debugging.

It is not yet a complete OpenAI API implementation, a complete Ollama implementation, or a general tool runtime.

## API Surface

Current endpoints:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/responses/compact`
- Ollama compatibility probes such as `/api/tags`, `/api/version`, `/api/ps`, `/api/pull`, and `/api/show`
- Raw fallback proxying for other GET/POST paths

What works well:

- Enough compatibility for the current local Codex workflow.
- Model metadata and setup probes keep Codex from failing early.
- The proxy can hide llama.cpp response quirks from Codex.

What is weak:

- The compatibility surface is empirical, based on what the current Codex flow needed.
- It is not a versioned contract.
- Ollama support is a shim, not full Ollama behavior.

Maturity: working beta.

## Model Aliases And Reasoning Budgets

Current behavior:

- Provides `QwenZhai-*` aliases.
- Keeps older `Qwen3.6Turbo-*` aliases for compatibility.
- Maps low, medium, high, and max profiles to `thinking_budget_tokens`.
- Applies practical defaults such as low temperature.

What works well:

- Simple local model names for Codex.
- Useful reasoning budget control without changing the backend manually.

What is weak:

- Model catalog and budget metadata are still mostly hardcoded.
- There is no formal config schema yet.

Maturity: stable enough for the current stack.

## Responses Adapter

Current behavior:

- Normalizes Responses input into upstream chat/completions-style payloads.
- Canonicalizes message roles and content.
- Drops historical reasoning and old tool-call artifacts that confuse the local model.
- Removes harness/meta blocks that should not be sent to Qwen.
- Normalizes supported tool declarations.

What works well:

- This is the core reason local Codex can run usefully.
- It cleans up a lot of traffic that would otherwise degrade local model behavior.

What is weak:

- The adapter is packed into one large Python file.
- The behavior needs fixture tests.
- Some compatibility choices are inferred from observed traffic instead of documented as a stable contract.

Maturity: useful beta.

## Streaming

Current behavior:

- Can pass through upstream SSE.
- Can transform reasoning visibility into raw, summary, or hidden modes.
- Can synthesize Responses-style stream events from non-streaming upstream responses.
- Emits local Codex rate-limit style events and headers.

What works well:

- Streaming is good enough for interactive local Codex sessions.
- Reasoning display control is useful when working with Qwen.

What is weak:

- Streaming transforms are fragile without replay fixtures.
- Fake rate-limit metadata satisfies client expectations but is not real accounting.
- Some event shapes may lag behind Codex/OpenAI changes.

Maturity: working beta, needs regression tests.

## Tool Handling

Current behavior:

- Normal function tools can pass through.
- `web_search` is implemented locally.
- Unsupported tools are dropped and recorded.
- Native and custom `apply_patch` declarations are translated into a model-friendly function schema.
- Valid model `apply_patch` function calls are translated back into native `apply_patch_call` items.
- Current Codex CLI custom `apply_patch` calls are translated back into `custom_tool_call` patch envelopes.
- `apply_patch_call_output` history is translated back into function-call output history for llama.cpp.

Missing or incomplete:

- `apply_patch` has not been tested yet with a live Qwen/TurboQuant model deciding and continuing from a real edit.
- No proxy-side patch executor exists.
- No shell/exec tool runtime.
- No computer-use tool runtime.
- No code interpreter runtime.
- No MCP/app tool bridge.
- No generic custom-tool execution framework.

What works well:

- Unsupported tool dropping is explicit enough to debug.
- `web_search` now has a real local implementation.
- The patch-tool protocol path is smoke-tested with fake upstreams and local Codex CLI.

What is weak:

- Tool handling is not yet a clean module or interface.
- Each tool path is too embedded in the proxy flow.
- There is no shared tool-call lifecycle for request normalization, execution, result injection, streaming, and capture.

Maturity:

- Function pass-through: partial.
- `web_search`: beta.
- `apply_patch`: alpha protocol adapter, smoke-tested.
- General tool runtime: missing.

## Search

Current behavior:

- Local `web_search` supports `search`, `open_page`, and `find_in_page`.
- SearXNG base URL is configurable.
- Policy-driven profiles exist for broad, coding, sysadmin, research, news, AI/model, and reference searches.
- Low-result fallback routing exists.
- The latest route is captured under `var/captures/latest-web-search-route.json`.

What works well:

- Search is now useful enough for normal local-agent work.
- Profile routing avoids treating every search like a coding search.
- Debug captures make routing decisions inspectable.

What is weak:

- Ranking, dedupe, and source scoring are first-pass.
- The search code should eventually leave the monolithic proxy file.
- Smoke tests are manual.

Maturity: good enough beta, parked for now.

## Compaction

Current behavior:

- Implements local `/v1/responses/compact`.
- Produces local compaction records using a `localcmp:v1:` prefix.
- Microcompacts old tool output to keep context manageable.

What works well:

- Practical for long local sessions.
- Keeps Codex moving without depending on hosted compaction.

What is weak:

- The field name may imply encryption, but the local payload is base64-encoded JSON, not cryptographic encryption.
- Format compatibility needs tests.

Maturity: useful beta.

## Observability

Current behavior:

- Writes request, forwarded request, upstream response, dropped tools, and search route captures under `var/captures`.
- Runtime state and logs are intended to live under `var/`.
- `var/` is ignored by git.

What works well:

- Captures have already made smoke testing and debugging much faster.
- Keeping runtime state out of tracked files is the right default.

What is weak:

- Most captures are latest-only and get overwritten.
- There is no redaction layer.
- There is no structured run ID across all captures yet.

Maturity: useful but ad hoc.

## Safety Boundary

Current behavior:

- QuantZhai mostly relies on Codex and the host environment for approval and workspace safety.
- The proxy does not currently execute filesystem-mutating tools.
- Docker isolates the model server path.

What works well:

- The current boundary is acceptable while the proxy is mainly an adapter.
- Avoiding local patch execution avoids a large class of path and permission risks.

What is weak:

- If QuantZhai starts executing tools directly, it needs explicit workspace-root validation, path canonicalization, redaction, and deny rules.
- The current proxy structure does not yet make those safety checks reusable.

Maturity: acceptable for adapter behavior; not ready for proxy-side filesystem tools.

## Backend Abstraction

Current behavior:

- The working backend is the current llama.cpp/TurboQuant server path.
- Fox is documented as a possible future backend only after parity with `thetom/llama.cpp-turboquant`.

What works well:

- The current backend works with the known local Qwen GGUF and Docker image.

What is weak:

- There is no formal backend adapter interface yet.
- llama.cpp assumptions are mixed into the proxy implementation.

Maturity: working single-backend implementation.

## What QuantZhai Does Well

- Makes local Codex usable against a llama.cpp/TurboQuant backend.
- Hides enough OpenAI/Responses/Ollama shape mismatch to keep the agent running.
- Provides practical model aliases and reasoning budgets.
- Keeps search local and configurable.
- Captures enough state to debug real failures.
- Keeps runtime data out of git by default.

## What QuantZhai Does Badly

- Too much logic lives in `proxy/quantzhai_proxy.py`.
- Too much compatibility is untested.
- Tool handling is not generalized.
- Streaming and Responses behavior need golden fixtures.
- Capture files are useful but not systematic.
- Safety boundaries are not strong enough for proxy-side filesystem tools.
- Config is still more script-shaped than product-shaped.

## Maturity Snapshot

Stable enough for current use:

- Launch environment.
- Local model aliasing.
- Basic model discovery.
- Chat completions proxying.
- Current Codex local workflow.

Working beta:

- Responses adapter.
- Streaming adapter.
- Local compaction.
- Profile-aware search.
- Capture-based debugging.

Partial:

- Ollama compatibility.
- Function-tool passthrough.
- Rate-limit compatibility metadata.
- Tool normalization.

Alpha:

- `apply_patch` protocol adapter.

Missing:

- General tool runtime.
- Proxy-side shell/code/computer tool support.
- MCP/app bridge.
- Formal backend abstraction.
- Automated compatibility test suite.
- Packaged Python module structure.

## Near-Term Roadmap

1. Run a live Qwen/TurboQuant patch workflow and capture whether it emits valid patch operations.
2. Extract tool handling into a small internal boundary.
3. Add golden tests for Responses normalization and streaming.
4. Split `proxy/quantzhai_proxy.py` into a conventional Python package.
5. Add a backend adapter boundary before Fox or Rust work.
6. Revisit search once the proxy shape is easier to test.

## Open Questions

- Should unsupported tools be dropped, converted into no-op tool messages, or surfaced as model-visible limitations?
- Should captures become run-scoped instead of latest-only?
- How much config should move from scripts into tracked sample config?
- Should QuantZhai ever execute filesystem tools directly, or should it always delegate writes back to Codex?
