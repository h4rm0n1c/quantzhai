# apply_patch Coercion Map

This audit maps the current `apply_patch` coercion stack in `proxy/qz_tool_apply_patch.py`.

## 1. Current contract

- `apply_patch` is exposed to Codex as `custom_tool_call name=apply_patch`.
- `apply_patch_call` is forbidden/removed.
- This audit does not change the wire protocol.

## 2. Accepted input shapes and coercion paths

The system employs multiple strategies to normalize incoming tool arguments.

| Coercion Strategy | Parser Function | Input Shape | Output Shape |
| :--- | :--- | :--- | :--- |
| `operation_object` | `_coerce_apply_patch_operation` | Canonical `{"operation": {...}}` | Cleaned dict |
| `sibling_patch_promoted` | `_coerce_apply_patch_operation` | `{"operation": {...}, "patch": "diff"}` | Merged dict |
| `top_level_operation` | `_coerce_apply_patch_operation` | `{"type": "...", ...}` | Promoted to op dict |
| `legacy_patch_envelope` | `_parse_apply_patch_arguments` | `{"patch": "*** Begin Patch ... ***"}` | Extracted op/path |
| `legacy_patch_with_path` | `_parse_apply_patch_arguments` | `{"patch": "...", "path": "..."}` | Extracted op/path |
| `partial_custom_envelope`| `_extract_partial_operation` | Mixed/Missing fields | Best-effort op dict |

## 3. Failure classifications

If coercion fails, one of the following is returned, often resulting in a specific advisory.

| Classification | Detection Logic |
| :--- | :--- |
| `failed_invalid_json` | JSON parser failure |
| `failed_non_object_json`| Input is not a JSON object |
| `failed_unknown_operation_type` | `op_type` not in `APPLY_PATCH_OPERATION_TYPES` |
| `failed_missing_path` | Missing mandatory `path` field |
| `failed_missing_diff` | Missing mandatory `diff` (except `delete_file`) |
| `failed_missing_destination`| Missing `destination` for `move_file`/`rename_file` |
| `failed_unclassified` | Catch-all for other parsing errors |

## 4. Move/rename handling

`move_file` and `rename_file` normalize to the `move_file` operation type.
Destination is identified via any key in `APPLY_PATCH_DESTINATION_KEYS`:
`("destination", "new_path", "to", "move_to", "target_path")`.

## 5. Output adapters

- `_function_call_to_custom_apply_patch_call`: Normalizes outgoing call.
- `_custom_apply_patch_call_to_function_call`: Codex-to-proxy conversion.
- `_custom_apply_patch_output_to_function_output`: Proxy-to-Codex output mapping.
- Streaming: `custom_tool_call_input.delta` plus final `output_item.done` are
  used for real-time patch streaming.

## 6. Telemetry safety

`inspect_apply_patch_arguments(arguments: str) -> dict` generates safe nested
`apply_patch` telemetry metadata. `build_tool_coercion_telemetry_payload()` in
`proxy/qz_responses_stream.py` builds the stream coercion telemetry payload used
by `ResponsesStreamRuntime`.

- **Included:** `args_shape`, `operation_present`, `patch_present`, `path_present`, `diff_present`, `destination_present`, `operation_type` (enum-clamped), `coercion_strategy` (enum).
- **Excluded:** Raw arguments, raw patch body, raw diff, file content, full path, destination path.
- **Certainty:** Certain (metadata only).

## 7. Implementation details

- `coerce()` itself does not emit telemetry.
- `completed_call_decision()` in `proxy/qz_proxy_tools.py` populates `CompletedToolCallDecision` with `coercion_applied` and `coercion_error`.
- `qz_responses_stream.py` emits `coercion_succeeded` or `coercion_failed` telemetry based on `decision.coercion_applied` and `decision.coercion_error`.
- Commit `6c8e7ac` claimed Slice B.2 stream telemetry integration coverage, but
  its test duplicated payload construction instead of exercising production
  code. The corrected B.2 tests now call the production helper used by
  `ResponsesStreamRuntime`; runtime behaviour is unchanged except for the
  helper extraction.

## Live apply_patch probe — linuxstreamtools /tmp clone

Date: 2026-05-26.

QuantZhai commit: `ff74216`.

Target repo: `https://github.com/h4rm0n1c/linuxstreamtools`, disposable clone
at `/tmp/qz-apply-patch-live/linuxstreamtools`, reset to `origin/main`
`1864b99`.

Model: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf`.

Attempts:

1. Prompt: create `QZ_APPLY_PATCH_PROBE.txt` containing exactly
   `QuantZhai apply_patch live probe`; stop, do not modify anything else, do
   not commit. Codex emitted `apply_patch` but local Codex sandbox was
   read-only, so the patch was rejected before the file was written.
2. Prompt: explicitly use the available `apply_patch` tool to create the same
   file with the same content and nothing else. Codex emitted `apply_patch`
   and the file was created as an untracked file with the requested single
   line.

Observed shape:

- Both live attempts emitted canonical `operation_object` arguments:
  `{"operation":{"type":"create_file","path":"...","diff":"..."}}`.
- No `sibling_patch_promoted`, `legacy_patch_envelope`, `legacy_patch_with_path`,
  or `partial_custom_envelope` shape occurred.
- QuantZhai adapted the canonical operation into the Codex-visible
  `custom_tool_call` patch envelope; no fallback argument repair was needed.

Telemetry observed:

- `coercion_succeeded` fired for both attempts.
- Nested `apply_patch` telemetry was:
  `coercion_strategy=operation_object`, `patch_present=false`,
  `path_present=true`, `diff_present=true`, `operation_type=create_file`.
- Telemetry did not include raw patch body, raw diff, or raw file path. Full
  request captures intentionally contained raw request/stream bodies because
  the probe ran with full capture enabled; telemetry remained metadata-only.
- Codex-visible forwarded SSE remained `custom_tool_call` plus
  `response.custom_tool_call_input.*`; no `apply_patch_call`,
  `apply_patch_call_output`, or `response.apply_patch_call.*` contract appeared.

Advisory recommendation:

- Do not add a model-visible advisory for canonical `operation_object` based on
  this live probe.
- #62 current scope complete. Future fallback-shape advisory should be a new
  issue if telemetry later proves need.
- Protocol remains unchanged.

## #62 closeout decision

- Advisory not implemented by design.
- Telemetry is sufficient for the currently observed canonical
  `operation_object` shape.
- Hard errors remain model-visible for invalid apply_patch calls.
- Future advisory requires repeated live fallback evidence such as
  `sibling_patch_promoted`, `legacy_patch_*`, or other bad-but-coerced patterns.
- Future fallback-shape advisory should be a new issue if telemetry later
  proves need.

## Post-close Codex source reconciliation — 2026-05-26

QuantZhai commit at reconciliation start: `17894d8`.

Codex repo path: `/tmp/qz-audit/codex`.

Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`.

Checked current Codex source:

- `codex-rs/protocol/src/models.rs`: `ResponseInputItem` and `ResponseItem`.
- `codex-rs/codex-api/src/sse/responses.rs`: `process_responses_event`.
- `codex-rs/core/src/tools/handlers/apply_patch_spec.rs`:
  `create_apply_patch_freeform_tool`.
- `codex-rs/core/src/tools/handlers/apply_patch.rs`: `ApplyPatchHandler`,
  `apply_patch_payload_command`, and patch parsing call path.
- `codex-rs/core/src/tools/handlers/apply_patch.lark`: patch envelope grammar.
- `codex-rs/core/tests/common/responses.rs`: apply_patch test helper emits
  `custom_tool_call` for the freeform path.

Result:

- `CustomToolCall` is present.
- `CustomToolCallOutput` is present.
- No `ApplyPatchCall` / `apply_patch_call` `ResponseItem` variant exists.
- `apply_patch` is represented as `custom_tool_call` with
  `name="apply_patch"` and freeform `input`.
- The current Codex stream parser handles `response.output_item.added`,
  `response.output_item.done`, and `response.custom_tool_call_input.delta`.
- Current Codex source does not parse `response.custom_tool_call_input.done`
  as a typed event; QuantZhai's extra `.done` marker is ignored/compatible and
  not required for Codex tool execution.
- `response.apply_patch_call.*`, `apply_patch_call`, and
  `apply_patch_call_output` remain absent from the Codex-visible contract.

Decision:

- #62 remains closed.
- No runtime change is required.
- No model-visible advisory is required for the observed canonical
  `operation_object` path.

## #73 custom_tool_call_input.done removal — 2026-05-26

QuantZhai commit at #73 start: `1fb3dba`.

Codex repo path: `/tmp/qz-audit/codex`.

Codex audit SHA: `46f30d02828bd4c52827e5f0482a6f2a982cce5b`.

Checked current Codex source:

- `codex-rs/protocol/src/models.rs`: `CustomToolCall` and
  `CustomToolCallOutput` are present; no `ApplyPatchCall` /
  `apply_patch_call` item exists.
- `codex-rs/codex-api/src/sse/responses.rs`: `process_responses_event`
  parses `response.output_item.added`, `response.output_item.done`, and
  `response.custom_tool_call_input.delta`; it does not parse
  `response.custom_tool_call_input.done`.
- `codex-rs/core/src/tools/handlers/apply_patch_spec.rs` and
  `apply_patch.rs`: apply_patch remains a freeform `custom_tool_call` using the
  `*** Begin Patch` / `*** End Patch` envelope.

Decision:

- Removed `response.custom_tool_call_input.done` from the default
  Codex-visible stream.
- Supported stream path is now `response.output_item.added` →
  `response.custom_tool_call_input.delta` → `response.output_item.done`.
- No runtime behaviour change to apply_patch coercion, patch envelope
  construction, telemetry, or advisory policy.
- `apply_patch_call`, `apply_patch_call_output`, and
  `response.apply_patch_call.*` remain forbidden.

## 8. Recommended Slice B

- Add tests proving each `coercion_strategy` is classified correctly.
- Add tests proving telemetry payload excludes raw patch/path/diff.
- Add tests for `partial_custom_envelope` path.
- Add tests for `failed_missing_diff` and `failed_missing_destination` failure modes.
- Live probe result: `operation_object` occurred in both attempts; no model-visible advisory is required for the canonical path right now.
- #62 current scope is complete; open a new issue before adding advisory logic
  for repeated fallback shapes such as `sibling_patch_promoted` or
  `legacy_patch_envelope`.
- Protocol remains unchanged.
