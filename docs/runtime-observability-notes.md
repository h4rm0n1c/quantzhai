# Runtime Observability Notes

Date: 2026-04-29

## What We Discovered

- `qz-top` recent activity can fail silently if it asks the sudo Docker helper
  for more log lines than the helper allows. The helper boundary is
  `docker logs --tail <= 1000`, so monitor defaults need to stay inside that
  limit.
- Codex sandbox networking can produce false negatives for local proxy tests:
  a proxy started inside the sandbox may not be reachable from host curls, and
  host services may not be visible from sandboxed curls. Live stack validation
  should run `qz-proxy`, curl probes, `qz-top --once`, and `qz-thoughts --once`
  on the host network.
- The current local `/v1/responses` tool/search path uses a buffered upstream
  request and then emits synthetic Responses SSE from the completed response.
- Because of that buffering, `qz-thoughts` can show live backend activity from
  logs, but thought/reasoning text from Responses captures is not token-live
  yet.
- The proxy telemetry path works for synthetic Responses SSE: a streamed
  `/v1/responses` request emits `sse_event` telemetry, `qz-top` reports the
  resulting throughput, and `qz-thoughts` can reconstruct the latest thought and
  answer without reading capture files.
- Small `max_output_tokens` caps can produce reasoning-only responses on this
  profile. That is model/profile tuning behavior, not a monitor failure, and it
  should be measured when comparing grug/caveman prompt variants.
- Real live thought viewing needs a streamed Responses runtime that can pause
  at tool calls, execute local tools, append tool results, and continue with
  another streamed upstream request.

## What Changed

- `scripts/qz-top` now keeps log scanning inside the sudo helper's supported
  tail limit and surfaces log/helper failures as recent activity instead of
  making the stack look broken.
- `scripts/qz-proxy` starts the proxy in a detached session when possible, so
  the proxy survives after the launcher exits under command runners or terminal
  wrappers.
- `scripts/qz-up` has convenience modes:
  - `--hold` starts the stack and then opens `qz-top`.
  - `--codex PROFILE` starts the stack and then launches Codex with the selected
    profile.
- `scripts/qz-thoughts` was added as a curses-style monitor for synthetic
  thought/output captures and live backend activity.
- `scripts/qz-thoughts` now uses proxy telemetry first, isolates the latest
  response window, and filters health/telemetry poll noise from its activity
  view.
- `README.md` documents the new launcher and monitor entry points.

## Roadmap Impact

- Multi-hop streamed Responses with tool-call continuation is now a first-class
  proxy roadmap item.
- Captures should become run-scoped so `qz-top`, `qz-thoughts`, benchmark runs,
  and proxy request logs can point at the same execution instead of fighting
  over latest-only files.
- The architecture split should include a testable streaming state machine,
  incremental capture writer, and fixtures for streamed tool continuation,
  buffered fallback, malformed events, and cancellation.
- Runtime monitors should eventually display search budget use, pages fetched,
  returned search tokens, cache hits, and exact run timestamps.
- Agents should receive a stable current date/timezone anchor, with exact clock
  time fetched only when the task needs it, so time-aware work is grounded
  without destroying prompt-cache reuse.
- QuantZhai's concurrency target is single-user local performance, not
  multi-user serving. Researching Linux process schedulers and interactive
  scheduling patterns may still provide useful hints for prioritizing the
  foreground Codex session, proxy streaming, monitors, tool subprocesses, and
  backend inference without adding a complex application-level scheduler.
- Classic Mac OS cooperative multitasking may also be worth reviewing as a
  low-confidence analogy: not as an implementation model, but for ideas around
  explicit yielding, foreground task priority, and keeping a single-user
  interactive system responsive under constrained resources.

See also: `docs/agent-runtime-session-notes-2026-04-29.md`.
