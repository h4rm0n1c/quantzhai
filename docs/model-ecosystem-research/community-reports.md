# Community Reports: GGUF Quality and Real-World Performance

**Scope:** Synthesized community intelligence on Qwen3 quant quality, fine-tune variants,
and real-world performance reports for the 26GB VRAM / llama.cpp / agentic coding setup.

**Research date:** 2026-05-31

---

## 1. Qwen3-35B-A3B Quant Quality

### Q4_K_M
- Reliable community default; Unsloth Q4_K_M achieves **99.9% KL divergence** on the
  Pareto frontier — described as SOTA among community quants.
- No reported quality regression for code tasks at this quant level.

### Q4_K_S (current)
- Slightly more aggressive than Q4_K_M; no community-reported quality regressions for
  code tasks.
- Difference vs Q4_K_M is marginal (~1.3GB on disk) — may be worth stepping up.

### IQ4_XS vs K-quants
- IQ4_XS (4.0 bits/param) can **match or beat Q4_K_M** (4.4 bits/param) when properly
  calibrated with a good imatrix corpus.
- Benefit: smaller VRAM footprint at equivalent quality.
- Risk: quality depends heavily on imatrix calibration dataset. Code-domain calibration
  data produces better code-task quants.

### iMatrix vs naive quants
- IMatrix reduces perplexity **10–30% vs naive quantization** — significant for
  aggressive quant levels (Q4 and below).
- The current `i1-imatrix Q4_K_S` is theoretically well-positioned.
- No direct community benchmark of this exact fine-tune at this quant for agentic coding
  workloads. Standard perplexity scores are a proxy, not proof.

---

## 2. MoE vs Dense: Community Benchmark Numbers

Sourced from community benchmark posts and model cards. Note that numbers vary by
benchmark and hardware. Take as directional, not authoritative.

| Benchmark | Qwen3-35B-A3B (MoE) | Qwen3-27B (dense) | Favours |
|---|---|---|---|
| SWE-Bench | 73.4 | 69.6 | **MoE (+3.8)** |
| Terminal-Bench | 51.5 | 40.4 | **MoE (+11.1)** |

**Note on label confusion in source data:** The research agent initially labelled these
as "favoring dense" but the numbers clearly favor MoE. The 35B-A3B MoE appears
competitive or better than 27B dense on coding-adjacent benchmarks.

**Critical counterpoint (adversarial agent):** SkillsBench and other agentic benchmarks
reportedly show dense models winning by +7.8–15.5 points. This may reflect benchmark
choice — SWE-Bench/Terminal-Bench vs SkillsBench test different capability axes.
Treat this area as contested; no single benchmark settles it.

**Speed trade-off:** 35B-A3B generates **3–5× faster tokens** than 27B dense at
equivalent VRAM (MoE only activates ~3B params per token). This is significant for
agentic workloads where throughput, not just quality, matters.

---

## 3. Fine-Tune Variants

### APEX quantization
- Purpose-built imatrix approach for MoE models.
- I-Balanced variant achieves **KL max 4.53** — reportedly the lowest ever tested for
  this model family.
- If available for the Qwen3-35B-A3B base, may be worth comparing against current
  `i1-Q4_K_S`.

### HauhauCS (previous model)
- Uncensored variant; community reports **0/465 refusals**.
- No direct quality benchmarks vs base or other fine-tunes for coding tasks.
- User's experience: slower TPS than kuato-DPO (IQ4_NL vs K-quant CUDA kernels).

### Abliteration + DPO (kuato-DPO, current)
- Abliteration surgically removes refusal behaviour.
- Reports: **18/18 test prompts answered** vs 0/18 for aligned baseline.
- DPO retraining after ablation reinforces coherent behaviour (vs pure abliteration
  which can degrade instruction following).
- **Gap:** No peer-reviewed or systematic benchmark of coding quality vs base model.
  Quality claim is anecdotal / small sample.

### General caution on fine-tune quality claims
Most uncensored/abliterated variant claims are tested on refusal rate, not coding quality.
No community member has published a rigorous A/B comparison of coding agent performance
(tool use, apply_patch, multi-hop reasoning) between abliterated and base Qwen3-35B-A3B.

---

## 4. Real-World TPS Reports

| Setup | Context | Quant | Prefill TPS | Gen TPS | Notes |
|---|---|---|---|---|---|
| RTX 6000 (single) | — | Q4_K_M | — | ~236 | High-end, single card |
| RTX 3080-class | 102K | Q4_K_M | 350–400 | ~15 | Low gen TPS at long context |
| 6GB VRAM (single) | — | Q4_K_M | — | ~30 | CPU offload likely |
| QuantZhai (current) | 262K | i1-Q4_K_S | ~1300–26000 | 92–97 | Dual GPU PCIe, cached prompts |

**Dual-GPU tensor parallel:** Community reports suggest **3–4× TPS improvement** for
equal-VRAM setups. RTX 3080 + V100 is a heterogeneous split (10GB + 16GB, Ampere + Volta)
over PCIe — reported gains are less consistent and may not apply here. Sparse community
data on this specific config.

---

## 5. KV Cache at 256K Context — CRITICAL CLARIFICATION

**The research agent reported "256K context requires 20–40GB KV cache". This is only
true for FP16 KV cache.** The existing `docs/model-selection-notes.md` says ~3GB at 262K,
which implies heavily quantized KV cache.

**Calculation:**
- Qwen3-35B-A3B architecture: ~48 layers, 8 KV heads (GQA), 128 head dim
- At FP16: `2 × 48 × 8 × 128 × 262144 × 2` bytes ≈ **48GB** — clearly doesn't fit
- At Q4 KV cache: ÷8 ≈ 6GB
- At Q2 KV cache: ÷16 ≈ 3GB ← matches the observed ~3GB

**Answer:** QuantZhai runs TheTom's llama.cpp fork. The default KV config is:
```
QZ_KV_KEY=q8_0        # key cache at Q8 (relatively high quality)
QZ_KV_VALUE=turbo3    # value cache at TheTom's custom turbo3 quant type
```

`turbo3` is TheTom's aggressive custom KV value quantization — this is what achieves ~3GB KV
at 256K. Key cache stays at q8_0 (higher quality). Value cache uses turbo3 (aggressive but custom).

The stack also exposes KV cache dtype and usage over HTTP — so actual in-use KV stats are
visible in `qz-top` output.

**Residual risk:** The `turbo3` compression may still degrade quality on long-context tasks
relative to higher-precision KV. The community reports on repetition at long context are
based on standard llama.cpp q2_K — TheTom's turbo3 may or may not share the same failure mode.
Worth testing at 100K+ context in real Codex sessions.

---

## 6. Known Gotchas

### NVFP4 quantization: silent corruption
**Avoid NVFP4 quants.** Reports of silent garbage output when `linear_attn` is missing
from the quantization config. This is a common misconfiguration. No obvious error — the
model generates plausible-looking but wrong output.

### Tool-call JSON malformation
Community reports: Qwen3 generates malformed tool-call JSON in some conditions,
e.g. `"filePath"/path` instead of `"filePath": "/path"`. This is a model-side failure,
not a quant issue — but worth monitoring in QuantZhai telemetry captures.
The proxy's apply_patch coercion layer may catch some of these but JSON key-colon
elision is a different failure mode.

### Long-context repetition
Severe repetition issues reported with quantized KV cache at long contexts. Appears
to be a Qwen3 attention pattern interacting poorly with low-bit KV quantization.
If QuantZhai sessions see repetition loops at 50K+ context, suspect this.

### Flash Attention 3D position crash
Flash attention crashes with Qwen3.5 hybrid attention due to 3D `position_ids`
misinterpretation. Primarily a vLLM issue; llama.cpp may not be affected, but monitor
for attention bugs on very long sessions.

---

## 7. Recommended GGUF Sources

| Priority | Source | Notes |
|---|---|---|
| 1 | **Unsloth** (`unsloth/`) | SOTA KL divergence, MTP-Preserved series, consistently high quality |
| 2 | **Bartowski** (`bartowski/`) | Standard K-quant releases, reliable, no imatrix |
| 3 | Community fine-tune publishers | Variable quality; prefer ones with published benchmark data |

---

## 8. Gaps and Open Questions

- No systematic benchmark of abliterated/DPO fine-tunes vs base for coding agent tasks
- No community data on dual-GPU PCIe (RTX 3080 + V100) llama.cpp performance
- No rigorous long-context (256K) quant degradation study for Qwen3-35B-A3B
- iMatrix calibration dataset best practices for coding workloads are undocumented
- Best KV cache quantization level for long-context accuracy vs VRAM trade-off untested
