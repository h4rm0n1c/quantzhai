# Proxy Intercept Research: Save-an-LLM-Call Opportunities

Systematic survey of proxy intercept opportunities under the Deterministic
Intercept Principle (see `AGENTS.md`): any failure predictable from call shape
is a proxy responsibility, not a model retry problem.

For each pattern: evidence source, failure shape, intervention level, fixability
verdict, and priority.

Implemented patterns are in `docs/proxy-transparent-intercept-contract.md`.
Decision framework is in `AGENTS.md §Deterministic Intercept Principle`.

---

## Methodology

Data sources used:
- Codex source at `/tmp/qz-audit/codex/codex-rs/apply-patch/src/`
- fd-test session logs (`/tmp/fd-short-v5.log`, `/tmp/fd-sandbox-fresh.log`)
- Latest forwarded request captures (`var/captures/latest-forwarded.json`)
- Existing coercion paths in `proxy/qz_tool_apply_patch.py`
- Existing signal classifier in `proxy/qz_native_tool_output.py`

Decision rule: a pattern is worth intercepting if it is (a) common, (b) the
fix is deterministic without knowing file state, and (c) the fix costs a full
LLM turn under the current code.

---

## Implemented (baseline)

| # | Pattern | Where fixed |
|---|---------|-------------|
| I1 | exec sandbox denial (`Read-only file system`, `landlock`, `seccomp`) | `SandboxEscalationManager` — Phase 1/2 intercept, saves 1 turn |
| I2 | apply_patch outer JSON markdown fences (```` ```json\n{...}\n``` ````) | `_parse_apply_patch_arguments` pre-pass — parse succeeds, no retry |
| I3 | apply_patch diff inner fences + `---`/`+++` headers | `_strip_unified_diff_headers` in outgoing path — already removed before Codex sees the call |
| I4 | apply_patch wrong field shapes (sibling `patch`, top-level `patch`) | `_parse_apply_patch_arguments` coerce paths |
| I5 | Correction acknowledgement | `CorrectionTracker` — injects note into next result |

---

## Candidate patterns (not yet implemented)

### AP-1 — apply_patch `update_file` fully empty diff after strip ✅ IMPLEMENTED

**Implemented** in `ApplyPatchToolAdapter.coerce()`. If `diff_stripped.strip()` is
empty the coercion returns a precise error immediately. Tests: `ApplyPatchAP1EmptyDiffTests`.

---

### AP-1b — apply_patch empty trailing hunk in multi-hunk diff (HIGH — confirmed live)

**Error**: `"Update file hunk for path '...' is empty"` / `"invalid hunk at line 2"`.

**Live session evidence (2026-05-31)**: 3 occurrences in a single 8-call session on
`src/exit_codes.rs`. The model generated two-hunk diffs but truncated the diff at the
second `@@` boundary, leaving the last hunk with no content lines. AP-1 correctly did
NOT fire (the stripped diff is non-empty), but Codex's apply_patch parser rejected
with the empty-hunk error.

**Pattern**:
```
@@ -3,13 +3,19 @@
 context_line
+added_line
 context_line
@@           ← model stopped here, no content for second hunk
```
After `_strip_unified_diff_headers`, the trailing `@@` is present but has no
`+`/`-`/` ` lines before the next `@@` or end-of-diff.

**Proxy-side detection**: After stripping, scan hunk markers. For each `@@` line,
check if any `+`/`-`/` ` content line follows before the next `@@` or end-of-diff.
If not, the hunk is empty → return coercion error: `"hunk N has no content lines
— include at least one +, -, or context line after each @@ marker"`.

**Fixable?** Not auto-correctable (we don't know what content should be there), but
early-error saves the Codex round-trip.

**Priority: HIGH** — 3 confirmed occurrences in a single real session, easy detect.

---

### AP-2 — apply_patch `update_file` lines without prefix (`+`/`-`/` `)

**Error**: `"Unexpected line found in update hunk: '...'. Every line should
start with ' ' (context line), '+' (added line), or '-' (removed line)"`
(`apply-patch/src/parser.rs:450`, `streaming_parser.rs:77`).

**When it occurs**: The model omits the space prefix on context lines, writing
raw file content instead of a proper unified diff. Example:

```
@@ @@
-old_function()
new_function()        ← missing leading space
```

The space-for-context-line is not enforced by the proxy's outgoing strip;
it has to be correct when the model generates it.

**Proxy-side detection**: After `_strip_unified_diff_headers`, scan hunk lines.
If a line is non-empty, doesn't start with `+`, `-`, or ` `, and doesn't look
like a hunk header (`@@`) or envelope marker (`*** `), it may be a missing
space. Auto-correct: prepend ` ` to unrecognised lines.

**Fixable?** YES — deterministic rule. The "unexpected line" is almost always
a context line missing its space prefix. Auto-prepending ` ` to such lines
converts them to context lines. The fix is only wrong if the model was trying
to ADD a line that looks like existing content without a `+` — but in that
case the generated diff was already semantically broken and the model would
have to retry anyway.

**Risk**: If the model genuinely intended to write a `+` line but omitted it,
prepending ` ` turns it into a no-op context line — the edit would be silently
dropped. Safer to emit a coercion note: `"auto-prefixed N unprefixed lines as
context lines"` so the model can verify.

**Evidence**: `"Unexpected line found in update hunk: 'use std::process;'"`
appeared multiple times in fd-test logs. The model read the file correctly but
generated the diff without the required space prefix on unchanged lines.

**Priority: MEDIUM-HIGH** — confirmed in test data, auto-correct is
deterministic but needs an acknowledgement note (via `CorrectionTracker`).

---

### AP-3 — apply_patch CRLF in diff field

**Error**: Context mismatch when the diff has `\r\n` endings but the target
file has `\n`. The context lines include the `\r` which doesn't appear in the
file, so `"Failed to find expected lines"` fires.

**When it occurs**: Model generates diff on Windows or the model's tokeniser
normalises to CRLF internally.

**Proxy-side detection**: Check if `\r\n` appears in the diff field.

**Fixable?** YES — strip `\r` from all lines in the diff field during
`_normalize_apply_patch_operation_for_codex`. Pure normalisation, zero
semantic loss.

**Evidence**: Not directly observed in fd-test (Linux host), but the Codex
source explicitly comments on CRLF normalisation in fixtures
(`app-server-protocol/src/schema_fixtures.rs:136`). Likely on mixed-OS setups
or if model was fine-tuned on Windows data.

**Priority: LOW-MEDIUM** — easy fix but only relevant in CRLF-producing envs.

---

### AP-4 — apply_patch `Failed to find expected lines` (context mismatch)

**Error**: `"Failed to find expected lines in X:\n..."` from
`apply-patch/src/lib.rs:772`. Context lines in the diff don't match the
actual file content.

**When it occurs**: Model read the file earlier but the file has since changed
(another tool modified it), OR the model hallucinated/misread the content, OR
leading/trailing whitespace differs.

**Proxy-side detection**: Easy — look for `"Failed to find expected lines"` in
`custom_tool_call_output.output`.

**Fixable?** NOT auto-correctable. We don't know the current file content.

**Proxy action**: Detect the pattern and inject a harness note:
`"apply_patch context mismatch — re-read the file with exec cat before
generating the diff"`. This is advisory (same as existing sandbox advisory
system), not a transparent fix. Saves the model from generating another
wrong diff by guiding the recovery action precisely.

**Evidence**: 3 occurrences confirmed in fd-test. The model read the file
first but then generated diffs with wrong context lines.

**Priority: MEDIUM** — can't auto-fix but advisory prevents a second wrong diff.

---

### E-1 — exec_command `command` field instead of `cmd`

**Schema reality**: `ExecCommandArgs.cmd` is the required field (Codex source:
`tools/handlers/unified_exec.rs:30`). The model sees the schema clearly with
`"required": ["cmd"]` in the forwarded tool definition. The `shell` tool uses
`command` (string), not `cmd`.

**When it occurs**: Model confuses exec_command with the shell tool, writes
`{"command": "ls -la"}` instead of `{"cmd": "ls -la"}`. The `cmd` field
defaults to empty string, exec runs a blank command, produces no output or
an error.

**Proxy-side detection**: In outgoing function_call arguments for exec_command:
`json.loads(args).get("cmd", "") == ""` AND `json.loads(args).get("command")`
is a non-empty string.

**Fixable?** YES — rename `command` → `cmd` in arguments. Zero risk.

**Evidence**: Not yet directly observed in test logs (the forwarded request
shows `"cmd": "ls -F"` correctly). But the schema dual — exec_command uses
`cmd`, shell uses `command` — is a known confusion source. Worth watching the
telemetry.

**Priority: LOW-MEDIUM** — guard against future regression, easy when it fires.

---

### E-2 — exec_command result microcompaction truncates error signal

**Problem**: `_microcompact_old_tool_results` truncates old exec outputs to
save context tokens. If a sandbox denial occurred several turns ago and the
model retried, the sandboxed output might be truncated, removing the denial
signal. The `SandboxEscalationManager` detection then misses it.

**Proxy-side mitigation**: The escalation check runs AFTER microcompaction.
Sandbox denial signals are short strings that should survive most truncation
budgets, but worth confirming by checking what token limit microcompaction
applies to exec outputs.

**Evidence**: Not yet observed, but the order-of-operations risk is real.

**Priority: LOW** — investigate by checking microcompaction budget in
`_microcompact_old_tool_results`.

---

### WS-1 — web_search result truncation mismatch

**Problem**: The proxy injects `[Proxy auto-truncated: N chars removed]`
markers into web search results. The model occasionally tries to reference
content past a truncation boundary.

**Proxy-side action**: Already handled via `_microcompact_old_tool_results`.
No new action needed.

**Priority: NONE** — already handled.

---

### RT-1 — request_permissions denial → advisory injection already exists

**Problem**: `request_permissions` tool returns empty permissions (denial).
Already handled by `_classify_request_permissions_output` in
`qz_native_tool_output.py` which classifies as
`request_permissions_denied_or_unavailable` and injects an advisory.

**Priority: NONE** — already handled.

---

## Data collection plan

To gather evidence for AP-1, AP-2, AP-3, and E-1 at scale, add a telemetry
event in `_apply_patch_operation_to_patch_text` that fires when a diff field
is empty after stripping, or when unprefixed context lines are detected. Run a
medium-length qz-codex session (20–50 turns) on a real codebase and count
events.

Specifically:

```python
# In _normalize_apply_patch_operation_for_codex or _apply_patch_operation_to_patch_text
diff_after_strip = _strip_unified_diff_headers(diff)
if not diff_after_strip.strip():
    telemetry.emit("apply_patch_empty_diff_after_strip", {"path": path})
crlf_lines = sum(1 for l in diff_after_strip.splitlines() if l.endswith("\r"))
if crlf_lines:
    telemetry.emit("apply_patch_crlf_diff", {"path": path, "count": crlf_lines})
unprefixed = [l for l in diff_after_strip.splitlines()
              if l and not l.startswith((" ", "+", "-", "@", "*"))
              and not l.startswith("@@")]
if unprefixed:
    telemetry.emit("apply_patch_unprefixed_context_lines",
                   {"path": path, "count": len(unprefixed), "sample": unprefixed[0][:60]})
```

Run the same instrumentation for exec_command `command`→`cmd` confusion:

```python
# In completed_call_decision for exec_command
args = json.loads(call.get("arguments") or "{}")
if not args.get("cmd") and args.get("command"):
    telemetry.emit("exec_command_wrong_field", {"has_command": True})
```

After 50+ turns of real usage, check telemetry for event counts. Implement
whichever patterns exceed threshold (suggest: >2 occurrences in 50 turns).

---

## Implementation order (recommended)

1. **AP-1** (empty diff after strip) — intercept in proxy outgoing path,
   return precise error immediately. No Codex round-trip.
2. **AP-2** (unprefixed context lines) — auto-prefix + correction note via
   `CorrectionTracker`. Implement after AP-1 since it requires line-level
   scanning that AP-1's empty-diff check establishes.
3. **AP-4** (context mismatch advisory) — extend native signal classifier with
   `"Failed to find expected lines"` pattern → advisory: re-read before diff.
4. **E-1** (cmd/command rename) — only if telemetry confirms it occurs.
5. **AP-3** (CRLF normalisation) — add to
   `_normalize_apply_patch_operation_for_codex`, zero-risk line.
