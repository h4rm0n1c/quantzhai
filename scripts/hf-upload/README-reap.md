---
license: apache-2.0
library_name: gguf
base_model: sandeshrajx/Qwen3.5-24B-A3B-REAP-0.32
tags:
- qwen3.5
- moe
- gguf
- iq4_nl
- reap
- pruned
- quantzhai
- quantized
---

# Qwen3.5-24B-A3B-REAP-0.32 IQ4_NL GGUF

GGUF quantization of [sandeshrajx/Qwen3.5-24B-A3B-REAP-0.32](https://huggingface.co/sandeshrajx/Qwen3.5-24B-A3B-REAP-0.32) — a REAP-pruned 24B total, **~3B active** MoE model.

**REAP** (Razor Edge And Pruning, [arxiv:2510.13999](https://arxiv.org/abs/2510.13999)) is a structured pruning technique that reduces the base Qwen3.5 model while preserving capability.

**Source:** [sandeshrajx/Qwen3.5-24B-A3B-REAP-0.32](https://huggingface.co/sandeshrajx/Qwen3.5-24B-A3B-REAP-0.32)  
**Converted by:** [QuantZhai](https://github.com/h4rm0n1c/quantzhai) benchmark pipeline  
**Quantization:** IQ4_NL with importance matrix (imatrix from sandeshrajx)

## Model Details

| Property | Value |
|---|---|
| Architecture | Qwen3.5 MoE, REAP-pruned |
| Parameters | 24B total, ~3B active |
| Blocks | 40 |
| Experts | 175 (REAP-split), 8 active per token |
| Context length | 262144 (256K) |
| Hidden size | 3072 |
| Attention heads | 32, KV heads = 2 |
| Quantization | IQ4_NL (4.58 bpw) with imatrix |
| File size | 14.0 GB |

## Benchmarks

Hardware: dual-GPU (RTX 3080 10GB + V100-SXM2 32GB)  
Engine: llama.cpp with TurboQuant KV (q8_0 K / turbo3 V)  
Perplexity: [`macvox68`](https://github.com/h4rm0n1c/macvox68) code corpus, ctx=4096, stride=512

| Metric | Cold | Warm |
|---|---|---|
| PPL | 3.0205 | **1.8298** |
| TPS | 33.6 tok/s | **45.9 tok/s** |
| TTFT | 1486 ms | 1090 ms |

### QuantZhai Ranking

**Rank #15 of 47** — combined score 63.6 (equal-weight: TPS, PPL, convergence).  
Higher than many larger dense models — REAP pruning + IQ4_NL quantization is an efficient combination.

## Usage

```bash
llama-cli -m Qwen3.5-24B-A3B-REAP-0.32-IQ4_NL.gguf \
  -p "Write a mergesort in Python" \
  -n 1024 -t 12 --temp 0.6 --top-p 0.95

llama-server -m Qwen3.5-24B-A3B-REAP-0.32-IQ4_NL.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -t 12 \
  --cache-type-k q8_0 --cache-type-v turbo3
```

## License

Apache 2.0 (this quantization).  
Source model by [sandeshrajx](https://huggingface.co/sandeshrajx) under Apache 2.0 — see [arxiv:2510.13999](https://arxiv.org/abs/2510.13999).
