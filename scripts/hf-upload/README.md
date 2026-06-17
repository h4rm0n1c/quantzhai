---
license: mit
library_name: gguf
base_model: Qwen/Qwen3.5-24B-A10B
tags:
- qwen
- qwen3.5
- moe
- gguf
- iq4_nl
- quantzhai
- quantized
---

# Qwen3.5-24B-A10B IQ4_NL GGUF

This is a GGUF quantization of [Qwen3.5-24B-A10B](https://huggingface.co/Qwen/Qwen3.5-24B-A10B) — a 24B-parameter MoE model from Alibaba's Qwen team with 10B active parameters per token.

**Original model:** Qwen/Qwen3.5-24B-A10B by Alibaba Group  
**Source safetensors:** [sandeshrajx/qwen3.5b-24b-a10b](https://huggingface.co/sandeshrajx/qwen3.5b-24b-a10b)  
**Converted by:** [QuantZhai](https://github.com/h4rm0n1c/quantzhai) benchmark pipeline  
**Quantization:** IQ4_NL (importance-matrix 4-bit non-linear)

## Model Details

| Property | Value |
|---|---|
| Architecture | Qwen3.5 MoE (Dense + Mamba-2 SSM interleaved) |
| Parameters | 24B total, 10B active per token |
| Experts | 39, 8 active per token (shared expert + routed) |
| Context length | 262144 (256K) |
| Hidden size | 3072 |
| Attention heads | 32, KV heads = 2 |
| Head dim | 256 |
| RoPE | MRope (multimodal), theta = 10,000,000 |
| SSM | Mamba-2 inspired conv/state-space per 4th layer |
| Quantization | IQ4_NL (4.50 bpw) |
| File size | 13.9 GB |
| Tokenizer | Qwen2 (GPT-2 based BPE, vocab 248,320) |

## Benchmarks

Hardware: dual-GPU (RTX 3080 10GB + V100-SXM2 32GB, 42 GB total)  
Engine: Qwen3.6 fork of llama.cpp with TurboQuant KV (q8_0 K / turbo3 V)  
Perplexity: [`macvox68`](https://github.com/h4rm0n1c/macvox68) code corpus, ctx=4096, stride=512

| Metric | Cold | Warm |
|---|---|---|
| PPL | 8.0217 | 3.9654 |
| TPS | 27.2 tok/s | 34.5 tok/s |
| TTFT | 1837 ms | 1451 ms |

### Comparison: IQ4_NL vs IQ3_S (same architecture)

| Quant | Size | Warm PPL | Warm TPS | Combined Score |
|---|---|---|---|---|
| **IQ4_NL** (this) | **13.9 GB** | **3.97** | **34.5** | **50.8** |
| IQ3_S | 11.0 GB | 4.03 | 26.3 | 49.3 |

IQ4_NL delivers 31% higher throughput (+8.2 TPS) with marginally better perplexity, at the cost of +2.9 GB on disk.

### QuantZhai Ranking

**Rank #30 of 46** — combined score 50.8 (equal-weight: TPS, PPL, convergence).

## Usage

```bash
# llama-cli
llama-cli -m qwen3.5b-24b-a10b-IQ4_NL.gguf \
  -p "Write a mergesort in Python" \
  -n 1024 -t 12 --temp 0.6 --top-p 0.95

# llama-server
llama-server -m qwen3.5b-24b-a10b-IQ4_NL.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -t 12 \
  --cache-type-k q8_0 --cache-type-v turbo3
```

Recommended: temp 0.6, top-p 0.95, context up to 256K.

## License

MIT (this quantization).  
Base model [Qwen3.5-24B-A10B](https://huggingface.co/Qwen/Qwen3.5-24B-A10B) is by Alibaba Group — review its license separately.  
Safetensors source by [sandeshrajx](https://huggingface.co/sandeshrajx).
