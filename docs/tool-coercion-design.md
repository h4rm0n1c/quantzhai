# Tool Coercion System Design

Date: 2026-05-11

## Problem

Every tool that needs to handle malformed model output is currently doing it
ad-hoc in its own code:

- `apply_patch` — ~150 lines of coercion logic in `qz_tool_apply_patch.py`:
  sibling-patch promotion, partial-envelope emission for bare operations,
  path extraction from legacy envelopes, specific error messages per failure mode.
- `web_search` — lenient argument parsing with in-band error results in
  `qz_tool_web.py`: defaults for missing fields, specific error strings per
  bad action type.
- Dropped tools — no feedback at all. Model calls a tool that was removed from
  the declaration list; the call silently falls through or hangs.
- Unknown tools — no feedback at all. Same outcome as dropped.

Every new tool added will need its own version of this logic. This is the
wrong shape. Coercion is a registry-level concern, not a per-tool concern.

---

## Three distinct problems

### 1. Argument coercion
The model emitted a structurally wrong function_call (missing field, wrong key
name, alternate format). The proxy should try to fix it before executing or
routing. If it can be fixed, proceed with the corrected call. If not, surface
a specific error.

Examples:
- apply_patch: `{"operation": {"type": "create_file", "path": "x.py"}, "patch": "content"}`
  → promote `patch` to `operation.diff`, proceed normally.
- web_search: `{"action": "search"}` with no `query`
  → already caught in-band and returns `"Missing query for search."`.

### 2. Coercion failure feedback
When coercion cannot recover the call, inject a specific error result back to
the model immediately. The error should say what was wrong and what to fix.

Currently apply_patch does this via partial Codex envelope (Codex's verifier
rejects it with a specific message) or via an assistant message with a reason
string. Web_search does it via `{"ok": false, "error": "..."}` in the tool
result payload. These mechanisms are good but inconsistent and bespoke.

The unified mechanism: synthesize a `function_call_output` with a specific
error string and inject it into the conversation as a tool result. The model
sees it on the next turn and can retry with corrected arguments.

### 3. Dropped and unknown tool feedback
When a function_call arrives for a tool that was not declared to the model
(either dropped during request normalisation or completely unrecognised), the
current proxy silently passes it to Codex or drops it. The model gets no
feedback, the conversation hangs or errors out opaquely.

This is not an argument coercion problem — it is an identity rejection problem.
The fix is at the routing layer, not the tool layer, and requires no per-tool
code.

---

## Design

### `ToolCoercionResult`

A new dataclass returned by the `coerce()` method:

```python
@dataclass
class ToolCoercionResult:
    corrected_arguments: str | None = None   # fixed JSON, ready to execute
    error_message: str | None = None         # specific reason to return to model
```

Exactly one field is set:
- `corrected_arguments` is set → coercion succeeded; re-run with this call
- `error_message` is set → coercion failed; inject this as an error tool result

### `coerce()` method on `ToolLifecycleSpec`

Add an optional `coerce(arguments: str) -> ToolCoercionResult` method to the
tool adapter/executor protocol. Default implementation returns a generic
failure error (see Generic fallback below).

Per-tool implementations:
- `apply_patch`: migrate existing coercion logic from `qz_tool_apply_patch.py`
  into this method. The sibling-patch promotion, path extraction, partial-envelope
  construction, and per-failure-mode error strings all move here.
- `web_search`: the existing lenient parsing is already mostly correct. The
  `coerce()` method handles JSON parse failure and completely unrecognised
  argument structures; the in-band `{"ok": false, "error": ...}` path stays
  for runtime validation during execution.
- Future tools: implement `coerce()` to get argument recovery and specific
  feedback for free. Omit it and get the generic fallback.

### Registry dispatch in `completed_call_decision()`

Current flow:
```
function_call arrives
  → proxy_local?      → execute
  → known adapter?    → convert shape, Codex executes
  → else              → pass raw call to Codex (silent failure risk)
```

Proposed flow:
```
function_call arrives
  → name in dropped_tools?     → inject dropped-tool error result
  → proxy_local?               → coerce() → execute (or inject error)
  → known adapter?             → coerce() → convert shape (or inject error)
  → known Codex-native name?   → pass through (exec_command, write_stdin, etc.)
  → else                       → inject unknown-tool error result
```

The `dropped_tools` set is already computed per request in
`ToolNormalizationReport.dropped`. It needs to be threaded into the routing
context so `completed_call_decision()` can see it.

The `known Codex-native name` allowlist is a small constant set of names that
the proxy knows Codex handles natively. Initially:
`{"exec_command", "write_stdin", "shell_command", "computer"}`.

### Generic fallback

For tools that do not implement `coerce()`, and for the unknown-tool case, the
registry synthesizes:

```
Tool call for '{name}' could not be completed by the proxy.
Check your arguments and retry, or use a different tool.
```

This is a `function_call_output` injected as a tool result, not an assistant
message. The model sees it as a real result and can act on it.

### Dropped-tool error format

```
Tool '{name}' is not available in this session: {reason}.
```

Where `reason` comes from the `ToolNormalizationReport.dropped` entry. Current
reasons include `"write_stdin(no live exec session)"` — this becomes the
human-readable part of the error.

---

## Migration plan

### Phase 1 — Infrastructure (no behaviour change)

1. Add `ToolCoercionResult` dataclass to `qz_tools.py`.
2. Add `coerce(arguments: str) -> ToolCoercionResult` to `ToolLifecycleSpec`
   with a generic-fallback default implementation.
3. Add `synthesize_tool_error_result(call, message)` helper that builds a
   `function_call_output` with the error string.
4. Add `CODEX_NATIVE_TOOL_NAMES` constant set to `qz_tools.py`.

### Phase 2 — Registry routing

5. Thread `dropped_tool_names` into the routing context (extend
   `ProxyToolExecutionContext` or add a parameter to
   `completed_call_decision()`).
6. Update `completed_call_decision()` to:
   - Check dropped list first → `synthesize_tool_error_result`
   - Call `coerce()` before proxy-local execution and adapter routing
   - Check `CODEX_NATIVE_TOOL_NAMES` before falling through to unknown
   - Unknown → `synthesize_tool_error_result`

### Phase 3 — Per-tool migration

7. `apply_patch`: implement `coerce()` using existing logic from
   `_parse_apply_patch_arguments`, `_extract_partial_operation`, and
   `_describe_args_failure`. The partial-envelope path stays in the
   protocol-adapter output conversion for now (it uses Codex's verifier as
   the feedback mechanism, which is valid).
8. `web_search`: implement `coerce()` for JSON parse failures and completely
   unrecognised argument structures. Leave the in-band
   `{"ok": false, "error": ...}` runtime path in place.
9. `qz_probe` test executor: add a trivial `coerce()` that passes through
   unchanged — proves the interface without needing recovery logic.

### Phase 4 — Stream path

10. The `ResponsesStreamRuntime` tool-call handling calls
    `proxy_tool_registry.completed_call_decision()`. Once Phase 2 is in,
    the stream path inherits dropped-tool and unknown-tool feedback
    automatically. Verify with new stream fixtures.

### Phase 5 — Tests

11. Unit tests for `ToolCoercionResult` and the generic fallback.
12. Unit test for dropped-tool injection.
13. Unit test for unknown-tool injection.
14. SSE fixture for dropped-tool feedback in the stream path.
15. SSE fixture for unknown-tool feedback in the stream path.
16. Migrate existing apply_patch coercion unit tests to test the new interface.

---

## What this does NOT change

- The `apply_patch` partial-envelope path (using Codex's verifier as the
  feedback mechanism). That works and stays. The `coerce()` migration is
  for the argument-repair part, not the envelope emission.
- The `web_search` in-band `{"ok": false, "error": ...}` runtime path.
  That is correct and stays. `coerce()` handles pre-execution structural
  failures, not runtime errors.
- The `ToolLifecycleSpec` continuation_hops and lifecycle_stages. Those are
  orthogonal to coercion.

---

## Relationship to existing docs

- `docs/apply-patch-compatibility-plan.md` — the apply_patch coercion work
  this system generalises. Phase 3 above migrates it.
- `docs/proxy-capability-roadmap.md` — the "Unsupported tool policy" open
  question in Known Blind Spots. Phase 2 above answers it.
- `proxy/qz_tools.py` — the registry types this design extends.
- `proxy/qz_proxy_tools.py` — `completed_call_decision()` is the primary
  change point.
