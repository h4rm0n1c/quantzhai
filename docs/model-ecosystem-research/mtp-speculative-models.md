# MTP and Speculative Decoding Research

**Scope:** Multi-Token Prediction (MTP) and speculative decoding options for the
QuantZhai 26GB VRAM setup (Qwen3-35B-A3B MoE, llama.cpp, 256K context).

**Research date:** 2026-05-31

---

## 1. Native MTP in Qwen3 — llama.cpp Status

**Status: MERGED AND STABLE (as of 2026-05-16)**

llama.cpp PR #22673 merged MTP support on 2026-05-16. It is no longer experimental.

### Enabling flags
```
--spec-type draft-mtp
--spec-draft-n-max 2          # or 3; community finds 2 optimal for most workloads
--spec-draft-p-min 0.75       # reject speculative tokens below this confidence
```

### Confirmed TPS uplift
| Model | Baseline TPS | With MTP | Multiplier |
|---|---|---|---|
| Qwen3.6-27B (dense) | 38 tok/s | 65 tok/s | **1.71×** |
| Qwen3.6-35B-A3B (MoE, RTX 6000) | — | 240 tok/s | — |

Note: No public benchmark for Qwen3.6-35B-A3B on a **26GB** (dual-GPU PCIe) setup yet.
The RTX 6000 result is single-card, so it doesn't apply directly.

### Critical requirement
Must use **MTP-Preserved GGUFs** — standard quantizations strip the MTP heads.
VRAM overhead: ~1–2GB (already in the weights, no separate model required).

---

## 2. MTP-Preserved GGUF Availability

Primary publisher: **Unsloth** (HuggingFace `unsloth/` namespace)

Available MTP-Preserved models (confirmed published):
- `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` (all standard quants: Q4_K_S, Q4_K_M, IQ4_XS, etc.)
- `unsloth/Qwen3.6-27B-MTP-GGUF`
- `unsloth/Qwen3.5-4B-MTP-GGUF`
- `unsloth/Qwen3.5-0.8B-MTP-GGUF`

All quantizations in the MTP-Preserved series preserve the MTP heads.
Compatible with current llama.cpp releases (May 2026+).

The user already has:
- `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS.gguf`
- `Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S.gguf`

These are usable immediately with the `--spec-type draft-mtp` flags.

---

## 3. Traditional Speculative Decoding on Qwen3-35B-A3B: FAILS

**Critical finding: conventional speculative decoding is net-negative for MoE models.**

Community benchmark (thc1006, RTX 3090 setup):
- 19 configurations tested: Qwen3.6-35B-A3B (target) + Qwen3.5-0.8B (draft)
- **Zero configurations achieved net speedup** — all were 3–12% slower than baseline

Root cause — **expert budgeting in MoE**:
- MoE architecture requires ~94 token batch to saturate 256 experts
- Draft trees activate different expert subsets on each verification pass
- Verification overhead exceeds draft speedup gain on consumer hardware

**Implication:** Do not use `--draft` with a separate small model for the 35B-A3B.
Use native MTP instead.

---

## 4. MTP vs Draft Model Trade-off

| Approach | Applicable to | VRAM cost | Net effect |
|---|---|---|---|
| Native MTP (`--spec-type draft-mtp`) | MoE (35B-A3B) and dense (27B) | ~1–2GB | **Recommended** |
| Separate draft model | Dense models only | ~2–3GB | MoE: net negative |

For the current 35B-A3B setup: **MTP only**.
For a dense 27B replacement: **MTP recommended** (1.71× confirmed).

---

## 5. Coding Agent Workload — MTP Acceptance Rates

MTP performs especially well on structured output:

| Output type | MTP acceptance rate |
|---|---|
| Tool-calling JSON | 94.3% |
| General code generation | ~85–90% |
| Natural language prose | ~75–80% |

Tool eval benchmark: Qwen3.6 scored **100/100** with MTP enabled.

Prefill (context loading) is ~2× slower with MTP — this affects the first token latency
but does not affect decode throughput. For agentic workloads where decode (tool-call JSON
output) dominates wall time, MTP is a net win.

Estimated effect on QuantZhai baseline (92–97 TPS decode):
**→ ~120–160 TPS decode** (rough estimate; no confirmed 26GB dual-GPU PCIe benchmark)

---

## 6. Smaller Models with Native MTP

| Model | MTP available | VRAM | Use as draft? |
|---|---|---|---|
| Qwen3.5-0.8B-MTP | Yes (Unsloth) | ~0.5GB | Too small; can't fix expert budgeting |
| Qwen3.5-4B-MTP | Yes (Unsloth) | ~2.5GB | Overkill; native MTP is simpler |
| Qwen3.5-9B-MTP | Yes (Unsloth) | ~5GB | Would eat into the 26GB envelope |
| DeepSeek V3/R1 | MTP heads exist | — | No benchmarks published |
| Gemma 4-26B-A4B | MoE, same constraint | — | Same expert budgeting failure |

No 7B–14B dense MTP-enabled models published as of research date.

---

## 7. Build Requirements and Known Gotchas

### CUDA build flags (important for RTX 3080 Ampere + V100 Volta)
```bash
-DGGML_CUDA=ON
-DCMAKE_CUDA_ARCHITECTURES="70;86"   # V100=70, RTX3080=86
-DGGML_CUDA_FA_ALL_QUANTS=ON
```
Note: The adversarial review cites `120` as the CUDA architecture — that is Blackwell
(RTX 5000 series). For RTX 3080 (Ampere) use `86`; for V100 (Volta) use `70`.

### Known stability issues
- Instability at scale (large batch/parallel sessions) — workaround: `--parallel 1`
- FP16 weights variant reduces instability if available
- Apple Metal: MTP is net-negative on Metal (Mac setups) — not relevant here

### Recommended tuning starting point
```
--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75
```
Increase `--spec-draft-n-max` to 3 if acceptance rate stays above ~85%.

---

## 8. Gaps and Open Questions

- No confirmed benchmark for MTP on Qwen3.6-35B-A3B on a **dual-GPU PCIe 26GB** setup.
  The RTX 6000 (single card) result of 240 TPS is not comparable.
- Cross-GPU PCIe overhead during MTP verification passes is unknown. The speculation
  draft tokens still need to be verified across both GPUs — PCIe bandwidth may limit gains.
- No confirmed benefit of `Qwen3.5-0.8B` as draft for the dense 27B variant of Qwen3.
- MTP stability under QuantZhai's concurrent-request profile (multiple Codex sessions)
  is untested.

---

## Recommendation

**Do NOT blindly enable MTP on the existing GGUFs.** The MTP-Preserved heretic variants are
already on disk but MTP is intentionally disabled in `models-preset.ini` (issue #80): at 19GB
weights the 35B-A3B + MTP heads exceed the 26GB budget at 256K context, causing OOM.

**Correct path to MTP:**
1. Download the APEX quant: `mudler/Qwen3.6-35B-A3B-APEX-GGUF` (~12GB, vs 19GB current)
2. At 12GB weights + 3GB turbo3 KV + ~2GB MTP heads ≈ 17GB — fits comfortably
3. Then enable: `--spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-p-min 0.75`

This gives two wins: better imatrix quality AND MTP TPS uplift, at lower VRAM than current.

Do **not** use traditional draft-model speculative decoding with the 35B-A3B MoE —
community data confirms it is net-negative due to MoE expert budgeting.

**Adversarial correction:** An earlier draft of these notes claimed MTP doesn't work in
llama.cpp. That was incorrect — MTP was merged May 16, 2026 and is functional. The claim
may have been based on pre-merge documentation. The NTP-Preserved GGUFs already in the
user's `var/models/` are directly usable with current llama.cpp.
