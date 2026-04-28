# Fox Roadmap

Date: 2026-04-28

## Goal

Evaluate whether Fox can become an alternate QuantZhai model server path beside the current TurboQuant llama.cpp Docker server.

Fox presents itself as a high-performance local LLM server and drop-in Ollama replacement with OpenAI-compatible endpoints, prefix caching, continuous batching, lazy model loading, multi-model serving, and TurboQuant KV cache support.

The decision gate is feature parity first, performance second. QuantZhai should not add supported Fox launch scripts until either Fox repo reaches parity with the new features in `thetom/llama.cpp-turboquant`, not merely parity with the local Docker launch path.

## Candidate Repositories

- `https://github.com/ferrumox/fox`: upstream-looking project page, described as "The fastest local LLM server. Drop-in replacement for Ollama."
- `https://github.com/CrimsonMartin/fox`: forked from `ferrumox/fox`; currently shows a larger visible commit history and separate CUDA/ROCm Dockerfiles.

Treat `ferrumox/fox` as canonical until proven otherwise. Treat `CrimsonMartin/fox` as an implementation or packaging reference worth inspecting, especially for Docker/GPU work.

## Why Fox Might Matter

- It exposes OpenAI-compatible `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, and `/v1/models` endpoints.
- It also exposes Ollama-compatible endpoints.
- It advertises prefix caching and continuous batching, which may fit multi-turn Codex sessions.
- It advertises multi-GPU support and TurboQuant KV cache options.
- It can search and pull GGUF models from Hugging Face.

## Phase 1: Recon

- Clone both repos outside QuantZhai, not into this repo.
- Compare Dockerfiles, GPU support, releases, branches, issues, and licenses.
- Confirm which repo is maintained and which branch should be tracked.
- Check whether Fox can load the known Qwen3.6 GGUF.
- Check whether Fox supports the same or equivalent KV cache settings used by QuantZhai: `q8_0` keys and `turbo3` values.

## Phase 2: Local Smoke Test

- Start Fox on a non-conflicting port, probably `18085`.
- Load or pull a small GGUF first.
- Confirm `/v1/models` and `/v1/chat/completions`.
- Test streaming and non-streaming responses.
- Test cancellation behavior.
- Check GPU selection, tensor split, context size, and memory use.

## Phase 3: Proxy Compatibility

- Point QuantZhai proxy upstream at Fox instead of llama.cpp.
- Verify Codex Responses flow still works through the proxy.
- Compare tool-call behavior, streaming chunks, finish reasons, token accounting, and error formats.
- Decide whether Fox needs a separate adapter path or can fit behind the same upstream contract.

## Phase 4: Benchmark Against Current Stack

- Use the same model, context, prompt sets, and Codex workflows.
- Compare time-to-first-token, tokens/sec, long-context stability, memory use, and multi-turn latency.
- Repeat with the current Docker llama.cpp server as baseline.
- Capture logs under `var/captures/` and keep benchmark artifacts out of git unless summarized.

## Phase 5: Integration Decision

Choose one:

- Keep Fox as an experimental doc-only path.
- Add `scripts/qz-up-fox` and `scripts/qz-down-fox`.
- Add `QZ_BACKEND=llamacpp|fox` to `qz-env`.
- Replace the current server path only if Fox is clearly simpler, faster, and compatible with Codex sessions.

Support is blocked until Fox reaches parity with the `thetom/llama.cpp-turboquant` repo and feature set on:

- TurboQuant-specific llama.cpp behavior used by the fork.
- Loading the known Qwen3.6 GGUF.
- GPU placement and multi-GPU behavior.
- Context size and KV cache options.
- OpenAI-compatible request/response behavior needed by QuantZhai's proxy.
- Streaming stability during real Codex sessions.
- Operational simplicity: build, launch, stop, clean, logs, and recovery.

## Non-Goals For Now

- Do not vendor Fox.
- Do not replace the working Docker path before benchmark evidence exists.
- Do not assume Ollama compatibility means Codex Responses compatibility.
- Do not add automatic model downloads to normal `qz-up`.
