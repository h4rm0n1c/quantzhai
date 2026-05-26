# Tool Coercion and Advisory Policy Audit

Date: 2026-05-20 (original) / 2026-05-26 (refresh)
Status: #59 Slice A-audit complete at 38eaa94. Refresh after #60/#63/#64/#67–#70/#72 closures.
        #59 is now a completed umbrella audit. Remaining implementation work lives in #61 and #62.

---

## 0. Refresh summary (2026-05-26)

The following gaps from the original Slice A-audit are now resolved:

| Original gap | Resolved by | Evidence |
|---|---|---|
| No operator telemetry for budget-exceeded | #60/#64 | `web_search_budget_exceeded`, `web_search_retrieve_budget_exceeded` emitted via `WebSearchRuntime._emit()` with `budget_mode` field |
| `search.json` routing fields not yet read by proxy | #60/#64 | Router reads `budget_modes`, `default_budget_mode`, `absolute_max_*`, flat compat fields at startup |
| Budget defaults hard-coded, no per-profile override | #64 | Named modes quick/normal/deep/audit; operator-configurable via `search.json`; absolute caps |
| No coercion-success telemetry (apply_patch) | Post-#59 work | `coercion_succeeded`/`coercion_failed` emitted in both `qz_responses_stream.py` and `qz_request_router.py` with `source=tool_adapter` |
| Source quality signals, profile feedback loop | #60 | Source quality annotations, capabilities introspection (`action=capabilities`, `GET /qz/web-search/capabilities`); model told to choose explicitly |
| Codex-native tool list stale at 4 tools | #67/#68 | 12 tools now; `computer` explicitly excluded; source-backed at Codex SHA 46f30d0 |
| `web_search` missing retrieve action | #63 | `action="retrieve"` implemented; budget, cache, telemetry, no localhost leak |
| `web_search` budget-exceeded only hard error (no advisory) | #64 | `web_search_budget_exceeded` telemetry event now emitted; hard error to model retained by design |

Remaining open work (not in #59 scope, tracked separately):

| Gap | Issue | Status |
|---|---|---|
| Advisory signals for native exec patterns (write loops, excessive calls) | #61 | OPEN |
| apply_patch borderline coercion advisory + edge-case test coverage | #62 | OPEN |
| No dedicated operator telemetry for dropped-tool refusal | (untracked) | Still no `dropped_tool_refused` event |
| No dedicated operator telemetry for unknown-tool refusal | (untracked) | Still no `unknown_tool_refused` event |
| Repeated-read v2 (persistent per-session state) | (deferred) | Explicitly deferred; needs scoped issue when BrainCase work resumes |

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

**Remaining gap:** No dedicated `dropped_tool_refused` operator telemetry event — hard to diagnose in qz-thoughts. No pre-announcement before first tool attempt.

---

### 3.2 Proxy-local tools (`web_search`, BrainCase tools when enabled)

**Owner:** `proxy/qz_proxy_tools.py` → `WebSearchProxyToolExecutor` (or equivalent BrainCase executor).

**Execution mode:** `ToolLifecycleSpec.execution = "proxy_local"`. The proxy executes the tool locally; a continuation hop follows.

**Coercion:**
- `WebSearchToolAdapter.coerce()` in `proxy/qz_tool_web.py`: validates `action`/`query`/`url`; fixes minor argument issues.
- If coercion fails: `synthesize_tool_error_result` → `kind="error"` (MODEL visible, hard).
- If coercion succeeds: re-runs with corrected arguments.

**Budget enforcement (web_search) — updated for #64:**

Named budget modes replace the original hard-coded constants. Default mode is `normal`.

| Mode | `max_results` | `max_searches` | `max_opens` | `max_retrievals` | `max_retrieved_chars` |
|---|---|---|---|---|---|
| `quick` | 8 | 4 | 3 | 2 | 6 000 |
| `normal` (default) | 12 | 8 | 8 | 4 | 12 000 |
| `deep` | 25 | 20 | 20 | 10 | 30 000 |
| `audit` | 50 | 40 | 40 | 20 | 60 000 |

Built-in absolute caps: 100 results, 100 searches, 100 opens, 50 retrievals, 120 000 chars.
Operator may lower (not raise) via `routing.absolute_max_*` in `search.json`.
`budget_mode` is a per-call argument; mode resolution uses `_resolve_budget_mode()` on each call.

Continuation hops: 6 (`WEB_SEARCH_TOOL_ADAPTER.lifecycle.continuation_hops`); unchanged.

**Budget refusal format:** `{"ok": false, "error": "Refusing search: reached per-turn limit..."}` injected
as `function_call_output` — MODEL visible, hard refusal.

**Budget-exceeded telemetry (resolved):** `web_search_budget_exceeded` and
`web_search_retrieve_budget_exceeded` emitted via `WebSearchRuntime._emit()`. Both include
`budget_mode`, `limit`, `counter`, and `action` fields. Also visible in qz-thoughts.

**Telemetry:** `tool_call_started`, `web_search_route`, `tool_call_completed` emitted.
`budget_mode` field present in `tool_call_started` and `tool_call_completed`.
`web_search_capabilities_requested` emitted on `action=capabilities`.
`repeated_read_signal` emitted as telemetry event when repeated-read advisory fires.

**Repeat-guard:** Signatures tracked in `seen_signatures`; repeated searches/opens refused with a hard error.

**Profile selection:** Model instructed to use `action=capabilities` first to discover live profiles and
budget modes. `auto_keywords`/`auto_precedence` config keys remain for backward compat but are not
advertised to the model; explicit selection is the recommended path.

**Model-visible lifecycle:** public `web_search_call` item emitted with `status="in_progress"` then
`status="completed"`. Failures show `status="failed"`.

**Retrieve action (resolved):** `action="retrieve"` calls Agent API `/retrieve` server-side. Normalizes
mediawiki/FSE and character-card response shapes. Cache 15 min TTL. Budget:
`max_retrievals_per_turn` and `max_retrieved_chars` per mode. No localhost endpoint in any
model-visible output.

**Remaining gaps:**
- Budget-exceeded refusal is still a hard error to the model (no soft advisory to help model plan within limits). Design choice; acceptable for now.
- Profile `auto_keywords` routing remains in code for backward compat but is not advertised.

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

**Coercion telemetry (resolved):** `coercion_succeeded` or `coercion_failed` emitted in both
`qz_responses_stream.py` and `qz_request_router.py` when `decision.coercion_applied` is true.
Payload includes `tool`, `call_id`, `correction_applied`, `error_summary`, `source=tool_adapter`,
and an `apply_patch` field with argument inspection metadata.

**Remaining gaps (→ #62):**
- No advisory to model when borderline coercion occurred (e.g. envelope unwrapping succeeded but input was malformed).
- Edge cases in `_extract_op_and_path_from_patch_envelope` have limited test coverage.
- The multi-layer fallback chain has 4+ paths; coercion path is still hard to audit visually.

---

### 3.4 Codex-native tools (12 tools — updated from original 4)

**Defined in:** `CODEX_NATIVE_TOOL_NAMES` frozenset in `proxy/qz_tools.py`.

**Current set (source-backed at Codex SHA 46f30d0, #67/#68):**

| Tool | Codex handler |
|---|---|
| `exec_command` | ExecCommandHandler |
| `write_stdin` | WriteStdinHandler |
| `shell_command` | ShellCommandHandler |
| `update_plan` | handlers/plan.rs |
| `request_user_input` | handlers/request_user_input.rs |
| `request_permissions` | handlers/request_permissions.rs |
| `view_image` | handlers/view_image.rs |
| `get_goal` | handlers/goal/get_goal.rs |
| `create_goal` | handlers/goal/create_goal.rs |
| `update_goal` | handlers/goal/update_goal.rs |
| `shell` | ShellHandler (fallback) |
| `container.exec` | ContainerExecHandler (fallback) |

**`computer` is NOT in this set.** It is a reserved validation namespace only; no ToolHandler
in Codex source at audited SHA. An exact-set guard test enforces this.

**Execution mode:** Pass-through. The proxy does not execute these; Codex handles them in its sandbox/harness.

**Coercion:** None. Native tool calls are never coerced by the proxy.

**Advisory signals (repeated-read):**
- `repeated_read_signal(call, repeated_read_state)` from `proxy/qz_file_signal.py`
- Triggers when a file path was already read in this session (input history or current run)
- Returns `should_signal=True` when repeated read is detected
- `render_advisory_output(call, rr_decision.message)` → `kind="signal"` → MODEL visible
- Plain-text output: model can use or ignore
- `FeedbackVisibility.MODEL` / `FeedbackChannel.FUNCTION_CALL_OUTPUT` (implicit)
- `repeated_read_signal` telemetry event emitted with `signal_metadata` payload

**Advisory scope:** Stateless per request (v1). `RepeatedReadState` seeded from input history at request start.

**Budget enforcement:** None at the proxy level. Native tool budgets are Codex-side.

**Pass-through case:** When no repeated-read signal fires, `kind="public"` with the call passed through as-is.

**Remaining gaps (→ #61):**
- No proxy-level advisory for patterns like excessive native tool calls in a turn.
- No advisory for repeated writes to the same path (only reads are monitored).
- Repeated-read v2 (persistent per-session state) is explicitly deferred; needs scoped issue.

---

### 3.5 Unknown tool

**Trigger:** `name` not in any of the above categories.

**Coercion/refusal:** Hard. `synthesize_tool_error_result` → `kind="error"`.

**Model-visible output:** `{"ok": false, "error": "Tool 'X' is not recognised by the proxy..."}`.

**Telemetry:** None dedicated. The error is injected into the stream; normal telemetry around tool
lifecycle captures the error at the call site.

**Remaining gap:** No dedicated `unknown_tool_refused` operator telemetry event. No proactive
unknown-tool notification at request start.

---

## 4. Telemetry coverage summary

| Event | Where emitted | Operator-visible | Model-visible |
|---|---|---|---|
| `tool_call_started` | `WebSearchRuntime._emit()` | ✅ (incl. `budget_mode`) | ❌ |
| `web_search_route` | `WebSearchRuntime._emit()` | ✅ | ❌ |
| `tool_call_completed` | `WebSearchRuntime._emit()` | ✅ (incl. `budget_mode`) | ❌ |
| `web_search_budget_exceeded` | `WebSearchRuntime._emit()` | ✅ (incl. `budget_mode`) | ✅ (hard error) |
| `web_search_retrieve_budget_exceeded` | `WebSearchRuntime._emit()` | ✅ (incl. `budget_mode`) | ✅ (hard error) |
| `web_search_capabilities_requested` | `WebSearchRuntime._emit()` | ✅ | ❌ |
| `coercion_succeeded` | router + stream (both paths) | ✅ (`source=tool_adapter`) | ❌ |
| `coercion_failed` | router + stream (both paths) | ✅ (`source=tool_adapter`) | ✅ (hard error) |
| `repeated_read_signal` | router + stream (both paths) | ✅ (signal_metadata) | ✅ (soft advisory) |
| Dropped-tool refusal | Injected as error result | ❌ (no event) | ✅ (hard error) |
| Unknown-tool refusal | Injected as error result | ❌ (no event) | ✅ (hard error) |

**Remaining operator-telemetry gaps:** No dedicated events for dropped-tool refusal or
unknown-tool refusal. These are model-visible but invisible as distinct events in
qz-thoughts / telemetry/recent.

---

## 5. Budget defaults (current — post #64)

Named budget modes replace the original hard-coded constants. Mode is a per-call argument.

| Mode | `max_results` | `max_searches` | `max_opens` | `max_retrievals` | `max_retrieved_chars` |
|---|---|---|---|---|---|
| `quick` | 8 | 4 | 3 | 2 | 6 000 |
| `normal` (default) | 12 | 8 | 8 | 4 | 12 000 |
| `deep` | 25 | 20 | 20 | 10 | 30 000 |
| `audit` | 50 | 40 | 40 | 20 | 60 000 |

Built-in absolute constants (code ceiling): 100/100/100/50/120 000.
Operator lowers via `routing.absolute_max_*` in `search.json`.
Continuation hops: 6 (unchanged from original).

Config wiring: `search.json` `routing.budget_modes`, `routing.default_budget_mode`,
`routing.absolute_max_*`, and flat `#60` compat fields are all read by the router at startup
and passed to `WebSearchRuntime`.

---

## 6. Improvement handoff (updated)

| Issue | Scope | Status |
|---|---|---|
| **#60** | web_search budget → OPERATOR telemetry; source quality; search.json wired | ✅ CLOSED |
| **#63** | web_search `action="retrieve"` | ✅ CLOSED |
| **#64** | Named budget modes quick/normal/deep/audit; absolute caps; per-call resolution | ✅ CLOSED |
| **#67–#70** | Codex tool contract audit; CODEX_NATIVE_TOOL_NAMES expanded to 12 | ✅ CLOSED |
| **#61** | Proxy advisory for native exec patterns (excessive calls, write-loop detection) | OPEN |
| **#62** | apply_patch coercion advisory on borderline inputs; edge-case tests | OPEN |

Dropped-tool and unknown-tool operator telemetry events remain untracked as a separate issue.
If these become a qz-thoughts diagnostic priority, open a focused issue.

---

## 7. BrainCase status

BrainCase tools (`braincase.render`, `braincase.recall`, `braincase.write_candidate`) are registered in `make_proxy_local_tool_registry()` when `db` is provided and the feature flag is enabled. They follow the proxy-local path (§3.2) and are outside the scope of this tool-policy audit.

**BrainCase work is paused.** No new BrainCase features, memory integration, session identity, or persistent repeated-read v2 will be added until #61 and #62 are resolved.

---

## 8. Non-gaps (working as intended)

- Hard refusals for dropped, unknown, and budget-exceeded calls are intentional.
- Coercion errors are MODEL-visible by design (model needs to know to retry differently).
- Repeated-read advisory is MODEL-visible and soft by design (advisory, not blocking).
- Codex-native tools are not coerced; Codex handles them with its own sandbox.
- `render_advisory_output` vs `render_coercion_error` distinction is correct: advisory = plain text, error = `{"ok": false}` JSON.
- Budget-exceeded is a hard error to the model (not a soft advisory); this is intentional — hard refusal prevents runaway loops.
- Profile `auto_keywords` routing remains in code for backward compat but is not advertised to the model. Explicit selection via `action=capabilities` is the recommended path.

---

## Related files

- `proxy/qz_tools.py` — `ToolLifecycleSpec`, `ToolCoercionResult`, `ToolRegistry`, `CODEX_NATIVE_TOOL_NAMES` (12 tools)
- `proxy/qz_proxy_tools.py` — `ProxyLocalToolRegistry`, `completed_call_decision()`
- `proxy/qz_feedback.py` — `FeedbackVisibility`, `FeedbackChannel`, `render_advisory_output`, `render_coercion_error`
- `proxy/qz_tool_web.py` — `WebSearchToolAdapter`, `WebSearchRuntime`, `_resolve_budget_mode`, `build_web_search_capabilities`, budget mode constants
- `proxy/qz_tool_apply_patch.py` — multi-layer apply_patch coercion
- `proxy/qz_file_signal.py` — `RepeatedReadState`, `repeated_read_signal`
- `proxy/qz_tool_lifecycle.py` — `CompletedToolCallDecision`, `ToolContinuationResult`
- `docs/codex-source-tool-inventory.md` — full Codex tool/item classification at SHA 46f30d0
- `docs/search-config-contract.md §64` — budget mode design and acceptance tests
