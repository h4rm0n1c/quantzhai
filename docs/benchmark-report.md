# QuantZhai Benchmark — Combined Ranking

*41 models · 2026-06-09 22:44 UTC UTC*

| # | Model | Size | PPL❄️ | PPL🔥 | ❄️TPS | 🔥TPS | **Score** | K | V | GPU0 M/KV | GPU1 M/KV |
|---:|------|---:|---:|---:|---:|---:|---:|:---:|:---:|---:|---:|
| 1 | `LFM2.5-8B-A1B-Q8_0` | 3080:5.8G(m=3.3,kv=0.6,f=4.6) | 9.1298 | 4.3315 | 93.5 | 136.1 | **80.2** | q8_0 | q8_0 | 3.3/0.6 | 5.7/1.1 |
| 2 | `LFM2.5-1.2B-Thinking-Q8_0` | 3080:3.0G(m=0.5,kv=0.6,f=7.4) | 11.1713 | 4.3448 | 200.3 | 208.3 | **80.1** | q8_0 | q8_0 | 0.5/0.6 | 0.8/1.1 |
| 3 | `LFM2.5-8B-A1B-APEX-I-Mini` | 3080:3.8G(m=1.3,kv=0.6,f=6.5) | 10.6522 | 4.9582 | 75.6 | 131.0 | **73.7** | q8_0 | q8_0 | 1.3/0.6 | 2.3/1.1 |
| 4 | `LFM2.5-Queen-Opus-4.7-8B-A1B.i1-Q4_K_M` | 3080:4.3G(m=1.8,kv=0.6,f=6.0) | 5.1082 | 3.6417 | 79.3 | 145.8 | **71.6** | q8_0 | q8_0 | 1.8/0.6 | 3.3/1.1 |
| 5 | `SmolLM3-Q8_0` | 3080:6.1G(m=1.2,kv=2.9,f=4.3) | 3.2792 | 2.5937 | 88.3 | 98.7 | **67.7** | q8_0 | turbo3 | 1.2/2.9 | 2.0/4.1 |
| 6 | `granite-4.0-1b-Q8_0` | 3080:5.7G(m=0.6,kv=3.1,f=4.7) | 3.0391 | 2.6723 | 93.3 | 103.6 | **67.7** | q8_0 | turbo3 | 0.6/3.1 | 1.1/4.7 |
| 7 | `Qwen3.5-9B-DeepSeek-V4-Flash-Q4_K_M` | 3080:5.4G(m=1.7,kv=1.2,f=4.9) | 3.3272 | 1.9722 | 58.4 | 59.6 | **66.4** | q8_0 | turbo3 | 1.7/1.2 | 3.3/2.0 |
| 8 | `qwen36_35b_Q4_K_M` | 3080:10.1G(m=8.2,kv=0.8,f=0.2) | 2.7132 | 1.7533 | 44.7 | 54.7 | **64.9** | q8_0 | turbo3 | 8.2/0.8 | 12.7/1.2 |
| 9 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3080:9.7G(m=7.7,kv=0.8,f=0.7) | 3.1372 | 1.8781 | 45.0 | 50.6 | **64.1** | q8_0 | turbo3 | 7.7/0.8 | 12.0/1.2 |
| 10 | `Darwin-36B-Opus-APEX-I-Compact` | 3080:9.2G(m=6.4,kv=0.8,f=1.1) | 2.8265 | 1.7721 | 42.0 | 48.5 | **64.1** | q8_0 | turbo3 | 6.4/0.8 | 9.8/1.2 |
| 11 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3080:8.0G(m=5.2,kv=0.8,f=2.4) | 3.2241 | 1.9011 | 44.4 | 50.3 | **64.0** | q8_0 | turbo3 | 5.2/0.8 | 8.0/1.2 |
| 12 | `Qwen3.6-35B-A3B-APEX-I-Mini` | 3080:8.5G(m=5.8,kv=0.8,f=1.8) | 2.7092 | 1.7937 | 45.8 | 56.9 | **64.0** | q8_0 | turbo3 | 5.8/0.8 | 8.3/1.2 |
| 13 | `Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Di` | 3080:9.5G(m=6.7,kv=0.8,f=0.9) | 3.1393 | 1.8822 | 43.6 | 51.7 | **64.0** | q8_0 | turbo3 | 6.7/0.8 | 10.4/1.2 |
| 14 | `qwen36_35b_IQ4_XS` | 3080:10.0G(m=7.2,kv=0.8,f=0.4) | 2.7032 | 1.7507 | 37.7 | 52.6 | **63.5** | q8_0 | turbo3 | 7.2/0.8 | 11.2/1.2 |
| 15 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 3080:10.3G(m=8.3,kv=0.9,f=0.1) | 2.8126 | 1.7628 | 23.9 | 51.6 | **62.0** | q8_0 | turbo3 | 8.3/0.9 | 11.8/1.3 |
| 16 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 3080:9.6G(m=6.8,kv=0.9,f=0.7) | 2.7341 | 1.7509 | 20.5 | 48.5 | **60.9** | q8_0 | turbo3 | 6.8/0.9 | 10.2/1.3 |
| 17 | `deepseek-ai_DeepSeek-R1-0528-Qwen3-8B-IQ4_XS` | 3080:9.9G(m=1.5,kv=5.9,f=0.5) | 2.7802 | 2.3716 | 73.6 | 82.0 | **60.6** | q8_0 | turbo3 | 1.5/5.9 | 2.7/8.2 |
| 18 | `Qwen3-Coder-30B-APEX-Mini` | 3080:9.7G(m=4.9,kv=3.7,f=0.7) | 2.1834 | 1.9448 | 55.4 | 76.0 | **60.0** | q8_0 | turbo3 | 4.9/3.7 | 7.1/5.7 |
| 19 | `ai21labs_AI21-Jamba2-3B-Q4_K_M` | 3080:2.4G(m=0.7,kv=0.1,f=8.0) | 1.7951 | 3.3273 | 100.2 | 129.3 | **59.5** | q8_0 | turbo3 | 0.7/0.1 | 1.1/0.1 |
| 20 | `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS` | 3080:10.2G(m=5.2,kv=2.4,f=0.2) | 2.5831 | 1.6989 | 25.4 | 25.0 | **59.2** | q8_0 | turbo3 | 5.2/2.4 | 9.5/4.0 |
| 21 | `ai21labs_AI21-Jamba2-3B-Q8_0` | 3080:2.9G(m=1.3,kv=0.1,f=7.4) | 1.7711 | 3.2988 | 107.5 | 106.3 | **59.2** | q8_0 | turbo3 | 1.3/0.1 | 1.9/0.1 |
| 22 | `Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APE` | 3080:8.4G(m=5.6,kv=0.9,f=1.9) | 2.7032 | 1.8036 | 20.9 | 38.5 | **58.5** | q8_0 | turbo3 | 5.6/0.9 | 8.5/1.3 |
| 23 | `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved` | 3080:9.9G(m=7.9,kv=0.9,f=0.5) | 2.7614 | 1.7554 | 2.3 | 17.2 | **58.3** | q8_0 | turbo3 | 7.9/0.9 | 11.2/1.3 |
| 24 | `Phi-4-mini-instruct.Q8_0` | 3080:9.0G(m=1.4,kv=5.1,f=1.4) | 3.8166 | 2.7733 | 63.0 | 79.3 | **57.3** | q8_0 | turbo3 | 1.4/5.1 | 2.7/7.4 |
| 25 | `nvidia_Nemotron-3-Nano-30B-A3B-IQ4_XS` | 3080:8.9G(m=6.9,kv=0.3,f=1.5) | 2.2900 | 2.1061 | 53.7 | 78.4 | **57.1** | q8_0 | turbo3 | 6.9/0.3 | 11.1/0.3 |
| 26 | `NVIDIA-Nemotron-Nano-12B-v2.Q4_K_M` | 3080:6.1G(m=2.7,kv=0.8,f=4.3) | 2.0216 | 1.8394 | 50.9 | 49.7 | **56.5** | q8_0 | turbo3 | 2.7/0.8 | 4.4/1.6 |
| 27 | `granite-4.0-h-tiny-DISTILL-4.5-opus-high-think-q5_k_m` | 3080:4.3G(m=1.9,kv=0.4,f=6.1) | 2.5327 | 2.2499 | 57.6 | 70.6 | **55.5** | q8_0 | turbo3 | 1.9/0.4 | 3.0/0.4 |
| 28 | `EXAONE-3.5-7.8B-Instruct-Q8_0` | 3080:9.7G(m=3.0,kv=5.1,f=0.6) | 1.4170 | 1.9538 | 55.9 | 65.5 | **52.0** | q8_0 | turbo3 | 3.0/5.1 | 4.8/7.4 |
| 29 | `gemma-4-12b-it-qat-q4_0` | 3080:5.1G(m=2.4,kv=0.7,f=5.3) | 33.3835 | 5.3650 | 36.1 | 37.4 | **48.1** | q8_0 | turbo3 | 2.4/0.7 | 4.5/1.1 |
| 30 | `phi-4-Q4_K` |  | 1.9193 | 1.8943 | 1.0 | 0.9 | **46.9** | q8_0 | turbo3 | ?/? | ?/? |
| 31 | `Falcon-H1-7B-Instruct-Q4_K_M` | 3080:5.2G(m=1.7,kv=1.8,f=5.2) | 1.7571 | 2.1837 | 44.6 | 44.7 | **45.3** | q8_0 | turbo3 | 1.7/1.8 | 2.7/2.6 |
| 32 | `gemma-4-26B-A4B-Claude-Distill-APEX-I-Compact` | 3080:9.3G(m=6.0,kv=0.8,f=1.0) | 9.3767 | 7.2414 | 38.1 | 58.9 | **35.0** | q8_0 | turbo3 | 6.0/0.8 | 9.5/1.2 |
| 33 | `gemma-3n-E2B-it-Q8_0` | 3080:3.5G(m=0.8,kv=0.4,f=6.9) | 4.5099 | 4.7717 | 45.7 | 52.2 | **34.1** | q8_0 | turbo3 | 0.8/0.4 | 1.8/0.4 |
| 34 | `allenai_Olmo-3-7B-Instruct-Q4_K_M` | 3080:5.7G(m=1.6,kv=3.3,f=4.6) | 4.0029 | 3.7309 | 11.6 | 14.3 | **33.1** | q8_0 | turbo3 | 1.6/3.3 | 2.6/5.4 |
| 35 | `ibm-granite_granite-4.1-8b-Q4_K_M` | 3080:9.9G(m=2.0,kv=6.2,f=0.5) | 28.3674 | 39.1524 | 64.9 | 75.2 | **27.0** | q8_0 | turbo3 | 2.0/6.2 | 3.3/9.4 |
| 36 | `GLM-4.7-Flash-IQ4_XS` | 3080:10.2G(m=6.2,kv=3.0,f=0.1) | 1.8523 | 3.6916 | 6.6 | 33.5 | **24.4** | q8_0 | turbo3 | 6.2/3.0 | 9.9/4.5 |
| 37 | `gemma-4-26B_q4_0-it` | 3080:8.9G(m=5.5,kv=0.8,f=1.5) | 9.4943 | 13.0802 | 48.4 | 59.3 | **24.2** | q8_0 | turbo3 | 5.5/0.8 | 8.9/1.2 |
| 38 | `google_gemma-4-26B-A4B-it-Q5_K_M` | 3080:10.2G(m=7.7,kv=0.8,f=0.2) | 18.4907 | 20.1509 | 38.5 | 56.5 | **23.7** | q8_0 | turbo3 | 7.7/0.8 | 11.6/1.2 |
| 39 | `gemma-4-26B-A4B-APEX-I-Compact` | 3080:9.1G(m=5.7,kv=0.8,f=1.3) | 19.2454 | 25.3029 | 44.3 | 58.7 | **21.8** | q8_0 | turbo3 | 5.7/0.8 | 9.1/1.2 |
| 40 | `google_gemma-4-26B-A4B-it-Q3_K_M` | 3080:8.4G(m=5.0,kv=0.8,f=2.0) | 29.0656 | 32.6819 | 32.1 | 55.4 | **21.0** | q8_0 | turbo3 | 5.0/0.8 | 8.0/1.2 |
| 41 | `gemma-3n-E4B-it-Q8_0` | ? | ? | ? | ? | ? | - | ? | ? | ?/? | ?/? |

### Score Formula
Three equal dimensions (TPS, PPL, Convergence) averaged to 0–100.
Each scored 0–1 against a percentile cap (90th for TPS & convergence,
10th for warm PPL). Three dimensions give architecture diversity a fair
shot — fast learners (convergence) and innovative architectures can
compete with absolute-PPL champs.
TPS = `min(1, cold_tps * warm_tps / cap_90)`
PPL = `cap_10 / warm_ppl`
Conv = `min(1, (cold_ppl / warm_ppl) / cap_90)` — >1 means PPL drops
Final = `(TPS + PPL + Conv) / 3 * 100`

### Hardware & Engine
| GPU 0 | GPU 1 | Total VRAM | Engine |
|---|---|---|---|
| RTX 3080 10GB | V100-SXM2 16GB | 26 GB | llama.cpp @ 189e512606a3 |

### ToolboxPPL
Code perplexity on [`macvox68`](https://github.com/h4rm0n1c/macvox68) (32 C/H files, 125KB, Mac OS 7 TTS).
Ctx=4096, stride=512, K/V cache types match inference config.

### Key Takeaways

**1st: LFM2.5-8B-A1B-Q8_0** — Score 80.2
   PPL🔥=4.3315 TPS=136.1
**2nd: LFM2.5-1.2B-Thinking-Q8_0** — Score 80.1
   PPL🔥=4.3448 TPS=208.3
**3rd: LFM2.5-8B-A1B-APEX-I-Mini** — Score 73.7
   PPL🔥=4.9582 TPS=131.0
