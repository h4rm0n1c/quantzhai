# QuantZhai Model Benchmark Report

*Generated: 2026-06-05 21:23:08 UTC*

## Hardware

| Item | Detail |
|---|---|
| GPU 0 | NVIDIA GeForce RTX 3080 (10 GB) |
| GPU 1 | Tesla V100-SXM2-16GB (16 GB) |
| CPU | Intel Xeon E5-2673 v2 @ 3.30GHz (48 GB RAM) |
| NVLink | No (host-staged fallback) |
| Tensor split | 10,16 |
| Split mode | layer |

## Engine

| Parameter | Value |
|---|---|
| Engine | llama.cpp (turboquant fork) |
| CUDA | 12.8 |
| Flash attention | on |
| KV cache K | q8_0 |
| KV cache V | turbo3 |
| Cache RAM | 4096 MiB (bench), 8192 MiB (production) |
| Context | 131072 (bench), 262144 (production) |
| Batch / ubatch | 4096 / 512 |
| Threads | 12 / 12 |
| Mlock | yes |
| Spec decode | default / draft-mtp |

## All Models — Direct Benchmark (131k ctx, no router)

| # | Model | Size | Params | Prompt t/s | Gen t/s | Type |
|---|---|---:|---:|---:|---:|:---|
| 1 | `Qwen3-Coder-30B-APEX-Mini` | 12.1 GB | ~30B | 28 | 89 | Medium |
| 2 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.IQ4_XS` | 13.4 GB | ~24B | 16 | 83 | Medium |
| 3 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.Q5_K_M` | 17.5 GB | ~24B | 16 | 84 | Large |
| 4 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.Q6_K` | 20.1 GB | ~24B | 11 | 76 | Large |
| 5 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 15.4 GB | ~27B | 17 | 36 | Large |
| 6 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 14.3 GB | ~35B | 14 | 82 | Large |
| 7 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact` | 17.3 GB | ~35B | 10 | 82 | Large |
| 8 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Mini` | 14.3 GB | ~35B | 15 | 84 | Large |
| 9 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS` | 19.4 GB | ~35B | 17 | 88 | Large |
| 10 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S` | 20.4 GB | ~35B | 11 | 90 | Large |
| 11 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 4.6 GB | 5B | 102 | 104 | Small |
| 12 | `gemma-4-26B-A4B-APEX-I-Compact` | 14.8 GB | ~26B | 30 | 77 | Large |
| 13 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 15.5 GB | ~26B | 59 | 81 | Large |
| 14 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 13.0 GB | ~26B | 56 | 74 | Medium |
| 15 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 19.3 GB | ~26B | 52 | 76 | Large |
| 16 | `mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_XS` | 12.8 GB | ~24B | 83 | 46 | Medium |
| 17 | `qwen36_35b_IQ4_XS` | 18.7 GB | 19B | 8 | 86 | Large |
| 18 | `qwen36_35b_Q4_K_M` | 21.2 GB | 21B | 19 | 82 | Large |

## VRAM — Router Mode (262k ctx)

| Model | GPU | Total | Model | KV Cache | Free |
|---|---:|---:|---:|---:|---:|
| `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | RTX 3080 | 8.9G | 1.6G | 5.8G | 1.5G |
| `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | V100 | 15.1G | 2.7G | 8.2G | 4.3G |
| `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | **Params** | **8.2B** | **Context** | **262,144** | |
| `qwen36_35b_IQ4_XS` | RTX 3080 | 8.9G | 7.2G | 0.8G | 0.9G |
| `qwen36_35b_IQ4_XS` | V100 | 14.9G | 11.2G | 1.2G | 2.5G |
| `qwen36_35b_IQ4_XS` | **Params** | **34.7B** | **Context** | **262,144** | |

## Key Observations

### MoE vs Dense generation TPS
MoE models (Qwen3.5/3.6, Gemma 4) show 76-90 t/s generation vs.
45-46 t/s for dense (Devstral) at comparable sizes — only a subset
of parameters activates per token.  Prompt processing is slower on MoE.

### Quantisation impact
Higher quantisation (Q5_K_M → Q6_K, IQ4_XS → Q4_K_S) has minimal
impact on generation TPS — bottleneck is cross-GPU transfers, not compute.

### Router CUDA context leak (fixed)
The router no longer calls `llama_backend_init()`, saving ~3.5 GB of
GPU memory.  Previously, models ≥24B would OOM on KV allocation; now
35B MoE models load successfully at 262k context.

### Non-NVLink host-staged fallback
All cross-GPU copies use the pinned staging pool with batched sync.
No performance floor observed — 103.9 t/s on the 8B DeepSeek proves
the fallback is not a bottleneck at this scale.

### Unload/reload race (fixed)
`load()` now waits for the model to reach UNLOADED before proceeding,
eliminating a silent no-op when load is called before unload completes.

### Router life-cycle (dining philosophers fix)
Dedicated `stop_mutex` prevents cv_stop/update_status contention that
caused permanent lifecycle thread stalls on child process death.

### Models that don't fit through router
At 262k context, dense models ≥24B (Devstral) OOM on KV allocation on
the RTX 3080.  This is a hardware limit — 10 GB is insufficient for
both model weights (~5 GB) and KV cache (~6 GB) at full context.
MoE models fit because their KV head dimension is smaller.

## Model Loading Status (Router Mode)

| Model | Status | Notes |
|---|---|---|
| 8B DeepSeek | ✅ Loaded, 109 t/s | Production daily driver |
| 35B MoE (qwen36_35b, APEX, Claude) | ✅ Loaded, 92 t/s | Full context fits |
| Gemma 4 26B | ✅ Loaded | Variants tested |
| Devstral 24B | ❌ OOM on KV | Needs smaller context or tensor split adjustment |
| 30B Coder | ❌ OOM on KV | Same issue — dense model |
| 35B uncensored Q4_K_S | ❌ OOM | 20 GB file exceeds GPU pool |

## PPL / KLD
Perplexity evaluation requires building `tools/perplexity` with
`LLAMA_BUILD_TESTS=ON`.  Not available in the current Docker image.
This is a future addition — the server exposes no PPL endpoint.
