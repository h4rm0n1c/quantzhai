# Current Architecture Authority Map

Date: 2026-05-12

Status: current source-of-truth map before Phase 1 SQLite work.

This document does not replace the detailed planning documents. It tells agents
which documents are authoritative now, which documents are historical inputs,
and which assumptions must not be used for new implementation.

---

## Current source of truth

Use these documents for current implementation decisions:

| Area | Current authority |
| --- | --- |
| Codex identity/thread/turn/window/workspace/memory-domain contract | `docs/codex-context-memory-contract.md` |
| LimbiCore model state/signal/memory envelope | `docs/model-state-signal-contract.md` |
| Live Codex 0.130 evidence | `docs/codex-0130-live-signal-capture.md` |
| Parser boundary | `proxy/qz_codex_metadata.py` and its tests |
| Request-body ownership / no internal qz metadata injection | `tests/test_qz_request_mutation_regression.py` and commit `0d7ae3b7dd2869cf9c9819464c4cceeb4adddbd1` |
| Responses stream/tool lifecycle | `docs/responses-stream-tool-state-contract.md` |
| Current stabilisation order | `docs/master-stabilisation-plan.md`, with this map and the Codex contract taking precedence for state/memory terms |
| Repeated-read v2 scope policy | `docs/codex-context-memory-contract.md` plus `docs/repeated-read-dedup-plan.md`; if stale terms conflict, use `memory_domain` + `workspace_id` from the Codex context contract |

---

## LimbiCore state/signal rule

For state or memory work above the Phase 1 SQLite substrate, use the LimbiCore
contract:

```text
LimbiCore stores scoped StateRecords with provenance, then renders small purpose-specific packets or recall results to models.
```

Current rule:

```text
SQLite stores operational facts.
Renderers decide model-facing packets.
Storage records are not automatically model-facing memory.
Recall results are not automatically model-facing memory.
No clever memory, active memory tools, or model-visible durable memory in Phase 1.
```

---

## Superseded language

Do not use these assumptions for new code or schema design:

```text
profile_family
profile_family or equivalent privacy class
workspace not derivable proxy-side
client_thread_id absent
synthetic sessions only
extract_codex_body_metadata planned/not implemented
extract_codex_request_context planned/not implemented
parse_codex_window_id planned/not implemented
QuantZhai-owned qz_* context injected into forwarded request bodies
```

Use these replacements:

| Old language | Current replacement |
| --- | --- |
| `profile_family` | `memory_domain` |
| `profile_family or equivalent privacy class` | explicit `memory_domain` plus `workspace_id` scope; no inference from profile/model/tool names |
| workspace not derivable | Codex provides workspace candidates; QuantZhai resolves `workspace_id` internally |
| client_thread_id absent | Codex 0.130 sends `thread_id`; older 0.125 captures did not |
| synthetic sessions only | QuantZhai owns `qz_session_id`, mapped to external Codex `session_id`/`thread_id` when present |
| parser functions planned | parser functions are implemented and tested |
| qz context injected into body metadata | removed; internal context must remain internal unless a future explicit feature changes that |

---

## Current implemented parser/helper surface

`proxy/qz_codex_metadata.py` currently provides:

```text
header_lookup
parse_codex_window_id
parse_codex_turn_metadata_header
extract_workspace_candidates
resolve_workspace_id
extract_codex_identity
extract_codex_body_metadata
resolve_memory_domain
generate_qz_session_id
extract_codex_request_context
```

Important behaviour:

```text
memory_domain defaults to isolated.
No memory_domain is inferred from client/model/profile/tool names.
workspace_id is derived internally from explicit config or Codex workspace evidence.
qz_session_id is QuantZhai-owned and internal.
The forwarded /v1/responses request body must not gain qz_session_id, qz_workspace_id, qz_memory_domain, or qz_text_verbosity.
```

---

## Historical documents and how to read them

Historical docs still matter as evidence. Do not delete them merely because some
facts changed later.

| Document | Current reading rule |
| --- | --- |
| `docs/state-and-memory-architecture-plan.md` | Useful for typed-memory taxonomy and DB motivation. Superseded by `codex-context-memory-contract.md` for Codex identity, workspace, and memory-domain policy. |
| `docs/model-state-signal-contract.md` | Current LimbiCore envelope for future model-facing packets, recall, utility LLM jobs, and active memory tool direction. Does not change Phase 1 SQLite scope. |
| `docs/state-and-memory-architecture-review-deepseek.md` | Historical review input. Do not treat stale `profile_family` or missing-thread assumptions as implementation authority. |
| `docs/state-and-memory-architecture-codex-metadata-delta.md` | Historical bridge between early header capture and the current contract. Useful context, not the final authority. |
| `docs/codex-client-header-metadata-audit.md` | Historical source audit. Superseded by later live Codex 0.130 evidence where they differ. |
| `docs/codex-request-signal-inventory.md` | Historical signal inventory. Useful checklist; use current parser/tests for implementation state. |
| `docs/codex-header-capture-verdict.md` | Historical 0.125-era capture verdict. Important because it explains why `thread_id` must remain nullable/backward-compatible. |
| `docs/codex-0130-live-signal-capture.md` | Current live evidence after status update. If older wording inside it conflicts with parser implementation, the current-status note wins. |
| `docs/repeated-read-dedup-plan.md` | Current v1 repeated-read design and v2 scope motivation. Any older `profile_family` wording inside it is superseded: v2 persistence must use explicit `memory_domain`, `workspace_id`, and same-scope file read/write/signal facts from the Codex context contract. |

---

## Repeated-read v2 scope rule

Repeated-read v2 must not use a generic profile privacy class.

Current required scope inputs:

```text
qz_session_id
qz_turn_id / codex_turn_id
qz_request_id
workspace_id
memory_domain
same-scope file_reads / file_writes / signals
```

Rules:

```text
memory_domain is explicit config only.
Missing memory_domain means isolated.
Do not infer durable memory authority from model name, profile name, client name, originator, user-agent, or tools list.
Do not share roleplay/private/HSM facts into coding memory.
Do not share coding workspace facts into roleplay/private/HSM memory.
```

---

## SQLite Phase 1 boundary

Phase 1 SQLite should store structured operational facts only:

```text
sessions
turns
requests
workspace_candidates
resolved_workspaces
session_workspace_bindings
identity conflicts
request/body metadata summaries
```

Phase 1 must not implement:

```text
model-visible durable memory
learned global preferences
roleplay/profile-private memory
HSM/archive memory
automatic promotion
cross-domain sharing
repeated-read v2 behaviour changes
forwarded qz_* request-body metadata injection
```

Recommended next implementation step:

```text
Phase 1 SQLite substrate, optional/non-fatal, parser-boundary only.
```

The DB should consume `extract_codex_request_context()` internally. It should not
modify the forwarded request body.

---

## Required pre-flight before SQLite agent work

Before handing implementation to an agent, verify:

```text
python3 -m pytest tests/test_qz_codex_metadata.py
python3 -m pytest tests/test_qz_codex_request_metadata.py
python3 -m pytest tests/test_qz_request_mutation_regression.py
python3 -m pytest tests/test_qz_runtime_io.py
```

Recent full-system checkpoint:

```text
focused suite: 165 passed
full suite: 377 passed
live Codex smoke: passed
/health: stable
forbidden qz context metadata in recent captures: not found
```

---

## Maintenance rule

When a new planning/audit doc changes implementation direction, update this map
and `docs/README.md` in the same commit.

A stale doc with no supersession marker is how goblins get schema ownership.
