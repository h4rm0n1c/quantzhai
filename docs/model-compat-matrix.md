# QuantZhai Model Compatibility Matrix

**Hardware:** RTX 3080 (10 GB) + V100-SXM2 (16 GB), no NVLink
**Backend:** llama.cpp turboquant fork, CUDA 12.8, `--tensor-split 10,16 --split-mode layer`
**KV:** `-ctk q8_0 -ctv turbo3 --kv-unified --cache-ram 4096` at 131072 ctx
**Benchmark:** 5-token prompt, 50-token generation, temp=0
**Benchmark script:** `scripts/qz-bench-telemetry`

> All models compiled against `main` @ [`0a77eb7f`](https://github.com/h4rm0n1c/llama-cpp-turboquant-zhai/tree/0a77eb7f0cfec7db98ea5756eeb5def6b5e0225a)
> with the pinned staging pool for non-NVLink cross-GPU copies.  
> Use `qz-bench-telemetry --prompt "..." --max-tokens 200 --output result.json` to run your own.

## GGUF Files (all unique, no symlinks)

| # | Filename | Params | Prompt t/s | Gen t/s | Notes |
|---|---|---|---|---|---|
| 1 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS.gguf` | 8.2B | 102.0 | 103.9 | Single-GPU capable, fastest |
| 2 | `mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_XS.gguf` | 24.2B | 83.4 | 45.6 | Fast prompt, slow gen (dense) |
| 3 | `Qwen3-Coder-30B-APEX-Mini.gguf` | ~30B | 27.8 | 88.9 | |
| 4 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.IQ4_XS.gguf` | ~24B | 16.0 | 83.0 | |
| 5 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.Q5_K_M.gguf` | ~24B | 15.8 | 83.8 | |
| 6 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic.Q6_K.gguf` | ~24B | 10.7 | 76.2 | |
| 7 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf` | ~27B | 17.2 | 36.1 | Slow gen — dense arch |
| 8 | `Qwen3.6-35B-A3B-APEX-I-Mini.gguf` | ~35B | 14.5 | 82.0 | |
| 9 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Mini.gguf` | ~35B | 15.3 | 83.9 | |
| 10 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf` | ~35B | 10.0 | 81.5 | |
| 11 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS.gguf` | ~35B | 17.0 | 87.7 | |
| 12 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S.gguf` | ~35B | 10.7 | 90.3 | |
| 13 | `qwen36_35b_IQ4_XS.gguf` | ~35B | 8.4 | 85.9 | |
| 14 | `qwen36_35b_Q4_K_M.gguf` | ~35B | 18.8 | 81.6 | |
| 15 | `gemma-4-26B-A4B-APEX-I-Compact.gguf` | ~26B | 29.5 | 76.7 | |
| 16 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact.gguf` | ~26B | 58.6 | 81.1 | |
| 17 | `google_gemma-4-26B-A4B-it-Q3_K_M.gguf` | ~26B | 56.0 | 73.9 | |
| 18 | `google_gemma-4-26B-A4B-it-Q5_K_M.gguf` | ~26B | 52.0 | 75.8 | |

## VRAM Snapshot (Reference: 8B DeepSeek @ 262144 ctx)

Measured from production router-mode load with `--cache-ram 8192`:

| GPU | Total | Model | KV Cache | Arena | Free |
|---|---|---|---|---|---|
| RTX 3080 | 10.35 GB | 1.55 GB | 5.85 GB | 1.21 GB | 1.47 GB |
| V100-SXM2 | 16.93 GB | 2.68 GB | 8.19 GB | 1.43 GB | 4.26 GB |

VRAM for larger models scales proportionally with model size per GPU. The 35B models (14-20 GB files) consume approximately:
- RTX 3080: ~4-6 GB model + ~4 GB KV
- V100: ~6-10 GB model + ~6 GB KV

## Key Patterns

### MoE vs Dense
MoE models (Qwen3.5/3.6, Gemma 4) show higher generation TPS (76-90) than dense (45-46) at comparable sizes, because only a subset of parameters activates per token. Prompt processing is generally slower on MoE.

### Quantisation Effect
Higher quantisation (Q5_K_M → Q6_K, IQ4_XS → Q4_K_S) increases file size and loading time but has minimal impact on generation TPS - the bottleneck is cross-GPU transfers, not compute.

### Gen TPS Floor
All models sustain at least 36 t/s generation. The cross-GPU host-staged fallback does not introduce a perceptible bottleneck at this scale.

### All Models Fit
Every model in the catalog loaded successfully at 131072 ctx with `--cache-ram 4096`. No OOM failures in this benchmark round.
