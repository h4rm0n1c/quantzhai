# QuantZhai Agent Notes

## Project Shape

QuantZhai is a local Codex stack for running Qwen through a TurboQuant llama.cpp server and an OpenAI-compatible proxy.

Keep the repo small and reproducible. Runtime state belongs in `var/`; source, config examples, scripts, and docs belong in git.

## Proxy Experience Principle

**The user sees a pause. They don't see an error.**

This is the design ethos for every failure mode the proxy can encounter.

When bad things happen — model switches, child crashes, VRAM exhaustion, backend
timeouts, stale state — the proxy's job is to absorb the disruption and recover
transparently. The SSE channel stays open. Keepalives flow. The inference resumes
when the system is ready. Codex experiences latency, not failure.

An error surfaced to Codex (`response.failed`, `503`, raw exception text) is a
proxy design failure, not a backend failure. The backend is allowed to crash.
The proxy is not allowed to pass that crash through.

Concrete rules that follow from this:
- Hold-open before forwarding. Never admit a request to a model that isn't ready.
- When a child crashes mid-session, reload it and retry on the same SSE channel.
- When switching models, hold-open until the new model is confirmed loaded.
- When state is stale (last_load_result="failed"), clear it before the next attempt.
- A `response.failed` event is a last resort — exhausted recovery budget, not first response.
- BrokenPipe on the client side is a transport failure, not an error; swallow it.
- Log failures internally. Expose recovery status as SSE comments, not as errors.

When writing new failure paths: ask "does the user see a pause, or do they see an
error?" If the answer is error, the path is not finished.

## Deterministic Intercept Principle

**Any failure that is deterministically predictable from the tool call shape is a proxy responsibility to fix — not a model problem to retry.**

The proxy sits between the LLM and every tool execution layer. When the model generates a call that will fail for a known, structurally-detectable reason, every turn spent on confused retrying is a proxy design failure.

Three intervention levels, in order of preference:

**1. Pre-execution correction (coerce path)**
Bad shape detected from call arguments alone, before any tool runs.
- If the fix is unambiguous: apply silently, inject a correction note so the model learns.
- If the fix requires model input: return a precise error immediately with the exact cause.
- Cost: zero Codex round-trips. Model sees success + note, or sees one actionable error.

**2. Post-execution interception (escalation path)**
Tool ran, failed for a deterministically-fixable reason (e.g. sandbox denial).
- Intercept the failure before it reaches the model.
- Apply the fix (re-emit call with corrected parameters, rewrite history).
- Inform the model in plain English what happened — no internal parameter names.
- Cost: zero extra model turns. The model sees the success result with a transparent explanation.

**3. Advisory injection**
Failure can't be auto-fixed (e.g. apply_patch context mismatch — proxy lacks file content)
but the cause is deterministically knowable from the error text.
- Inject a precise advisory into the model's next input naming the cause and the recovery action.
- This breaks the model's confused-retry loop before it generates another wrong call.
- Cost: saves 1–N retry turns compared to the model discovering the fix by trial and error.

**When evaluating a new tool failure pattern, ask:**
1. Is the failure deterministic for this call shape? (same inputs → same failure every time)
2. Is the fix deterministic? (known correct transformation exists)
3. Is the fix safe? (no semantic ambiguity — we can't guess at intent)

If 1+2+3: implement pre-execution correction.
If 1+2, fix requires tool re-run: implement post-execution interception.
If 1 only (can't fix): implement advisory injection.
If none: it's a model reasoning problem, not a proxy problem.

**Do not expose proxy internal parameter names in model-visible notes.** Strings like
`sandbox_permissions="require_escalated"` look like config keys the model tries to set
globally; plain-English explanations ("the proxy escalated permissions") are understood
correctly and don't trigger false inferences about policy settings.

See `proxy/qz_sandbox_escalation.py`, `docs/proxy-transparent-intercept-contract.md`,
and `docs/proxy-intercept-research.md` for the current implementation and known patterns.

Concrete rules added 2026-05-30/31:
- exec sandbox denial → `SandboxEscalationManager` two-phase intercept + plain-English note.
- apply_patch outer JSON fence → pre-pass strip in `_parse_apply_patch_arguments`.
- apply_patch empty diff / empty trailing hunk (AP-1/AP-1b) → coerce() precise error.
- apply_patch silent corrections → `CorrectionTracker` note in tool result.
- apply_patch delta_limit = -1 (unlimited) — diffs are file-size-bounded, no runaway risk.
- AP-4 (context mismatch) advisory → next item, see issue #83.

## Router Mode

QuantZhai uses llama.cpp router mode. The container stays alive; models are loaded and unloaded via HTTP. See `docs/router-mode-migration-plan.md` for history and remaining P2 cleanup.

Key rules:
- Model switching must NOT kill or recreate the container. Use `unload_model_http()` then `load_model_http()`.
- One model loaded at a time — VRAM constraint (26 GB total). Always unload before loading a new model.
- Hold-open for `/v1/responses` is unconditional for `stream=True` (no env var gate, `QZ_HOLDOPEN_LOADING` is removed).
- `healthy` backend + `unloaded` router status = `"loading"`, NOT `"unavailable"`.
- `restart()` is dead code; use `start()` (container start) or `load_model_http()` (model switch).
- `_auto_trigger_model_switch_nonblocking` must only return `True` when it actually triggered something.
- `_do_select_model()` must write with a source from `SELECTED_SOURCES` (e.g. `"qz_codex"`) — never `"recovery_select_model"` or `"status_snapshot"` which are treated as observational and discarded on restart.
- GPU offload is verified via `GET /v1/models` (child process inventory), not docker logs. Docker logs show the parent router only.

## Do Not Commit

- `.env`
- `var/`
- `logs/`, `captures/`, `run/`
- model files such as `*.gguf` and `*.safetensors`
- Python caches and test caches
- local Codex sessions, history, sqlite state, installation ids, or request captures

## Important Files

- `README.md`: first-run user documentation.
- `proxy/quantzhai_proxy.py`: local Responses API bridge.
- `scripts/qz-env`: shared environment defaults.
- `scripts/qz-up`: starts the model server and proxy.
- `scripts/qz-codex`: launches Codex against the local proxy.
- `scripts/qz-build-image`: builds the local TurboQuant Docker image.
- `config/`: publishable example config and model catalog.
- `docs/`: design notes, pickup plans, and roadmap docs.
- `docs/README.md`: documentation index and recommended reading path.
- `docs/current-architecture-authority.md`: final conflict resolver for current architecture and stale assumptions.
- `docs/current-task-hierarchy.md`: active task DAG, blocker order, and implementation prompts.
- `docs/master-stabilisation-plan.md`: controlling map for the current stabilisation work and fix order.
- `docs/progress-snapshot.md`: short periodic status view.
- `docs/bugs/`: known bug notes and regression reminders. Check this before planning new proxy/catalog work.
- `docs/edge-case-config-contract-plan.md`: planned audit/refactor for edge cases, errors, config layout, profile safety, and script-sprawl reduction.

## Development Rules

- Prefer small, direct changes that keep setup obvious.
- Preserve local runtime isolation under `var/`.
- Keep `.env.example` generic; no host usernames, private paths, private IPs, or secrets.
- Treat Docker image names as local tags unless the docs explicitly say otherwise.
- Do not run long Docker builds, model launches, or network installs unless the user asks.
- Do not rename `Qwen3.6Turbo-*` model slugs casually; `qz-codex` relies on the proven catalog names.
- If changing proxy behavior, update or add docs under `docs/` that explain the runtime contract.
- When the user asks what needs doing, review `docs/README.md`, `docs/current-architecture-authority.md`, `docs/current-task-hierarchy.md`, `docs/master-stabilisation-plan.md`, `docs/bugs/`, `docs/edge-case-config-contract-plan.md`, and the active roadmap docs before answering.

## Agent Behaviour Rules

Coding agents do not automatically preserve project memory. This repo uses docs, issues, captures, and tests as the durable memory layer. Keep those layers current while changing code.

When a discovery changes how future agents should work, update `AGENTS.md` in the same commit or create a follow-up issue if the rule needs review first.

When a bug changes the runtime contract, update the relevant contract or bug note. Good targets are usually:

```text
AGENTS.md
docs/README.md
docs/current-task-hierarchy.md
docs/master-stabilisation-plan.md
docs/bugs/*.md
docs/responses-stream-tool-state-contract.md
docs/runtime-observability-notes.md
```

When a new Markdown document is added, also update `docs/README.md`. A useful document that is not indexed will be rediscovered badly by the next agent.

When a task changes direction, update `docs/current-task-hierarchy.md` in the same commit as the doc or implementation change that caused it. A stale task DAG is a roadmap wearing novelty glasses.

Prefer evidence-backed issue notes for non-trivial bugs or design changes. A good issue includes:

```text
symptom
confirmed evidence or capture path
likely fault line
proposed fix
telemetry/capture expectations
acceptance tests
regression risks
```

Do not rely on chat memory, vibes, or a single terminal observation when the repo already has a contract, capture, bug note, or test fixture that can prove the point.

## Git and GitHub Workflow

Use normal `git` commands for local working-tree facts:

```bash
git status --short --ignored
git diff
git log --oneline --decorate -20
git branch --show-current
```

Use the GitHub CLI `gh` when GitHub-side state adds problem-solving value or is easier than reconstructing it from `git`. This includes issues, pull requests, Actions, releases, repository metadata, and GitHub API queries.

Useful checks:

```bash
gh auth status
gh repo view --json nameWithOwner,defaultBranchRef,url
gh issue list --limit 20
gh issue view <number> --comments
gh pr list --limit 20
gh pr view <number> --comments --files
gh run list --limit 10
```

Use `gh issue create`, `gh issue comment`, or `gh issue edit` when an investigation produces a durable bug/design note that should survive the current session.

Use `gh api` for wider GitHub access when no higher-level `gh` subcommand exposes the needed data cleanly.

Do not use `gh` as a replacement for local source inspection. For code truth, prefer the working tree and normal `git`/`rg`/`sed`/tests. For GitHub truth, use `gh`.

## Recursive Documentation Rule

Every non-trivial change should ask: what future agent behaviour should this teach?

If the answer is useful outside the immediate patch, capture it in the narrowest durable place:

```text
AGENTS.md                         repo-wide agent behaviour
docs/current-task-hierarchy.md     active work order and implementation prompts
docs/master-stabilisation-plan.md  broader stabilisation map and dependency chain
docs/bugs/*.md                    bug evidence, regression reminders, acceptance checks
docs/*contract*.md                runtime/API/data-shape contracts
GitHub issues                     open design/bug tasks that are not fixed yet
```

Do not let fixes become folklore. If an agent had to learn something by painful investigation, write it down where the next agent will look before repeating the pain.

## Evidence-First Debugging

For runtime and proxy bugs, collect request-scoped evidence before guessing.

Prefer request-scoped captures over `latest-*` files when concurrent Codex, monitor, or smoke requests may be active:

```text
var/captures/requests/<request_id>/incoming-request.json
var/captures/requests/<request_id>/forwarded-request.json
var/captures/requests/<request_id>/request-contract.json
var/captures/requests/<request_id>/upstream-response.raw
var/captures/requests/<request_id>/forwarded-sse.raw
```

Compare all relevant layers before assigning blame:

```text
incoming client request
forwarded upstream request
raw upstream SSE
forwarded client SSE
telemetry events
qz-top / qz-thoughts rendering
Codex-visible behaviour
```

For streaming bugs, distinguish transport failure from model behaviour. A stream can be perfectly transported and still be useless if upstream emits reasoning only and no `output_text`.

## Telemetry and Status Field Doctrine

For any new telemetry value, status component, or dashboard field:

Follow `docs/patterns/provenance-telemetry.md`.

Key rules:
- Every value needs `source`, `confidence`, `estimated`, and `backend_confirmed`.
- Use only the confidence vocabulary defined in the pattern doc.
- Do not collapse observed / configured / estimated / calibrated / provenance-only
  values into one confidence bucket.
- Never label residual as scratch. Residual = process minus components; scratch
  is one specific type that cannot be inferred without an allocator metric.
- Never mark an estimate as `backend_confirmed`. Only backend allocator metrics
  earn that label.
- Only subtract components with `subtractive=true` from residual math. Keep
  provenance-only facts visible but out of the arithmetic.
- Unknown quant dtype must not silently become f16. Set `formula_safe=false`
  and surface the unknown type in notes.

## BrainCaseDB / Memory Storage Doctrine

BrainCaseDB (`proxy/qz_braincase_db.py`) is the low-level SQLite state/memory
storage substrate. It is a storage case, not a policy layer.

Hard rules for any agent touching BrainCaseDB or planning SQLite work:

- **BrainCaseDB is not a telemetry warehouse.** Do not write telemetry events to it.
- **BrainCaseDB is not a request log.** Do not automatically store every request, turn, or session.
- **BrainCaseDB is not a runtime event store.** Do not write stream events, tool calls, recovery state, or backoff data to it.
- **BrainCaseDB is not a memory_domain registry.** memory_domain definitions remain config-owned.
- **Do not add automatic ingestion.** BrainCaseDB records may only be written through an explicit memory/state write path: manual/user-approved save, future promotion pipeline, future memory extractor, future StateRecord creation, explicit test fixture, or narrow provenance/scoping support attached to an actual stored memory/state record.
- **Store only explicit memory/state records or provenance needed by those records.** Parser-boundary identity/scoping facts are allowed only when needed to scope or prove provenance for an actual stored state/memory record.
- SQLite may record which configured memory_domain applied to a stored record, but must not infer, create, normalize, or grant domains.

Do not automatically ingest: every request, every turn, every session, every
stream event, telemetry events, tool calls, tool output bodies, recovery state,
backoff state, raw prompts, or raw request bodies.

Before adding any BrainCaseDB write path, confirm there is an explicit
memory/state record the write is scoping or supporting. If no such record
exists yet, wait for the StateRecord/memory-write API design. Do not preempt
that design with automatic parser-fact ingestion.

## BrainCase Memory Tool Plane Doctrine

The memory architecture is tool-mediated, not DB-first. See
`docs/braincase-memory-tool-api.md` for the full design doc.

Hard rules for any agent touching memory, SQLite, or state work:

- **The memory architecture is tool-mediated, not DB-first.** Do not start from SQL tables. Start from tool semantics, harness policy, memory tiers, and render boundaries.
- **BrainCaseDB stores; tools and helpers operate.** Do not add business logic, policy, or routing to the storage layer.
- **Deterministic helpers accelerate and constrain mechanics; they do not replace LLM reasoning.** Helpers handle scope routing, dedup, conflict surfacing, and render packing. The LLM reasons about relevance, intent, and correctness.
- **Do not make raw storage model-visible.** Records are internal until explicitly rendered through braincase.render.
- **Do not add automatic ingestion.** All BrainCaseDB write paths must be explicit.
- **memory_domain is config-owned.** BrainCaseDB must not infer, create, normalize, grant, or authorize memory_domain values.

The corrected framing:

```text
Not: LLM proposes -> deterministic judge decides -> DB stores
Better: LLM thinks -> uses memory tools -> helpers accelerate/constrain mechanics
        -> storage/indexes return exact evidence -> LLM reasons again
```

Before adding any memory/state implementation, read:

```text
docs/braincase-memory-tool-api.md  — tool plane design, tiers, helpers, slices
AGENTS.md BrainCaseDB doctrine     — storage hard rules
docs/model-state-signal-contract.md — StateRecord envelope and scope model
```

## Contract-First Fixes

QuantZhai bugs often come from blurred ownership between config, model catalog, profile aliases, backend routing, prompt policy, runtime state, SSE streaming, telemetry, monitors, and generated Codex metadata.

Before broad refactors, identify the owning layer and write or update the contract. Then patch the code. Then add the smallest regression test or capture-based acceptance check that proves the contract still holds.

Do not create a second truth in scripts, generated files, monitors, or docs. Proxy policy remains the source of truth for routing, prompt policy, runtime truth, and Codex-facing generated views.

## Proxy Policy Is the Source of Truth

Codex-facing scripts and generated files must stay in sync with proxy behavior.

Any script or generator that echoes proxy datapath information out to Codex for convenience, such as generated model catalogs, `config.toml` edits, model/profile aliases, context windows, prompt metadata, or `/status`-style summaries, must reflect the same routing and prompt policy enforced by the proxy.

Do not create a second truth in scripts. In particular:

- Profile/model aliases shown to Codex must remain the Codex-visible profile identity.
- Backend routing must remain separate as the proxy-selected backend target. Symlink profiles under `var/models/` keep their Codex-visible filename while routing to the resolved target GGUF stem.
- Prompt selection must follow the proxy prompt policy and selected model/profile overrides.
- If a helper script exports model names, context lengths, prompt sources, or status metadata to Codex, update it when proxy policy changes.
- Generated Codex catalogs are a view of proxy policy, not an authority over prompt or backend routing.

When changing proxy routing or prompt policy, audit at least:

```bash
rg -n "model_catalog|base_instructions|system_prompt|backend_id|backend_target|context_window|status|QZSTATE|prompt_policy" scripts proxy config docs AGENTS.md
```

Then verify with a capture from a real Codex request, not just generated config:

```bash
jq '{model, instructions_head:(.instructions|.[0:180]), policy:.metadata.qz_prompt_policy}' var/captures/latest-forwarded.json
curl -s http://127.0.0.1:18180/qz/status | jq '.backend.selected_backend_id, .backend.loaded_model, .backend.selected_context_length'
```

Known bug reminder: stale profile symlinks with missing GGUF targets must not be allowed to brick Codex sessions. See `docs/bugs/stale-profile-server-alias.md` before changing profile/catalog routing.

Known streaming reminder: Responses SSE forwarding and `qz-thoughts` need contract-aware handling. See `docs/bugs/responses-streaming-and-qz-thoughts.md` and `docs/responses-stream-tool-state-contract.md` before changing SSE transformation, telemetry, reasoning-summary handling, monitor rendering, tool-call buffering, repair hops, or terminal event handling.

Before broader error handling, profile routing, config layout, or script cleanup work, read `docs/master-stabilisation-plan.md` and `docs/edge-case-config-contract-plan.md`. The master plan gives the fix order. The config contract plan gives the audit/refactor rules.

## Host Sudo Workflow

This host may use `QZ_DOCKER_CMD="sudo docker"`. Codex sessions often cannot answer interactive sudo prompts, so simple Docker/sudo checks can fail even when the local setup is healthy.

When blocked by sudo for straightforward host checks, do not over-debug inside Codex. Give the user a small pasteable command block, ask them to run it in their terminal, and continue from the pasted output.

Typical block:

```bash
cd /home/harri/turboquant/quantzhai
sudo -v
./scripts/qz-doctor
```

For Docker inspection, prefer similarly pasteable, narrowly scoped commands such as:

```bash
cd /home/harri/turboquant/quantzhai
sudo docker images
sudo docker ps -a
```

## Validation

For script or proxy changes, run:

```bash
bash -n scripts/qz-env scripts/qz-doctor scripts/qz-up scripts/qz-proxy scripts/qz-codex scripts/qz-down scripts/qz-build-image
python3 -m py_compile proxy/quantzhai_proxy.py
```

For documentation-only changes, check links and paths:

```bash
git status --short --ignored
git add --dry-run .
```

## Git Hygiene

Before commit or push, inspect:

```bash
git status --short --ignored
rg -n "harri|/home/|192\.168|password|secret|api[_-]?key|installation_id|history\.jsonl" . -g '!var/**' -g '!.env' -g '!.git/**'
```

Only scripts should normally be executable. Docs, images, config, and Python source should normally be mode `100644`.
