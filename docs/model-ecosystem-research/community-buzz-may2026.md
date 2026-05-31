# Community Buzz — What's Genuinely Interesting (May 2026)

**Scope:** r/LocalLLaMA, HuggingFace discussions, llama.cpp community, benchmarking spaces.
Models and techniques getting *real* positive feedback, not just download-count inertia.

**Research date:** 2026-05-31

---

## Tier 1 — Genuine Cross-Platform Consensus

### Mistral Devstral Small 2 (24B)
**What it is:** Purpose-built agentic coding model, released May 2026.
- 72.2% SWE-bench Verified, 90.1% HumanEval
- Native FIM (fill-in-the-middle) and multi-file diff support
- ~14GB at Q4_K_M, excellent fit for 26GB
- Community: "Solves the 'I want local agentic coding' problem properly"

### Qwen3.6-27B Dense
**What it is:** April 2026 dense 27B. The community default pick right now.
- 77.2% SWE-bench Verified; most recommended for local agentic coding
- Native 262K context, consistent tool-call accuracy praised specifically
- Broad source: "If you're doing agentic coding locally, Qwen3.6-27B is the workhorse"

### APEX Quantization (MoE-specific imatrix)
**What it is:** Not a model — a quant technique specifically for MoE architectures.
- Layer-wise precision gradient: sensitive edge layers stay high-precision, redundant middles compressed
- **Beats Q8_0 and F16 on perplexity while being 38% smaller**
- Real numbers: APEX Mini (Qwen3.5-35B, 12.2GB) beats bartowski IQ2_M (11.3GB):
  PPL 7.088 vs 7.303, HellaSwag 81.0% vs 80.3%, MMLU 41.3% vs 39.6%
- Same quant level → genuinely better quality through MoE-aware compression
- Available now: `mudler/Qwen3.5-35B-A3B-APEX-GGUF` and `mudler/Qwen3.6-35B-A3B-APEX-GGUF`
- **This is directly applicable to QuantZhai's current model — worth testing immediately**

---

## Tier 2 — Strong Signal, Emerging

### MiniMax M2.7 (230B total, 10B active MoE)
**What it is:** MiniMax's sparse MoE flagship. SWE-bench Verified 78%. Ranked #1 open-weight for
agentic coding on Artificial Analysis index (as of May 2026).

**VRAM caveat:** Community reports claim ~12-14GB GGUF — but this is suspicious. A 230B model at
Q4_K_M should be ~130GB. The "12-14GB" claim may refer to active-parameter memory during inference
(not full model load) or a specific quantized release I couldn't verify. **Do not assume this fits
in 26GB without testing.** The "10B active" claim is compute-active, not VRAM-active — all 230B
parameters still need to be paged. Flagged as unverified.

GGUF: HuggingFace official + community quantizers. Investigate before attempting.

### Gemma 4 31B-A4B
**What it is:** Google's MoE model, April-May 2026. 4B active parameters from 31B total.
- "Matches 744B-parameter models on AI Arena benchmarks" — strong MoE efficiency claim
- Multimodal (image inputs natively)
- ~17GB Q4_K_M, fits in 26GB with context headroom
- llama.cpp support: added April 2, 2026 (catching up fast)
- Community: "Emerging favourite for systems that need concurrent requests"
- Community: "Arguably best inference-cost-per-capability available"

### Dynamic imatrix calibration (bartowski / Kalomaze approach)
**What it is:** Quant *technique*, not a type. Diverse calibration data (chat + code + reasoning +
tool-calling, no Wikipedia overfitting) vs standard calibration.
- Real impact: I-Balanced (diverse imatrix) 4.53 KL divergence vs Balanced (standard) 14.14
- Same quant level → vastly better quality when calibration set includes your workload domain
- **Practical rule:** When choosing between two Q4_K_M files, prefer the one from bartowski or
  similar using diverse imatrix. The difference is particularly pronounced for coding + tool-use.
- Now standard practice for good quantizers as of May 2026

---

## Tier 3 — Edge Cases and Surprises

### TheTom `turbo3` KV Cache — Already Running in QuantZhai

QuantZhai runs TheTom's llama.cpp fork, not mainline. The default KV config is:
```
QZ_KV_KEY=q8_0        # key cache at Q8
QZ_KV_VALUE=turbo3    # value cache at TheTom's custom turbo3 quant type
```

`turbo3` is TheTom's aggressive custom KV value quantization — this is what gets 256K KV
down to ~3GB for any model (not just the MoE), and why the docker image is built in-house.
The stack also exposes KV cache stats over HTTP as a custom feature.

**Research correction:** The earlier analysis that said "dense 32B can't do 256K in 26GB" was
wrong because it assumed FP16 or q8_0 KV. With turbo3:
- Dense 32B at Q4_K_M: ~20GB weights + ~3GB turbo3 KV at 256K = **~23GB — fits**
- This makes R1-Distill-Qwen-32B, Devstral Small 2, Gemma 4 31B-A4B all viable at 256K

The ICLR 2026 "TurboQuant" paper is a coincidentally named academic project; the capability
in this stack comes from TheTom's fork directly.

### ik_llama.cpp (ikawrakow's fork)
**What it is:** llama.cpp fork with:
- Custom quantization types via regex (mix different quant levels per layer)
- Optimised MoE operations
- FlashMLA for DeepSeek attention
- Bitnet support
- Community cooking custom quants for 16GB VRAM + 32K context in this fork

**For QuantZhai:** Not a drop-in replacement for llama.cpp but shows what's possible with
layer-targeted quantization. The APEX approach above is a more accessible version of this idea.

### HyperClovaX SEED-Think 14B/32B (Naver, Korean)
**What it is:** Strong Korean-origin model getting genuine buzz in non-English communities.
- 14B variant: ~10GB Q4_K_M
- "HyperClovaX 8B Omni" is any-to-any multimodal (text, image editing, speech, TTS in one model)
- Coding quality: competitive, not flagship
- Community: "Korean models are genuinely good but flying under English-community radar"
- Not competitive with Qwen3.6-27B for pure coding agents, but architecturally interesting

### Skywork-OR1-32B (RL-trained reasoning, no distillation)
**What it is:** GRPO-trained reasoning (not distilled from a teacher model) — LiveCodeBench 63.0,
AIME24 82.2. Comparable to QwQ-32B on coding.
- GRPO argument: reasoning "sticks" better during domain shift than supervised distillation
- ~24GB Q4_K_M — fits but tight
- GGUF: not yet confirmed; coming from community quantizers
- Status: Watch. When GGUF lands, worth a direct Codex session test

### Phi-4-reasoning (Microsoft, 14B)
**What it is:** 14B reasoning fine-tune that beats DeepSeek-R1-Distill-Llama-70B on some reasoning
benchmarks. ~9GB Q4_K_M.
- Genuine community surprise
- **Caveat:** Phi family historically weaker on agentic tool-use/JSON output vs Qwen/DeepSeek
- Use case: secondary model slot, not primary Codex backend

### Llama 4 Scout (256K, multi-file context)
**What it is:** Meta's 2026 long-context model. 256K practical context (10M theoretical).
- Standard inference: ~20-24GB Q4 — fits in 26GB at 256K if KV quantized
- Agentic angle: hold entire codebases in context between turns
- Community discussion active; direct coding agent benchmarks sparse

---

## Key Technical Trends (Not Models)

### "Big model at low quant" is settled — the answer is no
Research is unambiguous:
- 70B at Q2_K: 3-8 TPS on this hardware, severe coherence degradation at coding tasks
- 32B at Q4_K_M beats 70B at Q2_K on agentic coding by every metric that matters
- See `extreme-quant-tradeoffs.md` for full analysis

### QwQ-32B has a hard 32K context ceiling
Not a soft limit — QwQ was trained at 32K. Community sources confirm this is not extendable via
RoPE for useful quality. For Codex sessions that read many files, this is a dealbreaker.

### RLVR / GRPO is the hot research frontier
- GRPO-trained models (Skywork-OR1) show better domain generalisation than supervised distills
- "Distillation fatigue" setting in — researchers pivoting to RL-based approaches
- Production GGUF models still thin on the ground (Skywork-OR1 is the main example)

### MoE efficiency is the consensus architecture direction
- MiniMax M2.7, Gemma 4-A4B, Qwen3.6-35B-A3B — all MoE with sparse activation
- 4-10B active parameters from 30-230B total → fast inference from large capacity reservoir
- The "pure dense" winners (Qwen3.6-27B, Devstral Small 2) compete by being small enough that
  their dense cost is still lower than MoE overhead at local scale

---

## Ranked List for 26GB Agentic Coding

| Rank | Model | Why interesting | VRAM fit |
|---|---|---|---|
| 1 | Devstral Small 2 24B | Purpose-built agentic coding; 72% SWE | 14GB ✅ |
| 2 | Qwen3.6-27B dense | Proven default; 77% SWE | 17GB ✅ |
| 3 | Gemma 4 31B-A4B | MoE efficiency + multimodal | 17GB ✅ |
| 4 | APEX quant (technique) | Better quality from current model | free ✅ |
| 5 | Skywork-OR1-32B | RL reasoning, no distill | 24GB ✅ (when GGUF lands) |
| 6 | Phi-4-reasoning 14B | 14B beating 70B distills | 9GB ✅ |
| 7 | TurboQuant KV (technique) | 5-8× context expansion | not yet |
| 8 | MiniMax M2.7 | 78% SWE, 10B active | ❓ verify VRAM |
| 9 | HyperClovaX 14B | Under-radar gem | 10GB ✅ |
| 10 | Llama 4 Scout | Long context game-changer | ~20-24GB ✅ |
