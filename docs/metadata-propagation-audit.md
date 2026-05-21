# Responses Metadata Propagation Audit

Date: 2026-05-22
Status: Slice D discovery — authoritative metadata flow map.

Related:
- `docs/runtime-streaming-tool-contract-audit.md` — Slice A pipeline map.
- `docs/tool-schema-coercion-audit.md` — Slice B coercion paths.
- `docs/streaming-event-mapper-audit.md` — Slice C event mapper.

---

## 1. Metadata Inventory

### Codex → QuantZhai (incoming request body fields)

| field | type | proxy action |
|---|---|---|
| `model` | string | Rewritten to `backend_model` (selected catalog entry's backend_id or key). Never forwarded as-is. |
| `stream` | bool | Always set to True for streaming requests. Non-streaming: False. |
| `tools` | array | Normalised/replaced/deduped by `normalize_tool_request_for_llamacpp`. |
| `tool_choice` | dict/string | Normalised or forced to `"auto"` for unrecognised structured choices. |
| `input` / `messages` | array | Normalised by `normalize_responses_input_for_qwen`. Microcompaction applied. |
| `instructions` | string | May have `qz_runtime` state block appended (when `runtime_state_enabled`). Passed upstream. |
| `max_output_tokens` | int | Passed upstream unchanged. |
| `temperature` | float | Passed upstream unchanged (or overridden by reasoning policy). |
| `reasoning` | dict | Passed upstream after `apply_reasoning_policy` may rewrite/inject fields. |
| `previous_response_id` | string | **Passed upstream unchanged.** No proxy-side handling. |
| `response_format` | dict | **Passed upstream unchanged.** No proxy-side handling. |
| `truncation_strategy` / `truncation` | dict | **Passed upstream unchanged.** No proxy-side handling. |
| `parallel_tool_calls` | bool | **Passed upstream unchanged.** No proxy-side handling. |
| `user` | string | **Passed upstream unchanged.** No proxy-side handling. |
| `metadata` | dict | **Augmented with qz_* fields; forwarded to upstream.** See qz_* section. |
| `context_management` | dict | Checked for `compact_threshold`; if threshold exceeded, compaction response synthesized; field not forwarded upstream. |
| `store` | bool | Passed upstream unchanged. |
| `include` | array | Passed upstream unchanged. |

### QuantZhai internal metadata (injected into `body.metadata`)

All `qz_*` fields are placed in `body["metadata"]` and forwarded upstream to llama.cpp.
llama.cpp ignores unknown metadata fields but RECEIVES them.

| field | injected by | value |
|---|---|---|
| `qz_request_id` | `proxy_json_api` line 2846 | proxy-assigned UUID-based request ID |
| `qz_upstream_instructions_present` | `proxy_json_api` line 2845 | bool: whether Codex provided instructions |
| `qz_reasoning_stream_format` | `proxy_json_api` line 2854 | effective reasoning stream format after policy resolution |
| `qz_tool_policy` | `ensure_apply_patch_tool_policy` | `qz.tool_policy.v1` dict with apply_patch style |
| `qz_dropped_tool_names` | `normalize_tool_request_for_llamacpp` | list of tool names dropped from declaration |
| `qz_runtime` | `inject_runtime_state` (opt-in via `runtime_state_enabled`) | runtime state block for model's system prompt |
| `qz_prompt_policy` | `assemble_instruction_stack` in model router | prompt assembly metadata |
| `qz_turn_harness` | model router prompt policy | turn harness application status |
| `qz_reasoning` | model router reasoning policy | reasoning level, policy, budget |

### QuantZhai → llama.cpp (forwarded body fields)

| field | forwarded? | modification |
|---|---|---|
| `model` | yes | Rewritten to `backend_model` |
| `stream` | yes | Always True for streaming path |
| `tools` | yes | Replaced/deduped by normalisation |
| `tool_choice` | yes | Normalised |
| `input` | yes | Normalised for Qwen; microcompaction applied |
| `instructions` | yes | May have runtime state appended |
| `metadata` (with qz_*) | **yes — entire metadata dict forwarded** | augmented with qz_* fields |
| `max_output_tokens` | yes | unchanged |
| `reasoning` | yes | may be rewritten by reasoning policy |
| `previous_response_id` | yes | unchanged |
| `response_format` | yes | unchanged |
| `temperature` | yes | may be overridden by reasoning policy |
| `user` | yes | unchanged |
| All other body fields | yes | unchanged |

**Note**: llama.cpp receives `qz_*` metadata. This is not a security issue (local deployment) but is observable in captures and could confuse future backends that parse metadata.

### llama.cpp → QuantZhai (upstream response fields)

| field | notes |
|---|---|
| `response.id` | string, unique per response |
| `response.object` | always `"response"` |
| `response.created_at` | unix timestamp |
| `response.status` | `"in_progress"` / `"completed"` / `"failed"` |
| `response.model` | model name used by backend |
| `response.output` | list of output items |
| `output[].id` | item ID |
| `output[].type` | `message`, `function_call`, `reasoning`, etc. |
| `output[].call_id` | function call ID (for function_call items) |
| `output[].name` | function name |
| `output[].arguments` | function arguments JSON string |
| `usage.input_tokens` / `prompt_tokens` / `prompt_eval_count` | input token count |
| `usage.output_tokens` / `completion_tokens` / `eval_count` | output token count |
| `usage.input_tokens_details.cached_tokens` | cached input tokens |
| `usage.output_tokens_details.reasoning_tokens` | reasoning token count |
| `finish_reason` / `stop_reason` | not present in Responses API; llama.cpp uses status |

### QuantZhai → Codex (forwarded/rewritten response fields)

| field | how handled |
|---|---|
| `response.id` | Forwarded from upstream in normal flow. **Replaced with `resp_local_{_now_ts()}` in synthesised terminals** (tool continuation, fallback, repair). |
| `response.object` | Forwarded unchanged. |
| `response.created_at` | Forwarded from upstream. Synthesised terminals use `_now_ts()`. |
| `response.status` | Forwarded from upstream or set to `"completed"` / `"failed"` in synthesis. |
| `response.model` | **Rewritten to `requested_model`** (the model Codex asked for, which is the backend_model/selected model key). |
| `response.output` | Rewritten: `prepend_output=public_trace` adds prior tool items; reasoning items stripped per format; tool items converted via adapters. |
| `usage` | Normalized by `_normalize_response_usage`. Synthetic usage when `{}` passed. |
| `output[].id` | Upstream IDs forwarded. Synthetic IDs (`msg_local_*`, `wsc_local_*`) for proxy-synthesized items. |
| `output[].call_id` | Forwarded from upstream function_call items. |
| `output[].type` | May be converted: `function_call` → `apply_patch_call` or `custom_tool_call` or `web_search_call`. |
| `qz_*` fields | **NOT present in Codex-facing response.** qz_* stays internal to metadata only. |
| `error` | Forwarded from upstream on `response.failed`. Synthesised with proxy error text on local failures. |

### QuantZhai → qz-thoughts / qz-top

| metadata | qz-thoughts | qz-top |
|---|---|---|
| request_id | yes (via `sse_event`, `request_started`, `request_completed`) | indirect (via telemetry) |
| response.id | yes (via `sse_event` for `response.created`) | no |
| model | yes (via `sse_event` response.created, prompt_contract) | yes (via `/qz/model/status`, `/qz/control-plane`) |
| selected/loaded model | yes (via prompt_contract, control_plane_rows) | yes |
| selected_model_ready | no | yes |
| request_admission_state | no | yes |
| usage (tokens) | yes (via `sse_event` response.completed, compacted) | yes (via telemetry rates) |
| cached_tokens | no | no |
| reasoning_tokens | no | no |
| tool lifecycle (name, call_id) | yes (tool_call_started/completed) | no |
| search profile/budget | yes (web_search_route) | no |
| coercion report | no | no |
| runtime failure state | partial (prompt_contract.backend_* fields) | yes |
| request duration | yes (request_completed elapsed_ms) | yes (gen_ms, prompt_ms) |
| reasoning stream format | yes (prompt_contract) | no |
| memory_domain | yes (prompt_contract) | no |

---

## 2. Metadata Flow Table

| metadata field | source | current destination(s) | preserved? | rewritten? | synthesized? | Codex-visible? | qz-thoughts? | qz-top? | telemetry-only? | gap/bug | proposed test | fix pass |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `request_id` (Codex-supplied) | Codex request headers / body | not extracted from Codex; proxy generates its own | no | — | — | no | no | no | — | Codex does not supply a request_id in the body (it's in headers); proxy uses its own | — | none |
| `qz_request_id` | proxy-generated | `body.metadata`, captures, telemetry events | yes | no | yes (proxy) | no (internal) | yes | indirect | yes | forwarded to llama.cpp in metadata (harmless) | test that qz_request_id in capture matches telemetry | none |
| `response.id` | upstream (llama.cpp) | forwarded in streamed events; **replaced in synthesised terminals** | partial | **yes** (synthesis) | yes (fallback) | yes | yes (via sse_event) | no | no | **P1**: synthesised response.id (`resp_local_*`) does not match the upstream-assigned ID; Codex SDK may see two different IDs for the same logical response across hops | test that hop-2 response.completed carries the upstream response.id not a synthetic | D fix pass |
| `previous_response_id` | Codex request | forwarded upstream unchanged | yes | no | no | n/a | no | no | no | none | — | none |
| `model` (requested) | Codex request | rewritten to `backend_model` before upstream; `rewrite_sse_payload` sets `model=requested_model` in response | yes (as requested_model) | **yes** (rewritten to backend alias) | no | yes | yes | yes | no | model in response events always shows selected key, not Codex's client model string (intentional) | test that response.model in terminal matches selected_key | none |
| `model` (selected) | catalog resolution | stored in `selected_model` dict; written to `body["model"]` | yes | — | — | yes | yes | yes | no | ok | — | none |
| `model` (loaded/effective) | BackendManager snapshot | `/qz/model/status.backend_loaded_model` | yes | — | — | no | partial | yes | no | none | — | none |
| `response.model` | upstream response | rewritten via `rewrite_sse_payload(model=requested_model)` | partial | **yes** | no | yes | yes | no | no | ok — intentional; shows the canonical model key | — | none |
| `output_index` | upstream | offset by `rs.output_index_offset` when multi-hop | yes | **yes** (offset) | no | yes | no | no | no | output_index in Codex stream is consistent across hops; test confirms | test multi-hop index continuity | none |
| `output item id` | upstream (or synthetic) | forwarded; `proxy_local_item_id` used for proxy-local items | partial | no | yes (proxy items) | yes | yes (via sse_event item stripped) | no | no | synthetic IDs (`wsc_local_*`, `msg_local_*`) do not match any upstream ID; acceptable for new items | test wsc item id in lifecycle matches item.done | none |
| `call_id` (function_call) | upstream | forwarded from completed assembler call | yes | no | no | yes (for public tools) | no | no | no | ok | test call_id in public function_call matches upstream | none |
| `function_call_output call_id` | proxy-generated or upstream call_id | `execute_web_search_call` uses `call_item.get("call_id") or call_item.get("id")` | yes | no | yes (fallback) | no (upstream input) | no | no | no | ok — call_id always set from source call | test wsc function_call_output call_id matches web_search call | none |
| function/tool name | upstream | forwarded for public tools; hidden for proxy-local (only lifecycle type shown) | yes | no | no | yes | partial (tool_call_started payload) | no | no | ok | — | none |
| function arguments (metadata) | upstream | **never forwarded to Codex** (suppressed events) | no (to Codex) | — | — | **no** | no | no | no | correct — arguments are internal | test that no function_call delta appears in forwarded stream | none |
| `created_at` | upstream | forwarded in normal flow; synthesized (`_now_ts()`) in fallbacks | partial | no | yes (fallbacks) | yes | yes | no | no | synthesized created_at in fallbacks may differ from real time of completion | — | none |
| `completed_at` | proxy-measured | stream_result dict; `request_completed` telemetry | yes | no | yes (proxy) | no | yes | yes | telemetry | ok | — | none |
| `status` | upstream | forwarded; set to "completed"/"failed" in synthesis | yes | no | yes (fallbacks) | yes | yes | no | no | ok | — | none |
| `usage` | upstream (response.completed) | `_normalize_response_usage` applied; forwarded; synthesised `{}` in fallbacks | partial | yes (normalise) | yes (fallbacks use `{}`) | **yes** | yes (compacted in sse_event) | yes (telemetry rates) | no | **P1**: synthetic fallback responses emit `usage={}` (zero tokens). Codex SDK may compute zero-token operations incorrectly. | test that reasoning-abort and empty-repair fallbacks include usage from `rs.final_usage` | D fix pass |
| `input_tokens` | upstream usage | normalised | yes | yes (name normalise) | no | yes | yes | yes | no | ok | test usage normalisation from llama.cpp field names | none |
| `output_tokens` | upstream | normalised | yes | yes | no | yes | yes | yes | no | ok | — | none |
| `total_tokens` | upstream | normalised (recomputed if < sum) | yes | yes | no | yes | no | no | no | ok | — | none |
| `input_tokens_details.cached_tokens` | upstream | normalised | yes | yes (name normalise) | no | yes | **no** | **no** | no | **P2**: cached_tokens not shown in qz-thoughts or qz-top | add cached_tokens to sse_event telemetry compact | P2 fix |
| `output_tokens_details.reasoning_tokens` | upstream | normalised | yes | yes | no | yes | **no** | **no** | no | **P2**: reasoning_tokens not shown in qz-thoughts or qz-top | add reasoning_tokens to telemetry compact | P2 fix |
| reasoning item id | upstream | forwarded in raw/summary modes | yes | no | no | yes | partial (thought panel) | no | no | ok | — | none |
| reasoning summary part id | proxy-synthesized (summary mode) | emitted as part of `summary_text` events | no (synthetic) | — | yes | yes | yes | no | no | ok | — | none |
| `finish_reason`/`stop_reason` | upstream | not present in Responses API; llama.cpp uses status field | n/a | — | — | n/a | n/a | n/a | no | none | — | none |
| `error.type` | upstream or proxy | forwarded or synthesized | partial | no | yes (proxy) | yes | yes (request_failed) | yes | partial | proxy error types are plain strings, not typed codes | see error audit §6 | — |
| `error.code` | upstream | forwarded from upstream `response.failed` | partial | no | no | yes | no | no | no | **P3**: proxy-synthesized errors have no error.code | — | P3 |
| `error.message` | upstream or proxy | forwarded or synthesized from exception text | yes | no | yes | yes | yes | partial | no | bounded — no stack traces emitted | test that error message doesn't contain stack trace | none |
| tool schema replacement report | `ToolRequestNormalizationReport` | captures only (`latest-dropped-tools.txt`) | yes | — | — | no | **no** | **no** | **no** | **P2**: no telemetry event for replacement | add tool_schema_replaced event | B2 |
| dropped tool report | `qz_dropped_tool_names` in metadata | metadata forwarded upstream; `_emit_stream_event_timing` has tool_call_error for streaming | partial | no | — | no | partial | no | partial | **P2**: no explicit dropped_tools telemetry event | add tool_dropped event | B2 |
| coercion/advice report | internal only | no storage | no | — | — | no | **no** | **no** | **no** | **P2**: zero observability | add coercion_succeeded/failed telemetry | B2 |
| search profile | `web_search_route` telemetry | `web_search_route` telemetry + result payload | yes | no | — | no (result only) | yes | no | telemetry | ok | test web_search_route contains selected_profile | none |
| search budget_mode | resolved in `_resolve_budget_mode` | result payload + telemetry | yes | no | — | no (result) | yes | no | partial | ok | — | none |
| retrieval_source | args + result | result payload | yes | no | — | no | no | no | no | retrieval_endpoint URL never exposed | test retrieval_source in result, no endpoint URL | none |
| retrieval_retriever | Agent API response | result payload | yes | no | — | no | no | no | no | ok | — | none |
| `runtime_failure_error_type` | BackendManager / log classifier | `/qz/model/status`, `/qz/control-plane` | yes | — | — | no | partial | yes | no | mid-stream backend death not yet recorded as `runtime_failure_during_request_id` | add runtime_failure_during_request_id | H |
| `backend_died_after_healthy` | BackendManager snapshot | `/qz/model/status` | no | — | — | no | no | no | no | **P2**: not yet surfaced; planned | — | H |
| request duration / latency | proxy-measured | `request_completed` telemetry; `stream_result` dict | yes | — | yes | no | yes | yes | telemetry | ok | — | none |

---

## 3. Rewrite/Synthesis Audit

### Model field rewriting

`body["model"] = backend_model` (line 2848) where `backend_model = selected_model.get("backend_id") or selected_identity or client_model`. This ensures llama.cpp receives the canonical backend model identifier.

`rewrite_sse_payload(model=requested_model)` overwrites `response.model` in forwarded events with the Codex-requested model key. This is intentional — Codex always sees the model it requested, not the internal backend alias.

- Codex-visible: yes.
- Tests: `test_golden_basic_message_stream_replays_unchanged` doesn't verify model field explicitly. **Missing test** for model field roundtrip.

### output_index offsetting

`rs.output_index_offset` is incremented by `hs.max_output_index + 1` after each proxy-local tool break. Applied via `rewrite_sse_payload(output_index_offset=rs.output_index_offset)`. The next hop's item indices start where the previous hop left off.

- Codex-visible: yes (indices are monotonically increasing across hops).
- Can become stale/wrong: if `hs.max_output_index` is -1 (no output_index in any event), offset increments by 0, which is safe.
- Tests: `test_web_search_call_is_public_and_upstream_resumes_with_hidden_output` verifies continuation but doesn't assert index continuity explicitly.

### public_trace prepend

`rewrite_sse_payload(prepend_output=public_trace)` adds prior tool public items to `response.completed.response.output`. Ensures the terminal event contains all output items (not just the final hop's).

- Codex-visible: yes (in terminal).
- Can become stale: no — public_trace is accumulated in-order per hop.
- Tests: `test_web_search_call_is_public_and_upstream_resumes_with_hidden_output` verifies web_search_call in stream; doesn't explicitly verify terminal output list.

### Usage normalisation

`_normalize_response_usage(usage)` maps `prompt_tokens` → `input_tokens`, `eval_count` → `output_tokens`, `prompt_tokens_details` → `input_tokens_details`, `reasoning_tokens` → `output_tokens_details.reasoning_tokens`. Recomputes `total_tokens` if below sum.

- Codex-visible: yes.
- Tests: `test_usage_normalizer_maps_legacy_and_detail_fields`, `test_usage_normalizer_maps_llamacpp_token_fields` — good coverage.

### response.completed synthesis

`_emit_completed(requested_model, output, summary_started, usage)` emits a synthesised `response.completed` when the upstream terminal is suppressed (tool continuation, repair, abort).

- `response.id`: `f"resp_local_{_now_ts()}"` — **SYNTHETIC**. Differs from upstream ID.
- `created_at`: `_now_ts()` — proxy-local timestamp.
- `model`: `requested_model` — correct.
- `usage`: `_normalize_response_usage(usage)` — uses `rs.final_usage` if available; empty dict if not.

**Gap**: `rs.final_usage` is populated from the first `response.completed` event seen per hop. If a tool break happens before `response.completed`, `rs.final_usage` remains `{}` and the synthesised terminal emits zero-token usage. This is wrong — Codex receives a completed response with no token accounting.

### response.failed synthesis

On stream exception in `proxy_json_api`:
```python
error_payload = {"type": "response.failed", "response": {
    "id": f"resp_local_{_now_ts()}", "created_at": _now_ts(),
    "status": "failed", "model": client_model,
    "error": {"message": f"local streaming runtime error: {e}"},
    "output": [], "usage": _normalize_response_usage({}),
}}
```

- Error message: exception str — **bounded, but could include internal paths** if the exception message contains filesystem paths. No explicit sanitization.
- usage: zero (empty dict) — **missing usage**.
- Tests: `test_client_disconnect_closes_upstream_and_emits_cancel_telemetry` covers disconnect but doesn't verify error payload shape or usage.

### Fallback response ids

All proxy-synthesized response items use `f"resp_local_{_now_ts()}"` / `f"msg_local_{_now_ts()}"` / `f"wsc_local_{_now_ts()}"` / `f"fc_local_{_now_ts()}"`. These are time-based and do not match any upstream ID. The `_now_ts()` is integer seconds, so two items synthesised within the same second will have the same ID — **potential ID collision** if multiple items are synthesized in rapid succession.

### Synthetic tool item ids

`WebSearchRuntime.execute_web_search_call`:
- `web_call_item["id"]`: `call_item.get("id") or call_item.get("call_id") or f"wsc_local_{_now_ts()}"` — prefers upstream ID.
- `tool_output_item["call_id"]`: `call_item.get("call_id") or call_item.get("id") or f"fc_local_{_now_ts()}"` — prefers upstream call_id.

The proxy-local started item: `item_id = call.get("id") or call.get("call_id") or f"{lifecycle.name}_local_{public_index}"` — prefers upstream ID, falls back to public_index-based ID which IS stable per hop.

### qz_request_id insertion

`metadata["qz_request_id"] = request_id` at line 2846. Forwarded to llama.cpp. Used in captures and telemetry correlation. Not Codex-visible.

### metadata.qz_* insertion

All `qz_*` fields in metadata are forwarded upstream. llama.cpp ignores them. **There is no stripping of `qz_*` from the forwarded body.** This is acceptable for a local deployment but documents a future consideration for multi-tenant deployments.

### Dropped tool metadata

`metadata["qz_dropped_tool_names"]` is set by `normalize_tool_request_for_llamacpp` and forwarded to llama.cpp. Read by `ResponsesStreamRuntime.run()` to build `dropped_tool_names: frozenset`. No telemetry event.

### Tool policy metadata

`metadata["qz_tool_policy"]` with `apply_patch_output_style` and declaration state. Read by streaming and non-streaming paths. Forwarded to llama.cpp but ignored.

### Search route metadata

`web_search_route` telemetry event: `{query, requested_profile, selected_profile, categories, engines, fallback_used, result_count, ...}`. Emitted per search. Visible in qz-thoughts.

### Runtime failure metadata

`/qz/model/status` exposes: `runtime_failure_result`, `runtime_failure_error`, `runtime_failure_error_type`, `runtime_failure_at`, from `qz.model_state.v1`. Not yet populated during mid-stream backend death.

---

## 4. Tool Metadata Matching Audit

### function_call call_id generation/preservation

1. **Upstream emits function_call** with `call_id` in `response.output_item.done`.
2. `StreamedFunctionCallAssembler` preserves `call_id` from `response.output_item.{added,done}` events.
3. Completed call dict: `call.get("call_id")` preserved from assembler output.
4. For proxy-local (web_search): `call.get("call_id")` passed to `execute_web_search_call`.
5. In `execute_web_search_call`: `tool_output_item["call_id"] = call_item.get("call_id") or call_item.get("id") or f"fc_local_{_now_ts()}"`.
6. **Matching**: function_call.call_id ↔ function_call_output.call_id — YES, preserves upstream call_id.

### function_call_output call_id matching (proxy error path)

`synthesize_tool_error_result(call, message)`: `call_id = call.get("call_id") or call.get("id") or f"err_{int(time.time())}"`.

- Priority: call_id > id > timestamp. **Matches upstream call_id.**
- If call_id and id are both missing (malformed upstream item): timestamp fallback is non-stable. The model receives an error with a synthetic call_id that has no corresponding function_call in the history. **This could confuse conversation history.**

### proxy-local web_search call_id matching

Public item: `web_call_item["id"] = call_item.get("id") or call_item.get("call_id") or f"wsc_local_{_now_ts()}"`. Prefers upstream ID.

`_emit_proxy_local_started`: `item_id = call.get("id") or call.get("call_id") or f"{lifecycle.name}_local_{public_index}"`. Uses public_index fallback (stable per hop).

The `proxy_local_item_id` is set to the value from `_emit_proxy_local_started` and applied to the public_item via `public_item["id"] = proxy_local_item_id`. Both the start and done lifecycle events use the same item_id. **ID is stable and consistent.**

### apply_patch/public tool item id handling

For apply_patch: `_function_call_to_apply_patch_call`:
- `call_id = item.get("call_id") or item.get("id") or f"call_apply_patch_{_now_ts()}"`.
- `item_id = item.get("id") or f"apc_local_{_now_ts()}"`.
- Timestamp fallback has second-precision collision risk for rapid sequences.

### dropped/unknown tool synthetic call_ids

`synthesize_tool_error_result(call, ...)` uses `call.get("call_id") or call.get("id") or f"err_{int(time.time())}"`. If the completed call has no call_id (possible for malformed upstream items), a timestamp-based synthetic ID is used. This error result enters `next_input` with a call_id that has no corresponding function_call — the model should handle this gracefully (it's an error).

### Known-safe call_id paths

- Normal web_search: upstream call_id preserved end-to-end. ✓
- Normal apply_patch: upstream call_id preserved. ✓
- Normal exec_command: passed through unchanged. ✓
- Error injection for dropped/unknown: call_id from upstream call preserved. ✓

### Risky paths

- **Both call_id and id missing on upstream function_call item**: synthesize_tool_error_result uses timestamp. Error result has no matching call in history. Low probability (well-formed upstream should always emit call_id).
- **Rapid synthesis within same second**: `_now_ts()` collision means two items get same ID. Test coverage: none.
- **Multi-hop response.id mismatch**: Synthesised response.completed in hop-2 has different response.id than hop-1's response.created. Codex SDK may track response state by response.id and mismatch across hops.

### Missing tests

- Call_id preservation through web_search tool continuation.
- Error injection call_id matches upstream function_call call_id.
- apply_patch item_id stability (no timestamp collision on rapid invocations).

---

## 5. Usage/Token Accounting Audit

### Usage in streaming

- Populated from `response.completed` event: `rs.final_usage = _normalize_response_usage(response.get("usage"))`.
- Available in `rs.final_usage` from the first `response.completed` seen per run.
- Used in: `_emit_completed(usage=rs.final_usage)`, `_emit_stream_completed`, `_emit_reasoning_only_aborted`, `_emit_no_output_timeout_fallback`, `_emit_private_tool_call_aborted`.

**Gap**: If a tool break happens before `response.completed` (which is normal — the proxy breaks on `response.output_item.done`), `rs.final_usage` is `{}` at the time of the break. The proxy drains remaining SSE via `_drain_stream_for_usage(resp)` to capture usage:
```python
if resp is not None and (hs.error_injected or hs.signal_injected or is_proxy_local_call):
    drained_usage = self._drain_stream_for_usage(resp)
    if drained_usage is not None:
        rs.final_usage = drained_usage
```

`_drain_stream_for_usage` reads up to 200 more lines looking for `response.completed`. If found, updates `rs.final_usage`.

**Important**: If the upstream terminates without a `response.completed` after the tool break (e.g., sends only `[DONE]`), `rs.final_usage` remains `{}`. The synthesized `response.completed` emitted to Codex has zero usage. This is a real gap.

### Usage in non-streaming

`_run_responses_locally` returns `final_out["usage"] = _normalize_response_usage(final_out.get("usage"))`. Non-streaming upstream returns complete usage in the response body. No drain required. Usage is always present.

### Usage in fallbacks (reasoning abort, timeout, repair)

All `_emit_reasoning_only_aborted`, `_emit_no_output_timeout_fallback`, `_emit_private_tool_call_aborted` pass `rs.final_usage` to `_emit_completed`. If usage was not captured (no `response.completed` from upstream before abort), these emit zero usage.

The `_emit_completed` method does `_normalize_response_usage(usage)` — normalises even an empty dict to `{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}`. This is a **synthetic zero usage**, not an absent usage field.

### Usage normalisation between llama.cpp/OpenAI field variants

`_normalize_response_usage` handles:
- `input_tokens` (OpenAI Responses) ✓
- `prompt_tokens` (OpenAI Chat Completions) ✓
- `prompt_eval_count` (llama.cpp native) ✓
- `output_tokens` (OpenAI Responses) ✓
- `completion_tokens` (OpenAI Chat Completions) ✓
- `eval_count` (llama.cpp native) ✓
- `input_tokens_details.cached_tokens` (OpenAI) ✓
- `prompt_tokens_details.cached_tokens` (OpenAI Chat alt) ✓
- `cached_tokens` (top-level, llama.cpp) ✓
- `output_tokens_details.reasoning_tokens` (OpenAI) ✓
- `completion_tokens_details.reasoning_tokens` (OpenAI Chat alt) ✓
- `reasoning_tokens` (top-level, some providers) ✓

Tests: `test_usage_normalizer_maps_legacy_and_detail_fields`, `test_usage_normalizer_maps_llamacpp_token_fields` — well covered.

### cached_tokens / reasoning_tokens in observability

- Codex-visible: **yes** (in normalized usage).
- qz-thoughts: **no** — `_telemetry_sse_payload` compacts `response.completed` to `{id, model, status, created_at, usage}`. The `usage` dict IS included. qz-thoughts' `_apply_response_event` for response.completed does not read usage. `request_completed` telemetry includes usage summary.
- qz-top: **no** — telemetry rates track request counts and token throughput but not cached/reasoning breakdown.

---

## 6. Error Metadata Audit

### Upstream response.failed

- Forwarded via `transform_sse_event` → Codex sees the upstream error payload.
- Error shape from llama.cpp: `{"type": "response.failed", "response": {"status": "failed", "error": {...}}}`.
- llama.cpp error format varies; proxy forwards whatever llama.cpp sends.
- qz-thoughts: via `sse_event` telemetry. Error text visible in activity.
- qz-top: via `request_failed` telemetry.
- Raw internals: none (llama.cpp errors are bounded).

### Proxy-synthesized response.failed (stream exception)

```python
"error": {"message": f"local streaming runtime error: {e}"}
```
- Exception `str(e)` may contain filesystem paths or internal details from exception messages.
- No sanitization applied.
- **Partial risk**: stack trace is not included, but exception message text is. Python exceptions like `FileNotFoundError` or `PermissionError` include path strings.
- Tests: none explicitly check error message sanitization.

### Backend disconnect mid-stream (timeout)

- `_finish_no_output_timeout` / `_finish_terminal_timeout_after_output`: emits synthesised `response.completed` (not failed) with fallback message.
- The `response.completed` has synthetic usage (zero if no prior `response.completed`).
- qz-thoughts: `stream_terminal_classified` telemetry with classification, not the error cause.
- qz-top: `request_failed` or `request_completed` depending on path.
- Error cause (timeout): telemetry `stream_terminal_classified` with `kind=no_output` or `terminal`.

### Model not ready 503

`build_responses_error_payload` returns `qz.responses.error.v1` with:
- `error`: string ("model not found", "proxy not ready", etc.)
- `reason`: human-readable
- `requested_model`, `available_models`, `proxy_initialization`, `readiness`, `service_status`
- `operator_hint`: clean advisory string

No local paths. No stack traces. Well-bounded.
qz-thoughts: `responses_rejected_*` telemetry.
qz-top: indirect via control-plane status.

### Tool coercion failure

Error result: `{"ok": false, "error": "apply_patch: missing 'diff'..."}` in `function_call_output.output`.
Codex: not directly visible (next-hop input).
Model: visible as tool result next hop.
qz-thoughts: `tool_call_error` telemetry (streaming only).
Raw args: no (coercion errors reference field names, not values).

### web_search runtime error

In-band: `{"ok": false, "action": "search", "error": "Missing query for search."}` in `function_call_output.output`.
Codex: not directly visible.
Model: visible as tool result next hop.
qz-thoughts: `tool_call_completed` with status=failed.
Raw args: no.

### Dropped/unknown tool error

Same format as coercion error. Message is clean ("not available", "not recognised").

### Request timeout / empty answer repair

Fallback message emitted as assistant text. No error type in the Codex-visible response. `stream_terminal_classified` telemetry records classification.

### Reasoning-only abort

Fallback text emitted. `reasoning_only_aborted` telemetry with reason and char count.

---

## 7. Gap Classification

### P0 — Protocol correctness / tool matching

None currently identified as definitely broken.

### P1 — Missing metadata causing bad client behaviour

1. **Synthesised `response.id` mismatch across hops**: After a tool continuation, `_emit_completed` generates `resp_local_{_now_ts()}` for the synthesised terminal. This does not match the `response.id` from `response.created` emitted in hop 1. The Codex SDK may track responses by ID and see two inconsistent IDs for one logical exchange.
   - Affected paths: all multi-hop streaming (web_search, apply_patch, repair).
   - Fix: thread the upstream `response.id` through `StreamRunState` and use it in `_emit_completed`.

2. **Zero usage in synthesised terminals**: Fallback responses (reasoning abort, timeout, tool continuation when drain fails) emit `usage = {}` normalized to all-zeros. Codex receives a completed response with no token accounting.
   - Affected paths: reasoning abort, no-output timeout, terminal timeout, tool continuation on drain failure.
   - Fix: `_drain_stream_for_usage` already attempts to capture usage. Gap only when drain fails (no `response.completed` in drained events). Document the gap; fix requires upstream to always emit `response.completed`.

### P2 — Missing operator/observability metadata

3. **cached_tokens / reasoning_tokens not visible in qz-thoughts**: The token detail fields are in usage but not surfaced in qz-thoughts' rendering.
4. **tool_schema_replaced telemetry missing** (already in B2 plan).
5. **coercion_succeeded/failed telemetry missing** (already in B2 plan).
6. **dropped_tool_names not emitted as telemetry event** (separate from tool_call_error).
7. **runtime_failure_during_request_id not yet populated** (planned for Slice H).

### P3 — Documentation/test gaps

8. **No test for response.model roundtrip**: model in response.created/completed should match the selected model key.
9. **No test for response.id across hops**: verify hop-2 synthesised terminal id vs. hop-1 created id.
10. **No test for usage in synthesised terminals**: verify that zero usage is distinguishable from missing usage.
11. **Synthetic ID collision**: `_now_ts()` is second-precision; multiple items in same second get same ID. No test.
12. **Exception message path leak**: proxy-synthesized `response.failed` includes `str(exception)` without sanitization. Need test that common exception types don't leak paths.

---

## 8. Fixture/Test Plan

Tests for later fix passes (not implemented in this slice):

| test name | what it verifies | fix pass |
|---|---|---|
| `test_response_id_preserved_through_streaming` | Streaming: response.id in forwarded response.created matches response.id in forwarded response.completed (no-tool case) | none — should already work |
| `test_synthesised_terminal_response_id_matches_hop1` | After web_search tool continuation, synthesised response.completed.response.id matches the upstream hop-1 response.id | P1 fix |
| `test_response_model_matches_selected_key` | response.model in forwarded events matches `requested_model` arg, not backend alias | D fix pass |
| `test_output_index_continuity_across_hops` | After tool continuation, next hop's output_index values are monotonically increasing from prior hop's max | none — already covered implicitly |
| `test_call_id_roundtrip_web_search` | function_call.call_id == function_call_output.call_id in next_input | D fix pass |
| `test_usage_preserved_in_normal_completion` | Normal completion: usage.input_tokens > 0, usage.output_tokens > 0, cached_tokens present | existing coverage in sse tests |
| `test_usage_in_reasoning_abort_fallback` | Reasoning abort fallback: usage field contains values from rs.final_usage if available, otherwise all-zeros | P1 fix |
| `test_usage_in_tool_continuation_terminal` | After web_search: synthesised terminal contains usage from drain or all-zeros clearly documented | P1 fix |
| `test_response_failed_no_path_in_error_message` | Proxy-synthesized response.failed: error.message does not contain filesystem path strings | P3 fix |
| `test_cached_tokens_in_normalized_usage` | _normalize_response_usage preserves cached_tokens from both input and detail forms | existing coverage |
| `test_reasoning_tokens_in_normalized_usage` | _normalize_response_usage preserves reasoning_tokens | existing coverage |
| `test_qz_metadata_not_in_codex_response_output` | No `qz_*` fields appear in response output items forwarded to Codex | P3 |
| `test_synthesised_id_collision_guard` | Two items synthesised in same second have unique IDs or document the collision possibility | P3 |

---

## 9. Findings Summary

### Critical metadata bugs (P0)

None confirmed P0 (protocol-breaking). The call_id matching path is correct for normal upstream items.

### Likely bugs (P1)

1. **`response.id` mismatch in multi-hop streaming**: Synthesised `_emit_completed` uses `resp_local_{_now_ts()}`, not the upstream response ID. Codex SDK may see conflicting IDs across a tool-continuation exchange.

2. **Zero usage in synthesised terminals**: All fallback/abort/timeout completions emit zero-token usage when `_drain_stream_for_usage` fails to capture a `response.completed`. Codex receives a completed response with zero token accounting.

### Likely bugs (P2 — observability)

3. `cached_tokens` and `reasoning_tokens` not surfaced in qz-thoughts or qz-top.
4. Zero telemetry for schema replacement, coercion events, dropped tools (tracked in B2 plan).

### Uncertain areas needing live capture

- Whether llama.cpp always emits `response.completed` after a tool break before `[DONE]` — if it does, drain succeeds and zero-usage is never seen. Capture needed.
- Whether llama.cpp's `response.id` is stable across the stream (same ID in `response.created` and `response.completed`) — important for P1 fix scope.
- Whether Codex SDK raises an error or silently accepts mismatched `response.id` across hops.
- Whether exception messages from common failure modes (socket errors, JSON parse errors) contain filesystem paths.

### Proposed fix-pass order

1. **B2** (already planned): coercion telemetry, schema replacement telemetry, non-streaming dropped-tool gap.
2. **P1a — response.id threading**: Add `upstream_response_id: str` to `StreamRunState`; populate from `response.created` event; use in `_emit_completed`. Low-risk, single field addition.
3. **P1b — usage in fallbacks**: Document that zero usage in fallbacks is expected when upstream doesn't drain correctly. Add test asserting zero-usage is explicit, not missing.
4. **C2 — output_text artifact detection** (from Slice C): add `_looks_like_output_text_tool_artifact` in output_text delta path.
5. **P2 — cached/reasoning token observability**: Add to `_telemetry_sse_payload` compact for response.completed.

### Recommended next audit slice

**Slice E — observability audit**: qz-thoughts and qz-top rendering against the contract. Confirm what they display, what they miss, and where they mislead. The Slice A correction (thought panels DO work) makes this lower urgency than previously thought, but P2 gaps in token observability need a focused audit pass.
