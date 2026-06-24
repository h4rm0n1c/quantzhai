# Stream Watchdog Calibration Plan

Date: 2026-05-24
Status: Phase 0 — documentation only. No enforcement active.

Cross-references: `docs/agent-infrastructure-implementation-stocktake.md`,
`proxy/qz_stream_watchdog.py`, `proxy/qz_responses_stream.py`.

---

## A. Purpose

The stream watchdog exists to prevent Codex and other clients from hanging
forever when the streaming pipeline breaks down. Without a watchdog, a client
that sends a request and receives nothing back will sit at "Working…" until the
user kills it manually or the process times out through some external mechanism.

The watchdog is **not** meant to punish slow reasoning. Local quantised models
doing large-context, high-effort tasks legitimately appear idle to the client
for a long time because:

- large prompt prefill (prefilling 64k–256k tokens takes seconds)
- high reasoning level (extended thinking chains produce no visible tokens while reasoning)
- compact/reducer hops (the stream lifecycle restarts internally)
- model load or switch (GPU load latency, quantisation, VRAM movement)
- backend queueing (llamacpp batching, admission control, GPU contention)
- tool-loop handoffs (web search, apply_patch, repeated-read recovery)
- long first-token latency (common on large models at high context)

The distinction the watchdog must make is between **slow but alive** and
**actually wedged**. A stream that is prefilling a 200k-token prompt and will
emit a token in 90 seconds is fine. A stream that has silently dropped its
connection and will never emit anything is not.

The first implementation phase is **observation and calibration**, not
enforcement. We will not enable default timeouts until we have live data that
tells us what the real tail latencies are.

---

## B. Current Status

| Setting | Default | Configurable |
|---|---|---|
| `QZ_STREAM_NO_OUTPUT_TIMEOUT_S` | `300` | yes |
| `QZ_STREAM_TERMINAL_TIMEOUT_S` | `180` | yes |

Source: `proxy/qz_stream_watchdog.py`

```python
STREAM_NO_OUTPUT_TIMEOUT_S = float(os.environ.get("QZ_STREAM_NO_OUTPUT_TIMEOUT_S", "300"))
STREAM_TERMINAL_TIMEOUT_S  = float(os.environ.get("QZ_STREAM_TERMINAL_TIMEOUT_S", "180"))
```

The watchdog is now enabled by default (300s/180s). Set either to 0 to disable.

The unit tests for the watchdog mechanism exist and pass. They prove the
mechanism fires correctly when timeouts are set. They do not calibrate safe
production values.

---

## C. Failure Classes

The watchdog is designed to catch these failure classes, in priority order:

1. **No-stream start**: backend accepted the request but no SSE events arrive.
   The client is left in an indefinite "Working…" state.

2. **Silent stall after stream begins**: stream opened, some events appeared,
   then nothing for too long. Backend may have crashed mid-generation.

3. **Hop never terminates**: a tool/compact/reducer hop started but never
   reaches a terminal state (no `response.completed`, no `response.failed`,
   no error event).

4. **Protocol drift**: backend or client protocol drift causes the stream to
   produce events Codex does not recognise, leaving the client stuck.

5. **Partial output, missing terminal**: stream produces partial output text or
   tool events but never sends a final/terminal SSE event.

6. **Compact task repeated hang**: compact/reducer repeatedly errors or hangs
   without making progress, consuming hops indefinitely.

7. **Connection drop after admission**: TCP connection drops after the backend
   admits the request but before the terminal state is forwarded. Client never
   gets a clean close.

---

## D. Non-Goals

The watchdog must not:

- Kill valid long reasoning merely because visible text is delayed or invisible
  (reasoning chains are legitimately invisible to Codex during thinking phases)
- Mask backend bugs with a fake success response
- Auto-retry or auto-escalate silently
- Mutate model output semantics (no truncation, no injected fake answers)
- Replace stream reducer correctness (reducer bugs are separate problems)
- Enable BrainCase or memory features
- Be tuned to "sounds right" values without live data

---

## E. Timing Metrics to Collect Before Enabling Defaults

Before choosing any default timeout values, the following per-request metrics
must be observed in live traffic. They should be emitted as compact operator
telemetry events — **no full prompts, no full model outputs, no raw tool
arguments, no secrets**.

Required fields per request:

```text
request_id
model_key                        # key from catalog
backend_id                       # resolved backend
reasoning_level                  # low/medium/high/unknown
context_limit_tokens             # selected context window
prompt_tokens_estimate           # best available estimate (not required)
output_tokens                    # if countable from stream
tool_count                       # number of tools in the request
compact_reducer_active           # bool
model_load_switch_active         # bool (model was loading during this request)
backend_queue_depth              # if available from /health probe
time_to_backend_request_start_ms # latency from proxy admission to upstream connect
time_to_first_sse_event_ms       # from upstream connect to first SSE byte
time_to_first_visible_token_ms   # from request start to first output_text delta
time_to_first_tool_call_ms       # from request start to first function_call event
max_gap_between_sse_events_ms    # worst inter-event gap observed in this stream
max_gap_between_visible_events_ms # worst gap between Codex-visible events
time_from_last_event_to_terminal_ms
total_request_duration_ms
terminal_event_type              # response.completed / response.failed / etc.
watchdog_state                   # armed / fired / bypassed / n/a
watchdog_would_have_fired        # bool — was the threshold crossed in observe mode?
watchdog_reason                  # no_output_timeout / terminal_timeout / etc.
```

This data should be collected in **observe-only mode** first (Phase 1), before
any enforcement is active.

---

## F. Percentile-Based Calibration

Timeout values must be chosen from observed live data, not from intuition.

Required observations:

| Percentile | Meaning |
|---|---|
| P50 | typical request — should never be affected |
| P90 | moderately complex request — should never be affected |
| P95 | large context or high-reasoning — must not be affected |
| P99 | tail — long reasoning, compact chains, large tool loops |
| Max sane | largest legitimate request duration observed |
| Hung examples | cases that actually wedged and were manually killed |

Key rules:

- **Tune to avoid killing P99 legitimate requests**, not to the average.
- **Separate request profiles**: a simple chat request and a 128k-context
  high-reasoning compact task are different populations. A single timeout that
  fits both is wrong.
- **Hung examples drive the upper bound**: the timeout must fire on wedged
  requests, but the P99 must not reach that bound under normal operation.
- Collect **at least 1–2 weeks of live data** across varied workloads before
  committing to any default.

---

## G. Provisional Values

### Smoke-only values (for mechanism testing only)

These values are only suitable for proving the watchdog fires correctly under
artificially constrained test conditions. They are completely unsuitable for
production use.

```bash
QZ_STREAM_NO_OUTPUT_TIMEOUT_S=3
QZ_STREAM_TERMINAL_TIMEOUT_S=3
```

Using these values in production **will kill legitimate requests** on any model
larger than a few billion parameters.

### Conservative opt-in calibration values (not final defaults)

These are starting values for live observation on real workloads. They are
deliberately conservative to avoid false positives during calibration.

```bash
QZ_STREAM_NO_OUTPUT_TIMEOUT_S=300
QZ_STREAM_TERMINAL_TIMEOUT_S=180
```

These should only be enabled manually via env var during calibration, not
shipped as defaults. They will be revised once real P99 data is available.

**Do not use these as production defaults without completing Phase 1–3.**

---

## H. Adaptive Budget Sketch

A single fixed timeout is unlikely to work well across the full range of
QuantZhai request profiles. A future adaptive budget model would compute the
timeout per-request based on observable request characteristics.

This is a **design sketch only**. It is not implemented.

### No-output timeout adaptive budget

```text
base:                       180s (minimum)

+60s if context > 64k tokens
+120s if context > 128k tokens
+120s if reasoning level is high
+180s if compact/reducer is active in this hop
→ no limit / greatly extended while model load/switch is active
```

### Terminal timeout

- Starts only after the stream has produced at least one meaningful
  event (visible token, tool call, or explicit model response event).
- Should be shorter than the no-output timeout since by the time the
  terminal timeout fires, we know the model has already started responding.
- Must reset on valid progress events (each new event resets the counter).
- Conservative initial candidate: 180s after last progress event.

### Important constraints

- The no-output timeout and terminal timeout are independent. The no-output
  timeout covers the silent-before-first-output case; the terminal timeout
  covers the case where output began but the stream never closed.
- Neither timeout should fire while a backend model load/switch is known to
  be in progress. This is the single most common source of false positives.
- Tool-loop handoffs (web search, apply_patch) restart the hop; the timeout
  budget should reset accordingly.

---

## I. Watchdog Modes

The watchdog is designed to move through four phases:

### Mode 1 — Disabled (current default)

No timeouts enforced. The watchdog code exists but does not interrupt any
stream. `QZ_STREAM_NO_OUTPUT_TIMEOUT_S = 0` disables the no-output watchdog.
`QZ_STREAM_TERMINAL_TIMEOUT_S = 0` disables the terminal watchdog.

Suitable for: current state. No calibration data yet.

### Mode 2 — Observe-only

Watchdog computes whether a timeout **would have fired** given configurable
thresholds, but does not interrupt the stream. Emits telemetry events:

```text
stream_no_output_timeout_observed
stream_terminal_timeout_observed
```

No client impact. Allows safe data collection before any enforcement.

Suitable for: Phase 1 (see Section M).

### Mode 3 — Opt-in enforcement

Watchdog enforces timeouts only when explicitly enabled via env vars. Operators
running calibration studies can enable and observe the impact.

```bash
QZ_STREAM_NO_OUTPUT_TIMEOUT_S=300
QZ_STREAM_TERMINAL_TIMEOUT_S=180
```

Suitable for: Phases 3–4. Must be paired with live smoke tests and
qz-thoughts/qz-top visibility before being recommended to users.

### Mode 4 — Conservative defaults

Default timeouts shipped in the base configuration, chosen from live P99 data
after at minimum two weeks of calibration in Mode 2/3. Values will be
documented in this file before shipping.

Suitable for: Phase 5. **Not yet determined.**

---

## J. Model/Client-Facing Behaviour When Watchdog Fires

The watchdog must leave the client in a clean state. Requirements:

- Emit a clear terminal/fallback SSE event that Codex accepts and exits
  "Working…" for.
- Mark the response as **interrupted** or **failed**, not as successful.
- Preserve the watchdog reason in telemetry (which timeout fired, how long it
  waited, what phase the stream was in).
- Do not inject a fake model answer or truncated answer.
- Do not silently retry — if the operator wants retry logic, it must be
  explicit and logged.
- Do not silently truncate the partial output already received.
- Do not suppress the watchdog event in qz-thoughts (see Section K).

The existing `_emit_no_output_timeout_fallback` and
`_emit_terminal_timeout_completion` helpers in `qz_responses_stream.py` are the
implementation anchors for this behaviour. They must not be changed to produce
fake successful responses.

---

## K. Operator Visibility

When watchdog timeouts are active (Mode 2 or higher), operators should be able
to observe them in real time.

### qz-thoughts

Expected events in the telemetry stream:

```text
stream_no_output_timeout_observed   # observe-only: would have fired
stream_terminal_timeout_observed    # observe-only: would have fired
stream_no_output_timeout_fired      # enforcement: stream interrupted
stream_terminal_timeout_fired       # enforcement: stream interrupted
```

Event names are provisional. They should include:
- `request_id`
- `timeout_s` — the configured threshold that was crossed
- `elapsed_s` — actual elapsed time when the event was generated
- `watchdog_phase` — which phase (no_output / terminal)
- `stream_phase` — what the stream was doing (prefill / generation / hop / etc.)
- `model_key` — which model was in use

### qz-top

If feasible, the active-request view should show the watchdog state for each
in-flight request: `armed`, `fired`, or `bypassed`.

### qz-agent-infra-stocktake

The stocktake script should remain at **WARN disabled** until Mode 4 is active.
Intermediate phases will not be marked PASS — observe-only mode is not a
complete solution.

---

## L. Live Smoke Plan

Before enabling any enforcement mode in production, the following smoke steps
must pass:

1. **Normal stream should not trigger watchdog.** A standard chat request
   completes cleanly with no watchdog events in telemetry.

2. **Artificial no-output backend triggers no-output watchdog.** A backend that
   accepts the connection but emits no SSE bytes triggers
   `stream_no_output_timeout_fired` within the configured timeout under smoke
   values (`QZ_STREAM_NO_OUTPUT_TIMEOUT_S=3`).

3. **Artificial missing-terminal stream triggers terminal watchdog.** A stream
   that emits output but never sends a terminal event triggers
   `stream_terminal_timeout_fired` under smoke values.

4. **Long reasoning task does not trigger under conservative opt-in values.**
   A high-effort 128k-context request completes without triggering any watchdog
   event when `QZ_STREAM_NO_OUTPUT_TIMEOUT_S=300`.

5. **Compact/reducer task does not trigger under normal slow-but-alive progress.**
   A compact chain that produces events infrequently but continuously does not
   trip the terminal watchdog if each hop completes within the budget.

6. **qz-thoughts shows watchdog state.** Telemetry events appear in qz-thoughts
   during and after any fired watchdog.

7. **Codex client exits "Working…" cleanly when watchdog fires.** The Codex
   UI accepts the terminal/fallback event emitted by the watchdog and stops
   waiting.

---

## M. Recommended Implementation Phases

### Phase 0 — Documentation (this task)

Write the calibration plan. No code changes.

Deliverable: this file.

### Phase 1 — Observe-only timing telemetry

Add observe-only timing fields to stream lifecycle telemetry events. No
enforcement. No new timeouts. No new env vars (or a single opt-in
`QZ_WATCHDOG_OBSERVE_MODE=1` flag).

Collect the fields listed in Section E as part of normal stream telemetry.

Deliverable: timing fields in `stream_completed`/`stream_failed` telemetry.

### Phase 2 — Calibration report command

Add a script that reads historical telemetry and produces a calibration
summary: P50/P90/P95/P99 latencies, max observed gaps, distribution by model
key/reasoning level/context size.

Deliverable: `scripts/qz-watchdog-calibration-report` (or similar).

### Phase 3 — Live smoke harness

Add a smoke test for the no-output and missing-terminal failure classes using
the smallest viable smoke values (`QZ_STREAM_NO_OUTPUT_TIMEOUT_S=3`). This
proves the mechanism works end-to-end before opt-in values are recommended.

Deliverable: `scripts/qz-watchdog-smoke` (or integrated into `qz-live-smoke`).

### Phase 4 — Opt-in enforcement

Enable enforcement via env vars only. Document the recommended conservative
opt-in values derived from Phase 1–2 data. Update stocktake to PASS for
opt-in mode.

Deliverable: env-var-controlled enforcement + updated stocktake + smoke results.

### Phase 5 — Conservative defaults

Only after:
- At least two weeks of Phase 1 observe-only data
- Phase 3 smoke harness passing on live hardware
- Phase 4 opt-in running without false positives for at least one week

Set conservative defaults derived from real P99 data. Document final values
here. Update stocktake to PASS unconditionally.

Deliverable: default values in `qz_stream_watchdog.py` + this file updated with
rationale and P99 evidence.

---

## N. Open Questions

These must be resolved before Phase 4:

1. What is the actual P99 no-output latency for a 128k-context high-reasoning
   request on the current hardware?
2. What is the actual P99 no-output latency during a model load/switch?
3. Should the no-output timeout be suspended (not just extended) during model
   load, or is a +180s addition sufficient?
4. Do compact/reducer hops reset the no-output timeout or only the terminal
   timeout?
5. Should different request profiles (small chat vs. large agentic) have
   separate timeout budgets, or is a single adaptive formula sufficient?
6. Is backend connection state (`/health` probe result) a reliable signal for
   "model is loading" or does it lag too much?
