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
- config/var/script cleanup (#5) — safe any time, good between features
- revisit #7 memory_domain policy/config leftovers
- defer #51/#46 until operational store decision
Note: repeated-read v1 is COMPLETE (#3/#4/#43 closed); v2 blocked on SQLite.
```

---

## 9G. Slice 2F-design — next delicate seam selection

### Purpose

Slices 2B–2E safely extracted four low-risk bounded decision helpers. The
remaining #37 seam candidates are not equivalent in risk or extraction value.
This design slice compares them and selects the next safe seam before any code
is written.

No runtime code was changed in this slice. All changes are documentation only.

---

### Candidate comparison

| Candidate | Owner files | Risk level | Existing isolation | Test coverage | Verdict |
|---|---|---|---|---|---|
| Watchdog / timeout kind | `qz_stream_watchdog.py`, `qz_responses_stream.py` | **Low** | Decision predicates already extracted to `qz_stream_watchdog.py`; action code stays in `qz_responses_stream.py` | `test_qz_stream_watchdog.py`: 40+ tests; `WatchdogStreamRuntimeTests`: no-output + terminal paths | **Best next** — see §9G.3 |
| Terminal event handling | `qz_stream_terminal.py`, `qz_responses_stream.py`, `qz_sse.py` | **High** | `is_terminal_stream_event()` pure; `classify_stream_terminal()` pure; emission and flag management coupled to rendering | Several live stream tests | Skip — rendering and flag mutation are inseparable |
| Tool lifecycle | `qz_tool_lifecycle.py`, `qz_proxy_tools.py`, `qz_responses_stream.py` | **High** | `StreamToolCallState`, `abort_reason()`, `completed_call_decision()` already in `qz_tool_lifecycle.py`; inline code mixes decision + action + telemetry | Multiple integration tests | Skip — execution is a critical side effect; proxy-local timing is subtle |
| Proxy-local suppression | `qz_proxy_tools.py`, `qz_responses_stream.py` | **Medium** | Suppression condition is a 3-line inline check; no standalone test | §8 notes test gap for suppression decision | Defer — needs test gap filled first |
| Continuation / repair flow | `qz_responses_stream.py` | **Very high** | Not isolated at all; mutates `working_body`, `hop_body`, outer-loop repair counters | `test_reasoning_only_completed_triggers_exactly_one_repair_hop`, others | Skip — multi-hop state mutation; highest blast radius |

---

### Recommendation: watchdog timeout-kind combiner

**Chosen seam:** Watchdog timeout kind selection.

The pure decision predicates `should_trigger_no_output_timeout()` and
`should_trigger_terminal_timeout()` are already extracted into
`proxy/qz_stream_watchdog.py`. What remains inline in `qz_responses_stream.py`
is the following pattern, duplicated at two call sites (exception handler and
event loop):

```python
if should_trigger_no_output_timeout(hs.watchdog_state, now):
    return self._finish_no_output_timeout(...)
if should_trigger_terminal_timeout(hs.watchdog_state, now):
    return self._finish_terminal_timeout_after_output(...)
```

A pure combiner helper can select *which* timeout kind applies — if any — as a
single decision point:

```python
_stream_timeout_kind(watchdog_state, now) -> str | None
```

This:
- Wraps two already-pure predicates
- Eliminates the duplicated two-check pattern from two call sites
- Creates a single testable decision point
- Fits the `_should_*/kind-returning` helper pattern from Slices 2B–2E
- Carries no I/O, no state mutation, no SSE writes, no tool execution

The action code (`_finish_no_output_timeout()`, `_finish_terminal_timeout_after_output()`)
stays entirely in `qz_responses_stream.py`. Nothing moves there.

**Why not the others:**
- Terminal event handling has no pure decision layer to extract — emission and
  flag management are coupled to rendering.
- Tool lifecycle: execution is a critical side effect; proxy-local timing is too
  subtle for extraction without a dedicated design and test pass.
- Proxy-local suppression: simple condition, but the §8 test gap should be
  filled before extracting the suppression decision.
- Continuation/repair: mutates `working_body` and outer-loop hop counters;
  highest blast radius of all candidates.

---

### Proposed extraction boundary

**What stays in `qz_responses_stream.py`:**

```text
socket reads (resp.readline())
time.time() calls (event_parsed_at, timeout_at)
sync_terminal_read_timeout() — socket deadline side effect
_finish_no_output_timeout() — observation assembly, telemetry, SSE fallback, result build
_finish_terminal_timeout_after_output() — observation assembly, telemetry, SSE completion, result build
watchdog_state.triggered = True (inside _finish_*)
public_trace mutation
request body mutation
response close / drain
```

**What may move to a pure helper:**

```text
The selection logic: "which timeout kind fires at this moment, if any"
This is a pure function of watchdog_state and now.
It does not need to know about the action path that follows.
```

---

### Proposed helper shape

```python
def _stream_timeout_kind(
    watchdog_state: "StreamWatchdogState",
    now: float,
) -> str | None:
    """Return 'no_output', 'terminal', or None.

    Pure helper — no I/O, no state mutation.
    Combines should_trigger_no_output_timeout and should_trigger_terminal_timeout
    with a stable priority order: no_output takes precedence over terminal.
    Returns None when neither timeout should fire.
    """
    if should_trigger_no_output_timeout(watchdog_state, now):
        return "no_output"
    if should_trigger_terminal_timeout(watchdog_state, now):
        return "terminal"
    return None
```

Priority ordering preserves the existing inline order — no-output is checked
before terminal at both call sites. This must not change.

Caller would change from:

```python
if should_trigger_no_output_timeout(hs.watchdog_state, timeout_at):
    return self._finish_no_output_timeout(...)
if should_trigger_terminal_timeout(hs.watchdog_state, timeout_at):
    return self._finish_terminal_timeout_after_output(...)
```

To:

```python
timeout_kind = _stream_timeout_kind(hs.watchdog_state, timeout_at)
if timeout_kind == "no_output":
    return self._finish_no_output_timeout(...)
if timeout_kind == "terminal":
    return self._finish_terminal_timeout_after_output(...)
```

This substitution is applied at both the exception handler and the event-loop
call site.

---

### Existing test coverage

`tests/test_qz_stream_watchdog.py` (40+ tests, all passing):
- `DisabledWatchdogTests` — timeout=0, timeout<0, already_triggered
- `TriggerTests` — elapsed≥timeout fires, elapsed<timeout does not, first_event reference
- `SuppressionTests` — visible output prevents, terminal prevents, mark_* idempotent
- `TerminalTimeoutTests` — fires after output + deadline, suppressed by terminal
- `ElapsedSecsTests`, `BuildTimeout*Tests`, `WatchdogClassificationIntegrationTests`
- `WatchdogStreamRuntimeTests` — no-output fires on read stall, terminal fires on stall after output, disabled watchdog does not fire
- `NoInfiniteLoopTests` — triggered flag prevents re-trigger

Live stream tests in `test_qz_responses_stream.py`:
- `test_live_terminal_timeout_preserves_partial_output_and_emits_once`
- `test_live_terminal_timeout_does_not_duplicate_partial_output`
- `test_live_ok_stream_does_not_emit_terminal_classified`

**Coverage gaps (pre-work required before coding slice):**

| Gap | What to add |
|---|---|
| No unit test for `_stream_timeout_kind` combiner | Add `StreamTimeoutKindHelperTests` in `test_qz_stream_watchdog.py` or `test_qz_responses_stream.py` |
| No event-loop no-output timeout integration test | `WatchdogStreamRuntimeTests` covers the exception path; add test for no-output firing during event loop (not on exception) if feasible |

The combiner unit tests must include:
- `test_no_output_kind_returned_when_no_output_predicate_fires`
- `test_terminal_kind_returned_when_terminal_predicate_fires`
- `test_no_output_takes_priority_over_terminal`
- `test_none_returned_when_neither_fires`
- `test_none_returned_when_both_disabled`

These tests should be written as part of Slice 2F (the future coding slice),
not as pre-work, since the helper does not exist yet.

---

### Acceptance criteria for future Slice 2F coding slice

The implementation slice must:

```text
1. Add _stream_timeout_kind(watchdog_state, now) -> str | None near the other
   watchdog helpers or near the other pure stream helpers in qz_responses_stream.py.

2. Add StreamTimeoutKindHelperTests (5 unit tests) in test_qz_stream_watchdog.py.

3. Replace the duplicated two-check pattern at both call sites in
   qz_responses_stream.py (exception handler and event loop).

4. Full suite must remain green (2465 tests or more).

5. No SSE rendering moved.
6. No time.time() calls moved (time is still injected via event_parsed_at / timeout_at).
7. No response close/drain logic moved.
8. No tool lifecycle touched.
9. No continuation/repair logic touched.
10. No request body mutation changed.
11. No decide_stream_event() added.
12. git diff --check PASS.
```

---

### Explicit non-goals for Slice 2F

```text
Not a full reducer.
Not a decide_stream_event() implementation.
Not tool lifecycle extraction.
Not terminal event forwarding rewrite.
Not proxy-local suppression rewrite.
Not continuation / repair flow extraction.
Not BrainCase / LimbiCore work.
Not repeated-read changes.
Not operational persistence.
Not sync_terminal_read_timeout() extraction (socket side effect; not a pure decision).
```

---

## 9H. Slice 2F — COMPLETE

`stream_timeout_kind(watchdog_state, now) -> str | None` added to
`proxy/qz_stream_watchdog.py`. Two duplicated two-check patterns replaced at
both call sites (exception handler and event loop) in `qz_responses_stream.py`.
5 unit tests added in `StreamTimeoutKindHelperTests`.

```text
stream_timeout_kind(state: StreamWatchdogState, now: float) -> str | None
  Returns "no_output" | "terminal" | None.
  Pure: no I/O, no state mutation, no side effects.
  Priority: no_output checked before terminal — preserves inline order.

Call sites replaced (qz_responses_stream.py):
  1. Exception handler (socket TimeoutError path)
  2. Event-loop body (per-event arrival check)

Action methods unchanged and still in qz_responses_stream.py:
  _finish_no_output_timeout()
  _finish_terminal_timeout_after_output()
  watchdog_state.triggered = True
  SSE rendering
  telemetry
```

2470 tests passing. No behaviour change.

**Recommended next: Slice 2F.1** — audit/polish before any further stream seam extraction.

---

## 9I. Slice 2F.1 — COMPLETE

Audit of `stream_timeout_kind()` and Slice 2F call sites.

**Audit results:**

- `stream_timeout_kind` confirmed module-level, pure, and side-effect free.
  Only calls the two existing pure predicates; no I/O, no state mutation, no self.
- Both call sites confirmed: `timeout_at`/`event_parsed_at` unchanged,
  `_finish_*` arguments unchanged, no state mutation moved.
- `qz_responses_stream.py` confirmed as sole side-effect/action owner.
- No `decide_stream_event()` exists.
- Test fix: `test_no_output_takes_priority_over_terminal` now patches both predicates
  to `True` simultaneously via `unittest.mock.patch`, directly proving combiner priority
  independent of predicate natural mutual exclusivity.
- 52 watchdog tests pass. 2470 total tests pass. No behaviour change.

**Recommended next: pause before further stream seam extraction.**

Remaining seams (terminal events, tool lifecycle, proxy-local suppression,
continuation/repair) need fresh design micro-slices. Config/var cleanup (#5)
is the safe alternative.

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

## 9J. Post-#56 stocktake (2026-05-19) — next seam recommendation

### State verification

All Slices 1–2F.1 confirmed in code:

| Helper | Location | Tests |
|---|---|---|
| `StreamHopState` | `qz_responses_stream.py:92` | independence tests pass |
| `StreamDecision` | `qz_responses_stream.py:173` | vocabulary only; no `decide_stream_event()` |
| `_reasoning_only_abort_reason()` | `qz_responses_stream.py:263` | 14 unit tests |
| `_should_suppress_duplicate_response_start()` | `qz_responses_stream.py:215` | 6 unit tests |
| `_should_inject_hop_budget_signal()` | `qz_responses_stream.py:231` | 11 unit tests |
| `_should_inject_context_pressure_signal()` | `qz_responses_stream.py:243` | 12 unit tests |
| `stream_timeout_kind()` | `qz_stream_watchdog.py` (imported) | 5 unit tests in `StreamTimeoutKindHelperTests` |

Current file sizes: `qz_responses_stream.py` 2168 lines. Full suite: 2600 tests.

No tool lifecycle, terminal event, continuation/repair extraction has happened.
No `decide_stream_event()` exists.

---

### Candidate comparison (post-Slice 2F.1)

| Candidate | Risk | Key blocker | Recommendation |
|---|---|---|---|
| Proxy-local terminal suppression | **Low–medium** | §8 test gap: no pure suppression decision test | **Next** — bounded 3-condition check; test gap fillable with 4 unit tests |
| Terminal event handling | High | Rendering + flag mutation inseparable | Skip — do not extract |
| Tool lifecycle | High | `StreamToolCallState` timing + execution side effect | Skip — do not extract |
| Watchdog follow-up | Low | `stream_timeout_kind` already done; action code stays in `qz_responses_stream.py` | Nothing to extract |
| Continuation / repair flow | Very high | Outer-loop state mutation; `working_body` changes | Skip — do not extract |
| Outer-loop StreamRunState | Medium | New state bundling; risk of touching multi-hop path | Design-first; not urgent |

---

### Selected next seam: proxy-local terminal suppression

**Why this seam, not the others:**

The terminal suppression check at `qz_responses_stream.py:1779` is a 3-condition
boolean check that decides whether to suppress a terminal event when a proxy-local
tool call is active. It has:

- No state mutation
- No SSE rendering
- No socket I/O
- No tool execution
- Three fully pure inputs (event_type + payload dispatch, completed_call None-check, is_proxy_local bool)

The design is described in §4.6. The only blocker is the test gap: there is
no unit test that exercises the suppression decision in isolation.

**Extraction boundary:**

```text
Site to extract (qz_responses_stream.py ~line 1779):

    if (
        is_terminal_stream_event(event_type, payload)
        and hs.completed_call
        and self.proxy_tool_registry.is_proxy_local_call(hs.completed_call)
    ):
        self._emit_stream_event_timing(...)
        hs.event_lines = []
        continue
```

The pure condition (3-line check) can move to a helper.
The side-effect block (emit, clear event_lines, continue) stays in `qz_responses_stream.py`.

**Two other proxy-local checks (lines 2001, 2016) must not move:**
These are complex outer-loop control-flow conditions combining `error_injected`,
`signal_injected`, and `is_proxy_local`. They are not pure suppression decisions
and must remain untouched.

---

### Proposed helper shape

```python
def _should_suppress_proxy_local_terminal(
    event_type: str,
    payload: Any,
    completed_call: dict | None,
    is_proxy_local: bool,
) -> bool:
    """Return True if a proxy-local terminal event should be suppressed.

    Pure — no I/O, no state mutation.
    The caller computes is_proxy_local via proxy_tool_registry.is_proxy_local_call().
    Returns False whenever completed_call is None (no active tool call).
    """
    return (
        is_terminal_stream_event(event_type, payload)
        and completed_call is not None
        and is_proxy_local
    )
```

By taking `is_proxy_local` as a pre-computed bool rather than the registry callable,
the helper has no dependency on `proxy_tool_registry` and is fully testable with
plain data.

---

### Required tests for Slice 2G

```text
ProxyLocalTerminalSuppressionHelperTests (4 tests):

test_returns_true_when_all_conditions_met
  event_type="response.completed", completed_call={...}, is_proxy_local=True -> True

test_returns_false_when_event_is_not_terminal
  event_type="response.output_text.delta", is_proxy_local=True -> False

test_returns_false_when_completed_call_is_none
  event_type="response.completed", completed_call=None, is_proxy_local=True -> False

test_returns_false_when_not_proxy_local
  event_type="response.completed", completed_call={...}, is_proxy_local=False -> False
```

Plus regression: existing proxy-local continuation tests must still pass.

---

### Acceptance criteria for Slice 2G

```text
1. Add _should_suppress_proxy_local_terminal() near the other pure helpers
   in qz_responses_stream.py (after _should_inject_context_pressure_signal).

2. Add ProxyLocalTerminalSuppressionHelperTests (4 tests).

3. Replace the 3-condition check at the terminal suppression site (~line 1779).
   Side-effect block stays untouched.

4. Do NOT touch lines 2001 or 2016 (outer-loop drain/continuation conditions).

5. Full suite must remain green (2600 tests or more).

6. No SSE rendering moved.
7. No state mutation moved.
8. No tool lifecycle touched.
9. No continuation/repair touched.
10. No decide_stream_event() added.
11. git diff --check PASS.
```

---

### Non-goals for Slice 2G

```text
Not a full reducer.
Not decide_stream_event().
Not tool lifecycle extraction.
Not continuation/repair extraction.
Not touching the two outer-loop proxy-local conditions (lines 2001, 2016).
Not extracting terminal event rendering.
Not outer-loop StreamRunState bundling.
```

---

## 9K. Slice 2G — COMPLETE

`_should_suppress_proxy_local_terminal(event_type, payload, completed_call, is_proxy_local) -> bool`
added to `proxy/qz_responses_stream.py`.

```text
Helper location: qz_responses_stream.py, after _should_inject_context_pressure_signal()
Pure: no I/O, no state mutation, no registry calls
Calls: is_terminal_stream_event(event_type, payload) from qz_streaming
Takes: is_proxy_local as precomputed bool from call site

Call site change (qz_responses_stream.py ~line 1795):
  Before: inline 3-condition check calling is_proxy_local_call() directly
  After:  _should_suppress_proxy_local_terminal() with is_proxy_local precomputed inline

Side-effect block (emit, clear event_lines, continue): unchanged
Outer-loop conditions (~lines 2021/2037): untouched
```

4 unit tests in `ProxyLocalTerminalSuppressionHelperTests`:
- `test_returns_true_when_all_conditions_met`
- `test_returns_false_when_event_is_not_terminal`
- `test_returns_false_when_completed_call_is_none`
- `test_returns_false_when_not_proxy_local`

2604 tests passing. No behaviour change.

**Recommended next: Slice 2G.1** — audit/polish before any further stream seam extraction.

---

## 9L. Slice 2G.1 — COMPLETE

Audit of `_should_suppress_proxy_local_terminal()` and Slice 2G call site.

**Audit results:**

- Semantic drift found: Slice 2G used `completed_call is not None` but the original
  inline condition used `and hs.completed_call` (truthiness). In real flow
  `completed_call` is either `None` or a non-empty dict from the assembler (never
  `{}`), so no behaviour difference exists today. Nevertheless, the helper was
  updated to use `bool(completed_call)` to preserve exact original semantics and
  protect against future changes.

- Call-site guard updated: `if hs.completed_call is not None else False` changed to
  `if hs.completed_call else False`, matching truthiness semantics.

- `is_proxy_local` computation extracted into local variable `_completed_call_is_proxy_local`
  for clarity (was inline ternary on the call line).

- Helper confirmed pure: no I/O, no state mutation, no registry calls, calls only
  `is_terminal_stream_event()` from `qz_streaming`.

- Outer-loop conditions at ~lines 2021/2037 confirmed untouched.

- Side-effect block (emit, clear event_lines, continue) confirmed unchanged.

- No `decide_stream_event()` exists.

**Fix summary:**
```text
helper:   completed_call is not None -> bool(completed_call)
docstring: "None" -> "falsey (None or empty)"
call site: is not None guard -> truthiness guard; inline ternary -> local variable
test:     test_returns_false_when_completed_call_is_empty_dict added
```

2605 tests passing. No behaviour change.

**Recommended next: fresh design micro-slice before any further stream seam extraction.**

---

---

## 9M. Slice 2H-design — outer-loop StreamRunState assessment (2026-05-19)

### Purpose

After Slices 1–2G.1, evaluate whether an outer-loop `StreamRunState` bundling
the cross-hop locals in `run()` is the next safe step, and compare it against
the remaining higher-risk seam candidates.

No runtime code was changed in this slice.

---

### Outer-loop state inventory

The `run()` method maintains these variable clusters outside `StreamHopState`:

**Group 1 — Timing/run metadata** (initialized once; read-mostly)

| Variable | Lifecycle | Notes |
|---|---|---|
| `started_at` | set at `time.time()` before hop loop | passed to `_build_result` |
| `first_output_at` | set once at first visible output | set to `None` if no output |
| `completed_at` | set at each exit point | local, not cross-hop state |
| `final_usage` | updated from `response.completed` payload | mutated inside hop |

**Group 2 — Terminal emission flags** (simple booleans; safest cross-hop cluster)

| Variable | Where set | Where read | Notes |
|---|---|---|---|
| `sent_response_start` | set to `True` once at `response.created`; never reset | `_should_suppress_duplicate_response_start` | cross-hop dedup |
| `sent_terminal` | set at loop exit/fallback paths | end-of-hop completion block | prevents duplicate completion |
| `sent_done` | set alongside `sent_terminal` or just after | end-of-hop `[DONE]` emission | prevents duplicate `[DONE]` |

**Group 3 — Output accumulation** (tightly coupled to SSE; higher risk)

| Variable | Mutations | Coupling | Risk to move |
|---|---|---|---|
| `public_trace` | `extend`/`append` in 7 places | `emit_completed()`, fallback assembly | High |
| `sequence` | returned from SSE helpers; incremented in many SSE functions | every SSE write | Very high |
| `output_index_offset` | incremented once per hop: `hs.max_output_index + 1` | output_index computation for next hop | Medium |
| `summary_started` | passed to `_transform_sse_event()`; tracks seen reasoning IDs | SSE transform; cross-hop reasoning dedup | Medium |

**Group 4 — Continuation/repair state** (highest blast radius)

| Variable | Mutations | Coupling |
|---|---|---|
| `working_body` | `working_body["input"] = hs.next_input` once per continuation hop | hop request body; input accumulation |
| `max_hops` | read-only after init | loop bound |
| `repair_hops_used` | incremented when repair hop starts | repair quota enforcement |
| `pending_repair_hop_index` | set when repair needed; cleared each hop | repair hop scheduling |

**Group 5 — Advisory/signal state** (loosely coupled)

| Variable | Lifecycle |
|---|---|
| `repeated_read_state` | seeded once; updated by `record_tool_call` per tool hop |
| `seen_signatures` | dedup set; used in one tool-call path |
| `counters` | `{"search": 0, "open_page": 0}`; tool call accounting |

---

### StreamRunState value/risk assessment

**Benefits of StreamRunState:**

1. Makes the boundary between per-hop state (already `StreamHopState`) and
   cross-hop state (currently scattered locals) explicit.
2. Terminal flags (`sent_response_start`, `sent_terminal`, `sent_done`) are
   the smallest safe first cluster — simple booleans, clear semantics, read
   from/written to a handful of locations.
3. Enables future unit tests that construct a `StreamRunState` with known values
   (e.g. "what happens if `sent_terminal=True` and `sent_done=False`?").
4. Parallel to Slice 1's `StreamHopState` — same pattern, same justification.
5. No behaviour change required; pure state-bundling.

**Risks of StreamRunState:**

1. `sequence` is currently threaded as a value returned-and-updated through
   helper functions (`sequence = func(sequence)`). Moving it to a mutable
   object changes that calling convention across many call sites.
2. `public_trace` is directly extended/appended in 7 places; moving it requires
   careful update of every mutation site.
3. `working_body` mutation is the heart of the continuation flow. Must not enter
   `StreamRunState` until continuation/repair is safely extracted.
4. Overly broad first cut risks accidental coupling between adjacent fields.
5. Object lifetime confusion if `StreamRunState` outlives the `run()` scope.

**Verdict:** StreamRunState is worthwhile, but only if scoped to the simplest
cluster first. The terminal emission flags (`sent_terminal`, `sent_done`,
`sent_response_start`) are the right first cluster. They satisfy all the
criteria from Slice 1's StreamHopState: simple, clearly scoped, no I/O
coupling, testable in isolation.

Must **not** include in first cut: `sequence`, `public_trace`, `working_body`,
`repair_hops_used`, `pending_repair_hop_index`.

---

### Candidate comparison (post-2G.1)

| Candidate | Risk | Verdict |
|---|---|---|
| **StreamRunState (terminal flags only)** | **Low** | **Next — pure state bundling; no behaviour change** |
| Another pure helper (remaining conditions) | Low–medium | No obvious remaining target after 2G |
| Terminal event handling seam | High | Skip — rendering inseparable |
| Tool lifecycle seam | High | Skip — execution side effect + timing |
| Continuation / repair flow | Very high | Skip — outer-loop mutation |

---

### Proposed StreamRunState shape (first cut)

```python
@dataclass
class StreamRunState:
    """Cross-hop mutable outer-loop state for the Responses SSE streaming run.

    Bundles the terminal emission flags that persist across continuation hops.
    Analogous to StreamHopState for per-hop state. Production code uses
    StreamRunState.fresh() to construct.

    Does NOT include: sequence, public_trace, working_body, repair state,
    output_index_offset, or any SSE-coupled fields — those remain locals
    until their own extraction slice is designed.
    """
    sent_response_start: bool = False
    sent_terminal: bool = False
    sent_done: bool = False

    @classmethod
    def fresh(cls) -> "StreamRunState":
        return cls(
            sent_response_start=False,
            sent_terminal=False,
            sent_done=False,
        )
```

**Fields in first cut:**

| Field | Reason |
|---|---|
| `sent_response_start` | cross-hop dedup for response.created; set once, never reset |
| `sent_terminal` | guards duplicate terminal emission at end of each hop |
| `sent_done` | guards duplicate `[DONE]` emission |

**Fields deferred (must not move yet):**

| Field | Reason to defer |
|---|---|
| `sequence` | threaded through SSE helpers as return value; changing convention is high risk |
| `public_trace` | mutated in 7+ places; requires careful audit of every mutation site |
| `output_index_offset` | index arithmetic across hops; needs dedicated test-first slice |
| `summary_started` | passed to SSE transform; SSE coupling risk |
| `final_usage` | updated inside event loop; lives closer to hop logic than run logic |
| `working_body` | core of continuation mutation; extremely high blast radius |
| `repair_hops_used` / `pending_repair_hop_index` | repair flow; highest risk cluster |

---

### Coverage gaps before Slice 2H-impl

| Scenario | Coverage | Gap? |
|---|---|---|
| `sent_response_start` prevents duplicate response.created | `test_web_search_continuation_suppresses_duplicate_response_start` | ✅ covered |
| `sent_terminal=True` prevents duplicate completion | `test_web_search_continuation_final_completed_without_done_appends_done_once`, `test_web_search_continuation_final_done_only_emits_completed_once` | ✅ covered via integration |
| `sent_done=True` prevents duplicate `[DONE]` | Above tests | ✅ covered via integration |
| Unit test for `StreamRunState.fresh()` defaults | No unit test | **Gap** — add as part of 2H-impl |
| Cross-hop terminal flag persistence | Integration fixtures (proxy-local multi-hop) | ✅ partially covered |
| `sent_response_start` stays True across hops | Implicit in continuation fixture | **Gap** — add unit test in 2H-impl |

Required tests for Slice 2H-impl:

```text
StreamRunStateTests:
  test_fresh_returns_all_false_defaults
    StreamRunState.fresh().sent_response_start == False
    StreamRunState.fresh().sent_terminal == False
    StreamRunState.fresh().sent_done == False

  test_fields_are_independent_between_instances
    setting rs1.sent_terminal = True does not affect rs2

  test_sent_response_start_persists_across_logical_hops
    (integration: verify via proxy-local multi-hop fixture or web-search continuation test)
```

---

### Acceptance criteria for Slice 2H-impl

```text
1. Add StreamRunState dataclass near StreamHopState in qz_responses_stream.py.

2. Replace the 3 terminal emission locals in run() with rs.sent_response_start,
   rs.sent_terminal, rs.sent_done where rs = StreamRunState.fresh().

3. All existing tests must remain green. No behaviour change.

4. Add StreamRunStateTests (2+ unit tests for defaults and independence).

5. Do NOT move sequence, public_trace, working_body, or repair state.

6. py_compile PASS. git diff --check PASS. Full suite PASS.
```

---

### Non-goals for Slice 2H

```text
- No field moves beyond the 3 terminal flags
- No sequence or public_trace relocation
- No working_body encapsulation
- No continuation/repair bundling
- No tool lifecycle or terminal rendering extraction
- No decide_stream_event()
- No SSE rendering changes
- No test changes beyond StreamRunStateTests
```

---

## 9N. Slice 2H-impl — COMPLETE

`StreamRunState` dataclass added to `proxy/qz_responses_stream.py` near
`StreamHopState` and `StreamDecision`.

```text
StreamRunState.fresh() -> StreamRunState(
    sent_response_start=False,
    sent_terminal=False,
    sent_done=False,
)
```

In `run()`, the 3 locals were replaced with `rs = StreamRunState.fresh()`.
All uses replaced: `rs.sent_response_start`, `rs.sent_terminal`, `rs.sent_done`.

Fields NOT included: sequence, public_trace, working_body, repair state,
output_index_offset, summary_started, final_usage, tool lifecycle state.

3 unit tests in `StreamRunStateTests`: defaults, independence, independent mutation.
2608 tests passing. No behaviour change.

**Recommended next: Slice 2H.1** — audit/polish before any further stream seam extraction.

---

## 9O. Slice 2H.1 — COMPLETE

Audit of `StreamRunState` extraction and `run()` call sites.

**Audit results:**

- `rs = StreamRunState.fresh()` confirmed at line 1353, outside the for-hop loop.
  Not recreated inside any hop iteration — cross-hop persistence is correct.
- All three terminal flag reads (`rs.sent_terminal`, `rs.sent_done`,
  `rs.sent_response_start`) confirmed to replace the original bare locals in
  exactly the same branches and conditions.
- `_finish_no_output_timeout()`, `_finish_terminal_timeout_after_output()`, and
  `_merge_manual_stream_observation()` confirmed to receive keyword arguments
  `sent_terminal=rs.sent_terminal` / `sent_done=rs.sent_done` — no signature change.
- Method-internal uses of `sent_terminal` / `sent_done` as parameter names are
  inside helper function bodies, not `run()`. Correct.
- Confirmed `StreamRunState` has exactly three fields. No extra fields moved.
- `sequence`, `public_trace`, `working_body`, `repair_hops_used`,
  `pending_repair_hop_index`, `output_index_offset`, `summary_started`,
  `final_usage`, `repeated_read_state`, `seen_signatures`, `counters` all remain
  as locals in `run()`.
- No `decide_stream_event()`. No tool lifecycle / terminal rendering /
  continuation/repair extraction.

**Fix:** None — audit clean.

**Test added:** `test_fresh_returns_new_instance_each_time` — asserts `rs1 is not rs2`
as an explicit identity regression guard.

2609 tests passing. No behaviour change.

**Recommended next: fresh design micro-slice before any further stream seam extraction.**

---

## 9P. Finish-plan after Slice 2H.1 (2026-05-20)

### Definition of done for #37

**#37 is complete when:**

1. Per-hop state is explicitly isolated — **DONE** (StreamHopState)
2. Cross-hop terminal emission state is explicitly isolated — **DONE** (StreamRunState, Slices 2H–2H.1)
3. Cross-hop timing and index arithmetic are explicitly isolated — **NEXT** (Slice 2I)
4. Pure branch decisions are extracted where safe — **DONE** (7 pure helpers across Slices 2B–2G)
5. Remaining side-effect-heavy code is intentionally left in `qz_responses_stream.py` with documented seam boundaries — **Slice 2J close-out**

**A full `decide_stream_event()` reducer is NOT required for #37 to be complete.** The original objective was architectural seam extraction — making state explicit and decisions testable — not rewriting the entire stream loop.

The residual locals that will stay as-is after #37:
- `public_trace` (46 references; passed to many helpers as positional argument)
- `sequence` (threaded through SSE helpers as returned-value; changing the calling convention is high risk)
- `summary_started` (35 references; passed to SSE transform)
- `working_body`, `repair_hops_used`, `pending_repair_hop_index` (continuation core; very high blast radius)
- `completed_at` (set at every exit point locally; not cross-hop state)

These are intentionally bounded in place, not accidentally left behind.

---

### Remaining slice sequence

| Slice | Content | Risk | Status |
|---|---|---|---|
| **2I-impl** | StreamRunState timing + cross-hop arithmetic: `started_at`, `first_output_at`, `final_usage`, `output_index_offset` | Low | **Next** |
| **2I.1** | Audit/polish Slice 2I | Low | Follows 2I |
| **2J close-out** | Final audit, document remaining seam boundaries, close #37 | Low (docs only) | After 2I.1 |

---

### Slice 2I — StreamRunState timing + cross-hop arithmetic

**Fields to add:**

| Field | Current init | Mutation sites | Risk |
|---|---|---|---|
| `started_at: float` | `time.time()` at line 1343 | Read-only after init | None |
| `first_output_at: float \| None` | `None` at line 1344; set once at line 1489 | Set once inside loop | Low |
| `final_usage: dict` | `_normalize_response_usage({})` at line 1346 | Lines 1514, 2056 (replacement assignment) | Low |
| `output_index_offset: int` | `0` at line 1351 | Line 2069 (`+= hs.max_output_index + 1`) | Low |

**Why these four together:**
All four are cross-hop accumulators consumed by `_build_result()` and the terminal completion path. Moving them together makes the result-building path self-contained within `rs`.

**`completed_at` stays local:** It is set at every exit point immediately before use. It is not cross-hop state — each exit sets it fresh and uses it once.

**Proposed StreamRunState after 2I:**

```python
@dataclass
class StreamRunState:
    """Cross-hop mutable outer-loop state for one Responses SSE streaming run.

    Includes terminal emission flags and cross-hop timing/accumulation state.
    Does not include sequence, public_trace, working_body, repair state,
    summary_started, or tool lifecycle state.
    """
    started_at: float
    sent_response_start: bool = False
    sent_terminal: bool = False
    sent_done: bool = False
    first_output_at: float | None = None
    final_usage: dict = field(default_factory=dict)
    output_index_offset: int = 0

    @classmethod
    def fresh(cls, started_at: float) -> "StreamRunState":
        return cls(
            started_at=started_at,
            sent_response_start=False,
            sent_terminal=False,
            sent_done=False,
            first_output_at=None,
            final_usage=_normalize_response_usage({}),
            output_index_offset=0,
        )
```

**Call site change in `run()`:**

```python
# Before:
started_at = time.time()
first_output_at = None
completed_at = None
final_usage = _normalize_response_usage({})
...
output_index_offset = 0
...
rs = StreamRunState.fresh()

# After:
completed_at = None
...
rs = StreamRunState.fresh(started_at=time.time())
```

**Method call sites:** All passes of `started_at`, `first_output_at`, `final_usage`, `output_index_offset` to helpers become `rs.started_at`, `rs.first_output_at`, `rs.final_usage`, `rs.output_index_offset`. Method signatures themselves are NOT changed in Slice 2I.

---

### Acceptance criteria for Slice 2I-impl

```text
1. StreamRunState gains 4 fields; fresh() takes started_at as required parameter.

2. run() replaces 4 locals with rs.* references:
   - started_at → rs.started_at (all ~22 references)
   - first_output_at → rs.first_output_at (all ~5 references)
   - final_usage → rs.final_usage (all ~8 references)
   - output_index_offset → rs.output_index_offset (all ~5 references)

3. completed_at stays as a local variable — NOT moved.

4. No method signatures changed (_finish_no_output_timeout,
   _finish_terminal_timeout_after_output, _build_result, etc. still receive
   keyword arguments with rs.* values).

5. sequence, public_trace, working_body, repair_hops_used,
   pending_repair_hop_index, summary_started stay as locals.

6. No SSE rendering changed. No continuation/repair logic changed.
   No tool lifecycle changed. No decide_stream_event added.

7. Tests: StreamRunStateTests gains ~6 new tests:
   - test_fresh_requires_started_at
   - test_fresh_started_at_preserved
   - test_first_output_at_defaults_to_none
   - test_output_index_offset_defaults_to_zero
   - test_final_usage_defaults_to_empty_normalized
   - test_independence_for_timing_fields

8. Full suite passes (2609 tests or more after new tests added).
```

---

### Intentionally-kept locals (close-out documentation for Slice 2J)

These remain as locals in `run()` by design, with documented reasons:

```text
public_trace         — 46 references; passed positionally to many helpers;
                       high coupling, low migration benefit
sequence             — 35+ references; threaded as returned value through SSE
                       helper functions; changing calling convention is high risk
summary_started      — 35 references; passed to SSE transform; SSE coupling
working_body         — continuation core; mutated between hops;
                       requires dedicated design before any extraction
repair_hops_used /
pending_repair_hop_index — repair flow state; very high blast radius
completed_at         — set at every exit point immediately before use;
                       not cross-hop state; leave as local
```

The Slice 2J close-out will document each of these explicitly in the design doc, confirm no additional extraction is needed, and declare #37 complete.

---

## Cross-references

- `proxy/qz_responses_stream.py` — current side-effect owner; Slice 1 StreamHopState
- `proxy/qz_stream_terminal.py` — pure StreamObservation + classify_stream_terminal()
- `proxy/qz_stream_watchdog.py` — pure StreamWatchdogState predicates + stream_timeout_kind
- `proxy/qz_tool_lifecycle.py` — StreamToolCallState for function_call accumulation
- `proxy/qz_streaming.py` — is_terminal_stream_event() and SSE block construction
- `tests/test_qz_responses_stream.py` — 3927 lines, covers most decisions
- `docs/current-stocktake.md` — current project state
- Issue #37 — architectural seam extraction plan
