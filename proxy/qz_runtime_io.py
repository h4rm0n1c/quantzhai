#!/usr/bin/env python3
import json
import os
from pathlib import Path


def quantzhai_var_dir() -> Path:
    return Path(os.environ.get("QZ_VAR_DIR") or Path(__file__).resolve().parents[1] / "var")


def capture_dir() -> Path:
    path = quantzhai_var_dir() / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def capture_path(name: str) -> Path:
    return capture_dir() / name


def write_capture(name: str, payload, mode: str = "text"):
    path = capture_path(name)
    if mode == "bytes":
        path.write_bytes(payload)
    elif isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def append_capture(name: str, text: str):
    with capture_path(name).open("a", encoding="utf-8") as handle:
        handle.write(text)


def runtime_log(name: str, payload):
    write_capture(name, payload)
