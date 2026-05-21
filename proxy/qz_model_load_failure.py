"""qz.model_load_failure — classify llama.cpp model load failures from logs.

Backend HTTP /health can return 200 while the selected model failed to create
context (e.g. OOM during KV-cache allocation).  This module reads the recent
container log text and classifies the failure so that:

  * ``last_load_result`` / ``last_load_error`` / ``last_load_error_type`` in
    qz.model_state.v1 can be updated by the reload / select-and-restart paths.
  * /qz/model/status and /qz/control-plane can surface ``insufficient_vram``
    and ``context_creation_failed`` independently of HTTP health.

Raw logs are never returned by the API — only the matched evidence line,
trimmed to 200 chars.

See ``docs/proxy-model-selection-authority.md`` §10.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelLoadFailure:
    """A single classified failure read from recent container logs."""

    error_type: str          # 'insufficient_vram' | 'context_creation_failed' | 'unknown'
    error: str               # one-line safe excerpt (≤200 chars)
    matched_pattern: str     # which substring fired
    evidence_line: str       # the original log line (untrimmed)


# Substring patterns ordered specific → general.
# Each tuple: (substring_match, error_type).
_VRAM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("cudaMalloc failed",                          "insufficient_vram"),
    ("failed to allocate CUDA buffer",             "insufficient_vram"),
    ("failed to allocate buffer for kv cache",     "insufficient_vram"),
    ("failed to fit params to free device memory", "insufficient_vram"),
    ("alloc_tensor_range: failed to allocate",     "insufficient_vram"),
)

_CONTEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("common_init_result: failed to create context",      "context_creation_failed"),
    ("common_init_from_params: failed to create context", "context_creation_failed"),
    ("failed to create context with model",               "context_creation_failed"),
    # "failed to initialize the context" alone is context_creation_failed; it
    # gets promoted to insufficient_vram when a VRAM pattern fires earlier
    # (the typical real-world ordering).
    ("failed to initialize the context",                  "context_creation_failed"),
)


def classify_model_load_failure(log_text: str) -> "ModelLoadFailure | None":
    """Return the latest classified failure, or None when no signal present.

    Behaviour:
      * Scans every line.
      * Promotes a downstream "failed to initialize the context" / "failed to
        create context with model" to ``insufficient_vram`` when an earlier
        line matched a VRAM pattern (within the same log buffer).
      * When both categories are present without a VRAM-before-context ordering,
        the latest-line category wins.
      * Returns None for empty or non-string input.
    """
    if not isinstance(log_text, str) or not log_text:
        return None

    latest_vram: tuple[int, str, str] | None = None  # (idx, line, pattern)
    latest_ctx: tuple[int, str, str] | None = None

    for idx, line in enumerate(log_text.splitlines()):
        for pattern, _et in _VRAM_PATTERNS:
            if pattern in line:
                latest_vram = (idx, line, pattern)
                break
        for pattern, _et in _CONTEXT_PATTERNS:
            if pattern in line:
                latest_ctx = (idx, line, pattern)
                break

    if latest_vram is None and latest_ctx is None:
        return None

    if latest_vram is not None and latest_ctx is not None:
        v_idx, v_line, v_pat = latest_vram
        c_idx, c_line, c_pat = latest_ctx
        # VRAM line at or before context error → the VRAM is the root cause.
        if v_idx <= c_idx:
            return ModelLoadFailure(
                error_type="insufficient_vram",
                error=_concise(v_line),
                matched_pattern=v_pat,
                evidence_line=v_line,
            )
        return ModelLoadFailure(
            error_type="context_creation_failed",
            error=_concise(c_line),
            matched_pattern=c_pat,
            evidence_line=c_line,
        )

    if latest_vram is not None:
        v_idx, v_line, v_pat = latest_vram
        return ModelLoadFailure(
            error_type="insufficient_vram",
            error=_concise(v_line),
            matched_pattern=v_pat,
            evidence_line=v_line,
        )

    assert latest_ctx is not None  # the union is non-empty
    c_idx, c_line, c_pat = latest_ctx
    return ModelLoadFailure(
        error_type="context_creation_failed",
        error=_concise(c_line),
        matched_pattern=c_pat,
        evidence_line=c_line,
    )


def _concise(text: str, max_len: int = 200) -> str:
    """Trim a log line for safe one-line API surface."""
    s = (text or "").strip()
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s
