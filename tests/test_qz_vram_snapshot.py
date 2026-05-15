"""Tests for qz.vram.snapshot.v1 builder (proxy/qz_vram_snapshot.py)."""
import json
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_vram_snapshot import (
    VRAM_SNAPSHOT_SCHEMA,
    _assemble_snapshot,
    _build_context,
    _parse_nvidia_smi_gpus,
    _parse_prometheus_text,
    _probe_backend_process,
    build_vram_snapshot,
    get_cached_vram_snapshot,
)


# ---------------------------------------------------------------------------
# 1. Schema / never-raise guarantees
# ---------------------------------------------------------------------------

class SchemaTests(unittest.TestCase):
    def test_schema_present(self):
        snap = build_vram_snapshot()
        self.assertEqual(snap["schema"], VRAM_SNAPSHOT_SCHEMA)
        self.assertEqual(snap["schema"], "qz.vram.snapshot.v1")

    def test_always_returns_dict(self):
        snap = build_vram_snapshot()
        self.assertIsInstance(snap, dict)

    def test_json_serialisable(self):
        snap = build_vram_snapshot()
        json.dumps(snap)  # must not raise

    def test_no_raise_without_nvidia_smi(self):
        """Should not raise even when nvidia-smi is absent."""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
            snap = build_vram_snapshot()
        self.assertEqual(snap["schema"], VRAM_SNAPSHOT_SCHEMA)

    def test_required_top_level_fields(self):
        snap = build_vram_snapshot()
        for f in ("schema", "ok", "timestamp", "source", "confidence",
                  "backend_confirmed", "host_observed", "backend_metrics_available",
                  "notes", "totals", "components", "gpus", "backend", "context"):
            self.assertIn(f, snap, f"missing field: {f}")

    def test_no_nan_inf_in_output(self):
        snap = build_vram_snapshot()
        text = json.dumps(snap)
        self.assertNotIn("Infinity", text)
        self.assertNotIn("NaN", text)


# ---------------------------------------------------------------------------
# 2. nvidia-smi parsing
# ---------------------------------------------------------------------------

class ParseNvidiaSmiGpusTests(unittest.TestCase):
    def _mock_smi(self, stdout: str, returncode: int = 0):
        proc = mock.MagicMock()
        proc.returncode = returncode
        proc.stdout = stdout
        proc.stderr = ""
        return proc

    def test_parses_single_gpu(self):
        smi_out = "0, NVIDIA A100, 55, 20000, 40960, 200, 65, 4, 16\n"
        with mock.patch("subprocess.run", return_value=self._mock_smi(smi_out)):
            gpus = _parse_nvidia_smi_gpus()
        self.assertEqual(len(gpus), 1)
        g = gpus[0]
        self.assertEqual(g["index"], "0")
        self.assertAlmostEqual(g["used_mib"], 20000.0)
        self.assertAlmostEqual(g["total_mib"], 40960.0)
        self.assertAlmostEqual(g["available_mib"], 40960.0 - 20000.0)
        self.assertEqual(g["source"], "nvidia-smi")
        self.assertEqual(g["confidence"], "host-observed")

    def test_parses_two_gpus(self):
        smi_out = (
            "0, GPU-A, 10, 8192, 10240, 150, 55, 4, 16\n"
            "1, GPU-B, 20, 4096, 10240, 100, 50, 4, 16\n"
        )
        with mock.patch("subprocess.run", return_value=self._mock_smi(smi_out)):
            gpus = _parse_nvidia_smi_gpus()
        self.assertEqual(len(gpus), 2)

    def test_empty_on_returncode_nonzero(self):
        with mock.patch("subprocess.run", return_value=self._mock_smi("", returncode=1)):
            gpus = _parse_nvidia_smi_gpus()
        self.assertEqual(gpus, [])

    def test_empty_on_file_not_found(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            gpus = _parse_nvidia_smi_gpus()
        self.assertEqual(gpus, [])

    def test_clamps_negative_used(self):
        smi_out = "0, GPU-A, 0, -100, 10240, 0, 0, 4, 16\n"
        with mock.patch("subprocess.run", return_value=self._mock_smi(smi_out)):
            gpus = _parse_nvidia_smi_gpus()
        # Negative used should be clamped to 0
        self.assertEqual(gpus[0]["used_mib"], 0.0)

    def test_skips_non_digit_index_lines(self):
        smi_out = "idx, name, util, used, total, power, temp, gen, width\n"
        with mock.patch("subprocess.run", return_value=self._mock_smi(smi_out)):
            gpus = _parse_nvidia_smi_gpus()
        self.assertEqual(gpus, [])


# ---------------------------------------------------------------------------
# 3. Prometheus text parsing
# ---------------------------------------------------------------------------

class ParsePrometheusTextTests(unittest.TestCase):
    def test_parses_simple_metric(self):
        text = "llama_n_ctx_server 131072\n"
        m = _parse_prometheus_text(text)
        self.assertEqual(m.get("llama_n_ctx_server"), 131072.0)

    def test_skips_comment_lines(self):
        text = "# HELP llama_n_ctx_server Context size\nllama_n_ctx_server 65536\n"
        m = _parse_prometheus_text(text)
        self.assertEqual(m.get("llama_n_ctx_server"), 65536.0)

    def test_strips_labels_from_name(self):
        text = 'llama_kv_cache_usage_ratio{model="foo"} 0.25\n'
        m = _parse_prometheus_text(text)
        self.assertIn("llama_kv_cache_usage_ratio", m)
        self.assertAlmostEqual(m["llama_kv_cache_usage_ratio"], 0.25)

    def test_skips_non_finite(self):
        text = "broken_metric +Inf\n"
        m = _parse_prometheus_text(text)
        self.assertNotIn("broken_metric", m)

    def test_empty_text_returns_empty(self):
        m = _parse_prometheus_text("")
        self.assertEqual(m, {})

    def test_bad_lines_skipped(self):
        text = "not_a_metric\n\nbad  bad  bad\n"
        m = _parse_prometheus_text(text)
        self.assertEqual(m, {})


# ---------------------------------------------------------------------------
# 4. Context builder
# ---------------------------------------------------------------------------

class BuildContextTests(unittest.TestCase):
    def test_env_fallback(self):
        with mock.patch.dict("os.environ", {"QZ_CONTEXT": "131072"}):
            ctx = _build_context(handler=None, metrics={})
        self.assertEqual(ctx["limit_tokens"], 131072)
        self.assertEqual(ctx["confidence"], "config")

    def test_metrics_override_env(self):
        with mock.patch.dict("os.environ", {"QZ_CONTEXT": "65536"}):
            metrics = {"llama_n_ctx_server": 131072.0}
            ctx = _build_context(handler=None, metrics=metrics)
        self.assertEqual(ctx["limit_tokens"], 131072)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_used_tokens_from_kv_cells(self):
        metrics = {"llama_kv_cache_tokens_cell": 1024.0}
        with mock.patch.dict("os.environ", {"QZ_CONTEXT": "131072"}):
            ctx = _build_context(handler=None, metrics=metrics)
        self.assertEqual(ctx["used_tokens"], 1024)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_no_context_env_no_metrics(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            ctx = _build_context(handler=None, metrics={})
        # No QZ_CONTEXT in env, no metrics → limit unknown
        self.assertIsNone(ctx.get("limit_tokens"))

    def test_used_pct_computed(self):
        metrics = {
            "llama_n_ctx_server": 100.0,
            "llama_kv_cache_tokens_cell": 25.0,
        }
        ctx = _build_context(handler=None, metrics=metrics)
        self.assertAlmostEqual(ctx["used_pct"], 25.0)


# ---------------------------------------------------------------------------
# 5. Assemble snapshot — component residual
# ---------------------------------------------------------------------------

class AssembleSnapshotTests(unittest.TestCase):
    def _gpus(self, used=8192, total=10240):
        return [{
            "index": "0", "name": "Test GPU",
            "util_pct": 50.0, "used_mib": used, "total_mib": total,
            "available_mib": max(0.0, total - used),
            "power_w": 100.0, "temp_c": 60.0,
            "pcie_gen": "4", "pcie_width": "16",
            "source": "nvidia-smi", "confidence": "host-observed",
        }]

    def _ctx(self):
        return {"limit_tokens": 131072, "used_tokens": None, "used_pct": None,
                "source": "env", "confidence": "config"}

    def test_residual_equals_total_when_no_components_known(self):
        snap = _assemble_snapshot(
            self._gpus(used=8192, total=10240),
            {},   # no backend proc
            {},   # no metrics
            self._ctx(),
            now=1000.0,
        )
        # OTHER/residual should be 8192 (= total used, no components isolated)
        other = next(c for c in snap["components"] if c["name"] == "other_residual")
        self.assertAlmostEqual(other["mib"], 8192.0, delta=1)
        self.assertEqual(other["confidence"], "host-observed-residual")

    def test_totals_clamped(self):
        snap = _assemble_snapshot(
            self._gpus(used=0, total=0),
            {}, {}, self._ctx(), now=1000.0,
        )
        totals = snap["totals"]
        self.assertGreaterEqual(totals.get("used_mib", 0) or 0, 0)
        self.assertGreaterEqual(totals.get("available_mib", 0) or 0, 0)

    def test_host_observed_true_with_gpus(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0)
        self.assertTrue(snap["host_observed"])
        self.assertEqual(snap["confidence"], "host-observed")

    def test_no_gpus_confidence_unknown(self):
        snap = _assemble_snapshot([], {}, {}, self._ctx(), now=1000.0)
        self.assertFalse(snap["host_observed"])
        self.assertEqual(snap["confidence"], "unknown")

    def test_backend_confirmed_when_model_size_known(self):
        metrics = {"llama_model_size_bytes": 1024.0 * 1024.0 * 10240.0}  # 10 GiB
        snap = _assemble_snapshot(self._gpus(), {}, metrics, self._ctx(), now=1000.0)
        model_comp = next(c for c in snap["components"] if c["name"] == "model")
        self.assertEqual(model_comp["confidence"], "backend-confirmed")
        self.assertTrue(snap["backend_confirmed"])

    def test_json_serialisable(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0)
        json.dumps(snap)

    def test_notes_list(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0)
        self.assertIsInstance(snap["notes"], list)
        self.assertGreater(len(snap["notes"]), 0)


# ---------------------------------------------------------------------------
# 6. get_cached_vram_snapshot
# ---------------------------------------------------------------------------

class CachedSnapshotTests(unittest.TestCase):
    def test_returns_schema(self):
        snap = get_cached_vram_snapshot()
        self.assertEqual(snap["schema"], VRAM_SNAPSHOT_SCHEMA)

    def test_json_serialisable(self):
        snap = get_cached_vram_snapshot()
        json.dumps(snap)


# ---------------------------------------------------------------------------
# 7. Control-plane integration: vram field present
# ---------------------------------------------------------------------------

class ControlPlaneVramIntegrationTests(unittest.TestCase):
    def _make_handler(self):
        from tests.test_qz_control_plane import _make_handler
        return _make_handler()

    def test_vram_field_in_control_plane(self):
        from proxy.qz_control_plane import build_control_plane_status
        h = self._make_handler()
        cp = build_control_plane_status(h)
        self.assertIn("vram", cp)
        v = cp["vram"]
        self.assertEqual(v.get("schema"), VRAM_SNAPSHOT_SCHEMA)

    def test_vram_json_serialisable(self):
        from proxy.qz_control_plane import build_control_plane_status
        h = self._make_handler()
        cp = build_control_plane_status(h)
        json.dumps(cp["vram"])


if __name__ == "__main__":
    unittest.main()
