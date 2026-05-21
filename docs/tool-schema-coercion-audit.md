# Tool Schema, Coercion, and Advice Path Audit

Date: 2026-05-22
Status: Slice B audit — findings and B2 fix plan.

Related:
- `docs/runtime-streaming-tool-contract-audit.md` — pipeline overview (Slice A).
- `docs/tool-coercion-design.md` — coercion design spec; implementation now complete.
- `docs/responses-stream-tool-state-contract.md` — streaming contract.

---

## Summary

Tool schema replacement (dedup and name-based substitution) is **fixed** as of
commit `ebdf87b`. All other coercion/advice paths are **implemented** and
**correct** for the streaming path. The non-streaming path has a narrow gap for
non-proxy-local dropped/unknown tools. Telemetry coverage is the primary
outstanding gap: coercion success/failure and schema replacement are invisible
to telemetry and qz-thoughts.

---

## A. Tool Schema Replacement Audit

### Incoming Codex tool shapes accepted

| tool shape | handling |
|---|---|
| `{"type": "web_search"}` | Matched by `WebSearchToolAdapter.accepts_tool` (checks `type == "web_search"`). Replaced by proxy schema via `to_upstream_tool`. |
| `{"type": "function", "name": "web_search", ...}` | **Newly fixed (ebdf87b)**: matched by `ToolRegistry.adapter_for_name("web_search")` in the `type==function` branch. Replaced by proxy schema. Logged in `report.replaced`. |
| `{"type": "apply_patch"}` | Matched by `ApplyPatchToolAdapter.accepts_tool`. Replaced by function schema. |
| `{"type": "custom", "name": "apply_patch"}` | Matched by `ApplyPatchToolAdapter.accepts_tool` (also checks custom type). Replaced. |
| `{"type": "function", "name": "apply_patch"}` | Matched by `ToolRegistry.adapter_for_name("apply_patch")`. Replaced. Logged in `report.replaced`. |
| `{"type": "function", "name": "exec_command", ...}` | Passed through with `FILE_EDIT_TOOL_HINT` appended. |
| `{"type": "function", "name": "write_stdin", ...}` | Dropped if no live exec session; passed through with hint otherwise. |
| `{"type": "function", "name": "<other>"}` | Passed through; deduped by seen-name set. |
| `{"type": "<unknown-structured>"}` | Dropped (no adapter; no passthrough). |

### Dedup and replacement status

- Seen-name set is maintained across the entire tool list iteration.
- A second `type=web_search` after the first is skipped silently (seen-name guard).
- A `type=function name=web_search` appearing after a `type=web_search` → skipped.
- A `type=web_search` appearing after a `type=function name=web_search` → the function tool is already replaced and seen; the structured tool is skipped.
- Two identical `type=function name=web_search` entries → first replaced, second dropped with `"web_search(duplicate)"` in report.

**All dedup cases are deterministic and correct.** First occurrence wins.

### tool_choice handling

- `{"type": "web_search"}` → `WebSearchToolAdapter.normalize_tool_choice` → `{"type": "function", "name": "web_search"}`.
- `{"type": "apply_patch"}` or `{"type": "custom", "name": "apply_patch"}` → `{"type": "function", "name": "apply_patch"}`.
- `{"type": "function", ...}` → passed through unchanged.
- Any other structured type → forced to `"auto"` (tool_choice_forced_auto=True).

### Stale Codex web_search schema replacement status

Status: **confirmed replaced**. `WebSearchToolAdapter.to_upstream_tool()` always
emits the proxy-owned schema with `action="capabilities"` in the description and
the `engines`/`profile`/`budget_mode`/`retrieval_source` parameter set. No stale
Codex default schema can survive to upstream.

### Replacement/dedup captured in

- `report.replaced: tuple[str, ...]` — names replaced via name-based lookup.
- `report.translated: tuple[str, ...]` — names replaced via type-based adapter match.
- `report.dropped: tuple[str, ...]]` — names dropped (with reason suffix).
- Capture files: `latest-dropped-tools.txt`, `forwarded-request-after-tools.json`.

### Replacement/dedup telemetry status

**Gap**: no telemetry event is emitted for schema replacement or dedup. The
`ToolRequestNormalizationReport` is written to captures only. qz-thoughts and the
telemetry stream have no visibility into which tools were replaced or dropped.

---

## B. Coercion/Advice Path Audit

### ToolCoercionResult

```python
@dataclass
class ToolCoercionResult:
    corrected_arguments: str | None = None
    error_message: str | None = None

def succeeded(self) -> bool:
    return self.corrected_arguments is not None
```

**Gap**: neither-set case (`ToolCoercionResult()` with no arguments) is
constructible. `succeeded()` returns False. `coercion.error_message or ""`
produces an empty string. `synthesize_tool_error_result` then emits:
`{"ok": false, "error": ""}` — valid JSON but an empty error message. The model
receives no useful feedback.

No `__post_init__` validation exists to enforce the "exactly one field set" contract.

### synthesize_tool_error_result

```python
def synthesize_tool_error_result(call: dict, message: str) -> dict:
    call_id = call.get("call_id") or call.get("id") or f"err_{int(time.time())}"
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": json.dumps({"ok": False, "error": message}),
    }
```

- Protocol-valid `function_call_output`. ✓
- No raw arguments, local paths, or stack traces in output. ✓
- call_id falls back to `id` then a timestamp. ✓ (timestamp fallback produces a unique but non-stable ID — acceptable for error injection since no matching call is expected).

### CODEX_NATIVE_TOOL_NAMES

`frozenset({"exec_command", "write_stdin", "shell_command", "computer"})`

These bypass all coercion and go directly to `kind="public"` with the original
call as `public_item`. Codex receives them unchanged and executes them.
`repeated_read_signal` is still checked before passthrough for these names.

### ToolRegistry.coerce_call

Matches by `spec.name == call.get("name")` — first matching adapter wins.
Falls back to `_coercion_error(name)` (generic error message) for unmatched calls.
Note: `spec_for_name(name)` is the gate for entering the adapter coerce path
(step 3 in `completed_call_decision`). This only runs for adapters in
`DEFAULT_TOOL_REGISTRY` (apply_patch and web_search).

### completed_call_decision routing

Decision priority order:

1. `name in dropped_tool_names` → `kind="error"`, specific "not available" message.
2. `is_proxy_local_call(call)` → coerce() via executor → `kind="proxy_local"` or `kind="error"`.
3. `tool_registry.spec_for_name(name)` → coerce_call() → `kind="public"` or `kind="error"`.
4. `name in CODEX_NATIVE_TOOL_NAMES` → repeated-read check → `kind="signal"` or `kind="public"`.
5. Everything else → `kind="error"`, "not recognised" message.

Used by:
- Streaming path: `ResponsesStreamRuntime.run()` — every completed tool call.
- Non-streaming path: `_run_responses_locally()` — only for proxy-local items directly; non-proxy-local items use a limited version (signal check only, error NOT injected).

### web_search coerce() path

`WebSearchToolAdapter.coerce(call)`:
- Invalid JSON arguments → `ToolCoercionResult(error_message="web_search: arguments are not valid JSON...")`.
- Non-dict JSON → `ToolCoercionResult(error_message="web_search: arguments must be a JSON object...")`.
- Valid dict (even empty) → `ToolCoercionResult(corrected_arguments=arguments)`.

**Observation**: empty dict `{}` passes coerce(). Runtime validation in
`execute_web_search_call` handles missing fields (missing `action` defaults to
`"search"`; missing `query` returns `{"ok": false, "error": "Missing query"}`).
This is correct — structural validation by coerce(), semantic validation by runtime.

Web_search in-band errors (`{"ok": false, "error": "..."}` in `function_call_output`)
do NOT go through `synthesize_tool_error_result`. They are returned by
`execute_web_search_call` directly as `tool_output_item`. The format is
`{"type": "function_call_output", "call_id": ..., "output": json.dumps({...})}` — same shape as synthesize_tool_error_result but different creation path.

### apply_patch coerce() path

`ApplyPatchToolAdapter.coerce(call)`:
- Multiple parse paths tried in order: nested operation, sibling patch promotion, top-level flat, patch envelope extraction.
- On success: `ToolCoercionResult(corrected_arguments=json.dumps({"operation": operation}))`.
- On failure: `ToolCoercionResult(error_message="apply_patch: {specific reason}")`.
- Specific reasons: "arguments are not valid JSON", "missing diff", "missing destination", etc.

The corrected_arguments flow: `dict(call, arguments=coercion.corrected_arguments)` —
creates a new call dict with corrected arguments. The corrected call is then
passed to `output_to_codex(corrected, apply_patch_output_style)` which converts
it to the Codex-facing shape (apply_patch_call or custom_tool_call).

### qz_probe executor (test-only)

`ProbeProxyToolExecutor` exists in `tests/test_qz_proxy_tools.py` only. It is a
test fixture, not a production tool. It has no `coerce()` method — falling back to
the generic `_coercion_error` in `ToolRegistry.coerce_call`. This is intentional:
it proves the interface without needing recovery logic, as the design doc specifies.

### Repeated-read / advisory signal path

`repeated_read_signal(call, state)` returns a `RepeatedReadDecision(should_signal, message, paths, action, scope)`.
If `should_signal`:
- `render_advisory_output(call, message)` builds a `function_call_output` with advisory text.
- `kind="signal"` returned.
- In streaming: `hs.next_input.append(decision.signal_result)`, break hop, telemetry `repeated_read_signal`.
- In non-streaming: `next_input.append(rr_decision.signal_result)`, telemetry `repeated_read_signal`.
- Codex client: NOT visible (injected into upstream conversation, not client output stream).
- Model: visible next hop (receives advisory as function_call_output).
- qz-thoughts: YES — `repeated_read_signal` telemetry event is emitted with metadata.

### Dropped tool feedback (detailed)

Trigger: `name in dropped_tool_names` (frozenset from `body.metadata.qz_dropped_tool_names`).
Message: `"Tool '{name}' is not available in this session. It was removed from the tool list before this request. Use a different tool or approach."`

- Streaming path: `decision.error_result` → `hs.next_input.append(decision.error_result)`, `hs.error_injected = True`, telemetry `tool_call_error`.
- Non-streaming (proxy-local items only): `next_input.append(decision.error_result)`.
- Non-streaming (non-proxy-local items): **dropped/unknown error NOT applied** — item passes through unchanged to next_input. See gap below.
- Codex client: not visible (injected as upstream input, not as output stream event).
- Model: visible next hop.
- qz-thoughts: YES — `tool_call_error` telemetry (streaming path only).
- Raw args: no.
- Local paths: no.

### Unknown tool feedback (detailed)

Trigger: falls through all 4 known categories.
Message: `"Tool '{name}' is not recognised by the proxy and cannot be executed. Check the available tools and retry with a supported tool name."`

All visibility properties same as dropped tool feedback above.

---

## C. Codex-Native Passthrough Audit

### Names treated as Codex-native

`frozenset({"exec_command", "write_stdin", "shell_command", "computer"})`

These are defined in `proxy/qz_tools.py`. No other names bypass coercion.

### exec_command passthrough

- Always passed through with `FILE_EDIT_TOOL_HINT` appended to description (at request normalisation time).
- In `completed_call_decision`: reaches step 4 (CODEX_NATIVE_TOOL_NAMES), returns `kind="public"` with original call as `public_item`.
- No coercion applied. Codex executes it.
- Repeated-read check applies ONLY if `repeated_read_state` is not None AND the call reads a path already warned.

### write_stdin handling

**At request normalisation** (`normalize_tool_request_for_llamacpp`):
- `write_stdin` with no live exec session → dropped. Name added to `report.dropped` with suffix `"(no live exec session)"`.
- `write_stdin` with live exec session → passed through with hint.
- Live session detection: `input_has_exec_session(body.input)` — scans `function_call_output` items for `session_id` pattern.

**At completed-call routing** (if write_stdin passes normalisation and model calls it):
- Reaches step 4 (CODEX_NATIVE_TOOL_NAMES), returns `kind="public"`.
- Codex executes it.

**Dropped write_stdin**: if dropped at normalisation time, name goes into `dropped_tool_names`. If the model calls write_stdin anyway, `completed_call_decision` step 1 fires and injects the specific error.

### Do dropped native tools generate feedback?

- Yes — if write_stdin is in `dropped_tool_names` and the model calls it, step 1 of `completed_call_decision` fires and the dropped-tool error is injected.
- No explicit test covers this end-to-end. Unit test in `test_qz_tools.py` (`test_dropped_tool_returns_error_decision`) covers the routing decision; the streaming integration is not covered by a dedicated fixture.

### Codex-native names bypass proxy coercion correctly?

Yes. The check `if name in CODEX_NATIVE_TOOL_NAMES` runs at step 4, after dropped-tool (step 1), proxy-local (step 2), and protocol-adapter (step 3) checks. So if exec_command were somehow added to the dropped list, the dropped-tool error would fire before the native passthrough. This is correct behaviour.

---

## D. Failure Matrix

| scenario | current behaviour | expected | Codex-visible? | model next-hop feedback? | qz-thoughts? | telemetry? | leak risk | test coverage | fix needed? |
|---|---|---|---|---|---|---|---|---|---|
| valid web_search `type=web_search` | translated to function schema; forwarded to upstream | ✓ | no (upstream) | n/a | no (replacement only in captures) | **none** | none | unit tests | add replacement telemetry (B2) |
| valid web_search `type=function name=web_search` | **replaced** by proxy schema (ebdf87b) | ✓ | no | n/a | no | **none** | none | unit tests (ebdf87b) | add replacement telemetry (B2) |
| duplicate web_search (both typed) | first wins; second dropped | ✓ | no | n/a | no | none | none | unit tests (ebdf87b) | none |
| stale Codex web_search schema | replaced; `action="capabilities"` in description | ✓ | no | n/a | no | none | none | unit tests | none |
| malformed web_search JSON | coerce() → error result injected → model next hop | ✓ | no | yes (error string) | yes (tool_call_error streaming) | tool_call_error (streaming) | none | unit test (coerce), **no streaming fixture** | add streaming fixture (B2) |
| valid-but-runtime-invalid web_search (e.g. missing query) | runtime returns `{"ok": false, "error": "Missing query"}` in function_call_output | ✓ | no | yes | yes (tool_call_completed) | tool_call_completed | none | unit tests (execute_web_search_call) | none |
| valid apply_patch | coerce() → corrected args → output_to_codex → public item to Codex | ✓ | yes (apply_patch_call or custom_tool_call) | n/a | yes (output_item.done) | n/a | none | unit + golden tests | none |
| malformed apply_patch | coerce() → error result injected (streaming) or partial envelope (public path) | ✓ | no (streaming error) or partial envelope (public) | yes (streaming: error; public: Codex verifier) | yes (tool_call_error streaming) | tool_call_error (streaming) | none | coerce unit tests; **no streaming fixture** | add streaming fixture (B2) |
| unknown tool | completed_call_decision step 5 → error result | ✓ | no | yes | yes (tool_call_error streaming) | tool_call_error (streaming) | none | unit tests (test_qz_tools.py, test_qz_proxy_tools.py) | none |
| dropped write_stdin (no live session) | dropped at normalisation; if called → dropped-tool error injected | ✓ | no | yes | partial (tool_call_error streaming) | tool_call_error (streaming) | none | normalisation unit tests; **no streaming end-to-end fixture** | add streaming fixture (B2) |
| exec_command (Codex-native) | hint appended at normalisation; passes through via `kind="public"` | ✓ | yes | n/a | yes (output_item.done) | n/a | none | unit tests | none |
| repeated-read/advisory signal | signal result injected; model sees advisory; hop continues | ✓ | no | yes | yes (repeated_read_signal telemetry) | repeated_read_signal | none | unit + proxy_tools tests | none |
| coercion success (any tool) | corrected arguments used silently | ✓ | no | n/a | **no** | **none** | none | unit tests | add coercion_success telemetry (B2) |
| coercion failure (any tool) | error result injected | ✓ | no | yes | partial (tool_call_error streaming) | tool_call_error (streaming only) | none | unit tests; **no streaming fixture** | add coercion_failed telemetry (B2) |
| non-proxy-local dropped/unknown tool in non-streaming path (with web_search present) | completed_call_decision called, error computed, but `else` branch passes original item through | **gap** | original item (function_call) passed to Codex-facing output | no (item not error) | no | none | none | none | **fix in B2**: apply error injection for non-proxy-local items in non-streaming hop loop |
| ToolCoercionResult neither-set | `coercion.succeeded()=False`, `error_message=""`, empty error injected | should raise or produce non-empty error | no | yes (but empty message) | no | none | none | none | guard ToolCoercionResult (B2) |

---

## E. Test Coverage Audit

### Existing tests

| area | test file | tests | status |
|---|---|---|---|
| schema replacement (type=web_search) | test_qz_tool_request.py | `test_normalizer_reports_translated_dropped_and_tool_choice` | ✓ |
| schema replacement (type=function name=web_search) | test_qz_tool_request.py | `test_function_typed_web_search_is_replaced_by_proxy_schema` | ✓ (ebdf87b) |
| duplicate tool names | test_qz_tool_request.py | `test_duplicate_tool_names_deduped`, `test_function_typed_web_search_deduped_when_structured_also_present` | ✓ (ebdf87b) |
| ToolCoercionResult | test_qz_tools.py | `test_succeeded_when_corrected_arguments_set`, `test_not_succeeded_when_error_message_set` | ✓; neither-set case **not tested** |
| synthesize_tool_error_result | test_qz_tools.py | `test_produces_function_call_output`, `test_uses_call_id_from_call` | ✓ |
| unknown tool feedback | test_qz_tools.py, test_qz_proxy_tools.py | `test_unknown_tool_returns_error_decision` | ✓ |
| dropped tool feedback | test_qz_tools.py, test_qz_proxy_tools.py | `test_dropped_tool_returns_error_decision` | ✓ |
| web_search malformed args (coerce) | test_qz_tools.py | `test_bad_json_returns_error`, `test_non_dict_json_returns_error`, `test_valid_json_passes_through` | ✓ |
| apply_patch malformed args (coerce) | test_apply_patch_adapter.py | `test_coerce_bare_operation_returns_error_message`, `test_coerce_missing_destination`, `test_coerce_bad_json` | ✓ |
| apply_patch valid coerce | test_apply_patch_adapter.py | `test_coerce_valid_operation_returns_corrected_arguments`, `test_coerce_sibling_patch_returns_corrected_arguments` | ✓ |
| Codex-native passthrough | test_qz_tools.py, test_qz_proxy_tools.py | `test_codex_native_tool_passes_through`, `test_completed_call_decision_keeps_unknown_function_call_public` | ✓ |
| repeated-read signal | test_qz_proxy_tools.py | 10+ tests | ✓ |
| adapter_for_name | (no explicit test) | — | **missing** |
| replacement logged in captures | test_qz_tool_request.py | `test_replaced_appears_in_capture_notes` | ✓ |

### Missing tests (precise list)

1. **`test_neither_set_coercion_result_produces_nonempty_error`** — construct `ToolCoercionResult()` and verify `error_message` is not None or empty before injecting. Documents and guards the gap.

2. **`test_adapter_for_name_returns_correct_adapter`** — `ToolRegistry.adapter_for_name("web_search")` returns `WEB_SEARCH_TOOL_ADAPTER`; `adapter_for_name("apply_patch")` returns `APPLY_PATCH_TOOL_ADAPTER`; `adapter_for_name("unknown")` returns None.

3. **`test_streaming_coerce_error_suppresses_lifecycle_event`** — using a `ResponsesStreamRuntime` fixture with a mock upstream that emits a malformed web_search call: verify no `response.web_search_call.*` lifecycle events appear in the Codex stream; verify error `function_call_output` is in the next hop input; verify `tool_call_error` telemetry fires.

4. **`test_streaming_coerce_error_no_raw_args_leaked`** — in the coerce-failure scenario above: verify the error message in the injected `function_call_output` does not contain the raw arguments string.

5. **`test_streaming_dropped_tool_error_injected`** — via fixture: model calls a tool with a name that is in `dropped_tool_names`; verify error result in next_input, no lifecycle event to Codex.

6. **`test_non_streaming_dropped_nonproxy_tool_applies_error`** — non-streaming path: output_items contains web_search AND a dropped non-proxy-local tool; verify the dropped tool's error result is injected into next_input, not the original item. (Tests current gap — will fail until B2 fix.)

7. **`test_coercion_success_no_raw_args_in_codex_output`** — apply_patch sibling-patch coercion succeeds; verify the corrected arguments are used; verify original malformed arguments do not appear in Codex-visible output.

8. **`test_write_stdin_dropped_then_called_generates_feedback`** — write_stdin dropped at normalisation (no live session); model then calls write_stdin; verify error result injected, error text mentions "not available".

9. **`test_tool_schema_replaced_telemetry_event`** — when a function-typed web_search is replaced, a `tool_schema_replaced` telemetry event fires. (Will fail until B2 adds this event.)

10. **`test_coercion_failed_telemetry_event`** — when coerce() returns an error, a `coercion_failed` telemetry event fires. (Will fail until B2.)

---

## F. Critical Gaps

### Gap 1 — Zero telemetry for tool schema replacement

`ToolRequestNormalizationReport.replaced` is populated and written to captures, but
no telemetry event is emitted. The operator cannot tell from qz-thoughts or the
telemetry stream whether Codex sent a stale schema that was replaced.

**Fix (B2)**: in `normalize_tool_request_for_llamacpp`, emit a `tool_schema_replaced`
telemetry event when `replaced` is non-empty. Payload:
`{"replaced": list(replaced), "translated": list(translated), "dropped_count": len(dropped)}`.

### Gap 2 — Zero telemetry for coercion success/failure

No `coercion_success` or `coercion_failed` event is emitted. If apply_patch or
web_search calls need coercion, the operator sees nothing in qz-thoughts.

**Fix (B2)**: emit `coercion_succeeded` (with tool name and call_id) and
`coercion_failed` (with tool name, call_id, and error_message preview) in
`completed_call_decision` at the point of each coerce() result. Do NOT include
raw arguments in the payload.

### Gap 3 — ToolCoercionResult neither-set case

`ToolCoercionResult()` is constructible and produces an empty error message.
`synthesize_tool_error_result` emits `{"ok": false, "error": ""}`.

**Fix (B2)**: add a `__post_init__` assertion, or add `@classmethod success(args)` /
`@classmethod failure(msg)` constructors. Quickest safe fix: assert in `__post_init__`
that exactly one of the two fields is not None.

### Gap 4 — Non-streaming path: dropped/unknown errors not applied for non-proxy-local items

In `_run_responses_locally`, the `for item in output_items` loop calls
`completed_call_decision` for non-proxy-local items but only acts on `kind="signal"`.
`kind="error"` (dropped/unknown) falls to the `else` branch, passing the original
item unchanged to `next_input`.

Impact: when web_search and a dropped non-proxy-local tool appear in the same
upstream response, the model gets the dropped tool back in conversation history
instead of an error. It may keep calling it.

**Fix (B2)**: in the non-proxy-local item loop, add an `elif rr_decision.kind == "error"` branch:
```python
elif rr_decision.kind == "error":
    next_input.append(rr_decision.error_result)
```

### Gap 5 — No streaming fixture for coercion error path

The coercion error path in `completed_call_decision` → streaming `kind=="error"` → 
`hs.next_input.append(decision.error_result)` has no end-to-end streaming test. Only
unit tests for `completed_call_decision` exist; the streaming hop loop integration is
not covered.

**Fix (B2)**: add a `ResponsesStreamRuntime` fixture test that drives a malformed
tool call through the hop loop and verifies the error appears in the next-hop request.

---

## G. Recommended Slice B2 Fix Plan

Priority order (highest to lowest):

1. **Guard `ToolCoercionResult` neither-set case** (`proxy/qz_tools.py`)
   - Add `__post_init__` that asserts not both None OR raises if both None.
   - Risk: very low. Change is additive.

2. **Apply dropped/unknown errors in non-streaming hop loop for non-proxy-local items** (`proxy/qz_request_router.py`)
   - In `_run_responses_locally`, add `elif rr_decision.kind == "error": next_input.append(rr_decision.error_result)`.
   - Risk: low. Only fires when a tool is in `dropped_tool_names` AND appears in output with a web_search call.

3. **Emit `tool_schema_replaced` telemetry** (`proxy/qz_tool_request.py`)
   - In `normalize_tool_request_for_llamacpp`, if `replaced` is non-empty, emit via the handler telemetry (or accept a telemetry argument).
   - Risk: low. Observability only.
   - Note: `normalize_tool_request_for_llamacpp` has no telemetry reference currently; the telemetry can be emitted in `proxy_json_api` after normalisation since the report is available.

4. **Emit `coercion_succeeded` / `coercion_failed` telemetry** (`proxy/qz_proxy_tools.py`)
   - In `completed_call_decision`, after coerce() result, emit via a telemetry callback or store in the decision for the caller to emit.
   - Simpler approach: emit in `ResponsesStreamRuntime.run()` and `_run_responses_locally` where decision.kind is inspected.
   - Risk: low.

5. **Add missing tests** (`tests/test_qz_tools.py`, `tests/test_qz_tool_request.py`, `tests/test_qz_proxy_tools.py`, `tests/test_qz_responses_stream.py`)
   - Tests 1–8 from Section E. Tests 9–10 depend on telemetry implementation.

Do not: change streaming event shapes, change tool execution logic, add new tool adapters.
