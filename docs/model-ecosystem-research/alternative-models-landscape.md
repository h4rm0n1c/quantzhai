# Alternative Models Landscape: Beyond Qwen

**Research date:** 2026-05-31
**Scope:** Llama, Mistral, Gemma, Phi, Cohere, InternLM, EXAONE, Yi, model merges
**Hardware:** 26GB VRAM, llama.cpp, GGUF, 256K context, agentic coding

---

## Headline Finding: Two Strong Non-Qwen Candidates

### Mistral Devstral Small 2 (24B dense) ⭐
**Purpose-built agentic coding model. Fits comfortably. Strong benchmarks.**

- Q4_K_M weights: ~14GB — leaves 12GB for KV + overhead at 256K
- **SWE-bench Verified: 72.2%** (vs Qwen3.6-27B dense at 77.2%)
- **HumanEval: 90.1%**
- Supports fill-in-the-middle (FIM) and multi-file diffs natively
- Community: "First time a small dense model actually matches what you'd want from an agentic coder"
- Release: May 2026

Note: Devstral 2 (large, 123B) also exists but requires 65GB VRAM — not viable.

### Gemma 4 31B-A4B (MoE, 4B active) ⭐
**Google's MoE efficient model. 256K context. Multimodal. Emerging favourite.**

- Architecture: 31B total, ~4B active per token (similar MoE efficiency philosophy to Qwen3.6-35B-A3B)
- Q4_K_M weights: ~17.4GB — fits with reasonable KV headroom
- **SWE-bench Verified: ~70%** (competitive)
- Native 256K context
- Multimodal (image inputs) — bonus for UI/screenshot-driven coding tasks
- llama.cpp support: added April 2, 2026 (community support catching up fast)
- Community: "Genuine contender if you want MoE speed + coding quality"
- "Matches models 5x its parameter count" — the 4B active means generation is fast

---

## Llama 3.x Family

### Llama 3.3 70B — verdict: does not fit
- Q4_K_M: ~40-48GB — **not viable at 26GB**
- Q2_K: 26.4GB — technically fits but 3-8 TPS estimated; 30-60s per generation
- Coherence degrades severely at 2-bit for multi-step coding reasoning
- **Conclusion:** See `extreme-quant-tradeoffs.md` — this path is a dead end

### Llama 3.1/3.2 small (8B)
- Fits trivially; not competitive with 27-32B models
- Useful only as a fast draft/secondary model

---

## Phi-4 Family (Microsoft)

### Phi-4 14B
- Q4_K_M: ~8-9GB — trivial VRAM, enormous headroom
- Strong on reasoning benchmarks; known for "punching above weight" on math
- **Caveat for QuantZhai:** Phi historically weaker on structured JSON/tool-use (function calling schemas, agentic loops) vs Qwen/DeepSeek
- **SWE-bench:** Not published; community suggests tier-C for agentic work
- Use case: fast secondary inference (caveman profile slot), code analysis, not primary Codex backend

### Phi-4-reasoning (Microsoft, 2026)
- Also 14B; fine-tuned specifically for reasoning
- Reportedly outperforms DeepSeek-R1-Distill-Llama-70B on several reasoning benchmarks — remarkable for 14B
- GGUF: `unsloth/Phi-4-reasoning-GGUF`
- Same tool-use caveat applies
- Community buzz: "Phi gets overlooked but this variant is different"

---

## Mistral / Mixtral

### Mixtral-8x7B (47B MoE, ~13B active)
- Q4_K_M: ~26.4GB — right at the limit; tight KV budget
- Coding: ~40% SWE-bench — significantly behind Qwen3.6-27B
- Community: "Works technically but why not Qwen 27B? Better quality, leaves VRAM for context"
- **Conclusion:** Only if you specifically want an older MoE for concurrent-request throughput experiments

### Mistral Nemo 12B
- Q4_K_M: ~7GB — fits very easily
- Decent general model; not competitive with 27-32B for agentic coding
- Use case: secondary/fast profile only

### Codestral (Mistral coding model)
- Coding-specific fine-tune from Mistral; older generation
- Superseded by Devstral for coding agent use cases

---

## Cohere Command R+ (35B)
- Q4_K_M: ~24GB — fits, but very tight KV budget at 256K
- Community reports: lags Qwen3.6-35B-A3B on coding tasks
- Designed for RAG + instruction-following, not optimised for code generation or multi-step tool use
- **Conclusion:** Pass; better options exist at this VRAM level

---

## InternLM 2.5-20B (Shanghaitech)
- Q4_K_M: ~11-12GB — excellent fit, plenty of headroom
- Strong on math and multilingual; decent on coding
- Benchmarks: rivals Gemma 2 27B despite 25% smaller; not as strong as Qwen3.6-27B on coding
- Community: "Underrated for math-heavy coding, ignored for pure agentic loops"
- GGUF: available from community quantizers
- **Conclusion:** Valid if math-heavy coding is the use case; second-tier for pure agentic work

---

## EXAONE 3.5 (LG AI Research)
- Sizes: 7.8B (excellent fit), 32B (tight at Q4)
- Coding + math + bilingual (EN/Korean)
- GGUF: available but limited quantization options vs bartowski/Unsloth for Qwen
- Community: "Works fine, no one talks about it." Not bad, just eclipsed by Qwen momentum
- **Conclusion:** Worth experimenting with if Korean language tasks ever become relevant

---

## Yi-1.5-34B (01.ai)
- Q4_K_M: ~20GB — good fit
- All-rounder; competitive but not exceptional on coding
- Community: "Still works, people use it, but Qwen 3.6 is better for coding"
- Active maintenance unclear as of May 2026

---

## Granite 3.x (IBM)
- Available size: 8B only — doesn't utilise 26GB envelope
- Focus: code-specific narrow tasks, not general agentic work
- Minimal community adoption for the agentic use case
- **Conclusion:** Skip

---

## Model Merges (DARE/TIES/SLERP)
- Active as of May 2026; tools like mergekit available
- **Community consensus: "Merging works technically but you're better off running a proven single model"**
- Takes 8-16h to merge and quantize; community waits for official releases instead
- DARE/TIES reasoning merges are not outperforming source models on benchmarks in practice
- No well-regarded coding-specific merge found that beats the clean model it was derived from

---

## Comparison Table

| Model | Params | Q4_K_M VRAM | 256K viable | Coding tier | GGUF? |
|---|---|---|---|---|---|
| **Devstral Small 2** | 24B dense | ~14GB | ✅ (ample headroom) | **72% SWE** | ✅ |
| **Gemma 4 31B-A4B** | 31B MoE (4B active) | ~17GB | ✅ | **~70% SWE** | ✅ |
| Phi-4-reasoning | 14B | ~9GB | ✅ | Unclear coding | ✅ |
| InternLM 2.5-20B | 20B | ~11GB | ✅ | Good/math | ✅ |
| Mixtral-8x7B | 47B MoE | ~26.4GB | ⚠️ tight | ~40% SWE | ✅ |
| EXAONE 3.5 32B | 32B | ~19GB | ✅ | Unknown | ✅ limited |
| Yi-1.5-34B | 34B | ~20GB | ✅ | Mid | ✅ |
| Command R+ | 35B | ~24GB | ⚠️ tight | Below Qwen | ✅ |
| Llama 3.3 70B Q2_K | 70B | ~26GB | ❌ | Degraded | ✅ (but don't) |
