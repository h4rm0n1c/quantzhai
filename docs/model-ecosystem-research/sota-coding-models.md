# SOTA Coding Models for 26GB VRAM / llama.cpp / 256K Context

**Date:** 2026-05-31
**Hardware:** RTX 3080 (10GB) + Tesla V100-SXM2-16GB (16GB) = 26GB total VRAM
**Baseline:** Qwen3.6-35B-A3B (MoE), 262K native context, i1-Q4_K_S (~23GB in-flight)

---

## Executive Summary

Six model candidates evaluated. **Qwen3.6-27B dense (April 2026) is the top recommendation**:
it beats the 397B MoE on SWE-bench, has native 262K context, and fits comfortably in the
26GB envelope at Q4_K_M (~22.3GB in-flight).

**Critical note:** The user already has a Qwen3.6-27B fine-tune on disk:
`Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf`

This model is underutilized and has not been formally benchmarked against the current
35B-A3B baseline.

---

## 1. Qwen3.6-27B Dense ⭐ Top Candidate

### What it is
Qwen's April 2026 flagship dense 27B model. Purpose-built for coding. Beats much larger
MoE models on coding benchmarks despite being 15× smaller by total parameter count.

### VRAM at Q4_K_M + 256K context

| Component | VRAM |
|---|---|
| Model weights (Q4_K_M) | 16.8 GB |
| KV cache (256K context) | 3.5–4.0 GB |
| Runtime overhead | 1.0–1.5 GB |
| **Total estimated** | **21.3–22.3 GB** |

Headroom: **3.7–4.7 GB** — comfortable, room for context spikes.

### Coding benchmarks
| Benchmark | Qwen3.6-27B | Comparison |
|---|---|---|
| SWE-bench Verified | **77.2%** | vs 397B MoE: 76.2% (27B WINS) |
| SWE-bench Pro | **53.5%** | vs 397B MoE: 50.9% (27B WINS) |

This is remarkable: a 27B dense model outperforming a 397B MoE on standard coding evals.

### Context
- **Native 262K context** — matches the baseline requirement exactly
- Extensible to ~1M via ALiBi; VRAM scales linearly with context length

### GGUF availability
- **Unsloth** (`unsloth/Qwen3.6-27B-GGUF`) — official dynamic quants, UD-Q4_K_XL recommended
- Multiple community quantizers available
- Excellent llama.cpp support

### Community reports
- Widely praised; emerging as the go-to dense model for local inference in 2026
- ~50–70 TPS reported at 256K context on comparable hardware
- Consistently high-quality code generation and instruction following

### On disk already
`Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf` is a coding fine-tune of this base.
IQ4_XS is a lower quant than Q4_K_M — worth downloading the Unsloth Q4_K_M variant
for a direct quality comparison.

---

## 2. DeepSeek-R1-Distill-Qwen-32B

### What it is
DeepSeek's reasoning model distilled into the Qwen-32B architecture. Combines reasoning
training with Qwen's instruction-following and coding strength.

### VRAM at Q4_K_M + 256K context

| Component | VRAM |
|---|---|
| Model weights (Q4_K_M) | 20.0–20.5 GB |
| KV cache (256K context) | 3.5–4.0 GB |
| Runtime overhead | 1.0–1.5 GB |
| **Total estimated** | **24.5–26.0 GB** |

Headroom: **0–1.5 GB** — marginal, requires context discipline.

### Coding benchmarks
- Exceptional reasoning + coding combination; outperforms pure-coding models on complex tasks
- SWE-bench tier: ~70%+ estimated based on reasoning model class
- Self-verification and reflection capabilities improve correctness on hard tasks

### Context
- Context window length unclear from community data; likely 128K–256K based on base architecture

### GGUF availability
- Bartowski, Unsloth, Mungert all publish GGUFs; excellent llama.cpp support

### Community reports
- Highly praised for code reasoning and complex problem-solving
- Speed trade-off: reasoning chain adds 3–10× more output tokens; slower wall time
- Well-tested and stable across community

### Recommendation
**Strong candidate for reasoning-intensive agentic work.** VRAM is tight at 256K.
Good as a backup if Qwen3.6-27B underperforms on complex multi-hop tasks.

---

## 3. QwQ-32B

### What it is
Qwen's pure reasoning model; performance comparable to OpenAI o1-mini on math/science/code.

### VRAM at Q4_K_M + 256K context
Same envelope as DeepSeek-R1-Distill-Qwen-32B: **24.5–26.0 GB** (marginal).

### Coding benchmarks
- Exceptional on competitive programming and algorithmic problem-solving
- Similar tier to DeepSeek-R1-Distill; strong on hard verification tasks
- Extended chain-of-thought generation is core to the model

### GGUF availability
- Official GGUF: `Qwen/QwQ-32B-GGUF` on HuggingFace
- Multiple community quantizations available

### Recommendation
**Alternative to DeepSeek-R1-Distill for reasoning-first use cases.** Both are VRAM-tight
at 256K. Prefer if pure reasoning depth is the priority.

---

## 4. Gemma 3 27B

### What it is
Google's 27B dense instruction model. Solid general-purpose. **128K context native** —
extending to 256K requires RoPE scaling with quality degradation.

### VRAM at Q4_K_M + 256K context
~19.5–21.0 GB — good fit, but 256K quality not guaranteed above native 128K.

### Recommendation
**Skip for 256K use case.** If 128K is acceptable, it's a solid alternative to Qwen3.6-27B
with a smaller footprint. Coding quality is good but not flagship-class.

---

## 5. Mistral Small 3.1 24B

### What it is
Mistral's 24B instruction model. **128K context native.** Best speed and smallest footprint
in this candidate set.

### VRAM at Q4_K_M + 256K context
~17.9–19.3 GB — excellent fit, most conservative VRAM footprint. But 256K degrades above 128K.

### Recommendation
**Best fallback if VRAM proves tighter than estimated.** Not for 256K use. Faster
inference than all 32B candidates.

---

## 6. Qwen3-32B (Dense, older generation)

Older generation, superseded by Qwen3.6 variants. VRAM tight (~24.5 GB at 256K).
No recommendation to pursue.

---

## Comparison Table

| Model | VRAM @ Q4_K_M + 256K | Native 256K | Coding tier | Speed | Verdict |
|---|---|---|---|---|---|
| **Qwen3.6-27B** | **~22.3 GB** | **Yes** | **Flagship** | **Fast** | **Top pick** |
| DeepSeek-R1-Distill-32B | ~25.3 GB | Unclear | Flagship+reasoning | Slow | Strong alt |
| QwQ-32B | ~25.3 GB | Unclear | Flagship+reasoning | Slow | Strong alt |
| Gemma 3 27B | ~20.3 GB | No (128K) | Good | Fast | 128K only |
| Mistral Small 3.1 24B | ~18.6 GB | No (128K) | Good | Very fast | 128K only |
| Qwen3-32B | ~24.5 GB | Unclear | Mid | Fast | Skip |

---

## Gaps and Unknowns

- No direct benchmark of Qwen3.6-27B on the 26GB dual-GPU PCIe setup (RTX 3080 + V100)
- DeepSeek-R1-Distill and QwQ 256K context spec is not clearly documented in community data
- The `Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf` on disk is an unknown fine-tune;
  no benchmark data found for this specific variant
- Qwen3.6-27B vs Qwen3.6-35B-A3B direct A/B comparison is not available in community data

---

## Sources

Research via WebSearch (2026-05-31):
- knightli.com — Qwen3.6 local VRAM/quantization table
- willitrunai.com — VRAM guides for Qwen 3.6 27B, Gemma 3, Mistral
- qwen.ai/blog — official Qwen3.6-27B release
- unsloth.ai — QwQ-32B, Mistral Small 3.1, Qwen3.6 run guides
- huggingface.co — Qwen/QwQ-32B-GGUF, Mungert/DeepSeek-R1-Distill-Qwen-32B-GGUF
- simonwillison.net — Qwen3.6-27B coverage
- buildfastwithai.com, codersera.com, insiderllm.com — 2026 local LLM guides
