# QuantZhai documentation index

Start here when you want to understand the repo without reading every note in the tree.

## Recommended reading path

1. [Project README](../README.md) — what QuantZhai is, how to start it, what ships, and the known-good local setup.
2. [Agent instructions](../AGENTS.md) — rules for agents working inside this repo.
3. [Master stabilisation plan](master-stabilisation-plan.md) — controlling map for the current stabilisation work, bug relationships, and fix order.
4. [Responses stream and tool state contract](responses-stream-tool-state-contract.md) — current runtime contract for streamed Responses events, tool-call state, telemetry, and captures.
5. [Edge case and config contract plan](edge-case-config-contract-plan.md) — planned audit/refactor for edge cases, compact errors, profile safety, config layout, and script-sprawl reduction.
6. [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) — current focused TODO/review plan for `/status`, `qz-top`, `qz-thoughts`, profiles, and streaming.
7. [Benchmark harness](quantzhai-benchmark-harness.md) — how to compare profiles and prove whether changes help.
8. [Runtime observability notes](runtime-observability-notes.md) — how to inspect live proxy/model behaviour.
9. [Search roadmap](search-roadmap.md) — local web-search routing plan and policy direction.

## Documentation by area

| Area | Document | Use it for |
| --- | --- | --- |
| Project overview | [README](../README.md) | Main setup, architecture, quick start, configuration, troubleshooting, and repo hygiene. |
| Agent workflow | [AGENTS](../AGENTS.md) | Instructions for Codex/agent contributors working in this tree. |
| Master plan | [Master stabilisation plan](master-stabilisation-plan.md) | Controlling map for current bugs, contracts, dependencies, and fix order. |
| Runtime contract | [Responses stream and tool state contract](responses-stream-tool-state-contract.md) | State contract for streamed Responses events, tool calls, telemetry, and captures. |
| Config and error handling | [Edge case and config contract plan](edge-case-config-contract-plan.md) | Audit/refactor plan for edge cases, compact errors, profile safety, config layering, and reducing script sprawl. |
| Current bugfix focus | [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) | Triage, review plan, proposed fixes, and acceptance checks for `/status`, monitor tools, profile tuning, and proxy streaming. |
| Fixed bug / regression guard | [Stale profile symlink bug](bugs/stale-profile-server-alias.md) | Symlink profile contract, compact invalid-profile errors, and `qz-doctor` regression checks. |
| Known bug | [Responses streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md) | Audit plan for Responses SSE forwarding, summary transformation, and noisy live thought rendering. |
| Compact profiles | [Caveman Codex model instructions v2](qz-caveman-codex-model-instructions-v2.md) | The compact Codex prompt/profile instructions used by `scripts/qz-codex caveman`. |
| Compact profiles | [QuantZhai caveman profile](quantzhai-caveman-profile.md) | Notes and design intent for the caveman/compact profile. |
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

### I want to understand what needs fixing next

Read:

- [Master stabilisation plan](master-stabilisation-plan.md)
- [Edge case and config contract plan](edge-case-config-contract-plan.md)
- [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md)
- [Known bug notes](bugs/)

Focus:

```text
Stale symlink profile validation, compact errors, and doctor checks are done.
qz-thoughts delta coalescing is done.
Stream timing telemetry is done.
Summary-mode SSE transform and missing DONE marker are fixed and live-smoked.
Next keep the Responses stream/tool contract current, add golden replay fixtures, then extract tool lifecycle handling.
Do not start broad config movement before auditing data paths.
```

### I want to work on edge cases, config layout, or profile safety

Read:

- [Master stabilisation plan](master-stabilisation-plan.md)
- [Edge case and config contract plan](edge-case-config-contract-plan.md)
- [Stale profile symlink bug](bugs/stale-profile-server-alias.md)
- [Runtime observability notes](runtime-observability-notes.md)

Focus:

```text
Audit before refactor.
Compact errors and invalid-profile handling are done; keep doctor checks green before broad config movement.
Do not add new one-off shell scripts unless there is a strong reason.
```

### I want to fix streaming or qz-thoughts

Read:

- [Master stabilisation plan](master-stabilisation-plan.md)
- [Responses stream and tool state contract](responses-stream-tool-state-contract.md)
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
docs/bugs/responses-streaming-and-qz-thoughts.md
docs/bugs/stale-profile-server-alias.md
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
```

## Maintenance rule

When adding a new Markdown document, add it to this index in the same commit. A document that cannot be found is just a very small archaeological site.
