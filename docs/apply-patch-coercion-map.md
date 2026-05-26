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
- Streaming: `custom_tool_call_input.delta/done` are used for real-time patch streaming.

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

## 8. Recommended Slice B

- Add tests proving each `coercion_strategy` is classified correctly.
- Add tests proving telemetry payload excludes raw patch/path/diff.
- Add tests for `partial_custom_envelope` path.
- Add tests for `failed_missing_diff` and `failed_missing_destination` failure modes.
- Assess whether model-visible advisory is needed for `sibling_patch_promoted` / `legacy_patch_envelope` only.
- No advisory required for `operation_object` canonical path.
- Protocol remains unchanged.
