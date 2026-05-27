"""
Deterministic survival-weight span scorer for QuantZhai compaction.

This module identifies high-value technical atoms (paths, commands, SHAs, etc.)
and semantic anchors (negations, user corrections, decisions) that must survive
context compaction to maintain agent intelligibility and safety.

It is regex-driven and heuristic, intended as a pre-pass for LLM-based or
rule-based context compression. It does not provide full tokenization or
embedding-based semantic ranking.
"""

from dataclasses import dataclass
import re

@dataclass(frozen=True)
class SurvivalSpan:
    text: str
    weight: str                 # "light" | "medium" | "heavy"
    exactness_risk: str         # "low" | "medium" | "high"
    features: tuple[str, ...]
    start: int | None = None
    end: int | None = None

# Feature classification patterns
# Use a consistent three-group strategy:
# Group 1: Prefix (consumed)
# Group 2: Content (the survival atom)
# Group 3: Suffix (non-consuming lookahead)
PATTERNS = {
    "path": re.compile(r'(^|[\s"\'\(])((?:\.?\.?/[a-zA-Z0-9._\-\[\]]+)+|(?:\w+[/\\])+[a-zA-Z0-9._\-\[\]]+\.\w+)(?=($|[\s"\'\):,;]))'),
    "build_file": re.compile(r'(^|[\s"\'\(])((?:package\.json|pyproject\.toml|Cargo\.toml|Cargo\.lock|go\.mod|go\.sum|CMakeLists\.txt|Makefile))(?=($|[\s"\'\):,;]))'),
    "repo_dir": re.compile(r'(^|[\s"\'\(])((?:src|tests?|docs?|examples|include|cmake|completions|scripts|config)/)(?=($|[\s"\'\):,;]))'),
    "command": re.compile(r'(^|[\s"\'\(])((?:git|python3?|rg|curl|bash|sudo)\b(?:\s+(?!(?:and|or)\b)[^\s"\'\(\)\)\:,;]+)*)(?=($|[\s"\'\(\)\)\:,;]|(?:\s+and\b|\s+or\b)))'),
    "language_command": re.compile(r'(^|[\s"\'\(])((?:npm|pnpm|yarn)\s+(?:test|run\s+test)|go\s+test\s+\./\.\.\.|cargo\s+(?:test|build|run)|cmake\s+--build|ctest|pytest)(?=($|[\s"\'\):,;]))'),
    "flag": re.compile(r'(^|\s)(--[a-z0-9_-]+|-[a-z0-9])(?=($|[\s:,;]))'),
    "env_var": re.compile(r'(^|\s)([A-Z0-9_]{3,}=[^ \s]+|\$[A-Z0-9_]{3,})(?=($|[\s:,;]))'),
    "sha": re.compile(r'(^|[\s"\'\(])([0-9a-f]{7,64})(?=($|[\s"\'\):,;]))'),
    "issue_ref": re.compile(r'(^|\s)(#\d+|issue\s+#\d+|PR\s+#\d+)(?=($|[\s:,;]))', re.IGNORECASE),
    "version": re.compile(r'(^|\s)(v\d+\.\d+\.\d+|localcmp:v\d+:?|Codex\s+v?\d+\.\d+(?:\.\d+)?)(?=($|[\s:,;]))', re.IGNORECASE),
    "error_string": re.compile(r'(^|[\s"\'\(])(error:|failed:|exception:|traceback|permission denied|exit_code=\d+|ImportError|attempted relative import with no known parent package|local streaming runtime error|response\.custom_tool_call_input\.done)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "negation": re.compile(r'(^|[\s"\'\(])(not|never|no|without|unless|rejected|disallowed|disagree|don\'t|cannot)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "user_correction": re.compile(r'(^|[\s"\'\(])(user corrected|user rejected|user explicitly said|do not re-attempt|incorrect|mistake)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "decision_boundary": re.compile(r'(^|[\s"\'\(])(therefore|decided|deferred|blocked because|evidence-to-decision|evidence|source-backed|inferred|concluded)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "test_name": re.compile(r'(^|[\s"\'\(])(test_[a-z0-9_]+|[A-Z][a-zA-Z0-9]+Tests|[a-z0-9_]+\.py)(?=($|[\s"\'\):,;]))'),
    "model_name": re.compile(r'(^|[\s"\'\(])(Qwen[a-zA-Z0-9\._\-\[\]]+|gemini-[a-z0-9\.-]+|GPT-[a-z0-9\.-]+)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "c_macro": re.compile(r'(^|[\s"\'\(])(#define|[A-Z][A-Z0-9_]+_IMPLEMENTATION)(?=($|[\s"\'\):,;]))'),
    "qualified_symbol": re.compile(r'(^|[\s"\'\(])([A-Z][a-z0-9]+\(\))(?=($|[\s"\'\):,;]))'),
    "code_symbol": re.compile(r'(^|[\s"\'\(])([a-z_][a-z0-9_]{2,}_[a-z0-9_]+|[a-z_][a-z0-9_]*\(\)|[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]+|[a-z0-9_]+\.[a-z0-9_]+)(?=($|[\s"\'\):,;]))'),
}

# Weighting overrides
HEAVY_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string", "negation", "user_correction", "build_file", "language_command", "c_macro"}
MEDIUM_FEATURES = {"flag", "test_name", "code_symbol", "model_name", "decision_boundary", "repo_dir", "qualified_symbol"}

HIGH_RISK_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string", "negation", "user_correction", "decision_boundary", "build_file", "language_command", "c_macro"}

def score_text(text: str) -> list[SurvivalSpan]:
    if not text or not text.strip():
        return []

    # 1. Collect all potential matches
    all_matches = []
    # Pattern priority is defined by order in PATTERNS
    priority_map = {name: i for i, name in enumerate(PATTERNS.keys())}

    for name, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            # Group 2 is always the content we care about
            span_text = match.group(2).strip()
            if not span_text:
                continue
            
            # Basic heuristic for avoiding too many generic code symbols
            is_call = span_text.endswith("()")
            if name == "code_symbol" and len(span_text) < 5 and not is_call:
                continue

            all_matches.append({
                "start": match.start(2),
                "end": match.end(2),
                "text": span_text,
                "name": name,
                "priority": priority_map[name]
            })

    # 2. Pick non-overlapping matches
    # Sort by start position, then by priority (smaller is better), then by length (longer is better)
    all_matches.sort(key=lambda m: (m["start"], m["priority"], -(m["end"] - m["start"])))

    selected_spans = []
    last_end = -1
    seen_texts = set()

    for m in all_matches:
        if m["start"] >= last_end:
            # Skip if we already have this EXACT text as a survival atom
            if m["text"] in seen_texts:
                # Still count as consuming the space to avoid overlapping a different atom
                last_end = max(last_end, m["end"])
                continue
            
            name = m["name"]
            weight = "heavy" if name in HEAVY_FEATURES else "medium"
            risk = "high" if name in HIGH_RISK_FEATURES else "medium"
            
            if name in ("negation", "user_correction"):
                weight = "heavy"
                risk = "high"

            selected_spans.append(SurvivalSpan(
                text=m["text"],
                weight=weight,
                exactness_risk=risk,
                features=(name,),
                start=m["start"],
                end=m["end"]
            ))
            last_end = m["end"]
            seen_texts.add(m["text"])

    return selected_spans

def score_items(items: list[dict]) -> list[SurvivalSpan]:
    """Extract survival spans from a list of conversation items."""
    if not isinstance(items, list):
        return []

    all_spans = []
    seen_texts = set()

    def add_spans(text):
        if not isinstance(text, str) or not text:
            return
        spans = score_text(text)
        for s in spans:
            if s.text not in seen_texts:
                all_spans.append(s)
                seen_texts.add(s.text)

    for item in items:
        if not isinstance(item, dict):
            # Safe skip non-dict items if they are accidentally mixed in
            if isinstance(item, str):
                add_spans(item)
            continue
        
        item_type = item.get("type")
        
        # Message items
        if item_type == "message":
            content = item.get("content")
            if isinstance(content, str):
                add_spans(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        # Support input_text, output_text, or generic text
                        for field in ("input_text", "output_text", "text", "content"):
                            val = part.get(field)
                            if isinstance(val, str):
                                add_spans(val)
                            elif field == "content" and isinstance(val, str):
                                add_spans(val)
                    elif isinstance(part, str):
                        add_spans(part)
        
        # Tool call items
        elif item_type == "function_call":
            add_spans(item.get("name"))
            # For JSON arguments, just scan as text for now
            args = item.get("arguments")
            if isinstance(args, str):
                add_spans(args)
            elif isinstance(args, dict):
                # Fallback: scan string values in the dict
                for v in args.values():
                    if isinstance(v, str):
                        add_spans(v)
        
        elif item_type == "custom_tool_call":
            add_spans(item.get("name"))
            add_spans(item.get("input"))
            
        elif item_type == "function_call_output":
            add_spans(item.get("output"))
        
        # Fallback for unknown shapes: check common string fields
        else:
            for field in ("text", "content", "output", "summary", "input", "arguments"):
                val = item.get(field)
                if isinstance(val, str):
                    add_spans(val)
                elif isinstance(val, dict):
                     for v in val.values():
                        if isinstance(v, str):
                            add_spans(v)

    return all_spans

def format_survival_hints(spans: list[SurvivalSpan], *, max_spans: int = 80) -> str:
    """Format spans into a compact hint string for the compaction prompt."""
    if not spans:
        return ""

    # Weights for stable sorting
    weight_map = {"heavy": 0, "medium": 1, "light": 2}
    risk_map = {"high": 0, "medium": 1, "low": 2}

    # Deduplicate by text while preserving first occurrence order for priority
    unique_spans = []
    seen_text = set()
    for s in spans:
        if s.text not in seen_text:
            unique_spans.append(s)
            seen_text.add(s.text)

    # Priority sort: 
    # 1. heavy/high 
    # 2. heavy/medium
    # 3. medium/high
    # 4. medium/medium
    # 5. light
    # 6. text length (longer first)
    # 7. alphabetical (deterministic tie-break)
    sorted_spans = sorted(unique_spans, key=lambda s: (
        weight_map.get(s.weight, 99),
        risk_map.get(s.exactness_risk, 99),
        -len(s.text),
        s.text
    ))

    lines = ["Survival hints:"]
    for span in sorted_spans[:max_spans]:
        # Truncate very long spans safely (preserving start/end context)
        display_text = span.text
        if len(display_text) > 120:
            display_text = display_text[:58] + "..." + display_text[-59:]
        
        feature = span.features[0] if span.features else "unknown"
        lines.append(f"- {span.weight}/{span.exactness_risk} {feature}: {display_text}")

    return "\n".join(lines)
