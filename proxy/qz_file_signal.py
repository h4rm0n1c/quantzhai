import json
import os
import re
import shlex
from dataclasses import dataclass, field

@dataclass
class RepeatedReadState:
    read_paths: set[str] = field(default_factory=set)
    written_paths: set[str] = field(default_factory=set)
    warned_paths: set[str] = field(default_factory=set)
    # Track which paths came from input history vs current run
    history_read_paths: set[str] = field(default_factory=set)

@dataclass(frozen=True)
class RepeatedReadDecision:
    should_signal: bool
    message: str = ""
    paths: frozenset[str] = frozenset()
    action: str = ""  # "signalled", "allowed_after_prior_signal", "none"
    scope: str = ""   # "input_history", "current_run", "mixed"

READ_COMMANDS = {
    'cat', 'head', 'tail', 'sed', 'nl', 'bat', 'xxd',
    'grep', 'rg', 'awk', 'wc'
}

def normalize_path(path: str) -> str:
    if not path:
        return ""
    # Use os.path.normpath() only. Do not touch filesystem or resolve symlinks.
    # Also handle some basic shell-isms if they sneak in, but normpath is the core.
    return os.path.normpath(path)

def extract_command(call: dict) -> str:
    """Extracts the command string from a function_call dictionary."""
    if not isinstance(call, dict):
        return ""
    
    name = call.get("name")
    args = call.get("arguments")
    
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return ""
    
    # exec_command is the primary target for v1.
    # Real exec_command uses the "cmd" field; "command" is accepted as a fallback.
    if name == "exec_command":
        if isinstance(args, dict):
            return args.get("cmd", "") or args.get("command", "")

    return ""

def extract_read_paths(call: dict) -> frozenset[str]:
    """Extracts paths from read-like commands in a tool call."""
    command = extract_command(call)
    if not command:
        return frozenset()

    # Split command by common separators for multiple commands in one string
    # e.g., "cat a.txt && cat b.txt" or "cat a.txt ; cat b.txt"
    # Use a conservative regex that won't split inside quotes if possible, 
    # but for v1 simple is safer.
    segments = re.split(r'&&|\|\||[;&|]', command)
    
    paths = set()
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
            
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            # shlex failed (e.g. unclosed quote). Skip segment to be conservative.
            continue

        if not tokens:
            continue

        cmd = tokens[0]
        # Handle sudo/time prefix
        if cmd in ('sudo', 'time') and len(tokens) > 1:
            tokens = tokens[1:]
            cmd = tokens[0]

        if cmd not in READ_COMMANDS:
            continue

        # Basic path extraction for known commands
        if cmd == 'cat':
            for token in tokens[1:]:
                if not token.startswith('-'):
                    paths.add(normalize_path(token))
        
        elif cmd in ('head', 'tail'):
            i = 1
            while i < len(tokens):
                token = tokens[i]
                if token.startswith('-'):
                    # Skip common options that take an argument
                    if token in ('-n', '-c', '--lines', '--bytes', '-q', '--quiet', '-v', '--verbose'):
                        if i + 1 < len(tokens) and not tokens[i+1].startswith('-'):
                            i += 1
                else:
                    paths.add(normalize_path(token))
                i += 1
        
        elif cmd in ('grep', 'rg'):
            # grep/rg [OPTIONS] PATTERN [PATH]...
            found_pattern = False
            i = 1
            while i < len(tokens):
                token = tokens[i]
                if token.startswith('-'):
                    # Many options take arguments. This is complex.
                    # rg -e PATTERN, grep -e PATTERN, rg -f FILE, etc.
                    if token in ('-e', '--regexp'):
                        found_pattern = True
                        i += 1
                    elif token in ('-f', '--file', '-t', '--type', '-T', '--type-not', '-g', '--glob'):
                        i += 1
                elif not found_pattern:
                    found_pattern = True
                else:
                    paths.add(normalize_path(token))
                i += 1

        elif cmd == 'sed':
            found_script = False
            i = 1
            while i < len(tokens):
                token = tokens[i]
                if token.startswith('-'):
                    if token in ('-e', '--expression', '-f', '--file'):
                        i += 1
                elif not found_script:
                    found_script = True
                else:
                    paths.add(normalize_path(token))
                i += 1

        elif cmd in ('wc', 'nl', 'bat', 'xxd', 'awk'):
            found_first_non_opt = False
            for token in tokens[1:]:
                if token.startswith('-'):
                    # Some options take args, but many don't.
                    continue
                if not found_first_non_opt:
                    found_first_non_opt = True
                    if cmd == 'awk':
                        # First non-option for awk is the script
                        continue
                    # For others, first non-option is usually a file
                    paths.add(normalize_path(token))
                else:
                    paths.add(normalize_path(token))

    return frozenset(p for p in paths if p and p != '.')

def extract_write_paths(call: dict) -> frozenset[str]:
    """Extracts paths that are written to in a tool call."""
    if not isinstance(call, dict):
        return frozenset()

    name = call.get("name")
    args = call.get("arguments")
    
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return frozenset()
            
    if not isinstance(args, dict):
        return frozenset()

    paths = set()
    
    if name == "apply_patch":
        path = args.get("path")
        if path:
            paths.add(normalize_path(path))
            
        to_path = args.get("to_path")
        if to_path:
            paths.add(normalize_path(to_path))

    return frozenset(p for p in paths if p and p != '.')

def record_tool_call(call: dict, state: RepeatedReadState) -> None:
    """Records a tool call in the state."""
    read_paths = extract_read_paths(call)
    for p in read_paths:
        state.read_paths.add(p)
        
    write_paths = extract_write_paths(call)
    for p in write_paths:
        state.written_paths.add(p)

def record_tool_output(item: dict, state: RepeatedReadState) -> None:
    """Records a tool output in the state."""
    pass

def seed_repeated_read_state(input_items: list) -> RepeatedReadState:
    """Seeds the repeated-read state from input history."""
    state = RepeatedReadState()
    for item in input_items:
        if not isinstance(item, dict):
            continue
            
        item_type = item.get("type")
        if item_type == "function_call":
            record_tool_call(item, state)
    
    # After seeding from history, copy read_paths to history_read_paths
    state.history_read_paths.update(state.read_paths)
            
    return state

def repeated_read_signal(call: dict, state: RepeatedReadState) -> RepeatedReadDecision:
    """Checks if a tool call should trigger a repeated-read signal."""
    read_paths = extract_read_paths(call)
    if not read_paths:
        return RepeatedReadDecision(should_signal=False, action="none")
        
    repeated = []
    has_history = False
    has_current = False
    
    for p in read_paths:
        if p in state.read_paths and p not in state.written_paths:
            if p not in state.warned_paths:
                repeated.append(p)
                if p in state.history_read_paths:
                    has_history = True
                else:
                    has_current = True
    
    if not repeated:
        # Check if we already warned about these paths
        any_repeated_and_warned = any(
            p in state.read_paths and p not in state.written_paths and p in state.warned_paths 
            for p in read_paths
        )
        
        if any_repeated_and_warned:
             return RepeatedReadDecision(
                should_signal=False,
                action="allowed_after_prior_signal"
            )
        
        return RepeatedReadDecision(should_signal=False, action="none")

    # Scope determination
    if has_history and has_current:
        scope = "mixed"
    elif has_history:
        scope = "input_history"
    else:
        scope = "current_run"

    paths_frozenset = frozenset(repeated)
    
    if len(repeated) == 1:
        msg = f"Note: you already read {repeated[0]} earlier in this conversation. Use the existing context unless you believe the file changed."
    else:
        msg = f"Note: you already read these files earlier in this conversation: {', '.join(sorted(repeated))}. Use the existing context unless you believe they changed."
        
    return RepeatedReadDecision(
        should_signal=True,
        message=msg,
        paths=paths_frozenset,
        action="signalled",
        scope=scope
    )

