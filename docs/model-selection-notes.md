# Model Selection Notes

## Hardware envelope

- RTX 3080: 10GB VRAM
- Tesla V100-SXM2-16GB: 16GB VRAM
- Total: 26GB VRAM

## Current model (2026-05-11)

`OpenYourMind-Qwen3.6-35B-A3B-kuato-DPO-abliterated-uncensored.i1-Q4_K_S`
- 20GB on disk, ~23GB VRAM in-flight at 262K context (~3GB KV cache)
- ~3GB headroom unused
- Gen: ~92-97 TPS, Prompt: ~1300-26000 TPS (cache-dependent)
- Gen wall time: ~1.4-1.6s per response

Improvement over previous model (HauhauCS IQ4_NL):
- Gen TPS: 70 → 92-97 (~35% faster)
- Prompt TPS: dramatically faster (K-quant CUDA kernels vs IQ4_NL lookup table)
- Gen wall time: 2.0s → 1.4-1.6s (~25-30% faster)

## VRAM budget analysis

With 26GB total and ~3GB KV cache overhead at 262K context:

| Quant | Model size | Est. in-flight | Headroom | Viable |
|---|---|---|---|---|
| i1-Q4_K_S (current) | 20GB | ~23GB | ~3GB | ✓ |
| i1-Q4_K_M | 21.3GB | ~24.3GB | ~1.7GB | ✓ likely |
| i1-Q5_K_S | 24.1GB | ~27.1GB | -1.1GB | ✗ too tight |
| i1-Q5_K_M | 24.8GB | ~27.8GB | -1.8GB | ✗ OOM risk |

**Next logical step:** try i1-Q4_K_M on this same model — 1.3GB more for a full
quality tier bump, fits within the envelope.

## Open: model audit

We're not using the full VRAM budget. Worth a sweep of what's available in
the 20-24GB size class across:

- Other Qwen3.6-35B-A3B fine-tunes (other DPO datasets, different abliteration
  approaches, newer post-kuato variants)
- APEX vs standard imatrix quality comparison at equivalent sizes
- Whether newer quantisation approaches (K_P, APEX v2, etc.) change the
  quality/size tradeoff at this envelope
- Any non-Qwen MoE models in the same parameter class that fit the envelope
  and might be better for coding agent use

The goal: given 26GB, what's the highest quality coding-agent-capable model
we can run at 262K context? We're currently at i1-Q4_K_S. The ceiling may be
i1-Q4_K_M on this base model, or a different fine-tune entirely.

## Fine-tune lineage notes

`kuato-DPO-abliterated` (OpenYourMind) — abliterated then DPO-retrained.
DPO retraining after ablation reinforces coherent uncensored behaviour rather
than just punching out refusals. Produces more consistent instruction
following than pure abliteration. Benchmark comparison against HauhauCS
IQ4_NL pending (run: `kuato-baseline`).

`HauhauCS-Aggressive-IQ4_NL` (previous) — pure abliteration, no DPO
retraining. Standard imatrix quant. IQ4_NL non-uniform lookup table is
slower to dequantise than K-quants.
