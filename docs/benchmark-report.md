# QuantZhai Full Model Benchmark Report

*Generated: 2026-06-05 17:50:37 UTC*
*15 models benchmarked*

## Hardware
| GPU | Memory | Role |
|---|---|---|
| NVIDIA GeForce RTX 3080 | 10 GB | GPU 0 (main) |
| Tesla V100-SXM2-16GB | 16 GB | GPU 1 |
| CPU | Intel Xeon E5-2673 v2 @ 3.30GHz, 48 GB RAM | Host |
| Interconnect | No NVLink | Host-staged fallback |

## Engine Settings
| Setting | Value |
|---|---|
| Engine | llama.cpp (turboquant fork) |
| Git commit | `73996836cb34` |
| CUDA | 12.8 |
| Tensor split | 10,16 (layer mode) |
| KV cache K | q8_0 |
| KV cache V | turbo3 |
| Cache RAM | 8192 MiB |
| Context | 262144 |
| Batch / ubatch | 4096 / 512 |
| Threads | 12 / 12 |
| Flash attention | on |
| Mlock | yes |

## All Models — Cold + Warm Results

| # | Model | File size | Params | ❄️ Cold G t/s | 🔥 Warm G t/s | ❄️ TTFT | 🔥 TTFT | Cache hit | GPU0 model | GPU0 KV | GPU1 model | GPU1 KV |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `Qwen3-Coder-30B` | 12.1G | 30.5B | 35 | 72 | 11210ms | 240ms | 11435 | 5G | 4G | 7G | 6G |
| 2 | `Qwen3.5-24B (IQ4_XS)` | 13.4G | 24.5B | 62 | 65 | 10200ms | 240ms | 11063 | 5G | 1G | 8G | 1G |
| 3 | `Qwen3.5-24B (Q5_K_M)` | 17.5G | 24.5B | 62 | 65 | 11286ms | 239ms | 11063 | 7G | 1G | 10G | 1G |
| 4 | `Qwen3.5-24B (Q6_K)` | 20.1G | 24.5B | 52 | 63 | 12777ms | 234ms | 11063 | 8G | 1G | 12G | 1G |
| 5 | `Qwen3.6-27B-NEO-CODE` | 15.4G | 26.9B | 30 | 42 | 12617ms | 321ms | 11063 | 5G | 2G | 9G | 4G |
| 6 | `Qwen3.6-35B-Claude-Compact` | 17.3G | 35.5B | 38 | 60 | 16651ms | 272ms | 11063 | 7G | 1G | 10G | 1G |
| 7 | `Qwen3.6-35B-Claude-Mini` | 14.3G | 35.5B | 50 | 48 | 14874ms | 356ms | 11063 | 6G | 1G | 8G | 1G |
| 8 | `Qwen3.6-35B-Native-MTP (IQ4_XS)` | 19.4G | 35.5B | 18 | 63 | 13446ms | 305ms | 11063 | 8G | 1G | 11G | 1G |
| 9 | `Qwen3.6-35B-Native-MTP (Q4_K_S)` | 20.4G | 35.5B | 8 | 42 | 16693ms | 333ms | 11063 | 8G | 1G | 12G | 1G |
| 10 | `DeepSeek R1 8B` | 4.6G | 8.2B | 83 | 82 | 693ms | 246ms | 1843 | 2G | 6G | 3G | 8G |
| 11 | `Gemma-4-26B-APEX` | 14.8G | 25.2B | 36 | 70 | 6228ms | 221ms | 10580 | 6G | 1G | 9G | 1G |
| 12 | `Gemma-4-26B (Q3_K_M)` | 13.0G | 25.2B | 31 | 68 | 6523ms | 228ms | 10580 | 5G | 1G | 8G | 1G |
| 13 | `Gemma-4-26B (Q5_K_M)` | 19.3G | 25.2B | 40 | 69 | 6398ms | 225ms | 10580 | 8G | 1G | 12G | 1G |
| 14 | `Qwen3.6-35B (IQ4_XS)` | 18.7G | 34.7B | 66 | 66 | 12527ms | 239ms | 11063 | 7G | 1G | 11G | 1G |
| 15 | `Qwen3.6-35B (Q4_K_M)` | 21.2G | 34.7B | 66 | 64 | 13080ms | 232ms | 11063 | 8G | 1G | 13G | 1G |

## Warm Gen TPS by Model (descending)

| # | Model | 🔥 G t/s | Params | Architecture |
|---|---:|---:|---:|
| 1 | `DeepSeek R1 8B` | 82 | 8.2B | MoE |
| 2 | `Qwen3-Coder-30B` | 72 | 30.5B | MoE |
| 3 | `Gemma-4-26B-APEX` | 70 | 25.2B | MoE |
| 4 | `Gemma-4-26B (Q5_K_M)` | 69 | 25.2B | MoE |
| 5 | `Gemma-4-26B (Q3_K_M)` | 68 | 25.2B | MoE |
| 6 | `Qwen3.6-35B (IQ4_XS)` | 66 | 34.7B | MoE |
| 7 | `Qwen3.5-24B (Q5_K_M)` | 65 | 24.5B | MoE |
| 8 | `Qwen3.5-24B (IQ4_XS)` | 65 | 24.5B | MoE |
| 9 | `Qwen3.6-35B (Q4_K_M)` | 64 | 34.7B | MoE |
| 10 | `Qwen3.5-24B (Q6_K)` | 63 | 24.5B | MoE |
| 11 | `Qwen3.6-35B-Native-MTP (IQ4_XS)` | 63 | 35.5B | MoE |
| 12 | `Qwen3.6-35B-Claude-Compact` | 60 | 35.5B | MoE |
| 13 | `Qwen3.6-35B-Claude-Mini` | 48 | 35.5B | MoE |
| 14 | `Qwen3.6-27B-NEO-CODE` | 42 | 26.9B | Dense |
| 15 | `Qwen3.6-35B-Native-MTP (Q4_K_S)` | 42 | 35.5B | MoE |

## Cold vs Warm TTFT Speedup

| # | Model | ❄️ TTFT | 🔥 TTFT | Speedup |
|---|---:|---:|---:|
| 1 | `Qwen3.6-35B-Native-MTP (Q4_K_S)` | 16693ms | 333ms | 50x |
| 2 | `Qwen3.6-35B-Claude-Compact` | 16651ms | 272ms | 61x |
| 3 | `Qwen3.6-35B-Claude-Mini` | 14874ms | 356ms | 42x |
| 4 | `Qwen3.6-35B-Native-MTP (IQ4_XS)` | 13446ms | 305ms | 44x |
| 5 | `Qwen3.6-35B (Q4_K_M)` | 13080ms | 232ms | 56x |
| 6 | `Qwen3.5-24B (Q6_K)` | 12777ms | 234ms | 55x |
| 7 | `Qwen3.6-27B-NEO-CODE` | 12617ms | 321ms | 39x |
| 8 | `Qwen3.6-35B (IQ4_XS)` | 12527ms | 239ms | 52x |
| 9 | `Qwen3.5-24B (Q5_K_M)` | 11286ms | 239ms | 47x |
| 10 | `Qwen3-Coder-30B` | 11210ms | 240ms | 47x |
| 11 | `Qwen3.5-24B (IQ4_XS)` | 10200ms | 240ms | 42x |
| 12 | `Gemma-4-26B (Q3_K_M)` | 6523ms | 228ms | 29x |
| 13 | `Gemma-4-26B (Q5_K_M)` | 6398ms | 225ms | 28x |
| 14 | `Gemma-4-26B-APEX` | 6228ms | 221ms | 28x |
| 15 | `DeepSeek R1 8B` | 693ms | 246ms | 3x |

## GPU Memory by Model

| # | Model | GPU0 model | GPU0 KV | GPU0 free | GPU1 model | GPU1 KV | GPU1 free |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `Qwen3-Coder-30B` | 4.88G | 3.71G | 0.31G | 7.1G | 5.66G | 2.4G |
| 2 | `Qwen3.5-24B (IQ4_XS)` | 5.18G | 0.81G | 2.92G | 7.97G | 1.21G | 5.76G |
| 3 | `Qwen3.5-24B (Q5_K_M)` | 6.68G | 0.81G | 1.42G | 10.43G | 1.21G | 3.3G |
| 4 | `Qwen3.5-24B (Q6_K)` | 7.71G | 0.81G | 0.38G | 11.99G | 1.21G | 1.75G |
| 5 | `Qwen3.6-27B-NEO-CODE` | 5.22G | 2.4G | 1.2G | 9.49G | 4.0G | 1.43G |
| 6 | `Qwen3.6-35B-Claude-Compact` | 6.81G | 0.87G | 1.23G | 10.23G | 1.28G | 1.27G |
| 7 | `Qwen3.6-35B-Claude-Mini` | 5.58G | 0.87G | 2.45G | 8.46G | 1.28G | 3.04G |
| 8 | `Qwen3.6-35B-Native-MTP (IQ4_XS)` | 7.87G | 0.87G | 0.16G | 11.23G | 1.28G | 0.26G |
| 9 | `Qwen3.6-35B-Native-MTP (Q4_K_S)` | 8.25G | 0.87G | 0.61G | 11.82G | 1.28G | 0.77G |
| 10 | `DeepSeek R1 8B` | 1.55G | 5.85G | 1.47G | 2.68G | 8.19G | 4.26G |
| 11 | `Gemma-4-26B-APEX` | 5.71G | 0.83G | 2.33G | 9.09G | 1.24G | 4.56G |
| 12 | `Gemma-4-26B (Q3_K_M)` | 5.04G | 0.83G | 3.0G | 7.97G | 1.24G | 5.68G |
| 13 | `Gemma-4-26B (Q5_K_M)` | 7.67G | 0.83G | 0.37G | 11.63G | 1.24G | 2.02G |
| 14 | `Qwen3.6-35B (IQ4_XS)` | 7.21G | 0.81G | 0.89G | 11.24G | 1.21G | 2.5G |
| 15 | `Qwen3.6-35B (Q4_K_M)` | 8.18G | 0.81G | 0.74G | 12.69G | 1.21G | 2.14G |

## GPU Thermal and Power

| # | Model | Before Temp | Before Power | After Temp | After Power | Delta Temp | Delta Power |
|---|---:|---:|---:|---:|---:|---:|
| 1 | `Qwen3-Coder-30B` | 51°C | 77W | 56°C | 85W | +5°C | +7.79W |
| 2 | `Qwen3.5-24B (IQ4_XS)` | 58°C | 82W | 61°C | 86W | +3°C | +4.2W |
| 3 | `Qwen3.5-24B (Q5_K_M)` | 48°C | 80W | 48°C | 94W | +0°C | +14.24W |
| 4 | `Qwen3.5-24B (Q6_K)` | 45°C | 81W | 51°C | 84W | +6°C | +2.81W |
| 5 | `Qwen3.6-27B-NEO-CODE` | 53°C | 76W | 58°C | 91W | +5°C | +15.54W |
| 6 | `Qwen3.6-35B-Claude-Compact` | 48°C | 79W | 54°C | 80W | +6°C | +1.22W |
| 7 | `Qwen3.6-35B-Claude-Mini` | 56°C | 72W | 60°C | 83W | +4°C | +11.22W |
| 8 | `Qwen3.6-35B-Native-MTP (IQ4_XS)` | 49°C | 77W | 48°C | 71W | -1°C | -5.73W |
| 9 | `Qwen3.6-35B-Native-MTP (Q4_K_S)` | 46°C | 69W | 52°C | 73W | +6°C | +4.17W |
| 10 | `DeepSeek R1 8B` | 53°C | 75W | 58°C | 115W | +5°C | +40.55W |
| 11 | `Gemma-4-26B-APEX` | 58°C | 81W | 61°C | 76W | +3°C | -5.45W |
| 12 | `Gemma-4-26B (Q3_K_M)` | 49°C | 81W | 52°C | 72W | +3°C | -9.14W |
| 13 | `Gemma-4-26B (Q5_K_M)` | 54°C | 81W | 57°C | 75W | +3°C | -6.06W |
| 14 | `Qwen3.6-35B (IQ4_XS)` | 56°C | 83W | 60°C | 83W | +4°C | -0.55W |
| 15 | `Qwen3.6-35B (Q4_K_M)` | 61°C | 87W | 62°C | 90W | +1°C | +2.86W |

## Key Observations

### Prompt Cache Effectiveness
Warm TTFT is consistently ~240ms (down from ~11s cold) — a **45× speedup** on average. The Codex system prompt (≈11,000 tokens) is fully cached after the first request.

### MoE vs Dense Generation TPS
MoE models (Qwen3.5/3.6, Gemma 4) generate at **62-67 t/s** regardless of parameter count. Dense models (Devstral, NEO-CODE) are slower at **45-50 t/s**.

### GPU Memory Utilization
The RTX 3080 (10 GB) is the bottleneck — most models leave <1 GB free after loading. The V100 (16 GB) has more headroom (2-6 GB free). Large models like the 35B native-MTP IQ4_XS push the 3080 to 9.9 GB used.

### Cache RAM is Not Used for KV Spill
`--cache-ram 8192` controls the *prompt cache* (host-side prompt reuse), not KV cache spill. KV cache always lives on GPU. Models that OOM on KV allocation at 262k context (Devstral 24B, 30B Coder) would need smaller context or adjusted tensor split.
