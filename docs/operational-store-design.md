# QuantZhai Operational Store Design

Date: 2026-05-20
Status: Slice A.2-design — corrected scope (design only, no implementation).

Issues: #46 (qz-write-runtime-state replacement), #51 (reframing needed)

---

## 1. Purpose

The **OperationalStore** is a lightweight SQLite database for internal QuantZhai
runtime events and operational facts. It is the persistence layer for what
`qz-write-runtime-state` currently traces, and nothing more.

**What it stores:**
- Startup and runtime lifecycle events (replaces `qz-write-runtime-state`)
- Current operational key-value facts (last model, last backend start time, etc.)
- Schema version metadata

**What it does NOT store:**
- Model-visible memory — that is BrainCaseDB's domain
- Prompt context or recall candidates
- Character/persona/HSM memory
- Recovery/backoff timer state or cooldown policy
- Time-based backoff_until or manual_required persistence
- Repeated-read file-access signatures (v2 is not planned)
- Session or workspace identity (not needed for Phase 1 consumer)
- A replacement for `var/model-state.json` or `var/backend-state.json`
- Config files or generated artifacts

**Key rule:** OperationalStore facts are internal diagnostics only. Nothing in
the OperationalStore is automatically injected into forwarded request bodies,
rendered to the LLM, or made model-visible.

---

## 2. Non-goals

```text
- Not BrainCaseDB. BrainCaseDB (QZ_STATE_DB_PATH) stores model-facing memory.
  OperationalStore is a runtime event/fact log.

- Not model-visible memory. No render, recall, or write_candidate path goes
  through OperationalStore.

- Not a prompt injection mechanism. OperationalStore never mutates forwarded
  request bodies.

- Not a recovery policy engine. Backoff timers, cooldown state, and
  manual_required flags are intentionally NOT in Phase 1 and NOT desired.
  #51 needs explicit reframing before any recovery-policy persistence is added.

- Not a repeated-read persistence store. Repeated-read v2 (per-session file
  signatures) is not wanted. v1 stateless advisory signal stays as-is.

- Not a session/workspace identity store. sessions and workspaces tables are
  not needed for the #46 consumer; they may be added later only if a concrete
  new consumer requires them.

- Not a telemetry bus. The existing telemetry event system (qz_telemetry.py)
  stays as-is. OperationalStore adds persistence, not a new channel.

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
- Distinct from `generated/` (artifact outputs) and `captures/`, `logs/` (debug)
- `var/model-state.json` and `var/backend-state.json` may later migrate here

**Why separate from BrainCaseDB:**
- BrainCaseDB (`QZ_STATE_DB_PATH`) is for model-facing memory records
- OperationalStore is for diagnostic/control-plane runtime facts
- Different lifecycles and access patterns

**Future module:** `proxy/qz_operational_store.py`
This module does not exist yet. Slice B creates it.

---

## 4. Phase 1 schema

Phase 1 contains exactly three tables: `schema_meta`, `runtime_events`,
`runtime_facts`. Nothing else.

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

Writer: `qz-write-runtime-state` (dual-write in Slice C); proxy startup hook.
Reader: `/qz/config/effective` (shows last events); qz-doctor; qz-top.
Retention: Prune events older than 7 days.

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
Retention: Keys updated on change; no auto-prune (small bounded set).

---

## 5. Tables NOT in Phase 1

The following tables were considered and explicitly excluded from Phase 1:

| Table | Why excluded |
|---|---|
| `sessions` | No Phase 1 consumer. Add only when a concrete consumer requires it. |
| `workspaces` | No Phase 1 consumer. Same constraint. |
| `recovery_state` | Time-based backoff/cooldown persistence is explicitly NOT wanted. #51 needs explicit reframing before any recovery-policy persistence is added. |
| `repeated_read_state` | Repeated-read v2 is not wanted. v1 stateless advisory signal is sufficient. |

These are NOT deferred TODOs. They are removed from scope unless a future
explicit design issue justifies them.

---

## 6. #46 — qz-write-runtime-state replacement

This is the primary concrete driver for Phase 1.

### 6.1 Current role

`scripts/qz-write-runtime-state` is called by `qz-up` at startup phases
(`requested`, `backend-started`, `proxy-started`, `backend-healthy`) and writes
`var/run/qz-runtime-state.json` as a launcher trace. It is NOT the live status
authority (that is `/qz/control-plane`).

### 6.2 Replacement

**Slice C:** Modify `qz-write-runtime-state` to dual-write to `runtime_events`
and `runtime_facts` in OperationalStore when `QZ_OPERATIONAL_DB_ENABLED=1`.
The JSON file write stays for backward compatibility.

**Slice C.1:** Audit launcher compatibility. Confirm JSON file can eventually
be removed.

**Close-out:** Remove JSON file once:
- `/qz/config/effective` shows runtime events from OperationalStore
- `qz-doctor` no longer reads JSON for stale-context checks
- No other consumer reads it for routing decisions (already confirmed: zero)

### 6.3 What does NOT change

- `var/model-state.json` — stays (proxy restart persistence for last-selected model)
- `var/backend-state.json` — stays (proxy restart persistence for backend state)

These may migrate to OperationalStore `runtime_facts` in a later slice, but not
in Phase 1.

---

## 7. #51 — reframing required

The original #51 title ("Promote recovery/backoff runtime state to SQLite") was
written when time-based backoff/cooldown persistence was assumed desirable.

**That assumption is now incorrect.** Backoff timers, cooldown state, and
`manual_required` flags are NOT wanted in the OperationalStore. The in-memory
`RecoveryState` in `qz_recovery_state.py` is sufficient.

**What #51 might mean after reframing:**
- Persistence of last-known recovery diagnostic facts (not timers) as `runtime_facts`
  entries for operator inspection only
- Or: #51 may be closed or downgraded to a documentation task once #46 lands
- No new issue needs to be created now

**What #51 does NOT mean:**
- No `recovery_state` table
- No `backoff_until` or `expires_at_ms` fields
- No seeding in-memory RecoveryState from a database at startup
- No async write path on recovery state change

#51 remains open but its implementation scope is now undefined pending explicit
requirements from the operator.

---

## 8. Module: proxy/qz_operational_store.py

**Implemented in Slice B (commit 3fe042b).**

```python
# proxy/qz_operational_store.py — Phase 1 implemented API

class OperationalStore:
    """Lightweight SQLite store for QuantZhai runtime events and operational facts.

    Phase 1 scope: schema_meta, runtime_events, runtime_facts.
    Not BrainCaseDB. Not model-visible memory. Not recovery policy.
    """

    @classmethod
    def from_env(cls, env: dict | None = None) -> "OperationalStore":
        """Construct from QZ_OPERATIONAL_DB_ENABLED and QZ_OPERATIONAL_DB_PATH."""
        ...

    def init(self) -> bool:
        """Open/create DB. Idempotent. Non-fatal. Returns True when available."""
        ...

    def record_startup_event(self, phase: str, payload: dict | None = None,
                             source: str = "launcher") -> None:
        """Record a startup lifecycle event (replaces qz-write-runtime-state)."""
        ...

    def record_runtime_fact(self, key: str, value: dict,
                            provenance: str = "proxy") -> None:
        """Upsert a key-value operational fact."""
        ...

    def get_runtime_fact(self, key: str) -> dict | None:
        """Read a persisted operational fact, or None if absent or disabled."""
        ...

    def recent_events(self, event_type: str | None = None,
                      limit: int = 20) -> list[dict]:
        """Return recent runtime events, newest first. [] when disabled."""
        ...

    def health(self) -> dict:
        """Return {enabled, path, available, schema_version, last_error}."""
        ...

    def close(self) -> None: ...
```

**Accessor pattern:** `OperationalStore.from_env()` constructs from environment.
`store.init()` opens/creates the DB (idempotent, non-fatal). One instance per proxy
process; disabled mode is a complete no-op (no file created).

---

## 9. Implementation slice roadmap

| Slice | Content | Closes |
|---|---|---|
| **A-design** (Slice A + A.2) | Boundary, schema, non-goals, #46 replacement path | — |
| **B-impl** | `qz_operational_store.py` skeleton: open/close, schema creation, path/env | — |
| **B.1** | Audit/polish path, env, schema | — |
| **C-impl** | Startup event writer; `qz-write-runtime-state` dual-write | — |
| **C.1** | Launcher compatibility audit; JSON file stays | partial #46 |
| **Close-out** | Confirm #46 criteria met when JSON removed; decide #51 fate | #46 |

---

## 10. Test plan

```text
test_operational_store_path_respects_qz_var_dir
  QZ_VAR_DIR=/custom -> store at /custom/state/operational.sqlite3

test_operational_store_path_env_override
  QZ_OPERATIONAL_DB_PATH=/override.sqlite3 -> uses override path

test_schema_created_idempotently
  open() twice does not error; schema_meta has correct version row

test_runtime_event_insert_roundtrip
  record_startup_event("proxy_started", {"pid": 123})
  recent_events() returns the row

test_runtime_fact_upsert
  record_runtime_fact("last_model", {"slug": "qwen"})
  record_runtime_fact("last_model", {"slug": "apex"})
  get_runtime_fact("last_model") returns "apex"

test_operational_store_disabled_is_noop
  QZ_OPERATIONAL_DB_ENABLED=0 -> no file created; no error

test_operational_store_failure_is_non_fatal
  DB write failure -> no exception raised; error emitted to telemetry

test_qz_write_runtime_state_json_compatibility
  script still writes JSON file when OperationalStore disabled

test_qz_write_runtime_state_dual_write_when_enabled
  QZ_OPERATIONAL_DB_ENABLED=1 -> script also writes to runtime_events
```

---

## 11. Migration / compatibility constraints

```text
- var/run/qz-runtime-state.json:  stays until Slice C.1 confirms compatibility
- var/model-state.json:           not touched by OperationalStore in Phase 1
- var/backend-state.json:         not touched by OperationalStore in Phase 1
- BrainCaseDB (QZ_STATE_DB_PATH): untouched; separate module/schema/lifecycle
- qz-write-runtime-state script:  stays for compatibility through Slice C
```

---

## Related documents

- `scripts/qz-write-runtime-state` — current launcher trace script
- `scripts/qz-up` — calls qz-write-runtime-state at each phase
- `proxy/qz_recovery_state.py` — in-memory recovery state (unchanged by Phase 1)
- `proxy/qz_braincase_db.py` — BrainCaseDB (model-facing memory, separate)
- `docs/braincase-memory-tool-api.md` — BrainCaseDB doctrine
- Issue #51 — recovery state (needs explicit reframing before implementation)
- Issue #46 — qz-write-runtime-state replacement (primary Phase 1 driver)
