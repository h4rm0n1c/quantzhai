# Model Benchmark: 26G VRAM at 256K Context

System: RTX 3080 (10G) + Tesla V100 (16G), split-mode layer, tensor-split auto
KV: `-ctk q8_0 -ctv turbo3` (turbo2 noted where tested)
Context: 262144 tokens, flash-attn on
Measured: backend_process_used_mib from llama.cpp allocator (`/v1/models` → `memory`)

## Results (sorted by quality/interest)

| Model | HF Source | Disk | VRAM | KV | Free | Prompt TPS | Gen TPS | MTP | Reasoning |
|-------|-----------|------|------|-----|------|-----------|---------|-----|-----------|
| **24B Opus+Gemini Q5_K_M** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 18G | 21.0G | 1.9G | 4.4G | **105** | **91** | ❌ | Claude Opus + Gemini 3.1 Pro |
| **Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact** | [mudler/...-APEX-MTP-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF) | 17G | 20.9G | 1.9G | 4.5G | **24** | **72** | ✅ | Claude Opus 4.7 distilled |
| **DuoNeural Code imatrix IQ4_XS** | [DuoNeural/...-GGUF](https://huggingface.co/DuoNeural/Qwen3.6-35B-A3B-Code-imatrix-GGUF) | 19G | 22.2G | 1.9G | 3.2G | **110** | **99** | ❌ | coding imatrix |
| **24B Opus+Gemini Q6_K** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 20G | 23.4G | 1.9G | 2.0G | — | 87 | ❌ | Claude Opus + Gemini 3.1 Pro |
| **Gemma 4 Opus Distill Q4_K_M** | [TeichAI/...-GGUF](https://huggingface.co/TeichAI/gemma-4-26B-A4B-it-Claude-Opus-Distill-v2-GGUF) ⚠️ unreliable | 17G | 20.8G | 1.9G | 4.6G | **96** | **83** | ❌ | Opus on Gemma 4 — see mudler/ APEX variant instead |
| **Gemma 4 Q5_K_M** | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 19G | 23.2G | 1.9G | 2.2G | **117** | **64** | ❌ | Google Gemma 4 |
| **24B Opus+Gemini IQ4_XS** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 13G | 17.3G | 1.9G | 8.1G | — | 96 | ❌ | Claude Opus + Gemini 3.1 Pro |
| DuoNeural Code imatrix Q4_K_M | [DuoNeural/...-GGUF](https://huggingface.co/DuoNeural/Qwen3.6-35B-A3B-Code-imatrix-GGUF) | 21G | 22.7G | 1.9G | 2.7G | — | 86 | ❌ | coding imatrix |
| Qwen3.6-35B-A3B-APEX-I-Mini | [mudler/...-APEX-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-APEX-GGUF) | 14G | 24.9G | 1.9G | 1.1G | — | 101 | ✅ | Base model |
| Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS | [llmfan46/...-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF) | 19G | 25.0G | 1.9G | 0.0G | — | 91 | ✅ | Base model |
| google_gemma-4-26B-A4B-it-Q3_K_M | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 13G | 17.3G | 1.9G | 8.1G | — | 78 | ❌ | Google Gemma 4 |
| **Qwen3-Coder-30B-A3B APEX-Mini** | [mudler/...-APEX-GGUF](https://huggingface.co/mudler/Qwen3-Coder-30B-APEX-GGUF) | 12G | — | — | — | — | — | ✅ | `qwen3moe` arch, MTP capable |
| **Devstral-Small-2-24B IQ4_XS** | [bartowski/...-GGUF](https://huggingface.co/bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF) | 12G | — | — | — | — | — | ❌ | `mistral3` arch |

## Failed to load

| Model | HF Source | Reason |
|-------|-----------|--------|
| mudler/Qwen3.6-35B-A3B-APEX-I-Compact | — | VRAM exceeded (17G + 3G KV + MTP ≈ OOM) |
| DeepSeek-R1-Distill-Qwen-32B | — | 131K native context model doesn't support 262K server ctx |

## Speed: Prompt TPS vs Generation TPS

The endpoint returns both `prompt_per_second` (input processing including reasoning) and `predicted_per_second` (output generation). Key observations:

- **Prompt TPS varies massively**: 24 t/s (Opus I-Compact with MTP) to 544 t/s (8B DeepSeek). The Opus I-Compact's unusually low prompt TPS (24) is likely MTP overhead during prompt processing.
- **Generation TPS** is more consistent at 64-106 t/s across all models.
- **24B Opus+Gemini Q5_K_M** has the best balance: 105 prompt TPS + 91 gen TPS + 4.4G free.
- **8B DeepSeek v3.2 distill** is fastest overall: 544 prompt TPS + 106 gen TPS, but dense KV cost is high.
- MTP speculation (Opus I-Compact) hurts prompt TPS (24 vs 105 for non-MTP) but helps gen TPS with speculative decoding.

## Notes

- **MoE models** use ~2G KV cache at 256K vs **dense models** ~13-14G, making MoE far more VRAM-efficient at long context
- **APEX MTP quants** from mudler consistently deliver 60-100 TPS with speculative decoding enabled
- **Gemma 4** needs `reasoning=off` and `reasoning-format=auto` — doesn't support `--reasoning on`
- **bartowski's quants** are reliable across architectures (both Gemma 4 variants work)
- **turbo2 KV** tested on Gemma 4 Q5_K_M: no VRAM savings on MoE (KV cache too small), TPS improved 82 vs 76
- **mradermacher's imatrix quants** for the 24B Opus+Gemini model are solid — IQ4_XS through Q6_K all work
- **Dense models at 256K** (Qwen3-8B/14B) spend ~60% of VRAM on KV cache alone — only makes sense for small models or short-context use
- **`qwen3moe` architecture** (Qwen3-Coder-30B-A3B) works with turbo3 KV cache at 256K on dual-GPU split 10,16. The APEX-Mini variant (12G) fits comfortably. Benchmarks pending.
- **`mistral3` architecture** (Devstral-Small-2-24B) works with turbo3 KV cache at 256K on dual-GPU split 10,16. The IQ4_XS variant (12G) fits comfortably. Benchmarks pending.

## Recommended model

```bash
# Best balance of speed, quality, and VRAM headroom:
Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf
# 100 TPS | 4.5G free | reasoning-distilled | MTP speculation | 256K context
```
