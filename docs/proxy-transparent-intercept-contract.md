# Proxy Transparent Intercept Contract

## Design philosophy

Any failure that is **deterministically predictable from the tool call shape**
is a proxy responsibility to fix — not a model problem to retry. The proxy
sits between the LLM and every tool execution layer. Every turn the model
spends on confused retrying for a known-fixable failure is a proxy design gap.

Three intervention levels (see `AGENTS.md §Deterministic Intercept Principle`
for the decision framework):

| Level | Trigger | Cost saved |
|-------|---------|------------|
| Pre-execution correction | Bad shape in call arguments | 0 Codex round-trips |
| Post-execution interception | Tool ran, failed for fixable reason | 0 extra model turns |
| Advisory injection | Failure known but not auto-fixable | 1–N confused retry turns |

**Model-visible notes must use plain English** — no proxy internal parameter
names. `sandbox_permissions="require_escalated"` looks like a global config
key; "the proxy escalated permissions automatically" is understood correctly.

---

## Implemented intercepts

## 1. Sandbox Escalation (exec_command)

### What it fixes

When a Codex exec_command fails with a sandbox denial, the proxy intercepts
the failure, re-emits the same command with `sandbox_permissions:
require_escalated`, and rewrites the conversation history so the LLM receives
the escalated result instead of the original failure. The LLM never sees the
error and no retry turn is required.

### Why this works

Confirmed from Codex source (`exec_policy.rs`, `unix_escalation.rs`):

```
AskForApproval::Never + SandboxPermissions::RequireEscalated
  → Decision::Allow (non-dangerous commands)
  → EscalationExecution::Unsandboxed
  → command runs outside bwrap, no approval prompt
```

With `approval_policy = "never"` in `config.toml`, escalation is
auto-approved for non-dangerous commands. Dangerous commands (`rm -rf`, etc.)
remain `Decision::Forbidden` regardless.

### Detection signals

Only sandbox-specific kernel/Codex strings — broad OS strings (`permission
denied`, `operation not permitted`) are intentionally excluded to avoid false
positives on normal filesystem ACL errors:

```python
SANDBOX_DENIAL_SIGNALS = (
    "read-only file system",               # bwrap/landlock kernel message
    "seccomp",                             # seccomp sandbox violation
    "landlock",                            # landlock LSM violation
    "writing is blocked by read-only sandbox",   # Codex apply_patch rejection
    "rejected by user approval settings",        # Codex approval rejection
    "patch rejected",                            # Codex apply_patch denial prefix
)
```

### Two-phase flow

**Phase 1 — denial detected (incoming request from Codex):**

1. Proxy scans `body["input"]` for a `function_call_output` (or
   `custom_tool_call_output`) whose text matches a denial signal.
2. Finds the corresponding `exec_command` function_call by `call_id`.
3. Clones the call, forces `sandbox_permissions: require_escalated`,
   appends `_qzesc` to the `call_id`.
4. Registers `{esc_call_id → original_call_id}` in `SandboxEscalationManager`.
5. Emits a synthetic SSE stream to Codex containing only the escalated call:
   `response.created → output_item.added → function_call_arguments.delta →
   function_call_arguments.done → output_item.done → response.completed → [DONE]`
6. Returns early — the real LLM is never called this turn.

**Phase 2 — escalated result returns (follow-up request from Codex):**

1. Proxy scans `body["input"]` for a `function_call_output` whose `call_id`
   matches a pending `_qzesc` entry.
2. Captures the escalated result.
3. Rewrites `body["input"]`:
   - Removes the synthetic escalated `function_call` item.
   - Removes the escalated `function_call_output` item.
   - Replaces the original failed `function_call_output` with the escalated
     result, rewritten to use the original `call_id`.
   - Deduplicates: if the advisory injector also created a `function_call_output`
     for the same `call_id`, only the escalated success survives.
4. Removes the entry from `SandboxEscalationManager`.
5. Forwards the cleaned input to the real LLM — it sees a normal
   `call → success` pair.

### Guard rules

- **Re-escalation loop prevention**: `_build_call_map` skips any exec call
  whose `call_id` ends with `_qzesc`. If the proxy restarts mid-escalation
  and loses in-memory state, the orphaned escalated call won't trigger another
  attempt.
- **apply_patch pass-through**: Apply_patch `custom_tool_call` items have no
  `sandbox_permissions` field. Denial detection fires but `_build_call_map`
  finds no matching exec call and returns `(None, None)`. The failure is
  passed to the LLM normally.
- **Advisory dedup**: Both the `SandboxEscalationManager` and the existing
  `_model_visible_native_advisories` path produce `function_call_output` items
  for the same `call_id`. The rewrite collapses all of them into one success
  item using a `success_inserted` guard.

### Integration point

`proxy/qz_request_router.py`, after `_microcompact_old_tool_results` and
before `normalize_responses_input_for_qwen`. The check block is wrapped in
`try/except Exception: pass` to prevent any bug from breaking normal request
flow. Phase 1 early-returns before the LLM call; Phase 2 only rewrites input.

---

## 2. Apply_patch Correction + Acknowledgement

### What it fixes

Two problems with apply_patch:

**Problem A — outer JSON fences (parse failure → error → retry turn):**
The model sometimes wraps the entire JSON arguments string in markdown code
fences (`` ```json\n{...}\n``` ``). `json.loads` fails before coercion runs,
coercion returns `kind="error"`, an error is injected to the LLM, and a full
extra turn is needed. Fixed by adding a pre-pass in
`_parse_apply_patch_arguments` that strips outer fences before JSON parse.

**Problem B — silent correction (no acknowledgement):**
When coercion succeeds (strips fences, normalises structure), the corrected
call is silently sent to Codex. The LLM never knows its output was changed.
Fixed by the `CorrectionTracker` which injects a note into the apply_patch
result the LLM receives.

### What was already handled (outgoing direction)

`_strip_unified_diff_headers` and `_strip_markdown_code_fences` run in
`_apply_patch_operation_to_patch_text` and
`_normalize_apply_patch_operation_for_codex`. By the time a call reaches
Codex, the diff field already has fences and `---`/`+++` headers stripped.
The fixes in this section address the JSON-level outer wrapping (Problem A)
and the silent correction gap (Problem B), not the diff-level stripping which
was already working.

### Pre-pass fix (Problem A)

In `proxy/qz_tool_apply_patch.py`, `_parse_apply_patch_arguments` now starts:

```python
arguments = _strip_markdown_code_fences((arguments or "").strip())
try:
    data = json.loads(arguments or "{}")
```

Stripping outer fences before `json.loads` means the common ```` ```json...```
```` wrapping falls through to the existing coerce paths instead of returning
`None` immediately. No extra LLM turn needed.

### CorrectionTracker (Problem B)

`CorrectionTracker` in `proxy/qz_sandbox_escalation.py`:

1. **Registration** (`proxy/qz_responses_stream.py`): When
   `completed_call_decision` returns `kind="public"` + `coercion_applied` +
   no error + `name=="apply_patch"` + arguments actually changed, the stream
   calls `CorrectionTracker.register(call_id, original_args, corrected_args)`.

2. **Injection** (`proxy/qz_request_router.py`): After
   `normalize_responses_input_for_qwen` (so items are already in
   `function_call_output` form), `inject_notes` scans the input for any
   `function_call_output` matching a pending `call_id` and appends:
   `[Proxy auto-corrected apply_patch format: stripped markdown code fences]`

3. **Correction note content**: `_build_correction_note` compares original
   and corrected args. Produces phrases like `"stripped markdown code fences"`,
   `"stripped unified diff headers"`, or `"normalised argument structure"`.

---

## Tested behaviour (live, 2026-05-30)

Test script: `tests/test_qz_sandbox_escalation.py` (47 unit tests) and
`/tmp/test_proxy_intercepts.py` (12 integration checks against live proxy).

**Escalation intercept**: A request whose input contains a sandbox-denied
exec_command produces exactly:
```
codex.rate_limits → response.created → response.in_progress →
response.output_item.added → response.function_call_arguments.delta →
response.function_call_arguments.done → response.output_item.done →
response.completed → [DONE]
```
The LLM is not called. The synthetic response contains `require_escalated`.

**Clean request passthrough**: A request with no sandbox denial flows to
the LLM normally. `require_escalated` does not appear in the response.

**Correction acknowledgement**: `CorrectionTracker.inject_notes` appends
`[Proxy auto-corrected apply_patch format: stripped markdown code fences]`
to the matching `function_call_output`. The tracker clears after injection
(second call is identity).

**Re-escalation guard**: A `call_id` ending in `_qzesc` is never re-escalated
even if the output text contains denial signals.

---

## Known limitations

- Sandbox escalation only works for `exec_command` — apply_patch has no
  per-call `sandbox_permissions` field.
- Detection relies on in-memory state (`SandboxEscalationManager._pending`).
  If the proxy restarts between Phase 1 and Phase 2, the pending entry is
  lost; the follow-up request forwards normally to the LLM which sees both
  the original failure and the escalated call in its history.
- `CorrectionTracker` has the same in-memory limitation: if the proxy
  restarts between coercion and the follow-up request, the note is not
  injected (harmless — the call still succeeds).
