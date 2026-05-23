# Agent Infrastructure Implementation Stocktake

Date: 2026-05-24
Status: Authoritative source-grounded stocktake.

This document provides a comprehensive inventory of the agent infrastructure implemented in QuantZhai. It maps features to source files, implementation status, and observability channels to identify gaps and prioritize next steps for live agent readiness.

---

## A. Infrastructure Matrix

| Feature | Source issue/doc | Primary files | Status | Live hook | Model-visible channel | Operator-visible channel | Unit tests | Live smoke | Known gaps | Next action |
|---|---|---|---|---|---|---|---|---|---|---|
| **Tool registry / adapter declarations** | #41, #42 | `qz_tools.py`, `qz_proxy_tools.py` | implemented | yes | instructions/harness | telemetry, captures | `test_qz_tools.py` | yes | none | — |
| **Tool schema replacement/dedup** | #41 | `qz_tool_request.py` | implemented | yes | none (upstream only) | capture file | `test_qz_tool_request.py` | no | no telemetry | add replacement telemetry |
| **Tool coercion success path** | #59 | `qz_proxy_tools.py`, adapters | implemented | yes | none (silent) | none | adapter-specific tests | yes | zero observability | add success telemetry |
| **Tool coercion error path** | #59 | `qz_proxy_tools.py`, `qz_feedback.py` | implemented | yes | function_call_output | telemetry | `test_qz_feedback.py` | yes | zero telemetry | add failure telemetry |
| **apply_patch coercion** | #62 | `qz_tool_apply_patch.py` | implemented | yes | function_call_output | telemetry | `test_apply_patch_adapter.py` | yes | zero telemetry | add success/failure telemetry |
| **web_search coercion** | #60 | `qz_tool_web.py` | implemented | yes | function_call_output | telemetry | `test_qz_tool_web.py` | yes | zero telemetry | add success/failure telemetry |
| **unknown tool feedback** | #41 | `qz_proxy_tools.py` | implemented | yes | function_call_output | tool_call_error telemetry | `test_qz_tools.py` | no | no dedicated event | — |
| **dropped tool feedback** | #41 | `qz_proxy_tools.py` | implemented | yes | function_call_output | tool_call_error telemetry | `test_qz_tools.py` | no | no dedicated event | — |
| **proxy-local web_search lifecycle** | #41, #60 | `qz_tool_web.py`, `qz_responses_stream.py` | implemented | yes | public_item (SSE) | telemetry | `test_qz_tool_web.py` | yes | none | — |
| **web_search provider guidance** | #60 | `qz_tool_web.py` | implemented | yes | capabilities (action) | telemetry | `test_qz_tool_web.py` | yes | needs live validation | validate in live-smoke |
| **web_search source-strict handling** | #60 | `qz_tool_web.py` | implemented | yes | function_call_output | telemetry | `test_qz_tool_web.py` | yes | none | — |
| **retrieval action** | #41 | `qz_tool_web.py` | implemented | yes | function_call_output | telemetry | `test_qz_tool_web.py` | yes | none | — |
| **repeated-read parser** | #4, #43 | `qz_file_signal.py` | implemented | yes | none | none | `test_qz_file_signal.py` | yes | none | — |
| **repeated-read tool integration** | #4, #43 | `qz_proxy_tools.py` | implemented | yes | function_call_output (advisory) | repeated_read_signal | `test_qz_proxy_tools.py` | yes | none | — |
| **repeated-read live smoke** | #43 | `scripts/qz-smoke-repeated-read` | implemented | yes | advisory | repeated_read_signal | — | yes | none | — |
| **native sandbox-denied classifier** | #28 | `qz_native_tool_output.py` | implemented | yes | none | tool_sandbox_denied | `test_qz_native_tool_output.py` | yes | no model-visible advice | add model advice |
| **native connection-refused classifier** | #28 | `qz_native_tool_output.py` | implemented | yes | none | tool_connection_failed | `test_qz_native_tool_output.py` | yes | no model-visible advice | — |
| **native classifier telemetry wiring** | #28 | `qz_request_router.py` | implemented | yes | none | telemetry | `test_qz_telemetry.py` | yes | none | — |
| **native classifier model-visible advice** | #28 | — | partial | yes | turn harness | none | — | no | only in static harness | inject advisory result |
| **qz_feedback SignalDecision types** | #42 | `qz_feedback.py` | implemented | partial | none | none | `test_qz_feedback.py` | no | unused in main loop | wire into router |
| **qz_feedback render_coercion_error** | #42 | `qz_feedback.py` | implemented | yes | function_call_output | none | `test_qz_feedback.py` | yes | none | — |
| **qz_feedback render_advisory_output** | #42 | `qz_feedback.py` | implemented | yes | function_call_output | none | `test_qz_feedback.py` | yes | none | — |
| **qz_feedback integration into qz_tools** | #42 | `qz_tools.py` | partial | yes | function_call_output | none | — | yes | compatibility bridge | leave for now |
| **qz_feedback integration into native** | #42 | `qz_native_tool_output.py` | implemented | no | none | none | `test_qz_native_tool_output.py` | no | wrapper unused | wire into router |
| **qz_feedback integration into stream** | #42 | — | planned | no | none | none | — | no | phase 4 deferred | wire later |
| **empty-answer repair** | #9 | `qz_responses_stream.py` | implemented | yes | none (next hop) | repair telemetry | `test_qz_responses_stream.py` | yes | no model message | — |
| **reasoning-only abort/fallback** | #9 | `qz_responses_stream.py` | implemented | yes | fallback message | fallback telemetry | `test_qz_responses_stream.py` | yes | none | — |
| **stream no-output timeout** | #40 | `qz_stream_watchdog.py` | implemented | yes | fallback message | timeout telemetry | `test_qz_stream_watchdog.py` | no | default disabled | enable and smoke |
| **compact/hang watchdog** | #40 | `qz_stream_watchdog.py` | implemented | yes | fallback message | timeout telemetry | `test_qz_stream_watchdog.py` | no | default disabled | enable and smoke |
| **protocol drift / item events** | #41 | `qz_stream_terminal.py` | implemented | yes | none | drift telemetry | `test_qz_stream_terminal.py` | no | none | — |
| **qz-thoughts new signals** | #41 | `scripts/qz-thoughts` | implemented | yes | n/a | activity rows | `test_qz_thoughts_cli.py` | yes | none | — |
| **qz-top new signals** | #41 | `scripts/qz-top` | implemented | yes | n/a | GPU/VRAM/status | `test_qz_top.py` | yes | none | — |
| **qz-live-smoke coverage** | #35 | `scripts/qz-live-smoke` | implemented | yes | end-to-end | all events | — | yes | none | — |
| **BrainCase render/recall** | #53 | `qz_braincase_tools.py` | implemented | yes | RenderPacket | telemetry | `test_qz_braincase_tools.py` | no | feature-flagged | keep off |
| **BrainCase write_candidate** | #53 | `qz_braincase_tools.py` | implemented | yes | none | telemetry | `test_qz_braincase_tools.py` | no | feature-flagged | keep off |
| **BrainCase harness policy** | #53 | `qz_braincase_tools.py` | implemented | yes | instructions | none | `test_qz_braincase_tools.py` | no | feature-flagged | keep off |
| **BrainCase feature flags** | #53 | `qz_proxy_tools.py` | implemented | yes | none | none | `test_qz_proxy_tools.py` | no | default disabled | — |
| **BrainCase live-agent readiness** | #53 | — | partial | no | none | none | — | no | storage doctrine gate | wait for API |

---

## B. “Implemented but Unused” Audit

| Item | Status | Recommended Action |
|---|---|---|
| `classify_native_tool_output_signals` | Structured `SignalDecision` wrapper for native classifiers in `qz_native_tool_output.py`. | **Wire now**: update `qz_request_router.py` to use the structured return instead of raw tuples. |
| `SignalDecision` | Generic signal type in `qz_feedback.py`; unused in `qz_request_router.py`. | **Wire now**: adopt as the canonical return type for all classifier and signal detections in the router. |
| `FeedbackChannel.TURN_HARNESS` | Defined in `qz_feedback.py`; logic in `qz_request_normalization.py` is manual/static. | **Wire later**: automate harness injection via a generic signal dispatcher. |
| `FeedbackChannel.INSTRUCTIONS` | Defined in `qz_feedback.py`; no dynamic injection path yet. | **Wire later**: add dynamic instruction-block injection to the request router. |
| `StreamWatchdogState` | Default disabled (0s timeouts). | **Enable soon**: set conservative defaults (e.g. 120s/60s) after verifying in stable live smoke. |
| `provider_guidance` | Implemented in `qz_tool_web.py` and `qz_codex_client_config.py`. | **Validate now**: verify it appears in `capabilities` and generated client config. |
| `qz_tools.synthesize_tool_error_result` | Legacy bridge kept for compatibility. | Leave as-is; it is stable and byte-for-byte identical to `qz_feedback.render_coercion_error`. |
| `carry_forward` / `hop_budget` | Experimental signals in `qz_responses_stream.py`. | Leave as opt-in experimental features. |

---

## C. Codex Client Failure Classes

Focused audit of recovery and detection for common failure modes.

### 1. compact task repeatedly errors / stuck Working
- **Detection**: `qz_stream_terminal.py` detects `saw_compact_failed` (from future signals) or `compact_failed` classification.
- **Telemetry**: `compact_failed` schema `qz.stream.terminal.v1`.
- **Fallback**: Proxy returns `fallback_required=True`. Local compaction in `qz_responses.py` is deterministic.
- **Gap**: No watchdog on the specific `/v1/responses/compact` endpoint; only on stream hops.

### 2. automatic compact hangs
- **Detection**: `StreamWatchdogState` handles timing for all upstream hops, including those triggered by auto-compaction.
- **Telemetry**: `stream_no_output_timeout` or `stream_terminal_timeout`.
- **Fallback**: Synthetic terminal event or fallback message emitted to Codex.
- **Gap**: Need live-smoke of watchdog during context pressure scenarios.

### 3. prompt accepted but no stream begins
- **Detection**: `should_trigger_no_output_timeout` in `qz_stream_watchdog.py`.
- **Telemetry**: `classification: stream_no_output_timeout`.
- **Fallback**: Synthetic completion with error message emitted to Codex.
- **Gap**: Default is `0` (disabled). Needs enabling to be effective.

### 4. protocol drift / item streaming events
- **Detection**: `observation_from_event_type` in `qz_stream_terminal.py` detects `response.output_item.content.delta`.
- **Telemetry**: `protocol_drift_seen` in terminal classification.
- **Fallback**: None needed; proxy transforms unknown deltas to known `output_text` deltas for Codex.
- **Gap**: Only handles a few known drift patterns; need more coverage for newer subagent events.

---

## D. Other Agent/Runtime Patterns

| Pattern | Comparison | QuantZhai Alignment |
|---|---|---|
| **OpenAI Responses** | Event-driven SSE tool lifecycle. | **Strong**: designed as a compatible bridge; handles item buffering and continuation hops. |
| **MCP** | Capability-based provider/tool boundary. | **Weak**: QuantZhai uses a local registry and profile-based routing instead of a dynamic discovery protocol. |
| **ToolRegistry** | Protocol-agnostic registry with coercion. | **Strong**: `qz_tools.py` provides a clean `ToolAdapter` interface and `coerce()` path. |
| **vLLM / llama.cpp** | Simple OpenAI-compatible serving. | **Beyond**: QuantZhai adds the "agent loop" (continuation, coercion, feedback) that raw servers lack. |

**Architectural Expectations**:
- Separate tool registry from provider runtime: **Yes** (`qz_tools.py` vs `qz_responses_stream.py`).
- Separate capability discovery from prompt folklore: **Partial** (`provider_guidance` exists but model still relies on system prompts).
- Distinguish model-visible advice from operator telemetry: **Strong** (`qz_feedback.py`).
- Live smoke tests are required for agent behaviour: **Strong** (`scripts/qz-live-smoke`).

---

## E. Recommended Next Fix Passes

### Pass 1: Infrastructure Cleanup
- Wire `SignalDecision` into `qz_request_router.py` for native tool classification.
- Update `qz-live-smoke` to verify `provider_guidance` in capabilities.
- Guard `ToolCoercionResult` in `qz_tools.py` (already done in source, verify in tests).

### Pass 2: Model Advice Injection
- Add model-visible advisory results for `tool_sandbox_denied`.
- Inject a bounded advisory telling the agent to request escalation instead of retrying blindly.

### Pass 3: Live-Smoke Validation
- Live-smoke `repeated-read`, `sandbox-denied`, and `web_search` provider guidance.
- Verify `qz-thoughts` activity rows for all new signal types.

### Pass 4: Watchdog Enablement
- Enable `QZ_STREAM_NO_OUTPUT_TIMEOUT_S=120` and `QZ_STREAM_TERMINAL_TIMEOUT_S=60` by default.
- Add regression tests for timeout fallback emission.

### Pass 5: BrainCase Evaluation
- Revisit BrainCase tool exposure once the feedback subsystem is stable.
