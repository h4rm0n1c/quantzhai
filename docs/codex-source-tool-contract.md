# Codex Source Tool Contract

**Authority:** h4rm0n1c/codex SHA `46f30d02828bd4c52827e5f0482a6f2a982cce5b`

This document records what the Codex client *actually* parses from the OpenAI Responses API SSE
stream, and which item/event shapes it routes, renders, and executes. It supersedes all prior
QuantZhai docs that described fake or inferred lifecycle events.

Codex source was audited locally at `/tmp/qz-audit/codex` during issue #66.
Issue #62 post-close reconciliation and issue #73 on 2026-05-26 rechecked the
same SHA for the apply_patch custom-tool path.

---

## 1. SSE Events Codex Parses (allowlist)

| SSE event type | Codex handler | Notes |
|---|---|---|
| `response.created` | initial response state | Sets session response metadata |
| `response.output_item.added` | `ResponseEvent::OutputItemAdded` | Starts a new output item in Codex's item list |
| `response.output_item.done` | `ResponseEvent::OutputItemDone` | Finalises item; triggers tool routing |
| `response.output_text.delta` | `ResponseEvent::OutputTextDelta` | Streams assistant text deltas |
| `response.custom_tool_call_input.delta` | `ResponseEvent::ToolCallInputDelta` | Streams `custom_tool_call` input content (e.g. patch body) |
| `response.function_call_arguments.delta` | argument assembler | Streams function_call argument JSON |
| `response.function_call_arguments.done` | argument assembler | Finalises function_call argument JSON |
| `response.reasoning_summary_text.delta` | reasoning summary streamer | Streams reasoning summary text |
| `response.reasoning_summary_part.added` | reasoning summary part | Marks start of reasoning summary part |
| `response.completed` | terminal | Signals clean session end |
| `response.failed` | terminal | Signals model failure |
| `response.incomplete` | terminal | Signals incomplete response |
| `[DONE]` | terminal | SSE stream end sentinel |

Source: `codex-rs/codex-api/src/sse/responses.rs`

Note: current Codex source parses `response.custom_tool_call_input.delta` for
custom-tool input streaming. It does not parse
`response.custom_tool_call_input.done` as a typed `ResponseEvent`; issue #73
removed that unsupported marker from the default Codex-visible stream.

---

## 2. ResponseItem Variants Codex Recognises

| item.type | Codex ResponseItem variant | Notes |
|---|---|---|
| `message` | `Message` | Assistant message with content parts |
| `function_call` | `FunctionCall` | Standard JSON function call |
| `custom_tool_call` | `CustomToolCall { call_id, name, input, status }` | Freeform input; used for apply_patch |
| `web_search_call` | `WebSearchCall { status, action }` | Delivered via output_item.added/done only |
| `tool_search_call` | `ToolSearchCall` | |
| `image_generation_call` | `ImageGenerationCall` | |
| `local_shell_call` | `LocalShellCall` | |
| `reasoning` | `Reasoning` | |
| `function_call_output` | `FunctionCallOutput` | Tool result fed back to model |
| `custom_tool_call_output` | `CustomToolCallOutput` | Custom tool result |
| `compaction` | `Compaction` | |
| `context_compaction` | `ContextCompaction` | |
| `other` | `Other` | Unrecognised item passthrough |

Source: `codex-rs/protocol/src/models.rs:743-900`

**Critical: there is NO `apply_patch_call` ResponseItem variant.** apply_patch travels as
`custom_tool_call` with `name="apply_patch"`.

---

## 3. apply_patch Contract

### Wire shape

```
// output_item.added
{
  "type": "custom_tool_call",
  "id": "<item_id>",
  "call_id": "<call_id>",
  "name": "apply_patch",
  "input": "*** Begin Patch\n...\n*** End Patch\n",
  "status": "in_progress"
}

// response.custom_tool_call_input.delta
{ "item_id": "...", "call_id": "...", "output_index": N, "delta": "<full patch text>" }

// output_item.done
{
  "type": "custom_tool_call",
  "id": "<item_id>",
  "call_id": "<call_id>",
  "name": "apply_patch",
  "input": "*** Begin Patch\n...\n*** End Patch\n",
  "status": "completed"
}
```

### Codex-side execution

- Codex routes `custom_tool_call { name="apply_patch" }` → `apply_patch_spec.rs`
- `apply_patch_spec.rs` defines `ToolSpec::Freeform` — the `input` field is passed directly to
  the patch applier with no JSON parsing
- The patch applier expects the `*** Begin Patch / *** End Patch` envelope format

### QuantZhai proxy transformation

The proxy receives a `function_call { name="apply_patch", arguments: "{...}" }` from the upstream
model (llama.cpp / Qwen). It:
1. Coerces the JSON arguments into the `*** Begin Patch` envelope format
2. Rewrites the item as `custom_tool_call { name="apply_patch", input="*** Begin Patch..." }`
3. Emits `output_item.added` (status=in_progress)
4. Emits `custom_tool_call_input.delta` (delta=patch text)
5. Emits `output_item.done` (status=completed)

Source: `proxy/qz_tool_apply_patch.py`, `proxy/qz_streaming.py:custom_tool_call_input_events()`

Issue #73 removed the default `response.custom_tool_call_input.done` emission
after current Codex source confirmed that only `.delta` is parsed. This does not
change apply_patch coercion, the patch envelope, or tool execution semantics.

---

## 4. web_search Contract

### Wire shape

web_search is delivered via `output_item.added` / `output_item.done` only, with item type
`web_search_call`:

```
// output_item.added
{ "type": "web_search_call", "id": "...", "status": "in_progress", "call_id": "..." }

// output_item.done
{
  "type": "web_search_call",
  "id": "...",
  "status": "completed",
  "action": { "type": "search", "queries": ["..."] }
}
```

There are **no `response.web_search_call.*` subevents**. Codex does not parse
`response.web_search_call.in_progress`, `response.web_search_call.searching`, or
`response.web_search_call.completed`. These were fabricated.

Source: `codex-rs/codex-api/src/sse/responses.rs` (no web_search_call branch in parser),
`codex-rs/protocol/src/models.rs:WebSearchCall`

---

## 5. CODEX_NATIVE_TOOL_NAMES (proxy/qz_tools.py)

**This is a routing inventory, NOT an authority and NOT an immutability boundary.**
See `docs/codex-tool-parity-and-proxy-policy.md` for the full policy.

The proxy uses `CODEX_NATIVE_TOOL_NAMES` to identify tools currently routed as Codex-native
pass-through (after optional output_to_codex rewrite). Tools in this set are NOT locked
away forever — QuantZhai may add in-transit handling when justified by Codex source audit,
runtime evidence, and tests. Valid handling includes observation, telemetry, advisory
signals, argument normalisation, deliberate conversion, or proxy-local handling.

Current proven set (finalised in issues #67–#70 — audit SHA `46f30d02828bd4c52827e5f0482a6f2a982cce5b`):

| Tool name | Codex handler file | Added |
|---|---|---|
| `exec_command` | `handlers/unified_exec/unified_exec.rs` | issue #66 |
| `write_stdin` | `handlers/unified_exec/unified_exec.rs` | issue #66 |
| `shell_command` | `handlers/shell/shell_command.rs` | issue #66 |
| `update_plan` | `handlers/plan.rs` | issue #67 |
| `request_user_input` | `handlers/request_user_input.rs` | issue #67 |
| `request_permissions` | `handlers/request_permissions.rs` | issue #67 |
| `view_image` | `handlers/view_image.rs` | issue #67 |
| `get_goal` | `handlers/goal/get_goal.rs` | issue #67 |
| `create_goal` | `handlers/goal/create_goal.rs` | issue #67 |
| `update_goal` | `handlers/goal/update_goal.rs` | issue #67 |
| `shell` | `handlers/shell/shell_handler.rs` | issue #68 |
| `container.exec` | `handlers/shell/container_exec.rs` | issue #68 |

`shell` and `container.exec` notes (issue #68):
- Both use `ShellToolCallParams` (`command: Vec<String>`, not the string form used by `shell_command`).
- `shell` spec is present only when `shell_type=Default`; QuantZhai uses `shell_command`, so `ShellHandler::default()` is registered as a fallback (no spec, not advertised).
- `container.exec` is never advertised (no `spec()` override); always registered as fallback handler.
- Both are executed by Codex via `run_exec_like` with `ShellRuntimeBackend::Generic` when received.

`computer` was **removed** in issue #66. It appears only as a reserved namespace check in
`codex-rs/app-server/src/request_processors/thread_processor.rs:194` — it is not a routed
handler and never should have been in `CODEX_NATIVE_TOOL_NAMES`.

For the full tool-by-tool classification, audit-complete note, and adapter backlog, see
`docs/codex-source-tool-inventory.md`.

**Audit status:** Complete at SHA `46f30d02828bd4c52827e5f0482a6f2a982cce5b` (issues #66–#70).
Re-audit required when the Codex SHA advances. Adapter backlog is demand-driven.

---

## 6. Forbidden SSE Events (must never appear in QuantZhai output)

These event types do not exist in the Codex SSE parser. They were hallucinated by early QuantZhai
lifecycle specs. Any test that was `assertIn` for these strings has been converted to `assertNotIn`.

| Forbidden event | Source of the mistake |
|---|---|
| `response.web_search_call.in_progress` | `ToolLifecycleSpec.lifecycle_start_stages` |
| `response.web_search_call.searching` | `ToolLifecycleSpec.lifecycle_start_stages` |
| `response.web_search_call.completed` | `ToolLifecycleSpec.lifecycle_done_stages` |
| `response.apply_patch_call.in_progress` | same fake lifecycle system |
| `response.apply_patch_call.searching` | same fake lifecycle system |
| `response.apply_patch_call.completed` | same fake lifecycle system |
| `response.qz_probe_call.*` | test fixture that mirrored fake lifecycle |
| `response.file_search_call.*` | never parsed by Codex |
| `response.code_interpreter_call.*` | never parsed by Codex |

---

## 7. Removed Proxy Code (issue #66)

The following were removed:

- `proxy/qz_streaming.py`: `public_tool_lifecycle_event()`, `web_search_call_lifecycle_event()`
- `proxy/qz_tools.py`: `ToolLifecycleSpec` fields `lifecycle_event_prefix`, `lifecycle_start_stages`, `lifecycle_done_stages`
- `proxy/qz_proxy_tools.py`: `ProxyLocalToolRegistry.lifecycle_event_chunks()`, `.lifecycle_start_event_chunks()`, `.lifecycle_done_event_chunks()`

The following were added:

- `proxy/qz_streaming.py`: `custom_tool_call_input_events()` — emits
  `response.custom_tool_call_input.delta`
- `proxy/qz_responses_stream.py`: `_emit_public_tool_item()` now calls `custom_tool_call_input_events()` when the item type is `custom_tool_call`

---

## 8. Contract Enforcement Tests

| Test class | File | What it enforces |
|---|---|---|
| `ApplyPatchLifecycleContractTests` | `tests/test_qz_responses_stream.py` | Full apply_patch output item lifecycle including custom_tool_call_input.delta and absence of unsupported custom_tool_call_input.done |
| `NativeToolListContractTests` | `tests/test_qz_proxy_tools.py` | computer absent, exec_command/write_stdin/shell_command present |
| `ToolLifecycleSpecContractTests` | `tests/test_qz_proxy_tools.py` | Fake lifecycle fields do not exist on ToolLifecycleSpec |
| `StreamingStateTests.test_web_search_call_no_fake_lifecycle_events` | `tests/test_qz_streaming.py` | public_tool_lifecycle_event / web_search_call_lifecycle_event removed from module |
| `StreamingStateTests.test_custom_tool_call_input_events_emit_delta_only` | `tests/test_qz_streaming.py` | custom_tool_call_input_events() emits delta only with correct fields |
| `ParseSSEEventsTests` | `tests/test_qz_web_search_contract_check.py` | payload-aware SSE parser; event names never contain web_search_call |
| `CheckContractTests` | `tests/test_qz_web_search_contract_check.py` | contract via item.type; fake events → fail; no_search vs fail by mode |
| `DeterministicContractTests` | `tests/test_qz_web_search_contract_check.py` | run_deterministic() passes; all checks True |

---

## 9. web_search Detection Note (issue #66 follow-up)

**Critical:** web_search items appear in `response.output_item.added` and `response.output_item.done` events
with `item.type = "web_search_call"` in the JSON data payload. The event names do **not** contain
`web_search_call`. Any code that scans event names for the string `web_search_call` will find nothing.

Correct detection:
```python
is_web_search = (
    event_name in {"response.output_item.added", "response.output_item.done"}
    and isinstance(payload, dict)
    and isinstance(payload.get("item"), dict)
    and payload["item"].get("type") == "web_search_call"
)
```

The smoke script (`scripts/qz-web-search-codex-contract-smoke`) was updated in the issue #66 follow-up
to use `scripts/qz_web_search_contract_check.py` which uses the payload-aware check above. The old
event-name scan (`[e for e in events if "web_search_call" in e]`) always returned `[]` and caused
Section C to always SKIP with "model did not call web_search on this run".

---

*Created: 2026-05-25. Updated: 2026-05-26 (issues #69–#70 closure; #62 post-close apply_patch reconciliation). Governs: issues #66–#70 — Replace hallucinated tool lifecycle contracts with Codex-source contracts; expand and finalise CODEX_NATIVE_TOOL_NAMES.*
