# QuantZhai Current Task Hierarchy

Date: 2026-05-14
Status: active control sheet — post-stabilisation pass.

This document turns the current planning docs into an execution order. It does
not replace the architecture contracts. If this file conflicts with
`docs/current-architecture-authority.md` or
`docs/codex-context-memory-contract.md`, those documents win.

## Current hard rules

```text
Use memory_domain, not profile_family.
Missing memory_domain means isolated.
Capability detection from tools never grants durable memory access.
QuantZhai-owned qz_* context stays internal and must not be injected into
forwarded /v1/responses request bodies.
Phase 1 SQLite stores operational facts only.
No clever memory, active memory tools, learned preferences, roleplay memory,
HSM/archive memory, automatic promotion, or cross-domain sharing in Phase 1.
```

## Recently completed (2026-05-14 stabilisation run)

```text
qz.profiles.v1 active config + split default/example profiles (#26/PR#27)
  - config/default/profiles.json + profiles/*.json  (shipped defaults)
  - config/user/profiles.json + profiles/*.json     (local user config)
  - model-overrides.json preserved as legacy fallback per layer

memory_domain config plumbing (#23/PR#24, PR#25)
  - memory_domain read from profile overrides, stored on catalog entries
  - exposed in /v1/models, /qz/status, /qz/config/effective
  - memory.domain in qz.profiles.v1 maps to memory_domain internally
  - missing memory_domain resolves to isolated at request time
  - no inference from model/profile/client/tool names

Simplified reasoning-effort prompts (#29/PR#30)
  - short depth-only prompts; removed hard tool-call caps and cross-file mandates
  - high/xhigh preserve final-answer obligation

Sandbox/tool-failure telemetry and guidance (#28/PRs#31-34)
  - Slice 1: tool_escalation_requested on outgoing require_escalated calls
  - Slice 2: native tool-output classifier before normalization
    - tool_sandbox_denied  (Read-only file system)
    - tool_connection_failed  (Connection refused)
  - Slice 3: harness guidance in codex-core-qwenified.md
  - qz-thoughts renders denied/conn-fail/escalation activity rows

Live stack smoke test (#35/PR#36)
  - scripts/qz-live-smoke validates proxy health, config, qz-thoughts,
    unit guards, normal Codex path, and sandbox-denied telemetry end-to-end
```

## Dependency chain

```text
authority/docs cleanup (ongoing)
  -> [DONE] explicit memory_domain config plumbing
    -> optional/non-fatal Phase 1 SQLite operational substrate  ← current P1
      -> same-scope operational signals, starting with repeated-read v1/v2
        -> rendered state packets / LimbiCore recall later
```

---

## P0: Authority and task cleanup (ongoing)

Goal: stop agents from re-planning old decisions or following stale language.

Tasks:

```text
1. Keep this file updated as the short task DAG.
2. Update docs/README.md when new active docs are added.
3. Update docs/progress-snapshot.md after major implementation passes.
4. Mark old profile_family language as historical when encountered.
5. Keep docs/current-architecture-authority.md as the final conflict resolver.
```

Acceptance:

```text
A new agent can read docs/README.md, current-architecture-authority.md,
codex-context-memory-contract.md, and this file, then know what to do next.
```

---

## P1: Phase 1 SQLite operational substrate

Goal: store parser-derived operational facts safely without changing model-visible
behaviour.

Scope:

```text
Optional/non-fatal DB open.
Parser-boundary only.
Consume extract_codex_request_context().
Store structured facts and summaries, not giant raw request bodies.
DB write failure logs/telemeters but does not break proxy responses.
```

Likely new file:

```text
proxy/qz_state_db.py
```

Likely tests:

```text
tests/test_qz_state_db.py
tests/test_qz_request_state_integration.py
```

Phase 1 tables:

```text
sessions
turns
requests
workspace_candidates
resolved_workspaces
session_workspace_bindings
identity_conflicts
```

Must not implement:

```text
model-visible durable memory
learned global preferences
roleplay/profile-private memory
HSM/archive memory
automatic promotion
cross-domain sharing
repeated-read v2 persistence
forwarded qz_* request-body metadata injection
```

Acceptance tests:

```text
test_db_opens_optional_and_nonfatal
test_session_insert_with_codex_ids
test_turn_insert_groups_multiple_requests
test_request_insert_body_metadata_summary
test_workspace_candidate_insert
test_resolved_workspace_remote_preferred_over_path
test_workspace_unknown_when_no_candidates
test_identity_conflict_stored
test_db_failure_does_not_break_proxy_request
```

Blocked by:

```text
Nothing. memory_domain plumbing is done. This is the current P1.
```

Best resource:

```text
Codex/Claude after refresh.
```

---

## P2: Repeated-read signal

Goal: reduce wasted tool calls from redundant file reads without suppressing
legitimate re-reads.

Current rule:

```text
Implement repeated-read v1 as advisory, stateless, and input-history-seeded.
Do not implement persistent v2 until Phase 1 SQLite scope is available.
```

V1 likely files:

```text
proxy/qz_file_signal.py
proxy/qz_tool_lifecycle.py
proxy/qz_proxy_tools.py
proxy/qz_responses_stream.py
proxy/qz_request_router.py
tests/test_qz_file_signal.py
tests/test_qz_proxy_tools.py
```

V1 behaviour:

```text
Seed read/write state from body["input"] function_call/function_call_output items.
Detect conservative read commands such as cat/head/tail/sed/nl/rg/grep/wc.
Do not scan normal message text.
Do not parse ls/find as file reads.
Signal once per path per request/run.
After a warning in the same run, allow the next repeat through.
Suppress warning after a write to that path.
```

V2 behaviour:

```text
Use same-scope file read/write/signal facts from SQLite.
Scope by qz_session_id, qz_turn_id/codex_turn_id, qz_request_id, workspace_id,
and memory_domain.
Never cross workspace or memory_domain.
```

Blocked by:

```text
V1: not blocked. Start any time; integration is cleaner after P1 SQLite.
V2: blocked by P1 SQLite substrate and scope queries.
```

Best resource:

```text
DeepSeek can draft parser tests. Codex/Claude should do integration.
```

---

## P3: Telemetry filter ergonomics / qz-live-smoke refinements

Goal: reduce friction when diagnosing tool-failure events in a noisy telemetry
stream.

Scope:

```text
Optional /qz/telemetry/recent?type=tool_sandbox_denied query parameter.
Optional /qz/telemetry/recent?types=A,B filter for multiple event types.
qz-live-smoke --model flag default confirmation.
Consider a per-request telemetry endpoint (/qz/telemetry/request?request_id=...).
```

Blocked by:

```text
Not blocked. Low priority; implement when the noisy-window problem recurs.
```

---

## P4: Config/var/script ownership cleanup

Goal: reduce second truths and script-owned policy.

Current problem:

```text
source defaults, examples, user overrides, generated Codex config, runtime state,
captures, logs, cache, model inventory, and profile symlinks are still too easy
to confuse.
```

Rules:

```text
Do not move model files, profile symlinks, or Codex-visible slugs casually.
Do not add new one-off shell scripts unless strongly justified.
Do not make generated Codex catalog files into routing authority.
Proxy policy remains the source of truth.
```

Likely tasks:

```text
1. Improve /qz/config/effective coverage.
2. Surface missing prompt files and override warnings in one place.
3. Make /qz/models/refresh regenerate the Codex catalog file too.
4. Move more generated output toward var/generated/ only after report coverage.
5. Thin qz-codex-common after proxy owns catalog generation.
```

Blocked by:

```text
Not strictly blocked, but avoid broad refactor before P1/P2 unless fixing a
specific live breakage.
```

Best resource:

```text
Codex/Claude, after tests are strong.
```

---

## P5: Observability polish and backend VRAM telemetry

Goal: finish monitor truth and remove remaining fake certainty.

Known remaining work:

```text
backend/proxy VRAM snapshot telemetry for qz-top USED/BASE/cache/buffer split
first-status correctness tests
long-running TUI validation
profile prompt/config ownership review
fixed profile-eval prompt set in benchmark harness
```

Blocked by:

```text
No hard blocker. Lower priority than P1/P2 unless runtime diagnosis becomes hard.
```

Best resource:

```text
Cheap/free agents for audit, Codex for implementation.
```

---

## P6: LimbiCore rendered state packets and future memory

Goal: eventually render small purpose-specific state packets or recall results
from scoped records.

Not now.

Future work includes:

```text
StateRecord envelope
rendered coding state packets
utility LLM proposal jobs
memory.search / memory.propose_write tools
roleplay specialised renderers
HSM evidence/provenance renderers
```

Blocked by:

```text
P1 memory_domain config
P2 SQLite substrate
explicit render policy
cross-domain isolation tests
```

---

## First implementation prompt: P1 SQLite (current target)

```text
Implement the Phase 1 SQLite operational substrate for QuantZhai.

Read first:
- docs/current-architecture-authority.md
- docs/codex-context-memory-contract.md
- docs/model-state-signal-contract.md
- docs/current-task-hierarchy.md
- proxy/qz_codex_metadata.py

Goal:
- add optional/non-fatal SQLite storage for parser-derived operational facts
- consume extract_codex_request_context()
- store sessions, turns, requests, workspace candidates, resolved workspaces,
  session workspace bindings, and identity conflicts
- store structured metadata/digests/summaries, not giant raw request bodies
- DB open/write failures must not break proxy request handling
- do not change model-visible behaviour
- do not implement learned preferences, durable memory, profile-private memory,
  HSM/archive memory, promotion, recall, renderers, or repeated-read v2

Add tests for DB open, inserts, workspace resolution, unknown workspace,
identity conflict storage, and non-fatal DB failure.

Keep the patch boring.
```

## Maintenance rule

When a task changes direction, update this file in the same commit as the doc or
implementation change that caused it.

A stale task DAG is just a roadmap wearing novelty glasses.
