# Codex-Visible Tool Lifecycle Audit

Date: 2026-05-25
Status: Source-grounded. No implementation changes.

This document audits exactly which SSE events Codex sees during a tool call's lifecycle in the QuantZhai streaming runtime. It contrasts Codex-visible events with operator-only telemetry and model-facing feedback, then identifies gaps and recommends future implementation slices.

**Constraints on this pass:** Do not implement lifecycle changes. Do not invent custom `qz_*` SSE events for Codex. Do not send operator telemetry to Codex. Do not expose raw tool arguments, full schemas, or qz internal state. Do not change tool execution, model-visible `function_call_output` feedback, web_search behaviour, apply_patch behaviour, BrainCase runtime behaviour, or watchdog defaults.

---

## 1. Purpose and Scope

**What this document answers:**
- Which SSE event types reach the Codex client per tool-call path?
- Which events are always suppressed (never leave the proxy)?
- How do proxy-local, protocol-adapter, native, error, signal, and dropped paths differ from the Codex perspective?
- Where does QuantZhai diverge from or extend the official Responses streaming event schema?

**Out of scope:** operator telemetry internals, model-facing `function_call_output` bodies, BrainCase, watchdog calibration, schema normalisation. Those are covered in existing audit docs.

**Source files consulted:**

```
proxy/qz_responses_stream.py   — streaming runtime and event dispatch
proxy/qz_streaming.py          — SSE helper functions
proxy/qz_tools.py              — ToolLifecycleSpec, CODEX_NATIVE_TOOL_NAMES
proxy/qz_tool_web.py           — web_search lifecycle spec
proxy/qz_tool_apply_patch.py   — apply_patch lifecycle spec
proxy/qz_sse.py                — make_response_stream_events (non-streaming path)
proxy/qz_tool_lifecycle.py     — ToolContinuationResult, StreamToolCallState
tests/test_qz_streaming.py
tests/test_qz_tool_lifecycle.py
tests/test_qz_proxy_tools.py
tests/test_qz_responses_stream.py   (StreamingToolErrorFixtureTests)
docs/streaming-event-mapper-audit.md
docs/runtime-streaming-tool-contract-audit.md
```

---

## 2. Official Responses API Lifecycle Event Reference

QuantZhai targets the OpenAI Responses streaming API. The official event types that the API defines for tool-related items are:

| Event type | Purpose |
|---|---|
| `response.created` | Response object created, output empty |
| `response.in_progress` | Generation started |
| `response.output_item.added` | A new output item has started (message, function_call, web_search_call, apply_patch_call, …) |
| `response.function_call_arguments.delta` | Streaming delta of function call arguments |
| `response.function_call_arguments.done` | Full arguments for a function call item |
| `response.output_item.done` | An output item is complete |
| `response.web_search_call.in_progress` | Web search call initiated |
| `response.web_search_call.searching` | Web search actively querying |
| `response.web_search_call.completed` | Web search call finished |
| `response.content_part.added` | A content part started in a message item |
| `response.content_part.done` | A content part completed |
| `response.output_text.delta` | Text streaming delta inside a message |
| `response.output_text.done` | Full text for a content part |
| `response.completed` | Response complete, final output and usage |
| `[DONE]` | Stream sentinel |

QuantZhai uses `response.web_search_call.*` directly (matching the official schema). For `apply_patch_call` items, QuantZhai uses `response.output_item.added/done` with `"type": "apply_patch_call"` — this is a Codex-specific extension not in the base Responses API.

---

## 3. QuantZhai Source Inventory

### 3.1 Key Functions

| Function | File | What it emits to Codex |
|---|---|---|
| `public_tool_item_started_event(item, output_index, seq)` | `qz_streaming.py:76` | `response.output_item.added` (status: `in_progress`) |
| `public_tool_item_done_event(item, output_index, seq)` | `qz_streaming.py:90` | `response.output_item.done` (status: `completed`) |
| `public_tool_lifecycle_event(prefix, stages, stage, …)` | `qz_streaming.py:104` | `{prefix}.{stage}` (e.g. `response.web_search_call.searching`) |
| `web_search_call_lifecycle_event(stage, …)` | `qz_streaming.py:124` | `response.web_search_call.{stage}` |
| `public_tool_item_events(item, …)` | `qz_streaming.py:135` | `added` + `done` pair (calls started + done helpers) |
| `_emit_proxy_local_started(call, …)` | `qz_responses_stream.py:895` | `output_item.added` + all start-stage lifecycle events |
| `_emit_proxy_local_completed(call, item, …)` | `qz_responses_stream.py:909` | All done-stage lifecycle events + `output_item.done` |
| `_emit_public_tool_item(item, …)` | `qz_responses_stream.py:890` | `output_item.added` + `output_item.done` only |

### 3.2 ToolLifecycleSpec Per Tool

| Tool | Execution | `public_item_type` | `lifecycle_event_prefix` | Start stages | Done stages |
|---|---|---|---|---|---|
| `web_search` | `proxy_local` | `web_search_call` | `response.web_search_call` | `in_progress`, `searching` | `completed` |
| `apply_patch` (native mode) | `protocol_adapter` | `apply_patch_call` | *(none)* | *(none)* | *(none)* |
| `apply_patch` (custom mode) | `protocol_adapter` | `custom_tool_call` | *(none)* | *(none)* | *(none)* |
| `exec_command`, `write_stdin`, `shell_command`, `computer` | *(native passthrough)* | `function_call` | *(none)* | *(none)* | *(none)* |
| `qz_probe` (test fixture only) | `proxy_local` | `web_search_call` | `response.qz_probe_call` | `in_progress`, `working` | `completed` |

Sources: `proxy/qz_tool_web.py`, `proxy/qz_tool_apply_patch.py:568–609`, `proxy/qz_tools.py:9–15`, `tests/test_qz_proxy_tools.py:42–50`.

### 3.3 Dispatch Call Sites in the Streaming Runtime

From `proxy/qz_responses_stream.py`:

- **error kind** (lines ~1930–1935): Emits nothing to Codex. Telemetry only (`tool_call_error`). Injects `function_call_output` into next hop's `input` for the model.
- **proxy_local kind** (lines ~1940–2010): Calls `_emit_proxy_local_started`, executes tool, calls `_emit_proxy_local_completed`.
- **public kind** (lines ~2015–2036): Calls `_emit_public_tool_item`, then immediately finalises the response (`_emit_completed`). No continuation hop.
- **signal kind** (lines ~1919): Suppressed entirely (`suppressed="repeated_read_signal"`). Zero Codex events.

### 3.4 Function-Call Argument Suppression

All function-call argument events from upstream are suppressed from Codex. The suppression path is in two phases:

1. **While arguments are streaming** (`is_function_call_stream_event` returns true): events absorbed by `StreamToolCallState.observe()` without forwarding. Timing emitted with `suppressed="function_call"` (line 2044).
2. **After arguments complete** (the `output_item.done` for function_call): suppressed at line 2044 with `hs.event_lines = []`.

The reason (comment at line 1779): Codex treats `response.output_item.added` for a `function_call` as immediately runnable, which can trigger empty-argument command execution before `response.function_call_arguments.done`. QuantZhai absorbs all function-call upstream events and replaces them with the appropriate tool-typed events (web_search_call, apply_patch_call, function_call_output, etc.) after the call is complete and verified.

---

## 4. Codex-Visible Event Table

Per execution path, these are the SSE events Codex actually receives:

### 4.1 web_search (proxy_local)

```
response.output_item.added          {"type":"web_search_call", "status":"in_progress", …}
response.web_search_call.in_progress {"item_id": …, "output_index": …}
response.web_search_call.searching   {"item_id": …, "output_index": …}
    [tool executes — no events during execution]
response.web_search_call.completed   {"item_id": …, "output_index": …}
response.output_item.done           {"type":"web_search_call", "status":"completed", …}
    [proxy buffers result → sends to upstream for next hop]
    [next hop produces message items → forwarded as normal]
response.output_item.added          {"type":"message", …}
response.content_part.added         …
response.output_text.delta          …
response.output_text.done           …
response.content_part.done          …
response.output_item.done           {"type":"message", …}
response.completed
data: [DONE]
```

**Not sent:** `response.function_call_arguments.delta/done`, `response.output_item.added/done` for `function_call` type, any operator telemetry.

Sources: `_emit_proxy_local_started` (line 895), `_emit_proxy_local_completed` (line 909), `tests/test_qz_responses_stream.py::test_web_search_call_is_public_and_upstream_resumes_with_hidden_output`.

### 4.2 apply_patch (protocol_adapter, native mode)

```
response.output_item.added   {"type":"apply_patch_call", "status":"in_progress", "call_id": …}
response.output_item.done    {"type":"apply_patch_call", "status":"completed", "call_id": …, "operation": …}
response.completed
data: [DONE]
```

No `response.apply_patch_call.*` lifecycle stages exist — only the item pair. The apply_patch adapter has no `lifecycle_event_prefix`.

Sources: `_emit_public_tool_item` (line 890), `qz_tool_apply_patch.py:568–609`, `tests/test_qz_responses_stream.py::test_apply_patch_call_is_rewritten_as_public_tool_item`.

### 4.3 apply_patch (protocol_adapter, custom mode)

```
response.output_item.added   {"type":"custom_tool_call", "status":"in_progress", "name":"apply_patch", …}
response.output_item.done    {"type":"custom_tool_call", "status":"completed", "name":"apply_patch", …}
response.completed
data: [DONE]
```

Same two events; item type changes to `custom_tool_call` in custom apply_patch mode.

### 4.4 Codex-native tools (exec_command, write_stdin, shell_command, computer)

These pass through as `public` kind. `_emit_public_tool_item` emits:

```
response.output_item.added   {"type":"function_call", "status":"in_progress", "name":"exec_command", …}
response.output_item.done    {"type":"function_call", "status":"completed", "name":"exec_command", …}
response.completed
data: [DONE]
```

No sub-lifecycle stages. Codex receives the item pair and handles execution itself (the call exits the proxy cleanly before Codex executes).

Sources: `CODEX_NATIVE_TOOL_NAMES` (`proxy/qz_tools.py:9–15`), `_emit_public_tool_item`.

### 4.5 Malformed tool call (coercion fails)

```
[no lifecycle events at all]
    [error injected into next hop input as function_call_output — model-visible, not Codex SSE]
    [next hop produces message items → forwarded as normal]
response.output_item.added   {"type":"message", …}
…
response.completed
data: [DONE]
```

Codex sees no lifecycle events for the failed call. The model in the next hop receives the error as a `function_call_output` item in `input`, and typically produces a message that Codex then sees.

Sources: `tests/test_qz_responses_stream.py::StreamingToolErrorFixtureTests`, `tool-schema-coercion-audit.md §G Gap 5 (CLOSED)`.

### 4.6 Dropped tool call (in qz_dropped_tool_names)

Same as §4.5: zero Codex lifecycle events. Error as `function_call_output` in next hop's `input`.

### 4.7 Unknown tool call (not in any registry)

Same as §4.5: zero Codex lifecycle events. `tool_call_error` operator telemetry. Error as `function_call_output` in next hop's `input`.

### 4.8 Signal (repeated-read, sandbox advisory)

Zero Codex lifecycle events. The decision is consumed by the signal handler; no SSE is forwarded. Operator telemetry only (`repeated_read_signal`, `tool_sandbox_advisory_injected`, etc.). Advisory text is injected into the model's next-hop `input`, making it model-visible but not a Codex SSE event.

### 4.9 Standard response events (always forwarded, any path)

These arrive from upstream and are forwarded verbatim (raw mode) or transformed (summary/hidden mode):

```
response.created
response.in_progress
response.output_item.added          (message, reasoning)
response.reasoning_text.delta/done  (raw mode)
response.reasoning_summary_*.added/done  (summary mode)
response.content_part.added
response.output_text.delta
response.output_text.done
response.content_part.done
response.output_item.done           (message, reasoning)
response.completed                  (usage normalised)
data: [DONE]
```

Duplicate `response.created`/`response.in_progress` from continuation hops are suppressed (`_should_suppress_duplicate_response_start`). Proxy-local terminal events from upstream (before QuantZhai injects its own `response.completed`) are suppressed (`_should_suppress_proxy_local_terminal`).

---

## 5. Suppression Inventory

Events that are **never** forwarded to Codex:

| Suppressed event / category | Suppression mechanism | Reason |
|---|---|---|
| `response.output_item.added` for `function_call` | `is_function_call_stream_event` guard (line 1775, 2038) | Prevents premature empty-argument execution by Codex |
| `response.function_call_arguments.delta` | Same guard | Same |
| `response.function_call_arguments.done` | Same guard | Same |
| `response.output_item.done` for `function_call` | Same guard | Same |
| Operator `TelemetryBus` events | Never written to chunk writer | Internal bus only; Codex has no access |
| `tool_call_started`, `tool_call_completed`, `tool_call_error` | TelemetryBus | Operator observability only |
| `coercion_succeeded`, `coercion_failed` | TelemetryBus | Operator observability only |
| `tool_sandbox_denied`, `tool_sandbox_advisory_injected` | TelemetryBus | Operator observability only |
| `repeated_read_signal` | TelemetryBus + stream suppressed (`suppressed="repeated_read_signal"`) | Operator observability only |
| Duplicate `response.created`/`response.in_progress` from hop N>1 | `_should_suppress_duplicate_response_start` | Dedup across continuation hops |
| Upstream `response.completed`/`[DONE]` from proxy-local hop | `_should_suppress_proxy_local_terminal` | QuantZhai synthesises its own terminal after tool injection |
| `function_call_output` items in next-hop `input` | These are request items, not SSE events — only the model sees them | Not a streaming channel artefact |

---

## 6. Event Shape Reference

### response.output_item.added / done (web_search_call)

```json
{
  "type": "response.output_item.added",
  "output_index": 0,
  "sequence_number": 3,
  "item": {
    "id": "wsc_abc123",
    "type": "web_search_call",
    "status": "in_progress",
    "action": {"type": "search", "queries": ["…"]}
  }
}
```

Done: same but `"status": "completed"` and optionally `"call_id"` added.

### response.web_search_call.{stage}

```json
{
  "type": "response.web_search_call.searching",
  "output_index": 0,
  "item_id": "wsc_abc123",
  "sequence_number": 4
}
```

Stage ∈ `{"in_progress", "searching", "completed"}`.

### response.output_item.added / done (apply_patch_call, native mode)

```json
{
  "type": "response.output_item.added",
  "output_index": 0,
  "sequence_number": 3,
  "item": {
    "id": "apc_abc123",
    "type": "apply_patch_call",
    "status": "in_progress",
    "call_id": "call_abc",
    "operation": {}
  }
}
```

Done: same but `"status": "completed"` and `"operation"` filled.

### response.output_item.added / done (function_call, native tool passthrough)

```json
{
  "type": "response.output_item.added",
  "output_index": 0,
  "sequence_number": 3,
  "item": {
    "id": "fc_abc123",
    "type": "function_call",
    "status": "in_progress",
    "call_id": "call_abc",
    "name": "exec_command",
    "arguments": "{\"cmd\":\"pwd\"}"
  }
}
```

---

## 7. Current Visibility Gaps

| Gap | Description | Impact | Severity |
|---|---|---|---|
| **G1. apply_patch has no sub-lifecycle events** | apply_patch emits only `added`/`done`. There is no `response.apply_patch_call.in_progress` or `response.apply_patch_call.completed`. | Codex cannot show a progress indicator during patch application. The operation appears instantaneous. | Low — apply_patch is fast and synchronous; no known Codex UX regression. |
| **G2. No lifecycle events for error/dropped/unknown paths** | When a tool call fails coercion, is dropped, or is unknown, Codex receives no `output_item.added` for that call. The call never appears in Codex's output stream at all. | Codex (and the user) cannot see which tool failed — only the eventual model recovery message is visible. | Medium — the model explains in text, but there is no structured audit trail in the Codex session. |
| **G3. Codex-native tools: no sub-lifecycle stages** | exec_command, write_stdin, shell_command, computer emit only `added`/`done`. No `in_progress`/`searching`/`completed` sub-events. | Codex handles these natively and does not need sub-events from the proxy. | None currently — Codex drives these itself. |
| **G4. Signal decisions produce zero Codex events** | repeated-read and sandbox signals are invisible to Codex. The advisory text eventually appears in a model reply, not as a structured event. | Signals act invisibly from Codex's perspective. For the repeated-read advisory this is correct (it's model guidance). For future richer signal types this may be revisited. | Low — current signals are model-guidance only. |
| **G5. No structured error item in Codex stream** | Coercion/dropped errors are injected as `function_call_output` into the model's next-hop `input`, not as a Codex SSE event. Codex has no way to observe a tool-error event structurally. | Only visible through the model's natural-language recovery message. | Low for now — Codex does not have a tool-error event type in the Responses schema. |

---

## 8. Risks

### R1. apply_patch sub-lifecycle (if ever added)

apply_patch has `lifecycle_event_prefix=None` and no allowed stages. Adding sub-events would require updating `ToolLifecycleSpec`, the dispatch path, and the Codex client behaviour contract. The `public_tool_lifecycle_event` function raises `ValueError` for unsupported stages so any incomplete plumbing would be caught. Risk: low if done through the existing spec system; high if patched ad hoc.

### R2. function_call passthrough for native tools

Codex-native tools (`exec_command` etc.) receive the raw `function_call` item type from upstream. If `_emit_public_tool_item` ever rewrites the item type (e.g. to `custom_tool_call`), Codex may no longer recognise the item as executable. The current path deliberately leaves the type alone (`public_item_from_function_call` returns the call unchanged for native names). Tests: `test_public_tool_item_from_function_call_leaves_public_function_calls`.

### R3. Suppression boundary correctness

All function-call argument events are suppressed by `is_function_call_stream_event`. If a new event type is introduced that wraps a function_call item and fails this check, it could leak a partially-assembled call to Codex. The guard currently checks `event_type` prefix and `payload["item"]["type"]`. Adding a new event shape would require updating this guard. Test coverage: `test_function_call_event_detection` in `test_qz_streaming.py`.

### R4. Sequence number continuity

Codex (and the official Responses API) may use `sequence_number` for replay ordering. QuantZhai maintains a single `sequence` counter across hops and lifecycle events. If a future feature inserts events out-of-order or resets the counter, Codex could see duplicate or non-monotonic sequence numbers. Current: the counter is thread-local to a single `run()` call and incremented by every emit helper. No known issue.

### R5. Proxy-local terminal suppression

`_should_suppress_proxy_local_terminal` suppresses `response.completed`/`[DONE]` from the upstream for proxy-local tool hops, because QuantZhai synthesises its own terminal after injecting the tool result. If a future tool path emits a legitimate terminal before QuantZhai expects one (e.g. the tool fails mid-stream), this suppression could hide a real completion. Current: guarded by `public_item_seen` flag. Test: `test_proxy_local_streaming_lifecycle_is_not_web_search_specific`.

---

## 9. Recommended Implementation Slices

These are prioritised, non-breaking, and consistent with the existing `ToolLifecycleSpec` system. None are required for current live stability.

### Slice L1 — apply_patch sub-lifecycle events (optional, low risk)

Add `lifecycle_event_prefix="response.apply_patch_call"`, `lifecycle_start_stages=("in_progress",)`, `lifecycle_done_stages=("completed",)` to `APPLY_PATCH_TOOL_ADAPTER`. This would give Codex a structured `response.apply_patch_call.in_progress` / `response.apply_patch_call.completed` pair. No change to dispatch logic needed — `_emit_proxy_local_started/completed` already handle any prefix via the registry. Requires apply_patch execution mode to change from `protocol_adapter` to `proxy_local`, which is a broader change. **Do not implement without a full apply_patch execution audit.**

### Slice L2 — Structured error item in Codex stream (exploratory, medium risk)

When a tool call fails (coercion/dropped/unknown), synthesise a `response.output_item.added` + `response.output_item.done` pair with a `tool_call_error` item type before the next-hop message. This would make the failure visible as a structured item rather than only through model text. Requires: defining a `tool_call_error` item type, ensuring Codex renders it gracefully, and verifying the Responses API spec allows unknown item types in the output. **Do not implement until the Responses API extension point is confirmed.**

### Slice L3 — Test: apply_patch emits no sub-lifecycle events (low risk, documentation value)

Add a unit test that asserts `response.apply_patch_call.in_progress` is NOT in the Codex stream when apply_patch runs. This locks the current "no sub-events for apply_patch" contract explicitly, so any future accidental addition is caught. The `StreamingToolErrorFixtureTests` infrastructure can be reused. **Safe to implement now.**

### Slice L4 — Live smoke validation of web_search lifecycle events ✓ Done

`scripts/qz-web-search-lifecycle-smoke` asserts that `response.web_search_call.in_progress`, `response.web_search_call.searching`, and `response.web_search_call.completed` all appear in the forwarded SSE stream when a real search runs. Sections covered:

- **A** — searchengines `/guidance` direct (schema, provider_id, furry_fse profile, fse engine flags)
- **B** — QuantZhai `/qz/web-search/capabilities` provider_guidance bridge (available, schema, provider_id, profiles_present)
- **C** — Streaming `/v1/responses` lifecycle events (`output_item.added`, `web_search_call.in_progress`, `web_search_call.searching`, `web_search_call.completed`, `output_item.done`, `response.completed`)
- **D** — FSE direct search (agent_api.fse_search metadata, count_mismatch expected-True handling, result domains)
- **E** — Operator telemetry (tool_call_started, tool_call_completed, no tool_call_error)

Section C skips gracefully if the model doesn't call web_search (prompt-dependent) or if the model is not loaded. **HTTP 503 from the proxy is classified as a startup skip (`not_ready`) when `/qz/model/status` confirms the backend is not ready, so running the smoke immediately after `qz-up` does not produce a false failure. If the backend claims ready but returns 503, the smoke fails loudly (`error_503_despite_ready`) as a real admission bug.** Use `--wait-backend SECONDS` to poll until the backend is ready before Section C. All other sections run against static endpoints and are always checkable when the proxy and searchengines stack are up.

**Search backend env reporting:** The smoke banner shows `searchengines=` (resolved Agent API facade base) and a `legacy_searxng=... (ignored for Agent API smoke)` line only when `SEARXNG_BASE_URL` differs from the resolved base. The stocktake labels `SEARXNG_BASE_URL` as `legacy/lower-priority; not used while QZ_SEARCHENGINES_BASE_URL is set` when it points to a different address (e.g., raw SearXNG). `QZ_SEARCHENGINES_BASE_URL` and `SEARXNG_AGENT_API_BASE` are the canonical Agent API facade vars.

---

## Appendix A: Decision Kind → Codex Event Summary

| Decision kind | Source | Codex lifecycle events | Operator telemetry | Model input effect |
|---|---|---|---|---|
| `proxy_local` | web_search | `output_item.added`, `web_search_call.in_progress`, `web_search_call.searching`, `web_search_call.completed`, `output_item.done` | `tool_call_started`, `tool_call_completed` | Tool result as `function_call_output` in next hop `input` |
| `public` (apply_patch native) | apply_patch | `output_item.added` (apply_patch_call), `output_item.done` (apply_patch_call) | *(none specific)* | None — Codex handles the file operation |
| `public` (native tool) | exec_command etc. | `output_item.added` (function_call), `output_item.done` (function_call) | *(none specific)* | None — Codex handles execution |
| `error` | any malformed/dropped/unknown | *(none)* | `tool_call_error`, `coercion_failed` | Error as `function_call_output` in next hop `input` |
| `signal` | repeated-read, sandbox advisory | *(none)* | `repeated_read_signal`, `tool_sandbox_advisory_injected` | Advisory text as `function_call_output` in next hop `input` |

## Appendix B: Test Coverage Map

| Path | Tests |
|---|---|
| web_search lifecycle events in Codex stream | `test_web_search_call_is_public_and_upstream_resumes_with_hidden_output` |
| proxy_local lifecycle not web_search-specific | `test_proxy_local_streaming_lifecycle_is_not_web_search_specific` |
| apply_patch produces apply_patch_call item | `test_apply_patch_call_is_rewritten_as_public_tool_item` |
| public_tool_item_events emits added+done | `test_public_tool_item_events_emit_added_and_done` |
| web_search_call_lifecycle_event shape | `test_web_search_call_lifecycle_event` |
| Function-call event detection | `test_function_call_event_detection` |
| Error/coercion paths: no lifecycle events | `StreamingToolErrorFixtureTests` (17 tests) |
| Dropped write_stdin: no native passthrough | `test_dropped_write_stdin_no_native_passthrough` |
| apply_patch coercion failure path | `test_malformed_apply_patch_coercion_failed_streaming` |
| web_search coercion failure path | `test_malformed_web_search_no_lifecycle_events` |
