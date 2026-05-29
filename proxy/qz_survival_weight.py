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
    # Level: 1 = atom (preserve verbatim), 2 = semantic concept (preserve meaning,
    # paraphrase allowed). Level-2 spans come from SEMANTIC_PATTERNS and are
    # routed into the "concepts" line of the survival hint output.
    level: int = 1

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
    "language_command": re.compile(r'(^|[\s"\'\(])((?:npm|pnpm|yarn)\s+(?:test|run\s+test)|go\s+test\s+\./\.\.\.|cargo\s+(?:test|build|run)|cmake\s+--build|ctest|pytest|php\s+\S+|composer\s+\S+)(?=($|[\s"\'\):,;]))'),
    "flag": re.compile(r'(^|\s)(--[a-z0-9_-]+|-[a-z0-9])(?=($|[\s:,;]))'),
    "env_var": re.compile(r'(^|\s)([A-Z0-9_]{3,}=[^ \s]+|\$[A-Z0-9_]{3,})(?=($|[\s:,;]))'),
    "sha": re.compile(r'(^|[\s"\'\(])([0-9a-f]{7,64})(?=($|[\s"\'\):,;]))'),
    "issue_ref": re.compile(r'(^|\s)(#\d+|issue\s+#\d+|PR\s+#\d+)(?=($|[\s:,;]))', re.IGNORECASE),
    # version: generic semver only — removed QuantZhai-specific localcmp:v and Codex v prefixes
    "version": re.compile(r'(^|\s)(v\d+\.\d+(?:\.\d+)?(?:[-+][a-z0-9.]+)?)(?=($|[\s:,;]))', re.IGNORECASE),
    # error_string: generic error signals — removed three LLM/QuantZhai-specific literals
    # (attempted relative import…, local streaming runtime error, response.custom_tool_call_input.done)
    "error_string": re.compile(r'(^|[\s"\'\(])(error:|failed:|exception:|traceback|permission denied|exit_code=\d+|ImportError|AttributeError|TypeError|RuntimeError|SyntaxError|KeyError|ValueError|NullPointerException|segfault|SQLSTATE\[\w+\])(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "negation": re.compile(r'(^|[\s"\'\(])(not|never|no|without|unless|rejected|disallowed|disagree|don\'t|cannot)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "user_correction": re.compile(r'(^|[\s"\'\(])(user corrected|user rejected|user explicitly said|do not re-attempt|incorrect|mistake)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "decision_boundary": re.compile(r'(^|[\s"\'\(])(therefore|decided|deferred|blocked because|evidence-to-decision|evidence|source-backed|inferred|concluded)(?=($|[\s"\'\):,;]))', re.IGNORECASE),
    "test_name": re.compile(r'(^|[\s"\'\(])(test_[a-z0-9_]+|[A-Z][a-zA-Z0-9]+Tests|[a-z0-9_]+\.py)(?=($|[\s"\'\):,;]))'),
    # model_name removed: was hardcoded to Qwen/gemini-/GPT- — fires only in quantzhai,
    # zero hits in 8/9 corpus repos. LLM product names are not a general coding atom.
    "c_macro": re.compile(r'(^|[\s"\'\(])(#define|[A-Z][A-Z0-9_]+_IMPLEMENTATION)(?=($|[\s"\'\):,;]))'),
    "qualified_symbol": re.compile(r'(^|[\s"\'\(])([A-Z][a-z0-9]+\(\))(?=($|[\s"\'\):,;]))'),
    "code_symbol": re.compile(r'(^|[\s"\'\(])([a-z_][a-z0-9_]{2,}_[a-z0-9_]+|[a-z_][a-z0-9_]*\(\)|[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]+|[a-z0-9_]+\.[a-z0-9_]+)(?=($|[\s"\'\):,;]))'),
    # import_path: module/package import statements across languages — high signal, fired zero
    # times in current scorer despite being present in every corpus repo.
    # Covers: Python (from X import Y / import X), JS/TS (import X from 'Y' / require('Y')),
    # Rust (use X::Y), Go (import "X"), C/C++ (#include <X>/"X"), PHP (require/include 'X').
    "import_path": re.compile(
        r'(^|[\s"\'\(])'
        r'(from\s+[\w.]+\s+import\s+[\w*, ]+|import\s+[\w./"-]+|use\s+[\w:]+;?'
        r'|require\(["\'][\w./]+["\']\)|include[_once]*\s*["\'][\w./]+["\']'
        r'|#include\s*[<"][\w./]+[>"])'
        r'(?=($|[\s"\'\):,;]))',
        re.MULTILINE
    ),
    # sql_keyword: DDL/DML verbs that are schema atoms — zero coverage previously.
    # Fires on SQL files (CREATE TABLE, ALTER TABLE, PRIMARY KEY, etc.) and inline
    # query strings in any language. HEAVY because schema structure is high-value context.
    "sql_keyword": re.compile(
        r'(^|[\s"\'\(])'
        r'(CREATE\s+(?:TABLE|DATABASE|INDEX|VIEW|TRIGGER|PROCEDURE|FUNCTION)'
        r'|ALTER\s+TABLE|DROP\s+(?:TABLE|DATABASE|INDEX)'
        r'|PRIMARY\s+KEY|FOREIGN\s+KEY|FULLTEXT\s+KEY|UNIQUE\s+KEY'
        r'|AUTO_INCREMENT|NOT\s+NULL|CHARACTER\s+SET|COLLATE\s+\w+'
        r'|ENGINE=\w+|INSERT\s+INTO|SELECT\s+(?:\*|\w)|DELETE\s+FROM|UPDATE\s+\w)'
        r'(?=($|[\s"\'\):,;]))',
        re.IGNORECASE
    ),
}

# ---------------------------------------------------------------------------
# Level 2: semantic / sentence-level patterns
#
# These do NOT identify a span to copy verbatim; they identify a *sentence* that
# carries semantic weight (causal explanation, constraint discovery, correction,
# investigation outcome, failure record). The LLM compactor must preserve the
# *meaning* of the sentence but may paraphrase the surface form.
#
# Design rules:
#   - Anchored to lexemes that signal an actual discovery / conclusion, not
#     planning narration ("Let me check X", "I will Y" must NOT fire).
#   - Must generalise across domains (game dev, embedded, proxy, log curation).
#   - Cheap regex only; called per-item in a polling loop.
#   - Weight = medium, exactness_risk = high — losing the concept is costly
#     but verbatim phrasing is not required.
#
# Each match contributes ONE SurvivalSpan per category per item (the matched
# substring, used as a marker; the LLM should treat the *containing sentence*
# as the concept to preserve).
# ---------------------------------------------------------------------------
SEMANTIC_PATTERNS = {
    # Causal explanation: X because Y, since Y, due to Y, "the reason is",
    # "root cause", and the discovered-cause-to-action form ", so I'm/we Y".
    "causal": re.compile(
        r'\b(?:because|since|due to|owing to|as a result of'
        r'|the reason (?:is|was)|root cause|caused by'
        r'|,\s*so\s+I(?:\'m|\s+am)?\s+\w+ing'
        r'|,\s*so\s+(?:I|we)\s+(?:need|have|must|will|am|can|cannot|can\'t))\b',
        re.IGNORECASE | re.MULTILINE),
    # Hard constraint / requirement discovered or restated.
    "constraint": re.compile(
        r'\b(?:must (?:not )?(?:be|have|stay|remain|use|reflect|follow|keep|update|preserve|never)'
        r'|requires?\s+(?:a|an|the|that|\w+ing)'
        r'|required to|only if|only when|prevents?\s+\w+ing|incompatible with|depends on'
        r'|need(?:s|ed)?\s+to\s+(?:be|have|stay|remain|exit|finish|run|happen|process))\b',
        re.IGNORECASE | re.MULTILINE),
    # State correction / realization: "wait, actually", "turns out", "I was
    # wrong", "that's wrong", "redundant".
    "correction": re.compile(
        r'\b(?:wait,?\s+(?:actually|I)'
        r'|actually,\s+(?:looking|it|the|that|this)'
        r'|turn(?:s|ed)?\s+out|it turns out'
        r'|I (?:was wrong|realiz\w+|see (?:that|now)|notice that)'
        r'|that(?:\'s| is|\s+was)\s+(?:wrong|incorrect)'
        r'|redundant|which was wrong'
        r'|the (?:doc|previous|earlier)\s+\w+\s+(?:said|was)\s+wrong)\b',
        re.IGNORECASE | re.MULTILINE),
    # Investigation outcome / confirmation: "confirms that", "this means",
    # "indicates that", "X are all Y" (discovered classification).
    "outcome": re.compile(
        r'\b(?:confirm(?:s|ed|ing)\s+(?:it|that|is|the|a|an)'
        r'|confirming\s+(?:it|that|is|the)'
        r'|this means|that means|this indicates|indicat(?:es|ing) that'
        r'|evidence (?:shows|confirms|suggests)|proves? that|shown to be'
        r'|are all\s+\w+(?:\s+\w+){0,3}\s*(?:\(|,|\.|$))\b',
        re.IGNORECASE | re.MULTILINE),
    # Failure record: tried-X-but-Y, doesn't work, fails when, broken when,
    # quantified warnings (zero/two warnings, warning type/count is X).
    "failure_record": re.compile(
        r'\b(?:tried\s+\w+\s+but'
        r'|does(?:n\'t| not)\s+work\b'
        r'|fails?\s+(?:when|if|because|to)'
        r'|broke\s+(?:when|because)'
        r'|broken\s+(?:by|when|because|format|state)'
        r'|warning (?:type|count) (?:is|was)'
        r'|(?:two|three|four|zero|no)\s+warnings'
        r'|failure entry'
        r'|failed to (?:exit|finish|process|emit|write|read|load))\b',
        re.IGNORECASE | re.MULTILINE),
    # Quantified contrast: "X rather than Y", "instead of Y", "not X but Y",
    # "differ across".
    "contrast_finding": re.compile(
        r'\b(?:\d[\d,]*\s+\w+\s+(?:rather than|instead of)'
        r'|rather than\s+\w+(?:s|ed|ing)?\s+\w+'
        r'|instead of\s+\w+(?:ing|ed)?'
        r'|not\s+\w+\s+but\s+\w+'
        r'|differ(?:s|ed)?\s+(?:slightly\s+)?(?:across|between|from))\b',
        re.IGNORECASE | re.MULTILINE),
    # Verification / acceptance outcome: all tests pass, all clean, QA checks
    # passed, fix is working, the corrected run, certification checks.
    "verification_outcome": re.compile(
        r'\b(?:all\s+\d*\s*(?:tests?\s+(?:pass|passed)|valid forms work|rejection cases work|six derived reruns have completed|clean)'
        r'|all tests pass|all clean|qa checks? passed|works? correctly'
        r'|certification checks?|the patch (?:is|was) staged|fix is working'
        r'|completed with|the corrected run|stage qa)\b',
        re.IGNORECASE | re.MULTILINE),
    # Scoping rule: "go only to/under X", "only emitted N", "quarantined under
    # sensitive", "extract only the X", "only scans one".
    "scoping_rule": re.compile(
        r'\b(?:will go only|go only (?:to|under|into)|only emitted'
        r'|stays? at\s+\w+\s+\w+\s+only'
        r'|only one\s+\w+'
        r'|quarantined? .{0,40}\bunder\s+`?sensitive'
        r'|put .{0,40}only under'
        r'|only scans? one|extract only the\s+\w+'
        r'|only the\s+\w+\s+(?:databases|files|members))\b',
        re.IGNORECASE | re.MULTILINE),
}


# Weighting overrides
HEAVY_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string",
                  "negation", "user_correction", "build_file", "language_command", "c_macro",
                  "import_path", "sql_keyword"}
MEDIUM_FEATURES = {"flag", "test_name", "code_symbol", "decision_boundary", "repo_dir",
                   "qualified_symbol"}

HIGH_RISK_FEATURES = {"path", "command", "env_var", "sha", "issue_ref", "version", "error_string",
                      "negation", "user_correction", "decision_boundary", "build_file",
                      "language_command", "c_macro", "import_path", "sql_keyword"}

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
                end=m["end"],
                level=1,
            ))
            last_end = m["end"]
            seen_texts.add(m["text"])

    # Level-2: semantic patterns. Fire AT MOST ONCE per category per text
    # (paraphrasable concepts; we don't need every match). Span text is the
    # matched lexeme — purely a marker; the concept itself is the surrounding
    # sentence, which the LLM holds in the raw conversation prefix.
    for name, pattern in SEMANTIC_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            continue
        marker = m.group(0).strip()
        if not marker or marker in seen_texts:
            continue
        selected_spans.append(SurvivalSpan(
            text=marker,
            weight="medium",
            exactness_risk="high",
            features=(name,),
            start=m.start(),
            end=m.end(),
            level=2,
        ))
        seen_texts.add(marker)

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
    """Format spans as contextual guidance for the compaction prompt.

    Produces up to three inline lines:
      - ``verbatim``: Level-1 heavy atoms (paths, SHAs, commands, env vars,
        errors, negations, etc.) — LLM must copy these exactly.
      - ``context``: Level-1 medium atoms (flags, test names, code symbols,
        decision-boundary markers) — preserve in context, exactness still
        matters.
      - ``concepts``: Level-2 semantic patterns (causal explanations,
        constraint discoveries, corrections, investigation outcomes, failure
        records). The LLM must preserve the *meaning* of the sentence
        containing each marker but may paraphrase the surface form.
    """
    if not spans:
        return ""

    weight_map = {"heavy": 0, "medium": 1, "light": 2}
    risk_map = {"high": 0, "medium": 1, "low": 2}

    unique_spans = []
    seen_text = set()
    for s in spans:
        if s.text not in seen_text:
            unique_spans.append(s)
            seen_text.add(s.text)

    sorted_spans = sorted(unique_spans, key=lambda s: (
        getattr(s, "level", 1),                # level-1 atoms first
        weight_map.get(s.weight, 99),
        risk_map.get(s.exactness_risk, 99),
        -len(s.text),
        s.text
    ))

    selected = sorted_spans[:max_spans]

    verbatim = []
    context = []
    concepts = []
    for s in selected:
        display = s.text if len(s.text) <= 80 else s.text[:38] + "…" + s.text[-39:]
        if getattr(s, "level", 1) == 2:
            concepts.append(display)
        elif s.weight == "heavy":
            verbatim.append(display)
        else:
            context.append(display)

    lines = [
        "Atoms to preserve verbatim — integrate in context, do not create atom-type sections:",
    ]
    if verbatim:
        lines.append("  verbatim: " + " • ".join(verbatim))
    if context:
        lines.append("  context: " + " • ".join(context))
    if concepts:
        lines.append(
            "  concepts (preserve meaning, paraphrase allowed): "
            + " • ".join(concepts)
        )

    return "\n".join(lines)
