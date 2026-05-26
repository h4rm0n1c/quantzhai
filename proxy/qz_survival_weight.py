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
# Use a two-group strategy: Group 1 is the prefix (optional), Group 2 is the content.
PATTERNS = {
    "path": re.compile(r'(^|[\s"\'\(])((?:\.?\.?/[a-zA-Z0-9._\-\[\]]+)+|(?:\w+[/\\])+[a-zA-Z0-9._\-\[\]]+\.\w+)(?=$|[\s"\'\):,;])'),
    "command": re.compile(r'(^|[\s"\'\(])(git\s+\w+|python3?\s+(?:-m\s+)?[\w\.-]+(?:\s+[\w\.-]+)*|rg\b.*?|curl\b.*?|bash\b.*?|sudo\b.*?)(?=$|[\s"\'\):,;])'),
    "flag": re.compile(r'(^|\s)(--[a-z0-9_-]+|-[a-z0-9])(?=$|[\s:,;])'),
    "env_var": re.compile(r'(^|\s)([A-Z0-9_]+=[^ \s]+|\$[A-Z0-9_]+)(?=$|[\s:,;])'),
    "sha": re.compile(r'(\b)([0-9a-f]{7,64})(\b)'),
    "issue_ref": re.compile(r'(^|\s)(#\d+|issue\s+#\d+|PR\s+#\d+)(?=$|[\s:,;])', re.IGNORECASE),
    "version": re.compile(r'(^|\s)(v\d+\.\d+\.\d+|localcmp:v\d+:?|Codex\s+\d+\.\d+)(?=$|[\s:,;])', re.IGNORECASE),
    "error_string": re.compile(r'(^|[\s"\'\(])(error:|failed:|exception:|traceback|permission denied|exit_code=\d+)(?=$|[\s"\'\):,;])', re.IGNORECASE),
    "negation": re.compile(r'(\b)(not|never|no|without|unless|rejected|disallowed|disagree|don\'t|cannot)(\b)', re.IGNORECASE),
    "user_correction": re.compile(r'(\b)(user corrected|user rejected|user explicitly said|do not re-attempt|incorrect|mistake)(\b)', re.IGNORECASE),
    "decision_boundary": re.compile(r'(\b)(therefore|decided|deferred|blocked because|evidence|source-backed|inferred|concluded)(\b)', re.IGNORECASE),
    "test_name": re.compile(r'(\b)(test_[a-z0-9_]+|[A-Z][a-zA-Z0-9]+Tests|[a-z0-9_]+\.py)(\b)'),
    "model_name": re.compile(r'(\b)(Qwen[a-zA-Z0-9\.-]+|gemini-[a-z0-9\.-]+|GPT-[a-z0-9\.-]+)(\b)', re.IGNORECASE),
    "code_symbol": re.compile(r'(\b)([a-z_][a-z0-9_]{3,}|[A-Z][a-zA-Z0-9_]{3,})(\b)'),
}

# Weighting overrides
HEAVY_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string", "negation", "user_correction"}
MEDIUM_FEATURES = {"flag", "test_name", "code_symbol", "model_name", "decision_boundary"}

HIGH_RISK_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string", "negation", "user_correction", "decision_boundary"}

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
            if name == "code_symbol" and len(span_text) < 5:
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
            if m["text"] in seen_texts:
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
    all_spans = []
    seen_texts = set()

    def add_spans(text):
        if not text:
            return
        spans = score_text(text)
        for s in spans:
            if s.text not in seen_texts:
                all_spans.append(s)
                seen_texts.add(s.text)

    for item in items:
        if not isinstance(item, dict):
            continue
        
        # Message items
        if item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, str):
                add_spans(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "input_text":
                        add_spans(part.get("text"))
        
        # Tool call items
        elif item.get("type") == "function_call":
            add_spans(item.get("name"))
            add_spans(item.get("arguments"))
        
        elif item.get("type") == "custom_tool_call":
            add_spans(item.get("name"))
            add_spans(item.get("input"))
            
        elif item.get("type") == "function_call_output":
            add_spans(item.get("output"))
        
        # Fallback for unknown shapes: check 'text', 'content', 'output' fields
        else:
            for field in ("text", "content", "output", "summary"):
                val = item.get(field)
                if isinstance(val, str):
                    add_spans(val)

    return all_spans

def format_survival_hints(spans: list[SurvivalSpan], *, max_spans: int = 80) -> str:
    if not spans:
        return ""

    lines = ["Survival hints:"]
    
    # Priority sort: heavy/high first, then by text length descending?
    # Let's keep it simple: heavy/high first, then others.
    sorted_spans = sorted(spans, key=lambda s: (
        0 if s.weight == "heavy" and s.exactness_risk == "high" else 1,
        0 if s.weight == "heavy" else 1,
        -len(s.text)
    ))

    for span in sorted_spans[:max_spans]:
        # Truncate very long spans
        display_text = span.text
        if len(display_text) > 120:
            display_text = display_text[:60] + "..." + display_text[-60:]
        
        feature = span.features[0] if span.features else "unknown"
        lines.append(f"- {span.weight}/{span.exactness_risk} {feature}: {display_text}")

    return "\n".join(lines)
