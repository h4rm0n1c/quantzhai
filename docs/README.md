# QuantZhai documentation index

Start here when you want to understand the repo without reading every note in the tree.

## Current source-of-truth map

Read these first for current implementation authority:

1. [Current architecture authority map](current-architecture-authority.md)
2. [Current task hierarchy](current-task-hierarchy.md)
3. [Codex context and memory contract](codex-context-memory-contract.md)
4. [Model state signal contract](model-state-signal-contract.md)

The authority map tells agents which documents are current, which documents are historical inputs, and which stale assumptions must not be used for new work. The task hierarchy turns that authority map into the current execution order.

Current hard rules:

```text
Use memory_domain, not profile_family, for new code/docs.
Missing memory_domain means isolated.
Codex 0.130 provides session/thread/turn/window/workspace candidate signals.
QuantZhai owns qz_session_id, qz_turn_id, qz_request_id, workspace_id resolution, and memory_domain policy.
Capability detection from tools must not grant durable memory access.
QuantZhai-owned qz_* context must not be injected into forwarded /v1/responses request bodies.
Phase 1 SQLite stores operational facts only.
No clever memory, active memory tools, learned preferences, roleplay memory, HSM/archive memory, automatic promotion, or cross-domain sharing in Phase 1.
```

## Recommended reading path

1. [Project README](../README.md) — what QuantZhai is, how to start it, what ships, and the known-good local setup.
2. [Agent instructions](../AGENTS.md) — rules for agents working inside this repo.
3. [Current architecture authority map](current-architecture-authority.md) — current source-of-truth routing for docs, stale assumptions, and Phase 1 SQLite boundaries.
4. [Current task hierarchy](current-task-hierarchy.md) — current blocker/task DAG and implementation prompts.
5. [Codex context and memory contract](codex-context-memory-contract.md) — source-grounded v2 contract for Codex 0.130 identity, workspace candidates, thread/turn scope, and QuantZhai memory domains.
6. [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) — live request/header/body evidence from Codex 0.130.
7. [Model state signal contract](model-state-signal-contract.md) — LimbiCore state/signal/memory envelope; store scoped records, render narrowly later.
8. [Responses stream and tool state contract](responses-stream-tool-state-contract.md) — runtime contract for streamed Responses events, tool-call state, telemetry, and captures.
9. [Master stabilisation plan](master-stabilisation-plan.md) — broader stabilisation map; current authority and task hierarchy win for state/memory terms.
10. [Progress snapshot](progress-snapshot.md) — short overall percentage/status view.
11. [Codex/QuantZhai bidirectional signal map](codex-quantzhai-bidirectional-signal-map.md) — complete source-grounded map of signals in all directions, safety matrix, gaps, and next targets.
12. [Repeated-read signal plan](repeated-read-dedup-plan.md) — approved v1 plan for advisory repeated file-read signals.
12. [Benchmark findings: effort tuning](benchmark-findings-effort-tuning.md) — measured profile/tool-use behaviour and open questions.
13. [Edge case and config contract plan](edge-case-config-contract-plan.md) — audit/refactor plan for config layout, compact errors, profile safety, and script sprawl.
14. [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) — focused TODO/review plan for `/status`, `qz-top`, `qz-thoughts`, profiles, and streaming.
15. [Runtime observability notes](runtime-observability-notes.md) — how to inspect live proxy/model behaviour.

## Documentation by area

| Area | Document | Use it for |
| --- | --- | --- |
| Current authority | [Current architecture authority map](current-architecture-authority.md) | Final conflict resolver for current architecture, stale assumptions, and Phase 1 SQLite boundary. |
| Current execution | [Current task hierarchy](current-task-hierarchy.md) | Active blocker/task order, resource plan, and first implementation prompts. |
| Project overview | [README](../README.md) | Main setup, architecture, quick start, configuration, troubleshooting, and repo hygiene. |
| Agent workflow | [AGENTS](../AGENTS.md) | Instructions for Codex/agent contributors working in this tree. |
| Master plan | [Master stabilisation plan](master-stabilisation-plan.md) | Broader stabilisation work map. Use current-authority and Codex context docs for state/memory terminology. |
| Progress | [Progress snapshot](progress-snapshot.md) | Short overall percentage/status view for periodic project check-ins. |
| Runtime contract | [Responses stream and tool state contract](responses-stream-tool-state-contract.md) | State contract for streamed Responses events, tool calls, telemetry, and captures. |
| Codex contract | [Codex context and memory contract](codex-context-memory-contract.md) | Authoritative v2 contract for Codex 0.130 session/thread/turn/window/workspace metadata, SQLite scope direction, and memory_domain terminology. |
| Codex evidence | [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) | Live capture evidence for Codex 0.130 request/header/body/turn/workspace signals. |
| Runtime contract | [Codex native first request capture](codex-native-request-capture.md) | Raw first request shape Codex CLI sends before QuantZhai wrapper/proxy normalization. |
| LimbiCore contract | [Model state signal contract](model-state-signal-contract.md) | Future-facing state/signal/memory envelope; no clever memory in Phase 1. |
| Signal surface map | [Codex/QuantZhai bidirectional signal map](codex-quantzhai-bidirectional-signal-map.md) | Source-grounded map of all signals: Codex→QZ, QZ→model, backend→QZ, QZ→monitor, safety matrix, gaps, and next targets. |
| Feedback subsystem plan | [Signal/feedback subsystem plan](signal-feedback-subsystem-plan.md) | Phased plan for unifying tool coercion, native tool-output classifiers, and runtime signals into qz_feedback.py. |
| Backend control plane | [Backend control plane audit](backend-control-plane-audit.md) | Audit of llama.cpp/script/proxy data flow, readiness gap, ownership table, and safe next steps. |
| Backend service recovery | [Backend service recovery semantics](backend-service-recovery-semantics.md) | Status taxonomy, recovery classification matrix, HTTP behaviour map, canonical enum proposals, and next slices for #47. |
| Manual recovery policy | [Backend manual recovery endpoint policy](backend-manual-recovery-endpoint-policy.md) | Design policy for future `/qz/recovery/plan` and `/qz/recovery/trigger` endpoints: allowed actions, forbidden actions, backoff schedule, authority flags, active request safety, telemetry events, HTTP semantics, and implementation slices 6–10. Slice 6 done: pure `build_recovery_plan()` planner with 75 tests. |
| Repeated-read signal | [Repeated-read signal plan](repeated-read-dedup-plan.md) | Advisory repeated-read v1 plan and v2 scope blockers. |
| LLM signal system | [LLM signal system](llm-signal-system.md) | Hop budget, context pressure, compaction, and signal design notes. |
| Benchmarking | [Benchmark findings: effort tuning](benchmark-findings-effort-tuning.md) | Measured profile/tool-use behaviour and open questions around open-ended repo exploration. |
| Config and error handling | [Edge case and config contract plan](edge-case-config-contract-plan.md) | Audit/refactor plan for edge cases, compact errors, profile safety, config layering, script sprawl, and the profile-bundle design (qz.profiles.v1, profiles/*.json, memory_domain plumbing). |
| Current bugfix focus | [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) | Triage, review plan, proposed fixes, and acceptance checks for `/status`, monitor tools, profile tuning, and proxy streaming. |
| Deferred client/control UX | [qz-codex control plane future plan](qz-codex-control-plane-future.md) | Parked future seam for qz-codex fork/wrapper, /qz control plane, remote single-user mode, nginx/auth, and model/profile loading UX. |
| Memory architecture | [State and memory architecture plan](state-and-memory-architecture-plan.md) | Older typed-memory plan; useful taxonomy, superseded for Codex identity/workspace/domain decisions. |
| Fixed bug / regression guard | [Stale profile symlink bug](bugs/stale-profile-server-alias.md) | Symlink profile contract, compact invalid-profile errors, and qz-doctor regression checks. |
| Known bug | [Responses streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md) | Historical/audit plan for Responses SSE forwarding, summary transformation, and noisy live thought rendering. |
| Known bug | [Zombie model slot bug](bugs/zombie-model-slot.md) | Model slot/control-plane related parked issue. |
| Compact profiles | [Caveman Codex model instructions v2](qz-caveman-codex-model-instructions-v2.md) | Historical compact Codex prompt/profile instructions; current profiles use `config/default/prompts/caveman-mode.md`. |
| Compact profiles | [QuantZhai caveman profile](quantzhai-caveman-profile.md) | Notes and design intent for the caveman/compact profile. |
| Config examples | [Prompt compiler example](../config/example/prompt-compiler.md) | Example symlink-profile shape, override layering, and backend-target contract for a Codex-visible profile. |
| Benchmarking | [QuantZhai benchmark harness](quantzhai-benchmark-harness.md) | Running fixed prompts, collecting artifacts, and comparing profile compression/results. |
| Runtime debugging | [Runtime observability notes](runtime-observability-notes.md) | Captures, logs, thoughts stream, telemetry, and runtime inspection. |
| Search | [Search roadmap](search-roadmap.md) | Planned search capabilities, routing, and local SearXNG policy direction. |
| Search | [Profiled web search pickup README](profiled-web-search-pickup-README.md) | Pickup notes for the profiled web-search implementation/policy work. |
| Research | [Deep research report](deep-research-report.md) | Longer-form research/background notes relevant to QuantZhai direction. |
| Tooling roadmap | [Patch tool roadmap](patch-tool-roadmap.md) | Patch/edit tooling plans for safer repo modification. |
| Proxy roadmap | [Proxy capability roadmap](proxy-capability-roadmap.md) | Proxy feature expansion and compatibility work. |
| Conversation history | [Conversation history audit plan](conversation-history-audit-plan.md) | Plan for auditing/using conversation history safely. |
| Signal inventory | [Codex request signal inventory](codex-request-signal-inventory.md) | Historical signal checklist; use current parser/tests for implementation state. |

## Task-oriented entry points

### I want to run QuantZhai

Read:

```text
README.md quick start
README.md configuration
README.md troubleshooting
```

Useful scripts:

```bash
scripts/qz-doctor
scripts/qz-up
scripts/qz-live-smoke    # validate live stack after startup
scripts/qz-smoke-repeated-read --model MODEL   # validate repeated-read v1 signal end-to-end
scripts/qz-codex high
scripts/qz-down
```

### I want to know what to work on next

Read:

```text
docs/current-architecture-authority.md
docs/current-task-hierarchy.md
docs/codex-context-memory-contract.md
docs/progress-snapshot.md
```

Current next engineering target:

```text
P1 optional/non-fatal Phase 1 SQLite operational substrate.
```

Then:

```text
P2 repeated-read v1 advisory signal.
P3 telemetry filter ergonomics / qz-live-smoke refinements.
```

### I want to work on SQLite/state/memory

Read:

```text
docs/current-architecture-authority.md
docs/current-task-hierarchy.md
docs/codex-context-memory-contract.md
docs/codex-quantzhai-bidirectional-signal-map.md
docs/model-state-signal-contract.md
docs/codex-0130-live-signal-capture.md
```

Focus:

```text
Phase 1 stores identity, turns, requests, workspace candidates, resolved workspace bindings, and operational facts.
Do not implement learned preferences, profile-private memory, HSM/archive memory, recall, renderers, or promotion in Phase 1.
Do not store giant raw request bodies in SQLite by default.
Raw captures remain debug artifacts.
Do not inject qz_session_id/qz_workspace_id/qz_memory_domain/qz_text_verbosity into forwarded request bodies.
If a future LimbiCore renderer may use a fact, store enough scope/provenance/visibility metadata to keep it safe later.
```

### I want to work on repeated-read signals

Read:

```text
docs/current-task-hierarchy.md
docs/repeated-read-dedup-plan.md
docs/benchmark-findings-effort-tuning.md
docs/codex-context-memory-contract.md
```

Focus:

```text
V1 is advisory, stateless, and input-history-seeded.
V1 does not require SQLite.
V2 is blocked on same-scope SQLite facts using qz_session_id, qz_turn_id/codex_turn_id, qz_request_id, workspace_id, and memory_domain.
```

### I want to work on edge cases, config layout, or profile safety

Read:

```text
docs/current-task-hierarchy.md
docs/edge-case-config-contract-plan.md
docs/master-stabilisation-plan.md
docs/current-architecture-authority.md
```

Focus:

```text
Audit before refactor.
Do not move model files, profile symlinks, or Codex-visible slugs casually.
Generated Codex metadata is a view of proxy policy, not routing authority.
Do not add new one-off shell scripts unless there is a strong reason.
qz.profiles.v1 is the active config format — memory_domain plumbing and
profiles/*.json loader are already implemented. Next work is /qz/models/refresh
regenerating the Codex catalog, then broader var/generated/ cleanup.
See edge-case-config-contract-plan.md for the remaining items.
```

### I want to fix streaming, qz-top, or qz-thoughts

Read:

```text
docs/observability-streaming-bugfix-agenda.md
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
docs/master-stabilisation-plan.md
```

Focus:

```text
Proxy observes facts.
Proxy owns structured state/events.
Monitor tools render read-only views.
Files/logs are replay/debug fallback, not live truth.
```

### I want to compare prompt/profile performance

Read:

```text
docs/quantzhai-benchmark-harness.md
docs/benchmark-findings-effort-tuning.md
docs/qz-caveman-codex-model-instructions-v2.md
docs/quantzhai-caveman-profile.md
```

Useful scripts:

```bash
scripts/qz-up
scripts/qz-benchmark high caveman
scripts/qz-top
```

### I want to work on local web search

Read:

```text
docs/search-roadmap.md
docs/profiled-web-search-pickup-README.md
README.md local search section
```

Useful config:

```text
SEARXNG_BASE_URL
SEARXNG_POLICY
config/default/search-policy.json
```

### I want to revisit future qz-codex/client UX

Read:

```text
docs/qz-codex-control-plane-future.md
docs/runtime-observability-notes.md
docs/bugs/zombie-model-slot.md
docs/proxy-capability-roadmap.md
```

Focus:

```text
This is parked future work.
Keep /v1/responses upstream-compatible.
Use /qz/* as a QuantZhai control plane.
A qz-codex fork/wrapper should be a thin overlay, not a divergent client.
Do SQLite, runtime correctness, and script/data-path cleanup first.
```

## Current doc inventory

```text
README.md
AGENTS.md
docs/README.md
docs/current-architecture-authority.md
docs/current-task-hierarchy.md
docs/codex-quantzhai-bidirectional-signal-map.md
docs/signal-feedback-subsystem-plan.md
docs/backend-control-plane-audit.md
docs/backend-service-recovery-semantics.md
docs/backend-manual-recovery-endpoint-policy.md
docs/model-state-signal-contract.md
docs/qz-codex-control-plane-future.md
docs/bugs/responses-streaming-and-qz-thoughts.md
docs/bugs/stale-profile-server-alias.md
docs/bugs/zombie-model-slot.md
docs/benchmark-findings-effort-tuning.md
docs/codex-0130-live-signal-capture.md
docs/codex-context-memory-contract.md
docs/codex-native-first-request-capture.md
docs/codex-request-signal-inventory.md
docs/conversation-history-audit-plan.md
docs/deep-research-report.md
docs/edge-case-config-contract-plan.md
docs/llm-signal-system.md
docs/master-stabilisation-plan.md
docs/observability-streaming-bugfix-agenda.md
docs/patch-tool-roadmap.md
docs/profiled-web-search-pickup-README.md
docs/proxy-capability-roadmap.md
docs/quantzhai-benchmark-harness.md
docs/quantzhai-caveman-profile.md
docs/qz-caveman-codex-model-instructions-v2.md
docs/repeated-read-dedup-plan.md
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
docs/search-roadmap.md
docs/state-and-memory-architecture-plan.md
```

## Maintenance rule

When adding a new Markdown document, add it to this index in the same commit.

A document that cannot be found is just a very small archaeological site.
