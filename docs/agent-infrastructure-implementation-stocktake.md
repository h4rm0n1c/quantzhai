# Agent Infrastructure Implementation Stocktake

Date: 2026-05-24
Status: Authoritative source-grounded stocktake.

This document provides a comprehensive inventory of the agent infrastructure implemented in QuantZhai. It maps features to source files, implementation status, and observability channels to identify gaps and prioritize next steps for live agent readiness.

---

## A. Infrastructure Matrix

**Legend**:
- **EL** (Evidence Level): `source`, `unit_test`, `integration_test`, `live_smoke`, `operator_observed`
- **LV** (Live Validated): `yes`, `no`, `partial`, `unknown`

| Feature | EL | LV | Model Channel | Operator Channel | Gaps | Next Action |
|---|---|---|---|---|---|---|
| **Tool registry** | source | yes | instructions/harness | telemetry, captures | none | — |
| **Schema replacement** | unit_test | unknown | none (upstream only) | `tool_schema_replaced` (router + stream, `source="tool_schema_normalizer"`) | not live-validated | live-smoke schema event |
| **Coercion success** | unit_test | yes | none (silent) | coercion_succeeded | none | — |
| **Coercion error** | unit_test | yes | function_call_output | coercion_failed | none | — |
| **apply_patch coercion** | unit_test | yes | function_call_output | coercion_failed (source=tool_adapter) | none | — |
| **web_search coercion** | unit_test | yes | function_call_output | coercion_failed (source=tool_adapter) | none | — |
| **unknown/dropped tool** | source | yes | function_call_output | tool_call_error | no dedicated event | — |
| **web_search lifecycle** | live_smoke | yes | public_item (SSE) | telemetry | none | — |
| **provider guidance** | live_smoke | yes | capabilities (action) | telemetry | none | — |
| **retrieval action** | live_smoke | yes | function_call_output | telemetry | none | — |
| **repeated-read parser** | unit_test | yes | none | none | none | — |
| **repeated-read tool** | live_smoke | yes | function_call_output | repeated_read_signal | none | — |
| **native sandbox classifier** | live_smoke | yes | none | tool_sandbox_denied | — | — |
| **native conn-refused** | unit_test | yes | none | tool_connection_failed | no model-visible advice | — |
| **native telemetry wiring** | source | yes | none | telemetry | none | — |
| **native sandbox advisory** | unit_test | partial | function_call_output (advisory) | tool_sandbox_advisory_injected | not live-agent-validated | live-smoke advisory |
| **SignalDecision types** | source | yes | none | none | none | — |
| **render_coercion_error** | unit_test | yes | function_call_output | none | none | — |
| **render_advisory** | unit_test | yes | function_call_output | none | none | — |
| **empty-answer repair** | live_smoke | yes | none (next hop) | repair telemetry | no model message | — |
| **reasoning-only abort** | live_smoke | yes | fallback message | fallback telemetry | none | — |
| **no-output timeout** | unit_test | no | fallback message | timeout telemetry | disabled by default | enable and smoke |
| **compact/hang watchdog** | unit_test | no | fallback message | timeout telemetry | disabled by default | enable and smoke |
| **protocol drift** | unit_test | yes | none | drift telemetry | none | — |
| **qz-thoughts/qz-top** | live_smoke | yes | n/a | activity rows | none | — |
| **BrainCase render/recall** | source | no | RenderPacket | telemetry | feature-flagged (off) | keep off |
| **BrainCase write** | source | no | none | telemetry | feature-flagged (off) | keep off |
| **BrainCase flags** | source | yes | none | none | default disabled | — |

---

## B. Evidence Index

For each major claim, this index links to the proof of implementation and validation.

- **Repeated-read integration**:
    - Source: `proxy/qz_file_signal.py`, `proxy/qz_proxy_tools.py`
    - Tests: `tests/test_qz_file_signal.py`, `tests/test_qz_proxy_tools.py`
    - Smoke: `scripts/qz-smoke-repeated-read`
- **Native sandbox classifier**:
    - Source: `proxy/qz_native_tool_output.py`
    - Tests: `tests/test_qz_native_tool_output.py`
    - Smoke: `scripts/qz-live-smoke` (Check "denied native command" section)
- **SignalDecision native classifier path wired**:
    - Source: `proxy/qz_native_tool_output.py` (`classify_native_tool_output_signals`)
    - Router: `proxy/qz_request_router.py` (Imports and uses `classify_native_tool_output_signals` in `proxy_json_api`)
    - Tests: `tests/test_qz_native_tool_output.py` (`SignalWrapperTests`), `tests/test_qz_request_router.py` (`SignalDecisionEmissionTests`)
    - Proof of legacy compatibility: `tests/test_qz_native_tool_output.py` (`ClassifyNativeToolOutputsTests`) still uses legacy API.
- **Sandbox advisory (model-visible)**:
    - Source: `proxy/qz_request_router.py` (`_model_visible_native_advisories`, `_SANDBOX_READONLY_ADVISORY_TEXT`)
    - Router wiring: `proxy_json_api` appends advisory items to `body["input"]` for `sandbox_denied_readonly_fs` / `high` confidence signals, then emits `tool_sandbox_advisory_injected` telemetry.
    - Advisory rendered via `qz_feedback.render_advisory_output()` — plain text, not a JSON error shape.
    - Tests: `tests/test_qz_request_router.py` (`SandboxAdvisoryHelperTests`, `SandboxAdvisoryInjectionTests`)
    - Bounds: only `sandbox_denied_readonly_fs`, only `high` confidence, deduped per call_id per request, no persistent state, no auto-retry.
- **Web_search provider guidance**:
    - Source: `proxy/qz_tool_web.py` (`_fetch_provider_guidance_cached`)
    - Capabilities: `GET /qz/web-search/capabilities`
- **Stream watchdog disabled defaults**:
    - Source: `proxy/qz_stream_watchdog.py` (`STREAM_NO_OUTPUT_TIMEOUT_S = 0`)
- **BrainCase feature flags**:
    - Source: `proxy/qz_braincase_tools.py` (`QZ_BRAINCASE_TOOLS_ENABLED_ENV`)
    - Registry: `proxy/qz_proxy_tools.py` (`make_proxy_local_tool_registry` defaults to no BrainCase)
- **Tool error rendering (unified)**:
    - Owner: `proxy/qz_feedback.py` (`render_coercion_error()`)
    - Wrapper: `proxy/qz_tools.py` (`synthesize_tool_error_result()` now delegates)
    - Tests: `tests/test_qz_feedback.py` (`RenderCoercionErrorTests`), `tests/test_qz_tools.py` (`SynthesizeToolErrorResultTests::test_delegates_to_render_coercion_error`)
    - Invariant: both produce byte-for-byte identical `{"type":"function_call_output","call_id":...,"output":"{\"ok\":false,...}"}`.
- **Coercion telemetry enrichment**:
    - Source: `proxy/qz_request_router.py` and `proxy/qz_responses_stream.py` (`coercion_succeeded`/`coercion_failed` emission sites)
    - Fields: `tool`, `upstream_name`, `call_id`, `correction_applied`, `error_summary`, `request_id`, `source="tool_adapter"`
    - Tests: `tests/test_qz_responses_stream.py` (`CoercionTelemetryStreamingTests`)
- **Tool schema normalization telemetry**:
    - Event: `tool_schema_replaced` (operator-only, model-invisible)
    - Source: `proxy/qz_request_router.py` (`_emit_schema_normalization_telemetry` helper, called from `proxy_json_api`) and `proxy/qz_responses_stream.py` (hop 0 of streaming runtime)
    - Trigger: when `normalize_tool_request_for_llamacpp` produces any non-empty `replaced`, `translated`, `dropped`, or `deduped` set
    - Payload: `replaced`, `translated`, `dropped`, `dropped_count`, `deduped`, `source="tool_schema_normalizer"`, `request_id`
    - Deduplication: `ToolRequestNormalizationReport.deduped` tracks duplicate tool names separately from genuine `dropped` entries; deduped tools do NOT appear in `qz_dropped_tool_names` (fix for prior silent drop/incorrect routing)
    - Tests: `tests/test_qz_tool_request.py` (deduped field, metadata correctness), `tests/test_qz_request_router.py` (`ToolSchemaTelemetryRouterTests`), `tests/test_qz_responses_stream.py` (`ToolSchemaTelemetryStreamingTests`)
    - Constraints: names/counts only; no full schemas, no user arguments; no model injection

---

## C. External Runtime Patterns

QuantZhai is evaluated against three major architectural patterns to identify gaps and alignment.

### 1. Raw OpenAI-compatible Servers (llama.cpp, vLLM, Ollama)
- **Alignment**: High on basic completion/embedding.
- **QuantZhai Gap**: These servers lack the "agent loop." QuantZhai adds multi-hop continuation, buffering, and coercion that are missing in raw serving layers.

### 2. Responses-style Proxies/Adapters
- **Alignment**: Strong. QuantZhai is built as a native Responses-compatible bridge.
- **QuantZhai Gap**: Most Responses proxies are passive. QuantZhai is active, intercepting tools and injecting signals.

### 3. Tool Protocol/Provider Systems (MCP, ToolRegistry)
- **Alignment**: Partial.
- **QuantZhai Strength**: Clear separation between `ToolAdapter` (protocol) and `ProxyLocalToolExecutor` (runtime).
- **QuantZhai Gap**: Lacks a standardized capability discovery protocol like MCP. `provider_guidance` is a local custom implementation.

### Architectural Checklist

| Category | QuantZhai Implementation |
|---|---|
| **Tool Lifecycle** | Stateful multi-hop continuation with buffering. |
| **Model Feedback** | advisory results, coercion errors, empty-answer repair. |
| **Discovery** | Static `/capabilities` and `/guidance` endpoints. |
| **Telemetry** | Event-driven bus with provenance-aware fields. |
| **Terminality** | Watchdog-guaranteed stream termination. |
| **Repair** | Reasoning-only and empty-answer recovery hops. |

---

## D. Recommended Next Fix Passes (Evidence-Based)

1. **Live validate provider_guidance**: Verify `qz-live-smoke` confirms guidance appearing in capabilities.
2. **Model-visible Sandbox Advisory**: ✓ Done — `_model_visible_native_advisories` wired in `proxy_json_api`. Sandbox `tool_sandbox_denied` (classifier `sandbox_denied_readonly_fs`, confidence `high`) now appends a plain-text advisory `function_call_output` item before forwarding. Telemetry: `tool_sandbox_denied` (existing) + `tool_sandbox_advisory_injected` (new). Next: live-smoke to confirm the model receives and acts on the advisory.
3. **Coercion Telemetry**: ✓ Done — `coercion_succeeded` and `coercion_failed` events now carry `source="tool_adapter"` and `upstream_name` in both `qz_request_router` and `qz_responses_stream` paths. Tested in `CoercionTelemetryStreamingTests`.
4. **Tool Error Delegation**: ✓ Done — `synthesize_tool_error_result()` now delegates to `render_coercion_error()` from `qz_feedback`. `qz_feedback` is the single source of truth for coercion/error rendering. Tested in `SynthesizeToolErrorResultTests::test_delegates_to_render_coercion_error`.
5. **Tool Schema Normalization Telemetry**: ✓ Done — `tool_schema_replaced` event emitted in both `proxy_json_api` (router) and streaming runtime hop 0 when any normalization changes the tool list. Payload: `replaced`, `translated`, `dropped`, `deduped`, `source="tool_schema_normalizer"`. `ToolRequestNormalizationReport.deduped` is now a separate field; duplicate tools no longer pollute `qz_dropped_tool_names`. Tested in `ToolSchemaTelemetryRouterTests` and `ToolSchemaTelemetryStreamingTests`. Next: live-smoke to confirm event appears in qz-thoughts when Codex sends stale web_search schema.
6. **Watchdog Smoke**: Add a dedicated timeout smoke test before enabling default timeouts.
7. **BrainCase Isolation**: Maintain BrainCase as a feature-flagged experimental path.
