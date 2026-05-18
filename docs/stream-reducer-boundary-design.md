# Stream Reducer Boundary Design

Date: 2026-05-16

Status: design doc — Slice 2A. No runtime code changed.

---

## 1. Purpose

`proxy/qz_responses_stream.py` is 2000+ lines and contains mixed concerns:
network I/O, SSE parsing, state tracking, decision-making, tool execution,
output rendering, telemetry, and loop control. The Slice 1/1.1 work extracted
per-hop state into `StreamHopState`, giving the loop an explicit state object.

This document defines the **safe extraction boundary** for the next seam: a
future **pure stream state reducer** that consumes SSE event observations and
returns **decision packets** — without performing I/O, rendering, tool execution,
or side effects.

The reducer should be:

```text
input:  SSE event context + current StreamHopState + outer state summary
output: a decision packet (what to do next)
rules:  no socket reads, no writes, no SSE rendering,
        no tool execution, no request mutation, no DB/storage
```

`qz_responses_stream.py` remains the **side-effect owner** — it reads the
network, renders SSE output, executes tools, updates state, and drives the
loop. The reducer only decides.

---

## 2. Current architecture

```text
qz_responses_stream.py   (side-effect owner, 2000+ lines)
  owns HTTP/backend stream loop
  reads SSE lines from upstream
  parses events via parse_sse_event_lines()
  updates StreamHopState (hs.*) as events arrive
  makes inline decisions about suppression/injection/continuation
  executes proxy-local tool calls
  writes transformed SSE output to client
  emits telemetry events via self._emit()
  handles continuation hops (loop control)
  assembles public_trace
  emits terminal result

StreamHopState (Slice 1)
  per-hop mutable state only
  19 fields covering: event buffer, injection flags, output tracking,
    reasoning-only detection, tool call state, watchdog state

qz_stream_terminal.py
  pure StreamObservation accumulation
  pure classify_stream_terminal() — no I/O
  accumulate() updates StreamObservation from event type

qz_stream_watchdog.py
  per-hop StreamWatchdogState
  pure predicates: should_trigger_no_output_timeout(),
    should_trigger_terminal_timeout()
  mark_* methods update state as events arrive

qz_tool_lifecycle.py
  StreamToolCallState: accumulates function_call stream events
  completed_tool_item() returns assembled call when done
  abort_reason() checks timeout/delta limits

qz_streaming.py
  SSE block construction, event helpers
  pure functions, no I/O

qz_sse.py
  SSE transform pipeline
  transform_sse_event() rewrites reasoning summaries, output indices
```

The watchdog and terminal modules are already near-pure. The opportunity is to
extract the **decision logic** currently inline in the hop's while loop.

---

## 3. What must stay in qz_responses_stream.py

The following must not move into the reducer:

| Concern | Reason |
|---|---|
| Socket reads (`resp.readline()`) | I/O ownership |
| Raw log writes | File I/O |
| SSE chunk writing (`self._write_chunk()`) | Output rendering |
| `transform_sse_event()` pipeline | Output rendering |
| `_write_transformed_chunks()` | Output rendering |
| Proxy-local tool execution | Network + side effects |
| `public_trace.append()` | Shared state mutation |
| `sequence` increment/tracking | Shared state mutation |
| `continuation_hop` loop control | Loop mechanics |
| `hop_body` / `working_body` mutation | Request body ownership |
| `self._emit(event_type, ...)` telemetry calls | Telemetry emission |
| Response close / `resp.close()` | Resource management |
| Stream drain for final usage | Network I/O |
| Exception propagation boundaries | Error handling |

The reducer should decide what action to take. `qz_responses_stream.py` should
perform it.

---

## 4. Decision inventory — what can become reducer decisions

These are inline logic points currently embedded in the hop while loop. Each
is a candidate for extraction into the reducer. They are listed in call-site
order; the first natural extraction targets are at the top.

### 4.1 no_output_timeout

**Trigger**: `should_trigger_no_output_timeout(hs.watchdog_state, t)` returns True.
**Decision**: terminate loop, emit no-output-timeout fallback.
**Current action**: call `self._finish_no_output_timeout(...)`, return.
**Reducer output**: `kind="finish_no_output_timeout"`.

### 4.2 terminal_timeout_after_output

**Trigger**: `should_trigger_terminal_timeout(hs.watchdog_state, t)` returns True.
**Decision**: terminate loop, emit terminal-timeout fallback.
**Current action**: call `self._finish_terminal_timeout_after_output(...)`, return.
**Reducer output**: `kind="finish_terminal_timeout"`.

### 4.3 function_call_stream_suppression

**Trigger**: `is_function_call_stream_event(event_type, payload)` returns True
  and the function_call arguments are not yet complete.
**Decision**: suppress this SSE line from forwarding to client.
**Current action**: `continue` (do not render).
**Reducer output**: `kind="suppress_event", suppress_reason="function_call"`.

### 4.4 function_call_abort (stuck/timeout)

**Trigger**: `hs.tool_call_state.abort_reason(now, timeout_s, delta_limit)` returns non-empty.
**Decision**: abort stuck function_call stream, emit fallback.
**Current action**: call `self._emit_private_tool_call_aborted(...)`, return.
**Reducer output**: `kind="abort_tool_call", reason=abort_reason`.

### 4.5 reasoning_only_abort

**Trigger**: `hs.reasoning_only_started_at is not None` and one of:
  - `_looks_like_reasoning_tool_artifact(hs.reasoning_only_sample)` → `"artifact_tool_payload"`
  - idle > `self.reasoning_only_timeout_s` → `"timeout"`
  - `hs.reasoning_only_chars > self.reasoning_only_char_limit` → `"char_limit"`
**Decision**: abort reasoning-only loop, emit reasoning-aborted fallback.
**Current action**: call `self._emit_reasoning_only_aborted(...)`, return.
**Reducer output**: `kind="abort_reasoning_only", reason=abort_reason`.

This is the **best first extraction target** for Slice 2B. The inputs are:
`hs.reasoning_only_*`, `hs.visible_output_text_seen`, `hs.assistant_item_seen`,
`hs.public_item_seen`, and three scalar thresholds from `self`. The outputs
are clearly bounded. Tests already cover all three abort paths.

### 4.6 proxy_local_terminal_suppression

**Trigger**: `is_terminal_stream_event(event_type)` is True AND `completed_call`
  is a proxy-local tool call (`self.proxy_tool_registry.is_proxy_local_call`).
**Decision**: suppress the terminal event from forwarding; the proxy-local
  execution will emit its own continuation.
**Current action**: `hs.event_lines = []; continue`.
**Reducer output**: `kind="suppress_event", suppress_reason=<tool_name>_terminal`.

### 4.7 repeated_read_signal_injection

**Trigger**: `decision.kind == "signal"` after `completed_call_decision`.
**Decision**: inject signal result into next-hop input; do not emit public
  Codex lifecycle events.
**Current action**: `hs.next_input.append(decision.signal_result); break`.
**Reducer output**: `kind="inject_signal"`.

### 4.8 tool_call_error_injection

**Trigger**: `decision.kind == "error"` after `completed_call_decision`.
**Decision**: inject error result into next-hop input; do not emit public lifecycle.
**Current action**: `hs.next_input.append(decision.error_result); break`.
**Reducer output**: `kind="inject_tool_error"`.

### 4.9 proxy_local_tool_execution

**Trigger**: `decision.kind == "proxy_local"`.
**Decision**: execute the tool locally, emit lifecycle events to client.
**Current action**: `self.proxy_tool_registry.execute(...)`.
**Note**: tool execution itself must stay in `qz_responses_stream.py`. The
  decision to execute is a reducer output; the execution is a side effect.
**Reducer output**: `kind="run_proxy_local_tool"`.

### 4.10 public_tool_forwarding

**Trigger**: `decision.kind == "public"` or native tool call.
**Decision**: forward tool result as public Codex lifecycle item.
**Current action**: emit `output_item.done`, add to `public_trace`.
**Reducer output**: `kind="forward_public_tool"`.

### 4.11 duplicate_response_start_suppression

**Trigger**: `event_type in {"response.created", "response.in_progress"}` AND
  `sent_response_start` is already True.
**Decision**: suppress duplicate.
**Current action**: `hs.event_lines = []; continue`.
**Reducer output**: `kind="suppress_event", suppress_reason="duplicate_response_start"`.

### 4.12 empty_answer_repair decision

**Trigger**: `response.completed` arrives, no visible output, reasoning chars > 0
  or a repair hop is already active.
**Decision**: start a repair hop, or give up and emit fallback.
**Current action**: mutate `working_body`, set `pending_repair_hop_index`, break.
**Reducer output**: `kind="start_empty_answer_repair"` or `kind="abort_reasoning_only"`.

### 4.13 terminal_event_forwarding

**Trigger**: `is_terminal_stream_event(event_type)` and other conditions.
**Decision**: forward terminal event, set `sent_terminal`, `sent_done`.
**Current action**: `_write_transformed_chunks()`, set flags.
**Reducer output**: `kind="emit_terminal"`.

### 4.14 carry_forward_reasoning_summary

**Trigger**: after hop loop exit, `self.reasoning_carry_forward and hs.reasoning_only_sample.strip()`.
**Decision**: prepend reasoning snippet to next hop input.
**Current action**: `hs.next_input.insert(0, carry_msg)`.
**Reducer output**: part of `kind="start_next_hop"` with `carry_forward=True`.

### 4.15 hop_budget_signal

**Trigger**: `hops_remaining <= self.hop_budget_signal_threshold`.
**Decision**: append hop-budget warning to next-hop input.
**Current action**: `hs.next_input.append(hop_signal)`.
**Reducer output**: part of `kind="start_next_hop"`.

### 4.16 context_pressure_signal

**Trigger**: input token ratio exceeds `self.context_pressure_signal_threshold`.
**Decision**: append context-pressure message to next-hop input.
**Current action**: `hs.next_input.append(ctx_signal)`.
**Reducer output**: part of `kind="start_next_hop"`.

---

## 5. Proposed reducer boundary types

These are design sketches — not implementation. Types may evolve before Slice 2B.

### 5.1 StreamEventContext

Bundles the per-event context the reducer needs to make decisions.

```python
@dataclass
class StreamEventContext:
    event_type: str
    payload: dict | None
    event_received_at: float
    event_parsed_at: float
    # outer-loop state snapshot (read-only view for reducer)
    requested_model: str
    output_index_offset: int
    public_trace_len: int
    sent_terminal: bool
    sent_done: bool
    sent_response_start: bool
    final_usage: dict
    repair_hops_used: int
    max_repair_hops: int
    hop_index: int
    max_hops: int
    # runtime thresholds (from self.*)
    reasoning_only_timeout_s: float
    reasoning_only_char_limit: int
    private_function_call_timeout_s: float
    private_function_call_delta_limit: int
    apply_patch_output_style: str
```

### 5.2 StreamDecision

```python
@dataclass
class StreamDecision:
    kind: str  # see §5.3
    reason: str = ""
    # suppression
    suppress_reason: str = ""
    # items to forward to next hop
    upstream_items_to_append: list = field(default_factory=list)
    # tool/public item hints
    public_item_hint: dict | None = None
    # terminal hints
    terminal_hint: dict | None = None
    # repair hint
    repair_hop_index: int | None = None
    # carry-forward hint
    carry_forward_snippet: str | None = None
    # hop signals
    hop_budget_signal: dict | None = None
    context_pressure_signal: dict | None = None
    # telemetry hint (what to emit, not how)
    telemetry_hint: str = ""
    warnings: list = field(default_factory=list)
```

### 5.3 Decision kinds

```text
continue                      no decision needed; render normally
forward_event                 forward this event to client
suppress_event                do not forward; discard
finish_no_output_timeout      terminate loop, emit timeout fallback
finish_terminal_timeout       terminate loop, emit terminal fallback
abort_reasoning_only          emit reasoning-only fallback, return
abort_tool_call               emit stuck-tool fallback, return
inject_signal                 append signal to next_input, break hop
inject_tool_error             append error to next_input, break hop
run_proxy_local_tool          execute tool locally, break hop
forward_public_tool           emit public tool item, emit terminal
start_empty_answer_repair     trigger repair hop
emit_terminal                 emit terminal event and [DONE]
start_next_hop                prepare next hop with updated inputs
finish_result                 assemble and return final result
```

### 5.4 Reducer signature sketch

```python
def decide_stream_event(
    hs: StreamHopState,
    ctx: StreamEventContext,
) -> StreamDecision:
    """Pure (or near-pure) stream event decision function.

    Consumes the current per-hop state and event context.
    Returns a StreamDecision indicating what qz_responses_stream.py should do.
    Must not perform I/O, tool execution, or SSE rendering.
    """
```

---

## 6. Purity and side-effect rules

### Reducer must not

- Write to sockets or network streams
- Write SSE chunks (`_write_chunk`, `_write_transformed_chunks`)
- Execute tools (`proxy_tool_registry.execute(...)`)
- Mutate `hop_body` / `working_body` directly
- Append to `public_trace` directly
- Call `self._emit()` telemetry directly
- Close response objects
- Inspect BrainCaseDB or any persistence layer
- Touch the filesystem or captures
- Mutate global config or environment

### Reducer may

- Read all fields of `StreamHopState` (including `hs.watchdog_state`)
- Update `StreamHopState` in-place when passed explicitly (Option A below)
- Return a proposed state delta when in functional mode (Option B below)

### Option A: in-place mutation (recommended for v1)

```python
def decide_stream_event(hs: StreamHopState, ctx: ...) -> StreamDecision:
    # may mutate hs fields (e.g. hs.error_injected = True)
    # qz_responses_stream.py acts on decision.kind
```

**Why Option A for v1:** `qz_responses_stream.py` already mutates `hs` in place.
Introducing the reducer as a function that still mutates `hs` is an incremental
refactor. Tests can still verify deterministic behaviour by inspecting `hs` after
the call. Option A avoids a large structural change.

### Option B: functional (deferred)

```python
def decide_stream_event(hs: StreamHopState, ctx: ...) -> tuple[StreamHopState, StreamDecision]:
    # returns new/updated state alongside the decision
```

Option B would enable pure unit tests with no shared state. It imposes a larger
structural change and defers until the full reducer is proven stable in Option A.

---

## 7. Behaviour invariants

The following must not change during reducer extraction. Each is a
**test-backed correctness requirement**.

| Invariant | Key test(s) |
|---|---|
| SSE event names forwarded unchanged | `test_answer_deltas_are_written_before_terminal_completion` |
| Sequence number forwarded unchanged | Multiple golden-path tests |
| Terminal event emitted exactly once | `test_live_terminal_timeout_does_not_duplicate_partial_output`, `test_web_search_continuation_malformed_terminal_emits_completed_once` |
| `[DONE]` forwarded unchanged | `test_web_search_continuation_malformed_terminal_emits_completed_once` |
| `public_trace` ordering unchanged | Proxy-local continuation tests |
| Tool lifecycle public events unchanged | `test_proxy_local_streaming_lifecycle_is_not_web_search_specific` |
| Proxy-local terminal suppressed before execution | `test_proxy_local_continuation_can_multi_hop_from_registry_lifecycle` |
| Native tool call forwarding unchanged | Multiple golden-path tests |
| Reasoning-only fallback unchanged | `test_golden_reasoning_only_abort_replays_fallback` |
| Reasoning artifact abort unchanged | `test_reasoning_tool_artifact_aborts_without_length_limit`, `test_golden_reasoning_artifact_aborts_without_executing_tool` |
| Watchdog timeouts unchanged | `test_live_terminal_timeout_preserves_partial_output_and_emits_once` |
| Empty-answer repair unchanged | `test_reasoning_only_completed_triggers_exactly_one_repair_hop`, `test_successful_empty_answer_repair_streams_repaired_answer` |
| Duplicate response.created suppressed | `test_web_search_continuation_suppresses_duplicate_response_start` |
| Hop budget signal unchanged | `test_hop_budget_signal_injected_when_hops_tight`, `test_hop_budget_signal_not_injected_when_hops_plentiful` |
| Context pressure signal unchanged | `test_context_pressure_signal_injected_at_threshold` |
| No qz_* injection into forwarded body | `test_proxy_json_api_does_not_inject_qz_metadata` (in test_qz_request_mutation_regression.py) |
| Stuck tool call aborted without leaking | `test_stuck_function_call_timeout_aborts_without_leaking_private_call` |

---

## 8. Test coverage map

| Decision (§4) | Existing test coverage | Notable gaps | Required before extraction |
|---|---|---|---|
| no_output_timeout | `test_live_ok_stream_does_not_emit_terminal_classified`, watchdog tests | No pure unit test for the decision logic alone | Add unit test for `decide_stream_event` returning `finish_no_output_timeout` |
| terminal_timeout | `test_live_terminal_timeout_preserves_partial_output_and_emits_once` | Same — integration only | Same |
| function_call_suppression | `test_public_function_call_is_buffered_until_arguments_are_complete`, `test_golden_public_function_call_buffers_until_arguments_done` | No pure decision test | Add unit test for suppress decision |
| function_call_abort | `test_stuck_function_call_aborts_instead_of_silent_dead_air`, `test_stuck_function_call_timeout_aborts_without_leaking_private_call` | Good integration coverage | Add unit test asserting abort decision without rendering |
| **reasoning_only_abort** | `test_reasoning_only_stream_aborts_instead_of_never_answering`, `test_reasoning_tool_artifact_aborts_without_length_limit` (3 abort paths covered) | Missing pure-function test; currently tested end-to-end | **Best first extraction: add pure helper test in Slice 2B** |
| proxy_local_terminal_suppression | `test_proxy_local_continuation_can_multi_hop_from_registry_lifecycle` | Tests pass but cover side effects together | Separate suppression decision test |
| repeated_read_signal | `test_stream_repeated_read_signal_not_tool_call_error` | Functional coverage | Low gap |
| tool_call_error | `test_public_function_call_is_buffered_until_arguments_are_complete` | Limited | Pure decision test needed |
| proxy_local_tool_execution | `test_proxy_local_streaming_lifecycle_is_not_web_search_specific` | Integration only | Keep in `qz_responses_stream.py` |
| duplicate_response_start | `test_web_search_continuation_suppresses_duplicate_response_start` | One test | Low gap |
| empty_answer_repair | Full matrix: `test_reasoning_only_completed_triggers_exactly_one_repair_hop`, `test_successful_empty_answer_repair_streams_repaired_answer`, `test_failed_empty_answer_repair_emits_visible_fallback_without_looping` | Good coverage | Separate condition logic test |
| terminal_event_forwarding | `test_answer_deltas_are_written_before_terminal_completion`, `test_web_search_continuation_malformed_terminal_emits_completed_once` | Good | Low gap |
| hop_budget_signal | `test_hop_budget_signal_injected_when_hops_tight`, `test_hop_budget_signal_not_injected_when_hops_plentiful`, `test_hop_budget_signal_disabled_with_minus_one` | Good | Low gap |
| context_pressure_signal | `test_context_pressure_signal_injected_at_threshold`, `test_context_pressure_signal_not_injected_below_threshold` | Good | Low gap |
| carry_forward_reasoning | Unknown — not found in test search | **Gap** | Add test before extracting |

---

## 9. Slice 2B — COMPLETE

**Status: Implemented in commit 38df0e9.**

`StreamDecision` dataclass and `_reasoning_only_abort_reason()` pure helper
were added to `proxy/qz_responses_stream.py`. 14 new unit tests cover all
three abort paths, priority ordering, disabled states, exact boundary
conditions, and edge cases. 2437 tests pass. No behaviour change.

**What was implemented:**

```text
1. StreamDecision dataclass — reducer vocabulary object (not yet consumed broadly).

2. _reasoning_only_abort_reason(
       *,
       reasoning_only_sample: str,
       reasoning_only_chars: int,
       reasoning_only_progress_at: float | None,
       now: float,
       reasoning_only_timeout_s: float,
       reasoning_only_char_limit: int,
   ) -> str | None
   Returns "artifact_tool_payload" | "timeout" | "char_limit" | None.
   Pure: no I/O, no state mutation, no self, time injected via `now`.
   Exact semantics preserved: strict > comparisons, disabled at < 0,
   artifact check has highest priority.

3. Inline abort_reason block replaced with call to helper.
   reasoning_only_idle local variable removed (moved inside helper).
```

**Note on naming:** The pre-implementation plan (Slice 2A) used the name
`_is_reasoning_only_abort(hs, ctx)`. The implemented name
`_reasoning_only_abort_reason(...)` is better — it returns the reason
string, not just a boolean, and takes explicit keyword-only parameters
instead of a context object. All live guidance uses the implemented name.

---

## 9B. Slice 2C — COMPLETE

`_should_suppress_duplicate_response_start(event_type, sent_response_start) -> bool`
extracted in commit c320cfc. 6 unit tests added. 2443 tests pass. No behaviour change.

---

## 9C. Slice 2D — COMPLETE

`_should_inject_hop_budget_signal(hops_remaining, threshold) -> bool` extracted.
Condition moved out of `_hop_budget_signal_message()`. 11 unit tests added.
No behaviour change. Message construction and telemetry unchanged.

---

## 9D. Slice 2D.1 — COMPLETE

Audit of helpers extracted in Slices 2B–2D.

**Audit results:**

- All three helpers confirmed module-level, pure, side-effect free.
- `StreamDecision` confirmed vocabulary-only; not broadly consumed; no `decide_stream_event()` exists.
- Behaviour preservation confirmed for all helpers (exact comparison semantics, disabled-at-negative, None-safe progress_at).
- 155 stream tests pass. Coverage for all helpers present.
- No runtime code changes required.

**Recommended next: Slice 2E** — extract `_should_inject_context_pressure_signal(input_tokens, context_length, threshold) -> bool`.

Rationale: bounded inputs (two numbers + a float threshold), no tool execution, no terminal event, no SSE rendering. Logic lives in `_context_pressure_signal_message()` and is cleanly separable. Same extraction pattern as Slice 2D.

Avoid tool lifecycle extraction, terminal event extraction, and watchdog extraction until later.

---

## 9E. Slice 2E — COMPLETE

`_should_inject_context_pressure_signal(input_tokens, context_length, threshold) -> bool` extracted.
Condition moved out of `_context_pressure_signal_message()`. 12 unit tests added.
No behaviour change. Message construction and telemetry unchanged.

**What was implemented:**

```text
_should_inject_context_pressure_signal(
    input_tokens: int | float,
    context_length: int,
    threshold: float,
) -> bool

Semantics (exact, preserving original code):
  threshold <= 0    → False  (disabled; note: <= not <, unlike hop-budget helper)
  context_length <= 0 → False
  input_tokens <= 0   → False
  input_tokens / context_length >= threshold → True
  otherwise → False

_context_pressure_signal_message() now delegates to this helper.
Model lookup and type coercion remain in the method.
fill_ratio calculation and pct formatting remain unchanged.
Message text and _make_signal_message() call unchanged.
Telemetry emission (context_pressure_signal event) unchanged.
```

**Recommended next: Slice 2E.1** — audit and polish of Slices 2B–2E helpers before
any more complex extraction. Avoid tool lifecycle, terminal event, and watchdog
extraction until later.

---

## 9F. Slice 2E.1 — COMPLETE

Audit of all reducer-adjacent helpers extracted in Slices 2B–2E.

**Audit results:**

- All four helpers confirmed module-level, pure, and side-effect free:
  no I/O, no DB, no sockets, no SSE writes, no tool execution, no telemetry,
  no state mutation, no `self`.
- `StreamDecision` confirmed vocabulary-only; not broadly consumed; no
  `decide_stream_event()` exists in live code.
- `_is_reasoning_only_abort` appears only as historical naming context in §9
  — not live guidance.
- Behaviour preservation confirmed for all helpers:
  `_reasoning_only_abort_reason` (artifact priority, strict `>` comparisons,
  disabled-at-negative, `None`-safe `progress_at`);
  `_should_suppress_duplicate_response_start` (exact event-type set);
  `_should_inject_hop_budget_signal` (threshold-negative disables, `<=` injects);
  `_should_inject_context_pressure_signal` (threshold `<=0` disables, `>=` injects,
  context_length and input_tokens edge cases preserved).
- Test coverage confirmed: 167 stream tests, 2465 total — all pass.
- No runtime code changes required.

**Recommended next: pause for top-level stocktake.**

Rationale: Slices 2B–2E safely extracted low-risk, bounded decisions. The
remaining candidates carry higher extraction risk:

```text
tool lifecycle        — StreamToolCallState interaction is timing-sensitive
terminal events       — emit_terminal must stay coupled with rendering
watchdog/no-output    — timeout decisions depend on wall-clock injection
proxy-local suppression — subtle timing; missing test gap noted in §8
continuation/repair   — multi-hop state mutation; high blast radius
```

Before touching any of these, a fresh design micro-slice should define the
extraction boundary, identify gaps in test coverage, and confirm the approach.

Possible next options after stocktake:

```text
- continue #37 with a design micro-slice for the next delicate seam
- shift to repeated-read v1 advisory signal (stateless, no stream changes)
- revisit #7 memory_domain policy/config leftovers
- defer #51/#46 until operational store decision
```

---

## 10. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Accidental terminal duplication | Medium — decision and rendering must stay coupled | Keep rendering in `qz_responses_stream.py`; use existing dupe tests |
| Missing terminal event | Medium | Ensure `kind="emit_terminal"` is not swallowed by reducer |
| Tool call emitted before arguments complete | High risk if suppression logic moves | Keep `StreamToolCallState` interaction co-located with function_call events |
| Proxy-local terminal suppression broken | High risk — subtle timing | Do not move suppression decision without the associated test from §8 |
| Repeated-read signal leaks to public lifecycle | Medium | Maintain `signal_injected` flag semantics |
| Request body mutation regression | Low if `working_body` stays outside reducer | Never put `working_body` in `StreamHopState` |
| Timeout behaviour changes | Medium — timing-sensitive | Inject deterministic time values in tests; existing watchdog tests must stay green |
| `public_trace` ordering changes | Low | Keep `public_trace` as outer-loop state only |
| Reducer grows into second giant module | High over time | Hard limit: reducer returns decisions only; execution stays in `qz_responses_stream.py` |

---

## 11. Non-goals

The reducer extraction is explicitly not:

- BrainCase, LimbiCore, or any memory/state feature
- Telemetry persistence or SQLite operational storage
- HSM or human state modelling
- MCP integration
- Async job framework
- Stream protocol change (SSE event names, response shapes)
- A full rewrite of `qz_responses_stream.py`
- A new module that duplicates the gravity well problem in a different file

---

## Cross-references

- `proxy/qz_responses_stream.py` — current side-effect owner; Slice 1 StreamHopState
- `proxy/qz_stream_terminal.py` — pure StreamObservation + classify_stream_terminal()
- `proxy/qz_stream_watchdog.py` — pure StreamWatchdogState predicates
- `proxy/qz_tool_lifecycle.py` — StreamToolCallState for function_call accumulation
- `tests/test_qz_responses_stream.py` — 121 stream tests; covers most decisions
- `docs/current-stocktake.md` — current project state
- Issue #37 — architectural seam extraction plan
