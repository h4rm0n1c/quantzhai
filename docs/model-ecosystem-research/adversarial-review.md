# Adversarial Review: QuantZhai Model Ecosystem

**Purpose:** Challenge baseline assumptions about the current model selection, context
budget, fine-tune choices, and hardware setup. Weak-point audit, not a validation pass.

**Research date:** 2026-05-31

---

## Finding 1: Is Qwen3-35B-A3B Actually Good for Coding Agents?

### The challenge
MoE models activate only a fraction of their parameters per token (~3B of 35B here).
This means quality-per-VRAM is determined by the active parameter count, not the total.
A 27B dense model uses 27B parameters every token. Is the MoE actually winning?

### The evidence — conflicting benchmarks

| Benchmark | Qwen3-35B-A3B (MoE) | Qwen3-27B (dense) | Source |
|---|---|---|---|
| SWE-Bench | **73.4** | 69.6 | Community report (favors MoE) |
| Terminal-Bench | **51.5** | 40.4 | Community report (favors MoE) |
| SkillsBench | — | **+18.2pp** | Adversarial research pass |
| Agentic benchmark avg | — | **+7.8–15.5pp** | Adversarial research pass |

**These sources conflict.** SWE-Bench and Terminal-Bench favor MoE. SkillsBench and
generic "agentic benchmarks" favor dense. The difference likely reflects:
- SWE-Bench/Terminal-Bench test knowledge breadth (MoE's large parameter reservoir helps)
- SkillsBench-style agentic tests may favor sustained multi-hop reasoning (dense wins)

### Verdict on this finding
**Contested, not settled.** The MoE is not obviously worse. The right answer is a
direct A/B test on real QuantZhai Codex sessions, not benchmark extrapolation.
The speed advantage of MoE (3–5× faster generation at equivalent VRAM) is real and
matters for interactive agentic workloads.

---

## Finding 2: Is 256K Context Worth Its VRAM Cost?

### The challenge
~3GB of the 26GB VRAM budget is consumed by the KV cache at 256K context. That's 11.5%
of total budget. If Codex sessions typically use 7–13K tokens, this is a large headroom
tax for a rarely-exercised ceiling.

**VRAM reclaim if context drops to 128K:** ~1.5GB freed.
**What that buys:** Could upgrade from Q4_K_S to Q4_K_M (~1.3GB larger) with headroom.

### Counterargument
Codex sessions can spike. A long codebase read, a large apply_patch context, or a deep
research session can push 60–100K+ tokens. Hitting the ceiling mid-session causes a
hard failure, not a graceful degradation.

The proxy hold-open principle ("the user sees a pause, not an error") means running
close to context limits is a proxy design risk.

### Verdict
128K is probably sufficient for 95%+ of sessions and would reclaim meaningful VRAM.
But the floor matters more than the average. Recommend monitoring actual context usage
from request captures before cutting the limit.

See `docs/model-selection-notes.md` for the VRAM budget table.

---

## Finding 3: Do Abliterated Fine-Tunes Help or Hurt Coding Quality?

### The challenge
The current model is abliterated + DPO retrained. The claimed benefit is uncensored
instruction following. The risk is that abliteration + DPO on refusal data does not
necessarily improve or preserve coding agent capability.

### Evidence
- No peer-reviewed benchmark of abliterated Qwen3-35B-A3B vs base model for coding tasks
- Community claims ("18/18 test prompts answered") measure refusal rate, not code quality
- DPO retraining after ablation helps coherence but trains on the ablation author's
  preference dataset, which may not align with coding agent tasks

### Verdict
**Unvalidated for this use case.** The abliteration is likely neutral-to-beneficial for
the specific types of content Codex agents generate (code, shell commands, analysis).
But it is not proven. A base Qwen3-35B-A3B GGUF for comparison would settle this.
Cost of the experiment: download one GGUF and run a coding eval.

---

## Finding 4: Is 92–97 TPS Efficient Given the Hardware?

### The challenge
Efficiency metric: TPS per GB VRAM.
- Current setup: ~95 TPS ÷ 26GB = **3.65 TPS/GB**
- Single-GPU comparable setups (community reports): **7.8–8.3 TPS/GB**
- Efficiency ratio: current setup is ~2.2× less efficient per VRAM-GB

### Why this gap exists
This is the PCIe split penalty (see Finding 5). The gap is not evidence of a
misconfigured model — it is the expected cost of using two GPUs over PCIe.

### Verdict
The efficiency gap is real but the *absolute* TPS (92–97) is adequate for interactive
Codex sessions. 1.4–1.6s wall time per response is usable. The comparison to
"single-GPU baselines" is somewhat misleading because those single GPUs (RTX 4090 etc.)
have more VRAM than either GPU in this setup individually.

**The relevant question is:** could the same hardware do better with different software
(e.g., different llama.cpp tensor split settings)? That's worth a tuning pass.

---

## Finding 5: RTX 3080 + V100 PCIe Split — Real Bottleneck?

### The challenge
PCIe Gen3 ×16 bandwidth: 16 GB/s unidirectional. Layer-split operation requires
inter-GPU data transfer on every token. At 256K context with large hidden states,
this may create a synchronization bottleneck.

Community consensus (llama.cpp GitHub): if a model fits on one GPU, single-GPU is
faster. The split is done for VRAM capacity, not speed.

### The specifics of this setup
- V100 SXM2 is connected via PCIe (not NVLink — only in multi-V100 server configs)
- Heterogeneous split (Volta + Ampere) means different CUDA generation capabilities;
  mixed-generation splits can have kernel compatibility overhead
- llama.cpp `--tensor-split` tries to balance load; default split may not be optimal
  for 10GB + 16GB asymmetric cards

### Verdict
**The PCIe overhead is real but unavoidable given the VRAM envelope.** 27B dense at
Q4_K_M might fit entirely on the V100 (16GB) with no KV cache — freeing the RTX 3080
from the split. This would be a significant architectural simplification worth testing.

27B dense Q4_K_M: ~16–17GB. At 128K context KV cache: ~1.5GB. Total: ~18–18.5GB.
Does not fit on V100 alone, but the split would be smaller (less data crossing PCIe
per token with a smaller model).

---

## Finding 6: Is "Native-MTP-Preserved" Actually Usable?

### The challenge (original adversarial claim)
The original adversarial pass claimed MTP only works in vLLM, not llama.cpp, making
the MTP-Preserved GGUFs cargo-culted dead weight.

### Correction: This claim is factually wrong
**llama.cpp PR #22673 merged MTP support on 2026-05-16.** MTP is functional in current
llama.cpp builds. The MTP-Preserved GGUFs in `var/models/` are **immediately usable**:

```bash
--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75
```

Confirmed speedup: **1.71× on Qwen3.6-27B dense**. MoE results untested on 26GB dual-GPU
PCIe, but MTP acceptance rates for tool-call JSON are ~94.3% — excellent for this workload.

### Verdict
Enable MTP on the existing GGUFs immediately. The adversarial argument collapses against
current llama.cpp state. This is a free TPS gain that requires only a flag change.

See `mtp-speculative-models.md` for full details.

---

## Finding 7: Overall Verdict

### What the evidence actually supports

| Claim | Verdict |
|---|---|
| 35B-A3B MoE is clearly superior to 27B dense | **Contested** — benchmark-dependent |
| 256K context is necessary | **Unvalidated** — monitor actual usage |
| Abliterated fine-tune improves coding quality | **Unproven** — refusal metric, not coding metric |
| PCIe split is a bottleneck | **True, but unavoidable** given current VRAM |
| MTP doesn't work in llama.cpp | **False** — merged May 16, 2026 |
| 92–97 TPS is acceptable | **True** for interactive Codex latency |

### Recommended actions (in priority order)

**Immediate (no new downloads):**
1. Enable MTP on existing MTP-Preserved GGUFs — free TPS gain
2. Audit actual peak context usage from request captures to validate 256K headroom need
3. Check `--tensor-split` tuning for the asymmetric 10GB+16GB split

**Short-term (low cost):**
4. Download Qwen3-35B-A3B base (non-abliterated) at Q4_K_M and run a direct coding eval
   against the kuato-DPO variant to validate the fine-tune benefit
5. Test 128K context with the reclaimed VRAM going to Q4_K_M quant quality bump

**Medium-term:**
6. Download Qwen3-27B dense MTP-Preserved at Q4_K_M and benchmark directly
   against the 35B-A3B in real Codex sessions — not synthetic benchmarks
7. Profile whether 27B dense fits better on the V100 alone and reduces cross-GPU traffic

### What not to do based on this review
- Do not switch to 27B dense based on adversarial agent numbers alone — the SWE-Bench
  and Terminal-Bench numbers favor MoE, and the speed advantage of MoE is real
- Do not cut context to 128K without monitoring actual usage distribution first
- Do not remove MTP-Preserved GGUFs — they are directly usable and valuable now
