import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proxy.qz_file_signal import (
    RepeatedReadDecision,
    RepeatedReadState,
    extract_read_paths,
    extract_write_paths,
    normalize_path,
    record_tool_call,
    repeated_read_signal,
    seed_repeated_read_state,
)


def _exec(cmd):
    """Helper: build an exec_command function_call with real 'cmd' field."""
    return {"name": "exec_command", "type": "function_call", "call_id": "c", "arguments": {"cmd": cmd}}


def _exec_legacy(cmd):
    """Helper: build exec_command with legacy 'command' field (compat test)."""
    return {"name": "exec_command", "type": "function_call", "call_id": "c", "arguments": {"command": cmd}}


class NormalizePathTests(unittest.TestCase):
    def test_dot_prefix(self):
        self.assertEqual(normalize_path("./foo/bar"), "foo/bar")

    def test_dotdot(self):
        self.assertEqual(normalize_path("a/../b"), "b")

    def test_double_slash(self):
        self.assertEqual(normalize_path("foo//bar"), "foo/bar")

    def test_empty_string(self):
        self.assertEqual(normalize_path(""), "")

    def test_none(self):
        self.assertEqual(normalize_path(None), "")


class ExtractReadPathsTests(unittest.TestCase):
    def test_cat_single(self):
        self.assertEqual(extract_read_paths(_exec("cat README.md")), frozenset(["README.md"]))

    def test_cat_multiple(self):
        self.assertEqual(extract_read_paths(_exec("cat a.txt b.txt")), frozenset(["a.txt", "b.txt"]))

    def test_cat_with_flag(self):
        self.assertEqual(extract_read_paths(_exec("cat -n README.md")), frozenset(["README.md"]))

    def test_head_with_option(self):
        self.assertEqual(extract_read_paths(_exec("head -n 40 README.md")), frozenset(["README.md"]))

    def test_tail_multiple(self):
        self.assertIn("a.log", extract_read_paths(_exec("tail -c +10 a.log b.log")))

    def test_grep_path(self):
        self.assertEqual(extract_read_paths(_exec("grep pattern README.md")), frozenset(["README.md"]))

    def test_rg_path(self):
        self.assertIn("src", extract_read_paths(_exec("rg -i pattern src/")))

    def test_rg_with_type(self):
        self.assertIn("src", extract_read_paths(_exec("rg -e pattern -t py src/")))

    def test_sed_read(self):
        self.assertEqual(extract_read_paths(_exec("sed -n '1,80p' README.md")), frozenset(["README.md"]))

    def test_sed_multiple(self):
        result = extract_read_paths(_exec("sed 's/a/b/' file1 file2"))
        self.assertIn("file1", result)
        self.assertIn("file2", result)

    def test_compound_command(self):
        result = extract_read_paths(_exec("cat a.txt && rg pat b.txt ; head c.txt"))
        self.assertIn("a.txt", result)
        self.assertIn("b.txt", result)
        self.assertIn("c.txt", result)

    def test_sudo_prefix(self):
        self.assertEqual(extract_read_paths(_exec("sudo cat /etc/shadow")), frozenset(["/etc/shadow"]))

    def test_skip_ls(self):
        self.assertEqual(extract_read_paths(_exec("ls -la")), frozenset())

    def test_skip_find(self):
        self.assertEqual(extract_read_paths(_exec("find . -type f")), frozenset())

    def test_invalid_arguments_string_not_dict(self):
        call = {"name": "exec_command", "arguments": "cat README.md"}
        self.assertEqual(extract_read_paths(call), frozenset())

    def test_unclosed_quote_safe(self):
        self.assertEqual(extract_read_paths(_exec("cat 'unclosed quote")), frozenset())

    def test_json_string_arguments_cmd_field(self):
        """Real exec_command sends arguments as JSON string with 'cmd' field."""
        import json
        call = {"name": "exec_command", "arguments": json.dumps({"cmd": "cat README.md"})}
        self.assertEqual(extract_read_paths(call), frozenset(["README.md"]))

    def test_json_string_arguments_legacy_command_field(self):
        """Legacy 'command' field still works."""
        import json
        call = {"name": "exec_command", "arguments": json.dumps({"command": "cat README.md"})}
        self.assertEqual(extract_read_paths(call), frozenset(["README.md"]))

    def test_malformed_json_string(self):
        call = {"name": "exec_command", "arguments": '{"cmd":"cat README.md"'}
        self.assertEqual(extract_read_paths(call), frozenset())

    def test_legacy_command_field_fallback(self):
        """compat: command field (used in earlier tests) still works."""
        self.assertEqual(extract_read_paths(_exec_legacy("cat README.md")), frozenset(["README.md"]))


class ExtractWritePathsTests(unittest.TestCase):
    def test_apply_patch_path(self):
        call = {"name": "apply_patch", "arguments": {"path": "src/main.py", "patch": "...", "operation": "update"}}
        self.assertEqual(extract_write_paths(call), frozenset(["src/main.py"]))

    def test_apply_patch_move(self):
        call = {"name": "apply_patch", "arguments": {"path": "old.py", "to_path": "new.py", "operation": "move"}}
        self.assertIn("old.py", extract_write_paths(call))
        self.assertIn("new.py", extract_write_paths(call))

    def test_read_command_no_write(self):
        self.assertEqual(extract_write_paths(_exec("cat README.md")), frozenset())

    def test_invalid_args(self):
        call = {"name": "apply_patch", "arguments": "bad"}
        self.assertEqual(extract_write_paths(call), frozenset())

    def test_malformed_json_string(self):
        call = {"name": "apply_patch", "arguments": '{"path": "src/main.py"'}
        self.assertEqual(extract_write_paths(call), frozenset())

    def test_json_string_arguments(self):
        import json
        call = {"name": "apply_patch", "arguments": json.dumps({"path": "src/main.py", "operation": "update"})}
        self.assertEqual(extract_write_paths(call), frozenset(["src/main.py"]))


class SeedRepeatedReadStateTests(unittest.TestCase):
    def test_seeds_from_function_call(self):
        history = [
            {"type": "message", "content": "hello"},
            _exec("cat README.md"),
            {"type": "function_call_output", "output": "..."},
        ]
        state = seed_repeated_read_state(history)
        self.assertIn("README.md", state.read_paths)
        self.assertIn("README.md", state.history_read_paths)
        self.assertEqual(state.written_paths, set())

    def test_skips_message_blobs(self):
        history = [{"type": "message", "content": "I just ran cat README.md"}]
        state = seed_repeated_read_state(history)
        self.assertNotIn("README.md", state.read_paths)

    def test_seeds_write_from_apply_patch(self):
        history = [
            {"type": "function_call", "name": "apply_patch",
             "arguments": {"path": "README.md", "operation": "update"}},
        ]
        state = seed_repeated_read_state(history)
        self.assertIn("README.md", state.written_paths)

    def test_compacted_items_ignored_safely(self):
        history = [
            {"type": "localcmp", "summary": "Read some files"},
            _exec("cat README.md"),
        ]
        state = seed_repeated_read_state(history)
        self.assertIn("README.md", state.read_paths)

    def test_empty_input_degrades_gracefully(self):
        state = seed_repeated_read_state([])
        self.assertEqual(state.read_paths, set())
        self.assertEqual(state.history_read_paths, set())
        decision = repeated_read_signal(_exec("cat README.md"), state)
        self.assertFalse(decision.should_signal)

    def test_non_dict_items_ignored(self):
        state = seed_repeated_read_state([None, "string", 42])
        self.assertEqual(state.read_paths, set())


class RepeatedReadSignalTests(unittest.TestCase):
    def test_first_read_no_signal(self):
        state = RepeatedReadState()
        decision = repeated_read_signal(_exec("cat README.md"), state)
        self.assertFalse(decision.should_signal)
        self.assertEqual(decision.action, "none")

    def test_repeat_from_history_signals(self):
        history = [_exec("cat README.md")]
        state = seed_repeated_read_state(history)
        decision = repeated_read_signal(_exec("cat README.md"), state)
        self.assertTrue(decision.should_signal)
        self.assertIn("README.md", decision.paths)
        self.assertEqual(decision.scope, "input_history")
        self.assertIn("already read README.md", decision.message)

    def test_repeat_from_current_run_signals(self):
        state = RepeatedReadState()
        record_tool_call(_exec("cat a.txt"), state)
        decision = repeated_read_signal(_exec("cat a.txt"), state)
        self.assertTrue(decision.should_signal)
        self.assertEqual(decision.scope, "current_run")

    def test_repeat_after_write_no_signal(self):
        history = [_exec("cat README.md")]
        state = seed_repeated_read_state(history)
        record_tool_call({"name": "apply_patch", "arguments": {"path": "README.md", "operation": "update"}}, state)
        decision = repeated_read_signal(_exec("cat README.md"), state)
        self.assertFalse(decision.should_signal)

    def test_warn_once_then_allow(self):
        state = RepeatedReadState()
        state.read_paths.add("a.txt")
        call = _exec("cat a.txt")
        decision1 = repeated_read_signal(call, state)
        self.assertTrue(decision1.should_signal)
        state.warned_paths.add("a.txt")
        decision2 = repeated_read_signal(call, state)
        self.assertFalse(decision2.should_signal)
        self.assertEqual(decision2.action, "allowed_after_prior_signal")

    def test_mixed_scope(self):
        state = RepeatedReadState()
        state.read_paths.add("history.txt")
        state.history_read_paths.add("history.txt")
        state.read_paths.add("current.txt")
        call = _exec("cat history.txt current.txt")
        decision = repeated_read_signal(call, state)
        self.assertTrue(decision.should_signal)
        self.assertEqual(decision.scope, "mixed")

    def test_multiple_paths_message(self):
        state = RepeatedReadState()
        state.read_paths.update(["a.txt", "b.txt"])
        decision = repeated_read_signal(_exec("cat a.txt b.txt"), state)
        self.assertTrue(decision.should_signal)
        self.assertIn("files", decision.message)

    def test_prior_warning_not_seeded_v1_policy(self):
        """v1 policy: prior repeated_read_signal in history does NOT seed warned_paths."""
        history = [
            _exec("cat README.md"),
            # A prior repeated_read_signal advisory output in history
            {"type": "function_call_output", "call_id": "c",
             "output": "Note: you already read README.md earlier in this conversation."},
        ]
        state = seed_repeated_read_state(history)
        # v1: warned_paths is NOT seeded from prior advisory outputs
        self.assertNotIn("README.md", state.warned_paths)
        # So the proxy signals again
        decision = repeated_read_signal(_exec("cat README.md"), state)
        self.assertTrue(decision.should_signal)

    def test_empty_paths_from_unparseable_command(self):
        call = _exec("someunknowncommand README.md")
        state = RepeatedReadState()
        decision = repeated_read_signal(call, state)
        self.assertFalse(decision.should_signal)


if __name__ == "__main__":
    unittest.main()
