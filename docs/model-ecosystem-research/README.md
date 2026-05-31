# Model Ecosystem Research

**Purpose:** Survey of model candidates, speculative decoding options, community quality
reports, and an adversarial audit of current assumptions for the QuantZhai 26GB VRAM
setup (Qwen3.6-35B-A3B MoE, llama.cpp, 256K context, agentic coding use case).

**Date:** 2026-05-31

---

## Documents

### Pass 1 — Qwen-family analysis
| File | Contents |
|---|---|
| [sota-coding-models.md](sota-coding-models.md) | Qwen3.6-27B dense, QwQ-32B, DeepSeek-R1-Distill-32B, Gemma 3 27B, Mistral Small 3.1: VRAM, benchmarks, GGUF |
| [mtp-speculative-models.md](mtp-speculative-models.md) | MTP in llama.cpp (merged May 16 2026), flags, TPS uplift, native vs draft model approaches |
| [community-reports.md](community-reports.md) | Quant quality reports, fine-tune comparisons, real-world TPS, KV cache gotchas |
| [adversarial-review.md](adversarial-review.md) | Challenges: MoE vs dense, 256K cost, abliteration quality, PCIe split |

### Pass 2 — Broad landscape sweep
| File | Contents |
|---|---|
| [reasoning-distilled-models.md](reasoning-distilled-models.md) | Reasoning-distilled and "stolen reasoning" models: R1-Distill, QwQ, Skywork-OR1, Phi-4-reasoning, AM-Thinking, Claude→Qwen transfers, DeepSeek V4 distills |
| [alternative-models-landscape.md](alternative-models-landscape.md) | Non-Qwen alternatives: Devstral Small 2, Gemma 4, Phi-4, Llama, Mixtral, InternLM, EXAONE, Yi, Command R+, model merges |
| [community-buzz-may2026.md](community-buzz-may2026.md) | What's genuinely exciting: APEX quant, MiniMax M2.7, TurboQuant KV, ik_llama.cpp, HyperClovaX, Skywork-OR1 |
| [extreme-quant-tradeoffs.md](extreme-quant-tradeoffs.md) | 70B at Q2_K vs 32B at Q4_K_M: speed, quality, context — settled verdict |

### Synthesis
| File | Contents |
|---|---|
| [verdict.md](verdict.md) | Full synthesised recommendations and immediate actions |

---

## Quick Summary

### Highest-impact immediate action
Enable MTP on the existing MTP-Preserved GGUFs already in `var/models/`:
```bash
--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75
```
llama.cpp merged MTP support 2026-05-16. Free TPS gain with no downloads.

### Most important model discovery
**Mistral Devstral Small 2 (24B, May 2026)** is a purpose-built agentic coding model at 72.2%
SWE-bench Verified, ~14GB at Q4_K_M. It wasn't on the radar in pass 1 because it came out mid-research.

**Qwen3.6-27B dense** is still the community consensus pick (77.2% SWE-bench), but Devstral
competes directly and may have better tool-use ergonomics for coding agents specifically.

You already have a Qwen3.6-27B variant on disk: `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf`
Benchmark it before downloading anything new.

### Immediately actionable (no downloads)
1. Enable MTP: `--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75`
2. Try APEX quant for the current model: `mudler/Qwen3.6-35B-A3B-APEX-GGUF`
3. Benchmark the 27B-NEO already on disk vs the current 35B-A3B

### QwQ-32B hard context limit
**32K tokens, not 128K or 256K.** This is a training limit, not extendable. Dealbreaker for
long multi-file Codex sessions. Exclude from primary Codex backend consideration.
