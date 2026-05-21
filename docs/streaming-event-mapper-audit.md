# Streaming Event Mapper Audit

Date: 2026-05-22
Status: Slice C discovery — authoritative mapper boundary map.

Related:
- `docs/runtime-streaming-tool-contract-audit.md` — Slice A (corrected by §8 below).
- `docs/tool-schema-coercion-audit.md` — Slice B.
- `docs/responses-stream-tool-state-contract.md` — earlier living contract.

**Important correction from Slice A**: The Slice A finding that "qz-thoughts thought/answer
panels are blank during Responses API streaming" was INCORRECT. See §8.

---

## 1. Mapper Inventory

Every function that touches SSE parsing, rewriting, suppression, forwarding, or telemetry.

### A. SSE parsing

| function | location | purpose |
|---|---|---|
| `parse_sse_event_lines(event_lines)` | `proxy/qz_streaming.py` | Parse a buffered SSE frame into `(event_type, payload)`. Returns `(event_type, None)` on malformed data. |
| `transform_sse_event(event_lines, summary_started, mode)` | `proxy/qz_sse.py` | Re-parse and transform a frame. In raw mode: return unchanged bytes (except response.completed). In summary mode: convert reasoning_text → reasoning_summary_text. In hidden mode: strip reasoning entirely. |
| `_emit_sse_telemetry(chunk, request_id)` | `proxy/quantzhai_proxy.py` | Parse a forwarded SSE chunk and emit `sse_event` telemetry for allowlisted event types. Called on every chunk written via `_write_sse_chunk`. |

### B. SSE payload rewriting

| function | location | purpose |
|---|---|---|
| `rewrite_sse_payload(event_type, payload, output_index_offset, prepend_output, model)` | `proxy/qz_streaming.py` | Shallow-copy payload; apply output_index offset; prepend prior public_trace to response.output if present; rewrite model name. |
| `_normalize_response_usage(usage)` | `proxy/qz_sse.py` | Normalize usage field names across OpenAI and llama.cpp conventions. Applied to response.completed payload. |
| `_strip_reasoning_from_payload(obj)` | `proxy/qz_sse.py` | Remove `type=reasoning` items from output lists and from item payloads. Used by hidden mode. |
| `_convert_reasoning_item_to_summary(item)` | `proxy/qz_sse.py` | Convert a reasoning item's content to summary format; clear content. Used by summary mode for item-level events. |

### C. Event suppression

| location | condition | suppressed event(s) | suppression_reason in telemetry |
|---|---|---|---|
| `qz_responses_stream.py` main loop | `is_function_call_stream_event` AND no `completed` yet | `response.function_call_arguments.delta`, `.done`, `response.output_item.added/done` for function_call | `"function_call"` |
| main loop | `_should_suppress_proxy_local_terminal(event_type, payload, completed_call, is_proxy_local)` | terminal event during proxy-local tool execution | `"web_search_terminal"` (or registry-owned name) |
| main loop | `_should_suppress_duplicate_response_start(event_type, rs.sent_response_start)` | second+ `response.created` / `response.in_progress` in multi-hop | `"duplicate_response_start"` |
| main loop | `response.completed` with `completed_without_answer` AND repair budget remaining | `response.completed` | `"empty_answer_repair_started"` |
| main loop | `response.completed` with `payload is None` AND `public_trace` AND `not rs.sent_terminal` | malformed terminal | `"malformed_terminal"` (synthesised completion) |
| main loop | `done` or `[DONE]` with `public_trace` AND `not rs.sent_terminal` | bare DONE without prior completed | `"done_without_completed"` (synthesised completion) |
| main loop | reasoning abort fires | current reasoning delta | `"reasoning_only_aborted"` or `"reasoning_artifact_aborted"` |
| main loop | function-call stall abort fires | current function_call delta | `"function_call_aborted"` |
| main loop (signal) | `decision.kind == "signal"` | function_call item treated as repeated-read | `"repeated_read_signal"` |
| main loop (error) | `decision.kind == "error"` | function_call item treated as dropped/unknown | `"function_call_error"` |

### D. Reasoning transformation

| mode | function | what happens to reasoning_text.delta | what happens to reasoning in response.completed |
|---|---|---|---|
| `raw` | `transform_sse_event` | forwarded unchanged (original bytes via `b"".join(event_lines)`) | reasoning items kept in output list; `response.completed` re-serialized (usage normalized) |
| `summary` | `transform_sse_event` | converted: `reasoning_text.delta` → injects `reasoning_summary_part.added` (first time per item_id) + `reasoning_summary_text.delta` | reasoning items converted via `_convert_reasoning_item_to_summary`; content cleared, summary kept |
| `hidden` | `transform_sse_event` | dropped (returns `[]`) | reasoning items stripped via `_strip_reasoning_from_payload`; not forwarded |

### E. Function/tool call detection

| function | location | purpose |
|---|---|---|
| `is_function_call_stream_event(event_type, payload)` | `proxy/qz_streaming.py` | Returns True for `response.function_call_arguments.{delta,done}` and `response.output_item.{added,done}` with function_call items. |
| `StreamedFunctionCallAssembler.observe(event_type, payload)` | `proxy/qz_streaming.py` | Accumulates argument deltas. Returns completed call on `response.output_item.done`. |
| `StreamToolCallState.observe(event_type, payload, received_at)` | `proxy/qz_tool_lifecycle.py` | Wraps assembler; tracks stall timing and delta count for abort detection. |
| `_looks_like_reasoning_tool_artifact(text)` | `proxy/qz_responses_stream.py` | Heuristic: is the current reasoning_text sample a tool/patch payload? Checks for `"operation"`, `"path"`, `"diff"`, `apply_patch`, `---a/`, `+++ b/`, `@@` markers. |

### F. Proxy-local tool lifecycle events

| function | location | emits to Codex |
|---|---|---|
| `_emit_proxy_local_started(call, public_index, sequence)` | `qz_responses_stream.py` | `response.output_item.added` (type=web_search_call, status=in_progress) + lifecycle start events |
| `ProxyLocalToolRegistry.lifecycle_start_event_chunks(call, item_id, output_index, seq)` | `qz_proxy_tools.py` | `response.web_search_call.in_progress`, `response.web_search_call.searching` |
| `_emit_proxy_local_completed(call, public_item, public_index, sequence, item_id)` | `qz_responses_stream.py` | Lifecycle done events + `response.output_item.done` |
| `ProxyLocalToolRegistry.lifecycle_done_event_chunks(call, item_id, output_index, seq)` | `qz_proxy_tools.py` | `response.web_search_call.completed` |
| `_emit_public_tool_item(item, output_index, sequence)` | `qz_responses_stream.py` | `response.output_item.added` + `response.output_item.done` for public tools |

### G. Tool result injection

| path | function | how result enters next hop |
|---|---|---|
| proxy-local | `ProxyLocalToolRegistry.execute(call, context)` → `ToolContinuationResult.upstream_items` | `function_call` + `function_call_output` appended to `hs.next_input` → `working_body["input"]` |
| error | `synthesize_tool_error_result(call, message)` | `error_result` appended to `hs.next_input` |
| signal | `render_advisory_output(call, message)` | `signal_result` appended to `hs.next_input` |

### H. Response completion events

| what | how emitted |
|---|---|
| Normal `response.completed` forwarded | `_write_transformed_chunks(_transformed_chunks(...))` — forwarded from upstream with rewriting |
| `response.completed` synthesised (fallback) | `_emit_completed(requested_model, public_trace, summary_started, usage)` → `make_response_stream_events(out)` → chunks written via `_write_chunk` |
| `data: [DONE]` synthesised | Direct `_write_chunk(b"data: [DONE]\n\n")` when `rs.sent_terminal && !rs.sent_done` |
| `response.failed` synthesised (stream exception) | `make_sse_block("response.failed", error_payload)` + `data: [DONE]` via `_write_sse_chunk` |
| `response.failed` synthesised (timeout) | `_emit_no_output_timeout_fallback` / `_finish_terminal_timeout_after_output` → `_emit_completed` |

### I. qz-thoughts telemetry emission

**Key fact (corrects Slice A)**: `ResponsesStreamRuntime` uses `chunk_writer = lambda chunk: self._write_sse_chunk(chunk, request_id=request_id)`. `_write_sse_chunk` calls `self.handler._emit_sse_telemetry(chunk)` for every forwarded chunk. So `sse_event` telemetry IS emitted for `response.reasoning_text.delta` and `response.output_text.delta` from the Responses streaming path.

| event type | visible to qz-thoughts? | how |
|---|---|---|
| `response.reasoning_text.delta` | **yes** | forwarded via chunk_writer → `_emit_sse_telemetry` → `sse_event` telemetry |
| `response.output_text.delta` | **yes** | same |
| `response.reasoning_summary_text.delta` | **yes** | same |
| `response.function_call_arguments.delta` | **no** | suppressed; chunk_writer not called; no `sse_event` emitted |
| `response.output_item.added/done` (function_call) | **no** | suppressed |
| web_search lifecycle events | **yes** | via `tool_call_started`, `tool_call_completed` telemetry events |
| web_search_route | **yes** | `web_search_route` telemetry event |

### J. Stream captures

| file | written by | content |
|---|---|---|
| `latest-upstream-response.raw` / `upstream-response.raw` | `_open_raw_log()` in `ResponsesStreamRuntime` | raw bytes from upstream (pre-transform) |
| `latest-upstream-status.txt` | `_start_capture()` | stream metadata: mode, content type |
| `forwarded-sse.raw` | `_write_sse_chunk(chunk, request_id=request_id)` via `append_request_capture` | bytes forwarded to Codex (post-transform) |
| `latest-forwarded.json`, `forwarded-request.json` | `proxy_json_api` | normalised upstream request |
| `latest-web-search-route.json` | `WebSearchRuntime._search_web` | search routing decision |

---

## 2. Event Boundary Table

| event | upstream source | current QuantZhai handling | Codex-visible? | qz-thoughts-visible? | telemetry? | capture? | leak risk | missing visibility risk | reference expectation | proposed fix pass |
|---|---|---|---|---|---|---|---|---|---|---|
| `response.output_text.delta` | llama.cpp emits | forwarded via transform_sse_event (raw: unchanged; summary/hidden: unchanged — not a reasoning event) | **yes** | **yes** (sse_event telemetry) | sse_event_timing + sse_event | forwarded-sse.raw | model can emit tool JSON here — **not detected** | none | OpenAI Responses: forwarded as-is | D2: add output_text artifact detection |
| `response.output_text.done` | llama.cpp | forwarded | yes | yes (sse_event) | timing + sse_event | yes | model can finish tool JSON in done text | none | forwarded | D2 |
| `response.reasoning_text.delta` | llama.cpp | forwarded (raw) or converted to `reasoning_summary_text.delta` (summary) or dropped (hidden) | raw/summary: yes; hidden: no | raw/summary: yes (sse_event); hidden: no | timing + sse_event | yes | model emits tool JSON in reasoning: **detected** by `_looks_like_reasoning_tool_artifact` → abort | none | OpenAI Responses: forwarded in raw/summary modes | correct |
| `response.reasoning_text.done` | llama.cpp | same routing as .delta | same | same | timing + sse_event | yes | none | none | forwarded | correct |
| `response.reasoning_summary_text.delta` | llama.cpp | forwarded | yes | yes | timing + sse_event | yes | none | none | forwarded | correct |
| `response.reasoning_summary_text.done` | llama.cpp | forwarded | yes | yes | timing + sse_event | yes | none | none | forwarded | correct |
| `response.output_item.added` (message) | llama.cpp | forwarded | yes | yes (sse_event, item stripped to id/type/status/role) | timing + sse_event | yes | none | none | forwarded | correct |
| `response.output_item.done` (message) | llama.cpp | forwarded | yes | yes | timing + sse_event | yes | none | none | forwarded | correct |
| `response.output_item.added` (function_call) | llama.cpp | **suppressed** | **no** | **no** | stream_event_timing(suppressed=function_call) | upstream-response.raw only | none | **no qz-thoughts visibility** into model-originated function call starts | OpenAI Responses: suppressed by design (args incomplete) | correct |
| `response.output_item.done` (function_call) | llama.cpp | **suppressed** from Codex; triggers completed_call_decision | **no** | **no** | stream_event_timing(suppressed=function_call_private) | upstream-response.raw | none | none | suppressed by design | correct |
| `response.function_call_arguments.delta` | llama.cpp | **suppressed** | **no** | **no** | stream_event_timing(suppressed=function_call) | upstream-response.raw | none | none | suppressed by design | correct |
| `response.function_call_arguments.done` | llama.cpp | **suppressed** | **no** | **no** | stream_event_timing | upstream-response.raw | none | none | suppressed | correct |
| `response.web_search_call.*` lifecycle | proxy-synthesized | emitted to Codex by `_emit_proxy_local_started/completed` | **yes** | **yes** (tool_call_started/completed telemetry) | tool_call_started, tool_call_completed | forwarded-sse.raw | none | none | OpenAI: web_search_call lifecycle events | correct |
| `function_call_output` injection | proxy-synthesized | appended to next_input; NOT emitted as SSE event | **no** | **no** | depends on path (tool_call_completed) | none | none | correct — internal to hop loop | n/a | correct |
| `response.completed` (normal) | llama.cpp | forwarded; usage normalized; reasoning stripped per mode; output_index_offset applied | **yes** | **yes** (sse_event telemetry, compacted) | sse_event (compacted: id/model/status/usage) | yes | none | none | OpenAI: forwarded | correct |
| `response.failed` | llama.cpp | forwarded; or synthesised on exception | **yes** | **yes** (sse_event or request_failed) | sse_event or stream_failed | yes | none | none | forwarded | correct |
| malformed/unknown event | llama.cpp | `parse_sse_event_lines` returns (None, None) or (event_type, None); `transform_sse_event` returns raw bytes | depends on raw mode | forwarded chunk emitted as sse_event if parseable | timing | upstream-response.raw | none | could silently pass garbage | OpenAI: undefined | none needed now |
| backend disconnect mid-stream | socket timeout / EOF | `TimeoutError` caught → `stream_timeout_kind` → fallback message; or EOF → `break` then `_emit_stream_completed` | yes (fallback msg or clean terminal) | yes (stream_terminal_classified or stream_completed) | stream_terminal_classified | upstream partial capture | none | none | provider-dependent | correct |

---

## 3. Leak-Risk Audit

### L1 — Raw tool args emitted as assistant final text

**Risk path**: Model generates a `function_call` item, arguments arrive as `response.function_call_arguments.delta`. All delta events are **suppressed** (suppressed="function_call"). Arguments NEVER appear in the Codex SSE stream.
**Status**: No leak. Confirmed by tests (`test_web_search_call_is_public_and_upstream_resumes_with_hidden_output` — `assertNotIn('"type": "function_call"', stream_text)`).

### L2 — Raw tool results emitted as assistant final text

**Risk path**: `function_call_output` from proxy execution goes into `hs.next_input`. It is sent upstream in the next hop request body. It is NOT emitted as an SSE event to Codex.
**Status**: No leak. Tool results are internal to the hop loop.

### L3 — Tool lifecycle emitted as assistant text

**Risk path**: Proxy-synthesized `response.web_search_call.*` events are emitted as structured SSE items, not as `response.output_text.delta`. Codex receives them as `web_search_call` items, not as text.
**Status**: No leak. Lifecycle events are typed items.

### L4 — Model emits tool JSON in output_text (critical gap)

**Risk path**: Model produces `response.output_text.delta` with content like `{"action": "apply_patch", ...}` or `*** Begin Patch ...`. The proxy has NO detection for this in the output_text channel. The content is forwarded to Codex as assistant text.
**Confirmed gap**: `_looks_like_reasoning_tool_artifact` is only called when `event_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}`. There is no equivalent check for `response.output_text.delta`.
**Codex receives**: The tool JSON as assistant text. Codex may try to parse it, or the user sees raw tool instructions.
**Fix**: Add output_text artifact detection (lower confidence threshold than reasoning, since output_text is legitimate user-visible content — look for patch-envelope markers specifically).

### L5 — Model emits tool JSON in reasoning_text

**Risk path**: Same as L4 but in reasoning channel. `_looks_like_reasoning_tool_artifact` detects this and triggers `"artifact_tool_payload"` abort.
**Status**: **Detected and handled**. Fallback message emitted; no raw tool payload reaches Codex.
**Confirmed by test**: `test_golden_reasoning_artifact_aborts_without_executing_tool`.

### L6 — Proxy repair/debug text emitted as reasoning

**Risk path**: `EMPTY_ANSWER_REPAIR_MESSAGE` is injected as a user message in `next_input` — upstream conversation only. `_emit_reasoning_only_aborted` fallback text is emitted as a `response.output_text` message item to Codex.
**Status**: Fallback text is safe plain English. No debug/stack traces in any emitted message.

### L7 — Function call deltas accidentally forwarded

**Risk path**: `is_function_call_stream_event` returns True → `hs.event_lines = []; continue` → no forward call. The delta is never written to `chunk_writer`.
**Status**: Cannot happen. The suppression path clears event_lines before any write.
**Confirmed by test**: `test_web_search_call_is_public_and_upstream_resumes_with_hidden_output` + golden tests.

### L8 — Duplicated final text after tool continuation

**Risk path**: After proxy-local tool execution, the next hop produces a final answer. `rewrite_sse_payload` with `prepend_output=public_trace` adds prior public items (web_search_call items) to `response.completed.response.output`. This could duplicate items visible in the stream if both `response.output_item.added/done` AND `response.completed.output` contain the same web_search_call.
**Analysis**: The `response.output_item.added/done` lifecycle events are emitted for web_search_call items. Then on `response.completed`, `prepend_output=public_trace` adds the same items to the response's output list. Codex may see both the real-time events AND the final static output. This is the intended OpenAI Responses protocol pattern — the `response.completed` event is the canonical final state. Codex's SDK should deduplicate or ignore duplicates in the final snapshot.
**Status**: Not a proxy bug. Follows OpenAI Responses protocol.

### L9 — Missing final response.completed

**Risk path**: Stream ends with only `[DONE]` and no `response.completed`.
**Handling**: Lines 2113-2122 in `ResponsesStreamRuntime.run()`:
```python
if public_trace and not rs.sent_terminal and not rs.sent_done:
    self._emit_stream_completed(...)
    self._emit_completed(...)
    rs.sent_terminal = True
    rs.sent_done = True
```
Also `_drain_stream_for_usage` drains post-tool-break events to capture usage before close.
**Status**: Handled. Synthesized completion emitted if missing.
**Confirmed by test**: `test_golden_public_function_call_without_done_still_completes_once`.

### L10 — Missing stream failure marker

**Risk path**: Exception in stream loop without sending any terminal event to Codex.
**Handling**: `except Exception as exc:` in `proxy_json_api` → synthesized `response.failed` SSE + `data: [DONE]` via `_write_sse_chunk`. `stream_failed` telemetry emitted by `ResponsesStreamRuntime`.
**Status**: Handled. Confirmed by test: `test_client_disconnect_closes_upstream_and_emits_cancel_telemetry`.

---

## 4. Reasoning Channel Audit

### Raw mode (default)

- `transform_sse_event` returns `[b"".join(event_lines)]` — original bytes passed unchanged.
- Exception: `response.completed` is re-serialized with usage normalized.
- Reasoning items remain in `response.completed.response.output`.
- Codex receives: `response.reasoning_text.delta/done`, full reasoning in terminal.
- qz-thoughts: receives `sse_event` for all forwarded reasoning events. `_apply_thought_delta` updates thought panel.

### Summary mode

- `reasoning_text.delta` → emits `reasoning_summary_part.added` (first time per item_id via `summary_started` set) + `reasoning_summary_text.delta`.
- `reasoning_text.done` → emits `reasoning_summary_text.done` + `reasoning_summary_part.done`.
- Other events: passed through (including response.completed with items converted via `_convert_reasoning_item_to_summary`).
- Codex receives: no raw reasoning tokens; only summary text events.
- qz-thoughts: `_apply_thought_delta` fires on `reasoning_summary_text.delta` events.

### Hidden mode

- All `response.reasoning_*` events dropped (`return []`).
- `_strip_reasoning_from_payload` removes reasoning items from output lists in `response.output_item.added/done` and `response.completed`.
- Codex receives: no reasoning events; reasoning items absent from terminal.
- qz-thoughts: no thought panel updates.

### DeepSeek-style `<think>` tag handling

**Status: none implemented.** If llama.cpp or a future backend emits `<think>` tags within `output_text` content (a common DeepSeek inference behavior), the proxy has no extraction or filtering. `<think>` content would appear as assistant final text to Codex. This is currently not an issue since Qwen/TurboQuant emits proper `response.reasoning_text.*` events, but it is a compatibility gap for future model support.

### Reasoning-only abort

Trigger conditions (evaluated per reasoning delta when no visible output yet):
1. `artifact_tool_payload`: `_looks_like_reasoning_tool_artifact` returns True (scan up to `REASONING_ARTIFACT_SCAN_LIMIT=8192` chars).
2. `timeout`: `now - reasoning_only_progress_at > REASONING_ONLY_TIMEOUT_S` (default 120s; disabled if < 0).
3. `char_limit`: `reasoning_only_chars > REASONING_ONLY_CHAR_LIMIT` (default -1 = disabled).

On abort:
- Current event suppressed.
- Fallback message emitted to Codex as `response.output_text` item.
- `reasoning_only_aborted` telemetry emitted.
- Stream terminates.

### Reasoning during tool calls

When a proxy-local tool call fires (proxy-local break), the hop terminates and restarts. Any reasoning tokens accumulated in `hs.reasoning_only_*` are local to the current hop and are reset in `StreamHopState.fresh()` on the next hop. **Reasoning tokens from before the tool call ARE forwarded to Codex** (they passed through the forwarding path before the tool call was detected). **Reasoning tokens from after the tool call** in the same upstream response are impossible since the hop terminates at the tool call.

**Confirmed**: `reasoning_carry_forward` (disabled by default) optionally injects a user message with a 300-char reasoning snippet into the next hop.

### Reasoning duplication into final text

Risk: model produces identical content in both reasoning and output_text channels.
Status: Not a proxy problem. If both are emitted, both are forwarded. No proxy-side deduplication. This is a model behaviour issue.

---

## 5. Tool Continuation Audit

### web_search proxy-local call flow

1. **Upstream emits** `response.output_item.added` (function_call, name=web_search) → **suppressed** from Codex.
2. **Upstream emits** `response.function_call_arguments.delta` (one or more) → **suppressed**; accumulated in `StreamedFunctionCallAssembler`.
3. **Upstream emits** `response.function_call_arguments.done` → **suppressed**.
4. **Upstream emits** `response.output_item.done` (function_call, name=web_search) → triggers `completed_call_decision`:
   - `coerce()` validates arguments.
   - Returns `kind="proxy_local"`.
5. **Proxy emits to Codex**: `response.web_search_call.in_progress` lifecycle + `response.output_item.added` (web_search_call, in_progress).
6. **Proxy executes**: `WebSearchRuntime.execute_web_search_call(call, counters, seen_signatures)`.
7. **Proxy emits to Codex**: `response.web_search_call.searching` + `response.web_search_call.completed` + `response.output_item.done` (web_search_call, completed).
8. **Proxy injects into next_input**: `function_call` + `function_call_output` items.
9. **Proxy suppresses** upstream terminal event (`_should_suppress_proxy_local_terminal` → True).
10. **Drains** remaining upstream SSE to capture usage (`_drain_stream_for_usage`).
11. **Output index offset** updated: `rs.output_index_offset += hs.max_output_index + 1`.
12. **Next hop** opens new upstream request with `working_body["input"] = hs.next_input`.
13. **Next hop response** continues normally; `prepend_output=public_trace` ensures web_search_call item appears in terminal `response.completed.output`.

### What gets suppressed

- All upstream function_call SSE events (added, delta, done, item_done).
- Upstream terminal event during proxy-local execution.
- Duplicate response.created/in_progress on next hop.

### What gets emitted as lifecycle

- `response.output_item.added` (web_search_call, in_progress).
- `response.web_search_call.in_progress` + `response.web_search_call.searching`.
- `response.web_search_call.completed`.
- `response.output_item.done` (web_search_call, completed).

### What goes into next_input

- Original `function_call` item (with correct call_id for correlation).
- `function_call_output` item with JSON result or error.

### How completion is emitted

The next hop emits `response.completed` from upstream with the final answer. `rewrite_sse_payload` prepends `public_trace` (containing the web_search_call item) to the response.output list. So the terminal `response.completed` contains both the web_search_call and the final message.

### Where duplicate/missing completion can happen

- **Missing completion**: If the tool execution raises, `hs.error_injected=True` breaks the hop; `_drain_stream_for_usage` is called; next hop normally completes. If next hop also fails, the continuation limit fallback message is emitted as the final completion. The fallback is always emitted, so terminal is never truly missing.
- **Duplicate completion**: Multi-hop: `rs.sent_response_start` prevents re-emitting response.created/in_progress. `rs.sent_terminal` prevents re-emitting terminal. Each hop can only produce one terminal via the `if is_terminal_stream_event` path.
- **Duplicate web_search_call item**: `public_trace` is accumulated across hops. On completion, `prepend_output=public_trace` adds all prior items. Codex sees them once in the real-time stream (lifecycle) and once in the terminal snapshot. The OpenAI protocol expects this — the terminal is the canonical final state.

---

## 6. Capture/Fixture Plan

### Existing SSE fixtures (tests/fixtures/sse/)

| fixture file | event sequence | used in |
|---|---|---|
| `basic_message.raw` | response.created → output_item.added(message) → output_text.delta → response.completed → [DONE] | golden stream tests |
| `public_function_call.raw` | response.created → output_item.added(function_call/exec_command) → function_call_arguments.delta → output_item.done → response.completed → [DONE] | buffering tests |
| `web_search_call.raw` | response.created → output_item.added(function_call/web_search) → function_call_arguments.delta → output_item.done → [DONE] | web_search continuation tests |
| `web_search_final.raw` | final answer after tool continuation | web_search continuation tests |
| `reasoning_only.raw` | response.created → reasoning_text.delta (×n) → response.completed(reasoning only) → [DONE] | empty-answer repair tests |
| `reasoning_artifact.raw` | reasoning_text.delta with tool JSON content | artifact abort tests |
| `long_active_reasoning.raw` | reasoning_text.delta (long) followed by output_text | char limit non-abort test |
| `qwen_create_file_sibling_patch.raw` | function_call(apply_patch) with sibling patch shape | apply_patch coerce tests |
| apply_patch family | various apply_patch shapes | 20+ golden apply_patch tests |
| `malformed_terminal.raw` | response.failed with null payload | malformed terminal handling |
| `completed_without_done.raw` | response.completed without [DONE] | DONE injection test |
| `done_only.raw` | [DONE] without prior completed | synthesised completion test |

### Missing fixtures needed for fix passes

| fixture name | event sequence | purpose |
|---|---|---|
| `output_text_tool_json.raw` | response.created → output_item.added(message) → output_text.delta ("*** Begin Patch ... *** End Patch") → response.completed | Test L4: model-output tool JSON in output_text — confirm no detection currently; add detection later |
| `output_text_web_search_json.raw` | output_text.delta with `{"action":"search","query":...}` | Same gap, web_search shape |
| `web_search_malformed_args.raw` | function_call(web_search) with bad JSON args → output_item.done → [DONE] | Streaming coerce error path (missing from Slice B) |
| `apply_patch_bare_operation.raw` | already exists as `qwen_create_file_bare_operation.raw` | Coerce error streaming path — already has golden test |
| `reasoning_plus_text.raw` | reasoning_text.delta (×n) → output_text.delta → response.completed | Confirm reasoning does NOT duplicate into final text |
| `web_search_reasoning_then_call.raw` | reasoning_text.delta → function_call(web_search) → [DONE] | Confirm reasoning before tool call is forwarded; reasoning after is not lost |
| `backend_disconnect_mid_stream.raw` | response.created → output_text.delta (partial) → [silent EOF] | Backend disconnect mid-stream; confirm fallback emitted |
| `empty_answer_repair_no_reasoning.raw` | response.completed with empty output, no reasoning | Confirm repair NOT triggered without reasoning chars |
| `reasoning_only_raw_mode.raw` | reasoning_text.delta with char limit exceeded | Confirm raw mode abort works same as summary mode |
| `duplicate_response_created.raw` | response.created (hop 1) + response.created (hop 2 in same stream) | Confirm duplicate suppression |

---

## 7. Findings Summary

### Critical correction from Slice A

**The Slice A finding "qz-thoughts thought/answer panels are blank during Responses API streaming" was INCORRECT.**

`ResponsesStreamRuntime` uses `chunk_writer = lambda chunk: self._write_sse_chunk(chunk, request_id=request_id)`. `_write_sse_chunk` calls `self.handler._emit_sse_telemetry(chunk)` for every forwarded chunk. `_emit_sse_telemetry` emits `sse_event` telemetry for allowlisted event types including `response.reasoning_text.delta` and `response.output_text.delta`.

qz-thoughts connects to `/qz/telemetry/stream` and processes `sse_event` events via `_apply_response_event`. The thought and answer panels DO update in real time during Responses API streaming.

### Critical bugs

1. **L4 — Model-output tool JSON in output_text: not detected.**
   The proxy detects tool artifacts in the reasoning channel (`_looks_like_reasoning_tool_artifact`) but NOT in the output_text channel. If the model emits tool instructions as assistant final text, they reach Codex unmodified. This is the actual "tool leak as assistant text" vector mentioned in the original problem statement.
   Recommended fix: add `_looks_like_output_text_tool_artifact` check in the `response.output_text.delta` accumulation path. Only abort on strong signals (patch envelope markers) to avoid false positives on legitimate code snippets.

### Likely bugs

2. **DeepSeek think-tag content passes through as output_text.** Not a Qwen issue currently. Low priority.

3. **`_emit_sse_telemetry` not called on suppressed events.** This is actually correct and intentional. Function call deltas should not appear in qz-thoughts. No bug.

### Uncertain areas needing live capture

- Whether llama.cpp ever emits `response.output_text.delta` that begins with tool JSON (vs. pure reasoning_text). Needs a capture from a session where the model confuses tool vs text channels.
- Whether `reasoning_carry_forward` causes any content duplication into the next hop's visible output. Currently disabled by default.
- Whether `prepend_output=public_trace` in `rewrite_sse_payload` causes duplicate item IDs visible to Codex's SDK. The protocol expects the terminal to be the canonical state; a Codex SDK bug here is possible.

### Recommended next audit slice

**Slice D — metadata propagation**: Audit `request_id`, `response_id`, `call_id`, `output_item_id` stability across hops; `usage` normalization; reasoning token counts in terminal.

### Recommended fix pass order

1. **B2 fixes first**: ToolCoercionResult guard, non-streaming dropped tool gap, missing coercion telemetry (already planned).
2. **L4 fix (output_text tool artifact detection)**: Add detection in the streaming loop for patch-envelope markers in output_text.delta. The existing reasoning abort mechanism is a template.
3. **Slice E fixture tests**: Add 4 missing streaming coercion fixtures (from Slice B missing test list).
4. **Slice D audit**: Metadata propagation before any observability work.
