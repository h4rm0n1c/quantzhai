# QuantZhai documentation index

Start here when you want to understand the repo without reading every note in the tree.

## Current source-of-truth map

Read these first for current implementation authority:

1. [Current architecture authority map](current-architecture-authority.md)
2. [Current task hierarchy](current-task-hierarchy.md)
3. [Current stocktake](current-stocktake.md) — point-in-time state summary, open issue classification, recommended next order
4. [Codex context and memory contract](codex-context-memory-contract.md)
5. [Model state signal contract](model-state-signal-contract.md)
6. [Foundation audit before SQLite](foundation-audit-before-sqlite.md) — pre-#2 gravity-well, signal-path, and feedback-routing audit

The authority map tells agents which documents are current, which documents are historical inputs, and which stale assumptions must not be used for new work. The task hierarchy turns that authority map into the current execution order. The stocktake gives a quick "where are we now" for agents starting fresh.

Current hard rules:

```text
Use memory_domain, not profile_family, for new code/docs.
Missing memory_domain means isolated.
Codex 0.130 provides session/thread/turn/window/workspace candidate signals.
QuantZhai owns qz_session_id, qz_turn_id, qz_request_id, workspace_id resolution, and memory_domain policy.
Capability detection from tools must not grant durable memory access.
QuantZhai-owned qz_* context must not be injected into forwarded /v1/responses request bodies.
BrainCaseDB is the low-level SQLite storage case. It is not a policy layer, telemetry warehouse, request log, or memory_domain registry.
The memory architecture is tool-mediated, not DB-first. See docs/braincase-memory-tool-api.md.
Do not add automatic ingestion. All BrainCaseDB write paths must be explicit.
No automatic promotion, cross-domain sharing, or model-visible memory by default.
```

## Recommended reading path

1. [Project README](../README.md) — what QuantZhai is, how to start it, what ships, and the known-good local setup.
2. [Agent instructions](../AGENTS.md) — rules for agents working inside this repo.
3. [Current architecture authority map](current-architecture-authority.md) — current source-of-truth routing for docs, stale assumptions, and Phase 1 SQLite boundaries.
4. [Current task hierarchy](current-task-hierarchy.md) — current blocker/task DAG and implementation prompts.
5. [Codex context and memory contract](codex-context-memory-contract.md) — source-grounded v2 contract for Codex 0.130 identity, workspace candidates, thread/turn scope, and QuantZhai memory domains.
6. [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) — live request/header/body evidence from Codex 0.130.
7. [Model state signal contract](model-state-signal-contract.md) — LimbiCore state/signal/memory envelope; store scoped records, render narrowly later.
7a. [Codex Plan mode live capture](codex-plan-mode-live-capture.md) — live request_user_input evidence showing normal-mode unavailability versus Plan-mode success.
8. [Responses stream and tool state contract](responses-stream-tool-state-contract.md) — runtime contract for streamed Responses events, tool-call state, telemetry, and captures.
8a. [Runtime streaming tool contract audit](runtime-streaming-tool-contract-audit.md) — Slice A contract map: pipeline, event shapes, metadata flow, coercion map, test plan, and findings.
8b. [Tool schema coercion audit](tool-schema-coercion-audit.md) — Slice B: schema replacement, coercion/advice paths, failure matrix, test coverage, and B2 fix plan.
8c. [Streaming event mapper audit](streaming-event-mapper-audit.md) — Slice C: mapper inventory, event boundaries, leak-risk audit, reasoning channel, tool continuation, fixture plan.
8d. [Metadata propagation audit](metadata-propagation-audit.md) — Slice D: metadata inventory, flow table, rewrite/synthesis audit, usage accounting, error shapes, gap classification.
8e. [Observability UI audit](observability-ui-audit.md) — Slice E: qz-thoughts/qz-top data sources, visibility tables, reconnect audit, misleading-state audit, token observability.
8f. [Search profile granularity audit](search-profile-granularity-audit.md) — Slice F: profile inventory, furry/FSE/SoFurry audit, engine availability, policy/suppression, capabilities output, retrieval accuracy.
8f2. [Web search provider architecture audit](web-search-provider-architecture-audit.md) — P0/P1: provider architecture map, fse_direct honest finding, count mismatch semantics, trace log fields, operator debugging commands.
8f3. [Search provider boundary audit](search-provider-boundary-audit.md) — QuantZhai/searchengines-private boundary: KEEP_GENERIC/KEEP_FALLBACK/MOVE_TO_PROVIDER_GUIDANCE/REMOVE_LEAK classification, /guidance schema, boundary rules.
8g. [End-to-end smoke plan](end-to-end-smoke-plan.md) — Slice G: 37-test smoke matrix, command blocks, expected outputs, failure classification, fix-pass ordering H–M, result template.
8h. [End-to-end smoke results](end-to-end-smoke-results.md) — Fix Pass M: automated smoke results (YELLOW). 14 PASS, 1 FAIL (stale proxy), 16 SKIP. Operator live run required for GREEN.
8i. [Codex-visible tool lifecycle audit](codex-visible-tool-lifecycle-audit.md) — Source-grounded audit of which SSE events reach Codex per tool-call path, suppression inventory, gaps, risks, and recommended implementation slices.
8j. [Codex source tool contract](codex-source-tool-contract.md) — **Authoritative.** Codex-source-confirmed SSE event allowlist, ResponseItem variants, apply_patch and web_search wire shapes, CODEX_NATIVE_TOOL_NAMES basis, forbidden event table. Supersedes fake lifecycle docs. (issue #66, 2026-05-25)
8k. [Codex source tool inventory](codex-source-tool-inventory.md) — Full tool-by-tool audit table: item type, payload kind, Codex source evidence, QuantZhai status, and deferred slices. Native pass-through set expanded to 10 tools. (issue #67, 2026-05-25)
8l. [Native tool advisory policy](native-tool-advisory-policy.md) — #61 Slice A design: tool target set, five candidate advisory patterns, NativeToolAdvisoryState model, thresholds, signature safety, implementation slices B/C/D. (2026-05-26)
9. [Master stabilisation plan](master-stabilisation-plan.md) — broader stabilisation map; current authority and task hierarchy win for state/memory terms.
10. [Progress snapshot](progress-snapshot.md) — short overall percentage/status view.
11. [Codex/QuantZhai bidirectional signal map](codex-quantzhai-bidirectional-signal-map.md) — complete source-grounded map of signals in all directions, safety matrix, gaps, and next targets.
12. [Foundation audit before SQLite](foundation-audit-before-sqlite.md) — current pre-#2 signal/data-path audit and gravity-well map.
13. [Repeated-read signal plan](repeated-read-dedup-plan.md) — approved v1 plan for advisory repeated file-read signals.
14. [Benchmark findings: effort tuning](benchmark-findings-effort-tuning.md) — measured profile/tool-use behaviour and open questions.
15. [Edge case and config contract plan](edge-case-config-contract-plan.md) — audit/refactor plan for config layout, compact errors, profile safety, and script sprawl.
16. [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) — focused TODO/review plan for `/status`, `qz-top`, `qz-thoughts`, profiles, and streaming.
17. [Runtime observability notes](runtime-observability-notes.md) — how to inspect live proxy/model behaviour.
18. [Compaction audit and strategy](compaction-audit-and-strategy.md) — source-grounded audit of Codex and QuantZhai compaction, issue #8 promoted ideas, anchored summary schema v0, recommended prompt v0, and staged implementation roadmap. Stage 6.
19. [Anchored compaction schema v0](compaction-anchored-schema-v0.md) — canonical schema spec: field rules, exact-atom preservation, evidence-to-decision retention, stale fact correction, uncertainty marking, non-goals, and versioning notes. Stage 4.
20. [Compaction Codex setup](compaction-codex-setup.md) — how to wire `experimental_compact_prompt_file`, use `compaction.json`, keep env overrides explicit, and run manual smoke checks. Stage 6.
21. [Compaction live dogfood](compaction-live-dogfood.md) — Stage 6 direct-backend smoke runbook, live evidence, observed v2 fallback, and v3 tuning blocker.
22. [Compaction fixtures](fixtures/compaction/) — Stage 1 example input/output pairs: fixture-01 (basic coding session), fixture-02 (tool-heavy session), fixture-03 (rejected approaches and negative constraints). Stage 4 compatible.

## Documentation by area

| Area | Document | Use it for |
| --- | --- | --- |
| Current authority | [Current architecture authority map](current-architecture-authority.md) | Final conflict resolver for current architecture, stale assumptions, and Phase 1 SQLite boundary. |
| Current execution | [Current task hierarchy](current-task-hierarchy.md) | Active blocker/task order, resource plan, and first implementation prompts. |
| Current stocktake | [Current stocktake](current-stocktake.md) | Point-in-time state summary, open issue classification, dependency map, recommended next work order. |
| Project overview | [README](../README.md) | Main setup, architecture, quick start, configuration, troubleshooting, and repo hygiene. |
| Agent workflow | [AGENTS](../AGENTS.md) | Instructions for Codex/agent contributors working in this tree. |
| Master plan | [Master stabilisation plan](master-stabilisation-plan.md) | Broader stabilisation work map. Use current-authority and Codex context docs for state/memory terminology. |
| Progress | [Progress snapshot](progress-snapshot.md) | Short overall percentage/status view for periodic project check-ins. |
| Runtime contract | [Responses stream and tool state contract](responses-stream-tool-state-contract.md) | State contract for streamed Responses events, tool calls, telemetry, and captures. |
| Codex contract | [Codex context and memory contract](codex-context-memory-contract.md) | Authoritative v2 contract for Codex 0.130 session/thread/turn/window/workspace metadata, SQLite scope direction, and memory_domain terminology. |
| Codex evidence | [Codex 0.130 live signal capture](codex-0130-live-signal-capture.md) | Live capture evidence for Codex 0.130 request/header/body/turn/workspace signals. |
| Codex evidence | [Codex Plan mode live capture](codex-plan-mode-live-capture.md) | Live request_user_input evidence for normal-mode unavailability and Plan-mode answer flow. |
| Runtime contract | [Codex native first request capture](codex-native-request-capture.md) | Raw first request shape Codex CLI sends before QuantZhai wrapper/proxy normalization. |
| LimbiCore contract | [Model state signal contract](model-state-signal-contract.md) | Future-facing state/signal/memory envelope; no clever memory in Phase 1. |
| Signal surface map | [Codex/QuantZhai bidirectional signal map](codex-quantzhai-bidirectional-signal-map.md) | Source-grounded map of all signals: Codex→QZ, QZ→model, backend→QZ, QZ→monitor, safety matrix, gaps, and next targets. |
| Feedback subsystem plan | [Signal/feedback subsystem plan](signal-feedback-subsystem-plan.md) | Phased plan for unifying tool coercion, native tool-output classifiers, and runtime signals into qz_feedback.py. |
| Foundation audit | [Foundation audit before SQLite](foundation-audit-before-sqlite.md) | Pre-#2 architecture audit covering gravity wells, signal inventory, reaction taxonomy, Codex feedback gaps, and minimal SQLite prerequisites. |
| Backend control plane | [Backend control plane audit](backend-control-plane-audit.md) | Audit of llama.cpp/script/proxy data flow, readiness gap, ownership table, and safe next steps. |
| Backend service recovery | [Backend service recovery semantics](backend-service-recovery-semantics.md) | Status taxonomy, recovery classification matrix, HTTP behaviour map, canonical enum proposals, and next slices for #47. |
| Manual recovery policy | [Backend manual recovery endpoint policy](backend-manual-recovery-endpoint-policy.md) | Design policy for future `/qz/recovery/plan` and `/qz/recovery/trigger` endpoints: allowed actions, forbidden actions, backoff schedule, authority flags, active request safety, telemetry events, HTTP semantics, and implementation slices 6–10. Slice 6 done: pure `build_recovery_plan()` planner with 75 tests. |
| Repeated-read signal | [Repeated-read signal plan](repeated-read-dedup-plan.md) | Advisory repeated-read v1 plan and v2 scope blockers. |
| Stream watchdog | [Stream watchdog calibration plan](stream-watchdog-calibration-plan.md) | Phase 0 only. Failure classes, calibration metrics, percentile-based tuning, adaptive budget sketch, phased modes, smoke plan. Watchdog intentionally disabled until live data is collected. |
| LLM signal system | [LLM signal system](llm-signal-system.md) | Hop budget, context pressure, compaction, and signal design notes. |
| Compaction strategy | [Compaction audit and strategy](compaction-audit-and-strategy.md) | Codex audit (SHA 46f30d0), QuantZhai current behaviour, #8 promoted ideas, anchored schema v0, prompt v0, staged plan Stages 0–6. |
| Compaction schema | [Anchored compaction schema v0](compaction-anchored-schema-v0.md) | Schema spec: 14 fixed sections, exact-atom rules, evidence-to-decision retention, stale fact correction, uncertainty marking, Codex prompt-file relationship. Stage 1. |
| Compaction setup | [Compaction Codex setup](compaction-codex-setup.md) | Wire `experimental_compact_prompt_file`, use `compaction.json`, preserve safe defaults, and run manual /compact smoke checks. Stage 6. |
| Compaction dogfood | [Compaction live dogfood](compaction-live-dogfood.md) | Stage 6 live smoke evidence, direct backend runbook, observed v2 fallback, and v3 tuning blocker. |
| Compaction fixtures | [fixtures/compaction/](fixtures/compaction/) | Stage 1 example input/output pairs testing exact atom retention, tool signal retention, rejected-approach preservation, and negative constraint verbatim retention. |
| Benchmarking | [Benchmark findings: effort tuning](benchmark-findings-effort-tuning.md) | Measured profile/tool-use behaviour and open questions around open-ended repo exploration. |
| Config and error handling | [Edge case and config contract plan](edge-case-config-contract-plan.md) | Audit/refactor plan for edge cases, compact errors, profile safety, config layering, script sprawl, and the profile-bundle design (qz.profiles.v1, profiles/*.json, memory_domain plumbing). |
| Current bugfix focus | [Observability and streaming bugfix agenda](observability-streaming-bugfix-agenda.md) | Triage, review plan, proposed fixes, and acceptance checks for `/status`, monitor tools, profile tuning, and proxy streaming. |
| Deferred client/control UX | [qz-codex control plane future plan](qz-codex-control-plane-future.md) | Parked future seam for qz-codex fork/wrapper, /qz control plane, remote single-user mode, nginx/auth, and model/profile loading UX. |
| Memory architecture | [BrainCase memory tool API](braincase-memory-tool-api.md) | Tool-mediated memory plane design: tool set, harness policy, memory tiers, deterministic helpers, BrainCaseDB role, HSM mapping, and implementation slices. |
| Memory scope | [BrainCase architecture landscape and scope](braincase-architecture-landscape-and-scope.md) | LimbiCore positioning, what BrainCaseDB is and is not, comparison to RAG/MemGPT/Mem0 systems, HSM influence vs. deferred scope, QuantZhai as first LimbiCore testbed, architectural judgement, and next-work recommendations. |
| Memory retention | [BrainCase retention policy](braincase-retention-policy.md) | #54 Slice A design: multi-axis retention policy matrix, candidate/active/durable lifecycle rules, operator CLI design, no-DELETE contract, HSM scope exclusion. |
| Stream seam | [Stream reducer boundary design](stream-reducer-boundary-design.md) | #37 Slice 2A: defines the safe extraction boundary for a future pure stream state reducer — decision inventory, type sketches, purity rules, behaviour invariants, test coverage map, and recommended Slice 2B. |
| Operational state | [Operational store design](operational-store-design.md) | #51/#46 Slices A–C.1: OperationalStore (runtime_events/runtime_facts), qz-write-runtime-state dual-write, non-goals (no recovery policy, no repeated-read, no sessions/workspaces), close-out conditions for #46. |
| Memory architecture | [State and memory architecture plan](state-and-memory-architecture-plan.md) | Older typed-memory plan; useful taxonomy, superseded for Codex identity/workspace/domain decisions. |
| Fixed bug / regression guard | [Stale profile symlink bug](bugs/stale-profile-server-alias.md) | Symlink profile contract, compact invalid-profile errors, and qz-doctor regression checks. |
| Known bug | [Responses streaming and qz-thoughts bug](bugs/responses-streaming-and-qz-thoughts.md) | Historical/audit plan for Responses SSE forwarding, summary transformation, and noisy live thought rendering. |
| Known bug | [Zombie model slot bug](bugs/zombie-model-slot.md) | Model slot/control-plane related parked issue. |
| Compact profiles | [Caveman Codex model instructions v2](qz-caveman-codex-model-instructions-v2.md) | Historical compact Codex prompt/profile instructions; current profiles use `config/default/prompts/caveman-mode.md`. |
| Compact profiles | [QuantZhai caveman profile](quantzhai-caveman-profile.md) | Notes and design intent for the caveman/compact profile. |
| Config examples | [Prompt compiler example](../config/example/prompt-compiler.md) | Example symlink-profile shape, override layering, and backend-target contract for a Codex-visible profile. |
| Benchmarking | [QuantZhai benchmark harness](quantzhai-benchmark-harness.md) | Running fixed prompts, collecting artifacts, and comparing profile compression/results. |
| Runtime debugging | [Runtime observability notes](runtime-observability-notes.md) | Captures, logs, thoughts stream, telemetry, and runtime inspection. |
| Telemetry pattern | [Provenance telemetry pattern](patterns/provenance-telemetry.md) | Doctrine for source/confidence/subtractive fields in telemetry and status components. Use for any new telemetry/status work. |
| Tool contract | [Codex source tool contract](codex-source-tool-contract.md) | **Authoritative (issue #66).** Codex-source-confirmed SSE event allowlist, ResponseItem variants, apply_patch and web_search wire shapes, CODEX_NATIVE_TOOL_NAMES basis, forbidden event table. |
| Tool inventory | [Codex source tool inventory](codex-source-tool-inventory.md) | **Full audit table (issue #67).** Every tool classified: item type, payload, source evidence, QuantZhai status, deferred slices. Native pass-through set expanded to 10 tools. |
| Tool lifecycle | [Codex-visible tool lifecycle audit](codex-visible-tool-lifecycle-audit.md) | Source-grounded audit of Codex-visible SSE events per tool-call path, suppression inventory, event shapes, gaps, risks, and implementation slices. ⚠ Partially superseded — see codex-source-tool-contract.md for web_search_call correction. |
| Tool lifecycle | [apply_patch Codex lifecycle audit](apply-patch-codex-lifecycle-audit.md) | Dedicated audit of apply_patch: official event surface, adapter inventory, coercion paths, model-visible feedback, operator telemetry, gap matrix, and implementation slices AP1–AP5. ⚠ Table row corrected in issue #66 — see codex-source-tool-contract.md. |
| Tool policy | [Tool policy audit](tool-policy-audit.md) | #59 Slice A-audit: coercion/advice policy map for all tool types, budget enforcement gaps, telemetry coverage, handoff to #60/#61/#62. |
| Native tool advisory | [Native tool advisory policy](native-tool-advisory-policy.md) | #61 Slice A design: Codex source audit, advisory tool target set, candidate patterns (repeated failing commands, excessive calls, write_stdin loop, escalation retries), NativeToolAdvisoryState model, threshold/config proposal, signature safety rules. |
| Local search inventory | [Local SearXNG inventory](web-search-local-searxng-inventory.md) | #60 Slice D0: engine taxonomy, Agent API retrieval surface, profile gap analysis, Slice D annotation approach. |
| Retrieve action design | [web_search retrieve action design](web-search-retrieve-action-design.md) | #63 Slice A: contract for retrieve action, two upstream response shapes, normalization, budget, telemetry, cache, test plan. |
| Search | [Search config contract](search-config-contract.md) | #39 Slice A-design: search.json contract, precedence rules, compat with SEARXNG_POLICY, /qz/config/effective exposure, slice roadmap. |
| Search | [Search roadmap](search-roadmap.md) | Planned search capabilities, routing, and local SearXNG policy direction. |
| Search | [Profiled web search pickup README](profiled-web-search-pickup-README.md) | Pickup notes for the profiled web-search implementation/policy work. |
| Research | [Deep research report](deep-research-report.md) | Longer-form research/background notes relevant to QuantZhai direction. |
| Tooling roadmap | [Patch tool roadmap](patch-tool-roadmap.md) | Patch/edit tooling plans for safer repo modification. |
| Backend lifecycle | [Backend lifecycle control plane](backend-lifecycle-control-plane.md) | #65: BackendManager owns Docker lifecycle; qz-up starts proxy only; /qz/backend/* endpoints; operator guide in §15. |
| Model selection | [Proxy-owned model selection authority](proxy-model-selection-authority.md) | #65 follow-up: audit + final design for proxy-owned model selection; precedence; qz.model_state.v1; new /qz/model/* endpoints; qz-codex auto-select; no-new-scripts invariant. |
| Reasoning policy | [Qwen reasoning effort policy](qwen-reasoning-effort-policy.md) | Two-axis thinking control: `thinking_mode` × `reasoning_effort`; per-block budgets; profile config; TheTom budget field compat. |
| Backend compat | [TheTom OAI responses compat](thetom-oai-responses-compat.md) | `/v1/responses` path reads `thinking_budget_tokens`, not `reasoning_budget_tokens`; why both fields must be mirrored; validation evidence. |
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
scripts/qz-web-search-lifecycle-smoke          # validate web_search lifecycle SSE events and provider guidance
scripts/qz-smoke-repeated-read --model MODEL   # validate repeated-read v1 signal end-to-end
scripts/qz-smoke-recovery                      # validate recovery API (safe by default; --allow-restart/--allow-reload for dangerous)
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
BrainCase #53 CLOSED — render/recall/write_candidate live; BrainCaseDB proven.
BrainCase #54 CLOSED — retention policy; operator prune live.
Repeated-read v1 COMPLETE (#3/#4/#43 closed) — qz_file_signal.py live;
  advisory, stateless, input-history-seeded; qz-smoke-repeated-read passes.
#37 stream seam Slices 1–2E.1 COMPLETE — paused before delicate seams.

P1 #37 design micro-slice — define next delicate stream seam before coding.
   Candidates: tool lifecycle, terminal events, watchdog/timeout, proxy-local suppression.
   Each carries higher extraction risk than Slices 2B–2E. Design first.
   See docs/stream-reducer-boundary-design.md §9F.
P2 Config/var/script cleanup (#5) — safe any time; good between features.
P3 Telemetry filter ergonomics — when noisy-window problem recurs.
P4 Operational-state persistence (#51/#46) — deferred until store decision.
```

### I want to work on memory/state/BrainCaseDB

Read:

```text
docs/braincase-memory-tool-api.md      — tool plane design, memory tiers, helpers, slices A–I.1
docs/braincase-architecture-landscape-and-scope.md — scope boundary, LimbiCore positioning, comparison, HSM deferred scope
docs/braincase-retention-policy.md          — retention policy matrix design (#54 Slice A)
docs/model-state-signal-contract.md    — StateRecord envelope and scope model
AGENTS.md BrainCase Memory Tool Plane Doctrine
AGENTS.md BrainCaseDB / Memory Storage Doctrine
docs/current-architecture-authority.md
docs/current-task-hierarchy.md
docs/codex-context-memory-contract.md
docs/foundation-audit-before-sqlite.md

Key source files (proxy/):
  qz_braincase_db.py       — BrainCaseDB storage layer
  qz_braincase_write.py    — explicit write/update helpers
  qz_braincase_render.py   — internal RenderPacket builder
  qz_braincase_tools.py    — braincase.render + recall tool surface (Slices F+G)
```

Focus:

```text
The memory architecture is tool-mediated, not DB-first.
Start from tool semantics, harness policy, memory tiers, and render boundaries.
BrainCaseDB stores; tools and helpers operate.
Do not add automatic ingestion.
Do not start from SQL tables.
Slice A (StateRecord JSON schema fixtures) must come before Slice B (DB schema).
Raw captures remain debug artifacts.
Do not inject qz_session_id/qz_workspace_id/qz_memory_domain into forwarded request bodies.
memory_domain is config-owned; BrainCaseDB must not infer or grant domain values.
braincase.render + braincase.recall exposed (Slices F+G). QZ_BRAINCASE_TOOLS_ENABLED flag.
write/update/search/inspect remain internal until operator exposure is defined.
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
docs/patterns/provenance-telemetry.md
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
docs/codex-plan-mode-live-capture.md
docs/codex-native-first-request-capture.md
docs/codex-request-signal-inventory.md
docs/conversation-history-audit-plan.md
docs/deep-research-report.md
docs/edge-case-config-contract-plan.md
docs/llm-signal-system.md
docs/compaction-audit-and-strategy.md
docs/compaction-anchored-schema-v0.md
docs/compaction-codex-setup.md
docs/compaction-live-dogfood.md
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
docs/current-stocktake.md
docs/runtime-observability-notes.md
docs/patterns/provenance-telemetry.md
docs/search-roadmap.md
docs/braincase-memory-tool-api.md
docs/schemas/braincase/source-ref.schema.json
docs/schemas/braincase/state-record.schema.json
docs/schemas/braincase/render-packet.schema.json
docs/fixtures/braincase/source-refs/
docs/fixtures/braincase/state-records/
docs/fixtures/braincase/render-packets/
docs/state-and-memory-architecture-plan.md
docs/codex-visible-tool-lifecycle-audit.md
docs/qwen-reasoning-effort-policy.md
docs/thetom-oai-responses-compat.md
```

## Maintenance rule

When adding a new Markdown document, add it to this index in the same commit.

A document that cannot be found is just a very small archaeological site.
