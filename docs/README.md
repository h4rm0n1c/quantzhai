# QuantZhai documentation index

Start here when you want to understand the repo without reading every note in the tree.

## Current source-of-truth map

Read this first for current implementation authority:

- [Current architecture authority map](current-architecture-authority.md)

That map tells agents which documents are current, which documents are historical inputs, and which stale assumptions must not be used for new work.

Current hard rules:

```text
Use memory_domain, not profile_family, for new code/docs.
Missing memory_domain means isolated.
Codex 0.130 provides session/thread/turn/window/workspace candidate signals.
QuantZhai owns qz_session_id, qz_turn_id, qz_request_id, workspace_id resolution, and memory_domain policy.
Capability detection from tools must not grant durable memory access.
QuantZhai-owned qz_* context must not be injected into forwarded /v1/responses request bodies.
```

## Recommended reading path

1. [Project README](../README.md) — what QuantZhai is, how to start it, what ships, and the known-good local setup.
2. [Agent instructions](../AGENTS.md) — rules for agents working inside this repo.
3. [Current architecture authority map](current-architecture-authority.md) — current source-of-truth routing for docs, stale assumptions, and Phase 1 SQLite boundaries.
4. [Codex context and memory contract](codex-context-memory-contract.md) — source-grounded v2 contract for Codex 0.130 identity, workspace candidates, thread/turn scope, and QuantZhai memory domains.
5. [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) — live request/header/body evidence from Codex 0.130.
6. [Responses stream and tool state contract](responses-stream-tool-state-contract.md) — current runtime contract for streamed Responses events, tool-call state, telemetry, and captures.
7. [Master stabilisation plan](master-stabilisation-plan.md) — controlling map for stabilisation work, with the authority map and Codex context contract taking precedence for state/memory terminology.
8. [Progress snapshot](progress-snapshot.md) — short overall percentage/status view.
9. [State and memory architecture plan](state-and-memory-architecture-plan.md) — older typed-memory plan; useful for memory classes, but superseded by the Codex context contract for Codex identity/workspace/domain terminology.
10. [Edge case and config contract plan](edge-case-config-contract-plan.md) — planned audit/refactor for edge cases, compact errors, profile safety, config layout, and script-sprawl reduction.
11. [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) — focused TODO/review plan for `/status`, `qz-top`, `qz-thoughts`, profiles, and streaming.
12. [Runtime observability notes](runtime-observability-notes.md) — how to inspect live proxy/model behaviour.

## Documentation by area

| Area | Document | Use it for |
| --- | --- | --- |
| Current authority | [Current architecture authority map](current-architecture-authority.md) | First stop before implementation. Current source-of-truth map, stale assumption replacements, and SQLite Phase 1 boundary. |
| Project overview | [README](../README.md) | Main setup, architecture, quick start, configuration, troubleshooting, and repo hygiene. |
| Agent workflow | [AGENTS](../AGENTS.md) | Instructions for Codex/agent contributors working in this tree. |
| Master plan | [Master stabilisation plan](master-stabilisation-plan.md) | Stabilisation work map. Use current-authority and Codex context docs for state/memory terminology. |
| Progress | [Progress snapshot](progress-snapshot.md) | Short overall percentage/status view for periodic project check-ins. |
| Runtime contract | [Responses stream and tool state contract](responses-stream-tool-state-contract.md) | State contract for streamed Responses events, tool calls, telemetry, and captures. |
| Codex contract | [Codex context and memory contract](codex-context-memory-contract.md) | Authoritative v2 contract for Codex 0.130 session/thread/turn/window/workspace metadata, SQLite scope direction, and `memory_domain` terminology. |
| Codex evidence | [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) | Live capture evidence for Codex 0.130 request/header/body/turn/workspace signals. Historical sections may contain later-corrected implementation status; see the current-status note. |
| Runtime contract | [Codex native first request capture](codex-native-request-capture.md) | Clean local reference for the raw first request shape Codex CLI sends before QuantZhai wrapper/proxy normalization. |
| Memory architecture | [State and memory architecture plan](state-and-memory-architecture-plan.md) | Typed-memory classes, storage roles, and old planning context. Superseded for Codex identity/workspace/domain decisions. |
| Config and error handling | [Edge case and config contract plan](edge-case-config-contract-plan.md) | Audit/refactor plan for edge cases, compact errors, profile safety, config layering, and reducing script sprawl. |
| Current bugfix focus | [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) | Triage, review plan, proposed fixes, and acceptance checks for `/status`, monitor tools, profile tuning, and proxy streaming. |
| Fixed bug / regression guard | [Stale profile symlink bug](bugs/stale-profile-server-alias.md) | Symlink profile contract, compact invalid-profile errors, and `qz-doctor` regression checks. |
| Known bug | [Responses streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md) | Audit plan for Responses SSE forwarding, summary transformation, and noisy live thought rendering. |
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

## Task-oriented entry points

### I want to run QuantZhai

Read:

- [README: Quick Start](../README.md#quick-start)
- [README: Configuration](../README.md#configuration)
- [README: Troubleshooting](../README.md#troubleshooting)

Useful scripts:

```bash
scripts/qz-doctor
scripts/qz-up
scripts/qz-codex high
scripts/qz-down
```

### I want to understand Codex identity, workspace, and memory scope

Read:

- [Current architecture authority map](current-architecture-authority.md)
- [Codex context and memory contract](codex-context-memory-contract.md)
- [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md)
- [Codex native first request capture](codex-native-request-capture.md)
- [State and memory architecture plan](state-and-memory-architecture-plan.md)

Focus:

```text
Codex 0.130 provides session_id, thread_id, turn_id, window id, installation id, prompt_cache_key, and workspace candidates.
QuantZhai owns qz_session_id, qz_turn_id, qz_request_id, workspace_id resolution, and memory_domain policy.
Use memory_domain, not profile_family, for new code/docs.
Missing memory_domain means isolated.
Capability detection from tools must not grant durable memory access.
```

### I want to work on SQLite/state/memory

Read:

- [Current architecture authority map](current-architecture-authority.md)
- [Codex context and memory contract](codex-context-memory-contract.md)
- [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md)
- [State and memory architecture plan](state-and-memory-architecture-plan.md)
- [Runtime observability notes](runtime-observability-notes.md)

Focus:

```text
Phase 1 stores identity, turns, requests, workspace candidates, resolved workspace bindings, and operational facts.
Do not implement learned preferences, profile-private memory, HSM/archive memory, or promotion in Phase 1.
Do not store giant raw request bodies in SQLite by default.
Raw captures remain debug artifacts.
Do not inject qz_session_id/qz_workspace_id/qz_memory_domain/qz_text_verbosity into forwarded request bodies.
```

### I want to understand what needs fixing next

Read:

- [Current architecture authority map](current-architecture-authority.md)
- [Master stabilisation plan](master-stabilisation-plan.md)
- [Codex context and memory contract](codex-context-memory-contract.md)
- [Edge case and config contract plan](edge-case-config-contract-plan.md)
- [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md)
- [Known bug notes](bugs/)

Focus:

```text
Stale symlink profile validation, compact errors, and doctor checks are done.
qz-thoughts delta coalescing is done.
Stream timing telemetry is done.
Summary-mode SSE transform and missing DONE marker are fixed and live-smoked.
Codex 0.130 identity/workspace parsing is source/capture grounded.
Next state/memory work is Phase 1 SQLite substrate, optional/non-fatal and parser-boundary only.
```

### I want to work on edge cases, config layout, or profile safety

Read:

- [Current architecture authority map](current-architecture-authority.md)
- [Master stabilisation plan](master-stabilisation-plan.md)
- [Codex context and memory contract](codex-context-memory-contract.md)
- [Edge case and config contract plan](edge-case-config-contract-plan.md)
- [Stale profile symlink bug](bugs/stale-profile-server-alias.md)
- [Runtime observability notes](runtime-observability-notes.md)

Focus:

```text
Audit before refactor.
Use memory_domain as explicit config for memory boundaries.
Missing memory_domain must resolve to isolated.
Do not infer memory authority from tools, client names, profile names, or model names.
Do not add new one-off shell scripts unless there is a strong reason.
```

### I want to fix streaming or qz-thoughts

Read:

- [Current architecture authority map](current-architecture-authority.md)
- [Master stabilisation plan](master-stabilisation-plan.md)
- [Responses stream and tool state contract](responses-stream-tool-state-contract.md)
- [Codex context and memory contract](codex-context-memory-contract.md)
- [Codex native first request capture](codex-native-request-capture.md)
- [Responses streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md)
- [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md)
- [Runtime observability notes](runtime-observability-notes.md)

Focus:

```text
Audit upstream SSE, transformed SSE, telemetry, qz-thoughts rendering, and Codex-visible behaviour before patching.
Never expose runnable tool calls before arguments are complete.
Do not treat every tiny delta as a human activity event.
Do not add another one-off shell monitor.
```

### I want to compare prompt/profile performance

Read:

- [Benchmark harness](quantzhai-benchmark-harness.md)
- [Caveman Codex model instructions v2](qz-caveman-codex-model-instructions-v2.md)
- [QuantZhai caveman profile](quantzhai-caveman-profile.md)

Useful scripts:

```bash
scripts/qz-up
scripts/qz-benchmark high caveman
scripts/qz-top
```

### I want to debug what the proxy/model is doing

Read:

- [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md)
- [Runtime observability notes](runtime-observability-notes.md)
- [README: Troubleshooting](../README.md#troubleshooting)

Useful paths:

```text
var/logs/qz-proxy.log
var/captures/latest-request.json
var/captures/latest-forwarded.json
var/captures/latest-json-api.log
```

### I want to work on local web search

Read:

- [Search roadmap](search-roadmap.md)
- [Profiled web search pickup README](profiled-web-search-pickup-README.md)
- [README: Local Search](../README.md#local-search)

Useful config:

```text
SEARXNG_BASE_URL
SEARXNG_POLICY
config/default/search-policy.json
```

### I want to improve agent editing/tooling

Read:

- [AGENTS](../AGENTS.md)
- [Patch tool roadmap](patch-tool-roadmap.md)
- [Proxy capability roadmap](proxy-capability-roadmap.md)

## Current doc inventory

```text
README.md
AGENTS.md
docs/README.md
docs/current-architecture-authority.md
docs/bugs/responses-streaming-and-qz-thoughts.md
docs/bugs/stale-profile-server-alias.md
docs/codex-0130-live-signal-capture.md
docs/codex-context-memory-contract.md
docs/codex-native-request-capture.md
docs/deep-research-report.md
docs/edge-case-config-contract-plan.md
docs/master-stabilisation-plan.md
docs/observability-streaming-bugfix-agenda.md
docs/patch-tool-roadmap.md
docs/profiled-web-search-pickup-README.md
docs/proxy-capability-roadmap.md
docs/quantzhai-benchmark-harness.md
docs/quantzhai-caveman-profile.md
docs/qz-caveman-codex-model-instructions-v2.md
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
docs/search-roadmap.md
docs/state-and-memory-architecture-plan.md
```

## Maintenance rule

When adding a new Markdown document, add it to this index in the same commit. A document that cannot be found is just a very small archaeological site.
