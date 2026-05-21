# Runtime Streaming Tool Contract Audit

Date: 2026-05-22
Status: Slice A discovery — authoritative contract map.
This document supersedes stale planning notes on streaming and tool event shapes.

Related:
- `docs/responses-stream-tool-state-contract.md` — earlier living contract; use this document for current shapes.
- `docs/tool-coercion-design.md` — coercion design spec; implementation complete.
- `docs/tool-schema-coercion-audit.md` — Slice B: detailed tool schema replacement, coercion/advice, and failure matrix.
- `docs/streaming-event-mapper-audit.md` — Slice C: mapper boundaries, leak-risk audit, reasoning channel, fixture plan.
- `docs/metadata-propagation-audit.md` — Slice D: metadata flow table, rewrite/synthesis audit, usage/error audit, gap classification.
- `docs/observability-ui-audit.md` — Slice E: qz-thoughts/qz-top visibility tables, reconnect audit, misleading-state audit, token/usage observability.
- `docs/end-to-end-smoke-plan.md` — Slice G: 37-test smoke matrix, command blocks, failure classification, fix-pass ordering H–M.
- `docs/current-architecture-authority.md` — final conflict resolver.

**Slice A correction**: The finding "qz-thoughts thought/answer panels are blank during
Responses API streaming" was INCORRECT. `ResponsesStreamRuntime` does emit `sse_event`
telemetry via chunk_writer → `_write_sse_chunk` → `_emit_sse_telemetry`. qz-thoughts
receives reasoning and output text deltas in real time. See `docs/streaming-event-mapper-audit.md §7`.

---

## 1. Pipeline Map

### Stage 1 — Codex request enters proxy

- Codex sends `POST /v1/responses` with JSON body.
- `qz_request_router.proxy_json_api("/v1/responses")` reads the body.
- Request-scoped ID is assigned: `qz_request_id` (placed in `body.metadata.qz_request_id`).
- Captures written: `latest-request.json`, `latest-request-headers.json`, `latest-request-id.txt`.
- Telemetry emitted: `status_snapshot`, `runtime_snapshot`.

### Stage 2 — Model selection and admission gate

- `ModelCatalog.resolve(client_model)` selects the active model entry.
- `/qz/model/status` checked: `selected_model_ready` must be true, requested identity must match active loaded model.
- If model not ready or mismatch: 503 with `qz.responses.error.v1` payload, telemetry `responses_rejected_*`.
- Request gate (`request_gate`) serialises concurrent requests to one at a time.

### Stage 3 — Tool schema normalisation and coercion context setup

- `ensure_apply_patch_tool_policy(body, overwrite=True)` infers and writes `body.metadata.qz_tool_policy`:
  - `apply_patch_output_style`: `"native"` (apply_patch declared as `type=apply_patch`) or `"custom"` (Codex custom tool) or `"custom"` (absent).
- `normalize_tools_for_llamacpp(body)` → `normalize_tool_request_for_llamacpp(body)`:
  - Iterates `body.tools`:
    - `type=web_search` → replaced by `WebSearchToolAdapter.to_upstream_tool()` (proxy schema).
    - `type=apply_patch` or `type=custom name=apply_patch` → replaced by `ApplyPatchToolAdapter.to_upstream_tool()`.
    - `type=function name=<proxy-owned>` (e.g. `name=web_search`) → replaced by name-based adapter lookup (`ToolRegistry.adapter_for_name`). **Fixed in ebdf87b.**
    - `type=function name=exec_command` → passed through with hint appended.
    - `type=function name=write_stdin` → passed through with hint or dropped (no live session).
    - `type=function name=<other>` → passed through; deduped by seen-name set. **Fixed in ebdf87b.**
    - `type=<unknown-structured>` → dropped.
  - Replacement telemetry: captured in `ToolRequestNormalizationReport.{translated, replaced, dropped}` and written to `latest-dropped-tools.txt` / `forwarded-request-after-tools.json` captures.
  - `body.metadata.qz_dropped_tool_names` records dropped names for downstream coercion routing.
- `dropped_tool_names: frozenset` extracted from metadata for the stream hop loop.
- `repeated_read_state` seeded from `body.input` history.

### Stage 4 — Input normalisation per hop

- `normalize_responses_input_for_qwen(body, selected_model)`: translates Codex input shapes to Qwen-compatible upstream shape (role normalisation, content-type coercion, etc.).
- `normalize_tools_for_llamacpp(body)`: re-applied each hop so tool list is clean.
- `_microcompact_old_tool_results` / `_expand_local_compaction_items` applied to `body.input` once before the hop loop.
- Body deep-copied per hop: `working_body = json.loads(json.dumps(body))`.

### Stage 5 — Upstream request sent to llama.cpp

- `ResponsesStreamRuntime._open_upstream_stream(hop_body)`:
  - `POST {upstream}/v1/responses` with `Content-Type: application/json`, `Accept: text/event-stream`.
  - `hop_body.stream = True`.
- Captures: `latest-upstream-response.raw` (appended per event), `latest-upstream-status.txt`.

### Stage 6 — Upstream SSE stream read and classified

- SSE events read line-by-line; accumulated into `event_lines` until blank line.
- `parse_sse_event_lines(event_lines)` → `(event_type, payload)`.
- `StreamHopState` fields updated: `max_output_index`, `output_text_chars`, `visible_output_text_seen`, `assistant_item_seen`, `public_item_seen`, `reasoning_only_*`, `stream_obs_acc`, `watchdog_state`.
- `StreamRunState` fields updated: `first_output_at`, `final_usage`, `sent_response_start`, `sent_terminal`, `sent_done`.

### Stage 7 — Function call detection and assembly

- Every event passed to `StreamToolCallState.observe(event_type, payload, received_at)`:
  - `StreamedFunctionCallAssembler` accumulates deltas: `response.output_item.added`, `response.function_call_arguments.delta`, `response.function_call_arguments.done`, `response.output_item.done` (function_call).
  - On `response.output_item.done` with function_call item: returns completed call dict.
- `StreamToolCallState.abort_reason(now, timeout_s, delta_limit)`: stall detection (timeout or delta count exceeded).
- All function call stream events are **suppressed from Codex** (see Stage 9).

### Stage 8 — Completed call routing

On `completed = hs.tool_call_state.observe(...)` returning a non-empty list:

```
proxy_tool_registry.completed_call_decision(call, apply_patch_output_style, dropped_tool_names, repeated_read_state)
  → kind = "signal"      (repeated-read advisory)
  → kind = "error"       (dropped/unknown/coercion-failure)
  → kind = "proxy_local" (web_search, braincase tools)
  → kind = "public"      (apply_patch, native exec, native tools)
```

**proxy_local path (web_search)**:
1. `_emit_proxy_local_started` → emits `response.output_item.added` (type=web_search_call, status=in_progress) + lifecycle events to Codex.
2. `ProxyToolRegistry.execute(call, context)` → `WebSearchRuntime.execute_web_search_call(...)`.
3. Returns `ToolContinuationResult(public_item, upstream_items=(function_call, function_call_output), sources)`.
4. `_emit_proxy_local_completed` → emits lifecycle completed event + `response.output_item.done` to Codex.
5. `function_call` + `function_call_output` appended to `hs.next_input` for next hop.
6. `hop_index` loop continues with updated `working_body.input = hs.next_input`.

**public path (apply_patch, native tools)**:
1. `ToolRegistry.output_to_codex(call, apply_patch_output_style)` converts call shape.
2. `_emit_public_tool_item(public_item, public_index, sequence)` → emits `response.output_item.added` + `response.output_item.done` to Codex.
3. `_emit_stream_completed` + `_emit_completed` terminate the stream.
4. Returns immediately (no continuation hop needed; Codex executes the tool).

**error path**:
- `decision.error_result` (a `function_call_output` with `{"ok": false, "error": ...}`) appended to `hs.next_input`.
- No Codex lifecycle events emitted.
- Next hop sees the error result and can retry.

**signal path (repeated-read)**:
- `decision.signal_result` (advisory `function_call_output`) appended to `hs.next_input`.
- Telemetry: `repeated_read_signal`.
- No Codex lifecycle events.

### Stage 9 — Codex-visible stream emission

All non-suppressed events are transformed by `transform_sse_event(event_lines, summary_started, reasoning_stream_format)` and written to the Codex HTTP response via `_write_chunk`.

**Events suppressed from Codex (never forwarded)**:
- `response.function_call_arguments.delta` → `suppressed="function_call"`
- `response.function_call_arguments.done` → `suppressed="function_call"`
- `response.output_item.added` for function_call type → `suppressed="function_call"`
- `response.output_item.done` for function_call type → handled internally, not forwarded
- Terminal events during proxy_local break → `suppressed="web_search_terminal"` (or similar)
- Duplicate `response.created` / `response.in_progress` on subsequent hops → `suppressed="duplicate_response_start"`
- `response.completed` when triggering empty-answer repair → `suppressed="empty_answer_repair_started"`
- Malformed terminal with no payload → `suppressed="malformed_terminal"` (synthesised completion)
- `done` without prior completed when output exists → `suppressed="done_without_completed"`

**Reasoning stream routing** (controlled by `reasoning_stream_format`):
- `"raw"` (default for most models): `response.reasoning_text.delta/done` forwarded as-is to Codex.
- `"summary"`: `reasoning_text.delta` dropped; `reasoning_summary_text.delta` forwarded.
- `"hidden"`: all `type=reasoning` items stripped from payloads; reasoning events dropped.
- Profile override via `selected_model.overrides.{hide_reasoning_stream, reasoning_stream_format, client_reasoning_stream_format}`.

**Reasoning-only abort detection**:
- Tracks reasoning deltas when no `visible_output_text_seen` and no `assistant_item_seen`.
- Abort triggers: `artifact_tool_payload` (reasoning looks like tool JSON), `timeout` (120s default), `char_limit` (disabled by default).
- On abort: synthesized fallback message emitted to Codex; telemetry `reasoning_only_aborted`.

### Stage 10 — Tool results injected into next hop

- `hs.next_input` accumulates: original `body.input` + `function_call` + `function_call_output` (for web_search) or advisory/error items.
- `working_body.input = hs.next_input` before next hop iteration.
- Tool results are standard `function_call_output` items as required by the Responses API.

### Stage 11 — Telemetry emitted

See Section 3 (Event Visibility Table) for full telemetry list.
Notable: `sse_event_timing` emitted per forwarded event; direct telemetry events for tool lifecycle, stream lifecycle, and watchdog events.

**Critical gap — qz-thoughts observability**:
The `ResponsesStreamRuntime` does NOT call `_emit_sse_telemetry` (which wraps forwarded SSE events as `sse_event` telemetry). `_emit_sse_telemetry` is only called from the legacy `/v1/chat/completions` streaming path (`_write_transformed_sse_stream`). Therefore:
- qz-thoughts does NOT see `response.reasoning_text.delta` or `response.output_text.delta` in real time via the normal telemetry SSE path when using the Responses API.
- The thought/answer panels in qz-thoughts remain static during Responses streaming.
- qz-thoughts sees tool lifecycle events (tool_call_started, web_search_route, etc.) but not the model's actual text output.

### Stage 12 — Captures written

Request-scoped captures (when `QZ_CAPTURE_MODE` is set):
- `incoming-request.json` — raw Codex request
- `incoming-request-headers.json` — headers
- `forwarded-request.json` — normalised upstream request
- `request-contract.json` — `qz.capture.contract.v1` with prompt metadata
- `forwarded-request-after-tools.json` — post-tool-normalisation body
- `dropped-tools.txt` — dropped/translated/replaced tool names
- `upstream-response.raw` — raw SSE bytes from upstream
- `upstream-status.txt` — stream metadata
- `forwarded-sse.raw` — SSE forwarded to Codex (for streaming requests)
- `latest-web-search-route.json` — search routing decision

---

## 2. Reference Event Comparison Table

| feature/event | OpenAI Responses expected | Agents SDK handling | llama.cpp observed shape | QuantZhai current handling | Codex visible? | qz-thoughts visible? | gap/bug | proposed test | proposed fix slice |
|---|---|---|---|---|---|---|---|---|---|
| output text delta | `response.output_text.delta {delta, item_id, output_index}` | accumulate | emits same shape | forwarded via transform_sse_event | yes | **no** (sse_event not emitted from Responses path) | qz-thoughts thought/answer panels are blank during streaming | verify thought panel updates during live stream | B: emit sse_event from ResponsesStreamRuntime for key events |
| output text done | `response.output_text.done {text, item_id, output_index}` | mark complete | emits same | forwarded | yes | **no** | same as above | same | B |
| output item added | `response.output_item.added {output_index, item}` | track item | emits same | forwarded if not function_call | yes | yes (via sse_event from legacy path only) | function_call items not forwarded (correct); non-function items forwarded | confirm web_search_call item appears | C |
| output item done | `response.output_item.done {output_index, item}` | complete item | emits same | forwarded if not function_call | yes | yes | same | — | — |
| function call delta | `response.function_call_arguments.delta {delta, item_id}` | accumulate | emits same | **SUPPRESSED** (correct — Codex would execute on partial args) | **no** | **no** | correct suppression; documented | test that delta never appears in forwarded SSE | C |
| function call done | `response.function_call_arguments.done {arguments, item_id}` | complete call | emits same | **SUPPRESSED** (correct) | **no** | **no** | correct | same | C |
| web_search_call lifecycle | `response.web_search_call.{in_progress,searching,completed}` | display | not emitted (proxy-local) | proxy synthesizes via `lifecycle_event_chunks` | yes | yes (via tool_call_started/completed telemetry) | event shape matches OpenAI web_search_call spec | test lifecycle event sequence | C |
| reasoning text delta | `response.reasoning_text.delta {delta}` | accumulate | emits same | forwarded if reasoning_stream_format=raw; dropped if summary/hidden; reasoning_only tracking | yes (raw mode) | **no** (sse_event not emitted from Responses path) | qz-thoughts cannot see live reasoning | test thought panel update | B |
| reasoning summary delta | `response.reasoning_summary_text.delta {delta}` | accumulate | emits same | forwarded if format!=hidden | yes | **no** | same | same | B |
| reasoning done | `response.reasoning_text.done {text}` | mark complete | emits same | forwarded or stripped per format | yes | no | same | — | B |
| tool result injection | `function_call_output {call_id, output}` in input | replay on next turn | n/a (proxy constructs) | injected into next_input; consumed upstream next hop | no (internal) | no | correct — internal only | test that tool result appears in upstream next-hop input | C |
| coercion success | n/a (proxy-internal) | n/a | n/a | corrected arguments used; telemetry none | no | no | no telemetry event emitted for successful coercions | add coercion_success telemetry | B |
| coercion failure | n/a | n/a | n/a | `function_call_output` error injected into next_input | no (error to model next hop) | no | model sees error but qz-thoughts does not | add coercion_failed telemetry | B |
| dropped tool feedback | n/a | n/a | n/a | `function_call_output` error injected | no | no (only tool_call_error emitted) | no Codex lifecycle event; qz-thoughts sees tool_call_error | verify error text in next hop input | C |
| unknown tool feedback | n/a | n/a | n/a | same as dropped | no | no | same | same | C |
| stream completed | `response.completed {response}` | end | emits same | forwarded; `rs.final_usage` extracted; reasoning stripped per format | yes | yes (via request_completed telemetry) | ok | — | — |
| stream failed | `response.failed {response}` | handle | emits same | forwarded | yes | yes (via request_failed telemetry) | ok | — | — |
| backend disconnect mid-stream | connection reset/EOF | error | connection error | raises exception; `stream_failed` telemetry; `response.failed` SSE emitted to Codex | yes | yes | no runtime_failure_* fields recorded yet | test mid-stream connection reset | H |
| duplicate tool schema | n/a | n/a | n/a | **Fixed ebdf87b**: function-typed web_search replaced; seen-name dedup prevents duplicates | n/a | n/a (replacement logged in captures) | **FIXED** | test: function-typed web_search → single proxy schema upstream | already added |
| metadata propagation | response.id, model, usage, created_at, output[] | thread through | emits in response.completed | usage normalized via `_normalize_response_usage`; model written per-hop | yes | partial | output_tokens_details.reasoning_tokens present; cached_tokens present | test usage fields round-trip | D |
| reasoning-only abort | n/a | n/a | n/a | synthesized fallback message emitted | yes (fallback text) | yes (reasoning_only_aborted telemetry) | ok | test fallback fires on artifact pattern | already tested |
| empty-answer repair | n/a | n/a | n/a | repair hop injected with no-tools body | no (internal) | yes (empty_answer_repair_* telemetry) | ok | — | — |

---

## 3. Event Visibility Table

| event / source | example type/name | Codex client visible? | qz-thoughts visible? | telemetry stored? | raw tool args allowed? | local paths/URLs allowed? | expected transformation |
|---|---|---|---|---|---|---|---|
| assistant final text delta | `response.output_text.delta` | **yes** | **no** (Responses path gap) | yes (sse_event_timing only) | no | no | forwarded as-is via transform_sse_event |
| assistant final text done | `response.output_text.done` | yes | no | yes (timing) | no | no | forwarded |
| reasoning text delta (raw) | `response.reasoning_text.delta` | **yes** (raw mode) | **no** | yes (timing) | no | no | forwarded by transform_sse_event; dropped if summary/hidden |
| reasoning summary delta | `response.reasoning_summary_text.delta` | yes (unless hidden) | no | yes (timing) | no | no | forwarded |
| reasoning text done | `response.reasoning_text.done` | yes (raw) | no | timing | no | no | forwarded |
| tool call created | `response.output_item.added` (function_call) | **no** | no | timing (suppressed) | n/a | n/a | suppressed; internal only |
| tool call argument delta | `response.function_call_arguments.delta` | **no** | no | timing (suppressed) | n/a | n/a | suppressed; internal only |
| tool call argument done | `response.function_call_arguments.done` | **no** | no | timing (suppressed) | n/a | n/a | suppressed; internal only |
| tool call completed (upstream) | `response.output_item.done` (function_call) | **no** | no | timing (suppressed) | n/a | n/a | triggers proxy routing; never forwarded |
| web_search lifecycle event | `response.web_search_call.in_progress` | **yes** (proxy-synthesized) | **yes** (via tool_call_* telemetry) | yes (tool_call_started/completed) | no | no | proxy emits per `lifecycle_event_chunks` |
| web_search result | `function_call_output` in next_input | **no** (upstream input, not output stream) | no | yes (web_search_route) | no | **no** (endpoint hidden) | injected into next hop input; never direct Codex output |
| apply_patch public item | `response.output_item.done` (apply_patch_call or custom_tool_call) | **yes** | yes (item.done telemetry) | yes | no (operation only) | no | `_function_call_to_apply_patch_call` / custom path |
| native tool call (exec_command) | `response.output_item.done` (function_call) | **yes** (passed to Codex to execute) | yes (item.done) | yes | yes (model arguments) | yes (safe — Codex sandbox) | passed through unchanged; Codex executes |
| coercion success | internal | no | no | **none** | n/a | n/a | corrected arguments silently used |
| coercion failure | `function_call_output {ok:false, error}` in next_input | **no** (appears as input next hop) | no | tool_call_error | no | no | error text only; no stack trace |
| advisory/repeated-read hint | `function_call_output` in next_input | **no** | no | repeated_read_signal | no | no | advisory message; model sees it; Codex does not |
| dropped tool error | `function_call_output {ok:false}` in next_input | no | no | tool_call_error | no | no | specific "not available in session" message |
| unknown tool error | `function_call_output {ok:false}` in next_input | no | no | tool_call_error | no | no | specific "not recognised" message |
| proxy tool abort | synthesized `response.output_item.done` (message) | yes | yes (private_tool_call_aborted) | yes | no | no | fallback message emitted; no raw internals |
| reasoning-only abort | synthesized `response.output_item.done` (message) | yes | yes (reasoning_only_aborted) | yes | no | no | fallback message; no raw reasoning sample |
| backend disconnect | synthesized `response.failed` | yes | yes (stream_failed) | yes | no | no | error message without stack trace |
| stream completed (normal) | `response.completed` | yes | yes (via request_completed telemetry) | yes | no | no | forwarded; reasoning items stripped per format |
| stream failed (exception) | `response.failed` synthesized | yes | yes (request_failed) | yes | no | no | local error message; no stack trace to Codex |
| internal repair/debug | internal locals / telemetry | **no** | no | timing only | n/a | n/a | never emitted to client |

---

## 4. Metadata Flow Table

| metadata field | source | destination | currently preserved? | Codex-visible? | qz-thoughts-visible? | telemetry-only? | gap/bug | proposed test |
|---|---|---|---|---|---|---|---|---|
| request_id (qz_request_id) | proxy assigned at request entry | body.metadata; captures; telemetry | yes | no (internal) | yes (telemetry event payload) | yes | ok | — |
| response_id | upstream response.created | forwarded in response.created event | yes | yes | yes (sse_event from legacy path) | no | Responses path: qz-thoughts gets response_id only via request_completed telemetry post-stream | verify response_id in qz-thoughts state after stream |
| output item id | upstream or proxy-synthesized | forwarded in output_item.added/done | yes | yes | yes | no | proxy-synthesized IDs stable within hop (proxy_local_item_id) | test item ID stability across proxy_local execution |
| call_id | upstream function_call | threaded through tool execution; in function_call_output | yes | no (internal) | partial (in tool telemetry) | partial | call_id must match between function_call and function_call_output | test call_id roundtrip for proxy_local and public tools |
| model | selected model from catalog | written into hop_body; echoed in response events | yes | yes (in response.completed) | yes (prompt_contract, request_completed) | no | ok | — |
| selected model | proxy selection authority | /qz/model/status | yes | no | yes (qz-top) | no | ok | — |
| loaded model | backend observation | /qz/model/status.backend_loaded_model | yes | no | yes (qz-top) | no | ok | — |
| created_at | upstream response | forwarded in response events | yes | yes | no | no | ok | — |
| completed_at | proxy measures | stream_result dict; request_completed telemetry | yes | no | yes | telemetry | ok | — |
| status | upstream response | forwarded in response.completed | yes | yes | yes | no | ok | — |
| usage | upstream response.completed | `_normalize_response_usage`; forwarded | yes | yes | yes (request_completed) | no | ok; handles OpenAI/llama.cpp field name variants | test usage field normalisation |
| input_tokens | upstream | usage.input_tokens | yes | yes | yes | no | ok | — |
| output_tokens | upstream | usage.output_tokens | yes | yes | yes | no | ok | — |
| input_tokens_details.cached_tokens | upstream | preserved in normalisation | yes | yes | no | no | ok | — |
| output_tokens_details.reasoning_tokens | upstream | preserved in normalisation | yes | yes | no | no | ok | — |
| reasoning metadata | reasoning_only_chars, abort reason | telemetry: reasoning_only_aborted; not in Codex response | partial | no | yes (telemetry) | yes | reasoning_chars not in normal response.completed payload | add reasoning summary field to stream_result |
| tool call metadata | name, call_id, action | tool_call_started/completed telemetry | yes | no | yes | yes | ok | — |
| tool result metadata | ok, action, result shape | function_call_output.output (JSON) | yes | no (upstream only) | no | no | ok | — |
| search profile | args.profile → selected_profile | web_search_route telemetry; result payload | yes | no (result only) | yes | telemetry | ok | — |
| search budget_mode | args.budget_mode → effective mode | web_search_route; result payload | yes | no | yes | telemetry | ok | — |
| retrieval_source | result annotation / retrieval_source arg | search result; retrieve call telemetry | yes | no | no | no | ok | — |
| retrieval_retriever | Agent API response | normalized result payload | yes | no | no | no | ok | — |
| tool schema replacement report | ToolRequestNormalizationReport.replaced | captures only (latest-dropped-tools.txt) | yes | no | **no** | captures | no telemetry event for replacement | add tool_schema_replaced telemetry event |
| coercion/advice report | ToolCoercionResult | nothing emitted | **no** | no | no | — | zero observability on coercions | add coercion_success/coercion_failed telemetry |
| runtime failure metadata | BackendManager.snapshot() | /qz/model/status; /qz/control-plane | partial | no | partial (qz-top) | yes | mid-stream backend death not recorded as runtime_failure_* | implement runtime_failure_during_request_id |

---

## 5. Coercion/Advice Map

### ToolCoercionResult (proxy/qz_tools.py)

```python
@dataclass
class ToolCoercionResult:
    corrected_arguments: str | None = None   # succeeded; re-run with this
    error_message: str | None = None         # failed; inject as function_call_output
```

`succeeded()` returns `corrected_arguments is not None`.
Gap: neither-set case is constructible; produces empty error string silently.

### synthesize_tool_error_result (proxy/qz_tools.py)

Builds `{"type": "function_call_output", "call_id": ..., "output": '{"ok": false, "error": "..."}'}`.
Used by: `ProxyLocalToolRegistry.completed_call_decision` for all error paths.
Not visible to Codex as an event; injected as upstream input to next hop.

### CODEX_NATIVE_TOOL_NAMES (proxy/qz_tools.py)

`frozenset({"exec_command", "write_stdin", "shell_command", "computer"})`
These pass through to Codex unchanged. No coercion applied. No error injected.

### ToolRegistry.coerce_call (proxy/qz_tools.py)

Matches by `spec.name == call.get("name")`. First matching adapter's `coerce()` is called.
Generic fallback: `_coercion_error(name)` — returns `ToolCoercionResult(error_message="Tool call for '{name}' could not be completed by the proxy...")`.

### web_search coerce path (proxy/qz_tool_web.py)

`WebSearchToolAdapter.coerce(call)`:
- Parses `arguments` as JSON; returns error if invalid JSON.
- If JSON is not a dict: returns error.
- If valid dict (even empty): returns `corrected_arguments=arguments` (passes through; runtime validates action/query fields).
- In-band runtime errors returned as `{"ok": false, "error": "..."}` in `function_call_output.output`.

### apply_patch coerce path (proxy/qz_tool_apply_patch.py)

`ApplyPatchToolAdapter.coerce(call)`:
- Tries multiple parse paths: `operation` nested object, sibling `patch` promotion, top-level flat patch, patch envelope extraction.
- On success: `corrected_arguments = json.dumps({"operation": operation})`.
- On failure: `error_message = "apply_patch: {specific_reason}"`.

### dropped tool feedback

Path: `completed_call_decision` → name in `dropped_tool_names` → `synthesize_tool_error_result("Tool '{name}' is not available in this session. It was removed from the tool list...")`.
No Codex lifecycle event. Error appears as `function_call_output` in next hop input.
Telemetry: `tool_call_error` emitted.

### unknown tool feedback

Path: `completed_call_decision` → not in proxy_local, not in adapter registry, not in CODEX_NATIVE_TOOL_NAMES → `synthesize_tool_error_result("Tool '{name}' is not recognised by the proxy...")`.
Same injection path as dropped tool. Telemetry: `tool_call_error`.

### Where coercion/advice reaches streaming

1. `ProxyLocalToolRegistry.completed_call_decision(call, ...)` called in `ResponsesStreamRuntime.run()` on each completed call.
2. `kind="error"` → `decision.error_result` appended to `hs.next_input`; no Codex output.
3. `kind="signal"` → `decision.signal_result` appended to `hs.next_input`; `repeated_read_signal` telemetry.
4. `kind="proxy_local"` with coercion → corrected call passed to executor.
5. `kind="public"` with coercion → corrected call passed to `output_to_codex`.

### Where coercion/advice reaches qz-thoughts

Only via `tool_call_error` telemetry (which includes error message string in payload).
No visibility into coercion success/correction.

### Where coercion/advice may leak as assistant text

**Current risk**: if the model emits tool JSON as `output_text` content (not as a function_call), that text is forwarded to Codex as assistant text. The proxy has:
- `_looks_like_reasoning_tool_artifact` detection for reasoning channel only.
- No equivalent detection for output_text channel.

This is the "tool leak as assistant text" bug vector: the model produces wrong output format, the proxy does not detect it in the output_text stream. Confirmed by `_looks_like_reasoning_tool_artifact` being checked only when `event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}`.

---

## 6. Test Plan for Later Slices

### Slice B: Tool schema, coercion, and observability

- [ ] `test_function_typed_web_search_is_replaced` — already added (ebdf87b).
- [ ] `test_duplicate_tool_names_deduped` — already added (ebdf87b).
- [ ] `test_coercion_success_emits_telemetry` — add `coercion_success` telemetry event and test it fires.
- [ ] `test_coercion_failure_emits_telemetry` — add `coercion_failed` telemetry event.
- [ ] `test_tool_schema_replaced_emits_telemetry` — add `tool_schema_replaced` telemetry event.
- [ ] `test_neither_set_coercion_result_produces_nonempty_error` — guard ToolCoercionResult neither-set case.
- [ ] `test_web_search_empty_args_coercion` — verify coerce() passes through valid-dict args.
- [ ] `test_apply_patch_sibling_patch_promotion` — already tested in apply_patch tests.

### Slice C: Streaming event mapper

- [ ] `test_function_call_delta_suppressed_from_codex_stream` — parse forwarded SSE, assert no `response.function_call_arguments.delta`.
- [ ] `test_function_call_done_suppressed` — same for done.
- [ ] `test_web_search_call_lifecycle_emitted` — forwarded SSE contains `response.web_search_call.in_progress` → `searching` → `completed`.
- [ ] `test_web_search_result_not_in_assistant_text` — forwarded SSE `response.output_text.delta` deltas do not contain tool JSON.
- [ ] `test_tool_call_has_one_public_item` — exactly one `response.output_item.added` + one `response.output_item.done` for web_search call.
- [ ] `test_error_decision_no_lifecycle_event` — error decision emits no `response.output_item.added` to Codex.
- [ ] `test_dropped_tool_error_in_next_input` — dropped tool name → error function_call_output in next hop input.
- [ ] `test_reasoning_text_forwarded_raw_mode` — `response.reasoning_text.delta` forwarded when format=raw.
- [ ] `test_reasoning_text_suppressed_hidden_mode` — stripped when format=hidden.
- [ ] `test_output_text_leak_not_detected_in_output_text_channel` — document current gap; add detection later.
- [ ] `test_repair_hop_suppresses_completed` — repair injection suppresses `response.completed`.

### Slice D: Metadata propagation

- [ ] `test_usage_normalisation_openai_fields` — `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`.
- [ ] `test_usage_normalisation_llamacpp_fields` — `prompt_tokens`, `completion_tokens`, `prompt_eval_count`, `eval_count`.
- [ ] `test_call_id_stable_through_proxy_local_execution` — `call_id` matches between function_call and function_call_output.
- [ ] `test_output_item_id_stable_through_proxy_local` — `proxy_local_item_id` set on public_item before emission.
- [ ] `test_tool_schema_replacement_logged_in_captures` — `latest-dropped-tools.txt` contains `replaced:` line.

### Slice E: qz-thoughts / qz-top observability

- [ ] `test_responses_stream_emits_sse_event_for_reasoning_delta` — **currently missing** from ResponsesStreamRuntime path.
- [ ] `test_responses_stream_emits_sse_event_for_output_text_delta` — **currently missing**.
- [ ] `test_qz_thoughts_thought_panel_updates_during_stream` — end-to-end: thought chars > 0 after stream.
- [ ] `test_qz_thoughts_reconnect_after_proxy_restart` — telemetry feed reconnects within N seconds.
- [ ] `test_qz_thoughts_sequence_reset_safe` — `state.last_seq` reset to 0 on reconnect.
- [ ] `test_qz_top_shows_runtime_failure_fields` — runtime_failure_* populated after mid-stream backend death.

### Slice F: Search profile granularity

- [ ] `test_furry_fse_profile_exists` — already added (ebdf87b).
- [ ] `test_furry_images_profile_exists` — already added.
- [ ] `test_capabilities_lists_furry_fse_and_furry_images` — already added.
- [ ] `test_explicit_engines_fse_reaches_upstream` — `engines=["fse"]` in call → `fse` in upstream SearXNG query.
- [ ] `test_non_text_engines_disabled_respected` — `non_text_engines_disabled=true` blocks image engines but not FSE.

### Slice G: End-to-end smoke

See Section I in the original task for manual smoke steps. No new shell script required.
Acceptance checklist:
- [ ] `qz/model/status` returns `selected_model_ready=true`.
- [ ] Normal chat: no tool JSON in Codex output text.
- [ ] web_search capabilities: profile list includes `furry_fse`, `furry_images`.
- [ ] web_search search→retrieve: function_call events not in Codex stream; result injected next hop.
- [ ] qz-thoughts thought panel updates during streaming (requires Slice E fix).
- [ ] qz-thoughts reconnects after proxy restart.
- [ ] Controlled tool error: Codex receives clean error; no raw internals.

---

## 7. Findings Summary

### Critical bugs likely causing current reported issues

1. **qz-thoughts thought/answer panels are blank during Responses API streaming.**
   - Root cause: `ResponsesStreamRuntime` does not call `_emit_sse_telemetry`, so `sse_event` telemetry is never emitted for `response.reasoning_text.delta` or `response.output_text.delta` on the `/v1/responses` path.
   - `_emit_sse_telemetry` is only called from `_write_transformed_sse_stream` (legacy `/v1/chat/completions` path).
   - Fix: in `ResponsesStreamRuntime._write_transformed_chunks` (or at each forwarded chunk call site), call `self.handler._emit_sse_telemetry(out_chunk)` or a direct telemetry emit for key event types.

2. **Tool leak as assistant text is a model-output problem, not a proxy forwarding problem.**
   - The proxy correctly suppresses all function_call stream events.
   - The leak vector is: model produces tool JSON in `output_text` content instead of as a proper function_call.
   - `_looks_like_reasoning_tool_artifact` detects this in the reasoning channel and aborts.
   - No equivalent detection exists for the `output_text` channel.
   - Fix: add output-text artifact detection (similar heuristic, lower confidence threshold since output_text is user-visible content).

3. **Tool schema replacement had no telemetry/observability.**
   - Fixed structurally (ebdf87b): dedup + name-based replacement.
   - Still missing: `tool_schema_replaced` telemetry event for operator visibility.
   - Captures record it, but telemetry does not.

4. **Zero observability on coercion success/failure.**
   - Coercion results are never emitted to telemetry.
   - Operator cannot tell from qz-thoughts whether a tool call was coerced, whether coercion failed, or what the error was.
   - Fix: emit `coercion_success` and `coercion_failed` telemetry events in `completed_call_decision`.

### Likely missing metadata

- `reasoning_chars` not present in normal `response.completed` payload forwarded to Codex.
- `coercion_report` field absent from any observable surface.
- Mid-stream backend death not recorded as `runtime_failure_during_request_id` in `qz.model_state.v1`.
- `tool_schema_replaced` not in telemetry stream.

### Uncertain areas needing live capture

- Exact `output_text` content when model produces tool JSON (confirm via `latest-forwarded-sse.raw`).
- Whether `_looks_like_reasoning_tool_artifact` misses partial tool JSON in output_text.
- Whether llama.cpp emits `response.function_call_arguments.done` before or after `response.output_item.done` — both orderings seen in practice; proxy should handle both.
- Whether `output_tokens_details.reasoning_tokens` is populated by current llama.cpp version.

### Recommended next slice

**Slice B — qz-thoughts observability fix + coercion telemetry.**

Priority:
1. Add `_emit_sse_telemetry` calls (or equivalent direct telemetry) to `ResponsesStreamRuntime._write_transformed_chunks` for `response.reasoning_text.delta`, `response.output_text.delta`, and `response.reasoning_summary_text.delta`. This fixes the critical blank thought/answer panel gap.
2. Add `coercion_success` / `coercion_failed` telemetry events in `completed_call_decision`.
3. Add `tool_schema_replaced` telemetry event in `normalize_tool_request_for_llamacpp`.
4. Guard `ToolCoercionResult` neither-set case.

Do not: change streaming event shapes, change tool execution logic, or touch backend model launch settings.
