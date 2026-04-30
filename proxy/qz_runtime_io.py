#!/usr/bin/env python3
import json
import os
from pathlib import Path


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
    return capture_mode() != "off"


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


def capture_path(name: str) -> Path:
    return capture_dir() / name


def write_capture(name: str, payload, mode: str = "text"):
    if not capture_enabled():
        return
    _ensure_capture_dir()
    path = capture_path(name)
    if mode == "bytes":
        path.write_bytes(payload)
    elif isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def append_capture(name: str, text: str):
    if not capture_enabled():
        return
    _ensure_capture_dir()
    with capture_path(name).open("a", encoding="utf-8") as handle:
        handle.write(text)


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
