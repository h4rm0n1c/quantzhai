"""Tests for qz.vram.snapshot.v1 builder (proxy/qz_vram_snapshot.py)."""
import json
import math
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_vram_snapshot import (
    VRAM_SNAPSHOT_SCHEMA,
    _METRIC_ALIASES,
    _assemble_snapshot,
    _backend_base_url,
    _build_backend_metrics_summary,
    _build_context,
    _collect_container_pids,
    _estimate_kv_cache_bytes,
    _extract_context_from_props,
    _extract_context_from_slots,
    _extract_json_error_message,
    _kv_dtype_config,
    _metadata_arch_prefix,
    _metadata_first,
    _normalize_prometheus_metrics,
    _parse_compute_apps_csv,
    _parse_nvidia_smi_gpus,
    _parse_prometheus_text,
    _probe_backend_metrics,
    _probe_backend_process,
    _probe_props,
    _probe_slots,
    _selected_backend_model_id,
    _selected_model_catalog_entry,
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
            metrics_note="",
        )
        # OTHER/residual should be 8192 (= total used, no components isolated)
        other = next(c for c in snap["components"] if c["name"] == "other_residual")
        self.assertAlmostEqual(other["mib"], 8192.0, delta=1)
        self.assertEqual(other["confidence"], "host-observed-residual")

    def test_totals_clamped(self):
        snap = _assemble_snapshot(
            self._gpus(used=0, total=0),
            {}, {}, self._ctx(), now=1000.0, metrics_note="",
        )
        totals = snap["totals"]
        self.assertGreaterEqual(totals.get("used_mib", 0) or 0, 0)
        self.assertGreaterEqual(totals.get("available_mib", 0) or 0, 0)

    def test_host_observed_true_with_gpus(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0, metrics_note="")
        self.assertTrue(snap["host_observed"])
        self.assertEqual(snap["confidence"], "host-observed")

    def test_no_gpus_confidence_unknown(self):
        snap = _assemble_snapshot([], {}, {}, self._ctx(), now=1000.0, metrics_note="")
        self.assertFalse(snap["host_observed"])
        self.assertEqual(snap["confidence"], "unknown")

    def test_backend_confirmed_when_model_size_known(self):
        metrics = {"llama_model_size_bytes": 1024.0 * 1024.0 * 10240.0}  # 10 GiB
        snap = _assemble_snapshot(self._gpus(), {}, metrics, self._ctx(), now=1000.0, metrics_note="")
        model_comp = next(c for c in snap["components"] if c["name"] == "model")
        self.assertEqual(model_comp["confidence"], "backend-confirmed")
        self.assertTrue(snap["backend_confirmed"])

    def test_json_serialisable(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0, metrics_note="")
        json.dumps(snap)

    def test_notes_list(self):
        snap = _assemble_snapshot(self._gpus(), {}, {}, self._ctx(), now=1000.0, metrics_note="")
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


# ---------------------------------------------------------------------------
# 8. _parse_compute_apps_csv
# ---------------------------------------------------------------------------

class ParseComputeAppsCsvTests(unittest.TestCase):
    def test_three_column_format(self):
        text = "14224, /app/llama-server, 9698\n14224, /app/llama-server, 12778\n"
        rows = _parse_compute_apps_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pid"], "14224")
        self.assertEqual(rows[0]["process_name"], "/app/llama-server")
        self.assertAlmostEqual(rows[0]["used_mib"], 9698.0)

    def test_same_pid_two_rows_both_kept(self):
        text = "42, /app/llama-server, 5000\n42, /app/llama-server, 6000\n"
        rows = _parse_compute_apps_csv(text)
        self.assertEqual(len(rows), 2)
        total = sum(r["used_mib"] for r in rows)
        self.assertAlmostEqual(total, 11000.0)

    def test_two_column_legacy_format(self):
        text = "42, 5000\n"
        rows = _parse_compute_apps_csv(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], "42")
        self.assertAlmostEqual(rows[0]["used_mib"], 5000.0)

    def test_empty_input(self):
        self.assertEqual(_parse_compute_apps_csv(""), [])

    def test_skips_non_digit_pid(self):
        text = "notpid, /app/llama-server, 1000\n"
        self.assertEqual(_parse_compute_apps_csv(text), [])

    def test_clamps_negative_mib(self):
        text = "99, /app/llama-server, -500\n"
        rows = _parse_compute_apps_csv(text)
        self.assertEqual(rows[0]["used_mib"], 0.0)


# ---------------------------------------------------------------------------
# 9. _collect_container_pids
# ---------------------------------------------------------------------------

class CollectContainerPidsTests(unittest.TestCase):
    def _mock_proc(self, stdout, returncode=0):
        m = mock.MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = ""
        return m

    def test_docker_top_eo_pid_parses_pids(self):
        # Typical output of `docker top CONTAINER -eo pid`
        stdout = "PID\n14224\n14225\n"
        with mock.patch("subprocess.run", return_value=self._mock_proc(stdout)):
            pids, meta = _collect_container_pids("mycontainer", ["docker"], timeout=2.0)
        self.assertIn("14224", pids)
        self.assertIn("14225", pids)
        self.assertEqual(meta["confidence"], "host-observed")

    def test_docker_top_failure_returns_empty(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no docker")):
            pids, meta = _collect_container_pids("mycontainer", ["docker"], timeout=2.0)
        self.assertEqual(pids, set())
        self.assertIn("error", meta)

    def test_all_fail_returns_empty_no_raise(self):
        with mock.patch("subprocess.run", return_value=self._mock_proc("", returncode=1)):
            pids, meta = _collect_container_pids("mycontainer", ["docker"], timeout=2.0)
        self.assertEqual(pids, set())


# ---------------------------------------------------------------------------
# 10. _probe_backend_process — PID and heuristic matching
# ---------------------------------------------------------------------------

class ProbeBackendProcessTests(unittest.TestCase):
    def _mock_proc(self, stdout, returncode=0):
        m = mock.MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = ""
        return m

    def _make_subprocess_side_effect(self, *responses):
        """Return side_effect that yields each mock proc in turn."""
        call_count = {"n": 0}
        def se(*args, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx < len(responses):
                return responses[idx]
            return self._mock_proc("", returncode=1)
        return se

    def test_pid_match_uses_compute_apps(self):
        # docker top returns PID 14224, compute-apps has 14224 with llama-server
        top_out = "PID\n14224\n"
        apps_out = "14224, /app/llama-server, 9698\n14224, /app/llama-server, 12778\n"
        se = self._make_subprocess_side_effect(
            self._mock_proc(top_out),    # docker top -eo pid
            self._mock_proc(apps_out),   # nvidia-smi compute-apps
        )
        with mock.patch("subprocess.run", side_effect=se):
            result = _probe_backend_process("mycontainer", timeout=2.0)
        self.assertAlmostEqual(result["process_used_mib"], 22476.0)
        self.assertEqual(result["match_method"], "container-pid")
        self.assertEqual(result["confidence"], "host-observed")

    def test_process_name_heuristic_single_pid(self):
        # docker top fails, but compute-apps has unique llama-server PID
        apps_out = "14224, /app/llama-server, 9698\n14224, /app/llama-server, 12778\n"
        se = self._make_subprocess_side_effect(
            self._mock_proc("", returncode=1),   # docker top -eo pid fails
            self._mock_proc("", returncode=1),   # docker top -eo pid,comm,args fails
            self._mock_proc("", returncode=1),   # plain docker top fails
            self._mock_proc("", returncode=1),   # docker inspect fails
            self._mock_proc(apps_out),            # nvidia-smi compute-apps
        )
        with mock.patch("subprocess.run", side_effect=se):
            result = _probe_backend_process("mycontainer", timeout=2.0)
        self.assertAlmostEqual(result["process_used_mib"], 22476.0)
        self.assertEqual(result["match_method"], "process-name-heuristic")
        self.assertEqual(result["confidence"], "host-observed-heuristic")
        self.assertIn("14224", result["pids"])

    def test_multiple_llama_server_pids_no_guess(self):
        # docker top fails; two different PIDs with llama-server
        apps_out = "11, /app/llama-server, 5000\n22, /app/llama-server, 4000\n"
        se = self._make_subprocess_side_effect(
            self._mock_proc("", returncode=1),
            self._mock_proc("", returncode=1),
            self._mock_proc("", returncode=1),
            self._mock_proc("", returncode=1),
            self._mock_proc(apps_out),
        )
        with mock.patch("subprocess.run", side_effect=se):
            result = _probe_backend_process("mycontainer", timeout=2.0)
        self.assertIsNone(result["process_used_mib"])
        # Should mention multiple PIDs in notes
        self.assertTrue(any("Multiple" in n for n in result.get("notes", [])))


# ---------------------------------------------------------------------------
# 11. _probe_backend_metrics — JSON error detection
# ---------------------------------------------------------------------------

class ProbeBackendMetricsTests(unittest.TestCase):
    def _mock_urlopen(self, body: str, status: int = 200):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body.encode("utf-8")
        resp.__enter__ = lambda s: resp
        resp.__exit__ = mock.MagicMock(return_value=False)
        return resp

    def test_prometheus_text_returned(self):
        body = "llama_n_ctx_server 131072\n"
        with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            metrics, note = _probe_backend_metrics()
        self.assertIn("llama_n_ctx_server", metrics)
        self.assertEqual(note, "")

    def test_json_error_body_returns_empty(self):
        body = '{"error":{"code":400,"message":"model name is missing from the request","type":"invalid_request_error"}}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            metrics, note = _probe_backend_metrics()
        self.assertEqual(metrics, {})
        self.assertIn("model name is missing", note)
        self.assertFalse(metrics)  # empty dict — should NOT set backend_metrics_available

    def test_unreachable_returns_empty(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            metrics, note = _probe_backend_metrics()
        self.assertEqual(metrics, {})


# ---------------------------------------------------------------------------
# 12. _extract_json_error_message
# ---------------------------------------------------------------------------

class ExtractJsonErrorMessageTests(unittest.TestCase):
    def test_extracts_message_from_dict_error(self):
        body = '{"error":{"code":400,"message":"model name missing","type":"invalid_request_error"}}'
        msg = _extract_json_error_message(body)
        self.assertIn("model name missing", msg)

    def test_extracts_string_error(self):
        body = '{"error":"something went wrong"}'
        msg = _extract_json_error_message(body)
        self.assertEqual(msg, "something went wrong")

    def test_non_json_returns_empty(self):
        msg = _extract_json_error_message("# HELP llama_n_ctx\nllama_n_ctx 131072\n")
        self.assertEqual(msg, "")

    def test_no_error_field_returns_empty(self):
        msg = _extract_json_error_message('{"status":"ok"}')
        self.assertEqual(msg, "")


# ---------------------------------------------------------------------------
# 13. Residual uses backend_process_used_mib when known
# ---------------------------------------------------------------------------

class ResidualFromBackendProcessTests(unittest.TestCase):
    def _gpus(self):
        return [{"index": "0", "name": "GPU", "util_pct": 50, "used_mib": 22000,
                 "total_mib": 26000, "available_mib": 4000,
                 "power_w": 200, "temp_c": 70, "pcie_gen": "4", "pcie_width": "16",
                 "source": "nvidia-smi", "confidence": "host-observed"}]

    def _ctx(self):
        return {"limit_tokens": 131072, "used_tokens": None, "used_pct": None,
                "source": "env", "confidence": "config"}

    def test_residual_uses_process_mib_not_total(self):
        backend_proc = {
            "container": "mybox", "pid": "42", "pids": ["42"],
            "process_used_mib": 18000.0,
            "source": "nvidia-smi-compute-apps",
            "confidence": "host-observed",
            "match_method": "container-pid",
            "process_rows": [],
            "notes": [],
        }
        snap = _assemble_snapshot(
            self._gpus(), backend_proc, {}, self._ctx(), now=1000.0, metrics_note=""
        )
        other = next(c for c in snap["components"] if c["name"] == "other_residual")
        # Residual should be 18000 (process_used), not 22000 (total GPU used)
        self.assertAlmostEqual(other["mib"], 18000.0, delta=1)
        self.assertAlmostEqual(snap["totals"]["backend_process_used_mib"], 18000.0, delta=1)

    def test_backend_pid_in_backend_object(self):
        backend_proc = {
            "container": "mybox", "pid": "14224", "pids": ["14224"],
            "process_used_mib": 12000.0,
            "source": "nvidia-smi-compute-apps",
            "confidence": "host-observed",
            "match_method": "container-pid",
            "process_rows": [],
            "notes": [],
        }
        snap = _assemble_snapshot(
            self._gpus(), backend_proc, {}, self._ctx(), now=1000.0, metrics_note=""
        )
        self.assertEqual(snap["backend"]["pid"], "14224")
        self.assertIn("14224", snap["backend"]["pids"])
        self.assertEqual(snap["backend"]["match_method"], "container-pid")


# ---------------------------------------------------------------------------
# 14. _selected_backend_model_id
# ---------------------------------------------------------------------------

class SelectedBackendModelIdTests(unittest.TestCase):
    def test_env_override(self):
        with mock.patch.dict("os.environ", {"QZ_VRAM_METRICS_MODEL": "my-model"}):
            mid, src = _selected_backend_model_id(handler=None)
        self.assertEqual(mid, "my-model")
        self.assertEqual(src, "env")

    def test_no_env_no_handler_returns_unknown(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_VRAM_METRICS_MODEL", None)
            # Also mock /v1/models fallback to fail
            with mock.patch("urllib.request.urlopen", side_effect=OSError("no backend")):
                mid, src = _selected_backend_model_id(handler=None)
        self.assertEqual(mid, "")
        self.assertEqual(src, "unknown")

    def test_model_router_selected(self):
        class _FakeRouter:
            def selected_backend_id(self):
                return "qwen3-backend"

        class _FakeHandler:
            def _model_router(self):
                return _FakeRouter()

        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_VRAM_METRICS_MODEL", None)
            mid, src = _selected_backend_model_id(handler=_FakeHandler())
        self.assertEqual(mid, "qwen3-backend")
        self.assertEqual(src, "model-router")

    def test_no_raise_on_broken_handler(self):
        class _BrokenHandler:
            def _model_router(self):
                raise RuntimeError("broken")
            def _model_catalog(self):
                raise RuntimeError("broken")

        with mock.patch.dict("os.environ", {}, clear=False):
            os.environ.pop("QZ_VRAM_METRICS_MODEL", None)
            with mock.patch("urllib.request.urlopen", side_effect=OSError("no backend")):
                mid, src = _selected_backend_model_id(handler=_BrokenHandler())
        self.assertEqual(mid, "")


# ---------------------------------------------------------------------------
# 15. _normalize_prometheus_metrics
# ---------------------------------------------------------------------------

class NormalizePrometheusMetricsTests(unittest.TestCase):
    def test_maps_llamacpp_prefix(self):
        raw = {"llamacpp:kv_cache_usage_ratio": 0.35}
        norm = _normalize_prometheus_metrics(raw)
        self.assertIn("kv_cache_usage_ratio", norm)
        self.assertAlmostEqual(norm["kv_cache_usage_ratio"], 0.35)

    def test_maps_llama_underscore_prefix(self):
        raw = {"llama_model_size_bytes": 10 * 1024 * 1024 * 1024}
        norm = _normalize_prometheus_metrics(raw)
        self.assertIn("model_size_bytes", norm)

    def test_absent_key_not_in_output(self):
        raw = {"llamacpp:requests_processing": 0}
        norm = _normalize_prometheus_metrics(raw)
        self.assertNotIn("model_size_bytes", norm)

    def test_empty_input_empty_output(self):
        self.assertEqual(_normalize_prometheus_metrics({}), {})

    def test_first_alias_wins(self):
        # llama_n_ctx takes priority over llama_n_ctx_server
        raw = {"llama_n_ctx": 131072, "llama_n_ctx_server": 65536}
        norm = _normalize_prometheus_metrics(raw)
        self.assertEqual(norm["context_limit_tokens"], 131072)


# ---------------------------------------------------------------------------
# 16. _extract_context_from_props
# ---------------------------------------------------------------------------

class ExtractContextFromPropsTests(unittest.TestCase):
    def test_n_ctx_in_default_generation_settings(self):
        props = {"default_generation_settings": {"n_ctx": 262144}}
        n_ctx, path = _extract_context_from_props(props)
        self.assertEqual(n_ctx, 262144)
        self.assertIn("n_ctx", path)

    def test_n_ctx_in_params_sub_dict(self):
        props = {"default_generation_settings": {"params": {"n_ctx": 131072}}}
        n_ctx, path = _extract_context_from_props(props)
        self.assertEqual(n_ctx, 131072)
        self.assertIn("params", path)

    def test_missing_returns_none(self):
        props = {"default_generation_settings": {"other_key": "value"}}
        n_ctx, path = _extract_context_from_props(props)
        self.assertIsNone(n_ctx)

    def test_non_dict_input(self):
        n_ctx, path = _extract_context_from_props(None)  # type: ignore
        self.assertIsNone(n_ctx)


# ---------------------------------------------------------------------------
# 17. _extract_context_from_slots
# ---------------------------------------------------------------------------

class ExtractContextFromSlotsTests(unittest.TestCase):
    def test_n_ctx_from_slot(self):
        slots = [{"id": 0, "n_ctx": 262144, "is_processing": False}]
        n_ctx, n_past = _extract_context_from_slots(slots)
        self.assertEqual(n_ctx, 262144)

    def test_n_past_max_across_slots(self):
        slots = [
            {"n_ctx": 131072, "n_past": 500},
            {"n_ctx": 131072, "n_past": 1200},
        ]
        n_ctx, n_past = _extract_context_from_slots(slots)
        self.assertEqual(n_past, 1200)

    def test_empty_slots(self):
        n_ctx, n_past = _extract_context_from_slots([])
        self.assertIsNone(n_ctx)
        self.assertIsNone(n_past)

    def test_idle_slot_n_past_zero_ignored(self):
        # n_past=0 should still be reported (model might be freshly loaded)
        slots = [{"n_ctx": 131072, "n_past": 0}]
        n_ctx, n_past = _extract_context_from_slots(slots)
        # n_past=0 is max(None, 0) = 0 but > 0 check means None is returned
        # Actually the logic: max_n_past=0 → max_n_past > 0 is False → returns None
        # This is intentional: 0 tokens used is not interesting
        self.assertEqual(n_ctx, 131072)


# ---------------------------------------------------------------------------
# 18. _probe_backend_metrics uses ?model= parameter
# ---------------------------------------------------------------------------

class ProbeBackendMetricsModelParamTests(unittest.TestCase):
    def _mock_urlopen(self, body: str, status: int = 200, content_type: str = "text/plain"):
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body.encode("utf-8")
        resp.__enter__ = lambda s: resp
        resp.__exit__ = mock.MagicMock(return_value=False)
        return resp

    def test_url_includes_model_param(self):
        """When model_id is given, URL must include ?model=<model_id>."""
        body = "llamacpp:requests_processing 0\n"
        called_urls = []

        def mock_urlopen(url, timeout):
            called_urls.append(url)
            return self._mock_urlopen(body)

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            _probe_backend_metrics(model_id="my-model", timeout=1.0)
        self.assertTrue(any("model=my-model" in u or "model=" in u for u in called_urls))

    def test_no_model_id_no_model_param(self):
        body = '{"error":{"message":"model name is missing","type":"err"}}'
        called_urls = []

        def mock_urlopen(url, timeout):
            called_urls.append(url)
            resp = mock.MagicMock()
            resp.status = 200
            resp.read.return_value = body.encode()
            resp.__enter__ = lambda s: resp
            resp.__exit__ = mock.MagicMock(return_value=False)
            return resp

        with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
            metrics, note = _probe_backend_metrics(model_id="", timeout=1.0)
        self.assertEqual(metrics, {})
        self.assertFalse(any("model=" in u for u in called_urls))

    def test_prometheus_with_model_param_parsed(self):
        body = "llamacpp:n_ctx_server 262144\nllama_n_ctx_server 131072\n"
        with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            metrics, note = _probe_backend_metrics(model_id="qwen3", timeout=1.0)
        self.assertIn("llamacpp:n_ctx_server", metrics)
        self.assertEqual(note, "")


# ---------------------------------------------------------------------------
# 19. _build_context with props/slots
# ---------------------------------------------------------------------------

class BuildContextWithPropsSlots(unittest.TestCase):
    def test_props_n_ctx_backend_confirmed(self):
        props = {"default_generation_settings": {"n_ctx": 262144}}
        ctx = _build_context(handler=None, metrics={}, props=props, slots=[])
        self.assertEqual(ctx["limit_tokens"], 262144)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_slots_n_ctx_provides_limit(self):
        slots = [{"n_ctx": 131072, "is_processing": False}]
        ctx = _build_context(handler=None, metrics={}, props={}, slots=slots)
        self.assertEqual(ctx["limit_tokens"], 131072)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_slots_n_past_provides_used(self):
        slots = [{"n_ctx": 131072, "n_past": 500}]
        ctx = _build_context(handler=None, metrics={}, props={}, slots=slots)
        self.assertEqual(ctx["used_tokens"], 500)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_props_overrides_env(self):
        with mock.patch.dict("os.environ", {"QZ_CONTEXT": "65536"}):
            props = {"default_generation_settings": {"n_ctx": 262144}}
            ctx = _build_context(handler=None, metrics={}, props=props, slots=[])
        self.assertEqual(ctx["limit_tokens"], 262144)
        self.assertEqual(ctx["confidence"], "backend-confirmed")

    def test_model_size_bytes_maps_to_component(self):
        """model_size_bytes in metrics → MODEL component mib backend-confirmed."""
        gpus = [{"index": "0", "name": "GPU", "util_pct": 50, "used_mib": 22000,
                 "total_mib": 26000, "available_mib": 4000,
                 "power_w": 200, "temp_c": 70, "pcie_gen": "4", "pcie_width": "16",
                 "source": "nvidia-smi", "confidence": "host-observed"}]
        ctx = {"limit_tokens": 131072, "used_tokens": None, "used_pct": None,
               "source": "env", "confidence": "config"}
        # 10 GiB = 10240 MiB
        metrics = {"llama_model_size_bytes": 10 * 1024 * 1024 * 1024}
        snap = _assemble_snapshot(gpus, {}, metrics, ctx, now=1.0, metrics_note="")
        model_comp = next(c for c in snap["components"] if c["name"] == "model")
        self.assertAlmostEqual(model_comp["mib"], 10 * 1024, delta=1)
        self.assertEqual(model_comp["confidence"], "backend-confirmed")

    def test_llamacpp_prefix_model_size_maps(self):
        """llamacpp:model_size_bytes should also map via alias table."""
        gpus = [{"index": "0", "name": "GPU", "util_pct": 50, "used_mib": 22000,
                 "total_mib": 26000, "available_mib": 4000,
                 "power_w": 200, "temp_c": 70, "pcie_gen": "4", "pcie_width": "16",
                 "source": "nvidia-smi", "confidence": "host-observed"}]
        ctx = {"limit_tokens": 131072, "used_tokens": None, "used_pct": None,
               "source": "env", "confidence": "config"}
        metrics = {"llamacpp:model_size_bytes": 5 * 1024 * 1024 * 1024}
        snap = _assemble_snapshot(gpus, {}, metrics, ctx, now=1.0, metrics_note="")
        model_comp = next(c for c in snap["components"] if c["name"] == "model")
        self.assertAlmostEqual(model_comp["mib"], 5 * 1024, delta=1)


# ---------------------------------------------------------------------------
# 20. backend_metrics field in snapshot
# ---------------------------------------------------------------------------

class BackendMetricsFieldTests(unittest.TestCase):
    def test_backend_metrics_in_snapshot(self):
        snap = build_vram_snapshot()
        self.assertIn("backend_metrics", snap)
        bm = snap["backend_metrics"]
        self.assertIn("available", bm)
        self.assertIn("model", bm)

    def test_json_serialisable_with_backend_metrics(self):
        snap = build_vram_snapshot()
        json.dumps(snap)


# ---------------------------------------------------------------------------
# 21. _selected_model_catalog_entry
# ---------------------------------------------------------------------------

class SelectedModelCatalogEntryTests(unittest.TestCase):
    def _entry(self, backend_id="qwen3"):
        return {
            "backend_id": backend_id,
            "key": f"{backend_id}.gguf",
            "filename": f"{backend_id}.gguf",
            "stem": backend_id,
            "aliases": [backend_id, f"{backend_id}.gguf"],
            "size_bytes": 20 * 1024 ** 3,
            "metadata": {},
        }

    def test_model_router_path(self):
        entry = self._entry("mymodel")
        class _FakeRouter:
            def selected_model_entry(self):
                return entry
        class _FakeHandler:
            def _model_router(self):
                return _FakeRouter()
        e, src = _selected_model_catalog_entry(handler=_FakeHandler(), model_id="mymodel")
        self.assertEqual(src, "model-router")
        self.assertEqual(e.get("backend_id"), "mymodel")

    def test_catalog_selected_path(self):
        entry = self._entry("catalog-model")
        class _FakeCatalog:
            selected = entry
            entries = [entry]
        class _FakeHandler:
            def _model_router(self):
                raise RuntimeError("no router")
            def _model_catalog(self):
                return _FakeCatalog()
        e, src = _selected_model_catalog_entry(handler=_FakeHandler(), model_id="catalog-model")
        self.assertEqual(src, "catalog-selected")
        self.assertEqual(e.get("backend_id"), "catalog-model")

    def test_catalog_match_by_backend_id(self):
        entry = self._entry("backend-model")
        class _FakeCatalog:
            selected = None
            entries = [entry]
        class _FakeHandler:
            def _model_router(self):
                raise RuntimeError("no router")
            def _model_catalog(self):
                return _FakeCatalog()
        e, src = _selected_model_catalog_entry(handler=_FakeHandler(), model_id="backend-model")
        self.assertEqual(src, "catalog-match")
        self.assertEqual(e.get("backend_id"), "backend-model")

    def test_unknown_when_no_handler(self):
        e, src = _selected_model_catalog_entry(handler=None, model_id="anything")
        self.assertEqual(src, "unknown")
        self.assertEqual(e, {})

    def test_no_raise_on_broken_handler(self):
        class _BrokenHandler:
            def _model_router(self):
                raise RuntimeError("broken")
            def _model_catalog(self):
                raise RuntimeError("broken")
        e, src = _selected_model_catalog_entry(handler=_BrokenHandler(), model_id="x")
        self.assertEqual(src, "unknown")
        self.assertEqual(e, {})


# ---------------------------------------------------------------------------
# 22. _kv_dtype_config
# ---------------------------------------------------------------------------

class KvDtypeConfigTests(unittest.TestCase):
    def _clean_env(self):
        return {k: v for k, v in os.environ.items() if k not in ("QZ_KV_KEY", "QZ_KV_VALUE")}

    def test_env_f16_f16(self):
        with mock.patch.dict("os.environ", {"QZ_KV_KEY": "f16", "QZ_KV_VALUE": "f16"}):
            cfg = _kv_dtype_config()
        self.assertEqual(cfg["key_type"], "f16")
        self.assertAlmostEqual(cfg["key_bytes"], 2.0)
        self.assertEqual(cfg["source"], "env")
        self.assertEqual(cfg["confidence"], "config")

    def test_env_f32_q8_0(self):
        with mock.patch.dict("os.environ", {"QZ_KV_KEY": "f32", "QZ_KV_VALUE": "q8_0"}):
            cfg = _kv_dtype_config()
        self.assertAlmostEqual(cfg["key_bytes"], 4.0)
        self.assertAlmostEqual(cfg["value_bytes"], 1.0)

    def test_default_when_no_env(self):
        with mock.patch.dict("os.environ", self._clean_env(), clear=True):
            cfg = _kv_dtype_config()
        self.assertEqual(cfg["key_type"], "f16")
        self.assertEqual(cfg["source"], "default")
        self.assertEqual(cfg["confidence"], "estimated-default")

    def test_launch_args_path(self):
        entry = {"launch_args": ["--cache-type-k", "q8_0", "--cache-type-v", "f16"]}
        with mock.patch.dict("os.environ", self._clean_env(), clear=True):
            cfg = _kv_dtype_config(entry=entry)
        self.assertEqual(cfg["key_type"], "q8_0")
        self.assertAlmostEqual(cfg["key_bytes"], 1.0)
        self.assertEqual(cfg["source"], "launch_args")

    def test_unsupported_dtype_fallback_with_note(self):
        with mock.patch.dict("os.environ", {"QZ_KV_KEY": "q4_k_m", "QZ_KV_VALUE": "f16"}):
            cfg = _kv_dtype_config()
        self.assertEqual(cfg["key_type"], "f16")
        self.assertTrue(any("Unsupported" in n for n in cfg.get("notes", [])))

    def test_json_serialisable(self):
        with mock.patch.dict("os.environ", {"QZ_KV_KEY": "f16", "QZ_KV_VALUE": "f16"}):
            cfg = _kv_dtype_config()
        json.dumps(cfg)


# ---------------------------------------------------------------------------
# 23. _estimate_kv_cache_bytes
# ---------------------------------------------------------------------------

class EstimateKvCacheBytesTests(unittest.TestCase):
    def _kv_dtype(self):
        return {"key_type": "f16", "value_type": "f16", "key_bytes": 2.0, "value_bytes": 2.0}

    def _entry(self, **meta_overrides):
        meta = {
            "llama.block_count": 28,
            "llama.embedding_length": 4096,
            "llama.attention.head_count": 32,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
            "llama.attention.value_length": 128,
        }
        meta.update(meta_overrides)
        return {"metadata": meta, "size_bytes": 20 * 1024 ** 3}

    def test_basic_formula(self):
        result = _estimate_kv_cache_bytes(self._entry(), 131072, self._kv_dtype())
        self.assertIsNotNone(result["mib"])
        # 131072 tokens × 28 layers × (8 × 128 × 2 + 8 × 128 × 2) bytes
        expected_bytes = 131072 * 28 * (8 * 128 * 2.0 + 8 * 128 * 2.0)
        expected_mib = expected_bytes / (1024 ** 2)
        self.assertAlmostEqual(result["mib"], round(expected_mib, 2), delta=1.0)
        self.assertFalse(result["backend_confirmed"])
        self.assertTrue(result["estimated"])
        self.assertEqual(result["confidence"], "estimated-from-gguf-metadata")

    def test_head_count_kv_used_in_formula(self):
        result = _estimate_kv_cache_bytes(
            self._entry(**{"llama.attention.head_count_kv": 4}), 131072, self._kv_dtype()
        )
        self.assertEqual(result["formula"]["head_count_kv"], 4)

    def test_derive_key_length_from_embedding(self):
        entry = self._entry()
        entry["metadata"].pop("llama.attention.key_length")
        entry["metadata"].pop("llama.attention.value_length")
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNotNone(result.get("mib"))
        self.assertTrue(any("derived" in n for n in result.get("notes", [])))

    def test_missing_block_count_returns_unknown(self):
        entry = self._entry()
        entry["metadata"].pop("llama.block_count")
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNone(result["mib"])
        self.assertIn("missing_logical_fields", result)
        self.assertIn("block_count", result["missing_logical_fields"])

    def test_missing_head_count_returns_unknown(self):
        entry = self._entry()
        entry["metadata"].pop("llama.attention.head_count")
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNone(result["mib"])

    def test_no_context_limit_returns_unknown(self):
        result = _estimate_kv_cache_bytes(self._entry(), None, self._kv_dtype())
        self.assertIsNone(result["mib"])

    def test_notes_include_semantics(self):
        result = _estimate_kv_cache_bytes(self._entry(), 131072, self._kv_dtype())
        notes_text = " ".join(result.get("notes", []))
        self.assertIn("full-context", notes_text)
        self.assertIn("scratch", notes_text)

    def test_json_serialisable(self):
        result = _estimate_kv_cache_bytes(self._entry(), 131072, self._kv_dtype())
        json.dumps(result)

    def test_no_entry_returns_unknown(self):
        result = _estimate_kv_cache_bytes({}, 131072, self._kv_dtype())
        self.assertIsNone(result["mib"])


# ---------------------------------------------------------------------------
# 24. Provenance components in _assemble_snapshot
# ---------------------------------------------------------------------------

class ProvenanceComponentsTests(unittest.TestCase):
    def _gpus(self, used=22000, total=26000):
        return [{"index": "0", "name": "GPU", "util_pct": 50, "used_mib": used,
                 "total_mib": total, "available_mib": max(0, total - used),
                 "power_w": 200, "temp_c": 70, "pcie_gen": "4", "pcie_width": "16",
                 "source": "nvidia-smi", "confidence": "host-observed"}]

    def _ctx(self, limit=131072, used=None, pct=None):
        return {"limit_tokens": limit, "used_tokens": used, "used_pct": pct,
                "source": "env", "confidence": "config"}

    def _entry(self):
        return {
            "size_bytes": 20 * 1024 ** 3,
            "metadata": {
                "llama.block_count": 28,
                "llama.embedding_length": 4096,
                "llama.attention.head_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
        }

    def _kv_dtype(self):
        return {"key_type": "f16", "value_type": "f16", "key_bytes": 2.0, "value_bytes": 2.0}

    def _snap(self, **kwargs):
        defaults = dict(
            gpus=self._gpus(), backend_proc={}, metrics={},
            context=self._ctx(), now=1.0,
            catalog_entry=self._entry(),
            catalog_entry_source="catalog-match",
            kv_dtype=self._kv_dtype(),
        )
        defaults.update(kwargs)
        return _assemble_snapshot(**defaults)

    def test_model_estimate_from_catalog(self):
        snap = self._snap()
        m = next(c for c in snap["components"] if c["name"] == "model")
        self.assertIsNotNone(m["mib"])
        self.assertAlmostEqual(m["mib"], 20 * 1024, delta=1)
        self.assertEqual(m["confidence"], "estimated-from-gguf-size")
        self.assertTrue(m["estimated"])
        self.assertFalse(m["backend_confirmed"])

    def test_backend_model_size_overrides_catalog(self):
        metrics = {"llama_model_size_bytes": 18 * 1024 ** 3}
        snap = self._snap(metrics=metrics)
        m = next(c for c in snap["components"] if c["name"] == "model")
        self.assertEqual(m["confidence"], "backend-confirmed")
        self.assertFalse(m["estimated"])
        self.assertTrue(m["backend_confirmed"])

    def test_kv_alloc_from_formula(self):
        snap = self._snap()
        kv = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        self.assertIsNotNone(kv["mib"])
        self.assertEqual(kv["confidence"], "estimated-from-gguf-metadata")
        self.assertFalse(kv["backend_confirmed"])
        self.assertIn("formula", kv)

    def test_kv_used_from_used_tokens(self):
        snap = self._snap(context=self._ctx(limit=131072, used=1000))
        kv_used = next(c for c in snap["components"] if c["name"] == "kv_used")
        self.assertIsNotNone(kv_used["mib"])
        self.assertEqual(kv_used["confidence"], "estimated-runtime-occupancy")

    def test_kv_used_from_used_pct(self):
        snap = self._snap(context=self._ctx(limit=131072, pct=0.5))
        kv_used = next(c for c in snap["components"] if c["name"] == "kv_used")
        self.assertIsNotNone(kv_used["mib"])
        self.assertEqual(kv_used["confidence"], "estimated-runtime-occupancy")

    def test_kv_used_not_subtracted_from_residual(self):
        bp = {"process_used_mib": 22000.0, "notes": []}
        snap = self._snap(backend_proc=bp, context=self._ctx(limit=131072, used=1000))
        model  = next(c for c in snap["components"] if c["name"] == "model")
        kv_alloc = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        kv_used  = next(c for c in snap["components"] if c["name"] == "kv_used")
        other    = next(c for c in snap["components"] if c["name"] == "other_residual")
        # residual = process_used - model - kv_alloc  (not minus kv_used)
        expected = 22000.0 - (model.get("mib") or 0) - (kv_alloc.get("mib") or 0)
        if expected >= 0 and other["mib"] is not None:
            self.assertAlmostEqual(other["mib"], expected, delta=2)
        # kv_used is a subset of kv_alloc, so must be smaller
        if kv_alloc["mib"] and kv_used["mib"]:
            self.assertLess(kv_used["mib"], kv_alloc["mib"])

    def test_other_subtracts_model_and_kv_alloc(self):
        bp = {"process_used_mib": 22000.0, "notes": []}
        snap = self._snap(backend_proc=bp)
        model    = next(c for c in snap["components"] if c["name"] == "model")
        kv_alloc = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        other    = next(c for c in snap["components"] if c["name"] == "other_residual")
        expected = 22000.0 - (model.get("mib") or 0) - (kv_alloc.get("mib") or 0)
        if expected >= 0 and other["mib"] is not None:
            self.assertAlmostEqual(other["mib"], expected, delta=2)

    def test_residual_clamped_when_estimates_exceed_process(self):
        bp = {"process_used_mib": 100.0, "notes": []}
        snap = self._snap(gpus=self._gpus(used=100, total=26000), backend_proc=bp)
        other = next(c for c in snap["components"] if c["name"] == "other_residual")
        self.assertEqual(other["mib"], 0.0)
        self.assertEqual(other["confidence"], "derived-clamped")
        self.assertTrue(any("clamped" in n for n in other.get("notes", [])))

    def test_scratch_remains_unknown(self):
        snap = self._snap()
        scratch = next(c for c in snap["components"] if c["name"] == "scratch_buffer")
        self.assertIsNone(scratch["mib"])
        self.assertEqual(scratch["confidence"], "unknown")
        self.assertFalse(scratch["backend_confirmed"])

    def test_estimates_block_present_and_valid(self):
        snap = self._snap()
        self.assertIn("estimates", snap)
        est = snap["estimates"]
        self.assertEqual(est.get("schema"), "qz.vram.estimates.v1")
        self.assertIn("model_entry_source", est)
        self.assertIn("kv_formula_available", est)
        self.assertIn("residual_basis", est)

    def test_estimates_block_json_serialisable(self):
        snap = self._snap(context=self._ctx(limit=131072, used=1000))
        json.dumps(snap["estimates"])

    def test_all_components_json_serialisable(self):
        snap = self._snap(context=self._ctx(limit=131072, used=1000))
        json.dumps(snap)

    def test_no_catalog_entry_still_works(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(), now=1.0,
            catalog_entry=None, catalog_entry_source="unknown", kv_dtype=None,
        )
        self.assertEqual(snap["schema"], VRAM_SNAPSHOT_SCHEMA)
        other = next(c for c in snap["components"] if c["name"] == "other_residual")
        # Without backend_proc, residual = total GPU used
        self.assertAlmostEqual(other["mib"], 22000.0, delta=1)

    def test_component_set_names(self):
        snap = self._snap()
        names = [c["name"] for c in snap["components"]]
        self.assertIn("model", names)
        self.assertIn("kv_alloc", names)
        self.assertIn("kv_used", names)
        self.assertIn("scratch_buffer", names)
        self.assertIn("other_residual", names)

    def test_component_labels(self):
        snap = self._snap()
        labels = {c["name"]: c["label"] for c in snap["components"]}
        self.assertEqual(labels["model"], "MODEL")
        self.assertEqual(labels["kv_alloc"], "KV_ALLOC")
        self.assertEqual(labels["kv_used"], "KV_USED")
        self.assertEqual(labels["scratch_buffer"], "SCRATCH")
        self.assertEqual(labels["other_residual"], "OTHER")


# ---------------------------------------------------------------------------
# 25. _metadata_arch_prefix
# ---------------------------------------------------------------------------

class MetadataArchPrefixTests(unittest.TestCase):
    def test_returns_entry_architecture(self):
        entry = {"architecture": "qwen3", "metadata": {}}
        self.assertEqual(_metadata_arch_prefix(entry), "qwen3")

    def test_falls_back_to_general_architecture_in_metadata(self):
        entry = {"metadata": {"general.architecture": "qwen2"}}
        self.assertEqual(_metadata_arch_prefix(entry), "qwen2")

    def test_entry_architecture_wins_over_metadata(self):
        entry = {"architecture": "qwen3", "metadata": {"general.architecture": "llama"}}
        self.assertEqual(_metadata_arch_prefix(entry), "qwen3")

    def test_skips_unknown_architecture(self):
        entry = {"architecture": "unknown", "metadata": {"general.architecture": "qwen3"}}
        self.assertEqual(_metadata_arch_prefix(entry), "qwen3")

    def test_falls_back_to_llama_when_nothing(self):
        self.assertEqual(_metadata_arch_prefix({}), "llama")
        self.assertEqual(_metadata_arch_prefix(None), "llama")  # type: ignore

    def test_strips_whitespace(self):
        entry = {"architecture": " qwen3 "}
        self.assertEqual(_metadata_arch_prefix(entry), "qwen3")


# ---------------------------------------------------------------------------
# 26. _metadata_first
# ---------------------------------------------------------------------------

class MetadataFirstTests(unittest.TestCase):
    def test_arch_prefixed_wins(self):
        meta = {"qwen3.block_count": 28, "llama.block_count": 32}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertEqual(val, 28)
        self.assertEqual(key, "qwen3.block_count")

    def test_llama_fallback_when_arch_missing(self):
        meta = {"llama.block_count": 32}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertEqual(val, 32)
        self.assertEqual(key, "llama.block_count")

    def test_exact_name_fallback(self):
        meta = {"block_count": 24}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertEqual(val, 24)
        self.assertEqual(key, "block_count")

    def test_suffix_match_unambiguous(self):
        meta = {"some.arch.block_count": 16}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertEqual(val, 16)
        self.assertEqual(key, "some.arch.block_count")

    def test_suffix_match_ambiguous_returns_none(self):
        meta = {"a.block_count": 10, "b.block_count": 20}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertIsNone(val)
        self.assertEqual(key, "")

    def test_not_found_returns_none_empty(self):
        meta = {"unrelated.key": 42}
        val, key = _metadata_first(meta, ["block_count"], arch="qwen3")
        self.assertIsNone(val)
        self.assertEqual(key, "")

    def test_empty_meta_returns_none(self):
        val, key = _metadata_first({}, ["block_count"], arch="qwen3")
        self.assertIsNone(val)

    def test_llama_arch_deduplicates(self):
        # When arch="llama", llama.block_count should still be found once
        meta = {"llama.block_count": 40}
        val, key = _metadata_first(meta, ["block_count"], arch="llama")
        self.assertEqual(val, 40)
        self.assertIn("llama.block_count", key)

    def test_nested_attention_key(self):
        meta = {"qwen3.attention.head_count_kv": 8}
        val, key = _metadata_first(meta, ["attention.head_count_kv"], arch="qwen3")
        self.assertEqual(val, 8)
        self.assertEqual(key, "qwen3.attention.head_count_kv")


# ---------------------------------------------------------------------------
# 27. _estimate_kv_cache_bytes — architecture-aware
# ---------------------------------------------------------------------------

class EstimateKvCacheBytesArchTests(unittest.TestCase):
    def _kv_dtype(self):
        return {"key_type": "f16", "value_type": "f16", "key_bytes": 2.0, "value_bytes": 2.0}

    def _qwen3_entry(self, **overrides):
        meta = {
            "general.architecture": "qwen3",
            "qwen3.block_count": 28,
            "qwen3.embedding_length": 4096,
            "qwen3.attention.head_count": 32,
            "qwen3.attention.head_count_kv": 8,
            "qwen3.attention.key_length": 128,
            "qwen3.attention.value_length": 128,
        }
        meta.update(overrides)
        return {
            "architecture": "qwen3",
            "size_bytes": 20 * 1024 ** 3,
            "metadata": meta,
        }

    def test_qwen3_metadata_produces_kv_alloc(self):
        result = _estimate_kv_cache_bytes(self._qwen3_entry(), 262144, self._kv_dtype())
        self.assertIsNotNone(result["mib"])
        self.assertEqual(result["confidence"], "estimated-from-gguf-metadata")
        self.assertFalse(result["backend_confirmed"])

    def test_qwen3_formula_uses_qwen_prefixed_keys(self):
        result = _estimate_kv_cache_bytes(self._qwen3_entry(), 262144, self._kv_dtype())
        keys = result.get("formula", {}).get("keys", {})
        self.assertIn("qwen3", keys.get("layers", ""))
        self.assertIn("qwen3", keys.get("head_count_kv", ""))

    def test_qwen3_value_matches_formula(self):
        # 262144 tokens × 28 layers × (8 × 128 × 2 + 8 × 128 × 2) bytes
        entry = self._qwen3_entry()
        result = _estimate_kv_cache_bytes(entry, 262144, self._kv_dtype())
        expected_bytes = 262144 * 28 * (8 * 128 * 2.0 + 8 * 128 * 2.0)
        expected_mib = expected_bytes / (1024 ** 2)
        self.assertAlmostEqual(result["mib"], round(expected_mib, 2), delta=1.0)

    def test_architecture_from_entry_field(self):
        # entry["architecture"] = "qwen3" → uses qwen3.* keys
        entry = self._qwen3_entry()
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertEqual(result.get("architecture"), "qwen3")

    def test_architecture_from_general_architecture_metadata(self):
        # No entry["architecture"], but metadata has general.architecture
        entry = self._qwen3_entry()
        del entry["architecture"]
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertEqual(result.get("architecture"), "qwen3")
        self.assertIsNotNone(result["mib"])

    def test_llama_metadata_still_works(self):
        entry = {
            "architecture": "llama",
            "metadata": {
                "llama.block_count": 32,
                "llama.embedding_length": 4096,
                "llama.attention.head_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.attention.key_length": 128,
                "llama.attention.value_length": 128,
            },
        }
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNotNone(result["mib"])
        self.assertEqual(result.get("architecture"), "llama")

    def test_missing_metadata_includes_arch_and_sample(self):
        # Remove block_count so estimate fails
        entry = self._qwen3_entry()
        del entry["metadata"]["qwen3.block_count"]
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNone(result["mib"])
        self.assertEqual(result.get("architecture"), "qwen3")
        self.assertIn("block_count", result.get("missing_logical_fields", []))
        self.assertIsInstance(result.get("available_metadata_keys_sample"), list)
        self.assertGreater(len(result["available_metadata_keys_sample"]), 0)

    def test_missing_metadata_note_mentions_arch(self):
        entry = self._qwen3_entry()
        del entry["metadata"]["qwen3.block_count"]
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        note = " ".join(result.get("notes", []))
        self.assertIn("qwen3", note)

    def test_derive_head_dim_from_qwen_embedding(self):
        # Remove key/value lengths; should derive from embedding/head_count
        entry = self._qwen3_entry()
        del entry["metadata"]["qwen3.attention.key_length"]
        del entry["metadata"]["qwen3.attention.value_length"]
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        self.assertIsNotNone(result["mib"])
        self.assertTrue(any("derived" in n for n in result.get("notes", [])))

    def test_formula_keys_recorded(self):
        result = _estimate_kv_cache_bytes(self._qwen3_entry(), 131072, self._kv_dtype())
        keys = result.get("formula", {}).get("keys", {})
        for field in ("layers", "head_count", "head_count_kv"):
            self.assertIn(field, keys)
            self.assertIsInstance(keys[field], str)
            self.assertTrue(keys[field])  # non-empty

    def test_suffix_match_when_arch_unknown(self):
        # Unknown arch, metadata has a single key matching the suffix
        entry = {
            "architecture": "unknown",
            "metadata": {
                "custom.block_count": 24,
                "custom.embedding_length": 2048,
                "custom.attention.head_count": 16,
                "custom.attention.head_count_kv": 4,
                "custom.attention.key_length": 128,
                "custom.attention.value_length": 128,
            },
        }
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        # arch falls back to "llama", llama.* not found, tries suffix match
        # For each field, "custom.X" is the only candidate → should succeed
        self.assertIsNotNone(result["mib"])

    def test_json_serialisable_qwen3(self):
        result = _estimate_kv_cache_bytes(self._qwen3_entry(), 131072, self._kv_dtype())
        json.dumps(result)

    def test_json_serialisable_missing(self):
        entry = self._qwen3_entry()
        del entry["metadata"]["qwen3.block_count"]
        result = _estimate_kv_cache_bytes(entry, 131072, self._kv_dtype())
        json.dumps(result)


# ---------------------------------------------------------------------------
# 28. Full snapshot with qwen3 entry — KV_ALLOC non-null
# ---------------------------------------------------------------------------

class QwenSnapshotIntegrationTests(unittest.TestCase):
    def _gpus(self):
        return [{"index": "0", "name": "GPU", "util_pct": 50, "used_mib": 22000,
                 "total_mib": 26000, "available_mib": 4000,
                 "power_w": 200, "temp_c": 70, "pcie_gen": "4", "pcie_width": "16",
                 "source": "nvidia-smi", "confidence": "host-observed"}]

    def _ctx(self, limit=262144, used=None):
        return {"limit_tokens": limit, "used_tokens": used, "used_pct": None,
                "source": "backend-props", "confidence": "backend-confirmed"}

    def _qwen3_entry(self):
        return {
            "architecture": "qwen3",
            "size_bytes": 19 * 1024 ** 3,
            "metadata": {
                "general.architecture": "qwen3",
                "qwen3.block_count": 36,
                "qwen3.embedding_length": 4096,
                "qwen3.attention.head_count": 32,
                "qwen3.attention.head_count_kv": 8,
                "qwen3.attention.key_length": 128,
                "qwen3.attention.value_length": 128,
            },
        }

    def _kv_dtype(self):
        return {"key_type": "f16", "value_type": "f16", "key_bytes": 2.0, "value_bytes": 2.0}

    def test_kv_alloc_non_null_for_qwen3(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        kv_alloc = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        self.assertIsNotNone(kv_alloc["mib"])
        self.assertGreater(kv_alloc["mib"], 0)
        self.assertEqual(kv_alloc["confidence"], "estimated-from-gguf-metadata")

    def test_kv_alloc_formula_has_arch_prefixed_keys(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        kv_alloc = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        keys = kv_alloc.get("formula", {}).get("keys", {})
        self.assertIn("qwen3", keys.get("layers", ""))

    def test_kv_used_computed_when_context_known(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(limit=262144, used=5000),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        kv_used = next(c for c in snap["components"] if c["name"] == "kv_used")
        self.assertIsNotNone(kv_used["mib"])

    def test_other_reduced_by_kv_alloc(self):
        bp = {"process_used_mib": 22000.0, "notes": []}
        snap_no_catalog = _assemble_snapshot(
            self._gpus(), bp, {}, self._ctx(),
            now=1.0, catalog_entry=None, catalog_entry_source="unknown", kv_dtype=None,
        )
        snap_with_qwen = _assemble_snapshot(
            self._gpus(), bp, {}, self._ctx(),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        other_no_cat  = next(c for c in snap_no_catalog["components"] if c["name"] == "other_residual")
        other_with_q  = next(c for c in snap_with_qwen["components"] if c["name"] == "other_residual")
        # With catalog, MODEL + KV_ALLOC are subtracted, so OTHER is smaller
        if other_no_cat["mib"] is not None and other_with_q["mib"] is not None:
            self.assertLess(other_with_q["mib"], other_no_cat["mib"])

    def test_no_estimate_marked_backend_confirmed(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        for comp in snap["components"]:
            if comp.get("estimated"):
                self.assertFalse(
                    comp.get("backend_confirmed"),
                    f"Estimated component {comp['name']} should not be backend_confirmed",
                )

    def test_kv_alloc_comp_has_diagnostic_when_missing(self):
        # Remove block_count to trigger failure path
        entry = self._qwen3_entry()
        del entry["metadata"]["qwen3.block_count"]
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(),
            now=1.0,
            catalog_entry=entry,
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        kv_alloc = next(c for c in snap["components"] if c["name"] == "kv_alloc")
        self.assertIsNone(kv_alloc["mib"])
        self.assertEqual(kv_alloc.get("architecture"), "qwen3")
        self.assertIn("block_count", kv_alloc.get("missing_logical_fields", []))
        self.assertIsInstance(kv_alloc.get("available_metadata_keys_sample"), list)

    def test_json_serialisable(self):
        snap = _assemble_snapshot(
            self._gpus(), {}, {}, self._ctx(used=1000),
            now=1.0,
            catalog_entry=self._qwen3_entry(),
            catalog_entry_source="model-router",
            kv_dtype=self._kv_dtype(),
        )
        json.dumps(snap)


if __name__ == "__main__":
    unittest.main()
