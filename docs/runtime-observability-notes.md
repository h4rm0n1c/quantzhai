# Runtime Observability Notes

Date: 2026-04-29

## What We Discovered

- `qz-top` recent activity can fail silently if it asks the sudo Docker helper
  for more log lines than the helper allows. The helper boundary is
  `docker logs --tail <= 1000`, so monitor defaults need to stay inside that
  limit.
- The current local `/v1/responses` tool/search path uses a buffered upstream
  request and then emits synthetic Responses SSE from the completed response.
- Because of that buffering, `qz-thoughts` can show live backend activity from
  logs, but thought/reasoning text from Responses captures is not token-live
  yet.
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

See also: `docs/agent-runtime-session-notes-2026-04-29.md`.
