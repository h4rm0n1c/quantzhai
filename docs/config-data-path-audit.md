# QuantZhai Config / Data Path Audit

Date: 2026-05-10

> **Note (2026-05-19):** This audit predates the #56 A1 path migration.
> `var/model-inventory.json` references in this document describe the path
> **before** Slice C-impl. The current default is `var/generated/model-inventory.json`.
> See `docs/edge-case-config-contract-plan.md` §generated artifact path migration design.

This is the audit called for by `docs/edge-case-config-contract-plan.md`
before the config restructure work begins. It maps every significant data
path: source files, runtime writes, failure modes, current errors, and gaps.

---

## 1. Model Discovery

**Source files read:**
- `var/models/*.gguf` — all GGUF files in `QZ_MODEL_DIR`

**Env vars:**
- `QZ_MODEL_DIR` (default `$QZ_ROOT/var/models`) — set in `scripts/qz-env:56`

**Runtime writes:**
- `var/model-inventory.json` — generated catalog, written by `qz_model_catalog.py:write_cache()`

**Code path:**
`proxy/qz_model_catalog.py:scan_models()` → `build_entry()` per file → `write_cache()`

**Current failure behaviour:**
- Directory missing or empty → returns empty lists, no error raised, no stderr
- File corruption → exception caught in `build_entry()`, appended to `errors` list in return value
- Broken symlink → `path.is_symlink()` detected, `build_broken_symlink_entry()` called
- Caller receives an `errors` list but no errors are logged to stderr or surfaced to Codex

**Gaps:**
- Scan failures are completely silent; a missing model dir shows as an empty
  catalog with no visible hint to the operator
- No size or count limits (degenerate with 1000s of files)

---

## 2. Profile Alias Resolution

**Source files read:**
- `var/model-inventory.json` — cached catalog (primary)
- `config/user/model-overrides.json` (or fallback `var/model-overrides.json`) — per-model overrides

**Env vars:**
- `QZ_MODEL_OVERRIDES` (default `config/user/model-overrides.json`, fallback to `var/model-overrides.json`)

**Code path:**
`proxy/qz_model_catalog.py:match_model()` — two-pass: exact alias match then label/backend_id match

**Current failure behaviour:**
- No match → returns `None`; callers must check and handle

**Gaps:**
- No logged explanation of why a model name didn't resolve
- The `var/model-overrides.json` fallback location is in `var/`, which differs from
  the documented `config/user/` contract — a second truth risk

---

## 3. Symlink Profile Target Resolution

**Code path:**
`proxy/qz_model_catalog.py:validate_profile_targets()` — builds a real-file map,
resolves symlink targets into it, sets `profile_valid=False` and `profile_error`
for any target not found

**Current failure behaviour:**
- Broken symlink → `profile_valid=False`, `profile_error` string set
- Profile entry remains in catalog (can still be offered to Codex as a name)
- `qz-codex-common` filters these out at catalog-generation time using the
  `profile_valid` flag — so they are hidden from Codex

**Gaps:**
- Symlink chains (symlink → symlink) followed by `path.resolve()` without
  explicit max-hop validation
- A profile with `profile_valid=False` can still be named in config; if
  validation is ever skipped it would brick the session

---

## 4. Prompt Override Loading

**Source files read (per request):**
- `config/default/model-overrides.json` — base prompt defaults
- `config/user/model-overrides.json` — user overrides
- Arbitrary prompt files at paths specified in overrides (e.g. `system_prompt_file`)

**Code path:**
`proxy/qz_prompt_policy.py:assemble_instruction_stack()` →
`_selected_overrides()` → `_file_blocks()`

**Path resolution:**
`_resolve_prompt_path()` — expands `~`, then `{QZ_ROOT}/{relative}`, or absolute

**Current failure behaviour:**
- Missing prompt file → added to `prompt_files_missing` list in report dict;
  content silently omitted; no exception raised
- Encoding error → added to `prompt_files_failed` list; silently skipped
- All sources fail → assembled instructions are an empty string; no indication
  to the model that its system prompt is absent

**Gaps:**
- Empty prompt is accepted silently; there is no observable signal that a
  critical prompt file is missing
- File size not checked; a multi-MB file would be loaded into memory
- The report dict is generated per-request but only surfaced via telemetry;
  the operator has no at-a-glance view of "this session has no system prompt"

---

## 5. Codex Catalog Generation

**Script:** `scripts/qz-codex-common:qz_prepare_codex_home()` — runs on every
`qz-codex` invocation

**Source files read:**
- `var/model-inventory.json`
- `var/codex-home/config.toml` (if exists)

**Runtime writes:**
- `var/codex-home/model-catalogs/qwenzhai-models.json` — generated Codex-facing catalog
- `var/codex-home/config.toml` — updated with catalog path

**Code path:**
`python3 proxy/qz_model_catalog.py scan` → `var/model-inventory.json` →
inline Python heredoc in `qz-codex-common` → `var/codex-home/model-catalogs/qwenzhai-models.json`

**Current failure behaviour:**
- Inventory missing or corrupt → entire catalog generation fails with an
  exception inside the heredoc; `qz-codex` may proceed with a stale catalog
  or no catalog
- Individual model `build_live_model()` fails → that entry is skipped silently

**Gaps:**
- Single point of failure; if `var/model-inventory.json` is absent (first run,
  or deleted), the generation step fails and Codex gets an empty or stale catalog
- No verification that the output file was actually written
- Logic lives in a bash heredoc — hard to test, hard to debug
- Comment in the script acknowledges stale inventory risk but there is no
  enforcement

---

## 6. Runtime Status Generation (`/qz/status`)

**Code path:**
`proxy/qz_model_router.py:handle_ready_get()` → `status_snapshot()`

**Source files read:**
- `var/model-state.json` — via `_reconcile_status_state()`
- `var/backend-state.json` — via `_reconcile_status_state()`

**In-memory sources:**
- `handler` class attributes: `model_load_state`, `model_load_model`,
  `model_load_error`, `model_load_started_at`
- `handler.telemetry.latest_request_summary()`
- Live backend API: `_backend().get_models()`, `_backend().get_health()`

**Runtime writes:**
- May update `model-state.json` and `backend-state.json` if drift is detected

**Current failure behaviour:**
- Backend unreachable → health call returns status 0; error captured in
  `health_body`; `ready=False` reported
- State file corrupt → `read_json()` returns `{}`; defaults apply; no error surfaced
- `selected` is `None` if catalog is empty

**Gaps:**
- In-memory load state is authoritative during a session but lost on proxy
  restart; `/qz/status` may show stale state briefly after restart
- No indication in the `/qz/status` response that the state is freshly
  reconciled vs read from file

---

## 7. Backend State Persistence

**Files written:**
- `var/backend-state.json`
- `var/model-state.json`

**Env vars:**
- `QZ_BACKEND_STATE_PATH` (default `var/backend-state.json`)

**Code path:**
`proxy/qz_model_router.py:_persist_backend_state()` and `_persist_model_state()`
— called on load start, load complete, restart, and reconciliation

**Current failure behaviour:**
- Write fails (permission, disk full) → exception caught in `try/except: pass`
  — state is silently not persisted; in-memory state is still correct for the
  current session but is lost on restart
- File missing on read → defaults to `{}`
- Corrupted JSON → caught, returns `{}`

**Gaps:**
- `try/except: pass` on write — completely silent failure means the operator
  has no idea state is not being persisted
- No file locking; two concurrent proxy processes writing to the same files
  would corrupt state (unlikely in current use but not impossible)
- No schema version in the state files; format changes break existing files
  silently

---

## 8. Capture Writing

**Env vars:**
- `QZ_CAPTURE_MODE` (default `off`) — values: `off`, `latest`, `minimal`, `full`,
  or truthy aliases

**Code path:**
`proxy/qz_runtime_io.py` — `write_dual_capture()`, `append_dual_capture()`,
`open_dual_capture_append()`

**Directories written:**
- `var/captures/latest-*.json|txt|raw` — latest per-request state (overwrites)
- `var/captures/requests/{request_id}/` — per-request subdirectory, never deleted

**Notable files:**
- `latest-paths.log` — append-only routing log, no rotation
- `requests/*/forwarded-sse.raw` — raw upstream SSE stream per request

**Current failure behaviour:**
- `var/captures/` dir missing → `_ensure_capture_dir()` creates it; mkdir errors
  caught silently
- Disk full during write → not caught; exception propagates (only the mkdir is
  wrapped)
- Disabled mode → early return, no write

**Gaps:**
- `requests/` subdirectories accumulate indefinitely; no cleanup or rotation
- `latest-paths.log` is append-only with no rotation; grows without bound in
  any long-running session
- Disk-full errors on capture writes are not caught; they will surface as
  unhandled exceptions and potentially crash the request handler
- No documented retention policy or cleanup tooling

---

## 9. Search Policy Loading

**Source files read:**
- `config/default/search-policy.json` — base policy (env: `SEARXNG_POLICY`)
- Optional per-model policy file referenced in model overrides

**Env vars:**
- `SEARXNG_POLICY` (default `config/default/search-policy.json`)
- `SEARXNG_BASE_URL` — SearXNG instance URL
- `SEARXNG_TIMEOUT` (default 15s)

**Code path:**
`proxy/qz_search_policy.py:resolve_search_policy_selection()` — checks overrides,
tries to load from file, falls back to base policy

**Current failure behaviour:**
- Policy file not found → falls back to base policy; error stored in
  `SearchPolicySelection.error`
- Corrupt JSON → caught; falls back; error stored
- Base policy missing → returns empty dict

**Gaps:**
- No schema validation on loaded policy; a malformed policy file silently
  produces a bad policy rather than an error
- If both override and base policy fail, the caller gets an empty dict with no
  clear signal

---

## 10. `var/` Layout

**Currently exists at runtime:**

```
var/
  models/          GGUF files + symlink profiles        — well understood
  model-inventory.json  generated catalog               — well understood
  model-state.json      selected model state            — well understood
  backend-state.json    backend load state              — well understood
  codex-home/      Codex runtime dir                   — mostly generated
    config.toml
    model-catalogs/qwenzhai-models.json
    sqlite/
    sessions/
    memories/
  captures/        request/response captures           — well understood
    latest-*
    requests/
    reference/     (manual reference captures)
  logs/            proxy and benchmark logs             — well understood
  run/             pid files, runtime sockets           — implicit only
  benchmarks/      benchmark outputs                   — implicit only
  prompts/         user-placed prompt files             — undocumented
  tmp/             temporary files                     — undocumented
  sendtg.py        utility script in wrong place        — should be scripts/
  smartplugstate.py utility script in wrong place       — should be scripts/
  apply_patch_*.md  fuzz-session analysis docs          — ephemeral, correct
```

**Documentation vs code-only:**
- `var/run/`, `var/benchmarks/`, `var/prompts/`, `var/tmp/` — created by code
  or manual use; not formally documented anywhere
- `var/sendtg.py`, `var/smartplugstate.py` — utility scripts that ended up in
  `var/` but don't belong there; `var/` is gitignored so these would be lost
  on a fresh clone

---

## Findings Summary

### Severity: fix soon

| # | Finding | Location |
|---|---------|----------|
| F1 | State write failures are silently swallowed (`try/except: pass`) | `qz_model_router.py:_persist_*` |
| F2 | `latest-paths.log` is append-only with no rotation | `qz_runtime_io.py` |
| F3 | `var/captures/requests/` grows unbounded with no cleanup | `qz_runtime_io.py` |
| F4 | Disk-full errors during capture writes not caught | `qz_runtime_io.py` |
| F5 | `sendtg.py` and `smartplugstate.py` live in `var/` (gitignored) | `var/` |

### Severity: address in the config contract pass

| # | Finding | Location |
|---|---------|----------|
| F6 | Model scan failures are fully silent (no stderr, no log) | `qz_model_catalog.py` |
| F7 | Empty/missing prompt files silently produce a no-prompt session | `qz_prompt_policy.py` |
| F8 | Codex catalog generation is a bash heredoc — untestable, fragile | `qz-codex-common` |
| F9 | `var/model-overrides.json` fallback location conflicts with `config/user/` contract | `qz-env` |
| F10 | No retention policy or tooling for captures | `qz_runtime_io.py` |

### Informational / deferred

| # | Finding | Location |
|---|---------|----------|
| F11 | Symlink chain depth not validated | `qz_model_catalog.py` |
| F12 | Prompt file size not bounded | `qz_prompt_policy.py` |
| F13 | Search policy not schema-validated | `qz_search_policy.py` |
| F14 | `var/run/`, `var/benchmarks/`, `var/prompts/`, `var/tmp/` undocumented | `var/` |
| F15 | State files have no schema version field | `qz_model_router.py` |

---

## Recommended next actions

**Immediate (do not require config restructure):**

1. Fix F1 — replace `try/except: pass` on state writes with actual log + continue.
2. Fix F5 — move `sendtg.py` and `smartplugstate.py` out of `var/` into `scripts/` before they are lost.
3. Fix F4 — wrap capture write failures in a try/except that logs but does not crash.

**Config contract pass (do after the above, in order):**

4. Fix F8 — extract the Codex catalog generation heredoc from `qz-codex-common`
   into a proper Python script (`proxy/qz_codex_catalog.py` or similar) with
   tests. This unblocks #5.
5. Fix F7 — surface a visible signal (at minimum a `/qz/status` field, ideally
   a startup log line) when the assembled instruction stack is empty.
6. Fix F9 — collapse `var/model-overrides.json` fallback; only `config/user/` is
   the override location.
7. Fix F10 — add a `qz-capture-prune` script or a `QZ_CAPTURE_RETAIN_DAYS`
   policy; document in README.

**Deferred (requires broader restructure):**

8. F11-F15 — pick up alongside the config layout restructure proposed in
   `docs/edge-case-config-contract-plan.md`.
