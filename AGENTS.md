# QuantZhai Agent Notes

## Project Shape

QuantZhai is a local Codex stack for running Qwen through a TurboQuant llama.cpp server and an OpenAI-compatible proxy.

Keep the repo small and reproducible. Runtime state belongs in `var/`; source, config examples, scripts, and docs belong in git.

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
