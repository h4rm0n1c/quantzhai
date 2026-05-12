# Codex Client Header Metadata Audit

Date: 2026-05-12

Status: source audit for QuantZhai state/memory planning. No implementation.

Sources inspected:

- `h4rm0n1c/codex` fork:
  - `codex-rs/core/src/client.rs`
  - `codex-rs/core/src/turn_metadata.rs`
  - `codex-rs/core/src/turn_metadata_tests.rs`
  - `codex-rs/core/tests/responses_headers.rs`
- QuantZhai live capture summary:
  - `docs/codex-header-capture-verdict.md`

The fork currently mirrors the relevant upstream OpenAI Codex code paths used by
this audit.

---

## Verdict

Codex sends useful session/request/workspace metadata to `/v1/responses` in HTTP
headers.

QuantZhai should treat these headers as first-class input to the Phase 1 state
store, but should still own its own internal session id.

Phase 1 should store:

```text
qz_session_id
  QuantZhai-owned internal primary session key. Always generated.

client_session_id
  Codex `session_id` header. Present in local captures.

client_thread_id
  Codex `thread_id` / `thread-id` header if present. Absent in current local
  captures, but Codex tests show the client can send it in some paths.

client_request_id
  `x-client-request-id` header. Present in local captures.

codex_window_id
  `x-codex-window-id` header. Present in local captures. Format in Codex source
  is `{thread_id}:{window_generation}`.

originator
  `originator` header. Present in local captures, e.g. `codex_exec`.

codex_turn_metadata_raw
  Raw `x-codex-turn-metadata` header. Present in local captures.

codex_turn_metadata_json
  Parsed best-effort JSON object, if valid and bounded.
```

Do not make `client_thread_id` or `previous_response_id` mandatory in Phase 1.

---

## Header construction in Codex

`codex-rs/core/src/client.rs` defines the important header names:

```text
X_CODEX_INSTALLATION_ID_HEADER = x-codex-installation-id
X_CODEX_TURN_STATE_HEADER = x-codex-turn-state
X_CODEX_TURN_METADATA_HEADER = x-codex-turn-metadata
X_CODEX_PARENT_THREAD_ID_HEADER = x-codex-parent-thread-id
X_CODEX_WINDOW_ID_HEADER = x-codex-window-id
X_OPENAI_MEMGEN_REQUEST_HEADER = x-openai-memgen-request
X_OPENAI_SUBAGENT_HEADER = x-openai-subagent
```

Codex `ModelClientState` stores:

```text
session_id
thread_id
window_generation
installation_id
session_source
```

`ModelClient::current_window_id()` formats the window id as:

```text
{thread_id}:{window_generation}
```

`ModelClient::build_responses_identity_headers()` adds:

```text
x-codex-parent-thread-id, if applicable
x-codex-window-id
x-openai-subagent, if applicable
x-openai-memgen-request, if applicable
```

Codex also calls `build_session_headers(Some(session_id), Some(thread_id))` in
some HTTP paths, including compact requests. The public test suite also contains
a direct `/v1/responses` test expecting both underscore and hyphenated session
and thread headers in at least one configured path:

```text
session_id
session-id
thread_id
thread-id
```

QuantZhai local captures currently show only `session_id`, not the hyphenated
or thread variants. Treat that as a path/version/config difference, not proof
that Codex never sends them.

---

## Turn metadata header

`x-codex-turn-metadata` is JSON, not an opaque token.

`codex-rs/core/src/turn_metadata.rs` defines `TurnMetadataBag` with these fields:

```text
session_id
thread_id
thread_source
turn_id
workspaces
sandbox
```

Workspace metadata can include:

```text
associated_remote_urls
latest_git_commit_hash
has_changes
```

`TurnMetadataState::new(...)` builds the base metadata from:

```text
session_id
thread_id
thread_source
turn_id
cwd
permission profile / sandbox
```

The initial header includes session/thread/turn/sandbox data. Codex can then
spawn an enrichment task that adds git workspace metadata once it is available.

`merge_turn_metadata(...)` can add:

```text
turn_started_at_unix_ms
responsesapi_client_metadata values
```

Reserved fields are protected by insertion order: existing fields are not
replaced by client metadata.

Useful fields for QuantZhai:

```text
session_id
  Should match or corroborate the `session_id` HTTP header when present.

thread_id
  May be present here even when the raw `thread_id` header is absent.

turn_id
  Useful for grouping multiple `/v1/responses` requests that belong to one
  Codex turn.

turn_started_at_unix_ms
  Useful for ordering and per-turn timing.

sandbox
  Useful diagnostic/scope context, not a memory permission by itself.

workspaces
  Directly useful for deriving workspace_id candidates.
```

---

## Workspace identity impact

This audit changes the workspace story.

Previous assumption:

```text
QuantZhai cannot see client cwd/git root/repo identity.
```

Updated finding:

```text
QuantZhai may see workspace identity through `x-codex-turn-metadata`.
```

If present, `workspaces` is a map keyed by repo root path. Each workspace may
carry remote URLs, current git commit, and dirty-state.

Phase 1 should not rely on workspace data for memory isolation yet, but it
should capture and parse it as evidence.

Phase 1.5 / Phase 2 can derive a `workspace_id` candidate from:

```text
primary key:
  normalized remote URL, if available

secondary key:
  repo root path, if no remote URL exists

supporting facts:
  latest_git_commit_hash
  has_changes
```

Do not use raw repo root path alone as a globally shareable workspace id without
considering local path collisions, renamed directories, and private paths.

---

## Relationship to QuantZhai captures

`docs/codex-header-capture-verdict.md` showed these real local headers:

```text
session_id:             present
originator:             present
authorization:          present
user-agent:             present
x-client-request-id:    present
x-codex-turn-metadata:  present
x-codex-window-id:      present
thread_id/thread-id:    absent
```

This matches enough of Codex source to trust the local capture path.

The earlier body-only audit was incomplete because it did not capture HTTP
headers. It found `session_id` only inside the `exec_command` schema, but that
was a body/tool-schema artefact. Raw header capture corrected the conclusion.

---

## QuantZhai storage recommendation

Phase 1 DB tables should include raw and parsed header metadata without making
model behaviour depend on it yet.

Suggested additions to the first state-store plan:

```text
sessions:
  qz_session_id TEXT PRIMARY KEY
  client_session_id TEXT NULL
  client_thread_id TEXT NULL
  codex_window_id TEXT NULL
  originator TEXT NULL
  first_seen_request_id TEXT
  created_at_ms INTEGER
  updated_at_ms INTEGER

responses or requests:
  qz_request_id TEXT
  client_request_id TEXT NULL
  turn_id TEXT NULL
  turn_started_at_unix_ms INTEGER NULL
  codex_turn_metadata_raw TEXT NULL
  codex_turn_metadata_json TEXT NULL or JSON/TEXT

workspace_candidates:
  qz_request_id TEXT
  qz_session_id TEXT
  repo_root TEXT NULL
  remote_name TEXT NULL
  remote_url TEXT NULL
  normalized_remote_url TEXT NULL
  latest_git_commit_hash TEXT NULL
  has_changes INTEGER NULL
```

Keep `workspace_candidates` non-authoritative at first. Use it for diagnostics
and later workspace-id derivation tests.

---

## Parser recommendations

Add a small parser, not a giant state system:

```text
parse_codex_turn_metadata_header(value: str) -> dict | None
extract_codex_identity(headers_raw: dict) -> CodexIdentity
```

Parsing rules:

```text
1. Treat header names case-insensitively when extracting known fields.
2. Preserve raw header names in debug capture files.
3. Parse `x-codex-turn-metadata` as JSON only if it is below a sane size limit.
4. Accept only object-shaped JSON.
5. Store raw string plus parsed object/digests.
6. Do not execute, expand, or resolve paths from the metadata.
7. Do not treat workspace metadata as authoritative until tests prove stable.
```

Conflict rules:

```text
If session_id header and turn_metadata.session_id both exist and match:
  accept and store both.

If they differ:
  keep qz_session_id primary;
  store both raw values;
  mark identity_conflict=true;
  do not use either external id for cross-request lookup until policy is added.

If thread_id header is absent but turn_metadata.thread_id exists:
  store turn metadata thread id as client_thread_id_source=turn_metadata.

If thread_id header and turn_metadata.thread_id both exist and differ:
  treat as conflict.
```

---

## Tests to add before DB implementation

```text
test_extract_codex_identity_reads_session_id_header
test_extract_codex_identity_reads_client_request_id
test_extract_codex_identity_reads_codex_window_id
test_parse_turn_metadata_basic_session_thread_turn
test_parse_turn_metadata_workspaces_remote_commit_dirty_state
test_turn_metadata_thread_id_used_when_header_absent
test_turn_metadata_session_id_conflict_detected
test_turn_metadata_oversize_rejected_or_left_raw_only
test_turn_metadata_invalid_json_left_raw_only
test_workspace_candidates_are_diagnostic_not_authoritative
```

---

## Deferred work

Do not implement in the first DB patch:

```text
authoritative workspace_id derivation
cross-workspace memory sharing
previous_response_id chain resolution
turn metadata promotion into model-visible memory
raw header blob storage in SQLite
profile_family inference from originator/user-agent
HSM/Holstrom import/export from Codex metadata
```

---

## Immediate next action

Before implementing the SQLite store, update the state/memory plan one more time
with this audit's stronger finding:

```text
x-codex-turn-metadata is parseable JSON and can carry workspace candidates.
Phase 1 should parse/store it as request/session/workspace-candidate metadata,
but should not use it for authoritative workspace isolation yet.
```

Then ask an implementation agent for the first DB substrate with scope limited to:

```text
qz_state_store.py
schema_migrations
sessions
requests/responses
tool_calls
file_reads/file_writes
signals
compaction_events
workspace_candidates as diagnostic/non-authoritative
```
