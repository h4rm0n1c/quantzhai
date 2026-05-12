#!/usr/bin/env python3
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

CAPTURE_POLICY_SCHEMA = "qz.capture.policy.v1"


def capture_mode() -> str:
    raw = (os.environ.get("QZ_CAPTURE_MODE") or "off").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return "latest"
    if raw in {"minimal"}:
        return "minimal"
    if raw in {"full"}:
        return "full"
    if raw in {"latest"}:
        return "latest"
    return "off"


def capture_enabled() -> bool:
    return capture_policy().enabled


@dataclass(frozen=True)
class CapturePolicy:
    schema: str
    mode: str
    enabled: bool
    write_latest: bool
    write_request_scoped: bool
    write_raw_streams: bool

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "mode": self.mode,
            "enabled": self.enabled,
            "write_latest": self.write_latest,
            "write_request_scoped": self.write_request_scoped,
            "write_raw_streams": self.write_raw_streams,
        }


def capture_policy() -> CapturePolicy:
    mode = capture_mode()
    enabled = mode != "off"
    # Preserve current behaviour for latest/minimal/full while making the policy explicit.
    return CapturePolicy(
        schema=CAPTURE_POLICY_SCHEMA,
        mode=mode,
        enabled=enabled,
        write_latest=enabled,
        write_request_scoped=enabled,
        write_raw_streams=enabled,
    )


def quantzhai_var_dir() -> Path:
    return Path(os.environ.get("QZ_VAR_DIR") or Path(__file__).resolve().parents[1] / "var")


def runtime_state_path(name: str) -> Path:
    return quantzhai_var_dir() / name


def capture_dir() -> Path:
    return quantzhai_var_dir() / "captures"


def _ensure_capture_dir():
    path = capture_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_capture_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def capture_path(name: str) -> Path:
    return capture_dir() / name


def _safe_capture_component(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text[:160] or "unknown"


def request_capture_dir(request_id: str) -> Path:
    return capture_dir() / "requests" / _safe_capture_component(request_id)


def request_capture_path(request_id: str, name: str) -> Path:
    return request_capture_dir(request_id) / name


def write_capture(name: str, payload, mode: str = "text"):
    if not capture_policy().write_latest:
        return
    _ensure_capture_dir()
    path = capture_path(name)
    _write_payload(path, payload, mode=mode)


def write_request_capture(request_id: str, name: str, payload, mode: str = "text"):
    if not request_id or not capture_policy().write_request_scoped:
        return
    path = request_capture_path(request_id, name)
    _write_payload(path, payload, mode=mode)


def write_dual_capture(latest_name: str, request_id: str | None, request_name: str | None, payload, mode: str = "text"):
    policy = capture_policy()
    if not policy.enabled:
        return
    if policy.write_latest and latest_name:
        write_capture(latest_name, payload, mode=mode)
    if policy.write_request_scoped and request_id and request_name:
        write_request_capture(request_id, request_name, payload, mode=mode)


def _write_payload(path: Path, payload, mode: str = "text"):
    _ensure_capture_parent(path)
    if mode == "bytes":
        path.write_bytes(payload)
    elif isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def append_capture(name: str, text: str | bytes):
    if not capture_policy().write_latest:
        return
    _ensure_capture_dir()
    if isinstance(text, bytes):
        with capture_path(name).open("ab") as handle:
            handle.write(text)
        return
    with capture_path(name).open("a", encoding="utf-8") as handle:
        handle.write(text)


def append_request_capture(request_id: str, name: str, text: str | bytes):
    if not request_id or not capture_policy().write_request_scoped:
        return
    path = request_capture_path(request_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        with path.open("ab") as handle:
            handle.write(text)
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(str(text))


def append_dual_capture(latest_name: str, request_id: str | None, request_name: str | None, text: str | bytes):
    policy = capture_policy()
    if not policy.enabled:
        return
    if policy.write_latest and latest_name:
        append_capture(latest_name, text)
    if policy.write_request_scoped and request_id and request_name:
        append_request_capture(request_id, request_name, text)


def open_dual_capture_append(latest_name: str, request_id: str | None = None, request_name: str | None = None, binary: bool = False):
    policy = capture_policy()
    if not policy.enabled:
        return []
    mode = "ab" if binary else "a"
    encoding = None if binary else "utf-8"
    handles = []
    if policy.write_latest and latest_name:
        path = _ensure_capture_parent(capture_path(latest_name))
        handles.append(path.open(mode, encoding=encoding))
    if policy.write_request_scoped and request_id and request_name:
        path = _ensure_capture_parent(request_capture_path(request_id, request_name))
        handles.append(path.open(mode, encoding=encoding))
    return handles


def incoming_headers_payload(handler) -> dict:
    """Extract all incoming request headers as a plain dict.

    Python's http.server preserves original header name casing in
    handler.headers.items(). This function returns those items as-is.
    """
    return dict(handler.headers.items())


def runtime_log(name: str, payload):
    write_capture(name, payload)


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
