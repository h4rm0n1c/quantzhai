# Codex Source Tool Inventory

**Tracking issue:** h4rm0n1c/quantzhai#67

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
| `apply_patch` | `codex-rs/core/src/tools/handlers/apply_patch.rs` `ToolName::plain("apply_patch")` | `custom_tool_call` | Freeform (patch body as string) | `response.output_item.added` → `response.custom_tool_call_input.delta` × N → `response.custom_tool_call_input.done` → `response.output_item.done` → `response.completed` | Implemented (issue #66) | Keep — custom_tool_call path |
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
| `shell` | `codex-rs/core/src/tools/handlers/shell/shell_handler.rs` `ToolName::plain("shell")` | `function_call` | `ShellToolCallParams` (command: Vec\<String\>) | Same as exec_command | Not yet in CODEX_NATIVE_TOOL_NAMES | Defer — next audit slice; model-facing usage needs further audit |
| `container.exec` | `codex-rs/core/src/tools/handlers/shell/container_exec.rs` `ToolName::plain("container.exec")` | `function_call` | `ShellToolCallParams` (command: Vec\<String\>) | Same as exec_command | Not yet in CODEX_NATIVE_TOOL_NAMES | Defer — next audit slice; same handler family as `shell` |
| `local_shell` | `codex-rs/core/src/tools/handlers/shell/local_shell.rs` `ToolName::plain("local_shell")` | **`local_shell_call`** (`ToolPayload::LocalShell`) | `LocalShellExecAction` | Separate `LocalShellCall` ResponseItem variant | Deferred | Needs separate contract slice — different item type |
| `tool_search` | `codex-rs/core/src/tools/handlers/tool_search.rs` `TOOL_SEARCH_TOOL_NAME` | **`tool_search_call`** + `ToolSearchOutput` | `ToolSearchCall` ResponseItem | Separate item contract | Deferred | Needs separate contract slice |
| `image_generation` | `codex-rs/protocol/src/models.rs` `ResponseItem::ImageGenerationCall` | **`image_generation_call`** | id, status, result (base64) | `response.output_item.added` + `response.output_item.done` with item.type=image_generation_call | Document only | Not for QuantZhai at present |
| `request_plugin_install` | `codex-rs/core/src/tools/handlers/request_plugin_install.rs` `REQUEST_PLUGIN_INSTALL_TOOL_NAME` | `function_call` | Plugin install request | — | Deferred | Operator-specific; not needed |
| `test_sync` | `codex-rs/core/src/tools/handlers/test_sync.rs` `ToolName::plain("test_sync_tool")` | `function_call` | Test sync payload | — | Deferred | Test infrastructure only |
| MCP tools (`read_mcp_resource`, `list_mcp_resources`, etc.) | `codex-rs/core/src/tools/handlers/mcp_resource/` | `function_call` / namespace | MCP resource payload | — | Deferred | MCP integration not in scope |
| Multi-agent tools (`spawn_agent`, `send_message`, etc.) | `codex-rs/core/src/tools/handlers/multi_agents_v2/` | `function_call` | Agent coordination payload | — | Deferred | Multi-agent not in scope |
| Agent jobs (`spawn_agents_on_csv`, etc.) | `codex-rs/core/src/tools/handlers/agent_jobs/` | `function_call` | Agent job payload | — | Deferred | Not in scope |
| `computer` | No ToolHandler in Codex source. Reserved namespace only. | — | — | — | Not in CODEX_NATIVE_TOOL_NAMES | Reserved validation namespace; never a handler |

---

## QuantZhai Native Pass-Through Set (after issue #67)

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
| Shell-like function_call additions | `shell`, `container.exec` | Handlers confirmed in source; `ShellToolCallParams` (command: Vec\<String\>) shape differs from `shell_command`. Need capture-based test before adding. |
| LocalShell item contract | `local_shell` | `LocalShell` ResponseItem variant, not function_call. Needs dedicated contract slice. |
| ToolSearch item contract | `tool_search` | `ToolSearchCall` + `ToolSearchOutput` ResponseItem variants. Needs dedicated slice. |
| image_generation item | `image_generation` | `ImageGenerationCall` ResponseItem. Document only. |
| MCP / multi-agent / plugin | see table above | Out of scope for QuantZhai's single-model local stack. |

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

*Created: 2026-05-25. Governs: issue #67 — Audit and adopt source-backed Codex tool contracts.*
