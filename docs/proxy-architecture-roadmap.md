# Proxy Architecture Roadmap

Date: 2026-04-30

## Goal

Turn the current Python proxy into a conventional, testable proxy/arbitrator/adapter package before considering a Rust port.

The near-term target is not a rewrite. It is to break `proxy/quantzhai_proxy.py` into focused units that can be tested without launching Docker, Codex, or a live model server.

## Current Shape

- `proxy/quantzhai_proxy.py` is the working implementation.
- It owns HTTP handling, OpenAI/Responses adaptation, upstream calls, streaming, tool handling, logging, and most runtime behavior in one file.
- `proxy/qz_proxy_config.py` now holds the first extracted runtime constants and API contract metadata.
- `proxy/qz_sse.py` now holds pure SSE event formatting, test-only synthetic Responses stream helpers, reasoning visibility transforms, and response usage normalization.
- `proxy/qz_telemetry.py` now holds the first in-memory telemetry bus and subscriber ring buffer.
- `proxy/qz_telemetry.py` now also keeps the latest completed request so throughput counters survive noisy polling.
- `proxy/qz_runtime_io.py` now holds runtime `var/captures` path helpers and capture writes.
- `proxy/qz_backend.py` now holds the upstream/backend HTTP client helpers for model load/list and JSON forwarding.
- `proxy/qz_model_catalog.py` now holds GGUF scanning, metadata extraction, override merging, and the proxy-facing model catalog.
- `proxy/qz_model_router.py` now holds the model-selection, backend-load, readiness/status snapshots, compact runtime-state injection, and Ollama-compatibility route handling.
- `proxy/qz_model_router.py` now emits fresh status snapshots on `/ready`, `/qz/status`, and request boundaries, so monitors can see current load state without stale caches.
- Requests that target a model now wait on that model instead of falling back to some other already-loaded model, and `/v1/responses` plus model load/select calls are serialized behind a small request gate so model switches apply before the next request is admitted.
- When the requested model differs from the resident backend model, the router now unloads the resident model first, waits for that unload to finish, then loads the new target before admitting the request. Startup warmup follows the same selected-backend-id path.
- `proxy/qz_responses.py` now holds pure Responses normalization, apply_patch translation, tool declaration adaptation, and local compaction helpers.
- `proxy/qz_tools.py` now defines the first tool adapter/registry API.
- `proxy/qz_tool_apply_patch.py` now holds the apply_patch tool adapter and compatibility helpers.
- `proxy/qz_tool_web.py` now holds the web_search declaration/tool-choice adapter and the local web runtime for search/open/find execution.
- `proxy/qz_streaming.py` now holds the first streamed Responses state helpers, including SSE event parsing, streamed function-call assembly, output-index rewriting, and public tool-item event helpers.
- `proxy/qz_responses_stream.py` now holds the multi-hop Responses SSE runtime and its web-search continuation loop.
- This is acceptable for the first working stack, but it makes regression testing and future backend work harder than necessary.

## Phase 1: Python Package Restructure

Move toward a package layout like:

```text
proxy/
  quantzhai_proxy/
    __init__.py
    __main__.py
    server.py
    config.py
    upstream.py
    responses.py
    streaming.py
    tools.py
    logging_utils.py
    errors.py
  quantzhai_proxy.py
```

Keep `proxy/quantzhai_proxy.py` as a compatibility entrypoint at first, so existing scripts continue to work.

First extraction landed:

- model budget constants
- local Codex rate-limit metadata
- current API endpoint contract
- legacy endpoint deprecation metadata
- SSE event helpers, test-only synthetic stream helpers, and reasoning stream transforms
- in-memory telemetry event bus and local telemetry endpoints
- runtime capture path/write helpers
- upstream/backend HTTP client helpers
- Responses input/tool normalization, apply_patch adaptation, and local compaction helpers
- GGUF catalog scanning and backend model metadata wiring
- model-selection and backend-load route handling
- initial tool adapter/registry API with apply_patch as the first concrete adapter
- web_search declaration/tool-choice adapter and local execution runtime
- streamed SSE function-call assembly and the first real-SSE tool continuation path
- extracted Responses SSE runtime with unit coverage that does not need HTTP, Codex, Docker, or a live model server

## Tool Adapter API

Tool code should sit behind small adapters so the proxy does not special-case
each tool throughout request normalization, streaming, and local execution.

Current adapter responsibilities:

- Detect supported Codex/OpenAI tool declarations.
- Translate tool declarations into upstream llama.cpp function tools.
- Normalize `tool_choice`.
- Translate replayed history items into upstream-compatible function call/output
  items.
- Translate upstream function calls back into Codex-compatible output items.

This gives real SSE a cleaner hook point: the streaming state machine can detect
a completed function call, dispatch the adapter, append the result, and resume
the upstream request without embedding tool-specific rules into the stream
parser.

Real SSE now calls the local web runtime when
`qz_streaming.StreamedFunctionCallAssembler` reports a completed `web_search`
call, emits a Codex-facing `web_search_call`, appends the hidden
`function_call_output`, and resumes the upstream request.

The remaining streaming runtime now lives in `qz_responses_stream.ResponsesStreamRuntime`.
`ProxyHandler` is back to the HTTP boundary for local streaming: it sends
headers, writes the local rate-limit event, constructs the runtime, and reports
streaming errors.

Next target: harden output-index and terminal-event behavior with golden fixture
replays from captured upstream streams, then route more tool dispatch through the
tool adapter registry instead of naming tools directly in the stream runtime.

## Phase 2: Extract Testable Core Units

Extract functions and classes that can be tested with plain inputs and outputs:

- Request normalization.
- Responses API to upstream chat/completions translation.
- Upstream response normalization.
- Streaming chunk parsing and emission.
- Multi-hop Responses streaming state machine. Initial web-search dispatch/resume is extracted into `ResponsesStreamRuntime`; golden fixture coverage still needs to grow.
- Incremental streaming capture writer.
- Tool-call detection and formatting.
- Tool-call continuation boundaries between streamed upstream requests.
- Error mapping.
- Runtime config loading and validation.
- Capture/log path selection.

Avoid changing behavior during extraction. The first win is a cleaner internal shape with the same external contract.

## Phase 3: Test Harness

Add a lightweight test layout:

```text
tests/
  test_config.py
  test_responses_adapter.py
  test_streaming.py
  test_tools.py
  fixtures/
```

Target tests that do not require GPU, Docker, or Codex:

- Golden request/response fixture conversion.
- Streaming fixture replay.
- Streaming fixture replay with tool-call continuation across multiple upstream
  requests.
- Synthetic SSE fixture replay for the current buffered fallback.
- Tool-call fixture parsing.
- Cancellation and client-disconnect behavior during streamed runs.
- Config defaults and `.env` override behavior.
- Error response shapes.

Only add live integration checks later, and keep them opt-in.

## Phase 4: Backend Adapter Boundary

Define a small backend contract before adding more model servers:

- `list_models()`
- `chat_completion()`
- `stream_chat_completion()`
- `responses()` or a higher-level equivalent if a backend can natively support
  Responses-style events.
- `healthcheck()`
- `cancel()` if the backend supports it.

The current llama.cpp path should become one adapter. A future Fox backend should plug into the same boundary only after Fox reaches feature parity with `thetom/llama.cpp-turboquant`.

## Phase 5: Rust Port Maybe

A Rust port is a maybe, not a default destination.

Consider Rust only after the Python package has clear module boundaries and tests. Rust becomes attractive if QuantZhai needs:

- Lower proxy overhead under sustained streaming.
- Stronger typed adapter boundaries.
- A single static binary for easier deployment.
- Better concurrency and cancellation behavior.
- Cleaner long-term maintenance than the Python version.

Do not start with Rust. Use the Python restructure to discover the real boundaries first.

## Non-Goals For Now

- Do not rewrite the proxy while it is still being made testable.
- Do not break `scripts/qz-proxy` or `scripts/qz-up`.
- Do not require Docker, Codex, or a live model for normal unit tests.
- Do not add a build system until tests need it.

## API Contract Direction

QuantZhai should target current OpenAI-style agent clients through Responses.

Current endpoints:

- `/v1/responses`
- `/v1/responses/compact`

Legacy compatibility:

- `/v1/chat/completions`
- `/chat/completions`

The Chat Completions routes are still proxied for compatibility, but they are
now flagged as deprecated at runtime through headers and health metadata. They
should remain only until local clients are confirmed not to depend on them.

The next streaming architecture work should focus on real Responses SSE with
local tool-call continuation, not expanding legacy Chat Completions behavior.
