# Signal/Feedback Subsystem Plan

Date: 2026-05-14
Status: active — Phase 0-3 implemented.

Tracks the refactoring of QuantZhai's tool coercion, native tool-output
classifiers, and runtime advisory signals into a small generic feedback layer.

Cross-references: #41 (signal surface map), #42 (this refactor), #28 (sandbox
telemetry), #9 (empty-answer repair), #8 (compaction signals), #40 (watchdog).

---

## Problem

QuantZhai already has multiple working feedback paths:

```text
tool coercion error injection         qz_tools.py / qz_proxy_tools.py
apply_patch argument coercion         qz_tool_apply_patch.py
unknown/dropped tool error injection  qz_proxy_tools.py
native tool-output classifiers        qz_native_tool_output.py
hop budget signal                     qz_responses_stream.py
empty-answer repair                   qz_responses_stream.py
operator telemetry events             qz_telemetry.py + qz_responses_stream.py
```

These are variants of the same loop:

```text
observe event/state  ->  classify  ->  apply policy
  ->  render feedback/telemetry/public item
  ->  inject or expose through the safest channel
```

Currently each path has its own ad-hoc types. A small generic layer makes
future signals easier to add without bolting a one-off branch into a
gravity-well module.

---

## Goals

1. A shared `proxy/qz_feedback.py` leaf module with core data types.
2. Native tool-output classifier wrapped in the generic type.
3. Tool coercion renderer available in both old and new APIs.
4. No behaviour changes — compatibility preserved everywhere.
5. Stream/runtime signals migrated later when timing is right.

---

## Non-goals

```text
- No memory_domain policy changes.
- No qz_* injection into forwarded /v1/responses bodies.
- No durable memory or active memory tools.
- No large abstract framework.
- No merging of coercion and native-output classification into one unsafe path.
```

---

## Phase 0: Core types (done — #42)

`proxy/qz_feedback.py` — leaf module, no proxy imports:

```python
FeedbackVisibility  enum: MODEL / OPERATOR / BOTH
FeedbackChannel     enum: FUNCTION_CALL_OUTPUT / TELEMETRY / INSTRUCTIONS /
                          TURN_HARNESS / FUTURE_STATE
SignalDecision      frozen dataclass: event_type, payload, visibility,
                                      channel, confidence
render_coercion_error(call, message) -> dict
  same output shape as synthesize_tool_error_result()
```

## Phase 1: Native tool-output classifier wired (done — #42, #71)

`proxy/qz_native_tool_output.py` provides:

```python
classify_native_tool_output_signals(input_items) -> list[SignalDecision]
```

Delegates to `classify_native_tool_outputs()` and wraps each result in a
`SignalDecision` with `OPERATOR / TELEMETRY`. No model injection.

`qz_request_router.py` now calls `classify_native_tool_output_signals()` in its
hot path and emits telemetry via the structured signals.

`classify_native_tool_outputs()` preserved for compatibility.

## Phase 2: Tool coercion unification (done — #42, then unified)

`proxy/qz_feedback.py` owns `render_coercion_error()` as the single canonical
implementation of the coercion error `function_call_output` shape.

`proxy/qz_tools.py` keeps `synthesize_tool_error_result()` as a compatibility
wrapper that **delegates** to `render_coercion_error()`. Both produce byte-for-byte
identical output. Callers in `qz_proxy_tools.py` are unchanged — they continue
to import from `qz_tools`.

Invariant: `synthesize_tool_error_result(call, msg) == render_coercion_error(call, msg)`.
Tested in `tests/test_qz_tools.py::SynthesizeToolErrorResultTests::test_delegates_to_render_coercion_error`
and `tests/test_qz_feedback.py::RenderCoercionErrorTests::test_output_shape_matches_synthesize_tool_error_result`.

## Phase 3: Tests (done — #42)

`tests/test_qz_feedback.py` — enum values, SignalDecision construction,
render_coercion_error output shape matches synthesize_tool_error_result.

`tests/test_qz_native_tool_output.py` — new class
`SignalWrapperTests` covers classify_native_tool_output_signals return type,
visibility, channel, confidence, and empty input.

---

## Phase 3b: Sandbox advisory model injection (done)

`proxy/qz_request_router.py` adds:

```python
_SANDBOX_READONLY_ADVISORY_TEXT   # plain-text advisory string constant
RequestRouter._model_visible_native_advisories(signals) -> list[dict]
```

When `classify_native_tool_output_signals()` returns a `tool_sandbox_denied`
signal with `classifier=sandbox_denied_readonly_fs` and `confidence=high`, the
router appends a `function_call_output` advisory item to `body["input"]` before
forwarding to the upstream. The advisory uses `render_advisory_output()` from
`qz_feedback.py` (plain text, not a JSON error shape).

Telemetry separation:
- `_emit_signal_decisions()` emits `tool_sandbox_denied` (unchanged).
- `_model_visible_native_advisories()` renders advisory items only; no telemetry.
- The injection site in `proxy_json_api` emits `tool_sandbox_advisory_injected`
  for each advisory appended.

Bounds:
- Only `sandbox_denied_readonly_fs` / high-confidence triggers advisory.
- `connection_refused` remains telemetry-only.
- Deduplicated per call_id within a single request.
- No persistent state, no SQLite, no BrainCase, no cooldown database.
- No automatic retry or escalation.

Tests: `tests/test_qz_request_router.py` — `SandboxAdvisoryHelperTests`
(helper unit tests) and `SandboxAdvisoryInjectionTests` (classify→advise integration).

---

## Phase 3c: Coercion telemetry enrichment (done)

`coercion_succeeded` and `coercion_failed` telemetry events in both
`proxy/qz_request_router.py` (non-streaming proxy path) and
`proxy/qz_responses_stream.py` (streaming path) now carry enriched payloads:

```text
tool            tool/function name (unchanged)
upstream_name   same as tool — the upstream function name
call_id         call_id from the function_call item
correction_applied  True when coerce() succeeded and reran
error_summary   truncated error message (≤200 chars, failures only)
request_id      scoped to the request (added by emitter)
source          "tool_adapter" — identifies the coercion origin
```

`source = "tool_adapter"` is the key new field. It lets operators filter
coercion events from other telemetry without tool name pattern matching.

Tests: `tests/test_qz_responses_stream.py` (`CoercionTelemetryStreamingTests`)
— updated to assert `source == "tool_adapter"` on both succeeded and failed events.

---

## Phase 4 (deferred): Stream/runtime signals

Port these when ready:

```text
empty_answer_repair_started/completed/failed   qz_responses_stream.py
reasoning_only_aborted                         qz_responses_stream.py
reasoning_only_completed_without_answer        qz_responses_stream.py
stream terminal classification                 qz_responses_stream.py
compaction/stream hang watchdog (#40)          TBD
```

Migration path: emit telemetry via `SignalDecision` then wrap into the existing
`REQUEST_LIFECYCLE_EVENT_TYPES` bus. No behaviour change on first pass.

## Phase 5 (deferred): Additional model injection helpers

Connection refused and other signals remain telemetry-only. When broader
model injection is ready:

```python
# Example future shape — not yet implemented
inject_advisory_signal(call_id, message) -> dict  # function_call_output shape
```

All injected items must be advisory (model may ignore them). No auto-escalation
or retry. No changes to sandbox policy.

---

## Compatibility rules

```text
1. synthesize_tool_error_result() in qz_tools.py is never removed without
   updating all callers (qz_proxy_tools.py) and tests (test_qz_tools.py).

2. classify_native_tool_outputs() in qz_native_tool_output.py is never removed
   without updating qz_request_router.py.

3. The function_call_output shape {"type": ..., "call_id": ..., "output": ...}
   is never changed without updating test_qz_tools.py.

4. SignalDecision is frozen. Add fields only when all callers are updated.
```
