# QuantZhai Model Compatibility Matrix

**Hardware:** RTX 3080 (10 GB) + V100-SXM2 (16 GB), no NVLink
**Backend:** llama.cpp turboquant fork, CUDA 12.8, `--tensor-split 10,16 --split-mode layer`
**KV:** `-ctk q8_0 -ctv turbo3 --kv-unified --cache-ram 4096` at 131072 ctx
**Benchmark:** 5-token prompt, 50-token generation, temp=0

> All models compiled against `main` @ [`0a77eb7f`](https://github.com/h4rm0n1c/llama-cpp-turboquant-zhai/tree/0a77eb7f0cfec7db98ea5756eeb5def6b5e0225a)
> with the pinned staging pool for non-NVLink cross-GPU copies.

## Full Benchmark

| # | Model | File | Params | Prompt | Gen | Notes |
|---|---|---|---|---|---|---|
| 1 | DeepSeek R1 0528 Qwen3 8B | `IQ4_XS` | 8.2B | **102.0** t/s | **103.9** t/s | Single-GPU capable, fastest |
| 2 | Devstral-Small-2-24B | `IQ4_XS` | 24.2B | 83.4 t/s | 45.6 t/s | Fast prompt, slow gen (dense arch) |
| 3 | Qwen3-Coder-30B-APEX-Mini | — | ~30B | 27.8 t/s | 88.9 t/s | |
| 4 | Qwen3.5-24B Claude Distill | `IQ4_XS` | ~24B | 16.0 t/s | 83.0 t/s | |
| 5 | Qwen3.5-24B Claude Distill | `Q5_K_M` | ~24B | 15.8 t/s | 83.8 t/s | |
| 6 | Qwen3.5-24B Claude Distill | `Q6_K` | ~24B | 10.7 t/s | 76.2 t/s | |
| 7 | Qwen3.6-27B-NEO-CODE | `IQ4_XS` | ~27B | 17.2 t/s | 36.1 t/s | Slow gen — dense arch |
| 8 | Qwen3.6-35B-A3B-APEX-I-Mini | — | ~35B | 14.5 t/s | 82.0 t/s | |
| 9 | Qwen3.6-35B Claude 4.7 MTP-I-Mini | — | ~35B | 15.3 t/s | 83.9 t/s | |
| 10 | Qwen3.6-35B Claude 4.7 MTP-I-Compact | — | ~35B | 10.0 t/s | 81.5 t/s | |
| 11 | Qwen3.6-35B uncensored Native-MTP | `IQ4_XS` | ~35B | 17.0 t/s | 87.7 t/s | |
| 12 | Qwen3.6-35B uncensored Native-MTP | `Q4_K_S` | ~35B | 10.7 t/s | 90.3 t/s | |
| 13 | qwen36_35b | `IQ4_XS` | ~35B | 8.4 t/s | 85.9 t/s | |
| 14 | qwen36_35b | `Q4_K_M` | ~35B | 18.8 t/s | 81.6 t/s | |
| 15 | gemma-4-26B-A4B | `APEX-I-Compact` | ~26B | 29.5 t/s | 76.7 t/s | |
| 16 | gemma-4-26B-A4B Claude Distill | `APEX-I-Compact` | ~26B | 58.6 t/s | 81.1 t/s | |
| 17 | google_gemma-4-26B-A4B | `Q3_K_M` | ~26B | 56.0 t/s | 73.9 t/s | |
| 18 | google_gemma-4-26B-A4B | `Q5_K_M` | ~26B | 52.0 t/s | 75.8 t/s | |

## VRAM Snapshot (Reference: 8B DeepSeek @ 131072 ctx)

Measured from production router-mode load with `--cache-ram 8192` at 262144 ctx:

| GPU | Total | Model | KV Cache | Arena | Free |
|---|---|---|---|---|---|
| RTX 3080 | 10.35 GB | 1.55 GB | 5.85 GB | 1.21 GB | 1.47 GB |
| V100-SXM2 | 16.93 GB | 2.68 GB | 8.19 GB | 1.43 GB | 4.26 GB |

VRAM for larger models scales proportionally with model size per GPU. The 35B models (14–20 GB files) consume approximately:
- RTX 3080: ~4–6 GB model + ~4 GB KV
- V100: ~6–10 GB model + ~6 GB KV

## Key Patterns

### MoE (Qwen3.5/3.6, Gemma 4) vs Dense (Devstral)
MoE models show higher generation TPS (76–90) than dense (45–46) at comparable sizes, because only a subset of parameters activates per token. Prompt processing is generally slower on MoE for the same reason.

### Quantisation effect
Higher quantisation (Q5_K_M → Q6_K, or IQ4_XS → Q4_K_S) increases file size and loading time but has minimal impact on generation TPS — the generation is bottlenecked by cross-GPU transfers, not compute.

### Gen TPS floor
All models sustain at least 36 t/s generation (worst: Qwen3.6-27B-NEO-CODE dense). The cross-GPU host-staged fallback does not introduce a perceptible bottleneck at this scale.

### All models fit
Every model in the catalog loaded successfully at 131072 ctx with `--cache-ram 4096`. No OOM failures in this benchmark round.
