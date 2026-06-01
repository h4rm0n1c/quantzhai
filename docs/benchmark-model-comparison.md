# Model Benchmark: 26G VRAM at 256K Context

System: RTX 3080 (10G) + Tesla V100 (16G), split-mode layer, tensor-split auto
KV: `-ctk q8_0 -ctv turbo3` (turbo2 noted where tested)
Context: 262144 tokens, flash-attn on
Measured: backend_process_used_mib from llama.cpp allocator (`/v1/models` → `memory`)

## Results

| Model | HF Source | Disk | VRAM | Free | TPS | MTP | Reasoning |
|-------|-----------|------|------|------|-----|-----|-----------|
| **Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact** | [mudler/...-APEX-MTP-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF) | 17G | 20.9G | 4.5G | 100 | ✅ | Claude Opus 4.7 distilled |
| Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Mini | [mudler/...-APEX-MTP-GGUF](https://huggingface.co/mudler/Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-GGUF) | 14G | 20.8G | 5.1G | 68 | ✅ | Claude Opus 4.7 distilled |
| Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS | [llmfan46/...-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF) | 19G | 25.0G | 0.0G | 91 | ✅ | Base model |
| Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S | [llmfan46/...-Native-MTP-Preserved-GGUF](https://huggingface.co/llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF) | 19G | 24.1G | 1.3G | 74 | ✅ | Base model |
| google_gemma-4-26B-A4B-it-Q5_K_M | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 19G | 23.2G | 2.2G | 82 | ❌ | Google Gemma 4 |
| google_gemma-4-26B-A4B-it-Q3_K_M | [bartowski/...-GGUF](https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF) | 13G | 17.3G | 8.1G | 78 | ❌ | Google Gemma 4 |

## Failed to load

| Model | HF Source | Reason |
|-------|-----------|--------|
| unsloth/Devstral-Small-2-24B (various quants) | — | `mistral3` arch: KV cache allocation fails on dual-GPU tensor split |
| byteshape/Devstral-Small-2-24B | — | same root cause |
| mudler/Qwen3.6-35B-A3B-APEX-I-Compact | — | VRAM exceeded (17G + 3G KV + MTP ≈ OOM) |
| unsloth/Qwen3.6-27B-MTP | — | Loaded but MTP not auto-detected (filename lacks "MTP"), tensor-count heuristic needed |
| DeepSeek-R1-Distill-Qwen-32B | — | 131K native context model doesn't support 262K server ctx |

## Notes

- **MoE models** use ~2G KV cache at 256K vs **dense models** ~6G, making them far more VRAM-efficient at long context
- **APEX MTP quants** from mudler consistently deliver 60-100 TPS with speculative decoding enabled
- **Gemma 4** needs `reasoning=off` and `reasoning-format=auto` — doesn't support `--reasoning on`
- **bartowski's quants** are reliable across architectures (both Gemma 4 variants work)
- **turbo2 KV** tested on Gemma 4 Q5_K_M: no VRAM savings on MoE (KV cache too small), TPS improved 82 vs 76

## Recommended model

```bash
# Best balance of speed, quality, and VRAM headroom:
Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled-APEX-MTP-I-Compact.gguf
# 100 TPS | 4.5G free | reasoning-distilled | MTP speculation | 256K context
```
