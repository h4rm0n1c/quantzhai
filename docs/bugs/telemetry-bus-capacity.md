# Bug: Telemetry bus capacity — stream_event_timing evicts lifecycle events

Status: **Fixed** — 2026-05-11 (session 4).

`REQUEST_RETAINED_EVENT_TYPES` split into `REQUEST_LIFECYCLE_EVENT_TYPES` and
`REQUEST_TIMING_EVENT_TYPES`. Per-request store now keeps two separate deques:
lifecycle (`maxlen=200`) and timing (`maxlen=150`). `_request_events_for_locked`
merges both sorted by seq. Timing events can no longer evict lifecycle events.
Regression test: `test_timing_events_cannot_evict_lifecycle_events`.

## Symptom (pre-fix)

High-frequency `stream_event_timing` events fill the per-request telemetry
ring buffer and push out meaningful lifecycle events:

```
hop_budget_signal
tool_call_started
tool_call_completed
auto_compaction_triggered
```

After a moderately long streamed turn, `/qz/telemetry/recent` and the
per-request buffer may contain no lifecycle events at all — only
`stream_event_timing` entries.

## Root cause

`qz_telemetry.py` keeps a bounded per-request event list. Every SSE chunk
forwarded by the stream runtime emits a `stream_event_timing` event. On a
100-token streamed response, that is ~100 timing events competing for the same
fixed-size buffer as the handful of lifecycle events that matter for
observability (tool calls, compaction, hop signals, errors).

## Impact

- `qz-top` and `qz-thoughts` may miss tool/compaction activity entirely.
- The `auto_compaction_triggered` suppression label written by the compaction
  bridge is at risk of being evicted before a monitor reads it.
- Post-session debugging via `/qz/telemetry/recent` loses signal when sessions
  are long.

## Fix options (in order of preference)

1. **Partition buffers** — give `stream_event_timing` its own lower-priority
   ring separate from the lifecycle event ring. Monitors that need timing data
   read from the timing ring; monitors that need activity read from the
   lifecycle ring.

2. **Discard policy** — when the per-request buffer is full, prefer evicting
   `stream_event_timing` over lifecycle events. Simple to implement; slightly
   lossy on timing data for long turns.

3. **Raise capacity** — increase the per-request buffer from 200 to a larger
   value. Buys time but does not fix the structural problem.

## Do not fix by

- Dropping `stream_event_timing` globally. `qz-top` uses timing samples to
  compute token rates and latency; removing them breaks that panel.
- Filtering in the monitor. Monitors are read-only consumers and should not
  need to compensate for missing upstream data.

## Related

- `proxy/qz_telemetry.py` — buffer sizing and event ingestion.
- `docs/master-stabilisation-plan.md` — next engineering target.
- `docs/observability-streaming-bugfix-agenda.md` — broader telemetry agenda.
