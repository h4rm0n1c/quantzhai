# Codex Plan Mode request_user_input Live Capture

Date: 2026-05-27

QuantZhai commit under probe: `0627f39be801c18873950b2fecbca036e37b3263`

Codex audit checkout: `/tmp/qz-audit/codex`

Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`

Issue context: #75 follow-up after the live streaming import-mode regression was fixed.

## Scope

This note records live `qz-codex` behaviour for `request_user_input` in normal
mode versus Plan mode. The goal is to prevent QuantZhai agents from confusing
Codex mode-gated native-tool unavailability with a broken proxy tool.

No proxy behaviour changed during this probe. `request_user_input` remains a
Codex-native pass-through tool. `apply_patch` and `web_search` were not changed.

## Live Probe Setup

Proxy capture mode was enabled with:

```bash
QZ_CAPTURE_MODE=full ./scripts/qz-proxy
```

The disposable target repository was:

```text
/tmp/linuxstreamtools
origin: https://github.com/h4rm0n1c/linuxstreamtools
target HEAD during probe: 1864b9941c2b081df2d6c7a19d26467f97aaeccb
```

The Plan-mode probe used the repo launcher:

```bash
~/turboquant/quantzhai/scripts/qz-codex --no-alt-screen -m Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf
```

Manual interaction was required. The TUI was switched to Plan mode with
BackTab, then the prompt was submitted. Codex displayed the request-user-input
picker; answer `2` was entered manually. The turn resumed and the model replied:

```text
Got it, you picked 2.
```

## Capture Inventory

| Probe | Capture directory | Result |
| --- | --- | --- |
| Normal mode initial request | `var/captures/requests/qz_req_1779819572511_a210` | Model emitted a `request_user_input` function call. Stream completed. |
| Normal mode feedback request | `var/captures/requests/qz_req_1779819583424_9c70` | Codex supplied `function_call_output`: `request_user_input is unavailable in Default mode`. Model explained the mode gate. |
| Plan mode initial request | `var/captures/requests/qz_req_1779819842555_aea0` | Model emitted a `request_user_input` function call. Stream completed. TUI showed the picker. |
| Plan mode feedback request | `var/captures/requests/qz_req_1779819941281_6820` | Codex supplied answer JSON in `function_call_output`. Model acknowledged answer `2`. |

All four captures include `incoming-request.json`, `forwarded-request.json`,
`forwarded-request-after-tools.json`, `request-contract.json`,
`upstream-response.raw`, and `forwarded-sse.raw`.

No capture contained `response.custom_tool_call_input.done`.

## Codex Source Findings

Source-backed facts from Codex SHA
`46f30d02828bd4c52827e5f0482a6f2a982cce5b`:

| Question | Source-backed answer |
| --- | --- |
| Where is the tool registered? | `codex-rs/core/src/tools/spec_plan.rs:201-203` registers `RequestUserInputHandler` with `config.request_user_input_available_modes`. |
| Where is Plan-mode availability configured? | `codex-rs/tools/src/tool_config.rs:36-45` builds available modes from `TUI_VISIBLE_COLLABORATION_MODES` and `ModeKind::allows_request_user_input()`. `codex-rs/protocol/src/config_types.rs:544-546` returns true only for `ModeKind::Plan`. |
| Is Default-mode enablement generally on? | `codex-rs/features/src/lib.rs:1054-1058` defines `default_mode_request_user_input` with `default_enabled: false`. |
| Does the tool remain declared outside Plan mode? | Yes. `codex-rs/core/src/tools/handlers/request_user_input.rs:28-31` always returns the tool spec; the description comes from available modes. |
| What exact unavailable text is source-backed? | `codex-rs/core/src/tools/handlers/request_user_input_spec.rs:85-96` emits `request_user_input is unavailable in {mode_name} mode`. |
| Where is the current mode checked? | `codex-rs/core/src/tools/handlers/request_user_input.rs:58-60` reads `session.collaboration_mode().await.mode` and returns the unavailable message if the current mode is not allowed. |
| How is successful user input surfaced? | `codex-rs/core/src/tools/handlers/request_user_input.rs:66-81` awaits `session.request_user_input`, serializes `RequestUserInputResponse`, and returns text tool output. |
| What is the response shape? | `codex-rs/protocol/src/request_user_input.rs:36-44` defines `RequestUserInputResponse { answers: HashMap<String, RequestUserInputAnswer> }` and `RequestUserInputAnswer { answers: Vec<String> }`. |
| How does the TUI send answers back? | `codex-rs/protocol/src/protocol.rs:697-704` defines `Op::UserInputAnswer` / alias `request_user_input_response`; `codex-rs/core/src/session/mod.rs:2180-2209` emits `EventMsg::RequestUserInput`, and `:2216-2237` resolves the pending response. |

## Normal vs Plan Capture Comparison

| Field | Normal mode | Plan mode |
| --- | --- | --- |
| Client originator | `codex_exec` for the autonomous baseline capture | `codex-tui` for the interactive Plan-mode probe |
| Request headers | `x-codex-turn-metadata` did not include a mode field | `x-codex-turn-metadata` did not include a mode field |
| Incoming body mode signal | No collaboration mode developer block | Incoming developer content included the Plan-mode collaboration mode block |
| Forwarded body mode signal | QuantZhai forwarded only normalized model input; no separate mode scalar | QuantZhai forwarded only normalized model input; no separate mode scalar |
| `request_user_input` tool declaration | Present | Present |
| Tool description | `Request user input for one to three short questions and wait for the response. This tool is only available in Plan mode.` | Same |
| Tool schema | Same `questions[]` schema, `strict: false` | Same |
| Request contract | No per-tool Plan availability field; `turn_harness.available=false` was unrelated local harness state | Same |
| Initial upstream lifecycle | `response.output_item.added` and `response.output_item.done` for `function_call` named `request_user_input` | Same |
| Follow-up tool output | Plain text: `request_user_input is unavailable in Default mode` | JSON text: `{"answers":{"pick_number":{"answers":["2"]}}}` |
| User-visible outcome | Model explained Plan-mode-only availability | UI picker appeared; model resumed and acknowledged answer `2` |

## Lifecycle Details

Normal-mode initial request:

```text
response.output_item.added  function_call request_user_input
response.output_item.done   function_call request_user_input
response.completed
```

Normal-mode follow-up input contained:

```json
{
  "type": "function_call_output",
  "call_id": "fc_KIlpJicZ5R3DrSQNjkLSqoIU81BQAPDV",
  "output": "request_user_input is unavailable in Default mode"
}
```

Plan-mode initial request used the same public function-call lifecycle.

Plan-mode follow-up input contained:

```json
{
  "type": "function_call_output",
  "call_id": "fc_OU5KCkoSe1IzIKwhXEqPKg1vXR3wb4Be",
  "output": "{\"answers\":{\"pick_number\":{\"answers\":[\"2\"]}}}"
}
```

The answer output shape matches Codex source for
`RequestUserInputResponse`.

## QuantZhai Contract Conclusion

Normal-mode `request_user_input` unavailability is expected Codex behaviour, not
a QuantZhai proxy failure. The tool can remain declared in the tools array while
Codex rejects use in the current collaboration mode and feeds the model a normal
`function_call_output` error string.

Plan mode works through the live `qz-codex` streaming path. The picker appeared,
manual answer `2` was accepted, and the turn resumed without stream disconnect,
`Conversation interrupted`, relative-import failure, or fake lifecycle events.

Follow-up alignment: QuantZhai now detects the observed collaboration mode block
internally and injects exactly one tiny model-facing hint:

```text
You are in planning mode.
You are not in planning mode.
```

Only a clear Plan-mode block selects the first line; absent, Default, malformed,
or unclear mode uses the second. This does not change `request_user_input`
routing, hide tools, or treat normal-mode unavailability as failure.

QuantZhai should not classify `request_user_input is unavailable in Default mode`
as a broken native tool. If any future advisory is added for unavailable native
tools, it must be specific to source-backed text and must preserve pass-through
semantics.

## Open Questions

QuantZhai captures did not show a dedicated mode field in headers or in
`request-contract.json`. Plan mode was visible in the incoming developer message
as the collaboration mode block, while Codex source represents mode internally
through `CollaborationMode` on user turns. Treat the developer block as live
evidence, not as a guaranteed public API contract, unless future Codex source or
captures prove a stable header/body field.
