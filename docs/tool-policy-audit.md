# Tool Coercion and Advisory Policy Audit

Date: 2026-05-20
Status: #59 Slice A-audit — complete. No runtime changes.

---

## 1. Overview

QuantZhai intercepts every tool call before it reaches the Codex agent or the
upstream model. The interception chain has five decision paths, each with
different coercion, advisory, and telemetry behaviour.

**Feedback primitives** (`proxy/qz_feedback.py`):

| Type | Visibility | Channel | Use case |
|---|---|---|---|
| `render_advisory_output(call, msg)` | MODEL | FUNCTION_CALL_OUTPUT | Soft advisory (repeated-read); plain text output |
| `render_coercion_error(call, msg)` | MODEL | FUNCTION_CALL_OUTPUT | Hard coercion failure; `{"ok": false, "error": "..."}` JSON |
| `synthesize_tool_error_result(call, msg)` | MODEL | FUNCTION_CALL_OUTPUT | Same shape as `render_coercion_error`; kept for legacy callers |

`FeedbackVisibility` values: `MODEL`, `OPERATOR`, `BOTH`.
`FeedbackChannel` values: `FUNCTION_CALL_OUTPUT`, `TELEMETRY`, `INSTRUCTIONS`,
`TURN_HARNESS`, `FUTURE_STATE`.

---

## 2. Decision chain (`completed_call_decision` in `qz_proxy_tools.py`)

Called once per completed tool call during the streaming loop. Returns a
`CompletedToolCallDecision` with one of five `kind` values.

```
function call arrives
        │
        ▼
dropped_tool_names check  ──► kind="error" (MODEL, hard refusal)
        │ not dropped
        ▼
is_proxy_local_call?  ──► coerce ──► kind="error" (MODEL, hard)
        │ yes                          or kind="proxy_local" (execute)
        │ no
        ▼
tool_registry.spec_for_name?  ──► coerce ──► kind="error" (MODEL, hard)
        │ yes (apply_patch)              or kind="public" (pass-through)
        │ no
        ▼
CODEX_NATIVE_TOOL_NAMES?  ──► repeated-read check ──► kind="signal" (MODEL, advisory)
        │ yes                                            or kind="public" (pass-through)
        │ no
        ▼
unknown tool  ──► kind="error" (MODEL, hard refusal)
```

---

## 3. Path-by-path detail

### 3.1 Dropped tool

**Trigger:** `name in dropped_tool_names` (populated from `qz_dropped_tool_names`
metadata in the request body).

**Coercion/refusal:** Hard. `synthesize_tool_error_result` → `kind="error"`.

**Model-visible output:** `{"ok": false, "error": "Tool 'X' is not available in this session..."}`.

**Telemetry:** Telemetry emitted via `ProxyLocalToolRegistry.telemetry_payload()` at the call site in `qz_responses_stream.py`. No dedicated dropped-tool telemetry event.

**Budget enforcement:** None (tool is refused before execution).

**Gap:** No advisory signal before drop — model only learns tool was dropped after trying it. Could be pre-announced at start of request.

---

### 3.2 Proxy-local tools (`web_search`, BrainCase tools when enabled)

**Owner:** `proxy/qz_proxy_tools.py` → `WebSearchProxyToolExecutor` (or equivalent BrainCase executor).

**Execution mode:** `ToolLifecycleSpec.execution = "proxy_local"`. The proxy executes the tool locally; a continuation hop follows.

**Coercion:**
- `WebSearchToolAdapter.coerce()` in `proxy/qz_tool_web.py`: validates `action`/`query`/`url`; fixes minor argument issues.
- If coercion fails: `synthesize_tool_error_result` → `kind="error"` (MODEL visible, hard).
- If coercion succeeds: re-runs with corrected arguments.

**Budget enforcement (web_search):**

| Limit | Default | Enforcement location |
|---|---|---|
| `max_searches_per_turn` | 4 (`WEB_SEARCH_MAX_SEARCHES`) | `execute_web_search_call()` in `qz_tool_web.py` |
| `max_page_opens_per_turn` | 3 (`WEB_SEARCH_MAX_OPENS`) | same |
| `max_results_per_query` | 8 (`WEB_SEARCH_MAX_RESULTS`) | `_search_web()` |
| `max_continuation_hops` | 6 (`ToolLifecycleSpec.continuation_hops`) | `qz_request_router.py` |

**Budget refusal format:** `{"ok": false, "error": "Refusing search: reached per-turn limit..."}` injected as `function_call_output` — MODEL visible, hard refusal. No OPERATOR telemetry event for budget-exceeded.

**Repeat-guard:** Signatures tracked in `seen_signatures`; repeated searches/opens refused with a hard error.

**Telemetry:** `tool_call_started`, `web_search_route`, `tool_call_completed` emitted via `WebSearchRuntime._emit()`. Sources count, profile decision, engine list, fallback path all logged.

**Model-visible lifecycle:** public `web_search_call` item emitted with `status="in_progress"` then `status="completed"`. Failures show `status="failed"`.

**Known gaps:**
- Budget-exceeded refusal is hard error only; no soft advisory to help model plan within limits.
- No operator telemetry event when budget is hit; hard to diagnose budget exhaustion in qz-thoughts.
- Source quality signals beyond URL dedup are not implemented (→ #60).
- Profile auto-detection uses keyword matching only; no feedback loop (→ #60).

---

### 3.3 Protocol adapter tools (`apply_patch`)

**Owner:** `proxy/qz_tool_apply_patch.py` → `APPLY_PATCH_TOOL_ADAPTER`.

**Execution mode:** `ToolLifecycleSpec.execution = "protocol_adapter"`. The proxy coerces arguments and converts the call/output shape for Codex compatibility; Codex executes natively.

**Coercion chain (multiple fallback layers):**

1. `_parse_apply_patch_arguments(arguments)` — primary parser
2. `_coerce_apply_patch_operation(data)` — recursive operation dict normalization
3. `_extract_op_and_path_from_patch_envelope(patch_text)` — extracts from raw diff-like text if structured parsing fails
4. Minimal reconstruction from `type`+`path` only when full parse fails
5. `ApplyPatchToolAdapter.coerce()` aggregates all layers

**Coercion success:** Returns `ToolCoercionResult.corrected_arguments` (JSON string). Caller replaces `call["arguments"]` with corrected version.

**Coercion failure:** `ToolCoercionResult.error_message` → `synthesize_tool_error_result` → `kind="error"` (MODEL visible, hard). Error message includes specific failure reason from `_apply_patch_coercion_failure_reason()`.

**Telemetry:** No dedicated coercion-success telemetry. Coercion failure results in error injection but no separate event.

**Known gaps:**
- No telemetry event when coercion was required but succeeded — model silently gets corrected args.
- No advisory to model when borderline coercion occurred (e.g. envelope unwrapping) (→ #62).
- Edge cases in `_extract_op_and_path_from_patch_envelope` lack test coverage (→ #62).
- The multi-layer fallback chain has 4+ paths; observability is limited.

---

### 3.4 Codex-native tools (`exec_command`, `write_stdin`, `shell_command`, `computer`)

**Defined in:** `CODEX_NATIVE_TOOL_NAMES` frozenset in `proxy/qz_tools.py`.

**Execution mode:** Pass-through. The proxy does not execute these; Codex handles them in its sandbox/harness.

**Coercion:** None. Native tool calls are never coerced by the proxy.

**Advisory signals (repeated-read):**
- `repeated_read_signal(call, repeated_read_state)` from `proxy/qz_file_signal.py`
- Triggers when a file path was already read in this session (input history or current run)
- Returns `should_signal=True` when repeated read is detected
- `render_advisory_output(call, rr_decision.message)` → `kind="signal"` → MODEL visible
- Plain-text output: model can use or ignore
- `FeedbackVisibility.MODEL` / `FeedbackChannel.FUNCTION_CALL_OUTPUT` (implicit)

**Advisory scope:** Stateless per request (v1). `RepeatedReadState` seeded from input history at request start.

**Telemetry:** `repeated_read_state.warned_paths` updated; metadata dict with paths/action/scope is embedded in `CompletedToolCallDecision.signal_metadata`. How this reaches telemetry depends on call site.

**Budget enforcement:** None at the proxy level. Native tool budgets are Codex-side.

**Pass-through case:** When no repeated-read signal fires, `kind="public"` with the call passed through as-is.

**Known gaps:**
- No proxy-level advisory for patterns like excessive native tool calls in a turn (→ #61).
- No advisory for repeated writes to the same path (only reads are monitored).
- `signal_metadata` embedding in `CompletedToolCallDecision` is not consistently surfaced to telemetry.
- Repeated-read v2 (persistent per-session state) is deliberately not implemented; needs new scoped issue.

---

### 3.5 Unknown tool

**Trigger:** `name` not in any of the above categories.

**Coercion/refusal:** Hard. `synthesize_tool_error_result` → `kind="error"`.

**Model-visible output:** `{"ok": false, "error": "Tool 'X' is not recognised by the proxy..."}`.

**Telemetry:** None dedicated. The error is injected into the stream; the normal telemetry around tool lifecycle captures the error at the call site.

**Gap:** No proactive unknown-tool notification at request start.

---

## 4. Telemetry coverage summary

| Event | Where emitted | Operator-visible | Model-visible |
|---|---|---|---|
| `tool_call_started` | `WebSearchRuntime._emit()` | ✅ | ❌ |
| `web_search_route` | `WebSearchRuntime._emit()` | ✅ | ❌ |
| `tool_call_completed` | `WebSearchRuntime._emit()` | ✅ | ❌ |
| Budget-exceeded refusal | Injected as error result | ❌ | ✅ (hard error) |
| Coercion success (apply_patch) | — | ❌ (gap) | ❌ (silent) |
| Coercion failure | Injected as error result | ❌ | ✅ (hard error) |
| Repeated-read advisory | Injected as advisory result | via `signal_metadata` (partial) | ✅ (soft advisory) |
| Dropped-tool refusal | Injected as error result | ❌ | ✅ (hard error) |
| Unknown-tool refusal | Injected as error result | ❌ | ✅ (hard error) |

**Gap pattern:** Operator telemetry events are missing for budget-exceeded, dropped-tool, and coercion-success paths. These are all visible to the model but invisible in `qz-thoughts` / `telemetry/recent`.

---

## 5. Budget defaults

| Limit | Default | Config location |
|---|---|---|
| search calls per turn | 4 | `WEB_SEARCH_MAX_SEARCHES` in `qz_tool_web.py` |
| page opens per turn | 3 | `WEB_SEARCH_MAX_OPENS` in `qz_tool_web.py` |
| search results per query | 8 | `WEB_SEARCH_MAX_RESULTS` in `qz_tool_web.py` |
| continuation hops | 6 | `WEB_SEARCH_TOOL_ADAPTER.lifecycle.continuation_hops` |

All limits are hard-coded constants. No per-profile or per-user budget override is currently possible. `search.json` has `routing.max_searches_per_turn` and `routing.max_page_opens_per_turn` keys defined but they are not yet read by the proxy (→ #60).

---

## 6. Improvement handoff

| Issue | Scope | Trigger condition |
|---|---|---|
| **#60** | web_search budget signal → OPERATOR telemetry; source quality scoring; search.json routing fields wired | After this audit |
| **#61** | Operator/model advisory for native exec patterns (excessive calls, write-loop detection) | After this audit |
| **#62** | apply_patch coercion telemetry; advisory on borderline coercion; edge-case test coverage | After this audit |

---

## 7. BrainCase status

BrainCase tools (`braincase.render`, `braincase.recall`, `braincase.write_candidate`) are registered in `make_proxy_local_tool_registry()` when `db` is provided and the feature flag is enabled. They follow the proxy-local path (§3.2) and are outside the scope of this tool-policy audit.

**BrainCase work is paused.** No new BrainCase features, memory integration, session identity, or persistent repeated-read v2 will be added until the #60–#62 chain is complete.

---

## 8. Non-gaps (working as intended)

- Hard refusals for dropped, unknown, and budget-exceeded calls are intentional.
- Coercion errors are MODEL-visible by design (model needs to know to retry differently).
- Repeated-read advisory is MODEL-visible and soft by design (advisory, not blocking).
- Codex-native tools are not coerced; Codex handles them with its own sandbox.
- `render_advisory_output` vs `render_coercion_error` distinction is correct: advisory = plain text, error = `{"ok": false}` JSON.

---

## Related files

- `proxy/qz_tools.py` — `ToolLifecycleSpec`, `ToolCoercionResult`, `ToolRegistry`, `CODEX_NATIVE_TOOL_NAMES`
- `proxy/qz_proxy_tools.py` — `ProxyLocalToolRegistry`, `completed_call_decision()`
- `proxy/qz_feedback.py` — `FeedbackVisibility`, `FeedbackChannel`, `render_advisory_output`, `render_coercion_error`
- `proxy/qz_tool_web.py` — `WebSearchToolAdapter`, `WebSearchRuntime`, budget constants
- `proxy/qz_tool_apply_patch.py` — multi-layer apply_patch coercion
- `proxy/qz_file_signal.py` — `RepeatedReadState`, `repeated_read_signal`
- `proxy/qz_tool_lifecycle.py` — `CompletedToolCallDecision`, `ToolContinuationResult`
