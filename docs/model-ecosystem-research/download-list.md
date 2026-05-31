# Model Download List

**Date:** 2026-05-31 — filenames verified against live HuggingFace pages
**Target dir:** `var/models/`
**Purpose:** Benchmarking against current Qwen3.6-35B-A3B heretic Q4_K_S baseline
(92–97 TPS, 256K context, turbo3 KV, `QZ_KV_KEY=q8_0 / QZ_KV_VALUE=turbo3`).

Download to `var/models/` directly:
```bash
huggingface-cli download <repo> "<filename>" --local-dir /home/harri/turboquant/quantzhai/var/models/
```

---

## Priority 1

### A. Qwen3.6-35B-A3B APEX-I-Compact ← MTP candidate
**Repo:** `mudler/Qwen3.6-35B-A3B-APEX-GGUF`
**File:** `Qwen3.6-35B-A3B-APEX-I-Compact.gguf`
**Disk:** ~17GB

```bash
huggingface-cli download mudler/Qwen3.6-35B-A3B-APEX-GGUF \
  "Qwen3.6-35B-A3B-APEX-I-Compact.gguf" \
  --local-dir /home/harri/turboquant/quantzhai/var/models/
```

VRAM at 256K: 17GB weights + 3GB turbo3 KV + 2GB MTP heads = **22GB** ✅
MTP enabled via `models-preset.ini` (see below).

**Other APEX variants for reference (do not download for MTP):**
- `APEX-I-Quality.gguf` (22GB): 22+3=25GB — fits without MTP but not with
- `APEX-I-Balanced.gguf` (24GB): 24+3=27GB — **does not fit at all** at 256K

### B. Qwen3.6-27B dense base (Unsloth)
**Repo:** `unsloth/Qwen3.6-27B-GGUF`
**File:** `Qwen3.6-27B-UD-Q4_K_XL-00001-of-00002.gguf` + `…-00002-of-00002.gguf` (split)
**Disk:** ~17.6GB total

```bash
huggingface-cli download unsloth/Qwen3.6-27B-GGUF \
  --include "Qwen3.6-27B-UD-Q4_K_XL*" \
  --local-dir /home/harri/turboquant/quantzhai/var/models/
```

VRAM at 256K: 17.6GB + 3GB = **~21GB** ✅
Clean base model — the NEO-CODE on disk is a fine-tune at IQ4_XS, not a fair comparison.

---

## Priority 2

### C. Devstral Small 2 (Unsloth)
**Repo:** `unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF`
**File:** `Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf`
**Disk:** 14.3GB

```bash
huggingface-cli download unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF \
  "Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf" \
  --local-dir /home/harri/turboquant/quantzhai/var/models/
```

VRAM at 256K: 14.3GB + 3GB = **~17GB** ✅
72.2% SWE-bench Verified; native FIM and multi-file diff support.

### D. Gemma 4 26B-A4B (Unsloth)
**Repo:** `unsloth/gemma-4-26B-A4B-it-GGUF`
**File:** `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`
**Disk:** 16.9GB

```bash
huggingface-cli download unsloth/gemma-4-26B-A4B-it-GGUF \
  "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf" \
  --local-dir /home/harri/turboquant/quantzhai/var/models/
```

VRAM at 256K: 16.9GB + 3GB = **~20GB** ✅
MoE, 4B active from 26B total. ~70% SWE-bench Verified. Multimodal.

---

## Priority 3

### E. DeepSeek-R1-Distill-Qwen-32B (Bartowski)
**Repo:** `bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF`
**File:** `DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf`
**Disk:** 19.85GB

```bash
huggingface-cli download bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF \
  "DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf" \
  --local-dir /home/harri/turboquant/quantzhai/var/models/
```

VRAM at 256K: 19.85GB + 3GB = **~23GB** ✅
57.2 LiveCodeBench. Test on complex multi-hop sessions specifically.

---

## VRAM fit summary

| # | Model | Disk | Weights + KV | + MTP | Fits? |
|---|---|---|---|---|---|
| A | APEX-I-Compact | 17GB | 20GB | 22GB | ✅ MTP viable |
| B | 27B UD-Q4_K_XL | 17.6GB | 20.6GB | — | ✅ |
| C | Devstral Small 2 | 14.3GB | 17.3GB | — | ✅ |
| D | Gemma 4 26B-A4B | 16.9GB | 19.9GB | — | ✅ |
| E | R1-Distill-32B | 19.85GB | 22.85GB | — | ✅ tight |
| — | Current baseline | 19GB | 22GB | OOM | ❌ MTP |
| — | APEX-I-Balanced | 24GB | 27GB | — | ❌ won't fit |
