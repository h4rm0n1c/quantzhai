# Reasoning-Distilled and "Stolen Reasoning" Model Landscape

**Research date:** 2026-05-31
**Hardware target:** 26GB VRAM (RTX 3080 10GB + V100 16GB), llama.cpp, GGUF, agentic coding

---

## 256K Context Is Viable for Dense 32B on This Stack

**Correction from earlier research:** QuantZhai runs TheTom's llama.cpp fork, not mainline.
The default KV cache config is `QZ_KV_KEY=q8_0 / QZ_KV_VALUE=turbo3`. TheTom's `turbo3` is
an aggressive custom KV value quantization that brings 256K KV overhead to ~3GB for any model.

This means:
- Dense 32B at Q4_K_M: ~20GB weights + ~3GB turbo3 KV at 256K = **~23GB — fits in 26GB**
- The 256K constraint is a stack capability, not a model-architecture constraint

The earlier claim that dense 32B models can't do 256K in 26GB was based on FP16 or q8_0 KV
assumptions. With turbo3 KV, the 256K envelope opens up to any model that fits in ~23GB of weights.

---

## 1. Official Reasoning Distills

### DeepSeek-R1-Distill-Qwen-32B ⭐

- **Q4_K_M on disk:** ~20GB
- **Context sweet spot:** 128K (256K technically possible with aggressive KV quant, quality uncertain)
- **LiveCodeBench:** 57.2 — strong multi-step coding reasoning
- **GGUF:** Unsloth, Bartowski, Mungert (all well-maintained)

Community feedback: Best for multi-step agentic tasks where the reasoning chain actually matters.
Faster than QwQ per token due to architecture optimisation.

**Known llama.cpp issue:** Reported eval slowdown bug — check GH issue #11361 if inference is slower than expected.

### QwQ-32B (Alibaba) ⭐

- **Q4_K_M on disk:** 19.9GB
- **Context:** 32K native — **hard ceiling; this is a significant limitation**
- **LiveCodeBench:** 63.4 — **beats R1-Distill-Qwen-32B by 6.2 points**

Community: described as "smaller and sharper" than R1-Distill. Better for code review / IDE copilot
scenarios where you iterate on a smallish context window. The 32K ceiling is a real problem for
long Codex sessions that read multiple files.

### DeepSeek-R1-Distill-Qwen-14B

- **Q4_K_M:** ~11GB — leaves significant VRAM headroom
- Good secondary option if 256K is a hard requirement and you can accept the smaller model
- LiveCodeBench not directly reported; expect ~50 range (proportionally lower than 32B)

### DeepSeek-R1-Distill-Llama-70B

- ~40GB at Q4_K_M — **does not fit** in 26GB at any useful quality level

---

## 2. RLVR / GRPO Reasoning (Beyond Distillation)

### Skywork-OR1-32B ⭐ (interesting, watch closely)

- **Architecture:** Pure GRPO-based RL training — no distillation step from a teacher model
- **AIME24:** 82.2 | **AIME25:** 73.3 | **LiveCodeBench:** 63.0
- Outperforms DeepSeek-R1 on math despite being 32B
- **Q4_K_M VRAM:** ~24GB — fits in 26GB, tight but viable at 128K context
- **GGUF:** Not yet confirmed in community; likely available via quantizers soon
- Source: [SkyworkAI/Skywork-OR1 GitHub](https://github.com/SkyworkAI/Skywork-OR1)

**Why interesting:** GRPO-trained reasoning is argued to "stick" better during domain shift than
supervised distillation. The gains generalise more. If that holds for coding, this could be more
reliable than a distill across varied agent task types.

### Skywork-OR1-7B

- AIME24: 70.2 (exceptional for 7B), LiveCodeBench: 47.6
- ~9GB Q4_K_M — trivial VRAM
- Not a contender for the main slot but interesting as a fast draft model or secondary agent

### AM-Thinking-v1 (dataset, not a model)

- 350k verified reasoning traces; fine-tunes from this beat **both** R1-distilled and Qwen3
  synthetic-data models
- AIME2024: 84.3% | AIME2025: 72.2% | MATH500: 98.4% | LiveCodeBench: 65.9
- Adaptive chain-of-thought: 15k–23k tokens for hard tasks, ~3.5k for easy (vs uniform verbosity
  in standard distills)
- **Relevance:** If you ever fine-tune a smaller model, AM-Thinking traces are the gold calibration
  set in 2026 — better than R1 and Qwen3 synthetic data

---

## 3. Microsoft Phi-4 Reasoning ⭐ (most surprising finding)

- **14B parameters** — fits trivially (~8–9GB Q4_K_M, ~12GB with 256K KV)
- **Outperforms DeepSeek-R1-Distill-Llama-70B on several reasoning benchmarks** — remarkable for 14B
- GGUF: `unsloth/Phi-4-reasoning-GGUF` (confirmed)
- Community reception: genuine buzz; "Phi gets overlooked but this variant is different"

**Gotcha:** Microsoft small models historically less "agentic-friendly" for tool use (weaker at
structured JSON output, function calling schemas) vs Qwen/DeepSeek. No agentic coding benchmark
comparison found yet.

**Use case for QuantZhai:** Could work as a fast secondary model / caveman profile slot rather than
the primary backend. 8–9GB leaves enormous VRAM headroom for other work.

---

## 4. Ministral-3-14B-Reasoning (Mistral, May 2026)

- Specifically fine-tuned for reasoning on math, coding, STEM
- Q4_K_M: ~7–8GB — fits easily
- GGUF: `unsloth/Ministral-3-14B-Reasoning-2512-GGUF` (confirmed)
- Pairs Mistral's agentic tool-use training with reasoning; positioned for function-calling agents
- No LiveCodeBench vs R1-Distill comparison found yet

---

## 5. NVIDIA Nemotron-3-Nano-30B (watch — novel architecture)

- **Architecture:** Hybrid Mamba-Transformer MoE — NOT standard transformer attention
- **Context:** 1M-token context window (if you can afford the KV cache)
- 30B → ~15–17GB Q4_K_M
- Generates `<thinking>` segments inline before final answer
- **GGUF:** Not yet found; NVIDIA releases typically lag community quantization by weeks–months

**Why interesting:** Mamba layers have O(n) vs O(n²) attention complexity — 1M context doesn't
require the same KV cache explosion as transformer models. If this is true in practice, this could
be the first model that genuinely supports 256K+ context without the KV cache tax.

**Status:** Very new; no community benchmarks on llama.cpp yet. Needs watching.

---

## 6. "Stolen Opus / GPT-4 Distills" — Mostly Superseded

### Dolphin 3.0 R1 Mistral 24B (Eric Hartford)

- Uncensored + reasoning hybrid; ~15GB Q4_K_M
- Still actively maintained but no longer reference quality
- **Use case:** Uncensored + reasoning for adversarial / red-team testing only
- No 2026 LiveCodeBench scores found

### Hermes 4 (Nous Research)

- Now a Qwen3-based fine-tune (35B-A3B MoE variant)
- Nous has pivoted to Hermes Agent framework (Feb 2026)
- Strength is tool use and agent scaffolding over raw reasoning quality

### Orca-2, WizardLM, OpenHermes

- **Historical reference only** as of May 2026
- Superseded by official distills and Qwen3.6
- No new models in these lineages found in 2026 search results

---

## 7. New: Claude → Open Model Transfers

- **Qwen3.5-27B-Claude-4.6-Opus-Uncensored-Distilled** — confirmed published
- First confirmed Claude Opus → Qwen supervised fine-tune with reasoning transfer
- 27B → ~14–16GB Q4_K_M, fits easily in 26GB with 256K context
- Status: **very new, minimal community feedback yet**
- Quality unknown for coding tasks; reasoning transfer quality unvalidated

---

## 8. DeepSeek V4 Distills

- **DeepSeek-V4** uses "On-Policy Distillation" (OPD), trained on outputs from 10 teacher models
- One confirmed small distill: **Qwen3.5-9B-DeepSeek-V4-Flash**
  - GGUF: `Jackrong/Qwen3.5-9B-DeepSeek-V4-Flash-GGUF`
  - 9B → ~5–6GB Q4_K_M, trivial VRAM
  - Positioning: "efficient reasoning," but 9B is likely outclassed by 14B+ for agentic work
- No V3 distill series found (community appears to have skipped V3 and gone straight to V4)

---

## Benchmark Comparison Table

| Model | Params | LiveCodeBench | Q4_K_M VRAM | 256K viable? | GGUF? |
|---|---|---|---|---|---|
| QwQ-32B | 32B | **63.4** | ~20GB | ❌ (32K ctx only) | ✅ |
| Skywork-OR1-32B | 32B | 63.0 | ~24GB | Unclear | ❓ soon |
| Phi-4-reasoning | 14B | Not published | ~9GB | ✅ (plenty of headroom) | ✅ |
| Qwen3.6-35B-A3B | 35B MoE | ~57+ | ~20GB | ✅ (MoE KV advantage) | ✅ |
| R1-Distill-Qwen-32B | 32B | 57.2 | ~21GB | ⚠️ 128K practical | ✅ |
| Qwen3.6-27B dense | 27B | ~77 SWE-bench | ~17GB | ✅ | ✅ |
| Ministral-3-14B-R | 14B | Not published | ~8GB | ✅ | ✅ |
| R1-Distill-Qwen-14B | 14B | ~50 est | ~11GB | ✅ | ✅ |

---

## Community Sentiment (May 2026)

- **Distillation fatigue**: researchers are pivoting to RL-based reasoning (GRPO, RLVR) as more
  durable than supervised distillation
- **QwQ-32B** has a dedicated community arguing it's superior to R1-Distill for code tasks — the
  32K context ceiling is the only real objection
- **Thinking-mode models** (Qwen3.6, Nemotron-3) seen as next frontier after pure distillation
- **DARE/TIES reasoning merges** are not winning on benchmarks; technique has promise but production
  merged models aren't outperforming the source models in practice

---

## Recommended Experiments for QuantZhai

1. **R1-Distill-Qwen-32B at Q4_K_M + 128K context** — reasoning distill that fits cleanly; test
   against current 35B-A3B on real Codex sessions
2. **Phi-4-reasoning as caveman/fast slot** — 8–9GB, could coexist with main model or serve as
   fast secondary inference path
3. **Watch Skywork-OR1-32B** — no GGUF yet but coming; GRPO-trained reasoning may generalise
   better to agentic task variety
4. **Nemotron-3-Nano-30B when GGUF lands** — Mamba O(n) attention means 256K without KV explosion;
   could be the first genuinely 256K-capable dense model for this VRAM envelope
