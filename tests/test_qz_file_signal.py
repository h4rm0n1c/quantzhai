import pytest
from proxy.qz_file_signal import (
    RepeatedReadState, 
    RepeatedReadDecision, 
    normalize_path, 
    extract_read_paths, 
    extract_write_paths, 
    seed_repeated_read_state, 
    repeated_read_signal, 
    record_tool_call
)

def test_normalize_path():
    assert normalize_path("./foo/bar") == "foo/bar"
    assert normalize_path("a/../b") == "b"
    assert normalize_path("foo//bar") == "foo/bar"
    assert normalize_path("") == ""
    assert normalize_path(None) == ""

def test_extract_read_paths_cat():
    call = {"name": "exec_command", "arguments": {"command": "cat README.md"}}
    assert extract_read_paths(call) == frozenset(["README.md"])
    
    call = {"name": "exec_command", "arguments": {"command": "cat a.txt b.txt"}}
    assert extract_read_paths(call) == frozenset(["a.txt", "b.txt"])
    
    call = {"name": "exec_command", "arguments": {"command": "cat -n README.md"}}
    assert extract_read_paths(call) == frozenset(["README.md"])

def test_extract_read_paths_head_tail():
    call = {"name": "exec_command", "arguments": {"command": "head -n 40 README.md"}}
    assert extract_read_paths(call) == frozenset(["README.md"])
    
    call = {"name": "exec_command", "arguments": {"command": "tail -c +10 a.log b.log"}}
    assert extract_read_paths(call) == frozenset(["a.log", "b.log"])

def test_extract_read_paths_grep_rg():
    call = {"name": "exec_command", "arguments": {"command": "grep pattern README.md"}}
    assert extract_read_paths(call) == frozenset(["README.md"])
    
    call = {"name": "exec_command", "arguments": {"command": "rg -i pattern src/"}}
    assert extract_read_paths(call) == frozenset(["src"])
    
    call = {"name": "exec_command", "arguments": {"command": "rg -e pattern -t py src/"}}
    assert extract_read_paths(call) == frozenset(["src"])

def test_extract_read_paths_sed():
    call = {"name": "exec_command", "arguments": {"command": "sed -n '1,80p' README.md"}}
    assert extract_read_paths(call) == frozenset(["README.md"])
    
    call = {"name": "exec_command", "arguments": {"command": "sed 's/a/b/' file1 file2"}}
    assert extract_read_paths(call) == frozenset(["file1", "file2"])

def test_extract_read_paths_complex():
    call = {"name": "exec_command", "arguments": {"command": "cat a.txt && rg pat b.txt ; head c.txt"}}
    assert extract_read_paths(call) == frozenset(["a.txt", "b.txt", "c.txt"])
    
    # Sudo/time
    call = {"name": "exec_command", "arguments": {"command": "sudo cat /etc/shadow"}}
    assert extract_read_paths(call) == frozenset(["/etc/shadow"])

def test_skip_ls_find():
    call = {"name": "exec_command", "arguments": {"command": "ls -la"}}
    assert extract_read_paths(call) == frozenset()
    
    call = {"name": "exec_command", "arguments": {"command": "find . -type f"}}
    assert extract_read_paths(call) == frozenset()

def test_malformed_args():
    call = {"name": "exec_command", "arguments": "cat README.md"} # string instead of dict
    assert extract_read_paths(call) == frozenset()
    
    call = {"name": "exec_command", "arguments": {"command": "cat 'unclosed quote"}}
    assert extract_read_paths(call) == frozenset()

def test_extract_write_paths():
    call = {
        "name": "apply_patch", 
        "arguments": {"path": "src/main.py", "patch": "...", "operation": "update"}
    }
    assert extract_write_paths(call) == frozenset(["src/main.py"])
    
    call = {
        "name": "apply_patch", 
        "arguments": {"path": "old.py", "to_path": "new.py", "operation": "move"}
    }
    assert extract_write_paths(call) == frozenset(["old.py", "new.py"])

def test_seed_from_history():
    history = [
        {"type": "message", "content": "hello"},
        {"type": "function_call", "name": "exec_command", "arguments": {"command": "cat README.md"}},
        {"type": "function_call_output", "name": "exec_command", "output": "..."}
    ]
    state = seed_repeated_read_state(history)
    assert "README.md" in state.read_paths
    assert "README.md" in state.history_read_paths
    assert state.written_paths == set()

def test_repeated_read_signal_from_history():
    history = [
        {"type": "function_call", "name": "exec_command", "arguments": {"command": "cat README.md"}}
    ]
    state = seed_repeated_read_state(history)
    
    call = {"name": "exec_command", "arguments": {"command": "cat README.md"}}
    decision = repeated_read_signal(call, state)
    
    assert decision.should_signal is True
    assert "README.md" in decision.paths
    assert decision.scope == "input_history"
    assert "already read README.md" in decision.message

def test_repeated_read_signal_from_current():
    state = RepeatedReadState()
    
    # First call
    call1 = {"name": "exec_command", "arguments": {"command": "cat a.txt"}}
    decision1 = repeated_read_signal(call1, state)
    assert decision1.should_signal is False
    record_tool_call(call1, state)
    
    # Second call (repeated)
    call2 = {"name": "exec_command", "arguments": {"command": "cat a.txt"}}
    decision2 = repeated_read_signal(call2, state)
    assert decision2.should_signal is True
    assert decision2.scope == "current_run"

def test_repeat_after_write_no_signal():
    history = [
        {"type": "function_call", "name": "exec_command", "arguments": {"command": "cat README.md"}}
    ]
    state = seed_repeated_read_state(history)
    
    # Write to README.md
    write_call = {
        "name": "apply_patch", 
        "arguments": {"path": "README.md", "patch": "...", "operation": "update"}
    }
    record_tool_call(write_call, state)
    assert "README.md" in state.written_paths
    
    # Read again - should NOT signal
    read_call = {"name": "exec_command", "arguments": {"command": "cat README.md"}}
    decision = repeated_read_signal(read_call, state)
    assert decision.should_signal is False

def test_warn_once_then_allow():
    state = RepeatedReadState()
    state.read_paths.add("a.txt")
    
    call = {"name": "exec_command", "arguments": {"command": "cat a.txt"}}
    
    # First repeat -> Signal
    decision1 = repeated_read_signal(call, state)
    assert decision1.should_signal is True
    
    # Mark as warned
    state.warned_paths.add("a.txt")
    
    # Second repeat -> Allow
    decision2 = repeated_read_signal(call, state)
    assert decision2.should_signal is False
    assert decision2.action == "allowed_after_prior_signal"

def test_mixed_scope():
    state = RepeatedReadState()
    state.read_paths.add("history.txt")
    state.history_read_paths.add("history.txt")
    state.read_paths.add("current.txt") # Only in read_paths, not history_read_paths
    
    call = {"name": "exec_command", "arguments": {"command": "cat history.txt current.txt"}}
    decision = repeated_read_signal(call, state)
    
    assert decision.should_signal is True
    assert decision.scope == "mixed"

def test_message_blobs_ignored():
    history = [
        {"type": "message", "content": "I just ran cat README.md"}
    ]
    state = seed_repeated_read_state(history)
    assert "README.md" not in state.read_paths

def test_seed_from_body_with_compacted_items():
    # Compacted items might have different types or shapes
    history = [
        {"type": "localcmp", "summary": "Read some files"},
        {"type": "function_call", "name": "exec_command", "arguments": {"command": "cat README.md"}}
    ]
    state = seed_repeated_read_state(history)
    assert "README.md" in state.read_paths
    # The 'localcmp' item should be ignored safely

def test_seed_with_minimal_input_degrades_gracefully():
    # If input is empty (e.g. because of previous_response_id)
    state = seed_repeated_read_state([])
    assert state.read_paths == set()
    assert state.history_read_paths == set()
    
    # It shouldn't crash
    call = {"name": "exec_command", "arguments": {"command": "cat README.md"}}
    decision = repeated_read_signal(call, state)
    assert decision.should_signal is False

def test_extract_read_paths_json_string():
    call = {
        "name": "exec_command", 
        "arguments": "{\"command\":\"cat README.md\"}"
    }
    assert extract_read_paths(call) == frozenset(["README.md"])

def test_extract_read_paths_malformed_json_string():
    call = {
        "name": "exec_command", 
        "arguments": "{\"command\":\"cat README.md\"" # missing brace
    }
    assert extract_read_paths(call) == frozenset()

def test_extract_write_paths_json_string():
    call = {
        "name": "apply_patch", 
        "arguments": "{\"path\": \"src/main.py\", \"patch\": \"...\", \"operation\": \"update\"}"
    }
    assert extract_write_paths(call) == frozenset(["src/main.py"])

def test_extract_write_paths_malformed_json_string():
    call = {
        "name": "apply_patch", 
        "arguments": "{\"path\": \"src/main.py\"" # missing brace
    }
    assert extract_write_paths(call) == frozenset()
