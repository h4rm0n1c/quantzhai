# QuantZhai Operational Store Design

Date: 2026-05-20
Status: Slice A-design — design only, no implementation.

Issues: #51 (recovery state persistence), #46 (qz-write-runtime-state replacement)

---

## 1. Purpose

The **OperationalStore** is a lightweight SQLite database for internal QuantZhai
runtime facts. It is NOT BrainCaseDB and NOT model-visible memory.

**What it stores:**
- Startup and runtime lifecycle events (replaces `qz-write-runtime-state`)
- Recovery/backoff state (implements #51)
- Session and workspace identity (enables repeated-read v2)
- Repeated-read file-access signatures per session/workspace
- Key-value operational facts (cooldown timestamps, last-selected state, etc.)

**What it does NOT store:**
- Model-visible memory records — that is BrainCaseDB's domain
- Prompt context or recall candidates
- Character/persona/HSM memory
- User archive memory
- Config files or generated artifacts
- A replacement for `var/model-state.json` or `var/backend-state.json` in Phase 1

**Key rule:** OperationalStore facts are internal only. Nothing in the
OperationalStore is automatically injected into forwarded request bodies,
rendered to the LLM, or made model-visible without an explicit tool call.

---

## 2. Non-goals

```text
- Not BrainCaseDB. BrainCaseDB (QZ_STATE_DB_PATH) stores StateRecords and
  SourceRefs for model-facing memory. OperationalStore is control-plane state.

- Not model-visible memory. No render, recall, or write_candidate path goes
  through OperationalStore.

- Not a prompt injection mechanism. OperationalStore never mutates forwarded
  request bodies.

- Not a replacement for var/model-state.json or var/backend-state.json in Slice B.
  Those files handle restart-persistence for selected model and backend state.
  That migration belongs in a later slice.

- Not a telemetry bus. The existing telemetry event system (qz_telemetry.py)
  stays as-is. OperationalStore complements it for persistent facts.

- Not a config authority. memory_domain, profiles, and all policy remain in
  config files.
```

---

## 3. Default path and environment

| Variable | Default | Purpose |
|---|---|---|
| `QZ_OPERATIONAL_DB_PATH` | `$QZ_VAR_DIR/state/operational.sqlite3` | Path to operational SQLite file |
| `QZ_OPERATIONAL_DB_ENABLED` | `0` | Explicit enable/disable gate |

**Why `state/` subdirectory:**
- Distinct from `generated/` (artifact outputs)
- Distinct from `captures/`, `logs/`, `benchmarks/` (debug outputs)
- Groups all persistent runtime state in one place
- `var/model-state.json` and `var/backend-state.json` may later migrate here

**Why separate from BrainCaseDB:**
- BrainCaseDB (`QZ_STATE_DB_PATH`) is for model-facing memory records
- Different access patterns: OperationalStore is write-heavy / low-latency;
  BrainCaseDB requires careful semantic review before any LLM exposure
- Different lifecycle: operational facts may be pruned aggressively;
  memory records have explicit retention policy

**Future module:** `proxy/qz_operational_store.py`
This module does not exist yet. Slice B creates it.

---

## 4. Phase 1 schema

All tables include `PRAGMA user_version` as schema version tracking.

### 4.1 `schema_meta`

```sql
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- Rows: schema_version, created_at, qz_version
```

Purpose: Schema version tracking, creation metadata.
Writer: `qz_operational_store.py` at open time.
Reader: Schema migration logic.
Retention: Permanent.

---

### 4.2 `runtime_events`

```sql
CREATE TABLE runtime_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms        INTEGER NOT NULL,
    event_type   TEXT    NOT NULL,   -- e.g. "proxy_started", "backend_healthy"
    source       TEXT    NOT NULL,   -- "launcher", "proxy", "monitor"
    payload_json TEXT
);
CREATE INDEX idx_runtime_events_ts ON runtime_events(ts_ms);
CREATE INDEX idx_runtime_events_type ON runtime_events(event_type);
```

Purpose: Replaces `qz-write-runtime-state` launcher trace. Records startup
phases and runtime lifecycle events for diagnostics.

Writer: `qz-write-runtime-state` (migrated in Slice C); proxy startup hook.
Reader: `/qz/config/effective` (shows last events); qz-doctor; qz-top.
Retention: Prune events older than 7 days. Keep last N events per type.

**Replaces:** `var/run/qz-runtime-state.json` entries for phases:
`requested`, `backend-started`, `proxy-started`, `backend-healthy`.

---

### 4.3 `runtime_facts`

```sql
CREATE TABLE runtime_facts (
    key            TEXT PRIMARY KEY,
    value_json     TEXT    NOT NULL,
    updated_at_ms  INTEGER NOT NULL,
    provenance     TEXT                -- "launcher", "proxy", "operator"
);
```

Purpose: Key-value store for operational facts that must survive restarts.
Examples: `last_selected_model`, `last_backend_start_time`, `proxy_pid`.

Writer: Proxy and launcher scripts.
Reader: `/qz/control-plane`, `/qz/status` for diagnostics.
Retention: Keys updated on change; no auto-prune (small set).

---

### 4.4 `sessions`

```sql
CREATE TABLE sessions (
    session_id              TEXT    PRIMARY KEY,  -- internal qz session key
    created_at_ms           INTEGER NOT NULL,
    last_seen_at_ms         INTEGER NOT NULL,
    client_session_id       TEXT,                 -- from codex-session-id header
    installation_id         TEXT,                 -- from codex-installation-id header
    codex_conversation_id   TEXT,                 -- from turn metadata if available
    client_kind             TEXT,                 -- "codex", "direct", "unknown"
    workspace_id            TEXT,                 -- FK to workspaces.workspace_id
    metadata_json           TEXT
);
CREATE INDEX idx_sessions_client ON sessions(client_session_id);
CREATE INDEX idx_sessions_workspace ON sessions(workspace_id);
CREATE INDEX idx_sessions_last_seen ON sessions(last_seen_at_ms);
```

Purpose: Session identity tracking. Associates an internal `session_id` with
the client-provided identity from `CodexIdentity` (parsed by
`qz_codex_metadata.py`).

Writer: `qz_operational_store.py:resolve_session()` on each incoming request.
Reader: repeated-read v2; session-scoped telemetry; diagnostics.
Retention: Prune sessions not seen in 30 days.

**Session identity model:**
- `session_id` is a server-generated key: `sha256(installation_id + "/" + client_session_id)[:16]` or a UUID if headers are absent.
- `client_session_id` from `CodexIdentity.client_session_id` (the codex header).
- If no client identity is available, a per-connection ephemeral ID is generated; it is NOT persisted.
- Sessions that lack workspace binding use `workspace_id = NULL`.

---

### 4.5 `workspaces`

```sql
CREATE TABLE workspaces (
    workspace_id      TEXT    PRIMARY KEY,   -- stable derived ID
    path              TEXT,                  -- local filesystem path
    fingerprint       TEXT,                  -- git remote URL normalized, or path hash
    first_seen_at_ms  INTEGER NOT NULL,
    last_seen_at_ms   INTEGER NOT NULL
);
```

Purpose: Stable workspace identity. `workspace_id` is derived by
`resolve_workspace_id()` in `qz_codex_metadata.py` — the existing function
already produces a stable ID from git remote URL or path fingerprint.

Writer: `qz_operational_store.py:bind_workspace()`.
Reader: sessions table; repeated_read_state table.
Retention: Prune workspaces not seen in 90 days.

---

### 4.6 `recovery_state`

```sql
CREATE TABLE recovery_state (
    key             TEXT    PRIMARY KEY,   -- "global" or action name
    state_json      TEXT    NOT NULL,      -- matches qz.recovery.runtime_state.v1
    updated_at_ms   INTEGER NOT NULL,
    expires_at_ms   INTEGER               -- NULL = no expiry; >0 = soft expiry
);
```

Purpose: Persistent recovery/backoff state for #51. Survives proxy restarts.

The `state_json` payload mirrors the in-memory `RecoveryState` fields from
`qz_recovery_state.py`:

```json
{
    "schema": "qz.recovery.runtime_state.v1",
    "in_progress": false,
    "backoff_until": {"unload_model": null, "reload_model": 1716200000.0},
    "manual_required": {"unload_model": false},
    "attempt_counts": {"unload_model": 3, "reload_model": 1},
    "last_error": "connection timeout",
    "last_updated_at": 1716200000.0
}
```

Writer: `qz_recovery_state.py` — on state change, write to OperationalStore
asynchronously; failure must not break recovery flow.
Reader: `qz_recovery_state.py` — at startup, seed in-memory state from DB.
Retention: Reset on successful recovery; prune entries older than 24 hours.

---

### 4.7 `repeated_read_state`

```sql
CREATE TABLE repeated_read_state (
    session_id      TEXT    NOT NULL,
    workspace_id    TEXT,
    file_path       TEXT    NOT NULL,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    first_seen_at_ms INTEGER NOT NULL,
    last_seen_at_ms  INTEGER NOT NULL,
    PRIMARY KEY (session_id, file_path)
);
CREATE INDEX idx_repeated_read_session ON repeated_read_state(session_id);
```

Purpose: Enables repeated-read v2. Persists per-session file-access signatures
across requests within the same session.

**v1 status:** `RepeatedReadState` is stateless/in-memory, seeded from input
history on each request. v1 stays as-is.

**v2 design:** On each request, after the existing stateless check, the
OperationalStore is updated with file paths seen in this session. Subsequent
requests from the same session retrieve persisted signatures.

Writer: `qz_operational_store.py:record_repeated_read_signal()`.
Reader: `qz_file_signal.py` (future seed step).
Retention: Prune entries older than session expiry (30 days).

---

## 5. Session/workspace identity model

### 5.1 Inputs

From `CodexRequestContext` (parsed by `qz_codex_metadata.py`):

| Field | Source | Use |
|---|---|---|
| `identity.client_session_id` | `codex-session-id` header | Primary session key |
| `identity.installation_id` | `codex-installation-id` header | Scoping salt |
| `identity.workspace_id` | `resolve_workspace_id()` | Workspace binding |
| `identity.workspace_candidates` | Turn metadata `workspaces` field | Fingerprint source |

### 5.2 Server-side `session_id` derivation

```python
def _derive_session_id(installation_id: str | None, client_session_id: str | None) -> str:
    if not installation_id and not client_session_id:
        return "ephemeral"  # not persisted
    key = f"{installation_id or ''}:{client_session_id or ''}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### 5.3 Safety rules

- **Missing identity → isolated.** No client session headers = no persistent
  session. Ephemeral IDs are not stored in the OperationalStore.
- **No cross-domain leakage.** `workspace_id` scopes repeated-read state;
  facts from workspace A are never mixed with workspace B.
- **No forwarded-body mutation.** `qz_session_id` and `workspace_id` are
  internal operational metadata only. They must never be injected into the
  JSON body forwarded to the upstream model server.
- **memory_domain stays config-owned.** The OperationalStore never grants,
  creates, or infers memory_domain values.

### 5.4 Unknown workspace

If `workspace_id` cannot be resolved (no turn metadata, no git remote):
- Session is created with `workspace_id = NULL`
- repeated-read state is session-scoped only (no workspace cross-referencing)
- This is safe and isolated; no data loss

---

## 6. qz-write-runtime-state replacement plan

### 6.1 Current role

`scripts/qz-write-runtime-state` is called by `qz-up` at 5 startup phases:
- `requested` — launch parameters recorded
- `backend-started` — backend process launched
- `proxy-started` — proxy process started
- `backend-healthy` — backend health check passed
- each writes to `var/run/qz-runtime-state.json` (atomic write via tempfile)

The file is a launcher trace, not live status. The live authority is
`GET /qz/control-plane`.

### 6.2 Replacement sequence

**Slice C (implementation):**
1. Add `record_startup_event(phase, payload)` to `qz_operational_store.py`
2. Modify `qz-write-runtime-state` to **also** write to OperationalStore when
   `QZ_OPERATIONAL_DB_ENABLED=1`. The JSON file write stays for compatibility.
3. Add a `runtime_events` query to `/qz/config/effective` (shows last 10 events).
4. `qz-doctor` checks `var/run/qz-runtime-state.json` fallback if DB absent.

**Slice C.1 (audit):**
Verify backward compatibility. Script still produces the JSON file.

**Later (Slice E or close-out):**
- Remove the JSON file write when all consumers use control-plane + OperationalStore
- Deprecate `scripts/qz-write-runtime-state` after #46 acceptance criteria met

### 6.3 Compatibility constraint

The JSON file `var/run/qz-runtime-state.json` remains until:
- `/qz/config/effective` reports startup events from OperationalStore
- `qz-doctor` no longer reads the JSON file for its stale-context checks
- No other script reads it for routing decisions (already confirmed: zero routing consumers)

---

## 7. Recovery state persistence (#51)

### 7.1 What gets persisted

From `qz_recovery_state.py` in-memory `RecoveryState`:

| Field | Persistence priority | Notes |
|---|---|---|
| `_backoff_until` (per action) | High | Lost on restart; causes excessive retries |
| `_in_progress` flag | Medium | Reset to False on fresh start is safe |
| `_manual_required` (per action) | High | Operator must re-intervene after restart |
| Attempt counts (per action) | Medium | Useful for backoff schedule continuity |
| `last_error` string | Low | Diagnostic only |

### 7.2 Write path

On each state transition in `RecoveryState`:
1. Update in-memory state as today (no behaviour change)
2. Asynchronously write to `recovery_state` table via OperationalStore
3. Failure to write must not block recovery flow (non-fatal)

### 7.3 Read path (startup)

At proxy startup, before accepting requests:
1. Open OperationalStore if enabled
2. Read `recovery_state WHERE key = 'global'`
3. If found and not expired, seed in-memory `RecoveryState` from JSON payload
4. If absent or expired, start with fresh in-memory state (current behaviour)

### 7.4 Expiry

- `backoff_until` timestamps are naturally self-expiring (past timestamps = no backoff)
- `manual_required` flags persist until operator intervention
- `expires_at_ms` set to `updated_at_ms + 86400000` (24 hours); stale state is dropped

---

## 8. Repeated-read v2 dependency

**v1** (current): stateless, per-request, seeded from input history only.
Advisory signal only. No persistence.

**v2** (future): cross-request within a session.

**How OperationalStore enables v2:**

1. On each request with a resolved `session_id`:
   - After existing v1 stateless check runs
   - Record file paths seen this request to `repeated_read_state` table
   - On next request with same `session_id`: seed `RepeatedReadState` from DB
   - The signal now fires for files seen in prior requests in same session

2. Workspace scoping:
   - `(session_id, file_path)` is the primary key
   - `workspace_id` is stored for diagnostics but not used for cross-workspace lookup

3. Safety:
   - v1 advisory signal is unaffected
   - v2 signal is also advisory only (never blocks requests)
   - No cross-session or cross-workspace sharing
   - `session_id = NULL` means v2 is disabled for that request

**v2 is not implemented in #51. It is a separate design/implementation issue
that depends on OperationalStore being live. #51 close-out does not require v2.**

---

## 9. Safety and privacy rules

```text
1. Internal only.
   OperationalStore facts never reach the LLM prompt automatically.
   No automatic injection. No forwarded-body mutation.

2. session_id / workspace_id are operational metadata.
   They scope OperationalStore reads/writes only.
   They are never forwarded in the upstream request body.

3. memory_domain stays config-owned.
   OperationalStore never grants, creates, infers, or revokes memory_domain values.

4. BrainCaseDB is separate.
   Model-facing memory records go through BrainCaseDB.
   Operational facts go through OperationalStore.
   No data flows between them automatically.

5. Non-fatal DB failures.
   OperationalStore open/write failures must not break proxy responses.
   Failure mode: log, telemetry emit, continue without persistence.

6. No cross-domain leakage.
   workspace_id scopes data. workspace A state is never visible to workspace B.

7. Operator-controlled enable gate.
   QZ_OPERATIONAL_DB_ENABLED=0 means no DB is opened.
   Default is disabled in Phase 1; admin must explicitly enable.
```

---

## 10. Module design (future: proxy/qz_operational_store.py)

**Design only — do not implement yet.**

```python
# proxy/qz_operational_store.py — Phase 1 API sketch

class OperationalStore:
    """Lightweight SQLite store for internal QuantZhai operational facts.

    Not BrainCaseDB. Not model-visible memory. Internal control-plane state only.
    """

    @classmethod
    def open(cls, path: Path) -> "OperationalStore":
        """Open or create the store at path. Idempotent. Non-fatal on failure."""
        ...

    def record_startup_event(self, phase: str, payload: dict) -> None:
        """Record a startup lifecycle event (replaces qz-write-runtime-state)."""
        ...

    def record_runtime_fact(self, key: str, value: dict, provenance: str = "proxy") -> None:
        """Upsert a key-value operational fact."""
        ...

    def resolve_session(self, identity: "CodexIdentity") -> str | None:
        """Create or update the session record. Returns server-side session_id."""
        ...

    def bind_workspace(self, workspace_id: str, path: str, fingerprint: str) -> None:
        """Ensure a workspace record exists."""
        ...

    def record_recovery_state(self, state_json: dict) -> None:
        """Upsert recovery/backoff state."""
        ...

    def get_recovery_state(self) -> dict | None:
        """Read persisted recovery state, or None if absent/expired."""
        ...

    def record_repeated_read_signal(
        self,
        session_id: str,
        workspace_id: str | None,
        file_path: str,
    ) -> None:
        """Record that a file was read in this session."""
        ...

    def get_session_read_paths(self, session_id: str) -> frozenset[str]:
        """Return file paths seen by this session across all prior requests."""
        ...

    def close(self) -> None: ...
```

**Accessor pattern:** One `OperationalStore` instance per proxy process, opened
at startup if `QZ_OPERATIONAL_DB_ENABLED=1`. Reads/writes are synchronous with
non-fatal fallback. No connection pooling in Phase 1.

---

## 11. Implementation slice roadmap

| Slice | Content | Closes |
|---|---|---|
| **A-design** (this doc) | Boundary, schema, identity model, roadmap | — |
| **B-impl** | `qz_operational_store.py` skeleton: open/close, schema creation, path/env | — |
| **B.1** | Audit/polish path, env, schema | — |
| **C-impl** | Startup event writer; `qz-write-runtime-state` dual-write | — |
| **C.1** | Launcher compatibility audit | partial #46 |
| **D-impl** | `recovery_state` table + `RecoveryState` startup seed + write | #51 |
| **D.1** | Audit recovery persistence behaviour | — |
| **E-impl** | Session/workspace identity integration + `sessions`/`workspaces` tables | — |
| **E.1** | Audit identity model | — |
| **F-design** | Repeated-read v2 design (separate issue) | new issue |
| **Close-out B+C** | Confirm #46 replacement criteria met, close #46 | #46 |

---

## 12. Test plan (future)

```text
test_operational_store_path_respects_qz_var_dir
  QZ_VAR_DIR=/custom -> store at /custom/state/operational.sqlite3

test_schema_created_idempotently
  open() twice does not error; schema_meta has correct version

test_runtime_event_insert_roundtrip
  record_startup_event("proxy_started", {...}) -> readable from runtime_events

test_runtime_fact_upsert
  record_runtime_fact("last_model", {"slug": "qwen"})
  record_runtime_fact("last_model", {"slug": "apex"})
  only one row exists with "apex"

test_session_identity_stable_for_same_codex_metadata
  resolve_session(identity_A) == resolve_session(identity_A)

test_session_identity_isolated_when_workspace_missing
  identity with no workspace -> session created with workspace_id = NULL
  no error, no cross-leakage

test_workspace_binding_does_not_mutate_forwarded_request
  bind_workspace(...) does not add keys to any request dict

test_recovery_state_roundtrip
  record_recovery_state({...}) -> get_recovery_state() returns same payload

test_recovery_state_expired_returns_none
  record with expires_at_ms in past -> get_recovery_state() returns None

test_operational_store_failure_is_non_fatal
  DB write failure -> no exception raised; error logged

test_repeated_read_paths_persist_across_calls
  record_repeated_read_signal(session_id, ws, "foo.py")
  get_session_read_paths(session_id) -> {"foo.py"}

test_repeated_read_isolated_by_session
  session_A and session_B have separate read path sets

test_qz_write_runtime_state_compatibility
  script still writes JSON file; also writes to DB if enabled
```

---

## 13. Migration / compatibility constraints

```text
- var/run/qz-runtime-state.json:  stays until Slice C.1 confirms compatibility
- var/model-state.json:           not touched by OperationalStore in Phase 1
- var/backend-state.json:         not touched by OperationalStore in Phase 1
- BrainCaseDB (QZ_STATE_DB_PATH): untouched; separate module/schema/lifecycle
- qz-write-runtime-state script:  stays for compatibility through Slice C
```

---

## Related documents

- `proxy/qz_recovery_state.py` — recovery state in-memory implementation
- `proxy/qz_codex_metadata.py` — identity parsing and workspace resolution
- `proxy/qz_file_signal.py` — repeated-read v1 stateless signal
- `proxy/qz_braincase_db.py` — BrainCaseDB (model-facing memory, separate)
- `docs/braincase-memory-tool-api.md` — BrainCaseDB doctrine
- `scripts/qz-write-runtime-state` — current launcher trace script
- Issue #51 — recovery state persistence
- Issue #46 — qz-write-runtime-state replacement
