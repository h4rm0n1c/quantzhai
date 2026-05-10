# apply_patch Compatibility Plan

Date: 2026-05-10

## Status

Open. No fixes applied yet. This document records the diagnosis from a design
review on 2026-05-10 and the plan to collect live evidence before implementing.

---

## Problem Summary

QuantZhai's `apply_patch` adapter (`proxy/qz_tool_apply_patch.py`) has a known
broken failure path. When the local model emits a `function_call` for
`apply_patch` whose arguments fail coercion, the proxy returns an assistant
message to Codex instead of a tool call. That breaks the conversation loop.

The local model made a function call. Codex expects an `apply_patch_call` or
`custom_tool_call` back so it can attempt execution and return a result. Instead
Codex gets a prose message, shows it to the user, and sends nothing back to the
model. The model's function call has no corresponding output. On the next turn
the conversation is in an inconsistent state and the model cannot retry because
it never received feedback.

The second problem: even when the conversation does eventually continue, the
failure message (`"model emitted invalid patch arguments"`) tells the model
nothing actionable. It does not identify which field was missing, which type
was unrecognised, or what format was expected.

---

## Broken Failure Path — Current Behaviour

`_invalid_apply_patch_call_message` in `qz_tool_apply_patch.py` (line 319)
returns:

```python
{
    "id": ...,
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "apply_patch call rejected..."}],
}
```

This type is `"message"`, not a tool call. Codex treats it as a completion and
the model's `function_call` becomes an orphan.

---

## Known Failure Modes

All of the following currently route to the broken assistant-message path.

| Failure | Root cause |
|---------|------------|
| Malformed JSON arguments | `json.loads` throws, `_parse_apply_patch_arguments` returns `None` |
| Unrecognised `type` value (e.g. `modify_file`, `edit_file`, `add_file`) | Not in `APPLY_PATCH_OPERATION_TYPES` |
| Missing `diff` for `create_file` or `update_file` | `not isinstance(diff, str)` |
| `diff` is `null` rather than absent | Same as above |
| Missing destination for `move_file` | No recognised destination key found |
| `move_file` in custom mode with no hunk | `ValueError` in `_apply_patch_operation_to_patch_text` |
| `rename_file` with no destination | Converted to `move_file`, then same as above |

---

## Coercion Gaps (Suspected)

The following model behaviours have not been observed directly but are
plausible from the model's training data and the known unified-diff header
incident (2026-05-09). They should be checked against live captures before
implementing fixes.

- Using `"content"` or `"text"` instead of `"diff"` for file content.
- Using `"modify_file"`, `"edit_file"`, or `"patch_file"` as the operation
  type instead of `"update_file"`.
- Using `"add_file"` or `"new_file"` instead of `"create_file"`.
- Emitting `diff` as an array of lines rather than a newline-joined string.
- Omitting the `operation` wrapper and emitting a flat dict (the proxy already
  falls back to this, but further nesting is not handled).
- Sending a well-formed patch envelope as a raw string value of `"patch"` but
  without the `"path"` key alongside it.

---

## Fix Design

### Part 1 — Coercion improvements

After live captures confirm which variants the model actually emits, add targeted
normalisation to `_coerce_apply_patch_operation`:

- Map known `type` aliases before the `APPLY_PATCH_OPERATION_TYPES` check.
- Accept `"content"` or `"text"` as a fallback for a missing `"diff"` field.
- Join list-valued `diff` fields with `\n`.
- Accept missing `destination` for `rename_file` if `path` already encodes
  both sides (e.g. `"old.py -> new.py"`) — only if captures confirm this pattern.

### Part 2 — Error feedback path

Two options. Choose after capture data shows whether partial parse information
is recoverable.

**Option A — stateless pass-through (simpler)**

When coercion fails, form the best possible `apply_patch_call` or
`custom_tool_call` from whatever fields are present (even if the patch will fail
at execution time). Let Codex attempt execution. Codex returns a failure result
as a tool output. The model receives a concrete error it can act on.

Downside: Codex's failure message may be less specific than a proxy-generated
one. Upside: no state tracking required, loop remains intact.

**Option B — injected error result (targeted)**

When coercion fails, record `{call_id → reason}` in a per-request error map.
On the next inbound request from Codex, if the input includes no tool output for
that `call_id`, inject a synthetic `function_call_output` with a specific error
such as:

```
apply_patch rejected by proxy: missing diff field for update_file.
Retry with a V4A diff hunk in the diff field. No file headers. Lines
must start with +, -, or a space.
```

The model receives exact guidance for the next attempt. Downside: requires state
threaded through the inbound normalisation path.

Start with Option A. If Codex's native error messages prove too vague for the
model to self-correct, layer in Option B on top.

---

## Evidence Collection Plan

Before implementing anything, collect live captures with the full stack running.

### Setup

- Profile: `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL`
  (use this as the baseline — it is the tested profile for apply_patch work)
- Working directory for Codex tasks: `/tmp/qz-compat-patch-work/` (safe throwaway)
- Capture system must be active before starting the session. Verify with:
  ```bash
  curl -s http://127.0.0.1:18180/qz/status | jq '.capture'
  ls var/captures/
  ```
- The capture system is a tricky beast — confirm it is writing before running
  test tasks, not after.

### Tasks to run

Construct small Codex `exec` tasks that force each operation type. Run each in
a fresh `/tmp/qz-compat-patch-work/` subfolder so Codex has a real workspace.

Suggested task prompts:

1. **create_file** — "Create a new file named hello.py containing a function
   that returns the string 'hello'."
2. **update_file** — "Add a docstring to the function in hello.py."
3. **delete_file** — "Delete hello.py."
4. **move_file / rename_file** — "Rename hello.py to greet.py."
5. **Failed patch / retry** — Give Codex a task where the first attempt uses a
   wrong type name or missing diff, observe whether the model retries correctly.

### Capture artefacts to collect

```text
var/captures/latest-request.json
var/captures/latest-forwarded.json
var/captures/latest-upstream-response.raw
var/captures/latest-response.json
var/captures/latest-dropped-tools.txt
```

Check the upstream response for the raw `function_call` arguments the model
emits before the proxy touches them. That is the ground truth for coercion gaps.

```bash
jq '.output[] | select(.type=="function_call" and .name=="apply_patch") | .arguments' \
  var/captures/latest-upstream-response.raw 2>/dev/null || \
  cat var/captures/latest-upstream-response.raw | grep apply_patch | head -5
```

---

## Implementation Order

1. Collect live captures as above.
2. Review raw model `arguments` payloads for coercion gaps.
3. Add targeted coercion for confirmed patterns.
4. Implement Option A (pass-through on failure).
5. Add golden fixtures for each new coercion case.
6. Smoke with `tests/smoke_apply_patch_proxy.py` and `tests/smoke_apply_patch_codex_exec.py`.
7. If Option A error messages prove too vague for model self-correction, design
   Option B injection and add it.

---

## Relationship to Existing Docs

- `docs/patch-tool-roadmap.md` — parent roadmap for the adapter. The Phase 2
  "still pending" item ("Add more negative fixtures for Codex parser-failure
  history") is the test-coverage counterpart to this plan.
- `docs/master-stabilisation-plan.md` — this work is not a Phase 1 blocker but
  should be picked up after the generic tool lifecycle boundary is stable.
- `var/apply_patch_proxy_spec.md` and `var/apply_patch_examples.md` — the
  model-facing spec and example payloads written for this review. Keep those
  updated as coercion rules change.
