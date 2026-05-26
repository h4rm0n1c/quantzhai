"""
Offline fixture/eval harness for compaction strategies.
Pure module for evaluation metrics and strategies.
NOT for use in live proxy runtime.
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Any
from proxy.qz_survival_weight import score_text, format_survival_hints

@dataclass(frozen=True)
class FixtureSpec:
    name: str
    input_text: str
    expected_atoms: Set[str] = field(default_factory=set)
    required_decisions: Set[str] = field(default_factory=set) # evidence/decision markers
    negative_constraints: Set[str] = field(default_factory=set)
    file_path: str = ""

@dataclass(frozen=True)
class EvalResult:
    fixture_name: str
    strategy: str
    metrics: Dict[str, float]
    output_text: str

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

        # Extract input sketch - skip intro text and find the code block
        input_text = ""
        sketch_section = re.search(r"## Input Sketch\n\n(.*?)(?:\n##|$)", content, re.DOTALL)
        if sketch_section:
            section_content = sketch_section.group(1)
            code_block = re.search(r"```(?:text|markdown)?\n(.*?)\n```", section_content, re.DOTALL)
            if code_block:
                input_text = code_block.group(1).strip()
            else:
                input_text = section_content.strip()

        # Extract atoms from "What a Compliant Compaction Must Preserve" table
        atoms = set()
        table_match = re.search(r"## What a Compliant Compaction Must Preserve\n\n(.*?)(?:\n\n|\n##|\n#|$)", content, re.DOTALL)
        if table_match:
            rows = table_match.group(1).strip().split("\n")
            for row in rows:
                if "|" not in row or "---" in row or "Atom" in row:
                    continue
                parts = row.strip("|").split("|")
                if parts:
                    atom = parts[0].strip().strip("`")
                    if atom:
                        atoms.add(atom)

        # Heuristic extraction of negative constraints for fixture-03
        neg_constraints = set()
        if "fixture-03" in filename:
            for atom in atoms:
                if atom.lower().startswith("do not"):
                    neg_constraints.add(atom)
        
        # Add atoms from the table in a separate step to be sure
        if not atoms:
            # Try a simpler regex if the table one failed
             for match in re.finditer(r"\|\s*`([^`]+)`\s*\|\s*verbatim", content):
                 atoms.add(match.group(1))

        specs.append(FixtureSpec(
            name=filename.replace(".md", ""),
            input_text=input_text,
            expected_atoms=atoms,
            negative_constraints=neg_constraints,
            file_path=path
        ))
    return specs

# --- Strategies ---

def freeform_summary_baseline(input_text: str) -> str:
    """Simulates a loose, generic compactor that loses exact atoms."""
    # Just a very generic summary that drops most technical details
    return "The agent fixed some bugs in the proxy related to import modes and updated AGENTS.md with some planning mode hints. Some tests were run and commits were made."

def current_heuristic_baseline(input_text: str) -> str:
    """Mimics current flat bullet extraction baseline."""
    lines = input_text.split("\n")
    summary_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith("[Agent]") or line.startswith("[User]"):
            summary_lines.append(f"- {line}")
    
    return "<|history_summary|>\nPrior turn summary:\n" + "\n".join(summary_lines) + "\n<|end_history_summary|>"

def anchored_template(input_text: str) -> str:
    """Uses Stage 1 headings but simple summary inside."""
    return """## Goal
General task completion.

## Technical State
### Files / Paths
(various files updated)

## Next Actions
1. Continue as requested.
"""

def survival_weighted_anchored(input_text: str) -> str:
    """Uses qz_survival_weight to inject atoms into anchored schema."""
    spans = score_text(input_text)
    hints = format_survival_hints(spans)
    
    # Extract whole lines containing heavy/high spans to simulate "preservation"
    input_lines = input_text.split("\n")
    preserved_lines = []
    
    # We'll use atoms as anchors to keep the whole line
    for line in input_lines:
        line_spans = score_text(line)
        if any(s.weight == "heavy" for s in line_spans):
            preserved_lines.append(line.strip())

    # Simulate an anchored summary that preserves these hints
    output = "## Goal\nPreserve context with anchored schema.\n\n"
    
    output += "## Technical State\n### Files / Paths\n"
    for line in preserved_lines:
        # Heuristic: if it looks like a path or command line
        if "/" in line or "python" in line or "git" in line:
            output += f"{line}\n"
    
    output += "\n## Key Decisions & Anchors\n"
    for line in preserved_lines:
        # Heuristic: if it looks like a decision or constraint
        if any(w in line.lower() for w in ("do not", "not", "rejected", "decided", "evidence")):
            output += f"- {line}\n"

    output += "\n## Survival Hints (Internal Metadata)\n"
    output += hints
    
    return output

# --- Metrics ---

def calculate_metrics(output: str, spec: FixtureSpec) -> Dict[str, float]:
    metrics = {}
    
    # 1. Exact atom retention
    if not spec.expected_atoms:
        metrics["exact_atom_retention"] = 1.0
    else:
        preserved = sum(1 for atom in spec.expected_atoms if atom in output)
        metrics["exact_atom_retention"] = preserved / len(spec.expected_atoms)

    # 2. Path retention
    paths = {a for a in spec.expected_atoms if "/" in a or a.endswith(".py") or a.endswith(".md")}
    if not paths:
        metrics["exact_path_retention"] = 1.0
    else:
        preserved = sum(1 for p in paths if p in output)
        metrics["exact_path_retention"] = preserved / len(paths)

    # 3. Negation retention
    if not spec.negative_constraints:
        metrics["negation_retention"] = 1.0
    else:
        preserved = sum(1 for n in spec.negative_constraints if n in output)
        metrics["negation_retention"] = preserved / len(spec.negative_constraints)

    # 4. Token budget (approx)
    metrics["token_budget_ratio"] = len(output) / max(1, len(spec.input_text))

    # 5. Hallucinated fact rate (naive)
    # Count SHAs or paths in output that were NOT in input
    # (For Stage 3, we expect this to be 0 for our deterministic strategies)
    shas_in_output = set(re.findall(r"\b[0-9a-f]{7}\b", output))
    shas_in_input = set(re.findall(r"\b[0-9a-f]{7}\b", spec.input_text))
    hallucinated = len(shas_in_output - shas_in_input)
    metrics["hallucinated_fact_rate"] = hallucinated / max(1, len(shas_in_output))

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
