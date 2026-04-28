# QuantZhai / Qwen3.6Turbo Codex Local Tuning Report

**Date:** 2026-04-29
**System:** Devuan/Linux host, Docker llama.cpp TurboQuant backend, local QuantZhai proxy, Codex CLI
**Model:** `Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf`
**GPUs:** NVIDIA RTX 3080 10GB + Tesla V100-SXM2 16GB
**Goal:** Make local Codex reliably round-trip through a TurboQuant llama.cpp server at 128k context.

---

## Executive Summary

The stack now works reliably at **128k context** with Codex and tool calls.

The winning profile is:

```bash
QZ_CONTEXT=131072
QZ_PARALLEL=1
QZ_TENSOR_SPLIT=9,17
QZ_BATCH=4096
QZ_UBATCH=512
QZ_CACHE_RAM=8192
QZ_CACHE_REUSE=256
QZ_KV_KEY=q8_0
QZ_KV_VALUE=turbo3
```

The main failure was not one bug. It was a chain:

1. The proxy SSE stream sent `[DONE]` but did not close cleanly, so Codex kept reconnecting.
2. `qz-up` started the proxy even if the llama backend was not actually healthy.
3. The backend later crashed under the full Codex harness because GPU0 ran out of CUDA memory.
4. The crash produced a misleading `502 Bad Gateway` from the proxy because the upstream backend on `18084` had died.

Once the proxy stream handling, backend readiness check, and memory profile were fixed, Codex successfully created and read back `qz_roundtrip_test.txt`.

---

## Architecture

The working path is:

```text
Codex CLI
  -> QuantZhai proxy: http://127.0.0.1:18180
  -> llama.cpp TurboQuant backend: http://127.0.0.1:18084
  -> Docker container running /app/llama-server
```

Important local endpoints:

```text
Proxy health:   http://127.0.0.1:18180/health
Backend health: http://127.0.0.1:18084/health
Backend models: http://127.0.0.1:18084/v1/models
```

---

## Confirmed Fixes

### 1. Synthetic SSE Stream Must Close

A raw curl test proved the proxy generated a valid final response:

```text
event: response.output_text.delta
data: ... "delta": "synthetic-sse-smoke"

event: response.completed
data: ...

data: [DONE]
```

But curl originally did not exit until interrupted. That meant the proxy sent `[DONE]` but kept the HTTP connection open.

Fix:

- change SSE response headers from `Connection: keep-alive` to `Connection: close`
- set `self.close_connection = True` after writing the synthetic stream
- keep temporary `latest-synthetic-sse.raw` logging only while debugging

Result:

```text
data: [DONE]
curl exited:0
```

This stopped the false Codex reconnect behaviour caused by an unterminated stream.

---

### 2. `qz-up` Must Wait for Backend Health

Before the fix, `qz-up` started Docker and immediately started the proxy. If the backend crashed or failed to bind `18084`, the proxy still came up and Codex was launched into a dead upstream.

Fix added a backend wait loop after `docker run` and before `qz-proxy`:

```bash
printf 'waiting for backend: http://%s:%s/health\n' "$QZ_SERVER_HOST" "$QZ_SERVER_PORT"

backend_ok=0
for i in $(seq 1 120); do
  if curl -fsS "http://$QZ_SERVER_HOST:$QZ_SERVER_PORT/health" >/dev/null 2>&1; then
    backend_ok=1
    break
  fi

  if ! qz_docker ps --format '{{.Names}}' | grep -qx "$QZ_CONTAINER"; then
    echo "backend container exited before becoming healthy" >&2
    qz_docker logs --tail=160 "$QZ_CONTAINER" >&2 || true
    exit 1
  fi

  sleep 1
done

if [[ "$backend_ok" != 1 ]]; then
  echo "backend did not become healthy on http://$QZ_SERVER_HOST:$QZ_SERVER_PORT" >&2
  qz_docker ps -a --filter "name=^/${QZ_CONTAINER}$" >&2 || true
  qz_docker logs --tail=160 "$QZ_CONTAINER" >&2 || true
  exit 1
fi

printf 'backend healthy: http://%s:%s\n' "$QZ_SERVER_HOST" "$QZ_SERVER_PORT"
```

Successful startup now prints:

```text
waiting for backend: http://127.0.0.1:18084/health
backend healthy: http://127.0.0.1:18084
proxy listening: http://127.0.0.1:18180
```

---

### 3. Full Codex Harness Was Crashing GPU0

The actual remaining `502` was caused by backend death:

```text
CUDA error: out of memory
current device: 0
...
Exited (139)
```

The crash occurred after the backend had already processed about a 10k-token Codex harness.

The failing shape was roughly:

```bash
QZ_CONTEXT=131072
QZ_PARALLEL=2
QZ_BATCH=2048
QZ_UBATCH=512
QZ_TENSOR_SPLIT=10,16
QZ_CACHE_RAM=12288
QZ_CACHE_REUSE=256
```

The important part: **128k context itself was not the problem.**
The bad combination was 128k plus two slots, large batches, prompt cache/checkpoint pressure, and too much load on GPU0.

---

## Known-Good Final Profile

Use this as the current stable tuned profile:

```bash
QZ_CONTEXT=131072
QZ_PARALLEL=1
QZ_TENSOR_SPLIT=9,17
QZ_BATCH=4096
QZ_UBATCH=512
QZ_CACHE_RAM=8192
QZ_CACHE_REUSE=256
QZ_KV_KEY=q8_0
QZ_KV_VALUE=turbo3
```

Full `.env` core section:

```bash
QZ_SERVER_HOST=127.0.0.1
QZ_SERVER_PORT=18084
QZ_PROXY_HOST=127.0.0.1
QZ_PROXY_PORT=18180

QZ_CONTEXT=131072
QZ_PARALLEL=1
QZ_BATCH=4096
QZ_UBATCH=512
QZ_THREADS=12
QZ_THREAD_BATCH=12
QZ_TENSOR_SPLIT=9,17
QZ_MAIN_GPU=0
QZ_CACHE_RAM=8192
QZ_CACHE_REUSE=256
QZ_KV_KEY=q8_0
QZ_KV_VALUE=turbo3
```

---

## Known-Bad Settings

Do not use these as part of the 128k Codex profile:

```bash
QZ_PARALLEL=2
QZ_UBATCH=768
QZ_TENSOR_SPLIT=10,16
```

### `QZ_UBATCH=768`

This was a hard fail. The launch terminal crashed, VRAM dropped to zero, and the backend did not finish launching.

Conclusion:

```text
QZ_UBATCH=512 is the ceiling for this setup.
```

### `QZ_PARALLEL=2`

This doubles the slot pressure. Earlier logs showed:

```text
n_seq_max = 2
n_slots = 2
```

The stable profile uses:

```text
n_seq_max = 1
n_slots = 1
```

For Codex, single-slot operation is fine because the workflow is serial and tool-driven. Parallel slots are not worth the VRAM risk here.

---

## VRAM Tuning Results

The key VRAM readings from `nvtop` are below. Values are approximate but good enough for profile tuning.

| Test | Main Settings | RTX 3080 VRAM | V100 VRAM | Result |
|---|---:|---:|---:|---|
| Safer 128k baseline | `8,18`, `batch=1024`, `ubatch=256`, cache off | ~7.9 GiB | ~15.0 GiB | Worked |
| Rebalanced split | `9,17`, `batch=1024`, `ubatch=256` | 8.843 GiB | 14.054 GiB | Worked |
| Larger ubatch | `9,17`, `batch=1024`, `ubatch=384` | 9.142 GiB | 14.302 GiB | Worked |
| Larger ubatch | `9,17`, `batch=1024`, `ubatch=512` | 9.453 GiB | 14.564 GiB | Worked |
| Larger batch | `9,17`, `batch=1536`, `ubatch=512` | 9.453 GiB | 14.564 GiB | Worked |
| Larger batch | `9,17`, `batch=2048`, `ubatch=512` | 9.453 GiB | 14.564 GiB | Worked |
| Prompt cache | `cache_ram=2048`, `reuse=128` | 9.453 GiB | 14.564 GiB | Worked |
| Prompt cache | `cache_ram=4096`, `reuse=128` | 9.453 GiB | 14.564 GiB | Worked |
| Cache reuse | `cache_ram=4096`, `reuse=256` | 9.439 GiB | 14.564 GiB | Worked |
| Larger batch | `batch=3072`, `ubatch=512` | 9.455 GiB | 14.564 GiB | Worked |
| Larger batch | `batch=4096`, `ubatch=512` | 9.455 GiB | 14.564 GiB | Worked |
| Larger cache | `cache_ram=8192`, `reuse=256` | 9.455 GiB | 14.564 GiB | Worked |
| Too large ubatch | `ubatch=768` | dropped to zero | dropped to zero | Failed hard |

Takeaway:

- `QZ_UBATCH` materially affects VRAM pressure.
- `QZ_BATCH` from `1536` to `4096` did not visibly increase VRAM at `ubatch=512`.
- `QZ_CACHE_RAM` is host RAM side and did not visibly increase VRAM.
- `QZ_TENSOR_SPLIT=9,17` is a better balance than `8,18`.
- `QZ_TENSOR_SPLIT=10,16` is too risky because the 3080 only has about 500–600 MiB spare.

---

## Benchmark Results

Final tested backend configuration reported:

```text
n_seq_max     = 1
n_ctx         = 131072
n_ctx_seq     = 131072
n_batch       = 4096
n_ubatch      = 512
flash_attn    = enabled
kv_unified    = true
```

KV/cache and compute buffers:

```text
llama_kv_cache:
  CUDA0 KV buffer size = 279.00 MiB
  CUDA1 KV buffer size = 651.00 MiB
  total KV size        = 930.00 MiB

llama_memory_recurrent:
  CUDA0 RS buffer size = 25.12 MiB
  CUDA1 RS buffer size = 37.69 MiB
  total RS size        = 62.81 MiB

sched_reserve:
  CUDA0 compute buffer size = 1244.06 MiB
  CUDA1 compute buffer size = 1021.07 MiB
  CUDA_Host compute buffer  = 1032.08 MiB
```

### First Codex-Style Request

The first request processed a full Codex harness:

```text
prompt eval time = 13882.82 ms / 11343 tokens
prompt speed     = 817.05 tokens/sec

eval time        = 839.26 ms / 81 tokens
decode speed     = 96.51 tokens/sec

total time       = 14722.08 ms / 11424 tokens
```

### Second Similar Request With Cache Reuse

The second similar request restored a checkpoint:

```text
selected slot by LCP similarity, sim_best = 0.992
restored context checkpoint
```

Then only a tiny prompt delta needed evaluation:

```text
prompt eval time = 363.47 ms / 98 tokens
prompt speed     = 269.63 tokens/sec

eval time        = 169.82 ms / 17 tokens
decode speed     = 100.11 tokens/sec

total time       = 533.28 ms / 115 tokens
```

### Practical Meaning

The first request took about:

```text
14.7 seconds
```

The second similar cached request took about:

```text
0.53 seconds
```

That is roughly a **27.6× reduction in total request latency** for a similar follow-up request.

Prompt evaluation specifically dropped from:

```text
11343 tokens processed
```

to:

```text
98 tokens processed
```

That is the real win. Codex repeats a large harness and tool schema often, so prompt cache/checkpoint reuse is extremely valuable.

---

## Working Codex Roundtrip

The successful validation prompt was:

```text
Create a file called qz_roundtrip_test.txt containing the word ok, then read it back.
```

Codex successfully ran:

```bash
echo "ok" > qz_roundtrip_test.txt && cat qz_roundtrip_test.txt
```

And returned:

```text
ok
```

Final assistant response:

```text
Done. Created qz_roundtrip_test.txt with the content ok and verified it by reading it back.
```

This proves:

- Codex can talk to the proxy.
- The proxy can talk to llama.cpp.
- The model can request a tool call.
- Codex can execute the tool call.
- Tool output returns to the model.
- The final assistant message reaches Codex.
- The SSE stream closes correctly.

---

## Current Launch Shape

The backend launch is effectively:

```bash
/app/llama-server \
  -m /models/Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf \
  --alias QwenZhai \
  --host 0.0.0.0 \
  --port 8080 \
  -ngl 999 \
  -c 131072 \
  -np 1 \
  -b 4096 \
  -ub 512 \
  -t 12 \
  -tb 12 \
  -fa on \
  --split-mode layer \
  --tensor-split 9,17 \
  --main-gpu 0 \
  --kv-unified \
  --reasoning on \
  --reasoning-budget -1 \
  --cache-ram 8192 \
  --cache-reuse 256 \
  --mlock \
  -ctk q8_0 \
  -ctv turbo3 \
  --metrics \
  --reasoning-format deepseek
```

The proxy runs as:

```bash
python3 proxy/quantzhai_proxy.py \
  --listen 127.0.0.1 \
  --port 18180 \
  --upstream http://127.0.0.1:18084 \
  --reasoning-stream-format summary
```

Codex wrapper uses:

```bash
export CODEX_HOME="$QZ_ROOT/var/codex-home"
export CODEX_SQLITE_HOME="$CODEX_HOME/sqlite"
export CODEX_OSS_BASE_URL="http://$QZ_PROXY_HOST:$QZ_PROXY_PORT"
export LOCAL_QWEN_API_KEY="${LOCAL_QWEN_API_KEY:-local}"

exec codex --oss -m "$model"
```

---

## Practical Recommendations

### Keep

```bash
QZ_CONTEXT=131072
QZ_PARALLEL=1
QZ_TENSOR_SPLIT=9,17
QZ_BATCH=4096
QZ_UBATCH=512
QZ_CACHE_RAM=8192
QZ_CACHE_REUSE=256
```

### Avoid

```bash
QZ_UBATCH=768
QZ_PARALLEL=2
QZ_TENSOR_SPLIT=10,16
```

### If the Backend Dies

Check immediately, before restarting:

```bash
cd ~/turboquant/quantzhai

source scripts/qz-env

ss -ltnp | grep -E ':18180|:18084' || true
qz_docker ps -a --filter "name=^/${QZ_CONTAINER}$"
qz_docker logs --tail=160 "$QZ_CONTAINER" 2>&1 | tail -160
cat var/captures/latest-web-runtime-error.txt 2>/dev/null || true
```

Likely interpretation:

```text
18180 alive, 18084 missing:
  proxy alive, backend dead

Docker exited 139:
  likely backend crash

CUDA OOM in logs:
  reduce ubatch, parallelism, or shift split away from GPU0
```

### If Codex Reconnects

Check the SSE stream first:

```bash
cd ~/turboquant/quantzhai

grep -n "response.output_text.delta\|response.completed\|\[DONE\]" \
  var/captures/latest-synthetic-sse.raw 2>/dev/null || true
```

If `[DONE]` exists but the client hangs, confirm the proxy closes the stream.

---

## Notes on Command Style

Avoid using:

```bash
cd some/path || exit 1
```

That can kill interactive shells and PuTTY sessions.

Prefer:

```bash
cd ~/turboquant/quantzhai
pwd
```

Or, when needed:

```bash
if ! cd ~/turboquant/quantzhai; then
  echo "Directory not found"
fi
```

No booby-trapped shells. The computer is supposed to help.

---

## Bottom Line

This setup is now doing something genuinely useful:

- **Qwen3.6 35B A3B**
- **TurboQuant**
- **128k context**
- **Codex CLI**
- **local tool calls**
- **working prompt cache**
- **~817 prompt tokens/sec on first full harness**
- **~0.53 second cached follow-up request**

The insane part is not just that 128k runs. It is that after the first Codex-style request, the server can reuse almost the entire prompt and turn a similar 11k-token request into a sub-second operation.

That is the value of this run.
