# Codex 0.130 Live Signal Capture Audit

Date: 2026-05-12T17:20+08:00
Source: Live Codex 0.130.0 probe run, `QZ_CAPTURE_MODE=full`

## Verdict

**Ready for SQLite — with one parser slice still needed for body-level signals.**

The live capture confirms that Codex 0.130 sends the full set of session, thread,
turn, workspace, and request metadata needed for Phase 1 SQLite. The current
header parser (`extract_codex_identity` in `proxy/qz_codex_metadata.py`) covers
all observed header signals. The planned body-metadata functions
(`extract_codex_body_metadata`, `extract_codex_request_context`) are required
before DB persistence but can be implemented alongside the first DB substrate.

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

| Signal | Count | Example value | Parser coverage | Action |
| :--- | :--- | :--- | :--- | :--- |
| `session_id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` | `extract_codex_identity` — covered | Persist in Phase 1 DB |
| `session-id` | 0/6 | — | Hyphenated fallback exists in parser | Not sent by 0.130 |
| `thread_id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` (same UUID as session) | `extract_codex_identity` — covered | Persist in Phase 1 DB |
| `thread-id` | 0/6 | — | Hyphenated fallback exists in parser | Not sent by 0.130 |
| `x-client-request-id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1` (same UUID) | `extract_codex_identity` — covered | Capture raw |
| `x-codex-window-id` | 6/6 | `019e1b7a-9d23-77d2-9811-b4ce727bafb1:0` — `{thread_id}:{generation}` | `extract_codex_identity` — covered | Persist; planned `parse_codex_window_id` can parse thread/gen |
| `x-codex-turn-metadata` | 6/6 | Valid JSON object (see turn metadata section) | `extract_codex_identity` — covered | Persist raw + parsed |
| `x-codex-turn-state` | 0/6 | — | Not covered, not emitted by 0.130 | No action until observed |
| `x-codex-installation-id` | 0/6 | — | Not covered as header; sent inside `client_metadata` body field | Add body-level extraction |
| `x-codex-parent-thread-id` | 0/6 | — | Not covered, not emitted by 0.130 in this path | No action until observed |
| `x-openai-subagent` | 0/6 | — | Not covered, not emitted by 0.130 in this path | No action until observed |
| `x-openai-memgen-request` | 0/6 | — | Not covered, not emitted by 0.130 in this path | No action until observed |
| `originator` | 6/6 | `codex_exec` | `extract_codex_identity` — covered | Capture raw |
| `user-agent` | 6/6 | `codex_exec/0.130.0 (Linux Unknown; x86_64) xterm (codex_exec; 0.130.0)` | `extract_codex_identity` — covered | Capture raw |
| `authorization` | 6/6 | `[present; redacted]` | Not parsed (intentionally) | Keep in captures only |
| `content-type` | 6/6 | `application/json` | Not parsed | Not needed |
| `accept` | 6/6 | `text/event-stream` | Not parsed | Not needed |

**Key header finding — Codex 0.130 now sends `thread_id`:**

The previous 0.125.0 capture (docs/codex-header-capture-verdict.md) found
`thread_id` absent. Codex 0.130.0 sends it in every request. The value is
identical to `session_id` (same UUID). Both underscore variants are used;
hyphenated variants are never sent.

## Body signal matrix

| Signal | Count | Example shape | Parser coverage | Action |
| :--- | :--- | :--- | :--- | :--- |
| `model` | 6/6 | `"prompt-compiler"` (profile alias) | Not parsed — used in routing | Not needed in DB |
| `instructions` | 0/6 | — | Not parsed | Absent; codex uses input-based mode |
| `input` | 6/6 | Array of message objects | Not parsed (large) | Store digest only |
| `tools` | 6/6 | Array of 12 tool declarations | `extract_codex_body_metadata` planned | Store tool_names, tools_count |
| `tool_choice` | 6/6 | `"auto"` | Not parsed | Not needed |
| `parallel_tool_calls` | 6/6 | `true` | Not parsed | Not needed |
| `reasoning` | 6/6 | `{"effort": "high"}` | `extract_codex_body_metadata` planned | Persist reasoning_effort |
| `service_tier` | 0/6 | — | `extract_codex_body_metadata` planned | Not sent by 0.130 |
| `prompt_cache_key` | 6/6 | `"019e1b7a-9d23-77d2-9811-b4ce727bafb1"` (same UUID as session_id) | Not yet parsed; `extract_codex_body_metadata` planned | Persist in Phase 1 DB |
| `previous_response_id` | 0/6 | — | Not yet parsed | Absent in this capture path |
| `text` | 6/6 | `{"verbosity": "low"}` | Not yet parsed | Persist verbosity |
| `include` | 6/6 | `["reasoning.encrypted_content"]` | Not yet parsed | Not needed |
| `stream` | 6/6 | `true` | Not parsed | Not needed |
| `store` | 6/6 | `false` | Not yet parsed | Persist for session context |
| `client_metadata` | 6/6 | `{"x-codex-installation-id": "c9b4199c-2804-4533-82cc-9b4ec3c171bd"}` | `extract_codex_body_metadata` planned | Persist client_metadata JSON; extract installation_id |
| `metadata` (top-level) | 0/6 | — | Not parsed | Absent; proxy adds its own `metadata` |

**Key body findings:**

- `prompt_cache_key` is present in all 6 requests and matches the
  `session_id`/`thread_id` UUID. This is a strong session-affinity signal.
- `client_metadata` is present in all 6 and contains only
  `x-codex-installation-id`. No `cwd`, `personality`, or other fields observed.
- `previous_response_id` is absent in all 6. This HTTP/SSE path does not use
  stateful response chaining.
- `service_tier` is absent; Codex 0.130 does not request a service tier.
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
| `workspaces` | Absent in 1st request (initial turn), present in 5 subsequent requests |
| session_id header vs turn_metadata.session_id | Match — no conflict |
| thread_id header vs turn_metadata.thread_id | Match — no conflict |

### Workspace details (from turn metadata)

| Field | Value |
| :--- | :--- |
| repo_root | `/home/harri/turboquant/quantzhai` |
| remote origin | `git@github.com:h4rm0n1c/quantzhai.git` |
| latest_git_commit_hash | `cfad80f179285ba0ff6dbfa813fa2338790c4181` |
| has_changes | `false` |

Only one workspace entry observed. The remote URL uses SSH format
(`git@github.com:...`), not HTTPS. The existing `_normalize_remote_url`
function handles this correctly (no `.git` suffix, no trailing slash to strip).

### UUID overlap observation

In Codex 0.130, these identifiers all carry the **same UUID**:

```
session_id (header)            = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
thread_id (header)             = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
x-client-request-id (header)   = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
x-codex-window-id thread part  = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
prompt_cache_key (body)        = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
turn_metadata.session_id       = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
turn_metadata.thread_id        = 019e1b7a-9d23-77d2-9811-b4ce727bafb1
```

Only `turn_id` is a different UUID: `019e1b7a-9d50-7001-93bf-6dd9e9b83459`.

This means Codex 0.130 may be using the session UUID as a universal request
affinity key. Phase 1 DB should normalise on `qz_session_id` as internal
primary key and map the external UUID to `client_session_id`. The other fields
(`prompt_cache_key`, `x-client-request-id`, etc.) can reference the same value
without requiring separate UUID columns.

## Tool declaration findings

| Property | Finding |
| :--- | :--- |
| Tools per request | 12 declarations, identical across all 6 requests |
| Tool choice | `"auto"` |
| `tool_choice` is string | yes |
| `parallel_tool_calls` | `true` |
| Declaration shape | **Top-level `name` only** — no nested `function.name` |

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
| `web_search` | `web_search` (no `name`; has `external_web_access: false`) |

### Shape observation

Codex 0.130 uses top-level `name` for all `type=function` and `type=custom`
tools. The `web_search` tool has `type=web_search` and carries `name` as empty
string. The existing `extract_workspace_candidates` does not parse tools;
the planned `extract_codex_body_metadata` function should handle both shapes:

- `{"type": "function", "name": "exec_command"}` → `exec_command`
- `{"type": "custom", "name": "apply_patch"}` → `apply_patch`
- `{"type": "web_search", "external_web_access": false}` → `web_search`

The existing `test_qz_codex_request_metadata.py` already has tests covering
these shapes (top-level name, custom type, web_search type).

## Parser coverage verdict

### `extract_codex_identity` (current, in `proxy/qz_codex_metadata.py`)

**Covers all observed header signals:**

- `session_id` — yes
- `session-id` — fallback (not observed)
- `thread_id` — yes (new in 0.130)
- `thread-id` — fallback (not observed)
- `x-client-request-id` — yes
- `x-codex-window-id` — yes
- `x-codex-turn-metadata` — yes (parse + workspace extraction)
- `originator` — yes
- `user-agent` — yes
- `turn_id` — yes (from parsed turn metadata)
- `turn_started_at_unix_ms` — yes
- `identity_conflict` — yes

**Gaps (not observed in this probe, but parsers should still handle):**

- `x-codex-turn-state` — not covered, not sent
- `x-codex-installation-id` — sent inside `client_metadata` body, not parsed yet
- `x-codex-parent-thread-id` — not covered, not sent
- `x-openai-subagent` — not covered, not sent
- `x-openai-memgen-request` — not covered, not sent

### `extract_codex_body_metadata` (planned, not yet implemented)

**Required before Phase 1 DB persistence.**

Covers signals observed in this probe:

- `prompt_cache_key` — present in every request
- `client_metadata` — present in every request (contains installation_id)
- `reasoning.effort` — present (`"high"`)
- `text.verbosity` — present (`"low"`)
- `store` — present (`false`)
- `tools` summary (tool_names, tools_count) — present

The existing `tests/test_qz_codex_request_metadata.py` already defines tests
for all these functions. They will pass once the body-parser functions are
implemented.

### `extract_codex_request_context` (planned, not yet implemented)

The planned merge function for cross-checking header and body identity is not
yet needed for the signals observed (no conflicts found), but will be required
once `x-codex-installation-id` may appear in both header and body paths.

### `parse_codex_window_id` (planned, not yet implemented)

The window-id format `{thread_id}:{generation}` is consistent across all 6
captures. `thread_id` is a UUID without colons, so `rsplit(":", 1)` is safe
for this probe. The existing tests in `test_qz_codex_request_metadata.py`
already handle edge cases (thread IDs with colons, missing generation,
negative generation).

## SQLite readiness

**Phase 1 SQLite can start now** with the following fields justified by live
Codex 0.130.0 capture evidence:

### Sessions table

| Field | Evidence |
| :--- | :--- |
| `qz_session_id` | Generated by QuantZhai (always present) |
| `client_session_id` | `session_id` header — 6/6 confirmed |
| `client_thread_id` | `thread_id` header — 6/6 confirmed (new in 0.130) |
| `codex_window_id` | `x-codex-window-id` header — 6/6 confirmed |
| `originator` | `originator` header — 6/6 confirmed |
| `client_installation_id` | `client_metadata.x-codex-installation-id` — 6/6 confirmed |
| `first_seen_request_id` | Generated by QuantZhai |
| `created_at_ms` / `updated_at_ms` | Timestamps from proxy |

### Requests table

| Field | Evidence |
| :--- | :--- |
| `qz_request_id` | Generated by QuantZhai (always present) |
| `client_request_id` | `x-client-request-id` header — 6/6 confirmed |
| `turn_id` | `x-codex-turn-metadata.turn_id` — 6/6 confirmed (stable per turn) |
| `turn_started_at_unix_ms` | `x-codex-turn-metadata.turn_started_at_unix_ms` — 6/6 confirmed |
| `codex_turn_metadata_raw` | Raw `x-codex-turn-metadata` header — 6/6 confirmed |
| `codex_turn_metadata_json` | Parsed JSON — 6/6 valid |
| `prompt_cache_key` | Body field — 6/6 confirmed |
| `reasoning_effort` | `body.reasoning.effort` — 6/6 confirmed |
| `text_verbosity` | `body.text.verbosity` — 6/6 confirmed |
| `store` | `body.store` — 6/6 confirmed, `false` |
| `client_metadata_json` | `body.client_metadata` — 6/6 confirmed |
| `tools_count` | `body.tools.length` — 6/6 confirmed |
| `tool_names` | `body.tools[].name` — 6/6 confirmed |

### Workspace candidates table

| Field | Evidence |
| :--- | :--- |
| `qz_request_id` | Generated by QuantZhai |
| `repo_root` | Turn metadata `workspaces` key — 5/6 confirmed |
| `remote_url` | Turn metadata `workspaces.*.associated_remote_urls` — 5/6 confirmed |
| `normalized_remote_url` | Post-normalization — 5/6 confirmed |
| `latest_git_commit_hash` | Turn metadata `workspaces.*.latest_git_commit_hash` — 5/6 confirmed |
| `has_changes` | Turn metadata `workspaces.*.has_changes` — 5/6 confirmed |

### Fields still not observed (defer or keep nullable)

| Field | Status |
| :--- | :--- |
| `previous_response_id` | Absent in all 6 — defer |
| `service_tier` | Absent in all 6 — defer |
| `subagent` | `x-openai-subagent` absent — defer |
| `is_memgen` | `x-openai-memgen-request` absent — defer |
| `turn_state_raw` | `x-codex-turn-state` absent — defer |
| `parent_thread_id` | `x-codex-parent-thread-id` absent — defer |

### Prerequisite before DB writes

The `extract_codex_body_metadata()` and `extract_codex_request_context()`
functions must be implemented to cover `prompt_cache_key`, `client_metadata`,
`reasoning.effort`, `text.verbosity`, `store`, and tool summaries. The SQLite
substrate can be designed and schema-migrated in parallel, but DB writes should
wait until the parser produces structured metadata for all confirmed fields.

## Open questions

1. **Does `session_id` always equal `thread_id` in Codex 0.130?**
   This probe found them identical. If they always match, DB columns can use
   one as the foreign key and alias the other. A multi-thread probe would
   clarify whether threads get distinct UUIDs.

2. **Does `prompt_cache_key` always equal `session_id`?**
   Same UUID in all 6 requests. If it is always derived from the session,
   it may not need a separate column. If it can differ (e.g. when switching
   models or profiles), store it separately.

3. **Does `x-codex-turn-state` appear in any Codex path?**
   Not in this HTTP/SSE probe. It may be WebSocket-only, may appear in
   compact requests, or may have been removed in 0.130. Keep the parser slot
   but do not block on it.

4. **Does `previous_response_id` appear in any non-streaming path?**
   Not in these streaming requests. Codex 0.130 may send it on non-streaming
   `/v1/responses` calls (e.g. compact or tool-only hops). A non-streaming
   probe would clarify.

5. **Does `client_metadata` ever carry fields beyond `x-codex-installation-id`?**
   This probe only saw the installation-id field. The Codex source references
   `cwd`, `personality`, and other fields. They may appear in different
   request paths or be opt-in.
