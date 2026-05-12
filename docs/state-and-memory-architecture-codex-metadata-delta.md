# State and Memory Architecture — Codex Metadata Delta

Date: 2026-05-12

Status: planning delta. This narrows the next Phase 1 DB substrate based on the
Codex client-header audit in `docs/codex-client-header-metadata-audit.md`.

This document supersedes any earlier assumption that QuantZhai cannot see client
workspace/session metadata. QuantZhai can see some of it through Codex HTTP
headers, especially `session_id` and `x-codex-turn-metadata`.

---

## 1. Corrected finding

The earlier body/tool-shape audit was incomplete because it did not inspect HTTP
headers.

The corrected state is:

```text
Confirmed from QuantZhai raw header captures:
  session_id
  x-client-request-id
  x-codex-window-id
  x-codex-turn-metadata
  originator
  user-agent

Absent in inspected QuantZhai raw header captures:
  session-id
  thread_id
  thread-id

Absent in inspected body captures:
  previous_response_id
```

Source:

```text
docs/codex-header-capture-verdict.md
docs/codex-client-header-metadata-audit.md
```

---

## 2. Meaning of x-codex-turn-metadata

`x-codex-turn-metadata` is parseable JSON, not an opaque routing token.

From `h4rm0n1c/codex` / `codex-rs/core/src/turn_metadata.rs`, it can contain:

```text
session_id
thread_id
thread_source
turn_id
turn_started_at_unix_ms
sandbox
workspaces
```

The `workspaces` object can carry:

```text
repo root path
associated_remote_urls
latest_git_commit_hash
has_changes
```

Implication:

```text
QuantZhai may be able to derive workspace candidates from Codex metadata.
```

But workspace candidates are not yet authoritative workspace IDs.

---

## 3. Phase 1 DB impact

Phase 1 should store Codex metadata as request/session evidence.

Add/expect fields equivalent to:

```text
sessions:
  qz_session_id
  client_session_id
  client_thread_id nullable
  codex_window_id
  originator
  first_seen_request_id
  created_at_ms
  updated_at_ms

requests/responses:
  qz_request_id
  client_request_id
  turn_id nullable
  turn_started_at_unix_ms nullable
  codex_turn_metadata_raw nullable
  codex_turn_metadata_json nullable or TEXT/JSON

workspace_candidates:
  qz_request_id
  qz_session_id
  repo_root nullable
  remote_name nullable
  remote_url nullable
  normalized_remote_url nullable
  latest_git_commit_hash nullable
  has_changes nullable
```

`qz_session_id` remains QuantZhai's internal primary key. `client_session_id` is
external identity from Codex and should be mapped onto the internal session.

`client_thread_id` remains nullable. It may be recovered from turn metadata if
present there, even if no raw `thread_id` header exists.

---

## 4. Parser scope

Before DB implementation, add or plan a small parser layer:

```text
extract_codex_identity(headers_raw) -> CodexIdentity
parse_codex_turn_metadata_header(value) -> parsed object / None
extract_workspace_candidates(parsed_turn_metadata) -> list[WorkspaceCandidate]
```

Rules:

```text
- Header extraction should be case-insensitive for known names.
- Raw debug captures should preserve headers as received.
- Parse x-codex-turn-metadata only if it is valid, object-shaped JSON.
- Use a size limit; oversize metadata should remain raw-only.
- Do not resolve filesystem paths during parsing.
- Do not execute git or shell from metadata.
- Do not make workspace candidates model-visible in Phase 1.
```

---

## 5. Conflict policy

Use QuantZhai identity first:

```text
qz_session_id is primary.
Codex identifiers are external correlation fields.
```

Conflict handling:

```text
If session_id header and turn_metadata.session_id both exist and match:
  store both and mark identity_conflict=false.

If they differ:
  store both raw values;
  mark identity_conflict=true;
  do not use either external id for cross-request lookup until policy is added.

If thread_id header is absent but turn_metadata.thread_id exists:
  store it with source=turn_metadata.

If header thread id and turn_metadata.thread_id differ:
  mark conflict.
```

---

## 6. Workspace candidate policy

Workspace metadata is useful evidence, but not authoritative yet.

Phase 1 should treat it as:

```text
diagnostic / candidate / non-authoritative
```

Do not enable cross-session or cross-profile workspace memory from it yet.

Possible later derivation:

```text
preferred workspace identity:
  normalized remote URL, when present

fallback candidate:
  repo root path, local-only

supporting facts:
  latest_git_commit_hash
  has_changes
```

This requires tests before model-visible memory reads can use it.

---

## 7. Required tests before DB implementation

Add tests equivalent to:

```text
test_extract_codex_identity_reads_session_id_header
test_extract_codex_identity_reads_client_request_id
test_extract_codex_identity_reads_codex_window_id
test_parse_turn_metadata_basic_session_thread_turn
test_parse_turn_metadata_turn_started_at_unix_ms
test_parse_turn_metadata_workspaces_remote_commit_dirty_state
test_turn_metadata_thread_id_used_when_header_absent
test_turn_metadata_session_id_conflict_detected
test_turn_metadata_invalid_json_left_raw_only
test_turn_metadata_oversize_left_raw_only
test_workspace_candidates_are_non_authoritative_in_phase1
```

---

## 8. Deferred work

Do not implement with the first DB substrate:

```text
authoritative workspace_id derivation
cross-workspace memory sharing
profile_family inference from originator/user-agent
previous_response_id chain resolution
model-visible use of workspace candidates
HSM/Holstrom import/export from Codex metadata
raw header blob storage in SQLite
```

---

## 9. Next implementation prompt boundary

The next implementation-agent prompt should target only:

```text
qz_state_store.py
SQLite schema/migrations
session/request/response identity
tool call metadata
file read/write facts
signals
compaction events
Codex identity extraction
Codex turn metadata parsing
workspace_candidates as diagnostic/non-authoritative rows
```

No preferences. No skills. No HSM connector. No repeated-read v2. No model
behaviour changes.
