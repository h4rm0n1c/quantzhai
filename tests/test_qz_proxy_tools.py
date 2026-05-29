import unittest

from proxy.qz_proxy_tools import (
    ProxyLocalToolExecutor,
    ProxyLocalToolRegistry,
    ProxyToolExecutionContext,
    make_proxy_local_tool_registry,
)
from proxy.qz_tool_lifecycle import ToolContinuationResult
from proxy.qz_tools import CODEX_NATIVE_TOOL_NAMES, ToolLifecycleSpec


class FakeWebRuntime:
    def __init__(self):
        self.calls = []

    def execute_web_search_call(self, call, counters, seen_signatures, request_id="", progress_cb=None):
        self.calls.append({
            "call": call,
            "counters": counters,
            "seen_signatures": seen_signatures,
            "request_id": request_id,
        })
        return (
            {
                "id": "wsc_fake",
                "type": "web_search_call",
                "status": "completed",
                "call_id": call.get("call_id"),
            },
            {
                "type": "function_call_output",
                "call_id": call.get("call_id"),
                "output": "{\"ok\":true}",
            },
            [{"url": "https://example.test"}],
        )


class ProbeProxyToolExecutor(ProxyLocalToolExecutor):
    """Test-only proxy-local executor.

    Uses a fictional 'qz_probe_call' public item type.
    No lifecycle_event_prefix — the fake lifecycle subevent system was removed.
    See docs/codex-source-tool-contract.md.
    """
    function_name = "qz_probe"
    lifecycle = ToolLifecycleSpec(
        name="qz_probe",
        execution="proxy_local",
        public_item_type="qz_probe_call",
        telemetry_name="qz_probe",
        continuation_hops=2,
    )

    def __init__(self):
        self.calls = []

    def started_public_item(self, call: dict, public_index: int) -> dict:
        return {
            "id": call.get("id") or f"qz_probe_{public_index}",
            "type": "qz_probe_call",
            "status": "in_progress",
            "call_id": call.get("call_id"),
        }

    def execute(self, call: dict, context: ProxyToolExecutionContext) -> ToolContinuationResult:
        self.calls.append({
            "call": call,
            "request_id": context.request_id,
            "counters": context.counters,
            "seen_signatures": context.seen_signatures,
        })
        return ToolContinuationResult(
            public_item={
                "id": call.get("id") or "qz_probe_0",
                "type": "qz_probe_call",
                "status": "completed",
                "call_id": call.get("call_id"),
                "output": "probe ok",
            },
            upstream_items=(
                call,
                {
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": "{\"probe\":\"ok\"}",
                },
            ),
            sources=({"url": "probe://local", "title": "probe"},),
        )


class NativeToolListContractTests(unittest.TestCase):
    """Enforce the Codex-source-backed native tool name list.

    Source: codex-rs/core/src/tools/handlers/ at SHA 46f30d02828bd4c52827e5f0482a6f2a982cce5b
    See docs/codex-source-tool-contract.md and docs/codex-source-tool-inventory.md.

    function_call tools (pass through unchanged):
      exec_command, write_stdin, shell_command — unified_exec + shell handlers (pre-#66)
      update_plan, request_user_input, request_permissions — respective handlers (#67)
      view_image — view_image handler (#67)
      get_goal, create_goal, update_goal — goal handlers (#67)
      shell — shell_handler.rs, fallback handler when shell_type != Default (#68)
      container.exec — container_exec.rs, always-fallback handler (no spec) (#68)

    NOT in this set:
      apply_patch  — custom_tool_call item (Freeform), not function_call
      web_search   — web_search_call item, proxy_local execution
      tool_search  — ToolSearchCall item; separate contract slice
      local_shell  — LocalShell item; separate contract slice
      image_generation — ImageGenerationCall item; document only
      computer     — reserved validation namespace, not a handler
    """

    # --- presence tests: tools confirmed as function_call handlers ---

    def test_exec_command_in_native_tool_names(self):
        self.assertIn("exec_command", CODEX_NATIVE_TOOL_NAMES)

    def test_write_stdin_in_native_tool_names(self):
        self.assertIn("write_stdin", CODEX_NATIVE_TOOL_NAMES)

    def test_shell_command_in_native_tool_names(self):
        self.assertIn("shell_command", CODEX_NATIVE_TOOL_NAMES)

    def test_update_plan_in_native_tool_names(self):
        """update_plan: plan.rs ToolName::plain("update_plan")"""
        self.assertIn("update_plan", CODEX_NATIVE_TOOL_NAMES)

    def test_request_user_input_in_native_tool_names(self):
        """request_user_input: request_user_input.rs REQUEST_USER_INPUT_TOOL_NAME"""
        self.assertIn("request_user_input", CODEX_NATIVE_TOOL_NAMES)

    def test_request_permissions_in_native_tool_names(self):
        """request_permissions: request_permissions.rs ToolName::plain("request_permissions")"""
        self.assertIn("request_permissions", CODEX_NATIVE_TOOL_NAMES)

    def test_view_image_in_native_tool_names(self):
        """view_image: view_image.rs ToolName::plain("view_image")"""
        self.assertIn("view_image", CODEX_NATIVE_TOOL_NAMES)

    def test_get_goal_in_native_tool_names(self):
        """get_goal: goal/get_goal.rs GET_GOAL_TOOL_NAME = "get_goal" """
        self.assertIn("get_goal", CODEX_NATIVE_TOOL_NAMES)

    def test_create_goal_in_native_tool_names(self):
        """create_goal: goal/create_goal.rs CREATE_GOAL_TOOL_NAME = "create_goal" """
        self.assertIn("create_goal", CODEX_NATIVE_TOOL_NAMES)

    def test_update_goal_in_native_tool_names(self):
        """update_goal: goal/update_goal.rs UPDATE_GOAL_TOOL_NAME = "update_goal" """
        self.assertIn("update_goal", CODEX_NATIVE_TOOL_NAMES)

    def test_shell_in_native_tool_names(self):
        """shell: shell/shell_handler.rs ToolName::plain("shell").

        Payload: ShellToolCallParams (command: Vec<String>).
        Registered as ShellHandler::default() (fallback, no spec) when
        shell_type != Default (QuantZhai uses shell_command).
        Codex executes function_call{name="shell"} via run_exec_like.
        Source audit: issue #68.
        """
        self.assertIn("shell", CODEX_NATIVE_TOOL_NAMES)

    def test_container_exec_in_native_tool_names(self):
        """container.exec: shell/container_exec.rs ToolName::plain("container.exec").

        Payload: ShellToolCallParams (command: Vec<String>, same as shell).
        Never advertised (spec() returns None — no override). Always registered
        as a fallback handler in all non-disabled shell configurations.
        Codex executes function_call{name="container.exec"} via run_exec_like.
        Source audit: issue #68.
        """
        self.assertIn("container.exec", CODEX_NATIVE_TOOL_NAMES)

    # --- exclusion tests: tools that must NOT be in the native pass-through set ---

    def test_computer_not_in_native_tool_names(self):
        """computer is a reserved validation namespace only — no ToolHandler in Codex source."""
        self.assertNotIn(
            "computer", CODEX_NATIVE_TOOL_NAMES,
            "'computer' is not a native Codex handler — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_apply_patch_not_in_native_tool_names(self):
        """apply_patch uses custom_tool_call item (Freeform), not function_call pass-through."""
        self.assertNotIn(
            "apply_patch", CODEX_NATIVE_TOOL_NAMES,
            "'apply_patch' is a protocol_adapter custom_tool_call — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_web_search_not_in_native_tool_names(self):
        """web_search uses web_search_call item with proxy_local execution."""
        self.assertNotIn(
            "web_search", CODEX_NATIVE_TOOL_NAMES,
            "'web_search' is a proxy_local web_search_call — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_tool_search_not_in_native_tool_names(self):
        """tool_search uses ToolSearchCall item — NOT a function_call pass-through.

        Source audit (issue #69, SHA 46f30d0):
          - Handler: codex-rs/core/src/tools/handlers/tool_search.rs
          - Spec: ToolSpec::ToolSearch { execution, description, parameters } — dedicated spec type
          - handle() matches ToolPayload::ToolSearch { arguments } — separate dispatch path
          - ResponseItems: ToolSearchCall + ToolSearchOutput — both separate variants from function_call
          - Output: ToolSearchOutput (not FunctionToolOutput)
        Verdict: must NOT be in CODEX_NATIVE_TOOL_NAMES; requires a future dedicated adapter.
        """
        self.assertNotIn(
            "tool_search", CODEX_NATIVE_TOOL_NAMES,
            "'tool_search' is a ToolSearchCall item — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_local_shell_not_in_native_tool_names(self):
        """local_shell uses LocalShell item (not function_call) — NOT a function_call pass-through.

        Source audit (issue #69, SHA 46f30d0):
          - Handler: codex-rs/core/src/tools/handlers/shell/local_shell.rs
          - matches_kind → ToolPayload::LocalShell { .. } — NOT Function
          - Spec: ToolSpec::LocalShell {} via create_local_shell_tool() — dedicated spec type
          - ResponseItem: LocalShellCall { call_id, status, action: LocalShellAction }
          - Router: ResponseItem::LocalShellCall → ToolPayload::LocalShell { params }
          - normalize.rs: "LocalShellCall is represented in upstream streams by a FunctionCallOutput"
        Verdict: must NOT be in CODEX_NATIVE_TOOL_NAMES; requires a future dedicated adapter.
        """
        self.assertNotIn(
            "local_shell", CODEX_NATIVE_TOOL_NAMES,
            "'local_shell' is a LocalShell item — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_image_generation_not_in_native_tool_names(self):
        """image_generation is a hosted server-side tool, NOT a function_call handler.

        Source audit (issue #70, SHA 46f30d0):
          - Spec: ToolSpec::ImageGeneration { output_format } via create_image_generation_tool()
            in codex-rs/core/src/tools/hosted_spec.rs — NOT ToolSpec::Function
          - No ToolHandler in codex-rs/core/src/tools/handlers/
          - ResponseItem: ImageGenerationCall { id, status, revised_prompt, result } — separate wire type
          - Gated by image_gen_tool = image_generation_tool_auth_allowed
            && Feature::ImageGeneration && supports_image_generation(model_info)
          - QuantZhai uses local llama.cpp (Qwen) — no image generation capability
        Verdict: document-only / out of scope. Must NOT be in CODEX_NATIVE_TOOL_NAMES.
        """
        self.assertNotIn(
            "image_generation", CODEX_NATIVE_TOOL_NAMES,
            "'image_generation' is a hosted ImageGenerationCall item — must not be in CODEX_NATIVE_TOOL_NAMES",
        )

    def test_native_tool_names_contains_exactly_proven_tools(self):
        """Guard against unreviewed additions — exact set must match audit SHA 46f30d0.

        Updated in issue #68: shell and container.exec added after source audit confirmed
        they are function_call handlers registered as fallbacks in Codex at audited SHA.

        Confirmed unchanged in issue #69: local_shell and tool_search audited and excluded.
        Both use separate item types (LocalShell / ToolSearchCall) — not function_call
        pass-throughs. CODEX_NATIVE_TOOL_NAMES remains at 12 tools.

        Confirmed unchanged in issue #70: image_generation, MCP tools, multi-agent tools,
        agent_jobs, and request_plugin_install all audited and excluded. All are either
        hosted tools, infrastructure-gated, or out of scope for QuantZhai's single-model
        local stack. CODEX_NATIVE_TOOL_NAMES remains at 12 tools.
        """
        expected = {
            # Pre-existing (issue #66 baseline)
            "exec_command",
            "write_stdin",
            "shell_command",
            # Added in issue #67 — source-backed function_call handlers
            "update_plan",
            "request_user_input",
            "request_permissions",
            "view_image",
            "get_goal",
            "create_goal",
            "update_goal",
            # Added in issue #68 — shell + container.exec audit
            "shell",
            "container.exec",
        }
        self.assertEqual(
            set(CODEX_NATIVE_TOOL_NAMES), expected,
            f"CODEX_NATIVE_TOOL_NAMES must match the audited set. "
            f"If expanding, add source evidence to docs/codex-source-tool-inventory.md first.",
        )


class LocalShellToolSearchItemContractTests(unittest.TestCase):
    """Enforce the item-type contract for local_shell and tool_search (issue #69).

    Source audit at SHA 46f30d02828bd4c52827e5f0482a6f2a982cce5b confirms:
      - local_shell  → LocalShellCall ResponseItem + ToolPayload::LocalShell dispatch
      - tool_search  → ToolSearchCall/ToolSearchOutput ResponseItems + ToolPayload::ToolSearch dispatch
    Neither is a function_call handler. Neither belongs in CODEX_NATIVE_TOOL_NAMES.
    No proxy adapter exists yet — both are deferred to future dedicated slices.
    """

    def test_local_shell_not_in_native_names(self):
        """local_shell dispatches via LocalShell item, not function_call."""
        self.assertNotIn("local_shell", CODEX_NATIVE_TOOL_NAMES)

    def test_tool_search_not_in_native_names(self):
        """tool_search dispatches via ToolSearchCall item, not function_call."""
        self.assertNotIn("tool_search", CODEX_NATIVE_TOOL_NAMES)

    def test_both_absent_from_native_names(self):
        """Combined subTest: both must be absent regardless of future set growth."""
        for name in ("local_shell", "tool_search"):
            with self.subTest(tool=name):
                self.assertNotIn(
                    name, CODEX_NATIVE_TOOL_NAMES,
                    f"'{name}' uses a non-function_call item type — "
                    f"must not be in CODEX_NATIVE_TOOL_NAMES until a dedicated adapter is written",
                )

    def test_local_shell_not_a_dropped_tool_pass_through(self):
        """completed_call_decision for local_shell returns 'error' (unknown tool).

        local_shell calls should not appear as function_calls to the proxy in normal
        operation (Codex handles LocalShellCall natively), but if one does arrive,
        the proxy must treat it as an unsupported tool — not silently pass it through.
        """
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry

        class FakeWebRt:
            def execute_web_search_call(self, call, counters, seen_signatures, request_id="", progress_cb=None):
                return type('R', (), {'public_item': {}, 'upstream_items': (), 'sources': ()})()

        registry = make_proxy_local_tool_registry(FakeWebRt())
        call = {"type": "function_call", "name": "local_shell", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call)
        self.assertEqual(
            decision.kind, "error",
            "local_shell arriving as function_call must not be silently passed through",
        )

    def test_tool_search_not_a_dropped_tool_pass_through(self):
        """completed_call_decision for tool_search returns 'error' (unknown tool).

        Same reasoning as local_shell: tool_search uses ToolSearchCall items, not
        function_call. If it somehow arrives as a function_call to the proxy it must
        not be silently passed through.
        """
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry

        class FakeWebRt:
            def execute_web_search_call(self, call, counters, seen_signatures, request_id="", progress_cb=None):
                return type('R', (), {'public_item': {}, 'upstream_items': (), 'sources': ()})()

        registry = make_proxy_local_tool_registry(FakeWebRt())
        call = {"type": "function_call", "name": "tool_search", "call_id": "c1", "arguments": "{}"}
        decision = registry.completed_call_decision(call)
        self.assertEqual(
            decision.kind, "error",
            "tool_search arriving as function_call must not be silently passed through",
        )


class OutOfScopeToolContractTests(unittest.TestCase):
    """Enforce that document-only and out-of-scope Codex tools are absent from CODEX_NATIVE_TOOL_NAMES.

    Source audit at SHA 46f30d02828bd4c52827e5f0482a6f2a982cce5b (issue #70).

    image_generation
      Spec: ToolSpec::ImageGeneration (hosted, not ToolSpec::Function)
      No ToolHandler. ResponseItem: ImageGenerationCall. Gated by image_gen_tool config flag +
      Feature::ImageGeneration + model support. QuantZhai uses local llama.cpp — out of scope.
      Source: codex-rs/core/src/tools/hosted_spec.rs::create_image_generation_tool()

    MCP resource tools  (list_mcp_resources, list_mcp_resource_templates, read_mcp_resource)
      Spec: ToolSpec::Function; ToolPayload::Function — ARE function_call handlers in Codex.
      Registered only when params.mcp_tools.is_some() — requires MCP server infrastructure.
      QuantZhai has no MCP servers — these tools are never registered. Out of scope.
      Source: codex-rs/core/src/tools/handlers/mcp_resource_spec.rs; spec_plan.rs:189-191

    General MCP tools  (mcp__<server>__<tool_name> namespace pattern)
      McpHandler: ToolPayload::Function. Tool names are DYNAMIC — cannot be fixed in CODEX_NATIVE_TOOL_NAMES.
      Registered when MCP servers are configured. QuantZhai has no MCP servers. Out of scope.
      Source: codex-rs/core/src/tools/handlers/mcp.rs::McpHandler

    multi-agent v2  (spawn_agent, send_message, followup_task, wait_agent, close_agent, list_agents)
      All ToolPayload::Function — ARE function_call handlers in Codex.
      Gated by config.collab_tools && config.multi_agent_v2.
      QuantZhai is a single-model local stack — collab_tools not enabled. Out of scope.
      Source: codex-rs/core/src/tools/handlers/multi_agents_v2/

    multi-agent v1  (spawn_agent, send_input, resume_agent, wait_agent, close_agent)
      All ToolPayload::Function — ARE function_call handlers in Codex.
      Gated by config.collab_tools && !config.multi_agent_v2.
      Source: codex-rs/core/src/tools/handlers/multi_agents/

    agent_jobs  (spawn_agents_on_csv, report_agent_job_result)
      ToolPayload::Function — ARE function_call handlers.
      Gated by config.agent_jobs_tools / config.agent_jobs_worker_tools.
      QuantZhai has no agent jobs infrastructure. Out of scope.
      Source: codex-rs/core/src/tools/handlers/agent_jobs/

    request_plugin_install
      ToolName::plain(REQUEST_PLUGIN_INSTALL_TOOL_NAME) = "request_plugin_install"
      ToolPayload::Function — IS a function_call handler.
      Gated by config.tool_suggest && !discoverable_tools.is_empty().
      Requires ChatGPT auth, MCP connection manager, plugin marketplace. Out of scope.
      Source: codex-rs/core/src/tools/handlers/request_plugin_install.rs
    """

    # --- image_generation ---

    def test_image_generation_not_in_native_names(self):
        """image_generation is a hosted server-side tool, not a function_call handler."""
        self.assertNotIn("image_generation", CODEX_NATIVE_TOOL_NAMES)

    # --- MCP resource tools (fixed names, function_call, but MCP infra not in QuantZhai) ---

    def test_list_mcp_resources_not_in_native_names(self):
        """list_mcp_resources: function_call handler but gated on MCP server config — not in QuantZhai."""
        self.assertNotIn("list_mcp_resources", CODEX_NATIVE_TOOL_NAMES)

    def test_list_mcp_resource_templates_not_in_native_names(self):
        """list_mcp_resource_templates: function_call handler but gated on MCP server config."""
        self.assertNotIn("list_mcp_resource_templates", CODEX_NATIVE_TOOL_NAMES)

    def test_read_mcp_resource_not_in_native_names(self):
        """read_mcp_resource: function_call handler but gated on MCP server config."""
        self.assertNotIn("read_mcp_resource", CODEX_NATIVE_TOOL_NAMES)

    # --- multi-agent v2 fixed tool names ---

    def test_spawn_agent_not_in_native_names(self):
        """spawn_agent: function_call handler gated by config.collab_tools — not in QuantZhai."""
        self.assertNotIn("spawn_agent", CODEX_NATIVE_TOOL_NAMES)

    def test_send_message_not_in_native_names(self):
        """send_message: function_call handler (multi_agents_v2) gated by config.collab_tools."""
        self.assertNotIn("send_message", CODEX_NATIVE_TOOL_NAMES)

    def test_close_agent_not_in_native_names(self):
        """close_agent: function_call handler (multi_agents_v2) gated by config.collab_tools."""
        self.assertNotIn("close_agent", CODEX_NATIVE_TOOL_NAMES)

    def test_wait_agent_not_in_native_names(self):
        """wait_agent: function_call handler (multi_agents_v2) gated by config.collab_tools."""
        self.assertNotIn("wait_agent", CODEX_NATIVE_TOOL_NAMES)

    def test_list_agents_not_in_native_names(self):
        """list_agents: function_call handler (multi_agents_v2) gated by config.collab_tools."""
        self.assertNotIn("list_agents", CODEX_NATIVE_TOOL_NAMES)

    # --- agent_jobs ---

    def test_spawn_agents_on_csv_not_in_native_names(self):
        """spawn_agents_on_csv: function_call handler gated by config.agent_jobs_tools."""
        self.assertNotIn("spawn_agents_on_csv", CODEX_NATIVE_TOOL_NAMES)

    def test_report_agent_job_result_not_in_native_names(self):
        """report_agent_job_result: function_call handler gated by config.agent_jobs_worker_tools."""
        self.assertNotIn("report_agent_job_result", CODEX_NATIVE_TOOL_NAMES)

    # --- request_plugin_install ---

    def test_request_plugin_install_not_in_native_names(self):
        """request_plugin_install: function_call handler but requires plugin/auth infrastructure."""
        self.assertNotIn("request_plugin_install", CODEX_NATIVE_TOOL_NAMES)

    # --- combined subTest: all out-of-scope tool names must be absent ---

    def test_all_out_of_scope_tools_absent_from_native_names(self):
        """All document-only and out-of-scope tool names must be absent from CODEX_NATIVE_TOOL_NAMES.

        This combined subTest catches any future accidental addition.
        See class docstring for source evidence for each tool.
        """
        out_of_scope = [
            # image_generation — hosted tool, no function_call handler
            "image_generation",
            # MCP resource tools — function_call but MCP infra not in QuantZhai
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
            # multi-agent v2 — function_call but collab_tools not enabled
            "spawn_agent",
            "send_message",
            "followup_task",
            "wait_agent",
            "close_agent",
            "list_agents",
            # multi-agent v1 additional names
            "send_input",
            "resume_agent",
            # agent_jobs — function_call but agent_jobs_tools not enabled
            "spawn_agents_on_csv",
            "report_agent_job_result",
            # request_plugin_install — function_call but plugin/auth infra required
            "request_plugin_install",
        ]
        for name in out_of_scope:
            with self.subTest(tool=name):
                self.assertNotIn(
                    name, CODEX_NATIVE_TOOL_NAMES,
                    f"'{name}' is out of scope for QuantZhai — must not be in CODEX_NATIVE_TOOL_NAMES",
                )

    # --- proxy registry: out-of-scope arriving as function_call returns error ---

    def test_out_of_scope_tools_return_error_from_proxy_registry(self):
        """Out-of-scope tools arriving as function_call must receive unsupported-tool errors.

        These tools are not registered in QuantZhai's Codex config so the model should never
        call them. If they arrive anyway, the proxy must not silently pass them through.
        """
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry

        class FakeWebRt:
            def execute_web_search_call(self, call, counters, seen_signatures, request_id="", progress_cb=None):
                return type('R', (), {'public_item': {}, 'upstream_items': (), 'sources': ()})()

        registry = make_proxy_local_tool_registry(FakeWebRt())
        out_of_scope = [
            "image_generation",
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
            "spawn_agent",
            "send_message",
            "close_agent",
            "spawn_agents_on_csv",
            "report_agent_job_result",
            "request_plugin_install",
        ]
        for name in out_of_scope:
            with self.subTest(tool=name):
                call = {"type": "function_call", "name": name, "call_id": "c1", "arguments": "{}"}
                decision = registry.completed_call_decision(call)
                self.assertEqual(
                    decision.kind, "error",
                    f"'{name}' arriving as function_call must return error from proxy registry",
                )


class ToolLifecycleSpecContractTests(unittest.TestCase):
    """ToolLifecycleSpec no longer carries lifecycle_event_prefix or stage lists.

    The fake lifecycle subevent system (response.<tool>_call.in_progress etc.) was
    removed because current Codex source does not parse those events.
    See docs/codex-source-tool-contract.md.
    """

    def test_spec_has_no_lifecycle_event_prefix_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_event_prefix"),
            "ToolLifecycleSpec must not have lifecycle_event_prefix — removed in issue #66",
        )

    def test_spec_has_no_lifecycle_start_stages_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_start_stages"),
            "ToolLifecycleSpec must not have lifecycle_start_stages — removed in issue #66",
        )

    def test_spec_has_no_lifecycle_done_stages_field(self):
        spec = ToolLifecycleSpec(name="test", execution="proxy_local")
        self.assertFalse(
            hasattr(spec, "lifecycle_done_stages"),
            "ToolLifecycleSpec must not have lifecycle_done_stages — removed in issue #66",
        )


class ProxyToolRegistryTests(unittest.TestCase):
    def test_registry_classifies_only_registered_proxy_local_calls(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        self.assertEqual(registry.function_names, frozenset({"web_search"}))
        self.assertEqual(registry.max_continuation_hops, 6)
        self.assertEqual(registry.specs[0].execution, "proxy_local")
        self.assertTrue(registry.is_proxy_local_call({
            "type": "function_call",
            "name": "web_search",
        }))
        self.assertFalse(registry.is_proxy_local_call({
            "type": "function_call",
            "name": "apply_patch",
        }))

    def test_started_public_item_uses_web_search_display_shape(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        item = registry.started_public_item({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
        }, public_index=3)

        self.assertEqual(item["id"], "fc_web")
        self.assertEqual(item["type"], "web_search_call")
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["call_id"], "call_web")

    def test_spec_for_call_exposes_web_search_contract(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        spec = registry.spec_for_call({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
        })

        self.assertEqual(spec.name, "web_search")
        self.assertEqual(spec.public_item_type, "web_search_call")
        self.assertEqual(spec.telemetry_name, "web_search")
        # Lifecycle subevent fields were removed — check they are absent.
        self.assertFalse(hasattr(spec, "lifecycle_event_prefix"),
                         "lifecycle_event_prefix must not exist on web_search ToolLifecycleSpec")
        self.assertFalse(hasattr(spec, "lifecycle_start_stages"),
                         "lifecycle_start_stages must not exist on web_search ToolLifecycleSpec")
        self.assertFalse(hasattr(spec, "lifecycle_done_stages"),
                         "lifecycle_done_stages must not exist on web_search ToolLifecycleSpec")

    def test_proxy_local_telemetry_payload_comes_from_lifecycle_spec(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        started = registry.telemetry_payload(call)

        self.assertEqual(started["tool"], "web_search")
        self.assertEqual(started["function_name"], "web_search")
        self.assertEqual(started["call_id"], "call_web")
        self.assertEqual(started["execution"], "proxy_local")
        self.assertEqual(started["public_item_type"], "web_search_call")
        self.assertNotIn("sources", started)

    def test_proxy_local_completed_telemetry_includes_result_counts(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters={},
                seen_signatures=set(),
            ),
        )

        completed = registry.telemetry_payload(call, result=result)

        self.assertEqual(completed["sources"], 1)
        self.assertEqual(completed["upstream_items"], 2)

    def test_proxy_local_stream_reasons_are_registry_owned(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }

        self.assertEqual(registry.terminal_suppression_reason(call), "web_search_terminal")
        self.assertIn("web_search", registry.continuation_limit_message())

    def test_proxy_local_registry_has_no_fake_lifecycle_methods(self):
        """lifecycle_event_chunks/start/done_event_chunks were removed (issue #66).

        These methods emitted fake response.<tool>_call.* SSE events that
        current Codex source does not parse.
        """
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        self.assertFalse(hasattr(registry, "lifecycle_event_chunks"),
                         "lifecycle_event_chunks must be removed")
        self.assertFalse(hasattr(registry, "lifecycle_start_event_chunks"),
                         "lifecycle_start_event_chunks must be removed")
        self.assertFalse(hasattr(registry, "lifecycle_done_event_chunks"),
                         "lifecycle_done_event_chunks must be removed")

    def test_completed_call_decision_uses_registered_proxy_local_names(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())

        web_decision = registry.completed_call_decision({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        })
        patch_decision = registry.completed_call_decision({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        })

        self.assertEqual(web_decision.kind, "proxy_local")
        self.assertEqual(patch_decision.kind, "public")
        self.assertEqual(patch_decision.public_item["type"], "custom_tool_call")

    def test_completed_call_decision_keeps_unknown_function_call_public(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        call = {
            "id": "fc_exec",
            "type": "function_call",
            "call_id": "call_exec",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
        }

        decision = registry.completed_call_decision(call, "native")

        self.assertEqual(decision.kind, "public")
        self.assertEqual(decision.public_item, call)

    def test_continuation_result_returns_public_protocol_adapter_item(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        decision = registry.completed_call_decision({
            "id": "fc_patch",
            "type": "function_call",
            "call_id": "call_patch",
            "name": "apply_patch",
            "arguments": "{\"operation\":{\"type\":\"create_file\",\"path\":\"notes.md\",\"diff\":\"@@\\n+ok\\n\"}}",
        }, "custom")

        result = registry.continuation_result(decision)

        self.assertEqual(result.public_item["type"], "custom_tool_call")
        self.assertEqual(result.public_item["name"], "apply_patch")
        self.assertEqual(result.upstream_items, ())
        self.assertEqual(result.sources, ())

    def test_execute_returns_public_item_and_hidden_upstream_continuation_items(self):
        web_runtime = FakeWebRuntime()
        registry = make_proxy_local_tool_registry(web_runtime)
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        counters = {"search": 0, "open_page": 0}
        seen = set()

        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters=counters,
                seen_signatures=seen,
            ),
        )

        self.assertEqual(web_runtime.calls[0]["request_id"], "qz_req_test")
        self.assertIs(web_runtime.calls[0]["counters"], counters)
        self.assertIs(web_runtime.calls[0]["seen_signatures"], seen)
        self.assertEqual(result.public_item["type"], "web_search_call")
        self.assertEqual(result.upstream_items[0], call)
        self.assertEqual(result.upstream_items[1]["type"], "function_call_output")
        self.assertEqual(result.sources, ({"url": "https://example.test"},))

    def test_continuation_result_executes_proxy_local_tool_with_context(self):
        web_runtime = FakeWebRuntime()
        registry = make_proxy_local_tool_registry(web_runtime)
        call = {
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }
        counters = {"search": 0, "open_page": 0}
        seen = set()
        decision = registry.completed_call_decision(call, "native")

        result = registry.continuation_result(
            decision,
            ProxyToolExecutionContext(
                request_id="qz_req_test",
                counters=counters,
                seen_signatures=seen,
            ),
        )

        self.assertEqual(web_runtime.calls[0]["request_id"], "qz_req_test")
        self.assertEqual(result.public_item["type"], "web_search_call")
        self.assertEqual(result.upstream_items[0], call)

    def test_continuation_result_requires_context_for_proxy_local_tool(self):
        registry = make_proxy_local_tool_registry(FakeWebRuntime())
        decision = registry.completed_call_decision({
            "id": "fc_web",
            "type": "function_call",
            "call_id": "call_web",
            "name": "web_search",
            "arguments": "{\"query\":\"quantzhai\"}",
        }, "native")

        with self.assertRaises(ValueError):
            registry.continuation_result(decision)

    def test_registry_is_not_web_search_specific(self):
        """ProxyLocalToolRegistry works with any proxy-local executor.

        Tests basic execution contract using qz_probe test tool.
        Does NOT test fake lifecycle subevent generation — those were removed.
        """
        executor = ProbeProxyToolExecutor()
        registry = ProxyLocalToolRegistry([executor])
        call = {
            "id": "fc_probe",
            "type": "function_call",
            "call_id": "call_probe",
            "name": "qz_probe",
            "arguments": "{\"value\":1}",
        }

        self.assertEqual(registry.function_names, frozenset({"qz_probe"}))
        self.assertEqual(registry.max_continuation_hops, 2)
        self.assertTrue(registry.is_proxy_local_call(call))

        started_item = registry.started_public_item(call, public_index=7)
        self.assertEqual(started_item["type"], "qz_probe_call")
        self.assertEqual(started_item["status"], "in_progress")

        result = registry.execute(
            call,
            ProxyToolExecutionContext(
                request_id="qz_req_probe",
                counters={"probe": 0},
                seen_signatures=set(),
            ),
        )
        telemetry = registry.telemetry_payload(call, result=result)
        self.assertEqual(executor.calls[0]["request_id"], "qz_req_probe")
        self.assertEqual(result.public_item["type"], "qz_probe_call")
        self.assertEqual(result.upstream_items[1]["type"], "function_call_output")
        self.assertEqual(telemetry["tool"], "qz_probe")
        self.assertEqual(telemetry["public_item_type"], "qz_probe_call")
        self.assertEqual(telemetry["sources"], 1)
        self.assertEqual(registry.terminal_suppression_reason(call), "qz_probe_terminal")


class RepeatedReadDecisionTests(unittest.TestCase):
    """Tests for repeated-read signal integration in completed_call_decision."""

    def _make_registry(self):
        return make_proxy_local_tool_registry(FakeWebRuntime())

    def _exec_call(self, cmd, call_id="call_abc"):
        return {
            "type": "function_call",
            "name": "exec_command",
            "call_id": call_id,
            "arguments": {"cmd": cmd},
        }

    def _make_state_with_read(self, path):
        from proxy.qz_file_signal import RepeatedReadState
        state = RepeatedReadState()
        state.read_paths.add(path)
        state.history_read_paths.add(path)
        return state

    def test_first_read_codex_native_passthrough(self):
        from proxy.qz_file_signal import RepeatedReadState
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = RepeatedReadState()
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "public")

    def test_repeated_read_signal_before_codex_native_passthrough(self):
        registry = self._make_registry()
        call = self._exec_call("cat README.md", "call_1")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "signal")

    def test_repeated_read_signal_uses_original_call_id(self):
        import json
        registry = self._make_registry()
        call = self._exec_call("cat README.md", "call_orig_id")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertEqual(decision.kind, "signal")
        self.assertIsNotNone(decision.signal_result)
        self.assertEqual(decision.signal_result["call_id"], "call_orig_id")
        self.assertEqual(decision.signal_result["type"], "function_call_output")

    def test_repeated_read_signal_not_tool_call_error(self):
        registry = self._make_registry()
        call = self._exec_call("cat README.md")
        state = self._make_state_with_read("README.md")
        decision = registry.completed_call_decision(call, "native", repeated_read_state=state)
        self.assertNotEqual(decision.kind, "error")
        self.assertEqual(decision.kind, "signal")

class SandboxPermissionsPreservationTests(unittest.TestCase):
    """sandbox_permissions=require_escalated must be preserved in native tool pass-through.
    
    Codex source (codex-rs/protocol/src/models.rs:34-43) defines SandboxPermissions enum with
    UseDefault, RequireEscalated, and WithAdditionalPermissions variants. QuantZhai must
    preserve this field unchanged in pass-through (no stripping, no rewriting).
    """

    def _make_registry(self):
        from proxy.qz_proxy_tools import make_proxy_local_tool_registry

        class FakeWebRt:
            def execute_web_search_call(self, call, counters, seen_signatures, request_id="", progress_cb=None):
                return type('R', (), {'public_item': {}, 'upstream_items': (), 'sources': ()})()

        return make_proxy_local_tool_registry(FakeWebRt())

    def test_sandbox_permissions_preserved_in_exec_command_pass_through(self):
        """sandbox_permissions=require_escalated must survive pass-through in arguments."""
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "c1",
            "arguments": json.dumps({
                "cmd": "sudo apt update",
                "sandbox_permissions": "require_escalated",
                "justification": "need elevated access",
            }),
        }
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "public")
        # The pass-through preserves the call dict (including arguments) unchanged
        args = json.loads(decision.call["arguments"])
        self.assertEqual(args.get("sandbox_permissions"), "require_escalated")

    def test_sandbox_permissions_preserved_in_shell_command_pass_through(self):
        """sandbox_permissions=require_escalated must survive pass-through in shell_command."""
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "shell_command",
            "call_id": "c2",
            "arguments": json.dumps({
                "command": "sudo systemctl status",
                "sandbox_permissions": "require_escalated",
            }),
        }
        decision = registry.completed_call_decision(call)
        self.assertEqual(decision.kind, "public")
        args = json.loads(decision.call["arguments"])
        self.assertEqual(args.get("sandbox_permissions"), "require_escalated")

    def test_request_permissions_preserved_as_pass_through(self):
        """request_permissions must pass through as public, not error or signal."""
        import json
        registry = self._make_registry()
        call = {
            "type": "function_call",
            "name": "request_permissions",
            "call_id": "c3",
            "arguments": json.dumps({
                "reason": "need full disk access",
                "permissions": {"file_system": "full_read_write"},
            }),
        }
        decision = registry.completed_call_decision(call)
        self.assertEqual(
            decision.kind, "public",
            "request_permissions must be native pass-through (kind='public'), "
            f"got '{decision.kind}'",
        )
        # Arguments must be preserved
        args = json.loads(decision.call["arguments"])
        self.assertIn("permissions", args)
        self.assertIn("reason", args)


class NativeToolAdvisoryIntegrationTests(unittest.TestCase):
    """Tests for native tool advisory integration in completed_call_decision."""

    def _make_registry(self):
        return make_proxy_local_tool_registry(FakeWebRuntime())

    def _exec_call(self, cmd, call_id="call_abc"):
        return {
            "type": "function_call",
            "name": "exec_command",
            "call_id": call_id,
            "arguments": {"cmd": cmd},
        }

    def test_repeated_failing_command_integration(self):
        from proxy.qz_native_signal import NativeToolAdvisoryState, QZ_NATIVE_FAIL_REPEAT_THRESHOLD
        registry = self._make_registry()
        call = self._exec_call("fail")
        state = NativeToolAdvisoryState()
        
        from proxy.qz_native_signal import command_signature
        sig = command_signature(call)
        state.command_failure_counts[sig] = QZ_NATIVE_FAIL_REPEAT_THRESHOLD
        
        decision = registry.completed_call_decision(call, native_advisory_state=state)
        self.assertEqual(decision.kind, "signal")
        self.assertEqual(decision.signal_metadata["advisory_reason"], "repeated_failing_command")

    def test_excessive_call_count_integration(self):
        from proxy.qz_native_signal import NativeToolAdvisoryState, QZ_NATIVE_MAX_CALLS_PER_TURN
        registry = self._make_registry()
        call = self._exec_call("ls")
        state = NativeToolAdvisoryState()
        state.native_call_count = QZ_NATIVE_MAX_CALLS_PER_TURN
        
        decision = registry.completed_call_decision(call, native_advisory_state=state)
        self.assertEqual(decision.kind, "signal")
        self.assertEqual(decision.signal_metadata["advisory_reason"], "excessive_call_count")
