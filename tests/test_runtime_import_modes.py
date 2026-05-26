import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROXY_DIR = ROOT / "proxy"


def _run_python(code: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_package_mode_imports_router_and_native_output():
    result = _run_python(
        """
        import proxy.qz_request_router
        import proxy.qz_responses_stream
        import proxy.qz_native_tool_output
        import proxy.qz_native_signal
        print("package ok")
        """,
        ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "package ok" in result.stdout


def test_script_mode_imports_router_and_native_output():
    result = _run_python(
        """
        import qz_request_router
        import qz_responses_stream
        import qz_native_tool_output
        import qz_native_signal
        print("script ok")
        """,
        PROXY_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "script ok" in result.stdout


def test_script_mode_native_advisory_path_handles_request_user_input():
    result = _run_python(
        """
        from qz_native_signal import NativeToolAdvisoryState, check_native_advisories, record_native_tool_call
        from qz_proxy_tools import make_proxy_local_tool_registry

        class DummyWebRuntime:
            pass

        call = {
            "type": "function_call",
            "name": "request_user_input",
            "call_id": "call_input",
            "arguments": "{}",
        }
        state = NativeToolAdvisoryState()
        record_native_tool_call(call, state)
        assert check_native_advisories(call, state) is None
        decision = make_proxy_local_tool_registry(DummyWebRuntime()).completed_call_decision(
            call,
            native_advisory_state=state,
        )
        assert decision.kind == "public"
        print("request_user_input path ok")
        """,
        PROXY_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "request_user_input path ok" in result.stdout


def test_script_mode_seed_native_advisory_state_handles_tool_output():
    result = _run_python(
        """
        from qz_native_signal import seed_native_advisory_state

        items = [
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_exec",
                "arguments": '{"cmd":"false"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_exec",
                "output": "Process exited with code 1\\nOutput:\\n",
            },
        ]
        state = seed_native_advisory_state(items)
        assert state.native_call_count == 1
        assert sum(state.command_failure_counts.values()) == 1
        print("seed path ok")
        """,
        PROXY_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "seed path ok" in result.stdout


def test_script_mode_request_permissions_classifier_still_works():
    result = _run_python(
        """
        import qz_native_tool_output

        items = [
            {
                "type": "function_call",
                "name": "request_permissions",
                "call_id": "call_perm",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_perm",
                "output": '{"permissions":{},"scope":"turn","strict_auto_review":false}',
            },
        ]
        signals = qz_native_tool_output.classify_native_tool_output_signals(items)
        assert len(signals) == 1
        assert signals[0].event_type == "request_permissions_outcome"
        assert signals[0].payload["classifier"] == "request_permissions_denied_or_unavailable"
        print("permission classifier ok")
        """,
        PROXY_DIR,
    )
    assert result.returncode == 0, result.stderr
    assert "permission classifier ok" in result.stdout


def test_stream_runtime_still_does_not_emit_custom_tool_call_input_done():
    stream_source = (PROXY_DIR / "qz_responses_stream.py").read_text(encoding="utf-8")
    assert "response.custom_tool_call_input.done" not in stream_source
    assert "custom_tool_call_input.done" not in stream_source
