# Model Ecosystem Research: Verdict

**Last updated:** 2026-05-31 — incorporates full two-pass sweep + stack reality checks

---

## What we now know that changes everything

### TheTom turbo3 KV: 256K is viable for dense 32B
`QZ_KV_VALUE=turbo3` brings 256K KV overhead to ~3GB for any model on this stack.
Dense 32B at Q4_K_M: ~20GB weights + ~3GB KV = ~23GB — fits in 26GB.
All candidates in the research are viable at 256K. The architecture-specific KV-head
advantage of MoE is not required.

### MTP is disabled because of OOM — not because of tooling gaps
`models-preset.ini` (issue #80): the 19GB heretic 35B-A3B GGUFs + MTP draft heads push
over 26GB at 256K context, causing OOM. The flags work. The quant is too large.

**The fix is APEX quant, not flag tweaking.** APEX brings the 35B-A3B from ~19GB to ~12GB.
At ~12GB weights + ~3GB turbo3 KV + ~2GB MTP heads = ~17GB — MTP fits with room to spare.

### On disk right now
```
19G  Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.IQ4_XS.gguf
19G  Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved.Q4_K_S.gguf  ← active
15G  Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf                          ← untested
```

The 27B-NEO is a coding fine-tune at a lower quant level. It has never been benchmarked
against the active 35B-A3B baseline.

---

## Download List

### Priority 1 — Immediate quality wins

**A. APEX quant of Qwen3.6-35B-A3B**
```
mudler/Qwen3.6-35B-A3B-APEX-GGUF
```
- ~12GB on disk (vs 19GB current)
- MoE-specific imatrix: beats Q8_0 on perplexity at 38% smaller
- Unlocks MTP at 256K (OOM issue goes away at this size)
- Same base model, same fine-tune lineage question — cleanest comparison

**B. Qwen3.6-27B dense base at Q4_K_M (Unsloth)**
```
unsloth/Qwen3.6-27B-GGUF  →  Qwen3.6-27B-UD-Q4_K_XL.gguf
```
- ~17GB on disk
- Proper clean baseline for the 27B family (the NEO-CODE variant on disk is a fine-tune at a lower quant)
- 77.2% SWE-bench Verified; beats 397B MoE on coding
- Dense, no MTP — but MTP not needed at ~17GB since there's headroom for a 27B MTP variant if wanted

---

### Priority 2 — New architecture challengers

**C. Mistral Devstral Small 2 (24B)**
```
bartowski/Devstral-Small-2-GGUF  →  Devstral-Small-2-Q4_K_M.gguf
```
⚠️ Verify exact repo name on HuggingFace before downloading — released May 2026,
naming may vary. Search: `mistralai devstral small 2 GGUF`
- ~14GB on disk
- 72.2% SWE-bench Verified; purpose-built for agentic coding
- Native FIM (fill-in-the-middle) and multi-file diff support
- Probably the most interesting non-Qwen test given the use case

**D. Gemma 4 27B-A4B (Google)**
```
unsloth/gemma-4-27b-it-GGUF  →  gemma-4-27b-it-Q4_K_M.gguf
```
⚠️ Verify: Google naming may be `gemma-4-27b-it` or `gemma-4-31b-it` depending on param counting
- ~15-17GB on disk
- MoE efficiency (4B active from 27-31B total); multimodal bonus
- ~70% SWE-bench Verified; community "emerging favourite" for concurrent-request workloads
- llama.cpp support added April 2, 2026

---

### Priority 3 — Reasoning distill comparison

**E. DeepSeek-R1-Distill-Qwen-32B**
```
bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF  →  DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf
```
- ~20GB on disk; fits at 256K with turbo3 KV
- LiveCodeBench 57.2; strong on multi-step reasoning chains
- Test specifically for complex multi-hop agentic tasks, not general use
- Slower due to reasoning output verbosity — not a daily-driver replacement

---

### Watchlist (no GGUF yet as of 2026-05-31)

**Skywork-OR1-32B** — GRPO-trained reasoning (no distillation), LiveCodeBench 63.0.
Watch `SkyworkAI/Skywork-OR1` on HuggingFace; add Q4_K_M when community quant lands.

---

## What NOT to download

| Model | Reason |
|---|---|
| QwQ-32B | Hard 32K context ceiling — training limit, not extendable |
| Llama 3.3 70B (any quant) | 3-8 TPS on this hardware at viable quant levels |
| Qwen3-32B (older gen) | Superseded by Qwen3.6 variants |
| Mistral Small 3.1 24B | 128K native context only |
| Gemma 3 27B | 128K native context only |

---

## After downloading: test order

1. **APEX 35B-A3B** — drop-in for current kuato profile; measure TPS and enable MTP flags
2. **27B-NEO vs 27B-base** — compare fine-tune quality with identical quant level
3. **27B-base vs current 35B-A3B** — the core MoE vs dense quality question
4. **Devstral Small 2** — separate agentic coding eval; FIM and multi-file diff tests
5. **Gemma 4** — speed + multimodal comparison
6. **R1-Distill-32B** — complex reasoning session test only

---

## Summary table

| Model | HF source | ~Disk | Est. VRAM @256K | MTP? | Priority |
|---|---|---|---|---|---|
| Qwen3.6-35B-A3B APEX | `mudler/Qwen3.6-35B-A3B-APEX-GGUF` | ~12GB | ~15GB | ✅ enabled | **1** |
| Qwen3.6-27B UD-Q4_K_XL | `unsloth/Qwen3.6-27B-GGUF` | ~17GB | ~20GB | no | **1** |
| Devstral Small 2 Q4_K_M | `bartowski/Devstral-Small-2-GGUF` ⚠️verify | ~14GB | ~17GB | no | **2** |
| Gemma 4 27B-A4B Q4_K_M | `unsloth/gemma-4-27b-it-GGUF` ⚠️verify | ~15-17GB | ~18-20GB | no | **2** |
| R1-Distill-Qwen-32B Q4_K_M | `bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF` | ~20GB | ~23GB | no | **3** |
