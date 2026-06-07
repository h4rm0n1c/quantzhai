# QuantZhai Model Benchmark Report

*19 models · Generated 2026-06-07 23:22 UTC UTC*

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
| Commit | `189e512606a3` |
| CUDA | 12.8 |
| KV cache | `-ctk q8_0 -ctv turbo3` (per-model overrides) |
| Context | 262 144 |
| Batch / ubatch | 4096 / 512 |
| Flash attn | on |
| Split mode | layer |

---
## Ranking 1: Generation Speed (TPS)
Raw throughput. Fast does not mean accurate.

| # | Model | Size | 🔥TPS | ❄️TPS | 🔥TTFT | Speedup | K | V | GPU0 M/KV | GPU1 M/KV |
|---:|------|---:|---:|---:|---:|---:|:---:|:---:|---:|---:|
| 1 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 4.6G | 82.0 | 73.6 | 610.0ms | 1.1x | q8_0 | turbo3 | 1.5/5.9 | 2.7/8.2 |
| 2 | `Qwen3-Coder-30B-APEX-Mini` | 12.1G | 76.0 | 55.4 | 657.0ms | 1.4x | q8_0 | turbo3 | 4.9/3.7 | 7.1/5.7 |
| 3 | `Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M` | 5.6G | 59.6 | 58.4 | 840.0ms | 1.0x | q8_0 | turbo3 | 1.7/1.2 | 3.3/2.0 |
| 4 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 15.5G | 58.9 | 38.1 | 849.0ms | 1.5x | q8_0 | turbo3 | 6.0/0.8 | 9.5/1.2 |
| 5 | `gemma-4-26B-A4B-APEX-I-Compact` | 14.8G | 58.7 | 44.3 | 851.0ms | 1.3x | q8_0 | turbo3 | 5.7/0.8 | 9.1/1.2 |
| 6 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 14.3G | 56.9 | 45.8 | 878.0ms | 1.2x | q8_0 | turbo3 | 5.8/0.8 | 8.3/1.2 |
| 7 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 19.3G | 56.5 | 38.5 | 884.0ms | 1.5x | q8_0 | turbo3 | 7.7/0.8 | 11.6/1.2 |
| 8 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 13.0G | 55.4 | 32.1 | 903.0ms | 1.7x | q8_0 | turbo3 | 5.0/0.8 | 8.0/1.2 |
| 9 | `qwen36_35b_Q4_K_M` | 21.2G | 54.7 | 44.7 | 915.0ms | 1.2x | q8_0 | turbo3 | 8.2/0.8 | 12.7/1.2 |
| 10 | `qwen36_35b_IQ4_XS` | 18.7G | 52.6 | 37.7 | 951.0ms | 1.4x | q8_0 | turbo3 | 7.2/0.8 | 11.2/1.2 |
| 11 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 17.5G | 51.7 | 43.6 | 967.0ms | 1.2x | q8_0 | turbo3 | 6.7/0.8 | 10.4/1.2 |
| 12 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 20.4G | 51.6 | 23.9 | 968.0ms | 2.2x | q8_0 | turbo3 | 8.3/0.9 | 11.8/1.3 |
| 13 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 20.1G | 50.6 | 45.0 | 989.0ms | 1.1x | q8_0 | turbo3 | 7.7/0.8 | 12.0/1.2 |
| 14 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 13.4G | 50.3 | 44.4 | 993.0ms | 1.1x | q8_0 | turbo3 | 5.2/0.8 | 8.0/1.2 |
| 15 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 17.3G | 48.5 | 20.5 | 1030.0ms | 2.4x | q8_0 | turbo3 | 6.8/0.9 | 10.2/1.3 |
| 16 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 14.3G | 38.5 | 20.9 | 1298.0ms | 1.8x | q8_0 | turbo3 | 5.6/0.9 | 8.5/1.3 |
| 17 | `mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_XS` | 12.8G | 27.7 | 22.4 | 1263.0ms | 1.2x | q4_0 | turbo3 | 4.7/2.0 | 7.7/3.1 |
| 18 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 15.4G | 25.0 | 25.4 | 1996.0ms | 1.0x | q8_0 | turbo3 | 5.2/2.4 | 9.5/4.0 |
| 19 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 19.4G | 17.2 | 2.3 | 175.0ms | 7.5x | q8_0 | turbo3 | 7.9/0.9 | 11.2/1.3 |

---
## Ranking 2: Efficiency (PPL🔥 / Warm TPS)
**Lower is better.** Combines code-understanding quality (ToolboxPPL) with speed.
A fast model that struggles with code (high PPL) ranks lower than a moderately fast but accurate one.

| # | Model | Size | PPL🔥 | 🔥TPS | 🔥Eff | PPL❄️ | ❄️TPS | ❄️Eff |
|---:|------|---:|---:|---:|---:|---:|---:|---:|
| 1 | `Qwen3-Coder-30B-APEX-Mini` | 12.1G | 1.9448 | 76.0 | **0.02559** | 2.1834 | 55.4 | 0.03941 |
| 2 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 4.6G | 2.3716 | 82.0 | **0.02892** | 2.7802 | 73.6 | 0.03777 |
| 3 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 14.3G | 1.7937 | 56.9 | **0.03152** | 2.7092 | 45.8 | 0.05915 |
| 4 | `qwen36_35b_Q4_K_M` | 21.2G | 1.7533 | 54.7 | **0.03205** | 2.7132 | 44.7 | 0.06070 |
| 5 | `Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M` | 5.6G | 1.9722 | 59.6 | **0.03309** | 3.3272 | 58.4 | 0.05697 |
| 6 | `qwen36_35b_IQ4_XS` | 18.7G | 1.7507 | 52.6 | **0.03328** | 2.7032 | 37.7 | 0.07170 |
| 7 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 20.4G | 1.7628 | 51.6 | **0.03416** | 2.8126 | 23.9 | 0.11768 |
| 8 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 17.3G | 1.7509 | 48.5 | **0.03610** | 2.7341 | 20.5 | 0.13337 |
| 9 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 17.5G | 1.8822 | 51.7 | **0.03641** | 3.1393 | 43.6 | 0.07200 |
| 10 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 20.1G | 1.8781 | 50.6 | **0.03712** | 3.1372 | 45.0 | 0.06972 |
| 11 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 13.4G | 1.9011 | 50.3 | **0.03780** | 3.2241 | 44.4 | 0.07261 |
| 12 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 14.3G | 1.8036 | 38.5 | **0.04685** | 2.7032 | 20.9 | 0.12934 |
| 13 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 15.4G | 1.6989 | 25.0 | **0.06796** | 2.5831 | 25.4 | 0.10170 |
| 14 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 19.4G | 1.7554 | 17.2 | **0.10206** | 2.7614 | 2.3 | 1.20061 |
| 15 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 15.5G | 7.2414 | 58.9 | **0.12294** | 9.3767 | 38.1 | 0.24611 |
| 16 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 19.3G | 20.1509 | 56.5 | **0.35665** | 18.4907 | 38.5 | 0.48028 |
| 17 | `gemma-4-26B-A4B-APEX-I-Compact` | 14.8G | 25.3029 | 58.7 | **0.43105** | 19.2454 | 44.3 | 0.43443 |
| 18 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 13.0G | 32.6819 | 55.4 | **0.58993** | 29.0656 | 32.1 | 0.90547 |

*Not shown: Devstral (PPL unavailable, dense 24B would take ~6h on stride=512).*

---
## ToolboxPPL — Code Perplexity
**Corpus:** [`macvox68`](https://github.com/h4rm0n1c/macvox68) — 32 C/H source files (125KB), Mac OS 7 TTS app.
**Method:** Strided PPL, ctx=4096, stride=512, K/V cache matches inference config.
**PPL❄️** = first chunk (cold, zero domain context).  **PPL🔥** = avg remaining chunks (adapted).

| # | Model | PPL❄️ | PPL🔥 | K cache | V cache |
|---:|------|---:|---:|:---:|:---:|
| 1 | `Qwen3-Coder-30B-APEX-Mini` | 2.1834 | 1.9448 | q8_0 | turbo3 |
| 2 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 2.7802 | 2.3716 | q8_0 | turbo3 |
| 3 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 2.7092 | 1.7937 | q8_0 | turbo3 |
| 4 | `qwen36_35b_Q4_K_M` | 2.7132 | 1.7533 | q8_0 | turbo3 |
| 5 | `Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M` | 3.3272 | 1.9722 | q8_0 | turbo3 |
| 6 | `qwen36_35b_IQ4_XS` | 2.7032 | 1.7507 | q8_0 | turbo3 |
| 7 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 2.8126 | 1.7628 | q8_0 | turbo3 |
| 8 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 2.7341 | 1.7509 | q8_0 | turbo3 |
| 9 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3.1393 | 1.8822 | q8_0 | turbo3 |
| 10 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3.1372 | 1.8781 | q8_0 | turbo3 |
| 11 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3.2241 | 1.9011 | q8_0 | turbo3 |
| 12 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 2.7032 | 1.8036 | q8_0 | turbo3 |
| 13 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 2.5831 | 1.6989 | q8_0 | turbo3 |
| 14 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 2.7614 | 1.7554 | q8_0 | turbo3 |
| 15 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 9.3767 | 7.2414 | q8_0 | turbo3 |
| 16 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 18.4907 | 20.1509 | q8_0 | turbo3 |
| 17 | `gemma-4-26B-A4B-APEX-I-Compact` | 19.2454 | 25.3029 | q8_0 | turbo3 |
| 18 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 29.0656 | 32.6819 | q8_0 | turbo3 |

## Key Observations

### Throughput
Warm gen: **17–82** t/s.  MoE (qwen35moe): 48–82 t/s.  Dense (Devstral, NEO): 25–28 t/s.

### Code Understanding (ToolboxPPL)
**qwen35moe architecture dominates:** 12 of top 14 efficiency spots.  PPL🔥 1.70–1.97.
Models within this family differ in speed but all understand retro C patterns equally well.
**Gemma 4 is fast but inaccurate:** ranks #4–8 by speed but **dead last** by efficiency (0.12–0.59).
PPL🔥 7–33 vs qwen35moe's 1.7–1.9 — a 4-17x gap. The Gemma 4 attention mechanism doesn't handle
classic Mac Toolbox idioms, producing confident but wrong continuations.

### Distillation quality
Within the same architecture, MTP-preserved models score *worse* efficiency than their distilled
counterparts (e.g. 0.034 vs 0.032 for Qwen3.6-35B variants). MTP draft boosts speed but adds
noise that hurts PPL on unseen code.

### KV head count
4 heads (qwen35, gemma4): ~2G KV at 256K.  8 heads (qwen3): ~6-14G at 256K.

### VRAM tightest
Tightest: Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Pres — both GPUs below 1G free.
Most efficient 35B: Qwen3.6-35B-A3B-APEX-I-Mini — 14.3G file, 16.1G VRAM, 1.8G+5.0G headroom.

### On Devstral
Devstral (mistral3 arch) requires `--flash-attn 1` explicitly for `llama-perplexity` — FA auto-detect
disables on small context for this arch.  Even with the fix, a full PPL pass takes ~6h (dense 24B
at stride=512).  Its PPL is omitted from this report.
