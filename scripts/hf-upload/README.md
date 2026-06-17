---
license: mit
library_name: gguf
base_model: sandeshrajx/qwen3.5b-24b-a10b
tags:
- qwen3.5
- moe
- gguf
- iq4_nl
- quantzhai
- quantized
---

# qwen3.5b-24b-a10b IQ4_NL GGUF

GGUF quantization of [sandeshrajx/qwen3.5b-24b-a10b](https://huggingface.co/sandeshrajx/qwen3.5b-24b-a10b) — a 24B-parameter MoE model with 10B active parameters per token. Architecture is Qwen3.5 MoE.

**Source:** [sandeshrajx/qwen3.5b-24b-a10b](https://huggingface.co/sandeshrajx/qwen3.5b-24b-a10b)  
**Converted by:** [QuantZhai](https://github.com/h4rm0n1c/quantzhai) benchmark pipeline  
**Quantization:** IQ4_NL (importance-matrix 4-bit non-linear)

## Model Details

| Property | Value |
|---|---|
| Architecture | Qwen3.5 MoE (Dense + Mamba-2 SSM interleaved) |
| Parameters | 24B total, 10B active per token |
| Experts | 39, 8 active per token |
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
Engine: llama.cpp with TurboQuant KV (q8_0 K / turbo3 V)  
Perplexity: [`macvox68`](https://github.com/h4rm0n1c/macvox68) code corpus, ctx=4096, stride=512

| Metric | Cold | Warm |
|---|---|---|
| PPL | 8.0217 | 3.9654 |
| TPS | 26.1 tok/s | 31.4 tok/s |
| TTFT | 1919 ms | 1594 ms |

### QuantZhai Ranking

**Rank #30 of 46** — combined score 50.5 (equal-weight: TPS, PPL, convergence).

## Usage

```bash
llama-cli -m qwen3.5b-24b-a10b-IQ4_NL.gguf \
  -p "Write a mergesort in Python" \
  -n 1024 -t 12 --temp 0.6 --top-p 0.95

llama-server -m qwen3.5b-24b-a10b-IQ4_NL.gguf \
  --host 0.0.0.0 --port 8080 -ngl 99 -t 12 \
  --cache-type-k q8_0 --cache-type-v turbo3
```

Recommended: temp 0.6, top-p 0.95, context up to 256K.

## License

MIT (this quantization).  
Source model by [sandeshrajx](https://huggingface.co/sandeshrajx) — review its license separately.
