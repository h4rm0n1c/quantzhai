# Codex 0.130 Live Signal Capture Audit

Date: 2026-05-12T17:20+08:00
Source: Live Codex 0.130.0 probe run, `QZ_CAPTURE_MODE=full`

## Current status note

This audit was produced before the final parser/context cleanup landed. Preserve
the observed capture facts below, but use this status note for current
implementation state.

Current implementation status:

```text
parse_codex_window_id: implemented
extract_codex_body_metadata: implemented
extract_codex_request_context: implemented
resolve_workspace_id: implemented
resolve_memory_domain: implemented as an isolated-default skeleton
generate_qz_session_id: implemented
text.verbosity parsing: implemented
unsolicited forwarded body.metadata qz_* injection: removed
```

Relevant files:

```text
proxy/qz_codex_metadata.py
tests/test_qz_codex_metadata.py
tests/test_qz_codex_request_metadata.py
tests/test_qz_request_mutation_regression.py
docs/current-architecture-authority.md
docs/codex-context-memory-contract.md
```

Important ownership correction:

```text
QuantZhai parses Codex context internally.
QuantZhai must not inject qz_session_id, qz_workspace_id, qz_memory_domain, or qz_text_verbosity into forwarded /v1/responses request bodies.
```

Recent verification after cleanup:

```text
focused parser/router suite: 165 passed
full test suite: 377 passed
live Codex smoke: passed
/health: stable
forbidden qz context metadata in recent captures: not found
```

## Verdict

**Ready for Phase 1 SQLite substrate.**

The live capture confirms that Codex 0.130 sends the session, thread, turn,
workspace, and request metadata needed for Phase 1 SQLite. The parser/helper
surface now covers the observed header/body/window/workspace shapes. Phase 1
SQLite should consume `extract_codex_request_context()` internally and must not
modify forwarded request bodies.

Phase 1 remains limited to structured operational facts. It must not implement
model-visible durable memory, learned preferences, profile-private memory,
HSM/archive memory, automatic promotion, or repeated-read v2 behaviour changes.

## Run inspected

| Field | Value |
| :--- | :--- |
| Probe stdout | `var/audits/qz-codex-0130-signal-probe-20260512-171756.out` |
| Probe stderr | `var/audits/qz-codex-0130-signal-probe-20260512-171756.err` (empty) |
| Capture directory pattern | `var/captures/requests/qz_req_1778577481074_bba0/` through `qz_req_1778577521329_3a00/` |
| Requests inspected | 6 |
| Selection method | All requests with mtime 2026-05-12 17:18 matching the probe window |
| Capture mode | `full` |

## Header signal matrix

All 6 requests carried identical headers (session_id, thread_id, window_id, etc.
were stable across the turn). Codex 0.130 sends one POST to `/v1/responses` per
command/reasoning/message item.

| Signal | Count | Example value | Current parser coverage | Action |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` | `extract_codex_identity` — covered | Persist in Phase 1 DB |
| `session-id` | 0/6 | — | Hyphenated fallback exists | Nullable/backward-compatible |
| `thread_id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` (same UUID as session in this run) | covered | Persist in Phase 1 DB |
| `thread-id` | 0/6 | — | Hyphenated fallback exists | Nullable/backward-compatible |
| `x-client-request-id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` (same UUID in this run) | covered | Capture/persist as request metadata |
| `x-codex-window-id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1:0` | raw + parsed by `parse_codex_window_id` | Persist raw/thread/generation |
| `x-codex-turn-metadata` | 6/6 | Valid JSON object | raw + parsed; workspace candidates extracted | Persist raw + parsed summary |
| `x-codex-turn-state` | 0/6 | — | parser slot exists | Keep nullable/deferred |
| `x-codex-installation-id` | 0/6 | — | header parser slot exists; body client_metadata parser extracts observed value | Keep nullable |
| `x-codex-parent-thread-id` | 0/6 | — | parser slot exists | Keep nullable/deferred |
| `x-openai-subagent` | 0/6 | — | parser slot exists | Keep nullable/deferred |
| `x-openai-memgen-request` | 0/6 | — | parser slot exists with boolean-ish parsing | Keep nullable/deferred |
| `originator` | 6/6 | `codex_exec` | covered | Capture/persist raw |
| `user-agent` | 6/6 | `codex_exec/0.130.0 (Linux Unknown; x86_64) xterm (codex_exec; 0.130.0)` | covered | Capture/persist raw |
| `authorization` | 6/6 | `[present; redacted]` | intentionally not parsed | Keep in local captures only |
| `content-type` | 6/6 | `application/json` | not needed | Not needed |
| `accept` | 6/6 | `text/event-stream` | not needed | Not needed |

**Key header finding — Codex 0.130 now sends `thread_id`:**

The previous 0.125.0 capture (`docs/codex-header-capture-verdict.md`) found
`thread_id` absent. Codex 0.130.0 sends it in every request. The value is
identical to `session_id` in this probe. Do not assume that equality is a
permanent protocol guarantee.

## Body signal matrix

| Signal | Count | Example shape | Current parser coverage | Action |
| :--- | :--- | :--- | :--- | :--- |
| `model` | 6/6 | `"prompt-compiler"` (profile alias) | routing/profile code, not request metadata parser | Store profile/backend separately if needed |
| `instructions` | 0/6 | — | absent in this path | Not needed |
| `input` | 6/6 | Array of message/tool-history objects | not stored whole by default | Store digest/capture pointer only |
| `tools` | 6/6 | Array of 12 tool declarations | `extract_codex_body_metadata` summarises count/names | Persist summary only |
| `tool_choice` | 6/6 | `"auto"` | parsed | Persist nullable if useful |
| `parallel_tool_calls` | 6/6 | `true` | parsed | Persist nullable if useful |
| `reasoning` | 6/6 | `{"effort": "high"}` | effort/summary parsed | Persist reasoning_effort |
| `service_tier` | 0/6 | — | parser slot exists | Keep nullable/deferred |
| `prompt_cache_key` | 6/6 | same UUID as session/thread in this run | parsed | Persist |
| `previous_response_id` | 0/6 | — | parser slot exists | Keep nullable/deferred |
| `text` | 6/6 | `{"verbosity": "low"}` | verbosity + schema-presence parsed | Persist text_verbosity |
| `include` | 6/6 | `["reasoning.encrypted_content"]` | parsed as list | Persist nullable if useful |
| `stream` | 6/6 | `true` | parsed | Persist nullable if useful |
| `store` | 6/6 | `false` | parsed | Persist |
| `client_metadata` | 6/6 | `{"x-codex-installation-id": "c9b4199c-2804-4533-82cc-9b4ec3c171bd"}` | parsed; installation id extracted | Persist summary/json |
| `metadata` (top-level) | 0/6 | — | not used for qz_* context injection | Do not inject internal context |

**Key body findings:**

- `prompt_cache_key` is present in all 6 requests and matches the
  `session_id`/`thread_id` UUID in this probe.
- `client_metadata` is present in all 6 and contains only
  `x-codex-installation-id`.
- `previous_response_id` is absent in all 6. This HTTP/SSE path does not use
  stateful response chaining.
- `service_tier` is absent.
- `store` is present and `false` in all requests.

## Turn metadata findings

| Property | Finding |
| :--- | :--- |
| Valid JSON | Yes — all 6 payloads are valid |
| Top-level keys | `session_id`, `thread_id`, `turn_id`, `sandbox`, `turn_started_at_unix_ms`, `workspaces` (5/6) |
| `session_id` | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` — matches header |
| `thread_id` | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` — matches header |
| `turn_id` | `019e1b7a-9d50-7001-93bf-6dd9e9b83459` — unique per turn, stable across all 6 requests |
| `turn_started_at_unix_ms` | `1778577481046` — stable across all 6 requests |
| `sandbox` | `"seccomp"` in all 6 |
| `workspaces` | Absent in 1st request, present in 5 subsequent requests |
| session_id header vs turn_metadata.session_id | Match — no conflict |
| thread_id header vs turn_metadata.thread_id | Match — no conflict |

### Workspace details (from turn metadata)

| Field | Value |
| :--- | :--- |
| repo_root | `/home/harri/turboquant/quantzhai` |
| remote origin | `git@github.com:h4rm0n1c/quantzhai.git` |
| latest_git_commit_hash | `cfad80f179285ba0ff6dbfa813fa2338790c4181` |
| has_changes | `false` |

Only one workspace entry was observed. The remote URL uses SSH format. The
current workspace resolver treats remote URL evidence as preferred over local
path evidence and keeps path-derived IDs hashed.

### UUID overlap observation

In this Codex 0.130 probe, these identifiers all carry the same UUID:

```text
session_id header
thread_id header
x-client-request-id header
x-codex-window-id thread part
prompt_cache_key body field
turn_metadata.session_id
turn_metadata.thread_id
```

Only `turn_id` is a different UUID.

This is useful affinity evidence, not a guarantee. Phase 1 DB should store these
fields separately where helpful, keep conflicts detectable, and use
`qz_session_id` as the internal primary session key.

## Tool declaration findings

| Property | Finding |
| :--- | :--- |
| Tools per request | 12 declarations, identical across all 6 requests |
| Tool choice | `"auto"` |
| `parallel_tool_calls` | `true` |
| Declaration shape | Top-level `name` for function/custom tools; typed built-ins such as `web_search` may have no name |

### Detected tools

| name | type |
| :--- | :--- |
| `exec_command` | `function` |
| `write_stdin` | `function` |
| `update_plan` | `function` |
| `request_user_input` | `function` |
| `apply_patch` | `custom` |
| `view_image` | `function` |
| `spawn_agent` | `function` |
| `send_input` | `function` |
| `resume_agent` | `function` |
| `wait_agent` | `function` |
| `close_agent` | `function` |
| `web_search` | `web_search` |

Current parser coverage handles top-level Codex tool names, nested
`function.name`, custom tools, and type-only tools such as `web_search`.

## Parser coverage verdict

Current parser/helper coverage is sufficient for Phase 1 SQLite substrate:

```text
extract_codex_identity: implemented and tested
parse_codex_window_id: implemented and tested
extract_codex_body_metadata: implemented and tested
extract_codex_request_context: implemented and tested
resolve_workspace_id: implemented and tested
resolve_memory_domain: implemented as isolated-default skeleton and tested
generate_qz_session_id: implemented and tested
```

Current ownership regression guard:

```text
tests/test_qz_request_mutation_regression.py confirms forwarded request bodies do not gain qz_session_id, qz_workspace_id, qz_memory_domain, or qz_text_verbosity.
```

## SQLite readiness

**Phase 1 SQLite can start now** with the following fields justified by live
Codex 0.130.0 capture evidence and current parser coverage.

### Sessions table

| Field | Evidence |
| :--- | :--- |
| `qz_session_id` | Generated internally by QuantZhai |
| `client_session_id` | `session_id` header — 6/6 confirmed |
| `client_thread_id` | `thread_id` header — 6/6 confirmed |
| `client_installation_id` | `client_metadata.x-codex-installation-id` — 6/6 confirmed |
| `originator` | `originator` header — 6/6 confirmed |
| `user_agent` | `user-agent` header — 6/6 confirmed |
| `identity_conflict` / `conflict_notes_json` | Parser support exists |

### Turns table

| Field | Evidence |
| :--- | :--- |
| `qz_turn_id` | QuantZhai-owned grouping key |
| `qz_session_id` | QuantZhai-owned session FK |
| `client_thread_id` | Header/turn metadata — 6/6 confirmed |
| `codex_turn_id` | Turn metadata — 6/6 confirmed |
| `turn_started_at_unix_ms` | Turn metadata — 6/6 confirmed |
| `codex_window_id` | Header — 6/6 confirmed |
| `codex_window_thread_id` | Parsed from window id |
| `codex_window_generation` | Parsed from window id |
| `turn_state_raw` | Nullable; absent in this probe |

### Requests table

| Field | Evidence |
| :--- | :--- |
| `qz_request_id` | Generated by QuantZhai |
| `qz_session_id` | Internal session FK |
| `qz_turn_id` | Internal turn FK, nullable while grouping matures |
| `client_request_id` | `x-client-request-id` header — 6/6 confirmed |
| `prompt_cache_key` | Body field — 6/6 confirmed |
| `profile_id` / `model` / `backend_id` | Routing/profile facts, not Codex-owned |
| `reasoning_effort` | `body.reasoning.effort` — 6/6 confirmed |
| `reasoning_summary` | Parser slot exists |
| `text_verbosity` | `body.text.verbosity` — 6/6 confirmed |
| `store` | `body.store` — 6/6 confirmed |
| `stream` | `body.stream` — 6/6 confirmed |
| `tools_count` | Body tools length — 6/6 confirmed |
| `tool_names_json` | Body tools summary — 6/6 confirmed |
| `client_metadata_json` | Body client_metadata — 6/6 confirmed |
| `body_digest` | Recommended; do not store full body by default |

### Workspace candidates table

| Field | Evidence |
| :--- | :--- |
| `qz_request_id` | Generated by QuantZhai |
| `qz_session_id` | Internal session FK |
| `qz_turn_id` | Internal turn FK when available |
| `repo_root` | Turn metadata workspaces key — 5/6 confirmed |
| `repo_root_hash` | Recommended; avoid public/model-visible raw path identity |
| `remote_name` | Turn metadata associated remotes |
| `remote_url` | Turn metadata associated remotes — 5/6 confirmed |
| `normalized_remote_url` | Resolver input |
| `latest_git_commit_hash` | Turn metadata — 5/6 confirmed |
| `has_changes` | Turn metadata — 5/6 confirmed |

### Resolved workspaces and bindings

Follow `docs/codex-context-memory-contract.md`:

```text
resolved_workspaces
session_workspace_bindings
```

Remote URL evidence wins over local path hash. Unknown workspace is allowed and
must not crash the proxy.

### Fields still not observed

Keep nullable/deferred:

```text
previous_response_id
service_tier
subagent
is_memgen
turn_state_raw
parent_thread_id
```

## Open questions

1. Does `session_id` always equal `thread_id` in Codex 0.130?
2. Does `prompt_cache_key` always equal `client_thread_id`?
3. Does `x-codex-turn-state` appear in WebSocket/compact/prewarm paths?
4. Does `previous_response_id` appear in any non-streaming path?
5. Does `client_metadata` ever carry fields beyond `x-codex-installation-id`?

None of these block Phase 1 SQLite because the schema can keep those fields
nullable and conflict-aware.
