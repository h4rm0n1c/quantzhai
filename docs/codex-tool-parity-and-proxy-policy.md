# Codex Tool Parity and QuantZhai Proxy Policy

## 1. Purpose

This document records the complete tool parity matrix between current Codex source
(at a recorded SHA) and QuantZhai proxy policy for every Codex-visible tool/item family.

It supersedes the narrower `docs/codex-source-tool-inventory.md` as the single source
of truth for tool-by-tool parity. It is the governing document for issue #74.

## 2. Authorities

The actual authorities for tool parity decisions, in priority order:

1. **Codex source** at the recorded audit SHA — handler files, item types, dispatch paths.
2. **Tool schemas actually exposed to the model** — what Codex's `spec()` returns.
3. **QuantZhai routing/runtime behaviour** — what `qz_tools.py`, `qz_proxy_tools.py`,
   `qz_responses_stream.py`, `qz_request_router.py` actually do at runtime.
4. **Runtime captures or fixtures** — evidence from `var/captures/` or test fixtures.
5. **Tests** that lock the intended behaviour.

## 3. Non-authorities

The following are NOT authorities for tool parity decisions:

- `CODEX_NATIVE_TOOL_NAMES` — it is a **routing inventory only**, not an authority and
  not an immutability boundary. See Section 5.
- Any stale audit note that does not reflect current Codex source at the recorded SHA.
- Assumptions about what "native" means (it means "Codex executes this"; it does NOT mean
  "QuantZhai may never touch this").
- Comments or route tables outside the Codex source tree.

## 4. Proxy Policy

`CODEX_NATIVE_TOOL_NAMES` is a routing inventory only.

It is not an authority.
It is not an immutability boundary.
It does not mean QuantZhai may never add in-transit handling.

QuantZhai may observe, advise, normalise, convert, or proxy-handle tool traffic when
justified by Codex source, runtime captures/fixtures, and tests.

The hard rule is not "never touch native tools".
The hard rule is: **do not invent Codex contracts, do not fake lifecycle events,
and do not silently break Codex execution semantics.**

### 4.1 Valid in-transit handling

When justified, QuantZhai may add any of the following for any tool listed in
`CODEX_NATIVE_TOOL_NAMES`:

- **Observation** — count calls, hash args, track patterns (e.g. `qz_native_signal.py`)
- **Telemetry** — emit operator-facing events (e.g. `tool_escalation_requested`)
- **Advisory signals** — inject advisory `function_call_output` to guide model behaviour
  (e.g. repeated-read signal, native tool advisories)
- **Argument normalisation** — safely normalise argument shapes (e.g. `apply_patch` coercion)
- **Deliberate conversion** — change item type from `function_call` to another Codex-parseable
  type when Codex source supports it (e.g. `apply_patch` → `custom_tool_call`)
- **Proxy-local handling** — replace Codex-native execution with proxy-side execution
  (e.g. `web_search`)

### 4.2 What justifies a change

Before adding in-transit handling for any tool, the following evidence must exist:

1. Codex source confirms the tool handler, item type, and expected argument/output shape.
2. A test or capture confirms the current behaviour and the desired new behaviour.
3. The change does not silently break Codex execution semantics — if Codex expects a
   `function_call` item, the proxy must not silently drop or corrupt it without replacing
   with a valid Codex-parseable item.

### 4.3 Hard constraints (must never do)

- Do not invent Codex lifecycle SSE events that Codex does not parse.
- Do not emit `response.custom_tool_call_input.done` (Codex only parses `.delta`).
- Do not leak raw commands, paths, full args dumps, stdin text, patch text, diffs,
  full env maps, API keys, tokens, passwords, or secret-like values into telemetry.
- Bounded permission reason previews (≤200 chars) ARE allowed in permission-related
  telemetry (`request_permissions_requested`, `tool_escalation_requested`). The
  preview must be truncated and must not include full raw request dumps.
- Do not remove pass-through behaviour without source/runtime evidence.

## 5. Audit SHA

Codex source audited at:

```
46f30d02828bd4c52827e5f0482a6f2a982cce5b
```

Local checkout: `/tmp/qz-audit/codex`

If a different SHA appears in a future check, the source evidence in this document
may be stale. Re-run the audit before expanding parity decisions.

## 6. Parity Matrix

### 6.1 exec_command

| Field | Value |
|---|---|
| Tool / item name | `exec_command` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` via `create_exec_command_tool_with_environment_id` |
| Advertised argument shape/schema | `ExecCommandArgs`: `cmd: String`, `workdir: Option<String>`, `shell: Option<String>`, `login: Option<bool>`, `tty: bool`, `yield_time_ms: u64`, `max_output_tokens: Option<usize>`, `sandbox_permissions: SandboxPermissions`, `additional_permissions: Option<AdditionalPermissionProfile>`, `justification: Option<String>` |
| Result/output shape | `ExecCommandToolOutput` — wrapped sandbox stdout/stderr with exit code |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` (path #4 in `completed_call_decision`) |
| Current QuantZhai transformations | None (pass-through). `exec_command` description is amended with `FILE_EDIT_TOOL_HINT` at declaration time (`qz_tool_request.py:108-114`). Advisory signals may fire via `check_native_advisories()` (`qz_native_signal.py`). |
| Current telemetry | `tool_escalation_requested` when `sandbox_permissions == "require_escalated"` (`qz_responses_stream.py:797-824`). `native_tool_advisory` for excessive/repeated calls. `tool_sandbox_denied` / `tool_connection_failed` via `qz_native_tool_output.py` on incoming results. `tool_call_started`/`tool_call_completed`/`tool_call_failed` request lifecycle events. |
| Current tests | `NativeToolNamesMembershipTests.test_exec_command_present` (test_qz_tools.py). `NativeToolListContractTests.test_exec_command_in_native_tool_names` (test_qz_proxy_tools.py). `DroppedToolFeedbackTests.test_codex_native_tool_passes_through` (test_qz_proxy_tools.py). `NativeToolAdvisoryIntegrationTests` (test_qz_proxy_tools.py). |
| Runtime/capture evidence | Captures available at `var/captures/requests/` showing `exec_command` calls passing through as `function_call` items. |
| Known failure modes | None specific. If Coercion path intercepted erroneously, model would receive unsupported-tool error. |
| Current policy decision | **pass-through** with advisory observation and telemetry |
| Rationale | Codex source proves `ExecCommandHandler` receives `ToolPayload::Function`, executes via unified exec, returns `ExecCommandToolOutput`. QuantZhai correctly routes as pass-through. Advisory signals do not change execution semantics. |
| Follow-up issue required | No |

### 6.2 write_stdin

| Field | Value |
|---|---|
| Tool / item name | `write_stdin` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` via `create_write_stdin_tool()` |
| Advertised argument shape/schema | `WriteStdinArgs`: `session_id: i32` (required), `chars: String`, `yield_time_ms: u64`, `max_output_tokens: Option<usize>` |
| Result/output shape | `ExecCommandToolOutput` — wrapped sandbox stdout/stderr |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES`. Dropped from tool list at declaration time when no live exec session exists (`qz_tool_request.py:105-107`). |
| Current QuantZhai transformations | Description amended with `WRITE_STDIN_TOOL_HINT` at declaration time. Dropped when no live `session_id` in request input history. |
| Current telemetry | Same as `exec_command`. `tool_escalation_requested` does not apply (no `sandbox_permissions` field). |
| Current tests | `NativeToolNamesMembershipTests.test_write_stdin_present`. `NativeToolListContractTests.test_write_stdin_in_native_tool_names`. |
| Runtime/capture evidence | Captures show `write_stdin` in tool declarations. No live `write_stdin` calls observed in recent captures. |
| Known failure modes | Stuck interactive process loop (Pattern D in #61 design). Current drop mechanism is sufficient for no-session cases. |
| Current policy decision | **pass-through** with advisory observation (Pattern B2: repeated same args) |
| Rationale | Codex source proves `WriteStdinHandler` handles `ToolPayload::Function`. QuantZhai pass-through is correct. Hold on Pattern D write_stdin loop advisory until live evidence available. |
| Follow-up issue required | No for Phase A. Pattern D (write_stdin loop) deferred to #61 Slice C.3. |

### 6.3 shell_command

| Field | Value |
|---|---|
| Tool / item name | `shell_command` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/shell/shell_command.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function`. This is the primary shell tool in QuantZhai's `shell_command` config. |
| Advertised argument shape/schema | `ShellCommandToolCallParams`: `command: String`, `workdir: Option<String>`, `login: Option<bool>`, `timeout_ms: Option<u64>`, `sandbox_permissions: Option<SandboxPermissions>`, `prefix_rule: Option<Vec<String>>`, `additional_permissions: Option<AdditionalPermissionProfile>`, `justification: Option<String>` |
| Result/output shape | Standard function_call output with wrapped sandbox stdout/stderr |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None (pass-through) |
| Current telemetry | Same as `exec_command` |
| Current tests | `NativeToolNamesMembershipTests.test_shell_command_present`. `NativeToolListContractTests.test_shell_command_in_native_tool_names`. |
| Runtime/capture evidence | Captures show `shell_command` calls passing through. |
| Known failure modes | None specific |
| Current policy decision | **pass-through** with advisory observation |
| Rationale | Codex source proves `ShellCommandHandler` handles `ToolPayload::Function`. |
| Follow-up issue required | No |

### 6.4 update_plan

| Field | Value |
|---|---|
| Tool / item name | `update_plan` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/plan.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` |
| Advertised argument shape/schema | `UpdatePlanArgs`: `explanation: Option<String>`, `plan: Vec<PlanItemArg>` where `PlanItemArg = { step: String, status: StepStatus }` |
| Result/output shape | Standard function_call output |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_update_plan_present`. `NativeToolListContractTests.test_update_plan_in_native_tool_names`. |
| Runtime/capture evidence | Captures show `update_plan` in tool declarations. |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves `PlanHandler` handles `ToolPayload::Function`. No exec/write risk; advisory adds no value. |
| Follow-up issue required | No |

### 6.5 request_user_input

| Field | Value |
|---|---|
| Tool / item name | `request_user_input` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/request_user_input.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` |
| Advertised argument shape/schema | `RequestUserInputArgs`: `questions: Vec<RequestUserInputQuestion>`. Each question has: `id: String`, `header: String`, `question: String`, `isOther: bool`, `isSecret: bool`, `options: Option<Vec<Option>>`. |
| Result/output shape | `RequestUserInputResponse`: `answers: HashMap<String, RequestUserInputAnswer>` |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_request_user_input_present`. `NativeToolListContractTests.test_request_user_input_in_native_tool_names`. |
| Runtime/capture evidence | Captures show `request_user_input` in tool declarations. |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves handler. Pauses for user input — no loop/exec concern. |
| Follow-up issue required | No |

### 6.6 request_permissions

| Field | Value |
|---|---|
| Tool / item name | `request_permissions` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/request_permissions.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` |
| Advertised argument shape/schema | `RequestPermissionsArgs`: `reason: Option<String>`, `permissions: RequestPermissionProfile { network: Option<NetworkPermissions>, file_system: Option<FileSystemPermissions> }` |
| Result/output shape | `RequestPermissionsResponse`: `permissions: RequestPermissionProfile`, `scope: PermissionGrantScope`, `strict_auto_review: bool` |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | Description amended with `REQUEST_PERMISSIONS_TOOL_HINT` at declaration time (`qz_tool_request.py`). Telemetry-only observation: `request_permissions_requested` event with bounded fields (`reason_preview` ≤200 chars, `reason_len`, `permission_profile`, `call_id`). |
| Current telemetry | `request_permissions_requested` — emitted on outgoing `request_permissions` calls with `tool`, `call_id`, `reason_preview` (≤200 chars), `reason_len`, `permission_profile` summary. No full raw args. Standard request lifecycle events also apply. |
| Current tests | `NativeToolNamesMembershipTests.test_request_permissions_present`. `NativeToolListContractTests.test_request_permissions_in_native_tool_names`. `PermissionToolHintTests` (test_qz_tool_request.py). `RequestPermissionsTelemetryTests` (test_qz_responses_stream.py). |
| Runtime/capture evidence | Captures show `request_permissions` in tool declarations. No live `request_permissions` calls observed in recent captures. |
| Known failure modes | QuantZhai CANNOT see whether permission was granted or denied — it only sees the result as a `function_call_output` in the next request's input. The proxy can observe escalation patterns via `sandbox_permissions: "require_escalated"` on command tools (`tool_escalation_requested`). Bounded telemetry provides call observation without breaking pass-through. See Section 7. |
| Current policy decision | **pass-through** with bounded telemetry and model-facing affordance guidance (tool hint) |
| Rationale | Codex source proves `RequestPermissionsHandler` expects `ToolPayload::Function` and calls `session.request_permissions()`. QuantZhai cannot intercept the grant/deny flow without breaking the Codex permissions protocol. Bounded telemetry (Phase B.1, issue #74) adds observation without breaking semantics. |
| Follow-up issue required | Yes — see Section 7 for remaining Phase B items. |

### 6.7 view_image

| Field | Value |
|---|---|
| Tool / item name | `view_image` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/view_image.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `spec()` returns `ToolSpec::Function` |
| Advertised argument shape/schema | `ViewImageArgs`: `path: String`, `detail: Option<String>` |
| Result/output shape | `ViewImageOutput` — base64 image data for display |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_view_image_present`. `NativeToolListContractTests.test_view_image_in_native_tool_names`. |
| Runtime/capture evidence | Not observed in recent captures (QuantZhai uses local model — image tools rarely called). |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves handler. No exec/write risk. |
| Follow-up issue required | No |

### 6.8 get_goal

| Field | Value |
|---|---|
| Tool / item name | `get_goal` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/goal/get_goal.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `GET_GOAL_TOOL_NAME = "get_goal"` |
| Advertised argument shape/schema | No arguments |
| Result/output shape | `GoalToolResponse` |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_get_goal_present`. `NativeToolListContractTests.test_get_goal_in_native_tool_names`. |
| Runtime/capture evidence | Not observed in recent captures. |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves handler. Goal management — no exec/write risk. |
| Follow-up issue required | No |

### 6.9 create_goal

| Field | Value |
|---|---|
| Tool / item name | `create_goal` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/goal/create_goal.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `CREATE_GOAL_TOOL_NAME = "create_goal"` |
| Advertised argument shape/schema | `CreateGoalArgs`: `objective: String`, `token_budget: Option<i64>` |
| Result/output shape | `GoalToolResponse` |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_create_goal_present`. `NativeToolListContractTests.test_create_goal_in_native_tool_names`. |
| Runtime/capture evidence | Not observed in recent captures. |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves handler. Goal management — no exec/write risk. |
| Follow-up issue required | No |

### 6.10 update_goal

| Field | Value |
|---|---|
| Tool / item name | `update_goal` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/goal/update_goal.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | Yes — `UPDATE_GOAL_TOOL_NAME = "update_goal"` |
| Advertised argument shape/schema | `UpdateGoalArgs`: `status: ThreadGoalStatus` |
| Result/output shape | `GoalToolResponse` |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None |
| Current telemetry | Standard request lifecycle events only |
| Current tests | `NativeToolNamesMembershipTests.test_update_goal_present`. `NativeToolListContractTests.test_update_goal_in_native_tool_names`. |
| Runtime/capture evidence | Not observed in recent captures. |
| Known failure modes | None |
| Current policy decision | **pass-through** |
| Rationale | Codex source proves handler. Goal management — no exec/write risk. |
| Follow-up issue required | No |

### 6.11 shell

| Field | Value |
|---|---|
| Tool / item name | `shell` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/shell/shell_handler.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | No — `ShellHandler::default()` returns `None` for `spec()`. Not advertised in QuantZhai's `shell_command` config. |
| Advertised argument shape/schema | `ShellToolCallParams`: `command: Vec<String>`, `workdir: Option<String>`, `timeout_ms: Option<u64>`, `sandbox_permissions: Option<SandboxPermissions>`, `prefix_rule: Option<Vec<String>>`, `additional_permissions: Option<AdditionalPermissionProfile>`, `justification: Option<String>` |
| Result/output shape | Standard function_call output |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` — registered as fallback handler |
| Current QuantZhai transformations | None |
| Current telemetry | Same as `exec_command` if called |
| Current tests | `NativeToolNamesMembershipTests.test_shell_present`. `NativeToolListContractTests.test_shell_in_native_tool_names`. `DroppedToolFeedbackTests.test_shell_passes_through_as_public`. |
| Runtime/capture evidence | Not observed in captures (not advertised). |
| Known failure modes | If called via function_call, Codex executes via `run_exec_like`. Payload is `Vec<String>` not bare `String` — Qwen model may not know this shape. If model sends string-form command, Codex may fail parse and return error. |
| Current policy decision | **pass-through** (fallback). Advisory observation same as exec_command/shell_command. |
| Rationale | Codex source proves `ShellHandler` handles `ToolPayload::Function`. Always registered fallback. Model should not normally call it in QuantZhai config. |
| Follow-up issue required | No |

### 6.12 container.exec

| Field | Value |
|---|---|
| Tool / item name | `container.exec` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/shell/container_exec.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | `function_call` |
| Advertised to model | No — `spec()` returns `None`. Never advertised. |
| Advertised argument shape/schema | `ShellToolCallParams` (same as `shell`) |
| Result/output shape | Standard function_call output |
| QuantZhai route | Native pass-through via `CODEX_NATIVE_TOOL_NAMES` — always registered as fallback |
| Current QuantZhai transformations | None |
| Current telemetry | Same as `exec_command` if called |
| Current tests | `NativeToolNamesMembershipTests.test_container_exec_present`. `DroppedToolFeedbackTests.test_container_exec_passes_through_as_public`. |
| Runtime/capture evidence | Not observed in captures (never advertised). |
| Known failure modes | Same as `shell`. |
| Current policy decision | **pass-through** (fallback) |
| Rationale | Codex source proves fallback handler. Model should not normally call it. |
| Follow-up issue required | No |

### 6.13 apply_patch

| Field | Value |
|---|---|
| Tool / item name | `apply_patch` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/apply_patch.rs`, `codex-rs/core/src/tools/handlers/apply_patch_spec.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | **`custom_tool_call`** — NOT `function_call`. `ToolSpec::Freeform` |
| Advertised to model | Yes — as `function` type tool with JSON schema arguments. QuantZhai converts to `custom_tool_call` output. |
| Advertised argument shape/schema | `ToolSpec::Freeform` — Codex tools registration declares it as a `function`-type tool with structured JSON args (operation type, path, diff/content). The handler receives a raw patch body string. |
| Result/output shape | `custom_tool_call` item with `input: "*** Begin Patch..."` body. Codex `apply_patch_spec.rs` routes via `ToolSpec::Freeform`. |
| QuantZhai route | **Protocol adapter** — path #3 in `completed_call_decision`. NOT in `CODEX_NATIVE_TOOL_NAMES`. |
| Current QuantZhai transformations | Coerces JSON `arguments` into `*** Begin Patch` envelope. Rewrites item from `function_call` to `custom_tool_call`. Emits `output_item.added` → `custom_tool_call_input.delta` → `output_item.done`. Issue #73: no `.done` delta marker. |
| Current telemetry | `coercion_succeeded`/`coercion_failed` with `apply_patch` field from `inspect_apply_patch_arguments`. |
| Current tests | `ToolRegistryTests.test_registry_adapts_apply_patch_tool_and_choice`. `ApplyPatchLifecycleContractTests` in test_qz_responses_stream.py. `DroppedToolFeedbackTests` in test_qz_proxy_tools.py (verify `kind="public"` with `custom_tool_call` type). |
| Runtime/capture evidence | Captures show `apply_patch` function_calls arriving from Qwen, converted to custom_tool_call items. |
| Known failure modes | If coercion fails (unparseable arguments), returns error to model. If Qwen emits operation JSON that the adapter cannot parse, the model sees a retryable error. Stale `.done` delta regression was removed in issue #73. |
| Current policy decision | **protocol adapter** (custom_tool_call conversion). NOT a native advisory candidate. |
| Rationale | Codex source proves `apply_patch` is `ToolSpec::Freeform` — raw patch body, not JSON arguments. QuantZhai coercion layer converts from model JSON format to Codex patch envelope. This is NOT `function_call` pass-through. |
| Follow-up issue required | No for Phase A. Write-count advisory deferred to #61. |

### 6.14 web_search

| Field | Value |
|---|---|
| Tool / item name | `web_search` |
| Codex source file(s) | `codex-rs/protocol/src/models.rs` (ResponseItem::WebSearchCall) |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | **`web_search_call`** — NOT `function_call`. `ResponseItem::WebSearchCall` |
| Advertised to model | Yes — as `function` type tool with JSON schema arguments. QuantZhai proxies execution. |
| Advertised argument shape/schema | `function`-type tool with JSON args: `action`, `query`, `url`, `profile`, etc. |
| Result/output shape | `web_search_call` item type. `output_item.added` + `output_item.done` only. No sub-events. |
| QuantZhai route | **Proxy-local** — `WebSearchProxyToolExecutor` in `proxy/qz_proxy_tools.py`. NOT in `CODEX_NATIVE_TOOL_NAMES`. |
| Current QuantZhai transformations | Coercion via `WEB_SEARCH_TOOL_ADAPTER.coerce()`. Local execution via `WebSearchRuntime`. Public item type is `web_search_call`. |
| Current telemetry | `tool_call_started`/`tool_call_completed`/`tool_call_failed`. Telemetry payload includes `sources`, `upstream_items`. |
| Current tests | `ToolRegistryTests.test_registry_adapts_web_search_tool_and_choice`. `ProxyToolRegistryTests` (test_qz_proxy_tools.py). `ParseSSEEventsTests`, `CheckContractTests`, `DeterministicContractTests` in test_qz_web_search_contract_check.py. |
| Runtime/capture evidence | Captures show `web_search` in tool declarations and calls being executed by proxy. |
| Known failure modes | Budget enforcement `web_search_budget_exceeded`. No lifecycle event regression (issue #66). |
| Current policy decision | **proxy-local** |
| Rationale | Codex source proves `web_search_call` is a separate ResponseItem variant, not `function_call`. QuantZhai correctly routes via proxy-local executor. |
| Follow-up issue required | No |

### 6.15 local_shell

| Field | Value |
|---|---|
| Tool / item name | `local_shell` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/shell/local_shell.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | **`local_shell_call`** — NOT `function_call`. `ToolPayload::LocalShell` dispatch, `ToolSpec::LocalShell` spec. |
| Advertised to model | Depends on config (QuantZhai uses `shell_command`, not local_shell) |
| Advertised argument shape/schema | `LocalShellExecAction`: `command: Vec<String>`, `timeout_ms`, `working_directory`, `env`, `user` |
| Result/output shape | `LocalShellCall` ResponseItem |
| QuantZhai route | NOT in `CODEX_NATIVE_TOOL_NAMES`. Returns error if called as `function_call`. No proxy adapter. |
| Current QuantZhai transformations | None |
| Current telemetry | None (not routed) |
| Current tests | `NativeToolNamesMembershipTests.test_local_shell_absent`. `LocalShellToolSearchItemContractTests` (test_qz_proxy_tools.py). |
| Runtime/capture evidence | None |
| Known failure modes | None (never advertised in QuantZhai config) |
| Current policy decision | **documented unsupported path** — returns unsupported-tool error via `completed_call_decision` |
| Rationale | Codex source proves `local_shell` dispatches via `ToolPayload::LocalShell` NOT `ToolPayload::Function`. Separate ResponseItem variant. |
| Follow-up issue required | No for Phase A. Deferred adapter backlog. |

### 6.16 tool_search

| Field | Value |
|---|---|
| Tool / item name | `tool_search` |
| Codex source file(s) | `codex-rs/core/src/tools/handlers/tool_search.rs` |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | **`tool_search_call`** / **`tool_search_output`** — NOT `function_call`. `ToolSpec::ToolSearch` spec. |
| Advertised to model | Gated by config. QuantZhai does not expose it. |
| Advertised argument shape/schema | Unknown / needs follow-up |
| Result/output shape | `ToolSearchCall` + `ToolSearchOutput` ResponseItems |
| QuantZhai route | NOT in `CODEX_NATIVE_TOOL_NAMES`. Returns error if called as `function_call`. No proxy adapter. |
| Current QuantZhai transformations | None |
| Current telemetry | None (not routed) |
| Current tests | `NativeToolNamesMembershipTests.test_tool_search_absent`. `LocalShellToolSearchItemContractTests` (test_qz_proxy_tools.py). |
| Runtime/capture evidence | None |
| Known failure modes | None (not exposed) |
| Current policy decision | **documented unsupported path** |
| Rationale | Codex source proves `ToolSearchCall`/`ToolSearchOutput` are separate ResponseItems. |
| Follow-up issue required | No for Phase A. Deferred. |

### 6.17 image_generation

| Field | Value |
|---|---|
| Tool / item name | `image_generation` |
| Codex source file(s) | `codex-rs/core/src/tools/hosted_spec.rs::create_image_generation_tool()`, `codex-rs/protocol/src/models.rs` (ImageGenerationCall) |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | **`image_generation_call`** — hosted tool, no ToolHandler. `ToolSpec::ImageGeneration` |
| Advertised to model | Gated by `image_gen_tool` + `Feature::ImageGeneration` + model support |
| Advertised argument shape/schema | Unknown / needs follow-up |
| Result/output shape | `ImageGenerationCall` ResponseItem with base64 PNG result |
| QuantZhai route | NOT in `CODEX_NATIVE_TOOL_NAMES`. Returns error if called. |
| Current QuantZhai transformations | None |
| Current telemetry | None |
| Current tests | `NativeToolNamesMembershipTests.test_image_generation_absent`. `OutOfScopeToolContractTests` (test_qz_proxy_tools.py). |
| Runtime/capture evidence | None |
| Known failure modes | None (QuantZhai uses local llama.cpp — no image gen capability) |
| Current policy decision | **documented unsupported path** |
| Rationale | Hosted tool no ToolHandler. QuantZhai local model cannot generate images. |
| Follow-up issue required | No |

### 6.18 computer

| Field | Value |
|---|---|
| Tool / item name | `computer` |
| Codex source file(s) | Not a ToolHandler. Reserved validation namespace in `codex-rs/app-server/src/request_processors/thread_processor.rs`. |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | N/A — reserved namespace only |
| Advertised to model | No |
| Advertised argument shape/schema | N/A |
| Result/output shape | N/A |
| QuantZhai route | NOT in `CODEX_NATIVE_TOOL_NAMES` |
| Current QuantZhai transformations | None (not routed) |
| Current telemetry | None |
| Current tests | `NativeToolNamesMembershipTests.test_computer_absent`. |
| Runtime/capture evidence | None |
| Known failure modes | None |
| Current policy decision | **not a handler** — must never be in CODEX_NATIVE_TOOL_NAMES |
| Rationale | Codex source proves `computer` appears only as a reserved namespace check, never a routed handler. |
| Follow-up issue required | No |

### 6.19 sandbox_permissions (property, not a tool)

| Field | Value |
|---|---|
| Tool / item name | `sandbox_permissions` (argument field on `exec_command`, `shell_command`, `shell`, `container.exec`) |
| Codex source file(s) | `codex-rs/protocol/src/models.rs:34-43` (SandboxPermissions enum) |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | Argument field on `function_call` items |
| Advertised to model | Yes — part of the tool's JSON schema |
| Advertised argument shape/schema | `enum SandboxPermissions { UseDefault, RequireEscalated, WithAdditionalPermissions }` |
| QuantZhai route | Preserved as-is in pass-through. Detected via `_check_sandbox_escalation()` in `qz_responses_stream.py:797` |
| Current QuantZhai transformations | None (pass-through). Telemetry-only: `tool_escalation_requested` event when `== "require_escalated"`. |
| Current telemetry | `tool_escalation_requested` with `tool`, `call_id`, `sandbox_permissions`, `justification` (200-char), `cmd_preview` (80-char). |
| Current tests | None specific to sandbox_permissions preservation. Covered implicitly by pass-through tests. |
| Runtime/capture evidence | Captures show `sandbox_permissions: "require_escalated"` present on outgoing `exec_command` calls. |
| Known failure modes | None — preserved in pass-through. Telemetry is safe (bounded preview, not full payload). |
| Current policy decision | **preserved as-is** in pass-through. Telemetry observation only. |
| Rationale | Codex source proves `SandboxPermissions` enum is optional on shell tool params. QuantZhai correctly preserves it. |
| Follow-up issue required | No for Phase A. Escalation retry advisory (Pattern E) is deferred to #61 Slice C.1. |

### 6.20 custom_tool_call_input.delta / .done (SSE event, not a tool)

| Field | Value |
|---|---|
| Tool / item name | `response.custom_tool_call_input.delta` and `.done` |
| Codex source file(s) | `codex-rs/codex-api/src/sse/responses.rs` (ResponseEvent::ToolCallInputDelta) |
| Codex source SHA | `46f30d02828bd4c52827e5f0482a6f2a982cce5b` |
| Codex item type | SSE event for `custom_tool_call` input streaming |
| Advertised to model | N/A (SSE event, not a tool) |
| QuantZhai route | `.delta` is emitted for `custom_tool_call` items (apply_patch). `.done` is NOT emitted (issue #73 removal). |
| Current QuantZhai transformations | `custom_tool_call_input_events()` in `qz_streaming.py` emits only `.delta`. The `.done` marker was removed. |
| Current telemetry | None specific |
| Current tests | `StreamingStateTests.test_custom_tool_call_input_events_emit_delta_only` (test_qz_streaming.py). |
| Current policy decision | **emit delta only** — no `.done` event |
| Rationale | Codex source proves only `.delta` is parsed as a typed ResponseEvent. |
| Follow-up issue required | No — regression prevented by tests. |

## 7. High-Priority Findings

### 7.1 request_permissions — permission affordance gap

**Status:** Phase B.1 complete. See `docs/codex-tool-parity-and-proxy-policy.md` for Phase A status.

**What Codex expects:**
- Handler: `codex-rs/core/src/tools/handlers/request_permissions.rs`
- Schema: `RequestPermissionsArgs { reason: Option<String>, permissions: RequestPermissionProfile { network: Option<NetworkPermissions>, file_system: Option<FileSystemPermissions> } }`
- Output: `RequestPermissionsResponse { permissions, scope: PermissionGrantScope, strict_auto_review }`
- Codex routes to `session.request_permissions()` which blocks until user grants/denies

**What QuantZhai does today (Phase B.1 — issue #74):**
- Passes through the call unchanged (CODEX_NATIVE_TOOL_NAMES pass-through preserved)
- Adds model-facing affordance guidance via `REQUEST_PERMISSIONS_TOOL_HINT` appended to the
  tool description at declaration time (`qz_tool_request.py`). Text: "If a command fails because
  it needs filesystem or network access beyond the sandbox, request broader permissions here and
  explain why. Do not retry sandbox-blocked commands without requesting permission first."
- Emits `request_permissions_requested` telemetry on outgoing calls with bounded fields:
  `tool`, `call_id`, `reason_preview` (≤200 chars), `reason_len`, `permission_profile` summary.
- Cannot see grant/deny result (Codex handles the UI side) — denial observation deferred to Phase B.2
- Has `_check_sandbox_escalation()` for `sandbox_permissions: "require_escalated"` on command tools

**Remaining gaps (Phase B.2):**
1. Permission grant/deny outcome tracking — the proxy cannot directly observe
   `request_permissions` results, but the incoming `function_call_output` could be analysed
   for denial patterns.
2. Escalation retry advisory (Pattern E in `docs/native-tool-advisory-policy.md` §4) — when the
   model repeatedly uses `require_escalated` on command tools without success, inject an advisory
   suggesting `request_permissions` or explaining the blocker to the user. This requires threshold
   tracking beyond the current single-call telemetry.
3. The current `tool_sandbox_denied` classifier (`qz_native_tool_output.py`) already detects
   "Read-only file system" in sandbox output. This could be extended to detect broader denial
   patterns, but the proxy cannot distinguish `request_permissions` denial from other
   permission denials via function_call_output text alone.

### 7.2 sandbox_permissions — escalation tracking

**Status:** Fully implemented. Escalation retry advisory (Pattern E) completed in #74 Phase B.2.

**What Codex expects:**
- `SandboxPermissions` enum on `ShellToolCallParams` and `ExecCommandArgs`
- Three variants: `UseDefault`, `RequireEscalated`, `WithAdditionalPermissions`
- Codex evaluates the permission request and either grants or denies it

**What QuantZhai does today:**
- Preserves `sandbox_permissions` unchanged in pass-through
- Detects `require_escalated` via `_check_sandbox_escalation()` in the stream loop
- Emits `tool_escalation_requested` telemetry with safe preview fields
- `qz_native_signal.py` `seed_native_advisory_state()` and `record_native_tool_call()`
  count escalation requests into `NativeToolAdvisoryState.escalation_count`
- `check_native_advisories()` returns `repeated_escalation` advisory when
  `escalation_count >= QZ_NATIVE_ESCALATION_THRESHOLD` (default 2, configurable via
  `QZ_NATIVE_ESCALATION_THRESHOLD` env var)
- Advisory fires once per turn (dedup key `__escalation__`); model-visible wording
  suggests explaining the specific permission requirement to the user

### 7.3 apply_patch path

**Status:** Correct. Protocol adapter (not native advisory).

- `apply_patch` is NOT in `CODEX_NATIVE_TOOL_NAMES` — confirmed correct.
- `apply_patch` is handled through path #3 (protocol adapter) in `completed_call_decision()`.
- The #61 advisory system's `check_native_advisories()` does NOT fire for `apply_patch`.
  This is correct — `apply_patch` is not a native tool.
- Write-count advisory (Pattern C in #61) is deferred.

### 7.4 web_search path

**Status:** Correct. Proxy-local.

- `web_search` is NOT in `CODEX_NATIVE_TOOL_NAMES` — confirmed correct.
- Proxy-local execution via `WebSearchProxyToolExecutor`.
- No lifecycle event regression (removed in issue #66).
- Contract enforcement tests exist in `test_qz_web_search_contract_check.py`.

## 8. Follow-up Issues / TODOs

| Issue | Priority | Description |
|---|---|---|
| #74 Phase B.1 | Medium | request_permissions permission affordance — **COMPLETE**. Added tool hint, bounded telemetry, tests. |
| #74 Phase B.2 | Low | request_permissions denial detection / escalation retry advisory — **COMPLETE**. Pattern E implemented: escalation counting, threshold check, advisory, tests. |
| #61 Slice C.1 | Low | Escalation retry advisory (Pattern E) — **COMPLETE**. Implemented in #74 Phase B.2. |
| #61 Slice C.2 | Low | Write-count advisory for apply_patch — requires live evidence |
| #61 Slice C.3 | Low | write_stdin loop advisory (Pattern D) — requires live evidence |
| None (deferred) | Low | local_shell adapter — needs LocalShellCall wire contract |
| None (deferred) | Low | tool_search adapter — needs ToolSearchCall/ToolSearchOutput wire contract |

---

*Created: 2026-05-27. Issue: h4rm0n1c/quantzhai#74 Phase A / Phase B.1.*
*Codex audit SHA: 46f30d02828bd4c52827e5f0482a6f2a982cce5b*
*Governs: issue #74 — Audit Codex tool parity and QuantZhai proxy policy.*
*Depends on: docs/codex-source-tool-contract.md, docs/codex-source-tool-inventory.md, docs/native-tool-advisory-policy.md.*
