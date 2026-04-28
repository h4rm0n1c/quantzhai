<p align="center">
  <img src="docs/logo.png" alt="QuantZhai logo" width="180">
</p>

# QuantZhai

QuantZhai is a local Codex stack for running Qwen through a turboquant llama.cpp server with an OpenAI-compatible proxy.

This directory is the cleaned seed, not the discovery dump. Runtime state lives under `var/` and stays out of git.

## Status

QuantZhai is early but has run locally in a useful Codex workflow. Treat it as a reproducible lab stack, not a polished installer.

Known-good host used during initial bring-up:

```text
OS: Devuan GNU/Linux 6 excalibur
Kernel: Linux 6.12.73+deb13-amd64
Shell: bash 5.2
Docker: requires sudo on this host
Driver: NVIDIA 575.57.08
CUDA reported by nvidia-smi: 12.9
GPU 0: NVIDIA GeForce RTX 3080 10GB
GPU 1: NVIDIA Tesla V100-SXM2 16GB
Memory: 47GB RAM, 16GB swap
```

The tested launch split model state across both GPUs. Smaller or different models may work on less hardware; this README only documents the setup known to have worked here.

## Architecture

```text
Codex CLI
  -> QuantZhai proxy on 127.0.0.1:18180
  -> llama.cpp turboquant server on 127.0.0.1:18084
  -> local GGUF model mounted into Docker
```

The proxy exists because Codex expects OpenAI-style Responses behavior, model catalog metadata, streaming events, rate-limit headers, tool-call normalization, and local compaction behavior. The Docker server does the model inference.

## What Ships

- `proxy/quantzhai_proxy.py`: local Responses API bridge for Codex.
- `scripts/qz-up`: starts the turboquant llama.cpp Docker server and proxy.
- `scripts/qz-build-image`: builds the local turboquant llama.cpp Docker image.
- `scripts/qz-proxy`: starts or restarts only the proxy.
- `scripts/qz-codex`: runs Codex against the local proxy.
- `scripts/qz-down`: stops the proxy and QuantZhai container.
- `scripts/qz-doctor`: checks local prerequisites.
- `scripts/qz-clean-legacy`: stops the old source-tree proxy and shared container.
- `config/`: publishable Codex config and model catalog examples.

## Runtime Layout

```text
var/
  codex-home/   # Codex config, sessions, history, sqlite state, plugin/cache data
  logs/         # Proxy logs
  captures/     # latest request/response/debug captures
  run/          # pid files
```

`scripts/qz-codex` sets:

```bash
CODEX_HOME="$PWD/var/codex-home"
CODEX_SQLITE_HOME="$PWD/var/codex-home/sqlite"
CODEX_OSS_BASE_URL="http://127.0.0.1:18180"
```

That keeps the Codex environment for this stack inside `quantzhai/var/codex-home` instead of the global `~/.codex`, assuming the Codex CLI honors `CODEX_HOME` for the operation being run.

## Requirements

- Docker with NVIDIA GPU support.
- `nvidia-smi` visible on the host.
- A turboquant llama.cpp server image, or enough build tooling to create it.
- A local Qwen GGUF model.
- `codex` CLI available on `PATH`.
- Python 3.

Known local Docker image:

```text
thetom-llama-cpp-turboquant:cuda-server
```

This is a local image tag. It is not assumed to exist in a public registry.

If Docker needs sudo on your machine, set this in `.env`:

```bash
QZ_DOCKER_CMD="sudo docker"
```

For non-interactive runs, including Codex-driven setup checks, sudo must already be able to run. Either run the scripts in a terminal where sudo can prompt, pre-auth with `sudo -v`, or add the user to the Docker group if that is acceptable for the machine.

## Build Docker Image

If `scripts/qz-doctor` says the Docker image is missing, build it locally:

```bash
scripts/qz-build-image
```

That script clones or updates:

```text
https://github.com/TheTom/llama-cpp-turboquant.git
```

Default branch:

```text
feature/turboquant-kv-cache
```

Default image tag:

```text
thetom-llama-cpp-turboquant:cuda-server
```

Default build directory:

```text
$HOME/turboquant-work/llama-cpp-turboquant
```

Default CUDA architectures:

```text
70;86
```

Those match the known-good Tesla V100 plus RTX 3080 host. Change `QZ_CUDA_ARCH` in `.env` for other GPUs.

## Quick Start

```bash
cd quantzhai
cp .env.example .env
$EDITOR .env
scripts/qz-doctor
scripts/qz-build-image   # only needed if qz-doctor reports missing image
scripts/qz-clean-legacy
scripts/qz-up
scripts/qz-codex high
```

Default profile aliases:

```text
low
medium
caveman
high
max
```

These aliases currently map to these Codex model names:

```text
Qwen3.6Turbo-low
Qwen3.6Turbo-medium
Qwen3.6Turbo-caveman
Qwen3.6Turbo-high
Qwen3.6Turbo-max
```

`caveman` is an experimental compact-instructions profile. `scripts/qz-codex caveman`
loads `docs/qz-caveman-codex-model-instructions-v2.md` and caps Codex output at
2048 tokens for that session.

## Configuration

Main local config lives in `.env`.

Important settings:

- `QZ_IMAGE`: Docker image for the turboquant llama.cpp server.
- `QZ_DOCKER_CMD`: Docker command, usually `docker` or `"sudo docker"`.
- `QZ_CONTAINER`: container name, default `qwen36turbo`.
- `QZ_BUILD_DIR`: external build workspace for the turboquant source clone.
- `QZ_TQ_REPO`: turboquant llama.cpp Git repository.
- `QZ_TQ_BRANCH`: turboquant branch to build.
- `QZ_CUDA_ARCH`: CUDA architectures for the Docker build.
- `QZ_MODEL_SRC`: absolute path to the local GGUF.
- `QZ_MODEL_NAME`: filename mounted inside the container.
- `QZ_SERVER_PORT`: host port for llama.cpp server, default `18084`.
- `QZ_PROXY_PORT`: host port for QuantZhai proxy, default `18180`.
- `QZ_CONTEXT`: context window, default `131072`.
- `QZ_PARALLEL`: llama.cpp parallel slots, default `1`.
- `QZ_BATCH` / `QZ_UBATCH`: batch settings, defaults `4096` and `512`.
- `QZ_TENSOR_SPLIT`: GPU split passed to llama.cpp, default `9,17`.
- `QZ_CACHE_RAM` / `QZ_CACHE_REUSE`: prompt cache settings, defaults `8192` and `256`.
- `QZ_KV_KEY` / `QZ_KV_VALUE`: KV cache quant settings.
- `SEARXNG_BASE_URL`: optional SearXNG base URL for local web search. Leave empty to disable search.
- `SEARXNG_POLICY`: search routing policy, default `docs/searxng-agent-policy-profiled.json`.

The current defaults came from the working two-GPU Qwen3.6 setup. They are not universal.

## Local Search

QuantZhai can expose one local `web_search` tool to Codex when `SEARXNG_BASE_URL` points at a running SearXNG instance.

Search supports profiles:

```text
auto
broad
coding
sysadmin
research
news
ai_models
reference
```

Normal use should leave `profile` as `auto`. The proxy routes the query through `docs/searxng-agent-policy-profiled.json`, filters disabled or non-text engines, and writes the latest routing decision to:

```text
var/captures/latest-web-search-route.json
```

Example `.env` setting:

```bash
SEARXNG_BASE_URL=http://127.0.0.1:8080
```

Quick smoke test:

```bash
source scripts/qz-env
curl "$SEARXNG_BASE_URL/search?q=quantzhai%20smoke%20test&format=json"
scripts/qz-proxy
```

Useful test queries for Codex or a proxy-level smoke:

```text
latest qwen gguf release
python json decode error stdin
define shanzhai
```

## Useful Commands

Check environment:

```bash
scripts/qz-doctor
```

Start server and proxy:

```bash
scripts/qz-up
```

Restart only proxy:

```bash
scripts/qz-proxy
```

Run Codex:

```bash
scripts/qz-codex high
```

Stop QuantZhai:

```bash
scripts/qz-down
```

Stop old source-tree process/container:

```bash
scripts/qz-clean-legacy
```

## Troubleshooting

If `qz-doctor` says Docker image missing, check local images:

```bash
sudo docker images
```

Then build the image:

```bash
scripts/qz-build-image
```

If `qz-doctor` says Docker daemon access failed, fix Docker permissions first. With `QZ_DOCKER_CMD="sudo docker"`, run the script in a real terminal where sudo can prompt, or refresh sudo with `sudo -v` before running Codex-driven setup commands.

If `qz-proxy` says the port is in use, clear old proxy processes:

```bash
scripts/qz-clean-legacy
scripts/qz-proxy
```

If Codex says `Pulling model ...` then fails, check that `qz-codex` is using the local model catalog under `var/codex-home/model-catalogs/` and that the proxy is reachable:

```bash
curl http://127.0.0.1:18180/v1/models
```

If the proxy starts but requests fail, inspect:

```text
var/logs/qz-proxy.log
var/captures/latest-request.json
var/captures/latest-forwarded.json
var/captures/latest-json-api.log
```

If the model server fails or exits, inspect Docker:

```bash
sudo docker ps -a
sudo docker logs qwen36turbo
```

## Git Hygiene

Do not commit:

```text
.env
var/
models/
*.gguf
*.safetensors
logs/
captures/
run/
```

These may contain local paths, prompts, tool output, secrets, request captures, sqlite state, sessions, or model blobs.

## Roadmaps

- `docs/search-roadmap.md`: profile-aware local search plan.
- `docs/fox-roadmap.md`: future Fox backend evaluation and parity gate.
- `docs/proxy-architecture-roadmap.md`: Python proxy restructure, test harness, and possible Rust port.

## Name

`Zhai` comes from `shanzhai`: scrappy, DIY, mountain-fort energy. QuantZhai means local quant stack built from practical parts.
