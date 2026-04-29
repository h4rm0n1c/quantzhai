# Proxy Telemetry Roadmap

Date: 2026-04-29

## Problem

The proxy currently uses files under `var/` as debugging captures and keeps
live observability on the HTTP/SSE path. That works for development because the
latest request, normalized body, upstream response, and streamed SSE can be
inspected with normal shell tools.

It is not the right long-term live path.

Problems:

- File writes add latency to the request path.
- Raw captures can grow quickly or fill the filesystem if expanded beyond
  latest-file debugging.
- `qz-top` and `qz-thoughts` depend too much on side-effect files instead of a
  structured runtime feed.
- Debug captures, history logs, and live telemetry are different jobs but are
  currently mixed together.

## Direction

Split observability into three separate concepts.

## Telemetry Events

Small structured facts emitted by the proxy while work happens.

Examples:

- request started
- request completed
- model selected
- prompt/generation token counts
- reasoning delta
- reasoning completed
- answer delta
- answer completed
- tool call started/completed
- deprecated endpoint hit
- upstream error
- client disconnect

Telemetry should be cheap and nonblocking. If a consumer is slow, the proxy
should drop old telemetry rather than slowing model output.

## Captures

Heavy raw debugging artifacts.

Examples:

- latest request body
- latest normalized request body
- latest upstream response
- latest raw SSE stream
- latest streamed SSE capture

Captures are useful for debugging, but they should be optional and configurable.

Candidate knobs:

```text
QZ_CAPTURE_MODE=off|minimal|latest|full
QZ_CAPTURE_RAW=0|1
```

Default env should keep captures off. Local debugging or benchmark runs can
enable `latest` or `full` capture modes when raw artifacts are useful. Live
benchmarking should be able to disable raw capture writes or keep only minimal
metadata.

## Logs

Append-only history for postmortem inspection.

Logs should not be the primary transport for `qz-top`, `qz-thoughts`, or future
benchmark monitors. They are for after-the-fact diagnosis.

## Proposed Runtime Shape

Use the proxy HTTP server as the first transport instead of adding custom
sockets immediately.

Candidate endpoints:

```text
GET /qz/telemetry/state
GET /qz/telemetry/recent
GET /qz/telemetry/events
```

Meanings:

- `/qz/telemetry/state`: current counters and status snapshot for `qz-top`
- `/qz/telemetry/recent`: JSON ring buffer for reconnects and debugging
- `/qz/telemetry/events`: SSE stream of structured telemetry events

This keeps the transport simple, works over loopback, and fits the existing
proxy process.

## Proposed Internal Shape

Start with a small module instead of moving old file helpers as-is:

```text
proxy/qz_telemetry.py
  TelemetryEvent
  TelemetryBus
  RingBufferSink
  LatestFileCaptureSink
```

Flow:

```text
proxy emits event
  -> TelemetryBus
  -> in-memory ring buffer
  -> live SSE subscribers
  -> optional file/capture sink
```

Important constraints:

- Do not block response streaming on disk I/O.
- Do not let slow UI clients backpressure model output.
- Keep raw prompt/response capture behind explicit config.
- Preserve current `var/captures/latest-*` debugging during transition.
- Make `qz-top` and `qz-thoughts` prefer telemetry endpoints once available.

## Migration Plan

1. Add `proxy/qz_telemetry.py` with an in-memory ring buffer and no behavior
   changes.
2. Emit a small set of events:
   - request started
   - request completed
   - reasoning delta/done
   - answer delta/done
3. Add local telemetry endpoints.
4. Teach `qz-thoughts` to read `/qz/telemetry/events` or `/recent` before
   falling back to capture files.
5. Teach `qz-top` to read `/qz/telemetry/state` before falling back to Docker
   logs and capture files.
6. Add capture mode config.
7. Move existing capture helpers behind telemetry/capture sinks.

## Next Architectural Choice

Do not extract capture helpers unchanged as the next refactor. Build the
telemetry bus first, then migrate file captures into it as optional sinks.
