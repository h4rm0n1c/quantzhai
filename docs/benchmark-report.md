# QuantZhai Model Benchmark Report

*15 models · Generated 2026-06-05 18:33:13 UTC*

## Hardware
| GPU | Memory | Tensor split |
|---|---|---|
| NVIDIA GeForce RTX 3080 | 10 GB | GPU 0 (main), 10/26 |
| Tesla V100-SXM2-16GB | 16 GB | GPU 1, 16/26 |
| CPU | Intel Xeon E5-2673 v2 | 48 GB RAM |
| Interconnect | No NVLink | Host-staged fallback |

## Engine
| Setting | Value |
|---|---|
| Engine | llama.cpp (turboquant fork) |
| Commit | `73996836cb34` |
| CUDA | 12.8 |
| KV cache | `-ctk q8_0 -ctv turbo3` |
| Context | 262 144 |
| Batch / ubatch | 4096 / 512 |
| Flash attn | on |
| Spec decode | draft-mtp / default |

## All Models — sorted by warm generation TPS

| # | Model (GGUF filename) | Size | Params | 🔥 Gen t/s | ❄️ Gen t/s | 🔥 TTFT | ❄️ TTFT | Speedup | Cached | GPU0 model | GPU0 KV | GPU1 model | GPU1 KV |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 4.6G | 8.2B | 82 | 83 | 246ms | 693ms | 3x | 1843 | 2G | 6G | 3G | 8G |
| 2 | `Qwen3-Coder-30B-APEX-Mini` | 12.1G | 30.5B | 72 | 35 | 240ms | 11210ms | 47x | 11435 | 5G | 4G | 7G | 6G |
| 3 | `gemma-4-26B-A4B-APEX-I-Compact` | 14.8G | 25.2B | 70 | 36 | 221ms | 6228ms | 28x | 10580 | 6G | 1G | 9G | 1G |
| 4 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 19.3G | 25.2B | 69 | 40 | 225ms | 6398ms | 28x | 10580 | 8G | 1G | 12G | 1G |
| 5 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 13.0G | 25.2B | 68 | 31 | 228ms | 6523ms | 29x | 10580 | 5G | 1G | 8G | 1G |
| 6 | `qwen36_35b_IQ4_XS` | 18.7G | 34.7B | 66 | 66 | 239ms | 12527ms | 52x | 11063 | 7G | 1G | 11G | 1G |
| 7 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 17.5G | 24.5B | 65 | 62 | 239ms | 11286ms | 47x | 11063 | 7G | 1G | 10G | 1G |
| 8 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 13.4G | 24.5B | 65 | 62 | 240ms | 10200ms | 42x | 11063 | 5G | 1G | 8G | 1G |
| 9 | `qwen36_35b_Q4_K_M` | 21.2G | 34.7B | 64 | 66 | 232ms | 13080ms | 56x | 11063 | 8G | 1G | 13G | 1G |
| 10 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 20.1G | 24.5B | 63 | 52 | 234ms | 12777ms | 55x | 11063 | 8G | 1G | 12G | 1G |
| 11 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 19.4G | 35.5B | 63 | 18 | 305ms | 13446ms | 44x | 11063 | 8G | 1G | 11G | 1G |
| 12 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 17.3G | 35.5B | 60 | 38 | 272ms | 16651ms | 61x | 11063 | 7G | 1G | 10G | 1G |
| 13 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 14.3G | 35.5B | 48 | 50 | 356ms | 14874ms | 42x | 11063 | 6G | 1G | 8G | 1G |
| 14 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 15.4G | 26.9B | 42 | 30 | 321ms | 12617ms | 39x | 11063 | 5G | 2G | 9G | 4G |
| 15 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 20.4G | 35.5B | 42 | 8 | 333ms | 16693ms | 50x | 11063 | 8G | 1G | 12G | 1G |

## Key Observations

### Prompt cache
Warm TTFT is ~220–360 ms vs ~6–17 s cold — a **28–61×** speedup. The Codex system prompt (≈11 000 tokens) is fully cached after the first request.

### MoE vs dense
MoE models (Qwen3.5/3.6, Gemma 4) generate at **48–72 t/s** regardless of parameter count. Dense models (NEO-CODE) are slower at ~42 t/s.

### GPU pressure
The RTX 3080 (10 GB) is the bottleneck — most models leave <1 GB free. The V100 (16 GB) has 2–6 GB headroom.

### Benchmark method
All numbers captured through `qz-codex exec` (real user path), not raw API. The ~11 K token Codex system prompt inflates cold TTFT and prefill counts. Gen TPS reflects real agentic usage, not peak lab throughput.