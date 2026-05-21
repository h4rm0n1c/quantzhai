"""Structural tests for scripts/qz-top direct backend status rendering."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QZ_TOP = REPO_ROOT / "scripts" / "qz-top"


def test_qz_top_renders_direct_ready_and_admission_state():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert "selected_model_ready" in src
    assert "request_admission_state" in src
    assert "ready={str(model_status.selected_model_ready).lower()}" in src
    assert "admission={model_status.request_admission_state}" in src


def test_qz_top_renders_runtime_failure_as_runtime_death():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert "runtime_failure_error_type" in src
    assert "backend_died_after_healthy" in src
    assert '"DEATH"' in src
    assert "runtime={model_status.runtime_failure_error_type}" in src


def test_qz_top_no_longer_renders_backend_mode_as_loaded_state():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert "mode={model_status.backend_model_mode}" not in src
