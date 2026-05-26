"""
Offline fixture/eval harness for compaction strategies.
Pure module for evaluation metrics and strategies.
NOT for use in live proxy runtime.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Any, Optional
from proxy.qz_survival_weight import score_text, format_survival_hints

@dataclass(frozen=True)
class FixtureSpec:
    name: str
    input_text: str
    expected_atoms: Set[str] = field(default_factory=set)
    expected_paths: Set[str] = field(default_factory=set)
    expected_commands: Set[str] = field(default_factory=set)
    expected_errors: Set[str] = field(default_factory=set)
    expected_negations: Set[str] = field(default_factory=set)
    expected_evidence: Set[str] = field(default_factory=set)
    expected_decisions: Set[str] = field(default_factory=set)
    expected_deferred: Set[str] = field(default_factory=set)
    stale_old_absent: Set[str] = field(default_factory=set)
    stale_new_present: Set[str] = field(default_factory=set)
    file_path: str = ""

@dataclass(frozen=True)
class EvalResult:
    fixture_name: str
    strategy: str
    metrics: Dict[str, float]
    output_text: str

# Canonical schema headings from Stage 1
CANONICAL_HEADINGS = [
    "## Goal",
    "## Active Constraints & Guardrails",
    "## Current Status",
    "### Done",
    "### In Progress",
    "### Blocked / Deferred",
    "## Key Decisions",
    "## Evidence Boundaries",
    "## Technical State",
    "### Files / Paths",
    "### Commands / Flags / Env Vars",
    "### SHAs / Versions / Model Names",
    "### Tests / Results",
    "### Tool / Capture Outputs",
    "## Rejected / Abandoned Approaches",
    "## Open Questions / Uncertainties",
    "## Next Actions",
    "## Provenance / Source Pointers"
]

def load_fixtures(fixtures_dir: str) -> List[FixtureSpec]:
    specs = []
    if not os.path.exists(fixtures_dir):
        return []

    for filename in sorted(os.listdir(fixtures_dir)):
        if not filename.endswith(".md"):
            continue
        
        path = os.path.join(fixtures_dir, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract input sketch
        input_text = ""
        sketch_section = re.search(r"## Input Sketch\n\n(.*?)(?:\n##|$)", content, re.DOTALL)
        if sketch_section:
            section_content = sketch_section.group(1)
            code_block = re.search(r"```(?:text|markdown)?\n(.*?)\n```", section_content, re.DOTALL)
            if code_block:
                input_text = code_block.group(1).strip()
            else:
                input_text = section_content.strip()

        # Define expectations per fixture (Stage 3.1 hardening)
        # In a real system these might be parsed from the MD, but for now we'll 
        # hardcode or use simple extraction to ensure high-quality eval.
        atoms = set()
        paths = set()
        cmds = set()
        errors = set()
        negations = set()
        evidence = set()
        decisions = set()
        deferred = set()
        stale_old = set()
        stale_new = set()

        # Simple table parser for atoms
        # Look for rows that have backticks in the first column
        for match in re.finditer(r"\|\s*`([^`]+)`\s*\|", content):
            atom = match.group(1).strip()
            # Basic sanity check: ignore if it's the header
            if atom == "Atom": continue
            
            atoms.add(atom)
            atom_lower = atom.lower()
            
            # Order matters here! Check commands first because they might contain paths.
            if any(c in atom_lower for c in ("git ", "python", "rg ", "curl ", "bash ", "sudo ")) or "--" in atom or (atom.startswith("-") and len(atom) < 5):
                cmds.add(atom)
            elif "/" in atom or atom_lower.endswith(".py") or atom_lower.endswith(".md") or atom_lower == "agents.md":
                paths.add(atom)
            elif "exit_code=" in atom or "importerror" in atom_lower:
                errors.add(atom)
            elif atom_lower.startswith("do not"):
                negations.add(atom)

        # Fixture-specific overrides for Stage 3.1 precision
        if "fixture-01" in filename:
            evidence.update(["proxy/qz_request_router.py:312", "docs/codex-plan-mode-live-capture.md"])
            decisions.update(["input_mode guard must be checked", "Plan mode hint belongs in AGENTS.md"])
        elif "fixture-02" in filename:
            evidence.update(["proxy/qz_responses.py:243", "proxy/qz_responses.py:187"])
            decisions.update(["does not crash on empty item list", "Depth cap is 8"])
            errors.update(["exit_code=0"]) # In this fixture, 0 is the expected verbatim signal
        elif "fixture-03" in filename:
            negations.update(["Do not implement proxy-level sudo interception", "Do not add a sudo -v pre-run wrapper"])
            deferred.update(["#74 permissions/escalation", "model_auto_compact_token_limit"])
            stale_old.add("0627f39 — Fix live streaming runtime import mode (previous session)")
            stale_new.add("0627f39 — Fix live streaming runtime import mode\nbabf7b5 — Add permission outcome feedback advisory")

        specs.append(FixtureSpec(
            name=filename.replace(".md", ""),
            input_text=input_text,
            expected_atoms=atoms,
            expected_paths=paths,
            expected_commands=cmds,
            expected_errors=errors,
            expected_negations=negations,
            expected_evidence=evidence,
            expected_decisions=decisions,
            expected_deferred=deferred,
            stale_old_absent=stale_old,
            stale_new_present=stale_new,
            file_path=path
        ))
    return specs

# --- Strategies ---

def freeform_summary_baseline(input_text: str) -> str:
    """A fair but loose prose summary baseline."""
    # Simulates what a generic LLM might do without structural constraints
    return """The agent worked on the proxy code. Specifically, they addressed an import-mode regression 
and added a hint for Codex Plan mode. Files like qz_request_router.py were checked. 
Tests passed and changes were committed with SHAs like 0627f39."""

def current_heuristic_baseline(input_text: str) -> str:
    """Mimics current proxy bullet extraction."""
    lines = input_text.split("\n")
    summary_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith("[Agent]") or line.startswith("[User]"):
            summary_lines.append(f"- {line}")
    
    return "<|history_summary|>\nPrior turn summary:\n" + "\n".join(summary_lines) + "\n<|end_history_summary|>"

def anchored_template(input_text: str) -> str:
    """Uses Stage 1 headings with fair high-level content but no exact atoms."""
    return """## Goal
Resolve reported issues and document behavior.

## Current Status
### Done
- Bug fixed and documentation updated.

## Technical State
### Files / Paths
- Various proxy and doc files.

## Next Actions
1. Task complete.
"""

def survival_weighted_anchored(input_text: str) -> str:
    """Uses Stage 2.1 hardened scorer to anchor verbatim line preservation in canonical schema."""
    spans = score_text(input_text)
    hints = format_survival_hints(spans)
    
    input_lines = input_text.split("\n")
    preserved_lines = []
    for line in input_lines:
        line_spans = score_text(line)
        if any(s.weight == "heavy" for s in line_spans):
            preserved_lines.append(line.strip())

    output_lines = ["## Goal", "Preserve context with anchored survival-weighted strategy.", ""]
    
    output_lines.append("## Key Decisions")
    for line in preserved_lines:
        if any(w in line.lower() for w in ("decided", "rejected", "therefore", "evidence")):
            output_lines.append(f"- {line}")
    output_lines.append("")

    output_lines.append("## Technical State")
    output_lines.append("### Files / Paths")
    for line in preserved_lines:
        if "/" in line and not line.startswith("rg"):
            output_lines.append(line)
    output_lines.append("")
    
    output_lines.append("### Commands / Flags / Env Vars")
    for line in preserved_lines:
        if any(f in line for f in ("git", "python", "sudo", "--", "exit_code=")):
            output_lines.append(line)
    output_lines.append("")

    output_lines.append("## Evidence Boundaries")
    output_lines.append(hints)
    output_lines.append("")
    
    # Ensure some other headings are present for structure
    output_lines.append("## Next Actions")
    output_lines.append("1. Continue from preserved state.")

    return "\n".join(output_lines)

# --- Metrics ---

def calculate_metrics(output: str, spec: FixtureSpec) -> Dict[str, float]:
    metrics = {}
    
    # helper for retention
    def retention(expected):
        if not expected: return 1.0
        found = sum(1 for item in expected if item in output)
        return found / len(expected)

    metrics["exact_atom_retention"] = retention(spec.expected_atoms)
    metrics["exact_path_retention"] = retention(spec.expected_paths)
    metrics["exact_command_retention"] = retention(spec.expected_commands)
    metrics["error_retention"] = retention(spec.expected_errors)
    metrics["negation_retention"] = retention(spec.expected_negations)

    # 6. evidence_decision_chain
    ev_score = retention(spec.expected_evidence) * 0.34
    dec_score = retention(spec.expected_decisions) * 0.33
    def_score = retention(spec.expected_deferred) * 0.33
    metrics["evidence_decision_chain"] = ev_score + dec_score + def_score

    # 7. stale_fact_correction
    if not spec.stale_old_absent and not spec.stale_new_present:
        metrics["stale_fact_correction"] = 1.0
    else:
        old_absent = sum(1 for item in spec.stale_old_absent if item not in output)
        new_present = sum(1 for item in spec.stale_new_present if item in output)
        total = len(spec.stale_old_absent) + len(spec.stale_new_present)
        metrics["stale_fact_correction"] = (old_absent + new_present) / total if total > 0 else 1.0

    # 8. hallucinated_fact_rate
    out_spans = score_text(output)
    out_atoms = {s.text for s in out_spans if s.weight == "heavy"}
    # Ignore headings
    clean_headings = {h.strip("# ").strip() for h in CANONICAL_HEADINGS}
    in_spans = score_text(spec.input_text)
    in_atoms = {s.text for s in in_spans if s.weight == "heavy"}
    
    hallucinated = 0
    for atom in out_atoms:
        if atom in clean_headings: continue
        if atom not in in_atoms and atom not in spec.input_text:
            hallucinated += 1
    metrics["hallucinated_fact_rate"] = hallucinated / max(1, len(out_atoms))

    # 9. token_budget_ratio
    metrics["token_budget_ratio"] = len(output) / max(1, len(spec.input_text))

    # 10. downstream_recovery (proxy)
    relevant_metrics = [
        metrics["exact_path_retention"],
        metrics["exact_command_retention"],
        metrics["error_retention"],
        metrics["negation_retention"],
        metrics["evidence_decision_chain"]
    ]
    metrics["downstream_recovery"] = sum(relevant_metrics) / len(relevant_metrics)

    return metrics

def run_eval(specs: List[FixtureSpec]) -> List[EvalResult]:
    strategies = {
        "freeform_baseline": freeform_summary_baseline,
        "current_heuristic": current_heuristic_baseline,
        "anchored_template": anchored_template,
        "survival_weighted": survival_weighted_anchored
    }
    
    results = []
    for spec in specs:
        for name, strategy_fn in strategies.items():
            output = strategy_fn(spec.input_text)
            metrics = calculate_metrics(output, spec)
            results.append(EvalResult(
                fixture_name=spec.name,
                strategy=name,
                metrics=metrics,
                output_text=output
            ))
    return results
