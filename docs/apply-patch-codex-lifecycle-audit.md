# apply_patch Codex Lifecycle Audit

Date: 2026-05-25
Status: Source-grounded. No runtime behaviour changes.

This audit focuses exclusively on `apply_patch` visibility and feedback. It
distinguishes:

- **Codex-visible SSE lifecycle** — which `response.*` events Codex actually receives.
- **Model-visible feedback/advice** — what the model sees on the next hop when coercion fails.
- **Operator telemetry** — what appears in the TelemetryBus for operator debugging.
- **Backend adapter/coercion** — how QuantZhai converts between Codex shapes and llama.cpp.

`apply_patch` must not be treated as `web_search`. The two tools have fundamentally
different execution models:

| | `web_search` | `apply_patch` |
|---|---|---|
| Execution mode | `proxy_local` — QuantZhai fetches results | `protocol_adapter` — Codex applies the patch locally |
| Lifecycle stages | `in_progress → searching → completed` | none (only `added/done`) |
| Official event family | `response.web_search_call.*` (official) | none defined — unofficial Codex extension |
| Continuation hop | yes — tool result returned in next hop | no — item forwarded; Codex closes the loop |

**Constraints on this audit pass:** No runtime behaviour changes. No invented
`response.apply_patch_call.*` sub-lifecycle events. No raw patch bodies in
telemetry. See §10 for full risk inventory.

---

## 1. Purpose and Scope

This document answers:

- Which official Responses streaming events are relevant to `apply_patch`?
- What Codex-visible SSE events does QuantZhai emit for `apply_patch`?
- What model-visible feedback does a malformed `apply_patch` produce?
- What operator telemetry is emitted?
- What is the current coercion path inventory?
- Where are the gaps and risks?

**Not covered here:** `web_search`, BrainCase, watchdog, schema normalisation
(see `tool-schema-coercion-audit.md`), or the broader tool-policy audit (see
`tool-policy-audit.md`). The non-streaming (`qz_sse.py`) path for `apply_patch`
is covered in §5.4.

**Source files consulted:**

```
proxy/qz_tool_apply_patch.py     — adapter, coercion, shape conversion
proxy/qz_proxy_tools.py          — decision router (completed_call_decision)
proxy/qz_responses_stream.py     — streaming runtime (public/error dispatch)
proxy/qz_sse.py                  — non-streaming event synthesis
proxy/qz_tools.py                — ToolCoercionResult, ToolLifecycleSpec
proxy/qz_tool_lifecycle.py       — CompletedToolCallDecision, StreamToolCallState
tests/test_apply_patch_adapter.py
tests/test_qz_responses_stream.py
tests/test_qz_proxy_tools.py
docs/codex-visible-tool-lifecycle-audit.md
docs/tool-schema-coercion-audit.md
docs/responses-stream-tool-state-contract.md
```

---

## 2. Official Responses API Event Surface

### 2.1 All tool-related official event families

| Event family | Purpose | Relevant to apply_patch? |
|---|---|---|
| `response.output_item.added` | New output item started | ✅ Yes — wraps `apply_patch_call` / `custom_tool_call` |
| `response.output_item.done` | Output item complete | ✅ Yes — closes the item pair |
| `response.function_call_arguments.delta` | Streaming argument delta | ⚠️ Suppressed — never forwarded by QuantZhai |
| `response.function_call_arguments.done` | Full arguments ready | ⚠️ Suppressed — never forwarded by QuantZhai |
| `response.custom_tool_call_input.delta` | Streaming custom tool input delta | ❌ Not emitted — custom mode uses `output_item.*` |
| `response.custom_tool_call_input.done` | Full custom tool input ready | ❌ Not emitted — custom mode uses `output_item.*` |
| `response.web_search_call.in_progress` | Web search initiated | ❌ Not relevant — different tool family |
| `response.web_search_call.searching` | Web search querying | ❌ Not relevant |
| `response.web_search_call.completed` | Web search done | ❌ Not relevant |
| `response.file_search_call.*` | File search lifecycle | ❌ Not relevant |
| `response.code_interpreter_call.*` | Code interpreter lifecycle | ❌ Not relevant |
| `response.mcp_call.*` | MCP tool lifecycle | ❌ Not relevant |
| `response.completed` | Full response done | ✅ Yes — terminal event on every path |
| `response.failed` / `response.incomplete` / `error` | Terminal failure events | ✅ Yes — relevant if upstream errors |

### 2.2 apply_patch-specific events

The official OpenAI Responses streaming spec does **not** define:

- `response.apply_patch_call.in_progress`
- `response.apply_patch_call.searching`
- `response.apply_patch_call.completed`
- `response.apply_patch_call.*` (any stage)

These are **not** official Responses API event names. QuantZhai must not invent
or emit them without documented Codex client proof that they are rendered (not
ignored or broken). See `docs/codex-visible-tool-lifecycle-audit.md §8 Slice L1`
for the pre-conditions before any such change.

The item type `apply_patch_call` (in `output_item.added/done` payloads) **is** a
Codex-specific extension that Codex v0.130 understands. It is not an official
Responses API item type. QuantZhai's use of it is correct for the Codex client
but non-portable to other Responses API clients.

Similarly, `custom_tool_call` (for the custom apply_patch path) is a Codex
extension used when the tool is declared as `{"type": "custom", "name": "apply_patch"}`.

---

## 3. QuantZhai apply_patch Adapter Inventory

### 3.1 ToolLifecycleSpec

From `proxy/qz_tool_apply_patch.py:568–609`:

```python
ApplyPatchToolAdapter.lifecycle = ToolLifecycleSpec(
    name="apply_patch",
    execution="protocol_adapter",   # Not proxy_local — Codex applies the patch
    public_item_type="apply_patch_call",
    telemetry_name="apply_patch",
    # lifecycle_event_prefix: "" (empty — no sub-lifecycle stages)
    # lifecycle_start_stages: () (empty)
    # lifecycle_done_stages: () (empty)
)
```

`execution="protocol_adapter"` means:

- QuantZhai does **not** execute the patch. It translates the model's output
  shape for Codex and passes the patch item through.
- No `proxy_local` executor runs. No `tool_call_started`/`tool_call_completed`
  telemetry is emitted (those are `proxy_local` only).
- No sub-lifecycle events are emitted (those require `proxy_local` mode with
  `lifecycle_event_prefix` set).
- The streaming runtime follows the `public` decision path, not `proxy_local`.

### 3.2 Tool Declaration Acceptance

`accepts_tool()` returns `True` for:

- `{"type": "apply_patch"}` — native Codex declaration
- `{"type": "custom", "name": "apply_patch"}` — custom tool declaration

Both map to the same upstream `function` tool via `to_upstream_tool()`:

```json
{"type": "function", "name": "apply_patch", "description": "...", "parameters": {...}}
```

### 3.3 Input-to-upstream Conversions (Codex → llama.cpp)

| Codex input item type | Upstream (llama.cpp) type | Notes |
|---|---|---|
| `apply_patch_call` | `function_call` | Operation serialised into `arguments` JSON |
| `apply_patch_call_output` | `function_call_output` | Status and output wrapped in JSON |
| `custom_tool_call` with `name="apply_patch"` | `function_call` | `input` field serialised as `{"patch": "..."}` |
| `custom_tool_call_output` | `function_call_output` | `output` field passed through |

Source: `input_to_upstream()` at lines 600–611.

### 3.4 Output-to-Codex Conversions (llama.cpp → Codex)

| Upstream item | Output style | Codex item type | Notes |
|---|---|---|---|
| `function_call apply_patch` | `native` | `apply_patch_call` | Operation extracted and normalised |
| `function_call apply_patch` | `custom` | `custom_tool_call` | Patch text built via `_apply_patch_operation_to_patch_text` |

Output style is determined by the tool policy inferred at request time:

- `apply_patch_declared=True, apply_patch_client_tool_type="apply_patch"` → `output_style="native"`
- `apply_patch_declared=True, apply_patch_client_tool_type="custom"` → `output_style="custom"`
- `apply_patch_declared=False` → falls back to `"custom"` (default)

Source: `_apply_patch_output_style()`, `_infer_apply_patch_tool_policy()`, lines 522–565.

---

## 4. Coercion / Advice Path Inventory

### 4.1 Primary coercion sequence (`_parse_apply_patch_arguments`)

1. **Valid operation object in `arguments["operation"]`** → `_coerce_apply_patch_operation()` succeeds → corrected arguments returned.
2. **Sibling patch promotion** — `operation` present but lacks `diff`; sibling `patch` string promoted into `operation["diff"]` → retry coercion.
3. **Top-level operation fields** — direct `type`/`path`/`diff` in args (no nested `operation`) → `_coerce_apply_patch_operation()` tried on the top level.
4. **Top-level `patch` with explicit `path`** — legacy `{"patch": "...", "path": "..."}` shape.
5. **`*** Begin Patch` envelope extraction** — `_extract_op_and_path_from_patch_envelope()` pulls `type+path` from envelope header lines when no path is provided.

If all coercion attempts fail, `_parse_apply_patch_arguments()` returns `None`.

### 4.2 Operation normalisation steps

| Step | Function | Effect |
|---|---|---|
| `rename_file` → `move_file` | `_coerce_apply_patch_operation` | Alias normalised on all paths |
| Unified diff headers stripped | `_strip_unified_diff_headers` | `diff --git`, `index`, `---`, `+++` lines removed for `update_file`/`move_file` |
| Hunk header normalised | `_normalize_unified_diff_hunk_header` | `@@ -N,M +N,M @@` → `@@` |
| Destination key aliases | `APPLY_PATCH_DESTINATION_KEYS` | `new_path`, `to`, `move_to`, `target_path` all accepted |

### 4.3 `coerce()` method (called from `completed_call_decision`)

```python
def coerce(self, call: dict) -> ToolCoercionResult:
    operation = _parse_apply_patch_arguments(call.get("arguments") or "{}")
    if operation:
        return ToolCoercionResult(corrected_arguments=json.dumps({"operation": operation}))
    reason = _describe_args_failure(call.get("arguments") or "{}")
    return ToolCoercionResult(error_message=f"apply_patch: {reason}")
```

Coercion is **always attempted** for `apply_patch` (unlike native tools where it
is optional). If it succeeds, the corrected call is forwarded with `coercion_applied=True`.
If it fails, an error is injected with `coercion_applied=True, coercion_error=...`.

### 4.4 `_describe_args_failure` — specific error messages

| Failure | Error message fragment |
|---|---|
| JSON parse error | `"arguments are not valid JSON"` |
| Non-object JSON | `"arguments are not a JSON object"` |
| Unknown operation type | `"unknown operation type {!r}; expected one of: ..."` |
| Missing path | `"missing or empty 'path' on operation type {!r}"` |
| Missing diff (create/update) | `"missing 'diff' on operation type {!r}; include file content..."` |
| Missing destination (move/rename) | `"missing destination on operation type {!r}; expected one of: ..."` |
| All other | `"could not coerce arguments to a valid apply_patch operation"` |

The error message is specific enough for the model to correct itself. No raw
argument content is included in the error message.

### 4.5 Partial envelope fallback (custom mode only)

When full coercion fails but `type` and `path` (and `destination` for `move_file`)
can be extracted, `_build_partial_custom_envelope()` emits a minimal envelope:

```
*** Begin Patch
*** Update File: path/to/file.py
*** End Patch
```

This gives Codex's V4A verifier a specific target to reject, rather than
producing a blank error. It is intentionally incomplete — Codex's error will
be more useful than a proxy-side generic failure.

### 4.6 `_invalid_apply_patch_call_message` — fallback assistant message

When **no** coercion path succeeds and **no** partial envelope can be built,
the adapter returns an assistant `message` item:

```json
{
  "type": "message",
  "role": "assistant",
  "content": [{"type": "output_text", "text": "apply_patch call rejected by QuantZhai proxy: ..."}]
}
```

This path is triggered by `_function_call_to_apply_patch_call` /
`_function_call_to_custom_apply_patch_call` when the call is beyond salvage.

**Distinction:** `_invalid_apply_patch_call_message` returns a Codex-facing
message item directly from the shape converter, whereas `coerce()` returning
`error_message` causes the streaming runtime to inject a `function_call_output`
error into the **model's** next-hop `input` via `synthesize_tool_error_result`.
These are two distinct error injection paths that apply at different levels.

---

## 5. Codex-Visible Lifecycle Inventory

### 5.1 Streaming path — valid apply_patch (native mode)

```
[upstream emits function_call — SUPPRESSED by is_function_call_stream_event]
[upstream emits function_call_arguments.delta — SUPPRESSED]
[upstream emits function_call_arguments.done — SUPPRESSED]
[upstream emits output_item.done for function_call — SUPPRESSED]

→ QuantZhai assembles complete function_call, runs coerce(), gets corrected call
→ completed_call_decision returns kind="public"
→ _emit_public_tool_item called with apply_patch_call item

response.output_item.added   {"type": "apply_patch_call", "status": "in_progress", "call_id": "...", "operation": {}}
response.output_item.done    {"type": "apply_patch_call", "status": "completed", "call_id": "...", "operation": {...}}
response.completed
data: [DONE]
```

**Not emitted:**
- `response.apply_patch_call.in_progress`
- `response.apply_patch_call.searching`
- `response.apply_patch_call.completed`
- `response.function_call_arguments.delta/done`
- `tool_call_started` / `tool_call_completed` (those are proxy-local only)

Sources: `qz_responses_stream.py:890 (_emit_public_tool_item)`, `qz_tool_apply_patch.py:568`.

### 5.2 Streaming path — valid apply_patch (custom mode)

Same pattern as §5.1, item type differs:

```
response.output_item.added   {"type": "custom_tool_call", "status": "in_progress", "name": "apply_patch", ...}
response.output_item.done    {"type": "custom_tool_call", "status": "completed", "name": "apply_patch", "input": "*** Begin Patch\n..."}
response.completed
data: [DONE]
```

### 5.3 Streaming path — malformed apply_patch (coercion fails)

```
[upstream function_call events — SUPPRESSED]
→ coerce() fails → error_message set
→ completed_call_decision returns kind="error"
→ synthesize_tool_error_result builds function_call_output for model
→ error injected into next hop's input

[no response.output_item.* events for the failed call]
[no response.apply_patch_call.* events]

→ next hop produces message (model's error response)

response.output_item.added   {"type": "message", ...}
response.content_part.added  ...
response.output_text.delta   ...
response.output_text.done    ...
response.content_part.done   ...
response.output_item.done    {"type": "message", ...}
response.completed
data: [DONE]
```

Codex sees **no lifecycle events for the failed call**. The failure is invisible
to Codex at the SSE level; only the model's natural-language recovery message
arrives as structured output.

### 5.4 Non-streaming path (qz_sse.py)

The non-streaming path (`make_response_stream_events`) synthesises SSE from a
complete response object. For `apply_patch_call` items (lines 448–468):

```python
yield ev("response.output_item.added", {"output_index": ..., "item": {..., "status": "in_progress"}})
yield ev("response.output_item.done",  {"output_index": ..., "item": {..., "status": "completed"}})
```

For `custom_tool_call` items (lines 471–488): same pattern, `custom_tool_call`
type.

**Not synthesised:** No `response.apply_patch_call.*` stages in the non-streaming
path either.

### 5.5 Lifecycle table by scenario

| Scenario | Codex SSE events | Model sees | Operator telemetry |
|---|---|---|---|
| Valid native update_file | `output_item.added/done` (apply_patch_call) | operation on next hop | `coercion_succeeded` |
| Valid native create_file | `output_item.added/done` (apply_patch_call) | operation on next hop | `coercion_succeeded` |
| Valid native delete_file | `output_item.added/done` (apply_patch_call) | operation on next hop | `coercion_succeeded` |
| Valid native move_file | `output_item.added/done` (apply_patch_call) | operation on next hop | `coercion_succeeded` |
| Valid custom envelope | `output_item.added/done` (custom_tool_call) | input patch text on next hop | `coercion_succeeded` |
| Sibling patch promotion | `output_item.added/done` (patched) | operation on next hop | `coercion_succeeded` |
| rename_file alias | `output_item.added/done` (normalised to move_file) | operation on next hop | `coercion_succeeded` |
| Unified diff headers stripped | `output_item.added/done` (clean diff) | operation on next hop | `coercion_succeeded` |
| Invalid JSON args | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Non-object JSON | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Unknown operation type | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Missing path | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Missing diff (create/update) | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Missing destination (move) | none (error path) | `function_call_output` with error text | `coercion_failed`, `tool_call_error` |
| Partial native (type+path, no diff) | `output_item.added/done` (partial op) | partial operation on next hop | `coercion_succeeded` |
| Partial custom (type+path, no diff) | `output_item.added/done` (partial envelope) | partial envelope on next hop | `coercion_succeeded` |
| `*** Begin Patch` envelope | `output_item.added/done` (normalised) | operation on next hop | `coercion_succeeded` |
| Dropped (in dropped_tool_names) | none | `function_call_output` with "not available" | `tool_call_error` |
| Unknown tool (not registered) | none | `function_call_output` with "not recognised" | `tool_call_error` |

---

## 6. Model-Visible Feedback / Advice

### 6.1 On coercion success

The model receives its corrected `apply_patch_call` (or `custom_tool_call`) on
the **next hop's input**. The item is surfaced as a structured item in
`output[]`, not as an error. No text advice is injected.

### 6.2 On coercion failure

`synthesize_tool_error_result(call, coercion.error_message)` builds:

```json
{
  "type": "function_call_output",
  "call_id": "<original call_id>",
  "output": "{\"error\": \"apply_patch: missing 'diff' on operation type 'update_file'; ...\"}"
}
```

Contract:

- **`call_id` preserved** — the model can correlate the error to the call.
- **No raw argument content** — the error message describes the failure;
  it does not echo back the model's raw `arguments` string.
- **Specific reason** — each failure case has its own diagnostic text (§4.4).
- **No duplicate errors** — one `function_call_output` per failed call.

### 6.3 Boundaries

- The error text is capped at 200 characters in the `coercion_error` telemetry
  field (`error_summary`), but the full message is passed to `synthesize_tool_error_result`.
- No raw patch body is included in any model-visible or telemetry field.
- No raw malformed arguments are echoed in any path.

---

## 7. Operator Telemetry

### 7.1 Coercion succeeded (streaming path)

Emitted via `_coercion_event` logic at `qz_responses_stream.py:1899–1909`:

```json
{
  "type": "coercion_succeeded",
  "tool": "apply_patch",
  "upstream_name": "apply_patch",
  "call_id": "...",
  "correction_applied": true,
  "error_summary": "",
  "source": "tool_adapter"
}
```

### 7.2 Coercion failed (streaming path)

```json
{
  "type": "coercion_failed",
  "tool": "apply_patch",
  "upstream_name": "apply_patch",
  "call_id": "...",
  "correction_applied": false,
  "error_summary": "apply_patch: missing 'diff' on...",  // capped at 200 chars
  "source": "tool_adapter"
}
```

### 7.3 tool_call_error (streaming path, error kind)

Emitted when `decision.kind == "error"`:

```json
{
  "type": "tool_call_error",
  "tool": "apply_patch",
  "error": "<output field of the error function_call_output>"
}
```

### 7.4 tool_call_started / tool_call_completed

**Not emitted for apply_patch.** These are `proxy_local` telemetry events. Since
`apply_patch` uses `execution="protocol_adapter"`, it never triggers the
proxy-local executor path. No tool_call_started/completed will ever appear for
apply_patch.

### 7.5 What is safe to include in telemetry (forward-looking)

Safe patch metadata (not currently emitted but safe to add in future slices):

| Field | Why safe |
|---|---|
| `operation_type` | Enum value only — not content |
| `path_present: true/false` | Boolean — no path value exposed |
| `diff_present: true/false` | Boolean — no diff content exposed |
| `destination_present: true/false` | Boolean — no path value exposed |
| `coercion_strategy` | Which coercion path succeeded (e.g. "sibling_patch") |

Unsafe:

| Field | Why unsafe |
|---|---|
| Raw `arguments` string | May contain full patch body |
| `diff` value | Patch content — potentially large and sensitive |
| `path` value | File path — may leak workspace layout |
| `destination` value | Same |
| Any full `input`/`patch` text | Patch content |

---

## 8. Gap Matrix

| Scenario | Codex SSE expected | Model feedback expected | Operator telemetry expected | Current tests | Gap | Risk | Recommendation |
|---|---|---|---|---|---|---|---|
| Valid native update_file | `output_item.added/done` (apply_patch_call) | operation in output | `coercion_succeeded` | `test_model_function_call_becomes_native_apply_patch_call`, `test_golden_apply_patch_update_stream_rewrites_to_apply_patch_call` | None | Low | Lock with AP1 contract test |
| Valid native create_file | `output_item.added/done` | operation in output | `coercion_succeeded` | `test_native_update_patch_strips_unified_diff_file_headers` (partial) | No explicit create streaming test | Low | AP2 fixture |
| Valid native delete_file | `output_item.added/done` | operation in output | `coercion_succeeded` | indirect | No explicit streaming delete test | Low | AP2 fixture |
| Valid native move_file | `output_item.added/done` | operation in output | `coercion_succeeded` | `test_model_function_call_becomes_native_move_apply_patch_call` | No streaming runtime test | Low | AP2 fixture |
| Valid custom envelope | `output_item.added/done` (custom_tool_call) | patch text in input | `coercion_succeeded` | `test_golden_custom_apply_patch_stream_rewrites_to_custom_tool_call` | None | Low | Lock with AP1 |
| Sibling patch promotion | `output_item.added/done` | operation in output | `coercion_succeeded` | `test_qwen_create_file_sibling_patch_is_coerced` | No streaming test; adapter test only | Low | AP2 |
| rename_file alias | `output_item.added/done` (move_file) | operation | `coercion_succeeded` | `test_rename_operation_alias_becomes_custom_move_patch` | No native-mode rename streaming test | Low | AP2 |
| Unified diff headers stripped | `output_item.added/done` (clean diff) | operation | `coercion_succeeded` | `test_golden_apply_patch_update_stream_rewrites_to_apply_patch_call` | None | Low | — |
| Invalid JSON args | none | specific error text | `coercion_failed`, `tool_call_error` | `test_coerce_bad_json_returns_error_message` | No streaming lifecycle assertion | Low | AP2 |
| Non-object JSON | none | specific error | `coercion_failed`, `tool_call_error` | indirect | No explicit streaming test | Low | AP2 |
| Unknown operation type | none | specific error | `coercion_failed`, `tool_call_error` | `test_coerce_bare_operation_returns_error_message` | No streaming lifecycle assertion | Low | AP2 |
| Missing path | none | specific error | `coercion_failed`, `tool_call_error` | `test_invalid_patch_function_call_with_no_path_falls_back_to_message` | No streaming lifecycle assertion | Low | AP2 |
| Missing diff (create) | none | specific error | `coercion_failed` | `test_coerce_valid_operation_returns_corrected_arguments` (success side only) | No missing-diff coercion test | Low | AP2 |
| Missing destination (move) | none | specific error | `coercion_failed` | `test_coerce_missing_destination_returns_error_message` | No streaming lifecycle test | Low | AP2 |
| Partial native (type+path, no diff) | `output_item.added/done` (partial op) | partial op | `coercion_succeeded` | `test_qwen_bare_create_file_native_mode_emits_apply_patch_call` | None — covered | None | — |
| `*** Begin Patch` envelope | `output_item.added/done` | operation | `coercion_succeeded` | `test_qwen_legacy_patch_envelope_with_no_top_level_path_is_coerced` | No streaming lifecycle test | Low | AP2 |
| apply_patch emits NO web_search_call events | N/A | N/A | N/A | **None** | **G-AP1: no locking test** | Medium | **AP1 — add now** |
| apply_patch emits NO apply_patch_call.* sub-events | N/A | N/A | N/A | **None** | **G-AP1: no locking test** | Medium | **AP1 — add now** |
| coercion error does not leak raw args | N/A | no raw args in error | no raw args in telemetry | **None** | **G-AP2: no safety test** | Medium | **AP2 — add now** |
| Non-streaming path (qz_sse.py) | `output_item.added/done` | N/A | N/A | `test_stream_synthesis_includes_apply_patch_call_item` | None — covered | None | — |
| Dropped apply_patch | none | "not available" error | `tool_call_error` | indirect in proxy_tools tests | No explicit apply_patch-named drop test | Low | AP3 |

---

## 9. Recommended Implementation Slices

### Slice AP1 — Lock Codex-visible lifecycle contract (safe now)

Add tests asserting that valid `apply_patch` SSE output:

1. Emits `response.output_item.added` with `"type": "apply_patch_call"`.
2. Emits `response.output_item.done` with `"type": "apply_patch_call"`.
3. Does **not** emit `response.apply_patch_call.in_progress`.
4. Does **not** emit `response.apply_patch_call.searching`.
5. Does **not** emit `response.apply_patch_call.completed`.
6. Does **not** emit any `response.web_search_call.*`.
7. Does **not** emit `response.function_call_arguments.delta/done`.

Cover both native and custom output styles. Use existing `FakeStream` /
`_apply_patch_call_stream()` infrastructure.

**Priority: high.** No runtime changes. Tests only.

### Slice AP2 — Coercion/advice fixture coverage (safe now)

Add focused tests for model-visible `apply_patch` error/advice:

1. Invalid JSON args → error contains `"apply_patch: arguments are not valid JSON"`.
2. Missing diff → error contains `"missing 'diff'"`.
3. Missing destination → error contains `"missing destination"`.
4. `call_id` preserved in error `function_call_output`.
5. Raw argument content not present in error text.
6. Original bad `function_call` item not forwarded as-is when error result is generated.

**Priority: high.** No runtime changes. Tests only.

### Slice AP3 — Safe telemetry hardening (low risk)

Extend `coercion_succeeded`/`coercion_failed` events with safe metadata only:

- `operation_type` (enum value)
- `path_present` (bool)
- `diff_present` (bool)
- `destination_present` (bool)

Verify no raw patch body, no raw argument string, no file path value is emitted.
Tests: assert presence of safe fields, absence of unsafe content.

**Priority: low.** Small runtime change but verifiably safe.

### Slice AP4 — Live Codex capture (deferred, requires running Codex)

Capture a real `apply_patch` streaming session with `QZ_CAPTURE_MODE=on`.
Compare:

- Forwarded SSE to Codex vs official event contract.
- What Codex renders for `apply_patch_call` item type.
- What Codex renders for `custom_tool_call apply_patch` item type.
- Whether `response.apply_patch_call.*` sub-events would be rendered if added.

Only after this decide whether custom_tool_call_input.delta/done events would
be beneficial. Do not implement §AP5 without this evidence.

### Slice AP5 — Optional sub-lifecycle (deferred, requires AP4 evidence)

If Codex live capture proves that `response.apply_patch_call.in_progress/completed`
events are rendered and useful, add them via the existing `ToolLifecycleSpec`
system:

1. Change `execution` from `"protocol_adapter"` to `"proxy_local"`.
2. Add `lifecycle_event_prefix="response.apply_patch_call"`, start stages, done stages.
3. Implement a proxy-local executor that does the shape conversion (currently done
   in output_to_codex).
4. This is a non-trivial change — the current protocol_adapter path does not
   run a proxy executor. Full apply_patch execution audit required first.

**Do not implement without AP4 evidence and a full execution audit.**

---

## 10. Risks

### R-AP1: Invented apply_patch sub-lifecycle events

Adding `response.apply_patch_call.in_progress/completed` without Codex client
proof risks:

- Events ignored by Codex (no observable benefit, extra bytes sent).
- Events breaking Codex's internal event parser if the event names conflict with
  Codex's own event type registry.
- Out-of-sequence events visible to users if Codex renders them mid-application.

**Mitigation:** AP4 live capture before any AP5 implementation.

### R-AP2: Patch body leakage in telemetry

The `coercion_succeeded`/`coercion_failed` telemetry currently emits `error_summary`
(200-char cap) but no content from the patch body. If any future path adds patch
content to telemetry, it could leak sensitive file contents.

**Mitigation:** AP3 adds safe-metadata-only fields with explicit tests for absence
of raw content.

### R-AP3: `function_call_arguments.delta` leakage

If `is_function_call_stream_event` fails to match a new event type, raw argument
deltas (including full patch body) could be forwarded to Codex. The current guard
checks `event_type` prefix and `payload["item"]["type"]`.

**Mitigation:** Do not add new function-call-adjacent event types without updating
`is_function_call_stream_event` and adding a suppression test.

### R-AP4: Fake successful lifecycle for malformed patch

If `_function_call_to_apply_patch_call` returns a partial operation (via
`_extract_partial_native_operation`) when full coercion fails, Codex receives
an `apply_patch_call` item with an incomplete operation. Codex may then attempt
to apply an empty or partial patch, producing a confusing file system error.

**Current state:** partial operations are intentionally emitted when at least
`type+path` can be extracted, so Codex's own V4A verifier can produce a
specific error. The `coerce()` method in `ApplyPatchToolAdapter` runs before
`output_to_codex`, and if it fails the error path fires — so the partial-op path
only activates when `_parse_apply_patch_arguments` *succeeds* partially.

**Mitigation:** AP2 tests confirm the boundary between full-coerce-success and
the fallback assistant message path.

### R-AP5: Over-advising the model

If the error text from `_describe_args_failure` is too long or verbose, it
consumes context budget. Current messages are compact and specific. AP2 tests
will confirm no raw args are echoed.

### R-AP6: `_invalid_apply_patch_call_message` vs `synthesize_tool_error_result`

These are two distinct paths:

- `coerce()` fails → `synthesize_tool_error_result` → `function_call_output`
  injected into model's next-hop `input`.
- `_function_call_to_apply_patch_call` fails → `_invalid_apply_patch_call_message`
  → assistant `message` item returned as `public_item`.

The second path produces a **Codex-visible assistant message**, not just a model
feedback item. This happens when the streaming runtime calls `output_to_codex`
on a call for which coercion already reported success (because `_parse_apply_patch_arguments`
returned a partial result that `coerce()` accepted but `output_to_codex` cannot
render). In theory this path should be unreachable if `coerce()` and
`output_to_codex` are consistent. AP2 should verify this path is not silently
hit on normal valid calls.

---

## 11. Slice L3 Status (from codex-visible-tool-lifecycle-audit.md)

`docs/codex-visible-tool-lifecycle-audit.md §9 Slice L3` recommends:

> Add a unit test that asserts `response.apply_patch_call.in_progress` is NOT
> in the Codex stream when apply_patch runs.

This is superseded by **Slice AP1** above, which is broader and covers all
unsupported sub-lifecycle event names plus the web_search cross-contamination
check. Slice AP1 is the implementation vehicle for Slice L3.
