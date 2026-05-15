#!/usr/bin/env python3
"""Proxy-owned VRAM snapshot builder: qz.vram.snapshot.v1.

Never raises. Always returns a valid qz.vram.snapshot.v1 dict.
Calls nvidia-smi (host-observed) and optionally the llama.cpp /metrics endpoint
(backend-confirmed if available). All values are clamped; no NaN/Inf.

Source/confidence vocabulary:
  backend-confirmed            llama.cpp /metrics returned this value directly
  host-observed                nvidia-smi GPU total on this host
  host-observed-residual       GPU total minus known components (conservative residual)
  estimated-from-gguf-size     GGUF file size used as model weight proxy
  estimated-from-gguf-metadata GGUF metadata formula used to estimate KV allocation
  estimated-runtime-occupancy  KV_ALLOC scaled by context token occupancy ratio
  derived-clamped              residual clamped because estimates exceeded process VRAM
  estimated                    derived from other observed values
  config                       from QZ_CONTEXT env or model router
  unknown                      no data source available

Updated in #6 slice 4: TurboQuant router requires ?model=<selected_backend_id>.
Updated in #6 slice 6: provenance-based MODEL/KV_ALLOC/KV_USED split estimates.
"""
from __future__ import annotations

import json as _json
import math
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Metric alias table: canonical name -> list of known Prometheus metric names
# (in order of preference). Only mapped if the metric is actually present.
# ---------------------------------------------------------------------------
_METRIC_ALIASES: dict[str, list[str]] = {
    "context_limit_tokens": [
        "llama_n_ctx", "llama_n_ctx_server", "llama_context_size",
        "llamacpp:n_ctx", "llamacpp:context_size", "llamacpp:n_ctx_server",
    ],
    "context_used_tokens": [
        "llama_kv_cache_tokens_cell", "llama_kv_cache_used_cells",
        "llama_kv_cache_used_tokens",
        "llamacpp:kv_cache_tokens_cell", "llamacpp:kv_cache_used_cells",
    ],
    "kv_cache_usage_ratio": [
        "llama_kv_cache_usage_ratio", "llama_kv_cache_usage",
        "llamacpp:kv_cache_usage_ratio", "llamacpp:kv_cache_usage",
    ],
    "kv_cache_size_bytes": [
        "llama_kv_cache_size_bytes", "llamacpp:kv_cache_size_bytes",
    ],
    "kv_cache_used_bytes": [
        "llama_kv_cache_used_bytes", "llamacpp:kv_cache_used_bytes",
    ],
    "model_size_bytes": [
        "llama_model_size_bytes", "llama_model_loaded_size_bytes",
        "llamacpp:model_size_bytes", "llamacpp:model_loaded_size_bytes",
    ],
}

VRAM_SNAPSHOT_SCHEMA = "qz.vram.snapshot.v1"

# KV dtype → bytes per element
_KV_DTYPE_BYTES: dict[str, float] = {
    "f16":  2.0,
    "bf16": 2.0,
    "f32":  4.0,
    "q8_0": 1.0,
}

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


def _backend_base_url() -> str:
    host = os.environ.get("QZ_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("QZ_SERVER_PORT", "18084")
    return f"http://{host}:{port}"


def _selected_backend_model_id(handler=None) -> tuple[str, str]:
    """Return (model_id, source). Never raises. Returns ("", "unknown") when unavailable.

    Resolution order:
    1. QZ_VRAM_METRICS_MODEL env override
    2. handler._model_router().selected_backend_id()
    3. handler._model_catalog().selected entry backend_id
    4. Backend /v1/models first available model (fallback when catalog not yet loaded)
    """
    env_model = os.environ.get("QZ_VRAM_METRICS_MODEL", "").strip()
    if env_model:
        return env_model, "env"

    if handler is not None:
        try:
            router = handler._model_router()
            bid = router.selected_backend_id() or ""
            if bid:
                return bid, "model-router"
        except Exception:
            pass
        try:
            catalog = handler._model_catalog()
            # catalog.selected is None until a model is explicitly selected;
            # try entries[0] as fallback for the default loaded model
            entry = catalog.selected
            if entry is None and catalog.entries:
                entry = catalog.entries[0]
            if isinstance(entry, dict):
                bid = entry.get("backend_id") or entry.get("key") or entry.get("stem") or ""
                if bid:
                    return bid, "catalog"
        except Exception:
            pass

    # Fallback: query backend /v1/models to find what's loaded/available
    try:
        base = _backend_base_url()
        with urllib.request.urlopen(f"{base}/v1/models", timeout=1.0) as resp:
            data = _json.loads(resp.read().decode("utf-8", errors="replace"))
            models = data.get("data", [])
            if models and isinstance(models[0], dict):
                bid = models[0].get("id", "")
                if bid:
                    return bid, "backend-v1-models"
    except Exception:
        pass

    return "", "unknown"


def _normalize_prometheus_metrics(raw: dict) -> dict:
    """Map raw Prometheus metric names to canonical names via _METRIC_ALIASES.

    Returns only canonical names that are present in raw. Does not invent values.
    """
    normalized: dict[str, Any] = {}
    for canonical, aliases in _METRIC_ALIASES.items():
        for alias in aliases:
            if alias in raw:
                normalized[canonical] = raw[alias]
                break
    return normalized


def _probe_props(model_id: str, base_url: str, timeout: float = 1.0) -> tuple[dict, str]:
    """Probe /props?model=<model_id>. Returns (props_dict, error_note)."""
    if not model_id:
        return {}, ""
    url = f"{base_url}/props?model={urllib.parse.quote(model_id)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            obj = _json.loads(body)
            if not isinstance(obj, dict):
                return {}, "props returned non-dict"
            return obj, ""
    except _json.JSONDecodeError:
        return {}, ""
    except urllib.error.HTTPError as exc:
        return {}, f"props HTTP {exc.code}"
    except Exception:
        return {}, ""


def _probe_slots(model_id: str, base_url: str, timeout: float = 1.0) -> tuple[list, str]:
    """Probe /slots?model=<model_id>. Returns (slots_list, error_note)."""
    if not model_id:
        return [], ""
    url = f"{base_url}/slots?model={urllib.parse.quote(model_id)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            obj = _json.loads(body)
            if isinstance(obj, list):
                return obj, ""
            return [], ""
    except _json.JSONDecodeError:
        return [], ""
    except urllib.error.HTTPError as exc:
        return [], f"slots HTTP {exc.code}"
    except Exception:
        return [], ""


def _extract_context_from_props(props: dict) -> tuple[int | None, str]:
    """Extract context limit from /props response. Returns (n_ctx, field_path)."""
    if not isinstance(props, dict):
        return None, ""
    dgs = props.get("default_generation_settings")
    if isinstance(dgs, dict):
        # First check n_ctx directly
        n_ctx = dgs.get("n_ctx")
        if n_ctx is not None:
            try:
                return int(n_ctx), "default_generation_settings.n_ctx"
            except Exception:
                pass
        # Then check params sub-dict
        params = dgs.get("params")
        if isinstance(params, dict):
            n_ctx = params.get("n_ctx")
            if n_ctx is not None:
                try:
                    return int(n_ctx), "default_generation_settings.params.n_ctx"
                except Exception:
                    pass
    return None, ""


def _extract_context_from_slots(slots: list) -> tuple[int | None, int | None]:
    """Extract (n_ctx limit, max n_past used) from /slots list."""
    if not slots:
        return None, None
    n_ctx_val: int | None = None
    max_n_past: int | None = None
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        # Context limit
        c = slot.get("n_ctx")
        if c is not None:
            try:
                n_ctx_val = int(c)
            except Exception:
                pass
        # Best effort used tokens: n_past is most common
        for field in ("n_past", "n_prompt_tokens", "n_tokens",
                      "kv_cache_token_count", "token_count"):
            v = slot.get(field)
            if v is not None:
                try:
                    v_int = int(v)
                    max_n_past = max(max_n_past or 0, v_int)
                except Exception:
                    pass
    return n_ctx_val, max_n_past


def _build_backend_metrics_summary(
    model_id: str,
    model_src: str,
    raw_metrics: dict,
    metrics_note: str,
    props: dict,
    slots: list,
    props_note: str,
    slots_note: str,
    base_url: str,
) -> dict:
    """Build the backend_metrics sub-object for the snapshot."""
    normalized = _normalize_prometheus_metrics(raw_metrics)

    # Endpoint summaries (compact, no raw body)
    endpoints: dict = {}

    # /metrics?model
    if raw_metrics:
        names = sorted(raw_metrics.keys())
        endpoints["metrics"] = {
            "kind": "prometheus",
            "metric_count": len(names),
            "metric_names": names[:20],
            "error": metrics_note,
        }
    elif metrics_note:
        endpoints["metrics"] = {"kind": "json_error", "error": metrics_note}
    else:
        endpoints["metrics"] = {"kind": "unavailable", "error": ""}

    # /props?model
    if props:
        n_ctx_p, _ = _extract_context_from_props(props)
        endpoints["props"] = {
            "kind": "json",
            "keys": sorted(props.keys())[:20],
            "n_ctx": n_ctx_p,
            "error": props_note,
        }
    elif props_note:
        endpoints["props"] = {"kind": "unavailable", "error": props_note}

    # /slots?model
    if slots:
        n_ctx_s, n_past = _extract_context_from_slots(slots)
        endpoints["slots"] = {
            "kind": "json",
            "slot_count": len(slots),
            "n_ctx": n_ctx_s,
            "n_past_max": n_past,
            "error": slots_note,
        }
    elif slots_note:
        endpoints["slots"] = {"kind": "unavailable", "error": slots_note}

    available = bool(raw_metrics or props or slots)
    return {
        "available": available,
        "model": model_id,
        "model_source": model_src,
        "base_url": base_url,
        "endpoints": endpoints,
        "normalized": {
            k: {"value": v, "confidence": "backend-confirmed"}
            for k, v in normalized.items()
        },
    }


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


def _probe_backend_metrics(model_id: str = "", timeout: float = 1.0) -> tuple:
    """Fetch /metrics (with optional ?model=<id>). Returns (metrics_dict, error_note).

    TurboQuant router requires ?model=<selected_backend_id>; without it the router
    returns a JSON error {"error": {"message": "model name is missing from the request"}}.
    When model_id is provided, uses /metrics?model=<model_id>.
    When metrics return a JSON error body, error_note records the reason.
    """
    base = _backend_base_url()
    if model_id:
        url = f"{base}/metrics?model={urllib.parse.quote(model_id)}"
    else:
        url = f"{base}/metrics"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
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


def _build_context(
    handler=None,
    metrics: dict | None = None,
    props: dict | None = None,
    slots: list | None = None,
) -> dict:
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

    # Backend Prometheus metrics (alias table)
    if metrics:
        normalized = _normalize_prometheus_metrics(metrics)
        if "context_limit_tokens" in normalized:
            limit     = int(normalized["context_limit_tokens"])
            limit_src  = "backend-metrics"
            limit_conf = "backend-confirmed"

    # /props?model backend-confirmed context limit (overrides config if present)
    if props:
        n_ctx_p, field_path = _extract_context_from_props(props)
        if n_ctx_p is not None and n_ctx_p > 0:
            limit     = n_ctx_p
            limit_src  = f"backend-props/{field_path}"
            limit_conf = "backend-confirmed"

    # /slots?model (corroborates or provides context limit + used tokens)
    if slots:
        n_ctx_s, n_past = _extract_context_from_slots(slots)
        if n_ctx_s is not None and n_ctx_s > 0:
            # Only override limit from slots if not already backend-confirmed from props
            if limit_conf != "backend-confirmed" or limit_src.startswith("backend-props"):
                pass  # props is better; keep it
            if limit is None:
                limit     = n_ctx_s
                limit_src  = "backend-slots/n_ctx"
                limit_conf = "backend-confirmed"

    # Used tokens from KV cache cells (Prometheus) or slots n_past
    used: int | None = None
    used_src  = "unknown"
    used_conf = "unknown"
    if metrics:
        normalized = _normalize_prometheus_metrics(metrics)
        if "context_used_tokens" in normalized:
            used      = int(normalized["context_used_tokens"])
            used_src   = "backend-metrics"
            used_conf  = "backend-confirmed"
    if used is None and slots:
        _, n_past = _extract_context_from_slots(slots)
        if n_past is not None and n_past > 0:
            used      = n_past
            used_src   = "backend-slots/n_past"
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


def _selected_model_catalog_entry(handler=None, model_id: str = "") -> tuple[dict, str]:
    """Resolve the selected catalog entry. Returns (entry, source). Never raises.

    Resolution order:
    1. handler._model_router().selected_model_entry()
    2. handler._model_catalog().selected
    3. handler._model_catalog().entries matched by backend_id / key / filename / stem / alias
    4. empty dict if unavailable
    """
    if handler is not None:
        try:
            router = handler._model_router()
            entry = router.selected_model_entry()
            if isinstance(entry, dict) and entry:
                return entry, "model-router"
        except Exception:
            pass
        try:
            catalog = handler._model_catalog()
            entry = catalog.selected
            if isinstance(entry, dict) and entry:
                return entry, "catalog-selected"
        except Exception:
            pass
        if model_id:
            try:
                catalog = handler._model_catalog()
                q = model_id.lower()
                for entry in (catalog.entries or []):
                    if not isinstance(entry, dict):
                        continue
                    candidates: set = {
                        entry.get("backend_id"),
                        entry.get("key"),
                        entry.get("filename"),
                        entry.get("stem"),
                    }
                    candidates.update(entry.get("aliases") or [])
                    if any(isinstance(c, str) and c.lower() == q for c in candidates):
                        return entry, "catalog-match"
            except Exception:
                pass
    return {}, "unknown"


def _kv_dtype_config(handler=None, entry=None) -> dict:
    """Return KV key/value dtype config. Never raises.

    Sources in order: env QZ_KV_KEY/QZ_KV_VALUE, launch_args, default f16/f16.
    """
    notes: list = []

    env_key = os.environ.get("QZ_KV_KEY", "").strip().lower()
    env_val = os.environ.get("QZ_KV_VALUE", "").strip().lower()
    if env_key or env_val:
        ktype = env_key or "f16"
        vtype = env_val or "f16"
        kb = _KV_DTYPE_BYTES.get(ktype)
        vb = _KV_DTYPE_BYTES.get(vtype)
        if kb is None:
            notes.append(f"Unsupported KV key dtype '{ktype}'; using f16 estimate.")
            kb, ktype = 2.0, "f16"
        if vb is None:
            notes.append(f"Unsupported KV value dtype '{vtype}'; using f16 estimate.")
            vb, vtype = 2.0, "f16"
        return {
            "key_type": ktype, "value_type": vtype,
            "key_bytes": kb, "value_bytes": vb,
            "source": "env", "confidence": "config", "notes": notes,
        }

    if isinstance(entry, dict):
        launch_args = entry.get("launch_args") or []
        ktype = vtype = None
        i = 0
        while i < len(launch_args) - 1:
            arg = str(launch_args[i])
            nxt = str(launch_args[i + 1])
            if arg in ("--cache-type-k", "-ctk"):
                ktype = nxt.strip().lower()
            elif arg in ("--cache-type-v", "-ctv"):
                vtype = nxt.strip().lower()
            i += 1
        if ktype or vtype:
            ktype = ktype or "f16"
            vtype = vtype or "f16"
            kb = _KV_DTYPE_BYTES.get(ktype)
            vb = _KV_DTYPE_BYTES.get(vtype)
            if kb is None:
                notes.append(f"Unsupported KV key dtype '{ktype}'; using f16 estimate.")
                kb, ktype = 2.0, "f16"
            if vb is None:
                notes.append(f"Unsupported KV value dtype '{vtype}'; using f16 estimate.")
                vb, vtype = 2.0, "f16"
            return {
                "key_type": ktype, "value_type": vtype,
                "key_bytes": kb, "value_bytes": vb,
                "source": "launch_args", "confidence": "config", "notes": notes,
            }

    return {
        "key_type": "f16", "value_type": "f16",
        "key_bytes": 2.0, "value_bytes": 2.0,
        "source": "default", "confidence": "estimated-default", "notes": notes,
    }


def _metadata_arch_prefix(entry: dict) -> str:
    """Return architecture prefix for GGUF metadata key lookup. Never raises.

    Prefers entry["architecture"] (already resolved by qz_model_catalog),
    then metadata["general.architecture"], then falls back to "llama".
    """
    if isinstance(entry, dict):
        arch = entry.get("architecture")
        if isinstance(arch, str) and arch.strip() and arch.strip() != "unknown":
            return arch.strip()
        meta = entry.get("metadata") or {}
        arch = meta.get("general.architecture")
        if isinstance(arch, str) and arch.strip():
            return arch.strip()
    return "llama"


def _metadata_first(meta: dict, names: list, arch: str = "") -> tuple:
    """Search metadata for the first available key. Returns (value, key_used).

    For each name in names, tries in order:
    1. {arch}.{name}   — arch-prefixed (e.g. qwen3.block_count)
    2. llama.{name}    — llama compatibility fallback
    3. {name} exactly  — bare suffix

    Then as last resort:
    4. Suffix match: if exactly one metadata key ends with ".{name}", use it.
       Ambiguous matches (>1 candidate) are silently skipped.

    Returns (None, "") if no match found. Never raises.
    """
    tried: set = set()

    def _get(key: str):
        if key in tried:
            return None, False
        tried.add(key)
        if key in meta:
            val = meta[key]
            if val is not None:
                return val, True
        return None, False

    for name in names:
        # 1. arch-prefixed
        if arch and not name.startswith(f"{arch}."):
            val, found = _get(f"{arch}.{name}")
            if found:
                return val, f"{arch}.{name}"
        # 2. llama fallback
        if not name.startswith("llama."):
            val, found = _get(f"llama.{name}")
            if found:
                return val, f"llama.{name}"
        # 3. exact name
        val, found = _get(name)
        if found:
            return val, name

    # 4. Suffix match — last resort, unambiguous only
    for name in names:
        dot_suffix = f".{name}"
        candidates = [k for k in meta if k.endswith(dot_suffix) and k not in tried]
        if len(candidates) == 1 and meta[candidates[0]] is not None:
            return meta[candidates[0]], candidates[0]

    return None, ""


def _estimate_kv_cache_bytes(
    entry: dict, context_limit_tokens: int | None, kv_dtype: dict
) -> dict:
    """Estimate full-context KV cache allocation from GGUF metadata. Never raises.

    Architecture-aware: resolves metadata keys by {arch}.*, llama.*, bare suffix,
    then unambiguous suffix match. Works for qwen3, qwen2, llama, and other archs.

    Formula per token per layer:
      head_count_kv * key_length * key_bytes + head_count_kv * value_length * value_bytes

    Full allocation: context_limit_tokens * layers * (above)
    """
    _unknown: dict = {
        "mib": None, "bytes": None,
        "source": "gguf-metadata-formula",
        "confidence": "unknown",
        "estimated": True, "backend_confirmed": False,
    }
    if not isinstance(entry, dict):
        return {**_unknown, "notes": ["No catalog entry available."]}

    meta = entry.get("metadata") or {}
    arch = _metadata_arch_prefix(entry)
    notes: list = []

    layers,   layers_key   = _metadata_first(meta, ["block_count"], arch)
    emb_len,  emb_len_key  = _metadata_first(meta, ["embedding_length"], arch)
    head_cnt, head_cnt_key = _metadata_first(meta, ["attention.head_count"], arch)
    head_kv,  head_kv_key  = _metadata_first(meta, ["attention.head_count_kv"], arch)
    key_len,  key_len_key  = _metadata_first(meta, ["attention.key_length"], arch)
    val_len,  val_len_key  = _metadata_first(meta, ["attention.value_length"], arch)

    # head_count_kv falls back to head_count (GQA models where kv=full heads)
    if head_kv is None and head_cnt is not None:
        head_kv     = head_cnt
        head_kv_key = (head_cnt_key or "head_count") + " (fallback: head_count)"

    # Check required fields
    missing_logical: list = []
    if layers is None:
        missing_logical.append("block_count")
    if head_cnt is None:
        missing_logical.append("attention.head_count")
    if missing_logical:
        sample_keys = sorted(meta.keys())[:12]
        return {
            **_unknown,
            "architecture":                   arch,
            "missing_logical_fields":         missing_logical,
            "available_metadata_keys_sample": sample_keys,
            "notes": [
                f"KV estimate unavailable for arch={arch}: "
                f"missing {missing_logical}. "
                f"Available keys (sample): {sample_keys}"
            ],
        }

    # Derive per-head dimension if key/value lengths absent
    if key_len is None or val_len is None:
        if emb_len is not None and head_cnt:
            head_dim = emb_len / head_cnt
            if key_len is None:
                key_len    = head_dim
                key_len_key = (emb_len_key or "embedding_length") + "/head_count"
                notes.append(f"key_length derived from embedding_length/head_count ({head_dim:.0f})")
            if val_len is None:
                val_len    = head_dim
                val_len_key = (emb_len_key or "embedding_length") + "/head_count"
                notes.append(f"value_length derived from embedding_length/head_count ({head_dim:.0f})")
        else:
            deriv_missing: list = []
            if key_len is None:
                deriv_missing.append("attention.key_length")
            if val_len is None:
                deriv_missing.append("attention.value_length")
            if emb_len is None:
                deriv_missing.append("embedding_length")
            sample_keys = sorted(meta.keys())[:12]
            return {
                **_unknown,
                "architecture":                   arch,
                "missing_logical_fields":         deriv_missing,
                "available_metadata_keys_sample": sample_keys,
                "notes": [
                    f"KV estimate unavailable for arch={arch}: "
                    f"cannot derive head_dim; missing {deriv_missing}. "
                    f"Available keys (sample): {sample_keys}"
                ],
            }

    if not context_limit_tokens or context_limit_tokens <= 0:
        return {**_unknown, "notes": ["KV estimate unavailable: context_limit_tokens unknown."]}

    key_bytes   = kv_dtype.get("key_bytes", 2.0)
    value_bytes = kv_dtype.get("value_bytes", 2.0)

    kv_bytes_per_token_per_layer = (
        head_kv * key_len * key_bytes + head_kv * val_len * value_bytes
    )
    kv_alloc_bytes = context_limit_tokens * layers * kv_bytes_per_token_per_layer
    kv_alloc_mib   = kv_alloc_bytes / (1024.0 * 1024.0)

    notes.extend([
        "Estimates full-context KV allocation.",
        "Excludes scratch/work buffers.",
        "May miss padding/alignment/fragmentation.",
    ])

    return {
        "mib":               round(kv_alloc_mib, 2),
        "bytes":             kv_alloc_bytes,
        "source":            "gguf-metadata-formula",
        "confidence":        "estimated-from-gguf-metadata",
        "estimated":         True,
        "backend_confirmed": False,
        "architecture":      arch,
        "formula": {
            "layers":               layers,
            "head_count":           head_cnt,
            "head_count_kv":        head_kv,
            "key_length":           key_len,
            "value_length":         val_len,
            "key_bytes":            key_bytes,
            "value_bytes":          value_bytes,
            "context_limit_tokens": context_limit_tokens,
            "keys": {
                "layers":       layers_key,
                "head_count":   head_cnt_key,
                "head_count_kv": head_kv_key,
                "key_length":   key_len_key,
                "value_length": val_len_key,
                **({"embedding_length": emb_len_key} if emb_len_key else {}),
            },
        },
        "notes": notes,
    }


def _assemble_snapshot(
    gpus: list,
    backend_proc: dict,
    metrics: dict,
    context: dict,
    *,
    now: float,
    metrics_note: str = "",
    backend_metrics_summary: dict | None = None,
    catalog_entry: dict | None = None,
    catalog_entry_source: str = "unknown",
    kv_dtype: dict | None = None,
) -> dict:
    """Assemble full qz.vram.snapshot.v1 dict from collected data.

    Components: model, kv_alloc, kv_used, scratch_buffer, other_residual.
    Residual subtracts MODEL + KV_ALLOC only (not KV_USED, which is within KV_ALLOC).
    Backend metrics beat catalog estimates; estimates are never marked backend_confirmed.
    """
    host_observed         = len(gpus) > 0
    _bm                   = backend_metrics_summary or {}
    backend_metrics_avail = bool(metrics) or bool(_bm.get("available"))

    total_used  = sum(g.get("used_mib", 0.0) for g in gpus)
    total_total = sum(g.get("total_mib", 0.0) for g in gpus)
    total_avail = max(0.0, total_total - total_used)

    backend_process_used = backend_proc.get("process_used_mib")
    normalized_metrics   = _normalize_prometheus_metrics(metrics) if metrics else {}

    catalog_entry = catalog_entry or {}
    kv_dtype      = kv_dtype or {}

    # ------------------------------------------------------------------
    # MODEL
    # ------------------------------------------------------------------
    model_mib              = None
    model_src              = "unknown"
    model_conf             = "unknown"
    model_estimated        = True
    model_backend_confirmed = False
    model_notes: list      = []

    if "model_size_bytes" in normalized_metrics:
        raw = normalized_metrics["model_size_bytes"]
        if raw > 0:
            model_mib               = max(0.0, raw / (1024.0 * 1024.0))
            model_src               = "backend-metrics"
            model_conf              = "backend-confirmed"
            model_estimated         = False
            model_backend_confirmed = True

    if model_mib is None and catalog_entry:
        size_bytes = catalog_entry.get("size_bytes")
        if size_bytes and size_bytes > 0:
            model_mib  = size_bytes / (1024.0 * 1024.0)
            model_src  = "catalog.size_bytes"
            model_conf = "estimated-from-gguf-size"
            model_notes.append(
                "GGUF file size; useful proxy for model weight footprint, not exact VRAM allocation."
            )

    model_comp: dict = {
        "name": "model", "label": "MODEL",
        "mib":               round(model_mib, 2) if model_mib is not None else None,
        "source":            model_src,
        "confidence":        model_conf,
        "estimated":         model_estimated,
        "backend_confirmed": model_backend_confirmed,
    }
    if model_notes:
        model_comp["notes"] = model_notes

    # ------------------------------------------------------------------
    # KV_ALLOC — estimated reserved full-context KV allocation
    # ------------------------------------------------------------------
    kv_alloc_mib              = None
    kv_alloc_src              = "unknown"
    kv_alloc_conf             = "unknown"
    kv_alloc_estimated        = True
    kv_alloc_backend_confirmed = False
    kv_alloc_formula          = None

    if "kv_cache_size_bytes" in normalized_metrics:
        raw = normalized_metrics["kv_cache_size_bytes"]
        if raw > 0:
            kv_alloc_mib               = max(0.0, raw / (1024.0 * 1024.0))
            kv_alloc_src               = "backend-metrics"
            kv_alloc_conf              = "backend-confirmed"
            kv_alloc_estimated         = False
            kv_alloc_backend_confirmed = True

    kv_est: dict = {}
    if kv_alloc_mib is None:
        kv_est = _estimate_kv_cache_bytes(catalog_entry, context.get("limit_tokens"), kv_dtype)
        if kv_est.get("mib") is not None:
            kv_alloc_mib      = kv_est["mib"]
            kv_alloc_src      = kv_est["source"]
            kv_alloc_conf     = kv_est["confidence"]
            kv_alloc_formula  = kv_est.get("formula")

    kv_alloc_comp: dict = {
        "name": "kv_alloc", "label": "KV_ALLOC",
        "mib":               kv_alloc_mib,
        "source":            kv_alloc_src,
        "confidence":        kv_alloc_conf,
        "estimated":         kv_alloc_estimated,
        "backend_confirmed": kv_alloc_backend_confirmed,
        "context_limit":     context.get("limit_tokens"),
    }
    if kv_alloc_formula:
        kv_alloc_comp["formula"] = kv_alloc_formula
    # Propagate diagnostics when KV_ALLOC estimation failed
    if kv_alloc_mib is None and kv_est:
        if kv_est.get("architecture"):
            kv_alloc_comp["architecture"] = kv_est["architecture"]
        if kv_est.get("missing_logical_fields"):
            kv_alloc_comp["missing_logical_fields"] = kv_est["missing_logical_fields"]
        if kv_est.get("available_metadata_keys_sample"):
            kv_alloc_comp["available_metadata_keys_sample"] = kv_est["available_metadata_keys_sample"]
        if kv_est.get("notes"):
            kv_alloc_comp["notes"] = kv_est["notes"]

    # ------------------------------------------------------------------
    # KV_USED — estimated active context occupancy (within KV_ALLOC)
    # Do NOT subtract KV_USED from residual; it is already within KV_ALLOC.
    # ------------------------------------------------------------------
    kv_used_mib  = None
    kv_used_src  = "unknown"
    kv_used_conf = "unknown"
    kv_used_bc   = False

    if "kv_cache_used_bytes" in normalized_metrics:
        raw = normalized_metrics["kv_cache_used_bytes"]
        if raw > 0:
            kv_used_mib  = max(0.0, raw / (1024.0 * 1024.0))
            kv_used_src  = "backend-metrics"
            kv_used_conf = "backend-confirmed"
            kv_used_bc   = True

    if kv_used_mib is None and kv_alloc_mib is not None:
        used_tokens  = context.get("used_tokens")
        limit_tokens = context.get("limit_tokens")
        used_pct     = context.get("used_pct")
        if used_tokens is not None and limit_tokens and limit_tokens > 0:
            kv_used_mib  = round(kv_alloc_mib * used_tokens / limit_tokens, 2)
            kv_used_src  = "kv_alloc_estimate × backend context occupancy"
            kv_used_conf = "estimated-runtime-occupancy"
        elif used_pct is not None:
            kv_used_mib  = round(kv_alloc_mib * used_pct / 100.0, 2)
            kv_used_src  = "kv_alloc_estimate × context used_pct"
            kv_used_conf = "estimated-runtime-occupancy"

    kv_used_comp: dict = {
        "name": "kv_used", "label": "KV_USED",
        "mib":                 kv_used_mib,
        "source":              kv_used_src,
        "confidence":          kv_used_conf,
        "estimated":           True,
        "backend_confirmed":   kv_used_bc,
        "context_used_tokens": context.get("used_tokens"),
        "context_used_pct":    context.get("used_pct"),
        "context_limit":       context.get("limit_tokens"),
    }

    # ------------------------------------------------------------------
    # SCRATCH — unknown unless explicit backend metric exists
    # ------------------------------------------------------------------
    scratch_comp: dict = {
        "name": "scratch_buffer", "label": "SCRATCH",
        "mib": None, "source": "unknown", "confidence": "unknown",
        "estimated": True, "backend_confirmed": False,
    }

    # ------------------------------------------------------------------
    # OTHER / residual = process_used - MODEL - KV_ALLOC
    # KV_USED is intentionally excluded (it is already within KV_ALLOC).
    # ------------------------------------------------------------------
    known_sum = sum(
        c["mib"] for c in (model_comp, kv_alloc_comp) if c.get("mib") is not None
    )
    residual_base = (
        backend_process_used if backend_process_used is not None
        else total_used if host_observed
        else None
    )

    residual_notes: list = []
    if residual_base is not None:
        raw_residual = residual_base - known_sum
        if raw_residual < 0:
            residual_mib  = 0.0
            residual_conf = "derived-clamped"
            residual_notes.append(
                "Estimated components exceed measured process VRAM; residual clamped to 0."
            )
        else:
            residual_mib  = raw_residual
            residual_conf = "host-observed-residual"
    else:
        residual_mib  = None
        residual_conf = "unknown"

    other_comp: dict = {
        "name": "other_residual", "label": "OTHER",
        "mib":               round(residual_mib, 1) if residual_mib is not None else None,
        "source":            "host_process_residual" if residual_mib is not None else "unknown",
        "confidence":        residual_conf if residual_mib is not None else "unknown",
        "estimated":         True,
        "backend_confirmed": False,
    }
    if residual_notes:
        other_comp["notes"] = residual_notes

    components = [model_comp, kv_alloc_comp, kv_used_comp, scratch_comp, other_comp]

    # ------------------------------------------------------------------
    # Estimates/provenance block
    # ------------------------------------------------------------------
    estimates: dict = {
        "schema":                    "qz.vram.estimates.v1",
        "model_entry_source":        catalog_entry_source,
        "model_size_source":         model_src,
        "kv_formula_available":      kv_alloc_mib is not None and not kv_alloc_backend_confirmed,
        "kv_dtype":                  kv_dtype if kv_dtype else None,
        "known_allocated_mib":       round(known_sum, 2) if known_sum > 0 else None,
        "known_allocated_confidence": "mixed-estimated",
        "residual_basis":            "backend_process_used_mib - estimated MODEL - estimated KV_ALLOC",
    }

    # ------------------------------------------------------------------
    # Overall confidence
    # ------------------------------------------------------------------
    backend_confirmed_flag = backend_metrics_avail and any(
        c.get("confidence") == "backend-confirmed" for c in components
    )
    if backend_confirmed_flag and host_observed:
        confidence = "mixed"
    elif backend_confirmed_flag:
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
    for n in (backend_proc.get("notes") or []):
        if n and n not in notes:
            notes.append(n)

    return {
        "schema":                    VRAM_SNAPSHOT_SCHEMA,
        "ok":                        host_observed or backend_metrics_avail,
        "timestamp":                 now,
        "source":                    "proxy",
        "confidence":                confidence,
        "backend_confirmed":         backend_confirmed_flag,
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
        "estimates":  estimates,
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
        "context":         context,
        "backend_metrics": backend_metrics_summary or {
            "available": False, "model": "", "model_source": "unknown", "endpoints": {},
        },
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
        model_id, model_src = _selected_backend_model_id(handler)
        base_url  = _backend_base_url()

        gpus                      = _parse_nvidia_smi_gpus(timeout=2.0)
        backend_proc              = _probe_backend_process(container, timeout=1.5)
        metrics, metrics_note     = _probe_backend_metrics(model_id, timeout=1.0)
        props, props_note         = _probe_props(model_id, base_url, timeout=1.0)
        slots, slots_note         = _probe_slots(model_id, base_url, timeout=1.0)

        backend_metrics_summary = _build_backend_metrics_summary(
            model_id, model_src, metrics, metrics_note,
            props, slots, props_note, slots_note, base_url,
        )
        context = _build_context(
            handler=handler, metrics=metrics, props=props, slots=slots,
        )
        catalog_entry, catalog_entry_source = _selected_model_catalog_entry(handler, model_id)
        kv_dtype = _kv_dtype_config(handler=handler, entry=catalog_entry)
        return _assemble_snapshot(
            gpus, backend_proc, metrics, context,
            now=now, metrics_note=metrics_note,
            backend_metrics_summary=backend_metrics_summary,
            catalog_entry=catalog_entry,
            catalog_entry_source=catalog_entry_source,
            kv_dtype=kv_dtype,
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
