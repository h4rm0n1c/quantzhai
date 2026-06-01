# QuantZhai Model Selection Guide

Target: 26G VRAM (RTX 3080 10G + V100 16G), 256K context, turboquant build.

## The QuantZhai Shape

A model that works on this setup has these characteristics:

| Property | Required | Why |
|----------|----------|-----|
| **Architecture** | `qwen35moe` or `gemma4` | Only proven arches. `mistral3`, `qwen3moe`, `qwen3` all fail KV cache at 256K on dual-GPU tensor split. |
| **Disk size** | 13-20G | Under 13G is wasted VRAM headroom. Over 20G doesn't leave room for KV cache (~2G MoE, ~14G dense). |
| **VRAM budget** | 26G total | Model + KV cache + compute buffers + MTP heads must fit. MoE: model + 2G KV. Dense: model + 14G KV. |
| **Context** | 262144 native | Server is pinned at 256K. Models with smaller native context either fail or waste VRAM. |
| **Source** | mudler, bartowski, mradermacher, llmfan46 | These quantizers consistently produce loadable GGUFs. unsloth and byteshape are unreliable. |

## KV Cache: The Deciding Factor

At 256K, KV cache is the dominant VRAM cost:

| Type | KV cache | Model budget | Headroom |
|------|----------|-------------|----------|
| **MoE** (`qwen35moe`, `gemma4`) | **~2G** | Up to **22G** on disk | Generous |
| **Dense** (`qwen3`, `qwen35`) | **~14G** | Up to **10G** on disk | Tight |

This is why MoE dominates: dense models spend 60% of VRAM on cache alone.

## Working Sources (by reliability)

1. **mudler** — APEX (MTP) quants for qwen35moe. Consistently 60-100 TPS. MTP speculative decoding. Best overall.
2. **bartowski** — Gemma 4 quants only (qwen35moe also available but untested). Reliable.
3. **mradermacher** — imatrix quants for 24B MoE reasoning distills. Q5_K_M through Q6_K all work.
4. **llmfan46** — Heretic quants, qwen35moe base. The originals. Still work.

## What to Search For

When looking for new models, filter for:

```text
architecture: qwen35moe or gemma4
quantizer: mudler, bartowski, mradermacher, llmfan46
disk size: 13-20G
context: 262144 (or larger)
tags: reasoning, distilled, MTP, APEX, imatrix
```

Promising search terms that have yielded working models:
- `APEX-MTP-GGUF` — mudler's MTP-enabled quants
- `opus reasoning distilled` — Claude reasoning chains
- `gemini reasoning distilled` — Gemini reasoning chains
- `DeepSeek v3.2 distill` — DeepSeek reasoning chains
- `native-mtp-preserved` — llmfan46's MTP models
- `Code-imatrix-GGUF` — coding fine-tunes with imatrix

## What to Avoid

- `mistral3` architecture — fails KV cache on tensor split
- `qwen3moe` architecture — same failure mode
- `qwen3` (dense) at 256K — 14G KV cache kills budget
- Models with native context under 256K — can't use server context
- unsloth/byteshape quants — inconsistent quality
- Disk size > 20G — no room for cache
- Models without GGUF — not usable on llama.cpp

## Current Top Picks

```
Rank  Model                          Disk   VRAM   Free   TPS   Why
1.    24B Opus+Gemini Q5_K_M         18G    21.0G  4.4G   93    Q5 precision, dual reasoning
2.    Opus I-Compact (APEX MTP)      17G    20.9G  4.5G   100   MTP, reasoning, fast
3.    24B Opus+Gemini Q6_K           20G    23.4G  2.0G   87    Highest precision that fits
4.    DuoNeural Code IQ4_XS          19G    22.2G  3.2G   103   Fastest, coding
5.    Gemma 4 Q5_K_M                 19G    23.2G  2.2G   82    First working Q5
```
