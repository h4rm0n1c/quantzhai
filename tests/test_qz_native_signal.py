import unittest
import unittest.mock
import json
import os
from proxy.qz_native_signal import (
    NativeToolAdvisoryState,
    _parse_env_int,
    command_signature,
    arg_signature,
    seed_native_advisory_state,
    record_native_tool_call,
    check_native_advisories,
    QZ_NATIVE_FAIL_REPEAT_THRESHOLD,
    QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD,
    QZ_NATIVE_MAX_CALLS_PER_TURN
)

class NativeToolAdvisoryTests(unittest.TestCase):
    def test_command_signature_stable(self):
        # Same tool + same args with different key order -> same signature
        call1 = {"name": "exec_command", "arguments": {"cmd": "ls", "cwd": "/tmp"}}
        call2 = {"name": "exec_command", "arguments": {"cwd": "/tmp", "cmd": "ls"}}
        self.assertEqual(command_signature(call1), command_signature(call2))
        self.assertEqual(len(command_signature(call1)), 16)

    def test_arg_signature_stable(self):
        call1 = {"name": "exec_command", "arguments": {"cmd": "ls", "shell": True}}
        call2 = {"name": "exec_command", "arguments": {"shell": True, "cmd": "ls"}}
        self.assertEqual(arg_signature(call1), arg_signature(call2))
        self.assertEqual(len(arg_signature(call1)[1]), 12)

    def test_seed_failure_counts(self):
        history = [
            {"type": "function_call", "name": "exec_command", "arguments": {"cmd": "fail"}, "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": "Process exited with code 1\nOutput: err"},
            {"type": "function_call", "name": "exec_command", "arguments": {"cmd": "fail"}, "call_id": "c2"},
            {"type": "function_call_output", "call_id": "c2", "output": "Process exited with code 1\nOutput: err"},
        ]
        state = seed_native_advisory_state(history)
        sig = command_signature(history[0])
        self.assertEqual(state.command_failure_counts.get(sig), 2)

    def test_failure_reset_on_success(self):
        history = [
            {"type": "function_call", "name": "exec_command", "arguments": {"cmd": "fail"}, "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": "Process exited with code 1\nOutput: err"},
            {"type": "function_call", "name": "exec_command", "arguments": {"cmd": "fail"}, "call_id": "c2"},
            {"type": "function_call_output", "call_id": "c2", "output": "Process exited with code 0\nOutput: ok"},
        ]
        state = seed_native_advisory_state(history)
        sig = command_signature(history[0])
        self.assertEqual(state.command_failure_counts.get(sig), 0)

    def test_repeated_failing_command_advisory(self):
        state = NativeToolAdvisoryState()
        sig = "sig1"
        state.command_failure_counts[sig] = QZ_NATIVE_FAIL_REPEAT_THRESHOLD
        
        call = {"name": "exec_command", "arguments": {"cmd": "fail"}}
        # We need to make sure the sig matches
        with unittest.mock.patch("proxy.qz_native_signal.command_signature", return_value=sig):
            decision = check_native_advisories(call, state)
            self.assertIsNotNone(decision)
            self.assertTrue(decision.should_signal)
            self.assertEqual(decision.metadata["advisory_reason"], "repeated_failing_command")

    def test_excessive_call_count_advisory(self):
        state = NativeToolAdvisoryState()
        state.native_call_count = QZ_NATIVE_MAX_CALLS_PER_TURN
        
        call = {"name": "exec_command", "arguments": {"cmd": "ls"}}
        decision = check_native_advisories(call, state)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.metadata["advisory_reason"], "excessive_call_count")

    def test_repeated_same_tool_args_advisory(self):
        state = NativeToolAdvisoryState()
        tool_sig = ("exec_command", "arg_sig1")
        state.tool_arg_call_counts[tool_sig] = QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD
        
        call = {"name": "exec_command", "arguments": {"cmd": "ls"}}
        with unittest.mock.patch("proxy.qz_native_signal.arg_signature", return_value=tool_sig):
            decision = check_native_advisories(call, state)
            self.assertIsNotNone(decision)
            self.assertEqual(decision.metadata["advisory_reason"], "repeated_same_tool_args")

    def test_non_native_tools_ignored(self):
        state = NativeToolAdvisoryState()
        state.native_call_count = 100
        call = {"name": "apply_patch", "arguments": {"path": "a.txt"}}
        decision = check_native_advisories(call, state)
        self.assertIsNone(decision)

    def test_dedup_guard(self):
        state = NativeToolAdvisoryState()
        state.native_call_count = QZ_NATIVE_MAX_CALLS_PER_TURN

        call = {"name": "exec_command", "arguments": {"cmd": "ls"}}
        decision1 = check_native_advisories(call, state)
        self.assertIsNotNone(decision1)

        decision2 = check_native_advisories(call, state)
        self.assertIsNone(decision2)

    # ------------------------------------------------------------------
    # Slice B.1 hardening tests
    # ------------------------------------------------------------------

    # 1. JSON-string args canonicalize equivalently
    def test_arg_signature_json_string_canonicalize(self):
        """JSON-string args produce same arg_signature as equivalent dict args."""
        call_dict = {"name": "exec_command", "arguments": {"cmd": "ls", "shell": True}}
        call_str  = {"name": "exec_command", "arguments": '{"shell":true,"cmd":"ls"}'}
        self.assertEqual(arg_signature(call_dict), arg_signature(call_str))

    # 2a. repeated-failure signature includes full args — same sig for same args
    def test_command_signature_same_tool_same_args(self):
        """Same tool + same full args (different key order) -> same command_signature."""
        call1 = {"name": "exec_command", "arguments": {"cmd": "ls", "cwd": "/tmp"}}
        call2 = {"name": "exec_command", "arguments": {"cwd": "/tmp", "cmd": "ls"}}
        self.assertEqual(command_signature(call1), command_signature(call2))

    # 2b. repeated-failure signature includes full args — different cwd -> different sig
    def test_command_signature_different_cwd_differs(self):
        """Same cmd but different cwd produces a different command_signature."""
        call1 = {"name": "exec_command", "arguments": {"cmd": "ls", "cwd": "/foo"}}
        call2 = {"name": "exec_command", "arguments": {"cmd": "ls", "cwd": "/bar"}}
        self.assertNotEqual(command_signature(call1), command_signature(call2))

    # 3. repeated-failure signature includes tool name
    def test_command_signature_includes_tool_name(self):
        """exec_command and shell_command with equivalent args must NOT collide."""
        call1 = {"name": "exec_command",  "arguments": {"cmd": "ls"}}
        call2 = {"name": "shell_command", "arguments": {"cmd": "ls"}}
        self.assertNotEqual(command_signature(call1), command_signature(call2))

    # 4. repeated same tool+args ignores JSON key order
    def test_arg_signature_json_key_order_triggers_advisory(self):
        """Repeated-args advisory fires even if prior call used different JSON key order."""
        state = NativeToolAdvisoryState()
        # Seed state using dict-form args
        prior_sig = arg_signature({"name": "exec_command", "arguments": {"cmd": "ls", "shell": True}})
        state.tool_arg_call_counts[prior_sig] = QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD

        # Incoming call uses JSON-string args with different key order
        call = {"name": "exec_command", "arguments": '{"shell":true,"cmd":"ls"}'}
        decision = check_native_advisories(call, state)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.metadata["advisory_reason"], "repeated_same_tool_args")

    # 5. excessive call count warns once per turn across different tools
    def test_excessive_call_warns_once_per_turn(self):
        """After threshold, second call from a *different* tool must NOT re-fire."""
        state = NativeToolAdvisoryState()
        state.native_call_count = QZ_NATIVE_MAX_CALLS_PER_TURN

        call_exec  = {"name": "exec_command",  "arguments": {"cmd": "ls"}}
        call_shell = {"name": "shell_command", "arguments": {"cmd": "pwd"}}

        decision1 = check_native_advisories(call_exec, state)
        self.assertIsNotNone(decision1)
        self.assertEqual(decision1.metadata["advisory_reason"], "excessive_call_count")

        decision2 = check_native_advisories(call_shell, state)
        # Must not produce a second excessive_call_count advisory
        if decision2 is not None:
            self.assertNotEqual(decision2.metadata.get("advisory_reason"), "excessive_call_count")

    # 6. env parsing is safe
    def test_safe_env_parsing(self):
        """_parse_env_int returns defaults for invalid/empty/negative/zero values."""
        # Missing key -> default
        key = "QZ_PARSE_ENV_TEST_KEY_ZZXYZ"
        os.environ.pop(key, None)
        self.assertEqual(_parse_env_int(key, 42), 42)

        # Invalid string -> default
        with unittest.mock.patch.dict(os.environ, {key: "bad"}):
            self.assertEqual(_parse_env_int(key, 10), 10)

        # Empty string -> default
        with unittest.mock.patch.dict(os.environ, {key: ""}):
            self.assertEqual(_parse_env_int(key, 10), 10)

        # Whitespace only -> default
        with unittest.mock.patch.dict(os.environ, {key: "   "}):
            self.assertEqual(_parse_env_int(key, 10), 10)

        # Zero -> default
        with unittest.mock.patch.dict(os.environ, {key: "0"}):
            self.assertEqual(_parse_env_int(key, 10), 10)

        # Negative -> default
        with unittest.mock.patch.dict(os.environ, {key: "-3"}):
            self.assertEqual(_parse_env_int(key, 10), 10)

        # Valid positive -> override
        with unittest.mock.patch.dict(os.environ, {key: "7"}):
            self.assertEqual(_parse_env_int(key, 10), 7)

        # Whitespace-padded valid integer -> accepted
        with unittest.mock.patch.dict(os.environ, {key: "  15  "}):
            self.assertEqual(_parse_env_int(key, 10), 15)

    # 7. telemetry payload safety
    def test_telemetry_payload_no_raw_secrets(self):
        """Advisory metadata must not contain raw command/path/arg text."""
        secret_cmd  = "echo SUPER_SECRET_PASS_XYZ"
        secret_cwd  = "/etc/very/secret/path"
        secret_val  = "SECRETVALUE_XYZ"

        state = NativeToolAdvisoryState()
        state.native_call_count = QZ_NATIVE_MAX_CALLS_PER_TURN

        call = {
            "name": "exec_command",
            "arguments": {
                "cmd": secret_cmd,
                "cwd": secret_cwd,
                "env": {"SECRET_KEY": secret_val},
            },
        }
        decision = check_native_advisories(call, state)
        self.assertIsNotNone(decision)

        meta_str = json.dumps(decision.metadata)
        for secret in (secret_cmd, secret_cwd, secret_val):
            self.assertNotIn(secret, meta_str,
                f"Raw secret text found in advisory metadata: {secret!r}")

        allowed_keys = {"tool_name", "advisory_reason", "count", "threshold", "signature_hash"}
        extra = set(decision.metadata.keys()) - allowed_keys
        self.assertFalse(extra, f"Unexpected keys in advisory metadata: {extra}")


if __name__ == "__main__":
    unittest.main()
