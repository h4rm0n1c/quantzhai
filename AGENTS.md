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

## Development Rules

- Prefer small, direct changes that keep setup obvious.
- Preserve local runtime isolation under `var/`.
- Keep `.env.example` generic; no host usernames, private paths, private IPs, or secrets.
- Treat Docker image names as local tags unless the docs explicitly say otherwise.
- Do not run long Docker builds, model launches, or network installs unless the user asks.
- Do not rename `Qwen3.6Turbo-*` model slugs casually; `qz-codex` relies on the proven catalog names.
- If changing proxy behavior, update or add docs under `docs/` that explain the runtime contract.

## Proxy Policy Is the Source of Truth

Codex-facing scripts and generated files must stay in sync with proxy behavior.

Any script or generator that echoes proxy datapath information out to Codex for convenience, such as generated model catalogs, `config.toml` edits, model/profile aliases, context windows, prompt metadata, or `/status`-style summaries, must reflect the same routing and prompt policy enforced by the proxy.

Do not create a second truth in scripts. In particular:

- Profile/model aliases shown to Codex must remain the Codex-visible profile identity.
- Backend routing must remain separate, for example through `server_alias` or the proxy-selected backend target.
- Prompt selection must follow the proxy prompt policy and selected model/profile overrides.
- If a helper script exports model names, context lengths, prompt sources, or status metadata to Codex, update it when proxy policy changes.
- Generated Codex catalogs are a view of proxy policy, not an authority over prompt or backend routing.

When changing proxy routing or prompt policy, audit at least:

```bash
rg -n "model_catalog|base_instructions|system_prompt|server_alias|backend_id|context_window|status|QZSTATE|prompt_policy" scripts proxy config docs AGENTS.md
```

Then verify with a capture from a real Codex request, not just generated config:

```bash
jq '{model, instructions_head:(.instructions|.[0:180]), policy:.metadata.qz_prompt_policy}' var/captures/latest-forwarded.json
curl -s http://127.0.0.1:18180/qz/status | jq '.backend.selected_backend_id, .backend.loaded_model, .backend.selected_context_length'
```

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
bash -n scripts/qz-env scripts/qz-doctor scripts/qz-up scripts/qz-proxy scripts/qz-codex scripts/qz-down scripts/qz-clean-legacy scripts/qz-build-image
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
