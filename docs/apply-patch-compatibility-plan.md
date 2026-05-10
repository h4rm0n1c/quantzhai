# apply_patch Compatibility Plan

Date: 2026-05-10 (Phases 1–4 implemented and live-validated)

## Status

All five phases implemented, committed, documented, and live-validated.

Implementation commits:
- Phase 1 — `8e0af06` Coerce sibling patch field next to operation
- Phase 2 — `69e28ad` Emit partial apply_patch envelope on coercion failure
- Phase 3+4 — `e40c976` Extract path from legacy envelopes; pin rename-no-hunk
- Phase 5 — this commit

Validation: 8 of 8 worst-failing create prompts from the original synthetic
fuzz now succeed (was 0 of 5). Multi-step tasks complete end-to-end. See
`var/captures/apply-patch-revalidation-2026-05-10/report.md` for the
post-implementation evidence.

Original-state notes from before the fuzz are preserved in section "Pre-fuzz
plan" at the bottom for archival.

Evidence reports:

- `var/captures/apply-patch-fuzz-2026-05-10/report.md` — synthetic prompts (25)
- `var/captures/apply-patch-fuzz-realcode-2026-05-10/report.md` — real-codebase prompts (15)

---

## Problem Summary

QuantZhai's `apply_patch` adapter (`proxy/qz_tool_apply_patch.py`) has two
classes of issue:

1. **Broken error feedback path.** When the local model emits an `apply_patch`
   `function_call` whose arguments fail coercion, `_invalid_apply_patch_call_message`
   returns an `assistant` message rather than a tool result. The model's call
   is orphaned: Codex displays the message, no tool result reaches the model,
   and conversation state becomes inconsistent. The model retries (sometimes
   7+ times) but with no actionable feedback the retries do not converge.

2. **Coercion gaps.** The model emits a stable, identifiable variant shape that
   the current coercion does not handle: a sibling `patch` field next to
   `operation` instead of `operation.diff`. Coercion is also strict about
   missing destinations on rename without a content hunk, leading to silent
   no-ops in custom-output mode.

Two minor gaps round it out: a legacy `patch`-string variant with no top-level
`path`, and rename operations with no hunk (which Codex's V4A custom grammar
does not accept).

---

## Confirmed Qwen Failure Shapes

From 56 calls across two sessions with model
`Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL`:

### Shape A — sibling `patch` field with content (~13 occurrences)

```json
{"operation":{"type":"create_file","path":"hello.py"},
 "patch":"def greet():\n    return 'hello'\n"}
```

The model puts the operation correctly under `operation` but emits the file
content under a sibling `patch` key. Currently rejected because:

- `data.operation` coercion finds no `diff` → returns `None`
- Flat fallback over `data` → same
- Legacy `patch + path` fallback → fails because `path` is nested inside `operation`, not at top level

**Coercible.** Promote `data.patch` into `operation.diff` when `operation` lacks one.

Variant: in some payloads the `patch` value is double-quoted (the model wrapped
the content in extra `"` characters):

```json
"patch":"\"# Changelog\n\n## v0.1.0\n..."
```

Plumb this through verbatim. The resulting file would have stray quotes; the
model self-corrects after re-reading the file. Not the proxy's job to repair
content.

### Shape B — bare operation, no content anywhere (~12 occurrences)

```json
{"operation":{"type":"create_file","path":"CHANGELOG.md"}}
{"operation":{"type":"update_file","path":"streamlinkbgm/README.md"}}
```

The model omits content entirely. Cannot be coerced — there is no content to
recover. **Requires the error feedback path** so the model is told what is
missing and can retry with the diff included.

### Shape C — legacy `patch` string with missing path (1 occurrence)

```json
{"patch":"--- a/quote.py\n+++ b/quote.py\n@@ ..."}
```

Top-level `patch` is present, top-level `path` is not. The proxy's legacy
fallback requires both. **Coercible** by extracting the path from the unified
diff headers when `path` is absent, or by using a more specific error message
if extraction fails.

---

## Other Observed Bugs

### Rename without hunk silently no-ops (custom mode)

The model emits a clean rename shape:

```json
{"operation":{"type":"rename_file","path":"a.py","new_path":"b.py"}}
```

The proxy's coercion accepts it. In custom-output mode,
`_apply_patch_operation_to_patch_text` then raises `ValueError` because
`move_file` requires a non-empty diff for the V4A custom grammar
(`*** Update File:` + `*** Move to:` needs a hunk). The current handler
catches the ValueError and routes to the broken assistant-message path, so the
rename never reaches Codex.

In native mode this case works. The asymmetry is an issue.

**Fix options:** (a) synthesize a minimal context hunk by reading the source
file at proxy time — but the proxy is not a filesystem actor and shouldn't
become one. (b) Switch the operation to native output style for hunkless
renames. (c) Inject a placeholder hunk with one synthesized context line
(`@@\n` only). Option (b) is cleanest if the request's tool policy allows it;
otherwise (c).

### Model sometimes refuses apply_patch entirely

R14 (function signature update) produced 0 apply_patch calls. The model
produced no patch attempt and no shell action. Hard to distinguish from a
one-off without more samples. Track but do not fix without further evidence.

---

## Suspected Coercion Gaps That Were NOT Confirmed

The pre-fuzz plan listed several speculative model variants. Across 56 calls,
none were observed:

- ❌ `modify_file`, `edit_file`, `patch_file` type aliases — model uses
  canonical types (`create_file`, `update_file`, `delete_file`, `move_file`,
  `rename_file`)
- ❌ `add_file`, `new_file` aliases for create
- ❌ `content` or `text` field as alias for diff (the alias is `patch`, see Shape A)
- ❌ List-shaped `diff` field
- ❌ Top-level flat dict (no `operation` wrapper)
- ❌ `destination` aliases beyond what is already supported (the model uses
  `new_path` as in Shape A, which is already in the alias list)

**Decision:** drop these from the priority list. Track as TODO entries to
revisit if a different model or a longer fuzz run surfaces them. Do not write
coercion for unobserved variants.

---

## Implementation Plan

Phased, ordered by safety and value. Each phase produces a small, testable
change.

### Phase 1 — Sibling-`patch` coercion ✅ implemented (`8e0af06`)

Smallest, safest change. Closes ~13 of 25 missing-diff failures.

**Change:** in `_coerce_apply_patch_operation` (or upstream in
`_parse_apply_patch_arguments`), when:

- `data.operation` is a dict
- `operation` lacks a string `diff`
- `data.patch` is a non-empty string

then promote `data.patch` into `operation.diff`. Run the result through the
existing `_strip_unified_diff_headers` path so file-header noise is stripped.

**Test plan:**
- Add fixture `tests/fixtures/sse/qwen_create_file_sibling_patch.raw` reproducing the live shape.
- Add fixture `tests/fixtures/sse/qwen_update_file_sibling_patch_with_unified_headers.raw` for the B02 variant.
- Add a test in `test_apply_patch_adapter.py` asserting the operation is now coerced cleanly.
- Add a stream-replay test in `test_qz_responses_stream.py` for both fixtures.

**Smoke:** `tests/smoke_apply_patch_proxy.py`, `tests/smoke_apply_patch_codex_exec.py`.

### Phase 2 — Error feedback path ✅ implemented (`69e28ad`)

Biggest lever. Closes ~12 bare-operation failures and any future variants.

**Implementation differs from the original Option B plan.** During implementation
we found that Codex's V4A verifier already produces specific errors (e.g. "Add
File requires content lines") and Codex synthesizes a `custom_tool_call_output`
back to the model on the next turn. So instead of synthesizing a tool result
inside the proxy's SSE stream, the proxy now emits a **partial Codex envelope**
when coercion fails — `*** Add File: <path>` with no content, etc. Codex's
verifier then produces a specific error and the model gets actionable feedback
through Codex's normal flow. No proxy-side stream synthesis required.

When the args cannot yield a usable envelope at all (no path, no destination
for moves), the proxy still falls back to an assistant message — but that
message now carries a specific reason describing what was missing.

**Change:** replace `_invalid_apply_patch_call_message` (and its callers in
`_function_call_to_apply_patch_call` / `_function_call_to_custom_apply_patch_call`)
with a synthesized `apply_patch_call` + `apply_patch_call_output` pair emitted
in the same SSE stream. The output carries a specific error message keyed to
the failure mode:

- Missing `diff` for `create_file` →
  `"create_file requires the full file content as operation.diff. The model emitted operation.path but no diff."`
- Missing `diff` for `update_file` →
  `"update_file requires a V4A diff hunk in operation.diff with lines starting +, -, or space."`
- Missing destination for `move_file` →
  `"move_file requires a destination via operation.destination, new_path, to, move_to, or target_path."`
- Unknown operation type →
  `"unknown operation type {type!r}. Supported: create_file, update_file, delete_file, move_file, rename_file."`
- Legacy `patch` string with no `path` →
  `"top-level 'patch' string requires a top-level 'path'. Provide both, or use the operation wrapper."`

The synthesized output goes back to Codex in the same response. On the next
turn, Codex's normal flow includes the previous output in the input. The model
sees a real tool result with actionable text and can retry with the missing
field.

This is **Option B** from the pre-fuzz plan. The fuzz data confirms why
**Option A** (passthrough + Codex's generic error) would not work: the model
already retries multiple times with the same broken shape. It needs specific
feedback to converge.

**State implications:** the synthesis happens entirely within one outgoing SSE
stream. No cross-turn state tracking required. The proxy emits both items, the
stream completes, Codex consumes both, and the next request from Codex
naturally carries them as input items.

**Test plan:**
- Update existing `test_golden_invalid_apply_patch_*` tests in
  `test_qz_responses_stream.py` to assert the new shape (apply_patch_call +
  apply_patch_call_output pair) instead of the broken assistant-message shape.
- Add fixtures `tests/fixtures/sse/qwen_create_file_bare_operation.raw` and
  `tests/fixtures/sse/qwen_update_file_bare_operation.raw`.
- Add a test that the error message in the output matches the failure-mode key.

**Streaming risk:** the proxy already emits multi-item streams (web_search
hops). The apply_patch failure injection follows the same pattern. Validate
against `docs/responses-stream-tool-state-contract.md` and add a row to the
state table for "apply_patch invalid arguments".

### Phase 3 — Move-without-hunk handling ✅ implemented (`e40c976`, partly subsumed by Phase 2)

Closes the silent no-op for clean renames in custom-output mode.

**Change:** in `_apply_patch_operation_to_patch_text`, when `operation_type ==
"move_file"` and `diff` is empty:

- If the request's `apply_patch_output_style` is `native` (or can be promoted
  to native): keep native output. Native moves don't need a hunk.
- Otherwise: emit a minimal envelope of
  `*** Begin Patch\n*** Update File: <path>\n*** Move to: <destination>\n@@\n*** End Patch\n`
  and let Codex's parser handle the hunkless rename. If Codex rejects it,
  Phase 2's error feedback path catches the failure with a specific message.

**Test plan:**
- Fixture `tests/fixtures/sse/qwen_rename_no_hunk.raw`.
- Test that the resulting custom envelope or native operation passes through to Codex.

### Phase 4 — Legacy `patch`-string with missing path ✅ implemented (`e40c976`)

Rare (1/56) but easy.

**Change:** when `data.patch` is a string and `data.path` is missing, attempt
to extract the path from the unified-diff headers (`+++ b/<path>` or
`+++ <path>`). If extraction fails, route to Phase 2's error feedback with a
specific message.

**Test plan:**
- Fixture `tests/fixtures/sse/qwen_legacy_patch_missing_path.raw`.
- Test extraction success and extraction failure paths.

### Phase 5 — Documentation sync ✅ complete

This doc, `docs/patch-tool-roadmap.md`, and the state contract in
`docs/responses-stream-tool-state-contract.md` have been updated to reflect
Phases 1–4. Revalidation evidence is in
`var/captures/apply-patch-revalidation-2026-05-10/report.md`.

Revalidation summary: 8 of 8 worst-failing creates now succeed, vs 0 of 5
before. Detailed comparison and per-prompt outcomes in the revalidation
report.

---

## Out of Scope

The following are mentioned for clarity but **not** part of this plan:

- **Multi-operation patch envelopes** in a single `apply_patch` call. The
  current schema is one-operation-per-call. Codex's grammar can accept
  multi-op envelopes, but the model can also make multiple sequential calls
  per turn — which is what we observed it doing. No evidence the single-op
  schema is causing failures.
- **Local patch executor.** Codex remains the writer. QuantZhai is a protocol
  adapter, not a filesystem actor. (See `docs/patch-tool-roadmap.md` Phase 3.)
- **Binary-file patches.** Not supported and not in demand.
- **Coercion for unobserved variants** (`modify_file`, `content`/`text` etc.).
  Track as TODOs; do not implement until evidence appears.
- **Fix for "model refuses apply_patch entirely"** (R14 case). Single
  occurrence; not enough signal to act on.

---

## Test/Fixture Inventory

Fixtures to add under `tests/fixtures/sse/` (raw SSE replays redacted from
live captures):

| Fixture | Source request | Purpose |
|---------|----------------|---------|
| `qwen_create_file_sibling_patch.raw` | A04 capture | Phase 1 coercion |
| `qwen_create_file_bare_operation.raw` | A01 capture | Phase 2 error path |
| `qwen_update_file_sibling_patch_with_unified_headers.raw` | B02 capture | Phase 1 with header stripping |
| `qwen_update_file_bare_operation.raw` | E01 capture | Phase 2 error path |
| `qwen_rename_no_hunk.raw` | R12 capture | Phase 3 |
| `qwen_legacy_patch_missing_path.raw` | B08 capture | Phase 4 |
| `qwen_rename_file_new_path_alias.raw` | D03 capture | Pin existing pass behaviour |
| `qwen_create_file_with_diff_clean.raw` | E02 capture | Pin existing pass behaviour |

Redaction guidance: replace timestamps with `4102444800`, replace session and
response IDs with deterministic placeholders, ensure no local paths leak.

Existing `test_golden_invalid_apply_patch_*` tests need updates after Phase 2
(the broken assistant-message assertions become obsolete).

---

## Pre-fuzz plan (archived)

The investigation phase of this plan is complete. The pre-fuzz speculation
about coercion gaps (`modify_file`, `content`/`text`, list-shaped diff, etc.)
is preserved in git history at commit `612617f`. This document is now the
implementation plan, not the investigation plan.
