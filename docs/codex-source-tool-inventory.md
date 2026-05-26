# Codex Source Tool Inventory

**Tracking issues:** h4rm0n1c/quantzhai#67 (initial audit), h4rm0n1c/quantzhai#68 (shell + container.exec slice), h4rm0n1c/quantzhai#69 (local_shell + tool_search slice), h4rm0n1c/quantzhai#70 (document-only and out-of-scope buckets)

**Codex checkout:** `/tmp/qz-audit/codex`

**Codex audit SHA:** `46f30d02828bd4c52827e5f0482a6f2a982cce5b`

**Date:** 2026-05-25

---

## Rule

Codex-visible SSE must be sourced from current Codex parser/item/handler support.

- Operator telemetry is **not** Codex SSE.
- Model repair feedback is **not** Codex SSE.
- No generic `response.<tool>_call.*` lifecycle.

Every entry in `CODEX_NATIVE_TOOL_NAMES` must have a confirmed handler in the Codex source
at the audited SHA. See `docs/codex-source-tool-contract.md` for the full SSE and item-type contracts.

---

## Tool Inventory

| Tool | Codex source evidence | Item type | Payload kind | Events (Codex-visible) | QuantZhai status | Recommendation |
|---|---|---|---|---|---|---|
| `apply_patch` | `codex-rs/core/src/tools/handlers/apply_patch.rs` `ToolName::plain("apply_patch")` | `custom_tool_call` | Freeform (patch body as string) | `response.output_item.added` → `response.custom_tool_call_input.delta` × N → `response.output_item.done` → `response.completed` | Implemented (issue #66); unsupported `.done` marker removed in issue #73 | Keep — custom_tool_call path |
| `web_search` | `codex-rs/protocol/src/models.rs` `ResponseItem::WebSearchCall` | `web_search_call` | WebSearchAction (type, query) | `response.output_item.added` + `response.output_item.done` with `item.type=web_search_call` | Implemented (issue #66) | Keep — proxy_local web_search_call path |
| `exec_command` | `codex-rs/core/src/tools/handlers/unified_exec/unified_exec.rs` `ToolName::plain("exec_command")` | `function_call` | JSON arguments string | `response.output_item.added` → `response.function_call_arguments.delta` × N → `response.function_call_arguments.done` → `response.output_item.done` → `response.completed` | Native pass-through (CODEX_NATIVE_TOOL_NAMES) | Keep |
| `write_stdin` | `codex-rs/core/src/tools/handlers/unified_exec/unified_exec.rs` `ToolName::plain("write_stdin")` | `function_call` | JSON arguments string | Same as exec_command | Native pass-through | Keep |
| `shell_command` | `codex-rs/core/src/tools/handlers/shell/shell_command.rs` `ToolName::plain("shell_command")` | `function_call` | `ShellCommandToolCallParams` (command: string) | Same as exec_command | Native pass-through | Keep |
| `update_plan` | `codex-rs/core/src/tools/handlers/plan.rs` `ToolName::plain("update_plan")` | `function_call` | `UpdatePlanArgs` JSON | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `request_user_input` | `codex-rs/core/src/tools/handlers/request_user_input.rs` `REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"` | `function_call` | `RequestUserInputArgs` JSON | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `request_permissions` | `codex-rs/core/src/tools/handlers/request_permissions.rs` `ToolName::plain("request_permissions")` | `function_call` | `RequestPermissionsArgs` JSON | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `view_image` | `codex-rs/core/src/tools/handlers/view_image.rs` `ToolName::plain("view_image")` | `function_call` | `ViewImageArgs` JSON (path, detail) | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `get_goal` | `codex-rs/core/src/tools/handlers/goal/get_goal.rs` `GET_GOAL_TOOL_NAME = "get_goal"` | `function_call` | `{}` (no args) | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `create_goal` | `codex-rs/core/src/tools/handlers/goal/create_goal.rs` `CREATE_GOAL_TOOL_NAME = "create_goal"` | `function_call` | `CreateGoalArgs` JSON (objective, token_budget) | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `update_goal` | `codex-rs/core/src/tools/handlers/goal/update_goal.rs` `UPDATE_GOAL_TOOL_NAME = "update_goal"` | `function_call` | `UpdateGoalArgs` JSON (status) | Same as exec_command | Added to CODEX_NATIVE_TOOL_NAMES (issue #67) | Pass-through as function_call |
| `shell` | `codex-rs/core/src/tools/handlers/shell/shell_handler.rs` `ToolName::plain("shell")` | `function_call` | `ShellToolCallParams` (command: Vec\<String\>, workdir, timeout_ms, sandbox_permissions, justification) | Same as exec_command | **Added to CODEX_NATIVE_TOOL_NAMES (issue #68)** | Pass-through as function_call. Not advertised when shell_type=shell_command (QuantZhai's config), but registered as fallback. Codex executes on receipt. |
| `container.exec` | `codex-rs/core/src/tools/handlers/shell/container_exec.rs` `ToolName::plain("container.exec")` | `function_call` | `ShellToolCallParams` (same as shell) | Same as exec_command | **Added to CODEX_NATIVE_TOOL_NAMES (issue #68)** | Pass-through as function_call. Never advertised (spec()=None); always registered as fallback handler. Codex executes on receipt. |
| `local_shell` | `codex-rs/core/src/tools/handlers/shell/local_shell.rs` `ToolName::plain("local_shell")` | **`local_shell_call`** (`ToolPayload::LocalShell`) | `LocalShellExecAction` | Separate `LocalShellCall` ResponseItem variant | **Audited (issue #69) — NOT in CODEX_NATIVE_TOOL_NAMES** | LocalShell item type, not function_call. Needs dedicated future adapter. |
| `tool_search` | `codex-rs/core/src/tools/handlers/tool_search.rs` `TOOL_SEARCH_TOOL_NAME` | **`tool_search_call`** + `ToolSearchOutput` | `ToolSearchCall` ResponseItem | Separate item contract | **Audited (issue #69) — NOT in CODEX_NATIVE_TOOL_NAMES** | ToolSearch item types, not function_call. Needs dedicated future adapter. |
| `image_generation` | `codex-rs/core/src/tools/hosted_spec.rs::create_image_generation_tool()` → `ToolSpec::ImageGeneration { output_format }`. ResponseItem: `ImageGenerationCall { id, status, revised_prompt, result }` in `protocol/src/models.rs` | **`image_generation_call`** (hosted, no ToolHandler) | id, status, revised_prompt, result (base64 PNG) | `response.output_item.added` + `response.output_item.done` with item.type=image_generation_call | **Audited (issue #70) — document-only, out of scope** | Hosted tool, no function_call handler. Gated by `image_gen_tool` config + `Feature::ImageGeneration` + model support. QuantZhai uses local llama.cpp — not applicable. |
| `list_mcp_resources` | `codex-rs/core/src/tools/handlers/mcp_resource_spec.rs::create_list_mcp_resources_tool()` → `ToolSpec::Function`. Handler: `ListMcpResourcesHandler` | `function_call` | JSON args: optional server, cursor | Standard function_call events | **Audited (issue #70) — out of scope, not in CODEX_NATIVE_TOOL_NAMES** | function_call handler but registered only when `params.mcp_tools.is_some()`. QuantZhai has no MCP servers. |
| `list_mcp_resource_templates` | `codex-rs/core/src/tools/handlers/mcp_resource_spec.rs::create_list_mcp_resource_templates_tool()` → `ToolSpec::Function`. Handler: `ListMcpResourceTemplatesHandler` | `function_call` | JSON args: optional server, cursor | Standard function_call events | **Audited (issue #70) — out of scope** | Same gate as list_mcp_resources. QuantZhai has no MCP servers. |
| `read_mcp_resource` | `codex-rs/core/src/tools/handlers/mcp_resource_spec.rs::create_read_mcp_resource_tool()` → `ToolSpec::Function`. Handler: `ReadMcpResourceHandler` | `function_call` | JSON args: server (req), uri (req) | Standard function_call events | **Audited (issue #70) — out of scope** | Same gate as list_mcp_resources. QuantZhai has no MCP servers. |
| MCP tools (general) | `codex-rs/core/src/tools/handlers/mcp.rs::McpHandler`. Tool names: `mcp__<server>__<tool_name>` namespace pattern | `function_call` (dynamic names) | JSON args (dynamic schema per MCP server) | Standard function_call events | **Audited (issue #70) — out of scope** | Names are dynamic — cannot appear in CODEX_NATIVE_TOOL_NAMES. QuantZhai has no MCP servers. |
| `spawn_agent` | `codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs` `ToolName::plain("spawn_agent")`, `ToolPayload::Function` | `function_call` | SpawnAgentArgs JSON | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools`. QuantZhai is single-model local stack — collab_tools not enabled. |
| `send_message` | `codex-rs/core/src/tools/handlers/multi_agents_v2/send_message.rs` `ToolName::plain("send_message")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools && config.multi_agent_v2`. |
| `followup_task` | `codex-rs/core/src/tools/handlers/multi_agents_v2/followup_task.rs` `ToolName::plain("followup_task")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools && config.multi_agent_v2`. |
| `wait_agent` | `codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs` `ToolName::plain("wait_agent")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools`. |
| `close_agent` | `codex-rs/core/src/tools/handlers/multi_agents_v2/close_agent.rs` `ToolName::plain("close_agent")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools`. |
| `list_agents` | `codex-rs/core/src/tools/handlers/multi_agents_v2/list_agents.rs` `ToolName::plain("list_agents")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.collab_tools && config.multi_agent_v2`. |
| `spawn_agents_on_csv` | `codex-rs/core/src/tools/handlers/agent_jobs/spawn_agents_on_csv.rs` `ToolName::plain("spawn_agents_on_csv")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.agent_jobs_tools`. QuantZhai has no agent jobs infrastructure. |
| `report_agent_job_result` | `codex-rs/core/src/tools/handlers/agent_jobs/report_agent_job_result.rs` `ToolName::plain("report_agent_job_result")`, `ToolPayload::Function` | `function_call` | JSON args | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.agent_jobs_worker_tools`. |
| `request_plugin_install` | `codex-rs/core/src/tools/handlers/request_plugin_install.rs` `ToolName::plain(REQUEST_PLUGIN_INSTALL_TOOL_NAME)`, `ToolPayload::Function` | `function_call` | RequestPluginInstallArgs JSON | Standard function_call events | **Audited (issue #70) — out of scope** | Gated by `config.tool_suggest && !discoverable_tools.is_empty()`. Requires ChatGPT auth, MCP connection manager, plugin marketplace. |
| `request_plugin_install` | `codex-rs/core/src/tools/handlers/request_plugin_install.rs` `REQUEST_PLUGIN_INSTALL_TOOL_NAME` | `function_call` | Plugin install request | — | Deferred | Operator-specific; not needed |
| `test_sync` | `codex-rs/core/src/tools/handlers/test_sync.rs` `ToolName::plain("test_sync_tool")` | `function_call` | Test sync payload | — | Deferred | Test infrastructure only |
| MCP tools (`read_mcp_resource`, `list_mcp_resources`, etc.) | `codex-rs/core/src/tools/handlers/mcp_resource/` | `function_call` / namespace | MCP resource payload | — | Deferred | MCP integration not in scope |
| Multi-agent tools (`spawn_agent`, `send_message`, etc.) | `codex-rs/core/src/tools/handlers/multi_agents_v2/` | `function_call` | Agent coordination payload | — | Deferred | Multi-agent not in scope |
| Agent jobs (`spawn_agents_on_csv`, etc.) | `codex-rs/core/src/tools/handlers/agent_jobs/` | `function_call` | Agent job payload | — | Deferred | Not in scope |
| `computer` | No ToolHandler in Codex source. Reserved namespace only. | — | — | — | Not in CODEX_NATIVE_TOOL_NAMES | Reserved validation namespace; never a handler |

---

## QuantZhai Native Pass-Through Set (after issue #68)

```python
CODEX_NATIVE_TOOL_NAMES = frozenset({
    # Pre-existing (issue #66 baseline)
    "exec_command",
    "write_stdin",
    "shell_command",
    # Added in issue #67
    "update_plan",
    "request_user_input",
    "request_permissions",
    "view_image",
    "get_goal",
    "create_goal",
    "update_goal",
    # Added in issue #68 (shell + container.exec audit)
    "shell",
    "container.exec",
})
```

All names in this set:
- are proven `function_call` handlers in `codex-rs/core/src/tools/handlers/`
- pass through as public function_call items with standard lifecycle events
- do **not** receive QuantZhai coercion errors
- do **not** receive fake `response.<tool>_call.*` events

---

## Deferred / Next Audit Slices

| Slice | Tools | Reason |
|---|---|---|
| ~~Shell-like function_call additions~~ | ~~`shell`, `container.exec`~~ | **Done in issue #68.** Both added to CODEX_NATIVE_TOOL_NAMES. |
| ~~LocalShell item contract~~ | ~~`local_shell`~~ | **Done in issue #69.** Audited; excluded. LocalShell item type — future adapter if needed. |
| ~~ToolSearch item contract~~ | ~~`tool_search`~~ | **Done in issue #69.** Audited; excluded. ToolSearch item types — future adapter if needed. |
| ~~Document-only / out-of-scope buckets~~ | ~~`image_generation`, MCP, multi-agent, agent_jobs, `request_plugin_install`~~ | **Done in issue #70.** All audited; all excluded. See Slice #70 result section. |
| local_shell adapter | `local_shell` | Future work: LocalShellCall wire contract + proxy adapter for local_shell item type. |
| tool_search adapter | `tool_search` | Future work: ToolSearchCall/ToolSearchOutput wire contract + proxy adapter. |
| image_generation adapter | `image_generation` | Future work if QuantZhai ever targets a hosted API with image generation support. |
| MCP adapter | MCP tools | Future work if QuantZhai gains MCP server support. |

---

## SHA Freshness Warning

If this document references a different SHA than the current `/tmp/qz-audit/codex` HEAD, the source
evidence may be stale. Run:

```bash
cd /tmp/qz-audit/codex && git rev-parse HEAD
```

and compare to `46f30d02828bd4c52827e5f0482a6f2a982cce5b`. If different, re-audit before expanding
`CODEX_NATIVE_TOOL_NAMES` further.

---

---

## Slice #68 — shell and container.exec audit result

**Issue:** h4rm0n1c/quantzhai#68

**Decision: both added to `CODEX_NATIVE_TOOL_NAMES`.**

### `shell`

- Handler: `codex-rs/core/src/tools/handlers/shell/shell_handler.rs`
- `ToolName::plain("shell")`, `matches_kind` → `ToolPayload::Function { .. }`
- Payload: `ShellToolCallParams` — `command: Vec<String>`, `workdir`, `timeout_ms`, `sandbox_permissions`, `justification`
- Spec: `self.options.map(create_shell_tool)` → present only when `ShellHandler::new(options)` (shell_type=Default). When `ShellHandler::default()` (options=None), spec=None → not advertised.
- QuantZhai catalog uses `shell_type = shell_command` → `ShellHandler::default()` registered as fallback.
- Codex will execute any `function_call { name: "shell" }` it receives, regardless of whether it was advertised. The handler is always registered.
- Verdict: **native pass-through** — proxy must not inject unsupported-tool errors.

### `container.exec`

- Handler: `codex-rs/core/src/tools/handlers/shell/container_exec.rs`
- `ToolName::plain("container.exec")`, `matches_kind` → `ToolPayload::Function { .. }`
- Payload: `ShellToolCallParams` (identical to `shell`)
- No `spec()` override → always returns `None` (never advertised to model, in any config)
- Registered as fallback in every non-disabled shell-type configuration via `spec_plan.rs`
- Both `shell` and `container.exec` use `run_exec_like` with `ShellRuntimeBackend::Generic` — same execution path
- Verdict: **native pass-through** — proxy must not inject unsupported-tool errors.

### Why both passed the audit

QuantZhai's `CODEX_NATIVE_TOOL_NAMES` guards against the proxy injecting "tool not recognised" errors
for calls that Codex is prepared to execute. Both `shell` and `container.exec` have registered
handlers that Codex will route and execute. Adding them prevents spurious model-visible errors.

The prior "shape audit needed" flag was resolved: `ShellToolCallParams.command` is `Vec<String>`
(not a bare string like `ShellCommandToolCallParams.command`). This is a payload-level difference
that does not affect proxy pass-through — the proxy does not parse or validate function_call
argument content for native tools.

---

*Created: 2026-05-25. Updated: 2026-05-26 (issue #70 — document-only and out-of-scope buckets). Governs: issues #67, #68, #69, #70.*

---

## Slice #69 — local_shell and tool_search audit result

**Issue:** h4rm0n1c/quantzhai#69

**Decision: neither added to `CODEX_NATIVE_TOOL_NAMES`. Set remains at 12 tools.**

### `local_shell`

- Handler: `codex-rs/core/src/tools/handlers/shell/local_shell.rs`
- `ToolName::plain("local_shell")`, `matches_kind` → `ToolPayload::LocalShell { .. }` — NOT `ToolPayload::Function`
- Spec: `ToolSpec::LocalShell {}` via `create_local_shell_tool()` in `shell_spec.rs` — dedicated spec type, not `ToolSpec::Function`
- ResponseItem wire type: `LocalShellCall { call_id, status: LocalShellStatus, action: LocalShellAction::Exec(LocalShellExecAction) }`
- `LocalShellExecAction`: `command: Vec<String>`, `timeout_ms`, `working_directory`, `env`, `user`
- Router (`router.rs`): `ResponseItem::LocalShellCall → ToolPayload::LocalShell { params }` — never routed through function_call dispatch
- `normalize.rs` note: "LocalShellCall is represented in upstream streams by a FunctionCallOutput"
- **Verdict: NOT a function_call item. Must NOT be in `CODEX_NATIVE_TOOL_NAMES`.** Requires a dedicated future adapter covering the LocalShellCall wire contract before any QuantZhai integration.

### `tool_search`

- Handler: `codex-rs/core/src/tools/handlers/tool_search.rs`
- Tool name: `TOOL_SEARCH_TOOL_NAME` constant
- `matches_kind` default accepts `Function | ToolSearch`, but `handle()` matches `ToolPayload::ToolSearch { arguments }` — the ToolSearch payload variant
- Spec: `ToolSpec::ToolSearch { execution, description, parameters }` — dedicated spec type
- ResponseItem wire types: `ToolSearchCall` and `ToolSearchOutput` — both separate variants from function_call
- Output type: `ToolSearchOutput` (not `FunctionToolOutput`)
- **Verdict: NOT a normal function_call pass-through. Must NOT be in `CODEX_NATIVE_TOOL_NAMES`.** Requires a dedicated future adapter covering the ToolSearchCall/ToolSearchOutput wire contract.

### Why both were excluded

`CODEX_NATIVE_TOOL_NAMES` is specifically for tools that Codex routes via `function_call` items with standard JSON arguments. Both `local_shell` and `tool_search` use dedicated item types with separate wire formats, dispatch paths, and output types. Adding them to the native pass-through set would be incorrect — the proxy cannot pass them through as function_call items because Codex dispatches them via completely different paths.

The proxy has no adapter for either item type today. When a `function_call { name: "local_shell" }` or `function_call { name: "tool_search" }` arrives (which would be anomalous), the proxy correctly returns an unsupported-tool error. This is the right behaviour until dedicated adapters are written.

---

## Slice #70 — Document-only and out-of-scope tool buckets

**Issue:** h4rm0n1c/quantzhai#70

**Decision: CODEX_NATIVE_TOOL_NAMES unchanged — remains at 12 tools.**

### Classification table

| Tool / bucket | Codex item type | Codex gate | QuantZhai classification |
|---|---|---|---|
| `image_generation` | `ImageGenerationCall` (hosted, no ToolHandler) | `image_gen_tool` + `Feature::ImageGeneration` + model support | Document-only. Local llama.cpp has no image generation. |
| `list_mcp_resources` | `function_call` (ToolSpec::Function) | `params.mcp_tools.is_some()` | Out of scope. No MCP servers in QuantZhai. |
| `list_mcp_resource_templates` | `function_call` (ToolSpec::Function) | `params.mcp_tools.is_some()` | Out of scope. No MCP servers. |
| `read_mcp_resource` | `function_call` (ToolSpec::Function) | `params.mcp_tools.is_some()` | Out of scope. No MCP servers. |
| MCP tools (general, `mcp__*`) | `function_call` (dynamic names) | MCP server configuration | Out of scope. Dynamic names cannot be fixed in CODEX_NATIVE_TOOL_NAMES. |
| `spawn_agent` | `function_call` (ToolSpec::Function) | `config.collab_tools` | Out of scope. Single-model local stack. |
| `send_message` | `function_call` | `config.collab_tools && multi_agent_v2` | Out of scope. |
| `followup_task` | `function_call` | `config.collab_tools && multi_agent_v2` | Out of scope. |
| `wait_agent` | `function_call` | `config.collab_tools` | Out of scope. |
| `close_agent` | `function_call` | `config.collab_tools` | Out of scope. |
| `list_agents` | `function_call` | `config.collab_tools && multi_agent_v2` | Out of scope. |
| `spawn_agents_on_csv` | `function_call` | `config.agent_jobs_tools` | Out of scope. |
| `report_agent_job_result` | `function_call` | `config.agent_jobs_worker_tools` | Out of scope. |
| `request_plugin_install` | `function_call` | `config.tool_suggest && discoverable_tools` | Out of scope. Requires plugin/auth infrastructure. |

### Key findings

**image_generation** is a _hosted_ tool (no ToolHandler — `ToolSpec::ImageGeneration`, not `ToolSpec::Function`). The API emits `ImageGenerationCall` ResponseItems server-side when triggered. QuantZhai uses local llama.cpp with no image generation capability — this is document-only.

**MCP resource tools** (`list_mcp_resources`, etc.) are genuine `function_call` handlers but are conditionally registered only when MCP servers are configured (`params.mcp_tools.is_some()`). QuantZhai has no MCP servers — these are never registered and the model is never told about them. They belong in the "out of scope" category, not the native pass-through set.

**MCP general tools** use a dynamic `mcp__<server>__<tool_name>` naming pattern. By definition, their names cannot appear in a static `CODEX_NATIVE_TOOL_NAMES` frozenset.

**Multi-agent tools** (v1 and v2: `spawn_agent`, `send_message`, `followup_task`, `wait_agent`, `close_agent`, `list_agents`, `send_input`, `resume_agent`) are genuine `function_call` handlers but gated by `config.collab_tools`. QuantZhai is a single-model local stack — `collab_tools` is not enabled.

**Agent jobs** (`spawn_agents_on_csv`, `report_agent_job_result`) are genuine `function_call` handlers gated by `config.agent_jobs_tools`. Not applicable to QuantZhai.

**request_plugin_install** is a genuine `function_call` handler gated by `config.tool_suggest && !discoverable_tools.is_empty()`. Requires ChatGPT auth, MCP connection manager, and plugin marketplace infrastructure. Not applicable to QuantZhai.

### Why none were added to CODEX_NATIVE_TOOL_NAMES

`CODEX_NATIVE_TOOL_NAMES` guards against the proxy injecting "tool not recognised" errors for calls that Codex is _actually registered to handle_ in QuantZhai's specific configuration. All tools in this slice are either:
1. Not function_call handlers at all (image_generation — hosted; MCP dynamic — no fixed names), or
2. Function_call handlers that are not registered in QuantZhai's configuration because the required feature/infrastructure gate is not satisfied.

For case 2, the model will never be told about these tools (they are not in the Codex tool spec), so the model will never call them. If they somehow arrive as function_calls (anomalous), the proxy correctly returns an unsupported-tool error.

### Future adapter backlog (if QuantZhai scope changes)

| Future work | Prerequisite |
|---|---|
| `local_shell` adapter | LocalShellCall wire contract + proxy-side LocalShellExecAction adapter |
| `tool_search` adapter | ToolSearchCall/ToolSearchOutput wire contract + proxy adapter |
| `image_generation` pass-through | Hosted API with image_gen enabled + ImageGenerationCall wire contract |
| MCP adapter | MCP server infrastructure in QuantZhai |
| Multi-agent adapter | Codex multi-agent session infrastructure |

---
