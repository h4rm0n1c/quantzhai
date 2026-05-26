import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

@dataclass
class NativeToolAdvisoryState:
    # Per-turn call counts
    native_call_count: int = 0          # total native tool calls this turn

    # Failure tracking
    # Key: command_signature hash, Value: consecutive failure count
    command_failure_counts: dict[str, int] = field(default_factory=dict)

    # Repeated-args tracking
    # Key: (tool_name, arg_hash), Value: call count
    tool_arg_call_counts: dict[tuple[str, str], int] = field(default_factory=dict)

    # Dedup guard — warn once per (namespace, advisory_kind, signature) per turn.
    # Excessive-call uses namespace "__native_total__" so it fires once per turn
    # regardless of which tool crosses the threshold.
    warned_signatures: set[tuple[str, str, str]] = field(default_factory=set)

@dataclass(frozen=True)
class NativeAdvisoryDecision:
    should_signal: bool
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_env_int(name: str, default: int) -> int:
    """Safely parse an integer from an environment variable.

    Returns *default* on missing key, empty string, non-integer value, zero, or
    negative value.  Whitespace around a valid integer is accepted.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return default
    if n <= 0:
        return default
    return n


# Default thresholds — overridable via QZ_NATIVE_* env vars.
# _parse_env_int guards against crash-on-import from bad env values.
QZ_NATIVE_FAIL_REPEAT_THRESHOLD = _parse_env_int("QZ_NATIVE_FAIL_REPEAT_THRESHOLD", 3)
QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD = _parse_env_int("QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD", 4)
QZ_NATIVE_MAX_CALLS_PER_TURN = _parse_env_int("QZ_NATIVE_MAX_CALLS_PER_TURN", 24)


def _canonicalize_args(args: Any) -> str:
    """Return a canonical JSON string for *args* suitable for stable hashing.

    - dict  → json.dumps with sort_keys and compact separators.
    - JSON string → parse, then canonicalize the parsed value.
    - Anything else → stable string representation.
    - Parse failure → fall back to the raw string.

    Never returns raw command/path/secret content — callers must hash the
    result before storing or emitting it.
    """
    if args is None:
        return ""
    if isinstance(args, dict):
        return json.dumps(args, sort_keys=True, separators=(",", ":"))
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return args  # unparseable — use as-is
        if isinstance(parsed, dict):
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        if isinstance(parsed, list):
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        # scalar (int, bool, null…)
        return json.dumps(parsed, separators=(",", ":"))
    return str(args)


def command_signature(call: dict) -> str:
    """Safe signature for repeated-failure detection.

    Hashes tool name + the full canonicalized arguments object so that:
    - Different cwd/env/sandbox args produce a different signature.
    - Same arguments in different key order produce the same signature.
    - exec_command and shell_command with equivalent args do NOT collide.

    Does NOT store or return raw args/paths/commands.  Returns a stable
    16-char hex hash.
    """
    tool_name = call.get("name") or "unknown"
    canonical = _canonicalize_args(call.get("arguments"))
    payload = f"{tool_name}\x00{canonical}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def arg_signature(call: dict) -> Tuple[str, str]:
    """Safe (tool_name, arg_hash) signature for repeated-args detection.

    JSON-string args are parsed and canonicalized before hashing so that
    {"cmd":"ls","shell":true} and {"shell":true,"cmd":"ls"} produce the
    same signature whether passed as a dict or as a JSON string.

    Returns (tool_name, 12-char hex hash).  The hash alone is safe to store
    in telemetry; raw args are never stored.
    """
    tool_name = call.get("name") or "unknown"
    canonical = _canonicalize_args(call.get("arguments"))
    arg_hash = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return (tool_name, arg_hash)

def seed_native_advisory_state(input_items: list) -> NativeToolAdvisoryState:
    """Seeds the native advisory state from input history."""
    state = NativeToolAdvisoryState()
    if not isinstance(input_items, list):
        return state

    # Map call_id to signature for exec commands in history
    call_id_to_signature: Dict[str, str] = {}
    
    # First pass: find all function_calls in history to know their signatures
    # and count turn-wide usage.
    from .qz_tools import CODEX_NATIVE_TOOL_NAMES
    
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        
        name = item.get("name")
        if name in CODEX_NATIVE_TOOL_NAMES:
            state.native_call_count += 1
            tool_sig = arg_signature(item)
            state.tool_arg_call_counts[tool_sig] = state.tool_arg_call_counts.get(tool_sig, 0) + 1
            
            if name in ("exec_command", "shell_command", "shell", "container.exec"):
                call_id = item.get("call_id") or item.get("id")
                if call_id:
                    call_id_to_signature[str(call_id)] = command_signature(item)

    # Second pass: check outputs for failures
    from .qz_native_tool_output import _parse_exit_code
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
            
        call_id = str(item.get("call_id") or "")
        sig = call_id_to_signature.get(call_id)
        if not sig:
            continue
            
        output = item.get("output")
        if isinstance(output, str):
            exit_code = _parse_exit_code(output)
            if exit_code is not None and exit_code != 0:
                state.command_failure_counts[sig] = state.command_failure_counts.get(sig, 0) + 1
            elif exit_code == 0:
                # Reset failure count on success
                state.command_failure_counts[sig] = 0

    return state

def record_native_tool_call(call: dict, state: NativeToolAdvisoryState) -> None:
    """Records a native tool call in the state during a run."""
    from .qz_tools import CODEX_NATIVE_TOOL_NAMES
    
    name = call.get("name")
    if name in CODEX_NATIVE_TOOL_NAMES:
        state.native_call_count += 1
        tool_sig = arg_signature(call)
        state.tool_arg_call_counts[tool_sig] = state.tool_arg_call_counts.get(tool_sig, 0) + 1

def check_native_advisories(call: dict, state: NativeToolAdvisoryState) -> Optional[NativeAdvisoryDecision]:
    """Checks if a native tool call should trigger an advisory."""
    from .qz_tools import CODEX_NATIVE_TOOL_NAMES
    
    name = call.get("name")
    if name not in CODEX_NATIVE_TOOL_NAMES:
        return None

    # 1. Excessive native call count — warn once per turn regardless of which
    #    tool triggered the check.  The dedup key is turn-scoped ("__native_total__")
    #    not tool-scoped, so only the first over-threshold call produces an advisory.
    if state.native_call_count >= QZ_NATIVE_MAX_CALLS_PER_TURN:
        kind = "excessive_call_count"
        if ("__native_total__", kind, "total") not in state.warned_signatures:
            state.warned_signatures.add(("__native_total__", kind, "total"))
            return NativeAdvisoryDecision(
                should_signal=True,
                message="Native tool use is high in this turn. Summarize current findings and choose the next smallest diagnostic before continuing.",
                metadata={
                    "tool_name": name,
                    "advisory_reason": kind,
                    "count": state.native_call_count,
                    "threshold": QZ_NATIVE_MAX_CALLS_PER_TURN,
                }
            )

    # 2. Repeated failing native command
    if name in ("exec_command", "shell_command", "shell", "container.exec"):
        sig = command_signature(call)
        fail_count = state.command_failure_counts.get(sig, 0)
        if fail_count >= QZ_NATIVE_FAIL_REPEAT_THRESHOLD:
            kind = "repeated_failing_command"
            if (name, kind, sig) not in state.warned_signatures:
                state.warned_signatures.add((name, kind, sig))
                return NativeAdvisoryDecision(
                    should_signal=True,
                    message="This native command has failed repeatedly in this turn. Inspect the previous error and change approach before retrying.",
                    metadata={
                        "tool_name": name,
                        "advisory_reason": kind,
                        "count": fail_count,
                        "threshold": QZ_NATIVE_FAIL_REPEAT_THRESHOLD,
                        "signature_hash": sig,
                    }
                )

    # 3. Repeated same native tool + same args
    tool_sig = arg_signature(call)
    # Note: state.tool_arg_call_counts was updated by record_native_tool_call or seeding
    call_count = state.tool_arg_call_counts.get(tool_sig, 0)
    if call_count >= QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD:
        kind = "repeated_same_tool_args"
        sig_hash = tool_sig[1]
        if (name, kind, sig_hash) not in state.warned_signatures:
            state.warned_signatures.add((name, kind, sig_hash))
            return NativeAdvisoryDecision(
                should_signal=True,
                message="This native tool call has repeated with the same arguments. Reuse the prior result or explain what changed before repeating it.",
                metadata={
                    "tool_name": name,
                    "advisory_reason": kind,
                    "count": call_count,
                    "threshold": QZ_NATIVE_REPEAT_SIGNATURE_THRESHOLD,
                    "signature_hash": sig_hash,
                }
            )

    return None
