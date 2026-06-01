# Model Benchmark: 26G VRAM at 256K Context

System: RTX 3080 (10G) + Tesla V100 (16G), split-mode layer, tensor-split auto
KV: `-ctk q8_0 -ctv turbo3` (turbo2 noted where tested)
Context: 262144 tokens, flash-attn on
Measured: backend_process_used_mib from llama.cpp allocator (`/v1/models` → `memory`)

## Results (sorted by quality/interest)

| Model | HF Source | Disk | VRAM | KV | Free | TPS | MTP | Reasoning |
|-------|-----------|------|------|-----|------|-----|-----|-----------|
| **24B Opus+Gemini Q6_K** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 20G | 23.4G | 1.9G | 2.0G | 87 | ❌ | Claude Opus + Gemini 3.1 Pro |
| **24B Opus+Gemini Q5_K_M** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 18G | 21.0G | 1.9G | 4.4G | 93 | ❌ | Claude Opus + Gemini 3.1 Pro |
| **24B Opus+Gemini IQ4_XS** | [mradermacher/...-GGUF](https://huggingface.co/mradermacher/Qwen3.5-24B-A3B-Claude-Opus-Gemini-3.1-Pro-Reasoning-Distilled-heretic-GGUF) | 13G | 17.3G | 1.9G | 8.1G | 96 | ❌ | Claude Opus + Gemini 3.1 Pro |
| **Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact** | [mudler/...-APEX-MTP-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF) | 17G | 20.9G | 1.9G | 4.5G | 100 | ✅ | Claude Opus 4.7 distilled |
| **Qwen3-8B DeepSeek v3.2 distill IQ4_XS** | [TeichAI/...-GGUF](https://huggingface.co/TeichAI/Qwen3-8B-DeepSeek-v3.2-Speciale-Distill-GGUF) | 5G | 20.1G | 13.1G | 5.3G | 104 | ❌ | DeepSeek v3.2 reasoning |
| **DuoNeural Code imatrix Q4_K_M** | [DuoNeural/...-GGUF](https://huggingface.co/DuoNeural/Qwen3.6-35B-A3B-Code-imatrix-GGUF) | 21G | 22.7G | 1.9G | 2.7G | 86 | ❌ | coding imatrix |
| **DuoNeural Code imatrix IQ4_XS** | [DuoNeural/...-GGUF](https://huggingface.co/DuoNeural/Qwen3.6-35B-A3B-Code-imatrix-GGUF) | 19G | 22.2G | 1.9G | 3.2G | 103 | ❌ | coding imatrix |
| **Qwen3-14B DeepSeek v3.2 distill Q4_K_M** | [TeichAI/...-GGUF](https://huggingface.co/TeichAI/Qwen3-14B-DeepSeek-v3.2-Speciale-Distill-GGUF) | 9G | 23.8G | 14.5G | 1.5G | 61 | ❌ | DeepSeek v3.2 reasoning |
| Qwen3.6-35B-A3B-APEX-I-Mini | [mudler/...-APEX-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-APEX-GGUF) | 14G | 24.9G | 1.9G | 1.1G | 101 | ✅ | Base model |
| Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Mini | [mudler/...-APEX-MTP-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF) | 14G | 20.8G | 1.9G | 5.1G | 68 | ✅ | Claude Opus 4.7 distilled |
| Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS | [llmfan46/...-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF) | 19G | 25.0G | 1.9G | 0.0G | 91 | ✅ | Base model |
| Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S | [llmfan46/...-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF) | 19G | 24.1G | 1.9G | 1.3G | 74 | ✅ | Base model |
| google_gemma-4-26B-A4B-it-Q5_K_M | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 19G | 23.2G | 1.9G | 2.2G | 82 | ❌ | Google Gemma 4 |
| google_gemma-4-26B-A4B-it-Q3_K_M | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 13G | 17.3G | 1.9G | 8.1G | 78 | ❌ | Google Gemma 4 |

## Failed to load

| Model | HF Source | Reason |
|-------|-----------|--------|
| unsloth/Devstral-Small-2-24B (various quants) | — | `mistral3` arch: KV cache allocation fails on dual-GPU tensor split |
| byteshape/Devstral-Small-2-24B | — | same root cause |
| mudler/Qwen3.6-35B-A3B-APEX-I-Compact | — | VRAM exceeded (17G + 3G KV + MTP ≈ OOM) |
| DeepSeek-R1-Distill-Qwen-32B | — | 131K native context model doesn't support 262K server ctx |
| Qwen3-Coder-30B-A3B | — | `qwen3moe` arch: KV cache allocation fails (different from qwen35moe) |
| Qwen3.5-24B-A3B (Devstral base) | — | `mistral3` arch: same issue as Devstral |

## Notes

- **MoE models** use ~2G KV cache at 256K vs **dense models** ~13-14G, making MoE far more VRAM-efficient at long context
- **APEX MTP quants** from mudler consistently deliver 60-100 TPS with speculative decoding enabled
- **Gemma 4** needs `reasoning=off` and `reasoning-format=auto` — doesn't support `--reasoning on`
- **bartowski's quants** are reliable across architectures (both Gemma 4 variants work)
- **turbo2 KV** tested on Gemma 4 Q5_K_M: no VRAM savings on MoE (KV cache too small), TPS improved 82 vs 76
- **mradermacher's imatrix quants** for the 24B Opus+Gemini model are solid — IQ4_XS through Q6_K all work
- **Dense models at 256K** (Qwen3-8B/14B) spend ~60% of VRAM on KV cache alone — only makes sense for small models or short-context use
- **The `qwen3moe` architecture** (Qwen3-Coder-30B-A3B) does NOT work — KV cache allocation fails, different from `qwen35moe`

## Recommended model

```bash
# Best balance of speed, quality, and VRAM headroom:
Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf
# 100 TPS | 4.5G free | reasoning-distilled | MTP speculation | 256K context
```
