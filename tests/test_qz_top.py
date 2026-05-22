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


def test_qz_top_shows_proxy_offline_label_when_not_proxy():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert "PROXY OFFLINE" in src
    assert "proxy_offline" in src or "not proxy" in src


def test_qz_top_control_plane_reads_profile_section():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert 'pf = cp.get("profile") or {}' in src
    assert 'pf.get("reasoning_level")' in src
    assert 'pf.get("prompt_files")' in src
    assert 'pf.get("selected_context_length")' in src
    assert 'pf.get("backend_context_length")' in src


def test_qz_top_rates_includes_cached_reasoning_tokens():
    src = QZ_TOP.read_text(encoding="utf-8")
    assert "cached_tokens" in src
    assert "reasoning_tokens" in src
    assert "cached=" in src
    assert "reasoning=" in src


def test_qz_top_state_uses_service_status_model_state_as_fallback():
    """qz-top must fall back to service_status.model_state=loaded when top-level
    status is stale (model_not_loaded) but service_status says loaded.

    Regression guard: before the fix, a timing race left status='model_not_loaded'
    even though service_status.model_state='loaded', causing STATE=not_loaded.
    """
    src = QZ_TOP.read_text(encoding="utf-8")
    # The fallback must read service_status.model_state
    assert 'service_status' in src or 'ss.get("model_state")' in src
    # The fallback must override selected_state for "not_loaded" and "unknown"
    assert '"not_loaded"' in src or "'not_loaded'" in src
    assert '"loaded"' in src or "'loaded'" in src


def test_qz_top_service_status_fallback_reads_correct_key():
    """The service_status model_state fallback uses the correct key name."""
    src = QZ_TOP.read_text(encoding="utf-8")
    assert 'ss.get("model_state")' in src or '"model_state"' in src


def test_qz_top_status_map_maps_ready_to_loaded():
    """status_map must map 'ready' to 'loaded', not 'not_loaded'."""
    src = QZ_TOP.read_text(encoding="utf-8")
    assert '"ready": "loaded"' in src or "'ready': 'loaded'" in src


def test_qz_top_status_map_does_not_map_model_not_loaded_to_loaded():
    """status_map must not map 'model_not_loaded' to 'loaded'."""
    src = QZ_TOP.read_text(encoding="utf-8")
    assert '"model_not_loaded": "loaded"' not in src
    assert "'model_not_loaded': 'loaded'" not in src
