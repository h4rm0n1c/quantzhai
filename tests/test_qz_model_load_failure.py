"""Tests for proxy/qz_model_load_failure.py — Slice E classifier."""

import unittest

from proxy.qz_model_load_failure import (
    ModelLoadFailure,
    classify_model_load_failure,
)


# ---------------------------------------------------------------------------
# Raw log fixtures — short, realistic excerpts.  No raw log surface in the API.
# ---------------------------------------------------------------------------

_LOG_CUDA_MALLOC = "\n".join([
    "load_tensors: offloaded 49/49 layers to GPU",
    "load_tensors: CUDA0 model buffer size = 7200.42 MiB",
    "cudaMalloc failed: out of memory",
    "alloc_tensor_range: failed to allocate CUDA0 buffer of size 268435456",
    "llama_init_from_model: failed to initialize the context",
])

_LOG_KV_CACHE_OOM = "\n".join([
    "load_tensors: offloaded 49/49 layers to GPU",
    "llama_init_from_model: failed to initialize the context: failed to allocate buffer for kv cache",
    "common_init_from_params: failed to create context with model",
])

_LOG_FIT_PARAMS = "\n".join([
    "common_fit_params: failed to fit params to free device memory",
    "common_init_result: failed to create context with model",
])

_LOG_CTX_ONLY = "\n".join([
    "load_tensors: offloaded 49/49 layers to GPU",
    "common_init_result: failed to create context",
])

_LOG_CTX_INIT_ONLY = "llama_init_from_model: failed to initialize the context"

_LOG_OK = "\n".join([
    "load_tensors: offloaded 49/49 layers to GPU",
    "load_tensors: CUDA0 model buffer size = 7200.42 MiB",
    "INFO: server listening on 0.0.0.0:8080",
])


# ---------------------------------------------------------------------------
# Pattern classification
# ---------------------------------------------------------------------------

class CudaMallocTests(unittest.TestCase):
    def test_cudaMalloc_returns_insufficient_vram(self):
        result = classify_model_load_failure(_LOG_CUDA_MALLOC)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")
        # cudaMalloc and alloc_tensor_range are both VRAM patterns; spec says
        # latest-line wins, so the alloc_tensor_range line is the evidence.
        self.assertIn(result.matched_pattern, (
            "cudaMalloc failed",
            "alloc_tensor_range: failed to allocate",
        ))

    def test_cudaMalloc_alone_returns_insufficient_vram_with_pattern(self):
        result = classify_model_load_failure("cudaMalloc failed: out of memory")
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")
        self.assertEqual(result.matched_pattern, "cudaMalloc failed")

    def test_failed_to_allocate_CUDA_buffer_returns_insufficient_vram(self):
        log = "alloc_tensor_range: failed to allocate CUDA0 buffer of size 4096"
        result = classify_model_load_failure(log)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")


class KVCacheTests(unittest.TestCase):
    def test_failed_to_allocate_kv_cache_returns_insufficient_vram(self):
        result = classify_model_load_failure(_LOG_KV_CACHE_OOM)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")
        # The kv-cache line should be the matched evidence; a downstream
        # "failed to create context" line does NOT override it.
        self.assertIn("kv cache", result.error)


class FitParamsTests(unittest.TestCase):
    def test_failed_to_fit_params_returns_insufficient_vram(self):
        result = classify_model_load_failure(_LOG_FIT_PARAMS)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")


class ContextCreationOnlyTests(unittest.TestCase):
    def test_common_init_result_returns_context_creation_failed(self):
        result = classify_model_load_failure(_LOG_CTX_ONLY)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "context_creation_failed")

    def test_failed_to_init_context_alone_is_context_creation_failed(self):
        result = classify_model_load_failure(_LOG_CTX_INIT_ONLY)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "context_creation_failed")

    def test_common_init_from_params_returns_context_creation_failed(self):
        log = "common_init_from_params: failed to create context with model"
        result = classify_model_load_failure(log)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "context_creation_failed")


class MixedTests(unittest.TestCase):
    """Promotion rule: VRAM line followed by a context-creation line wins as VRAM."""

    def test_cudaMalloc_followed_by_failed_to_create_context_is_vram(self):
        log = "\n".join([
            "cudaMalloc failed: out of memory",
            "common_init_from_params: failed to create context with model",
        ])
        result = classify_model_load_failure(log)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "insufficient_vram")
        self.assertIn("cudaMalloc", result.error)

    def test_context_failure_only_when_no_vram_evidence(self):
        log = "common_init_from_params: failed to create context with model"
        result = classify_model_load_failure(log)
        self.assertIsNotNone(result)
        self.assertEqual(result.error_type, "context_creation_failed")


# ---------------------------------------------------------------------------
# Negative / edge cases
# ---------------------------------------------------------------------------

class NoFailureTests(unittest.TestCase):
    def test_empty_string_returns_none(self):
        self.assertIsNone(classify_model_load_failure(""))

    def test_none_returns_none(self):
        self.assertIsNone(classify_model_load_failure(None))  # type: ignore[arg-type]

    def test_non_string_returns_none(self):
        self.assertIsNone(classify_model_load_failure(12345))  # type: ignore[arg-type]

    def test_clean_load_returns_none(self):
        self.assertIsNone(classify_model_load_failure(_LOG_OK))


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

class SafetyTests(unittest.TestCase):
    def test_error_is_trimmed_to_safe_length(self):
        long_line = "cudaMalloc failed: " + ("X" * 5000)
        result = classify_model_load_failure(long_line)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result.error), 201)  # 200 + ellipsis

    def test_evidence_line_preserved_in_dataclass(self):
        """evidence_line is the original — but error is the safe excerpt."""
        log = "cudaMalloc failed: out of memory at frame 0xDEAD"
        result = classify_model_load_failure(log)
        self.assertEqual(result.evidence_line, log)

    def test_raw_logs_not_returned_in_error(self):
        """The full multi-line log buffer is not part of the error string."""
        result = classify_model_load_failure(_LOG_CUDA_MALLOC)
        self.assertIsNotNone(result)
        # error must be a single line (no embedded newline).
        self.assertNotIn("\n", result.error)


if __name__ == "__main__":
    unittest.main()
