import unittest
import json
from proxy.qz_native_signal import (
    NativeToolAdvisoryState,
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
        call1 = {"name": "exec_command", "arguments": {"cmd": "ls -l"}}
        call2 = {"name": "exec_command", "arguments": {"cmd": "  ls   -l  "}}
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

if __name__ == "__main__":
    unittest.main()
