# QuantZhai Model Benchmark Report

*19 models · Generated 2026-06-07 22:31 UTC UTC*

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

## All Models — sorted by warm generation TPS

| # | Model | Size | 🔥Gen | ❄️Gen | 🔥TTFT | ❄️TTFT | Speedup | Cached | K | V | PPL❄️ | PPL🔥 | GPU0 M/KV | GPU1 M/KV |
|---:|------|---:|---:|---:|---:|---:|---:|---:|:---:|:---:|---:|---:|---:|---:|
| 1 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 4.6G | 82.0 | 73.6 | 610.0ms | 680.0ms | 1.1x | 22 | q8_0 | turbo3 | 2.7802 | 2.3716 | 1.5/5.9 | 2.7/8.2 |
| 2 | `Qwen3-Coder-30B-APEX-Mini` | 12.1G | 76.0 | 55.4 | 657.0ms | 903.0ms | 1.4x | 34 | q8_0 | turbo3 | 2.1834 | 1.9448 | 4.9/3.7 | 7.1/5.7 |
| 3 | `Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M` | 5.6G | 59.6 | 58.4 | 840.0ms | 856.0ms | 1.0x | 23 | q8_0 | turbo3 | 3.3272 | 1.9722 | 1.7/1.2 | 3.3/2.0 |
| 4 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 15.5G | 58.9 | 38.1 | 849.0ms | 1313.0ms | 1.5x | 22 | q8_0 | turbo3 | 9.3767 | 7.2414 | 6.0/0.8 | 9.5/1.2 |
| 5 | `gemma-4-26B-A4B-APEX-I-Compact` | 14.8G | 58.7 | 44.3 | 851.0ms | 1128.0ms | 1.3x | 22 | q8_0 | turbo3 | 19.2454 | 25.3029 | 5.7/0.8 | 9.1/1.2 |
| 6 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 14.3G | 56.9 | 45.8 | 878.0ms | 1091.0ms | 1.2x | 22 | q8_0 | turbo3 | 2.7092 | 1.7937 | 5.8/0.8 | 8.3/1.2 |
| 7 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 19.3G | 56.5 | 38.5 | 884.0ms | 1300.0ms | 1.5x | 23 | q8_0 | turbo3 | 18.4907 | 20.1509 | 7.7/0.8 | 11.6/1.2 |
| 8 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 13.0G | 55.4 | 32.1 | 903.0ms | 1560.0ms | 1.7x | 23 | q8_0 | turbo3 | 29.0656 | 32.6819 | 5.0/0.8 | 8.0/1.2 |
| 9 | `qwen36_35b_Q4_K_M` | 21.2G | 54.7 | 44.7 | 915.0ms | 1119.0ms | 1.2x | 23 | q8_0 | turbo3 | 2.7132 | 1.7533 | 8.2/0.8 | 12.7/1.2 |
| 10 | `qwen36_35b_IQ4_XS` | 18.7G | 52.6 | 37.7 | 951.0ms | 1326.0ms | 1.4x | 20 | q8_0 | turbo3 | 2.7032 | 1.7507 | 7.2/0.8 | 11.2/1.2 |
| 11 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 17.5G | 51.7 | 43.6 | 967.0ms | 1146.0ms | 1.2x | 22 | q8_0 | turbo3 | 3.1393 | 1.8822 | 6.7/0.8 | 10.4/1.2 |
| 12 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 20.4G | 51.6 | 23.9 | 968.0ms | 2095.0ms | 2.2x | 23 | q8_0 | turbo3 | 2.8126 | 1.7628 | 8.3/0.9 | 11.8/1.3 |
| 13 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 20.1G | 50.6 | 45.0 | 989.0ms | 1112.0ms | 1.1x | 22 | q8_0 | turbo3 | 3.1372 | 1.8781 | 7.7/0.8 | 12.0/1.2 |
| 14 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 13.4G | 50.3 | 44.4 | 993.0ms | 1127.0ms | 1.1x | 22 | q8_0 | turbo3 | 3.2241 | 1.9011 | 5.2/0.8 | 8.0/1.2 |
| 15 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 17.3G | 48.5 | 20.5 | 1030.0ms | 2440.0ms | 2.4x | 22 | q8_0 | turbo3 | 2.7341 | 1.7509 | 6.8/0.9 | 10.2/1.3 |
| 16 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 14.3G | 38.5 | 20.9 | 1298.0ms | 2398.0ms | 1.8x | 22 | q8_0 | turbo3 | 2.7032 | 1.8036 | 5.6/0.9 | 8.5/1.3 |
| 17 | `mistralai_Devstral-Small-2-24B-Instruct-2512-IQ4_XS` | 12.8G | 27.7 | 22.4 | 1263.0ms | 1565.0ms | 1.2x | 24 | q4_0 | turbo3 | ? | ? | 4.7/2.0 | 7.7/3.1 |
| 18 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 15.4G | 25.0 | 25.4 | 1996.0ms | 1966.0ms | 1.0x | 20 | q8_0 | turbo3 | 2.5831 | 1.6989 | 5.2/2.4 | 9.5/4.0 |
| 19 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 19.4G | 17.2 | 2.3 | 175.0ms | 1285.0ms | 7.5x | 20 | q8_0 | turbo3 | 2.7614 | 1.7554 | 7.9/0.9 | 11.2/1.3 |

## ToolboxPPL — Code Perplexity on Retro Mac OS C

**Corpus:** [`macvox68`](https://github.com/h4rm0n1c/macvox68) — C-based TTS for Mac OS 7.
32 `.c`/`.h` files (125KB) — cooperative-multitasking 68k/PowerPC Toolbox APIs.
**Method:** Strided PPL, ctx=4096, stride=512, K/V cache types match inference.

## Key Observations

### Performance
Warm gen: **17–82** t/s.  MoE models (qwen35moe) hit 48–82 t/s.
Dense models (Devstral 24B, NEO-CODE 27B) slower at 25–28 t/s.

### ToolboxPPL
qwen35moe arch models score best (PPL🔥 1.70–1.97) — genuinely understand retro C patterns.
Gemma 4 struggles (PPL🔥 7–33) — attention mechanism doesn't handle classic Mac Toolbox idioms.

### KV head count
**4 heads** (qwen35, gemma4): ~2G KV at 256K — fits any model.
**8 heads** (qwen3): ~6–14G KV at 256K — tight fit, limits context on smaller GPUs.

### VRAM
Tightest: Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Pres — <1G free on both GPUs.
Most efficient 35B: Qwen3.6-35B-A3B-APEX-I-Mini — 14.3G file, 16.1G VRAM, 1.8G+5.0G headroom.
