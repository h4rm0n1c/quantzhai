# State and Memory Architecture Review — DeepSeek

Date: 2026-05-12
Reviewer: DeepSeek via OpenCode
Status: review-only; no implementation

## Verdict

**Needs doc changes before Phase 1 can start.** The architecture doc is directionally sound — typed memory classes, explicit scopes, bleed prevention, SQLite substrate — but gaps in the current repo mean several key pieces are not yet grounded enough to write safe DB code.

The smallest safe next step is to:

1. Audit whether `session_id`/`thread_id` headers actually arrive from local Codex.
2. Derive `workspace_id` and `profile_family` from current request metadata.
3. Align the open questions in sections 11–14 with the repo's actual available scope metadata.

## High-confidence findings

- **No `session_id`, `thread_id`, or `previous_response_id` from Codex is captured or stored today.** The proxy generates its own `request_id` (`qz_req_<ts>_<id>`) and uses per-hop response IDs (`resp_local_<ts>`). The repeated-read plan's `previous_response_id`/minimal-input path and the architecture plan's session/response-chain tables therefore rest on unconfirmed metadata.

- **`workspace_id` cannot be derived proxy-side today.** The proxy knows `QZ_ROOT` (the repo root) but has no access to the client's `cwd`, git root, or repo URL. Codex sends workspace-relative paths inside tool calls, but the proxy does not reconstruct the client-side workspace identity.

- **`profile_family` has no explicit representation.** Profile names in `var/models/` (e.g. `prompt-compiler.gguf`, `roleplay-character.gguf`) hint at persona intent, but there is no `profile_family` field in the model catalog, inventory, overrides, or prompt contract. The existing `infer_reasoning_level()` function (`qz_model_catalog.py:179`) is the closest pattern, but it infers reasoning effort from name text, not family.

- **Runtime state is split across three JSON files that are written by different paths and are sometimes stale.** `model-state.json` (written by `_persist_model_state` in `qz_model_router.py`), `backend-state.json` (written by `_persist_backend_state`), and `qz-runtime-state.json` (written by `qz-write-runtime-state` script) each hold overlapping fields. The proxy's in-memory `ModelRouter`/`TelemetryBus` is the de facto live truth.

- **Repeated-read v1 is not yet implemented.** `proxy/qz_file_signal.py` does not exist. No `RepeatedReadState`, `RepeatedReadDecision`, or `kind='signal'` lifecycle type exists. The v1 design is sound and stays intentionally stateless/input-seeded. It should proceed independently of the larger architecture.

- **The doc's "required research" (section 11) correctly identifies the unknowns but does not document the repo's actual current state.** Items 1 (headers), 2 (JSON ownership), 3 (profile_family), 4 (workspace_id), 7 (isolation tests), and 8 (promotion tests) can be answered immediately from a repo audit, but the doc lists them as open research. The review below provides those answers.

## Current repo state map

| Path or subsystem | Current role | Live truth / generated / config / debug / stale | Should move to SQLite? | Notes |
|---|---|---|---|---|
| `var/model-state.json` | Persisted runtime truth for selected model | State fallback; live truth is in-memory `ModelRouter` | Yes — first | Written by `_persist_model_state()`, read by `load_last_selected_model()`. Classified as "state_fallback" by `qz_config_report.py:219`. |
| `var/backend-state.json` | Persisted runtime truth for backend state | State fallback; live truth is in-memory `ModelRouter` | Yes — first | Written by `_persist_backend_state()`, read by `load_backend_state()`. Classified as "state_fallback" by `qz_config_report.py:220`. |
| `var/run/qz-runtime-state.json` | Startup/phase snapshot written by `qz-write-runtime-state` | Debug/startup snapshot; classified as "startup_snapshot" | Yes — after model/backend state | Written by `qz-up` phases. Not read by any proxy code in normal operation. Stale if the proxy restarts independently. |
| `var/model-inventory.json` | Generated model scan cache | Generated; classified as "generated_inventory" | Deferred | Written by `ModelCatalog.write_cache()`. Read by `qz_codex_catalog.py` script. Proxy owns authoritative catalog data in-memory. |
| `var/codex-home/config.toml` | Generated Codex config | Generated; patched in-place by `qz_codex_catalog.py` | No — file-based | Codex reads this from disk. Proxy can regenerate it. |
| `var/codex-home/model-catalogs/qwenzhai-models.json` | Generated Codex model catalog | Generated; shipped to Codex | No — file-based | Codex consumes via `model_catalog_json` config key. Proxy regenerates from inventory. |
| `var/captures/latest-*.json, latest-*.raw, latest-*.txt` | Request/response debug captures | Debug/replay artifacts | No — file-based | Written by `qz_runtime_io.py` when `QZ_CAPTURE_MODE` is on. |
| `var/captures/requests/` | Request-scoped captures | Debug/replay artifacts | No — file-based | Per-request subdirectories. |
| `var/logs/` | Log files | Debug artifacts | No — file-based | Not read by proxy logic. |
| `var/benchmarks/<run>/` | Benchmark run output | Debug/replay artifacts | No — file-based | Contains `latest-summary.json` as benchmark cache. |
| `config/default/*.json` | Shipped defaults | Config | No — file-based | Human-editable. |
| `config/example/*.json` | Copyable examples | Config | No — file-based | Never active unless explicitly selected via `QZ_LOAD_EXAMPLE_MODEL_OVERRIDES`. |
| `config/user/*.json` | Local overrides | Config | No — file-based | Human-editable. Not committed. |
| `proxy/qz_telemetry.py` in-memory `TelemetryBus` | Live telemetry and event bus | Live truth | **Already in-memory**; optionally persisted | The `TelemetryBus` holds recent events, request-scoped lifecycle, and throughput samples. No file persistence yet. |
| `proxy/qz_model_router.py` in-memory `ModelRouter` | Live model/backend state | Live truth | **Already in-memory**; move state persistence | The `ModelRouter` owns `status_snapshot()`, context length resolution, backend model loading/unloading. JSON files are write-through cache. |
| `proxy/qz_request_router.py` in-memory `RequestRouter` | Request dispatch, response chaining | Live truth for request lifecycle | Session/response tables needed | All request identity, previous_response_id handling, session tracking is in-memory only or absent. |

## Memory classes found in the repo

### Runtime state

- **Evidence:** `var/model-state.json`, `var/backend-state.json`, `var/run/qz-runtime-state.json`, `ModelRouter.status_snapshot()`, `TelemetryBus`, `RequestRouter._runtime_metrics()`.
- **Current files/functions:** `qz_model_catalog.py:40` (load_last_selected_model), `qz_model_router.py:123-186` (model_state_path, backend_state_path, _persist_model_state, _persist_backend_state), `qz_request_router.py:385-423` (_runtime_metrics).
- **Proposed future owner:** SQLite `runtime_state` table or equivalent.
- **Risks:** Runtime truth is already partially duplicated across three JSON files and in-memory state. JSON writes can fail silently (caught in `_persist_model_state` and `_persist_backend_state`). No transactional consistency between model and backend state.

### Operational tool memory

- **Evidence:** **Not present.** `qz_file_signal.py` does not exist. Repeated-read v1 is not implemented. No `RepeatedReadState`, `kind='signal'` lifecycle type, or file-read/write tracking exists.
- **Current files/functions:** None. The repeated-read plan (`docs/repeated-read-dedup-plan.md`) defines the v1 design, but no code exists yet.
- **Proposed future owner:** SQLite tables for file_reads, file_writes, signals; v1 is stateless (input-seeded).
- **Risks:** None yet — the feature does not exist. If v1 is implemented before the architecture review, ensure it stays stateless per its own design doc.

### Conversation/session memory

- **Evidence:** **Not present.** No session_id, thread_id, previous_response_id, or response chain is persisted. `_request_id()` in `qz_request_router.py:593-594` generates a unique ID per request but does not link to prior requests. Response IDs (`resp_local_<ts>`) are generated per-hop and not stored.
- **Current files/functions:** `qz_request_router.py:593` `_request_id()`, `qz_request_router.py:865` `request_id` local variable. All ephemeral.
- **Proposed future owner:** SQLite `sessions`, `responses`, `tool_calls` tables.
- **Risks:** Codex manages conversation history by replaying items in `body["input"]`. The proxy never needs to reconstruct session state for the current local proxy contract. `previous_response_id` support would be a new contract.

### Workspace/project memory

- **Evidence:** **Not present.** The proxy has no concept of workspace_id, cwd, git root, or repo URL for the client's project.
- **Current files/functions:** `qz_config_report.py:72` `_resolve_repo_path()` resolves prompt file paths relative to QZ_ROOT (the proxy's repo), not the client's workspace. `qz_tool_apply_patch.py:44` mentions "workspace-relative" as a description field. `QZ_ROOT` is the QuantZhai repo, not the coding project.
- **Proposed future owner:** SQLite `workspace_memory` table; requires client-originated workspace_id.
- **Risks:** Cannot be implemented until workspace_id is derivable from the client request or explicit config. Attempting DB code without a workspace_id source would build on speculation.

### Coding preference memory

- **Evidence:** **Not present.** No persistent coding preferences exist. Prompt files (`config/user/prompts/*.md`), model overrides (`config/user/model-overrides.json`), and turn harnesses (`turn_harness_definitions` in overrides) are the closest approximation, but they are file-based and per-profile, not a learned preference store.
- **Current files/functions:** `qz_prompt_policy.py` `assemble_instruction_stack()`, `config/default/model-overrides.json`, `config/user/model-overrides.json`.
- **Proposed future owner:** SQLite `preferences`/`skills` tables; promotion audit trail.
- **Risks:** Section 5 of the architecture doc correctly flags that private/profile facts must not become global preferences. Since no preference system exists yet, the risk is future design, not current bleed.

### Profile-private memory

- **Evidence:** **Not present.** Profiles (`*.gguf` symlinks under `var/models/`) carry per-model overrides (prompt files, reasoning stream format, turn harnesses) but no persistent session/memory state. Each request starts fresh unless Codex replays history in `body["input"]`.
- **Current files/functions:** `qz_model_catalog.py:325-385` `build_entry()` creates profile entries from GGUF metadata + overrides. `qz_prompt_policy.py` loads prompt files per profile. No per-profile memory exists.
- **Proposed future owner:** SQLite tables scoped to profile_id; separate DB file or hard scope barrier for belt-and-braces isolation.
- **Risks:** No current bleed risk because no persistent private memory exists. The real risk is that the first DB layer accidentally creates cross-profile leakage by using a single global table without scope columns.

### HSM / Holstrom / archive memory

- **Evidence:** **Not present.** No HSM-specific code, tables, files, or endpoints exist.
- **Current files/functions:** None.
- **Proposed future owner:** Separate connector/profile; explicit import/export boundaries.
- **Risks:** The architecture doc correctly says "Do not build that in the first DB layer" (section 10). Low risk if deferred.

### Debug/capture memory

- **Evidence:** Fully present. `qz_runtime_io.py` manages captures, logs, and runtime state files. `QZ_CAPTURE_MODE` controls verbosity (`off`/`latest`/`minimal`/`full`).
- **Current files/functions:** `qz_runtime_io.py` (capture_path, write_capture, write_dual_capture, append_capture, etc.), `qz_request_router.py` (write_capture calls), `qz_config_report.py:222-224` (capture_dir, log_dir, benchmark_summary records).
- **Proposed future owner:** Keep as files; optionally add SQLite indexes later.
- **Risks:** The architecture doc correctly rules that "Captures are not memory just because they exist" (section 3.8). The critical risk is that generated catalog files under `var/codex-home/` are sometimes read by scripts as if they were config sources — this is a config-contract problem, not a memory problem.

## Scope metadata available today

| Metadata | Source | Reliability | Useful for v1 DB? | Notes |
|---|---|---|---|---|
| `request_id` | `RequestRouter._request_id()` (`qz_request_router.py:593`) | **High** — generated from monotonic ts + handler id | **Yes** — primary key for requests table | Format: `qz_req_{ts}_{id}`. Unique per request. |
| `profile/model slug` | `selected_model` dict (`key`, `stem`, `label`, `backend_id`) (`qz_model_catalog.py:351-368`) | **High** — from GGUF scan + overrides | **Yes** — profile_id for scopes | Present in `prompt_contract`, `runtime_metrics`, catalog entries. |
| `backend_id` | `selected_model["backend_id"]` or `entry_identity()` (`qz_model_catalog.py:354`) | **High** — resolved from GGUF scan | **Yes** — backend routing | Distinct from profile slug; the architecture doc should clarify whether v1 DB stores profile_id or backend_id as the identity. |
| `reasoning_stream_format` | Selected model overrides + env default (`qz_request_router.py:98-112`) | **High** | **Maybe** — profile metadata | Stored in request metadata as `qz_reasoning_stream_format`. |
| `prompt_policy` mode/files | `qz_prompt_policy.py` report, injected as `qz_prompt_policy` metadata | **High** for current request | **Maybe** — request metadata | Already structured as `prompt_contract.prompt_policy`. |
| `turn_harness` state | `qz_request_normalization.py` harness selection | **High** | **Maybe** — request metadata | Already structured as `prompt_contract.turn_harness`. |
| `reasoning` level/policy | `ModelRouter.selected_reasoning_policy()` (`qz_model_router.py:502-522`) | **High** | **Maybe** — profile/session metadata | Injected as `qz_reasoning` metadata on body. |
| `QZ_ROOT` (proxy root) | `QZ_ROOT` env var or `Path(__file__).parents[1]` (`qz-env:27`, `qz_config_report.py:11-15`) | **High** | **Useful for config paths, NOT workspace_id** | This is the QuantZhai repo root, not the client's coding project. |
| `cwd` / workspace / git root | **Not available proxy-side** | N/A | **Must be sourced before workspace_memory** | Codex sends paths relative to its own cwd inside tool call arguments, but the proxy does not extract them into a workspace_id field. |
| `session_id` | **Not captured** | N/A | **Must audit Codex traffic first** | No handler reads `session_id` or `session-id` headers. `qz_tool_request.py:17` has a regex for `session_id` inside tool output, not metadata. |
| `thread_id` | **Not captured** | N/A | **Must audit Codex traffic first** | No handler reads `thread_id` or `thread-id` headers. |
| `previous_response_id` | **Never received** | N/A | **Cannot populate v1 DB without client support** | The architecture doc assumes `previous_response_id` will arrive; the repeated-read plan correctly flags this as a future contract. |
| `response_id` | Generated per-hop (`resp_local_<ts>`) | **High** but ephemeral | **Store generated IDs for chain tracking** | Each hop creates a new `resp_local_<ts>`. Not persisted between hops or requests. |
| Model catalog `context_window` | Generated Codex catalog (`qz_codex_catalog.py:262-263`) | **High** after catalog regeneration | **Maybe** — useful as profile metadata | Duplicated in `prompt_contract.context_length` and `runtime_metrics`. |
| `profile_family` | **Not represented** | N/A | **Must be added before scope isolation** | No field in catalog, inventory, overrides, or request metadata. |

## Gaps in the architecture doc

### Section 3 — Memory classes

- Operational tool memory: the doc correctly describes this as the v2 repeated-read target. Update to reflect that v1 is stateless and the v2 dependency on SQLite should wait until after v1 is shipped and the file-read/fact schema is tested against real traffic.
- Workspace/project memory: the doc assumes `workspace_id` is available. It is not. Section 3.4 should note that the workspace memory class is blocked without a workspace_id derivation mechanism.

### Section 4 — Scope boundaries

- `profile_family` is listed as a minimum scope dimension but has no proposed derivation. Add: "Currently unavailable. Must be added as an explicit override field in model overrides or inferred from a new `profile_family` field in the catalog. Do not derive from profile name text alone."

### Section 5 — Bleed-prevention policy

- "roleplay/private/intimate session state → coding session" is correctly forbidden. However, the doc does not define what makes a profile "roleplay/private" vs "coding." Without `profile_family`, this policy cannot be enforced. Add a note: "Profile family classification is prerequisite. See section 4."

### Section 7 — Minimal table families

- `preferences`, `skills`, `promotion_events`, and `artifacts` tables are listed as "planning sketch" but are not clearly marked as deferred. Section 7 should explicitly say "Do not implement `preferences`, `skills`, `promotion_events`, or `artifacts` in Phase 1."
- `sessions` table needs a note that `session_id` and `thread_id` are not currently available in local Codex traffic. Phase 1 may need to generate proxy-internal session IDs or use `response_chain_id` as the primary session identity.

### Section 8 — What should stay as files

- `var/model-inventory.json` is correctly listed as "until catalog ownership is fully settled." Add clarification that after Phase 4 (runtime JSON migration), the inventory remains JSON-generated by the proxy but is no longer the source of backend/model-state truth.
- `var/codex-home/sqlite/*` (Codex's own SQLite DB) is not listed. Add as a file that should stay — it is Codex's internal storage, not QuantZhai's.

### Section 11 — Required research

- **Item 1 (headers):** Add the finding from this review: `session_id`, `session-id`, `thread_id`, `thread-id`, and `previous_response_id` are NOT present in current proxy code or known capture traffic. Audit required before session/thread tables can be populated.
- **Item 2 (JSON ownership):** The audit from `qz_config_report.py` already exists and classifies each path. Point to it.
- **Item 3 (profile_family):** Add: "No profile_family field exists. Must be added explicitly or resolved from a new profile registry. Inferred heuristics from profile names are unreliable."
- **Item 4 (workspace_id):** Add: "Not derivable from current proxy-side data. Options: accept workspace_id from Codex headers (not currently sent), derive from a `QZ_WORKSPACE` env var, or skip workspace isolation in v1 DB."
- **Item 7 (isolation tests):** Add prerequisite: "Cannot test private→coding isolation without profile_family classification."

### Section 12 — Implementation phases

- Phase 1: "Persist only session/request/response identity at first." Add: "If `session_id` is not available from client traffic, generate a proxy-owned session ID or use `request_id` chain as the session identity."
- Phase 2: "Add scope records for coding/workspace/profile/private/admin." Add: "Blocked until `profile_family` is available and `workspace_id` derivation is solved."

### Section 14 — Open questions

- "Should `profile_family` be declared in model overrides, inferred from profile name, or stored in a separate profile registry?" → Answer from repo audit: It does not exist anywhere today. Declaring it in model overrides (`config/user/model-overrides.json`) is the smallest change. Inference from name is unreliable (roleplay vs coding can share naming patterns). A separate registry is overkill for v1.
- "Should private/roleplay memory live in the same SQLite database with hard scope barriers, or a separate DB file?" → Answer: Same DB with hard scope barriers for v1. Only split when a concrete use case proves cross-scope reads are happening despite scope columns.
- "How does the user approve/promote a learned coding preference?" → Answer: Not implementable until preferences exist (Phase 5+). Do not design promotion UI before Phase 4.
- "Can workspace_id be safely derived from git root?" → Answer: Not proxy-side. The proxy does not see the client's filesystem. Workspace_id must come from the client or explicit config.
- "Should HSM/Holstrom integration live inside QuantZhai or as an explicit external store/connector?" → Answer: External connector. The doc already says this. Keep in deferred.
- "How long should operational tool memory live?" → Answer: Session-scoped for v1; workspace-scoped for v2.
- "How much raw output should be stored versus digests/previews only?" → Answer: Digests/previews only for operational memory. Raw output stays in captures (files).

### Sections needing updates

- Section 3.1: Add that runtime state files are written but not live truth.
- Section 3.3: Add that session/response chain identity is absent.
- Section 4: Add that `profile_family` and `workspace_id` are not derivable.
- Section 7: Mark preferences/skills/promotion_events/artifacts as Phase 5+.
- Section 11: Add concrete answers from this review for items 1–4.
- Section 12 Phase 1: Add note about session_id availability being unknown.
- Section 12 Phase 2: Mark scope records as blocked on profile_family + workspace_id.
- Section 14: Add answers from this review.

## Proposed exact doc edits

### Section 7 — "Minimal table families"

Change "planning sketch, not final SQL" paragraph to add:

> **Phase 1 restrictions:** Do not implement `preferences`, `skills`, `promotion_events`, or `artifacts` in the first DB patch. Those tables depend on scope metadata (`profile_family`, `workspace_id`) that is not yet available, and on promotion semantics that have not been designed. Keep v1 focused on identity and history: `scopes`, `sessions`, `responses`, `tool_calls`, `file_reads`/`file_writes`, `signals`, `compaction_events`.

### Section 4 — Scope boundaries

After `profile_family` definition, add:

> **Availability note (2026-05-12):** `profile_family` cannot be derived from current profile metadata. Profile names in `var/models/` hint at family (e.g. `prompt-compiler.gguf` for coding, `roleplay-character.gguf` for roleplay), but there is no explicit field in the catalog, inventory, or overrides. Phase 1 must either add a `profile_family` field to `config/user/model-overrides.json` or defer scope isolation until Phase 2.

After `workspace_id` definition, add:

> **Availability note (2026-05-12):** The proxy cannot derive the client's coding workspace. It knows `QZ_ROOT` (QuantZhai's own repo root) but does not receive the client's `cwd`, git root, or repo URL. `workspace_id` must be accepted from a future Codex header, read from an explicit env override (`QZ_WORKSPACE_ID`), or omitted from the first DB patch entirely.

### Section 12 Phase 1 — "Minimal DB substrate"

After "Persist only session/request/response identity at first," add:

> **Known limitation:** `session_id` and `thread_id` are not currently present in local Codex → QuantZhai traffic. The proxy generates its own `request_id` but does not receive session or thread identifiers from the client. Phase 1 should either generate proxy-owned session IDs (via `request_id` chain or a new `session_id` header injection) or skip session-scoped responses until traffic confirms these identifiers exist.

### Section 11 item 1 — Headers audit

Replace:

> 1. Which headers/fields are available in real local Codex → QuantZhai traffic? session_id, session-id, thread_id, thread-id, previous_response_id, etc.

With:

> 1. **Headers audit result (2026-05-12):** `session_id`, `session-id`, `thread_id`, `thread-id`, and `previous_response_id` are absent from current proxy code and have not been observed in captures. The proxy generates its own `request_id` (`qz_req_<ts>_<id>`) and per-hop `resp_local_<ts>` response IDs. Before the first DB patch, run a capture audit on real Codex traffic to confirm whether any of these fields arrive in the body or headers. If none are found, Phase 1 must generate proxy-owned session/thread IDs.

### Section 14 — Open questions

Replace the open questions list with answers from this review (see "Gaps in the architecture doc / Section 14" above for full text).

## Smallest safe DB groundwork

### First module name

`proxy/qz_state_store.py`

### First tables

1. **`schema_migrations`** — versioning. Standard pattern.
2. **`scopes`** — tenant, profile_id, profile_family (nullable in v1), workspace_id (nullable in v1). One row per scope dimension combination.
3. **`sessions`** — session_id (generated proxy-side if client does not send one), thread_id (nullable), created_ts, last_seen_ts, scope_id FK.
4. **`responses`** — response_id, previous_response_id (nullable), request_id, session_id FK, created_ts, input_mode ("full_history" or "incremental" or "unknown"), compaction_flag.
5. **`tool_calls`** — tool_call_id, response_id FK, tool_name, call_type ("public", "proxy_local", "error", "signal"), status, created_ts.
6. **`file_reads`** / **`file_writes`** — path (normalised), response_id FK, session_id FK, tool_call_id FK, first_read_ts, read_count.
7. **`signals`** — signal_type ("repeated_read", "hop_budget", "context_pressure"), session_id FK, response_id FK, payload (JSON), created_ts.
8. **`compaction_events`** — response_id FK, session_id FK, compacted_item_count, new_item_count, created_ts.

### First migrations

- `001_create_schema_migrations.sql` — standard version table.
- `002_create_scopes.sql` — scopes table with unique constraint on (profile_id, workspace_id, profile_family).
- `003_create_sessions.sql` — sessions table linked to scopes.
- `004_create_responses.sql` — responses table linked to sessions.
- `005_create_tool_calls.sql` — tool_calls table linked to responses.
- `006_create_file_reads_writes.sql` — file_reads and file_writes tables linked to responses and sessions.
- `007_create_signals.sql` — signals table linked to responses and sessions.
- `008_create_compaction_events.sql` — compaction_events table linked to responses.

### First call sites

- After `_request_id()` is called in `proxy_json_api()` (`qz_request_router.py:865`): insert or update session row, insert response row.
- After a tool call decision is made in `completed_call_decision()` (`qz_proxy_tools.py`): insert tool_call row.
- After `record_tool_call()` / `record_tool_output()` in repeated-read v2 (`qz_file_signal.py`, not yet written): insert file_read/file_write rows.
- After a repeated-read signal, hop budget signal, or context pressure signal is emitted (`qz_responses_stream.py:1056-1073`): insert signal row.
- After auto-compaction triggers (`qz_request_router.py:950-963`): insert compaction_event row.

### What behaviour must not change

- All existing HTTP endpoints must return the same responses.
- All existing capture/log paths must keep working.
- Repeated-read v1 (not yet implemented but stateless by design) must not require SQLite.
- Model/backend/state JSON files must keep working if SQLite is unavailable — treat DB as optional enrichment, not hard dependency.
- `QZ_CAPTURE_MODE` must still control captures independently of DB writes.
- The proxy must start without a state DB; DB creation should be automatic on first import.

## Isolation tests

These tests must pass before Phase 2 (scopes and isolation) is considered complete. List them in a new `tests/test_qz_state_scopes.py` module.

| Test name | What it proves |
|---|---|
| `test_private_profile_memory_not_readable_by_coding_profile` | A row scoped to `profile_family="roleplay"`, `profile_id="roleplay-char"` cannot be read by a query scoped to `profile_family="coding"`, `profile_id="prompt-compiler"`. |
| `test_coding_workspace_memory_isolated_by_workspace_id` | A row scoped to `workspace_id="repo-A"` cannot be read by a query scoped to `workspace_id="repo-B"`, even when `profile_id` and `profile_family` match. |
| `test_global_coding_preferences_readable_by_coding_profiles_only` | A row with `visibility="global_coding"` is readable by `profile_family="coding"` queries but not by `profile_family="roleplay"` or `profile_family="hsm"`. |
| `test_debug_capture_memory_not_model_visible` | Capture/log/scoped rows tagged `state_class="debug_capture"` are excluded from model-visible query results by default. |
| `test_hsm_memory_requires_explicit_import_export` | A row scoped to `state_class="hsm_archive"` cannot be read by any normal query path; only explicit `WHERE state_class = "hsm_archive" AND scope_id = <exported_scope>` returns it. |
| `test_same_session_operational_tool_facts_readable` | A file_read row with same `session_id` as the query session is returned by the operational tool memory query. |
| `test_cross_session_operational_tool_facts_not_readable` | A file_read row with a different `session_id` is not returned by the operational tool memory query. |
| `test_previous_response_id_resolves_within_session` | A `responses` query by `previous_response_id` returns the correct prior response row only when the query and target share the same `session_id`. |
| `test_previous_response_id_cross_session_denied` | A `responses` query by `previous_response_id` where the query session differs from the target session returns no rows. |
| `test_repeated_read_v2_uses_same_scope_file_reads_only` | Repeated-read v2 signal query against file_read returns results scoped to the current session/workspace — does not return file_read rows from other sessions or profiles. |
| `test_minimal_input_mode_uses_db_history_when_scope_known` | When `previous_response_id` is present and the session exists in DB, the minimal-input resolution returns the expected prior response. When the session does not exist, it degrades safely (no crash, empty result). |
| `test_scope_requires_profile_family_or_workspace_id` | An insert into `scopes` with both `profile_family=NULL` and `workspace_id=NULL` is rejected. |
| `test_default_profile_family_is_unknown` | A scope inserted with no explicit `profile_family` stores `"unknown"` — never `NULL` or empty string. |

## Deferred work

These must NOT be implemented in the first DB/state patch:

- **`preferences` table** — depends on promotion semantics, user approval UI, and trust boundaries not yet designed. Phase 5+.
- **`skills` table** — same as preferences. Phase 5+.
- **`promotion_events` table** — the promotion mechanism (source→target, confidence, redaction_state) requires user-facing tooling. Phase 5+.
- **`artifacts` table** — file pointers for HSM/Holstrom sources. Phase 6+.
- **HSM/Holstrom connector** — explicit external store. Phase 6+.
- **Separate DB files for runtime vs memory vs HSM** — one SQLite DB for v1. Split only if retention/privacy/lifecycle requirements emerge.
- **`workspace_id`-scoped memory without a workspace_id source** — do not add empty code paths for scope columns that cannot be populated.
- **`profile_family`-based isolation without explicit profile_family classification** — do not build isolation logic on inferred heuristics.
- **`session_id`/`thread_id` from client headers** — do not add header parsing until captures confirm these arrive.
- **`previous_response_id` resolution** — do not implement server-side response chain resolution until Codex actually sends `previous_response_id` to the proxy.
- **Cross-profile preference promotion** — do not implement promotion logic until the preference store exists and a concrete "promote this" scenario is demonstrated.
- **Repeated-read v2** — v1 must ship and prove itself before adding persistent session-keyed state. The repeated-read plan (`docs/repeated-read-dedup-plan.md`) correctly defers v2 to after the architecture work.
- **Full shell parser for file-read/write extraction** — v1 uses `shlex.split()` + regex fallback. Defer full AST-level parsing.
- **Symlink resolution for path normalisation** — `os.path.normpath()` only for v1. Defer `os.path.realpath()`/filesystem access.
- **Content-hash similarity detection** for diminishing-return signals. Repeated-read plan defers Option 3.
- **Generic tool-call counter signal** — repeated-read plan defers Option 2 until Option 1 (path dedup) proves insufficient.
- **Orientation/redundant ls/find signal** — repeated-read plan defers in v1.
- **`warned_paths` seeding from prior `repeated_read_signal` outputs** — repeated-read plan explicitly defers v1 policy.
- **QZSTATE integration** — the LLM signal system doc (`docs/llm-signal-system.md`) says "This is not QZSTATE. QZSTATE is a separate experiment with a different purpose and is not part of this system." Keep it separate.
