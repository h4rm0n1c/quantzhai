# QuantZhai documentation index

Start here when you want to understand the repo without spelunking through every note like a caffeinated truffle pig.

## Recommended reading path

1. [Project README](../README.md) — what QuantZhai is, how to start it, what ships, and the known-good local setup.
2. [Agent instructions](../AGENTS.md) — rules for agents working inside this repo.
3. [Benchmark harness](quantzhai-benchmark-harness.md) — how to compare profiles and prove whether changes help.
4. [Runtime observability notes](runtime-observability-notes.md) — how to inspect live proxy/model behaviour.
5. [Search roadmap](search-roadmap.md) — local web-search routing plan and policy direction.

## Documentation by area

| Area | Document | Use it for |
| --- | --- | --- |
| Project overview | [README](../README.md) | Main setup, architecture, quick start, configuration, troubleshooting, and repo hygiene. |
| Agent workflow | [AGENTS](../AGENTS.md) | Instructions for Codex/agent contributors working in this tree. |
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
docs/searxng-agent-policy-profiled.json
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
docs/deep-research-report.md
docs/patch-tool-roadmap.md
docs/profiled-web-search-pickup-README.md
docs/proxy-capability-roadmap.md
docs/quantzhai-benchmark-harness.md
docs/quantzhai-caveman-profile.md
docs/qz-caveman-codex-model-instructions-v2.md
docs/runtime-observability-notes.md
docs/search-roadmap.md
```

## Maintenance rule

When adding a new Markdown document, add it to this index in the same commit. A document that cannot be found is just a very small archaeological site.
