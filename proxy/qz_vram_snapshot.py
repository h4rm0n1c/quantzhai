#!/usr/bin/env python3
"""Proxy-owned VRAM snapshot builder: qz.vram.snapshot.v1.

Never raises. Always returns a valid qz.vram.snapshot.v1 dict.
Calls nvidia-smi (host-observed) and optionally the llama.cpp /metrics endpoint
(backend-confirmed if available). All values are clamped; no NaN/Inf.

Source/confidence vocabulary:
  backend-confirmed      llama.cpp /metrics returned this value directly
  host-observed          nvidia-smi GPU total on this host
  host-observed-residual GPU total minus known components (conservative residual)
  estimated              derived from other observed values
  config                 from QZ_CONTEXT env or model router
  unknown                no data source available

This is #6 slice 2.
"""
from __future__ import annotations

import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

VRAM_SNAPSHOT_SCHEMA = "qz.vram.snapshot.v1"

# Module-level TTL cache so nvidia-smi is not called more than once per interval.
_VRAM_CACHE_TTL: float = 3.0
_vram_cache: dict[str, Any] = {"ts": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(value) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def _safe_int(value) -> int | None:
    try:
        return int(float(value))
    except Exception:
        return None


def _parse_nvidia_smi_gpus(timeout: float = 2.0) -> list[dict]:
    """Run nvidia-smi CSV query and return per-GPU dicts. Returns [] on failure."""
    query = (
        "index,name,utilization.gpu,memory.used,memory.total,"
        "power.draw,temperature.gpu,"
        "pcie.link.gen.current,pcie.link.width.current"
    )
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 9 or not parts[0].isdigit():
                continue
            used_mib  = max(0.0, _safe_float(parts[3]))
            total_mib = max(0.0, _safe_float(parts[4]))
            gpus.append({
                "index":       parts[0],
                "name":        parts[1],
                "util_pct":    max(0.0, _safe_float(parts[2])),
                "used_mib":    used_mib,
                "total_mib":   total_mib,
                "available_mib": max(0.0, total_mib - used_mib),
                "power_w":     max(0.0, _safe_float(parts[5])),
                "temp_c":      max(0.0, _safe_float(parts[6])),
                "pcie_gen":    parts[7],
                "pcie_width":  parts[8],
                "source":      "nvidia-smi",
                "confidence":  "host-observed",
            })
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    except Exception:
        return []


def _collect_container_pids(
    container: str, docker_cmd: list, timeout: float
) -> tuple[set, dict]:
    """Collect host PIDs for processes running inside container.

    Tries multiple docker invocations in order; returns (pids, metadata).
    Never raises; returns empty set with diagnostic metadata on failure.
    """
    meta: dict = {"source": "unknown", "confidence": "unknown", "error": "", "command": ""}
    pids: set = set()

    # Attempt A: docker top CONTAINER -eo pid
    # Attempt B: docker top CONTAINER -eo pid,comm,args
    # Attempt C: plain docker top CONTAINER (default ps columns)
    for extra_args in (["-eo", "pid"], ["-eo", "pid,comm,args"], []):
        cmd = docker_cmd + ["top", container] + extra_args
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines()[1:]:  # skip header
                    parts = line.strip().split()
                    if parts and parts[0].isdigit():
                        pids.add(parts[0])
                if pids:
                    meta.update({
                        "source": "docker-top",
                        "confidence": "host-observed",
                        "command": " ".join(cmd),
                    })
                    return pids, meta
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, PermissionError):
            pass
        except Exception:
            pass

    # Attempt D: docker inspect --format '{{.State.Pid}}'
    try:
        cmd = docker_cmd + ["inspect", "--format", "{{.State.Pid}}", container]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
        if proc.returncode == 0:
            pid_str = proc.stdout.strip()
            if pid_str.isdigit() and int(pid_str) > 0:
                pids.add(pid_str)
                meta.update({
                    "source": "docker-inspect",
                    "confidence": "host-observed",
                    "command": " ".join(cmd),
                })
                return pids, meta
    except Exception:
        pass

    meta["error"] = "docker top and docker inspect both failed or returned no PIDs"
    return pids, meta


def _parse_compute_apps_csv(text: str) -> list:
    """Parse nvidia-smi --query-compute-apps=pid,process_name,used_memory CSV.

    Also accepts the older pid,used_memory format (2 columns).
    Same PID appearing multiple times (one row per GPU) is kept as separate rows.
    Never raises; returns [] on bad input.
    """
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            if len(parts) >= 3 and parts[0].isdigit():
                rows.append({
                    "pid":          parts[0],
                    "process_name": parts[1],
                    "used_mib":     max(0.0, _safe_float(parts[2])),
                })
            elif len(parts) == 2 and parts[0].isdigit():
                # Legacy 2-column format: pid,used_memory
                rows.append({
                    "pid":          parts[0],
                    "process_name": "",
                    "used_mib":     max(0.0, _safe_float(parts[1])),
                })
        except Exception:
            continue
    return rows


def _probe_backend_process(container: str, timeout: float = 1.5) -> dict:
    """Isolate backend process VRAM via nvidia-smi compute-apps.

    Uses container PID mapping first; falls back to process-name heuristic.
    Never raises. Returns dict with process_used_mib (or None), source, confidence.
    """
    docker_raw = os.environ.get("QZ_DOCKER_CMD", "docker")
    docker_cmd = docker_raw.split() if docker_raw else ["docker"]

    result: dict = {
        "container":         container,
        "pid":               None,
        "pids":              [],
        "process_used_mib":  None,
        "source":            "unknown",
        "confidence":        "unknown",
        "match_method":      "unknown",
        "process_rows":      [],
        "notes":             [],
    }
    notes: list = []

    try:
        # Step 1: Collect container PIDs
        pids, pid_meta = _collect_container_pids(container, docker_cmd, timeout)
        if pid_meta.get("error"):
            notes.append(f"Container PID collection: {pid_meta['error']}")

        # Step 2: Query compute-apps (pid,process_name,used_memory)
        apps_proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        rows = _parse_compute_apps_csv(apps_proc.stdout)

        if not rows:
            result["notes"] = notes
            return result

        # Step 3: PID-based matching (authoritative)
        if pids:
            matched = [r for r in rows if r["pid"] in pids]
            if matched:
                total = sum(r["used_mib"] for r in matched)
                primary = matched[0]["pid"]
                result.update({
                    "pid":              primary,
                    "pids":             sorted({r["pid"] for r in matched}),
                    "process_used_mib": round(total, 1),
                    "source":           "nvidia-smi-compute-apps+docker-top",
                    "confidence":       "host-observed",
                    "match_method":     "container-pid",
                    "process_rows":     matched[:16],
                })
                result["notes"] = notes
                return result

        # Step 4: Process-name heuristic fallback
        llama_rows = [
            r for r in rows
            if "llama-server" in r.get("process_name", "")
        ]
        unique_llama_pids = {r["pid"] for r in llama_rows}

        if len(unique_llama_pids) == 1:
            pid_val = next(iter(unique_llama_pids))
            total = sum(r["used_mib"] for r in llama_rows)
            result.update({
                "pid":              pid_val,
                "pids":             [pid_val],
                "process_used_mib": round(total, 1),
                "source":           "nvidia-smi-compute-apps-process-name",
                "confidence":       "host-observed-heuristic",
                "match_method":     "process-name-heuristic",
                "process_rows":     llama_rows[:16],
            })
            notes.append(
                "Matched backend process by unique llama-server compute-app process name; "
                "container PID mapping unavailable."
            )
        elif len(unique_llama_pids) > 1:
            notes.append(
                f"Multiple llama-server PIDs in compute-apps "
                f"({sorted(unique_llama_pids)}); not guessing which is the backend."
            )

    except Exception as exc:
        notes.append(f"Backend process probe failed: {exc}")

    result["notes"] = notes
    return result


def _parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse Prometheus text format; return name → float. Ignores labels, skips bad lines."""
    metrics: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            if "{" in name:
                name = name[:name.index("{")]
            val = float(parts[1])
            if math.isfinite(val):
                metrics[name] = val
        except Exception:
            continue
    return metrics


def _extract_json_error_message(body: str) -> str:
    """Return error message from a JSON error response body, or '' if not a JSON error."""
    try:
        import json
        stripped = body.strip()
        if not stripped.startswith("{"):
            return ""
        obj = json.loads(stripped)
        err = obj.get("error")
        if isinstance(err, dict):
            return str(err.get("message", err.get("type", str(err))))
        if isinstance(err, str):
            return err
        return ""
    except Exception:
        return ""


def _probe_backend_metrics(timeout: float = 1.0) -> tuple:
    """Fetch llama.cpp /metrics. Returns (metrics_dict, error_note).

    metrics_dict is empty when metrics are unavailable or the endpoint returned
    a JSON error. error_note is a human-readable string explaining the failure.
    """
    import json as _json
    host = os.environ.get("QZ_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("QZ_SERVER_PORT", "18084")
    url  = f"http://{host}:{port}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            # Detect JSON error body despite HTTP 200
            json_err = _extract_json_error_message(body)
            if json_err:
                return {}, f"Backend /metrics did not return Prometheus metrics: {json_err}"
            return _parse_prometheus_text(body), ""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
            json_err = _extract_json_error_message(body)
            if json_err:
                return {}, f"Backend /metrics did not return Prometheus metrics: {json_err}"
        except Exception:
            pass
        return {}, f"Backend /metrics HTTP {exc.code}."
    except Exception:
        return {}, ""


def _build_context(handler=None, metrics: dict | None = None) -> dict:
    """Build context window info dict."""
    limit: int | None = None
    limit_src = "unknown"
    limit_conf = "unknown"

    # Try model router
    if handler is not None:
        try:
            router = handler._model_router()
            ctx_len = router.backend_context_length()
            if ctx_len and int(ctx_len) > 0:
                limit     = int(ctx_len)
                limit_src  = "model-router"
                limit_conf = "config"
        except Exception:
            pass

    # Env fallback
    if limit is None:
        env_ctx = os.environ.get("QZ_CONTEXT", "")
        if env_ctx:
            try:
                limit     = int(env_ctx)
                limit_src  = "env"
                limit_conf = "config"
            except Exception:
                pass

    # Backend metrics override (backend-confirmed)
    if metrics and "llama_n_ctx_server" in metrics:
        limit     = int(metrics["llama_n_ctx_server"])
        limit_src  = "backend_metrics"
        limit_conf = "backend-confirmed"

    # Used tokens from KV cache cells
    used: int | None = None
    used_src  = "unknown"
    used_conf = "unknown"
    if metrics and "llama_kv_cache_tokens_cell" in metrics:
        used      = int(metrics["llama_kv_cache_tokens_cell"])
        used_src   = "backend_metrics"
        used_conf  = "backend-confirmed"

    used_pct: float | None = None
    if limit and limit > 0 and used is not None:
        used_pct = round(used / limit * 100.0, 1)

    return {
        "limit_tokens":  limit,
        "used_tokens":   used,
        "used_pct":      used_pct,
        "source":        used_src if used is not None else limit_src,
        "confidence":    used_conf if used is not None else limit_conf,
    }


def _assemble_snapshot(
    gpus: list,
    backend_proc: dict,
    metrics: dict,
    context: dict,
    *,
    now: float,
    metrics_note: str = "",
) -> dict:
    """Assemble full qz.vram.snapshot.v1 dict from collected data."""
    host_observed           = len(gpus) > 0
    backend_metrics_avail   = bool(metrics)

    # GPU totals
    total_used  = sum(g.get("used_mib", 0.0) for g in gpus)
    total_total = sum(g.get("total_mib", 0.0) for g in gpus)
    total_avail = max(0.0, total_total - total_used)

    backend_process_used = backend_proc.get("process_used_mib")

    # Components — all default to unknown unless metrics provide values
    components: list[dict] = []

    # MODEL
    model_mib    = None
    model_src    = "unknown"
    model_conf   = "unknown"
    # llama.cpp may expose llama_model_size_bytes in some builds
    if "llama_model_size_bytes" in metrics:
        raw = metrics["llama_model_size_bytes"]
        if raw > 0:
            model_mib  = max(0.0, raw / (1024.0 * 1024.0))
            model_src  = "backend_metrics"
            model_conf = "backend-confirmed"
    components.append({
        "name": "model", "label": "MODEL",
        "mib": model_mib, "source": model_src, "confidence": model_conf,
    })

    # KV cache
    kv_mib     = None
    kv_ratio   = metrics.get("llama_kv_cache_usage_ratio")
    kv_cells   = _safe_int(metrics.get("llama_kv_cache_tokens_cell"))
    kv_src     = "backend_metrics" if (kv_ratio is not None or kv_cells is not None) else "unknown"
    kv_conf    = "backend-confirmed" if kv_src == "backend_metrics" else "unknown"
    # Note: byte size of KV cache is not exposed by standard llama.cpp metrics;
    # we know usage ratio but not absolute MiB unless llama_kv_cache_size_bytes exists.
    if "llama_kv_cache_size_bytes" in metrics:
        raw = metrics["llama_kv_cache_size_bytes"]
        if raw > 0:
            kv_mib  = max(0.0, raw / (1024.0 * 1024.0))
            kv_src  = "backend_metrics"
            kv_conf = "backend-confirmed"
    components.append({
        "name": "kv_cache", "label": "KV",
        "mib": kv_mib, "source": kv_src, "confidence": kv_conf,
        "context_tokens_used":   kv_cells,
        "context_limit":         context.get("limit_tokens"),
        "context_used_pct":      round(kv_ratio * 100.0, 1) if kv_ratio is not None else None,
    })

    # Scratch / buffers — not separately exposed
    components.append({
        "name": "scratch_buffer", "label": "SCRATCH",
        "mib": None, "source": "unknown", "confidence": "unknown",
    })

    # Residual = process_used (or total used) - sum of known components
    known_sum = sum(c["mib"] for c in components if c.get("mib") is not None)
    residual_base = (
        backend_process_used if backend_process_used is not None
        else total_used if host_observed
        else None
    )
    residual_mib = (
        max(0.0, residual_base - known_sum) if residual_base is not None else None
    )
    components.append({
        "name": "other_residual", "label": "OTHER",
        "mib": residual_mib,
        "source": "host_process_residual" if residual_mib is not None else "unknown",
        "confidence": "host-observed-residual" if residual_mib is not None else "unknown",
    })

    # Overall confidence
    backend_confirmed = backend_metrics_avail and any(
        c.get("confidence") == "backend-confirmed" for c in components
    )
    if backend_confirmed and host_observed:
        confidence = "mixed"
    elif backend_confirmed:
        confidence = "backend-confirmed"
    elif host_observed:
        confidence = "host-observed"
    else:
        confidence = "unknown"

    notes: list = []
    if host_observed:
        notes.append("GPU totals observed via nvidia-smi on host.")
    else:
        notes.append("nvidia-smi not available; GPU totals unknown.")
    if not backend_metrics_avail:
        if metrics_note:
            notes.append(metrics_note)
        else:
            notes.append(
                "llama.cpp /metrics not available; "
                "model/KV/scratch allocation split is unknown."
            )
    else:
        notes.append("llama.cpp /metrics available; confirmed fields marked backend-confirmed.")
    if backend_process_used is None and host_observed:
        notes.append(
            "Backend process VRAM not isolated from host total; "
            "OTHER/residual equals total GPU VRAM used."
        )
    # Include diagnostic notes from backend proc probe
    for n in (backend_proc.get("notes") or []):
        if n and n not in notes:
            notes.append(n)

    return {
        "schema":                    VRAM_SNAPSHOT_SCHEMA,
        "ok":                        host_observed or backend_metrics_avail,
        "timestamp":                 now,
        "source":                    "proxy",
        "confidence":                confidence,
        "backend_confirmed":         backend_confirmed,
        "host_observed":             host_observed,
        "backend_metrics_available": backend_metrics_avail,
        "notes":                     notes,
        "totals": {
            "used_mib":               round(total_used, 1) if host_observed else None,
            "total_mib":              round(total_total, 1) if host_observed else None,
            "available_mib":          round(total_avail, 1) if host_observed else None,
            "backend_process_used_mib": round(backend_process_used, 1) if backend_process_used is not None else None,
            "known_allocated_mib":    round(known_sum, 1) if known_sum > 0 else None,
            "unknown_or_residual_mib": round(residual_mib, 1) if residual_mib is not None else None,
        },
        "components": components,
        "gpus":       gpus,
        "backend": {
            "container":         backend_proc.get("container", os.environ.get("QZ_CONTAINER", "")),
            "pid":               backend_proc.get("pid"),
            "pids":              backend_proc.get("pids") or [],
            "process_used_mib":  round(backend_process_used, 1) if backend_process_used is not None else None,
            "source":            backend_proc.get("source", "unknown"),
            "confidence":        backend_proc.get("confidence", "unknown"),
            "match_method":      backend_proc.get("match_method", "unknown"),
            "process_rows":      backend_proc.get("process_rows") or [],
        },
        "context": context,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_vram_snapshot(handler=None, *, now: float | None = None) -> dict:
    """Build a qz.vram.snapshot.v1 dict. Never raises. Always returns the schema.

    Args:
        handler: Optional ProxyHandler instance. Used to access model router for
                 context length. Not required; gracefully absent.
        now:     Optional timestamp override (seconds since epoch).

    Returns:
        qz.vram.snapshot.v1 dict, JSON-serialisable.
    """
    if now is None:
        now = time.time()
    try:
        container = os.environ.get("QZ_CONTAINER", "qwen36turbo")
        gpus            = _parse_nvidia_smi_gpus(timeout=2.0)
        backend_proc    = _probe_backend_process(container, timeout=1.5)
        metrics, metrics_note = _probe_backend_metrics(timeout=1.0)
        context         = _build_context(handler=handler, metrics=metrics)
        return _assemble_snapshot(
            gpus, backend_proc, metrics, context, now=now, metrics_note=metrics_note
        )
    except Exception as exc:
        return {
            "schema":                    VRAM_SNAPSHOT_SCHEMA,
            "ok":                        False,
            "timestamp":                 now,
            "source":                    "proxy",
            "confidence":                "unknown",
            "backend_confirmed":         False,
            "host_observed":             False,
            "backend_metrics_available": False,
            "error":                     str(exc),
            "notes":                     [f"VRAM snapshot builder failed: {exc}"],
            "totals":                    {"used_mib": None, "total_mib": None, "available_mib": None},
            "components":                [],
            "gpus":                      [],
            "backend":                   {"source": "unknown", "confidence": "unknown"},
            "context":                   {"source": "unknown", "confidence": "unknown"},
        }


def get_cached_vram_snapshot(handler=None, ttl: float = _VRAM_CACHE_TTL) -> dict:
    """Return a TTL-cached vram snapshot. Minimises repeated nvidia-smi calls."""
    now = time.time()
    if _vram_cache["data"] is not None and (now - _vram_cache["ts"]) < ttl:
        return _vram_cache["data"]
    data = build_vram_snapshot(handler=handler, now=now)
    _vram_cache["ts"]  = now
    _vram_cache["data"] = data
    return data
