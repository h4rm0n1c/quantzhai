# Codex Request Signal Inventory

Status: placeholder for the Gemini source-audit report.

Expected producer: Gemini 3 Flash Preview.

Expected task:

```text
Audit h4rm0n1c/codex and h4rm0n1c/quantzhai for every useful Codex-provided signal that QuantZhai should capture, parse, or persist before Phase 1 SQLite work.
```

Expected output sections:

```text
# Verdict
# Codex signals found
# Highest-value missing signals
# Recommended parser changes before SQLite
# Recommended DB columns/tables
# Things to defer
# Tests to add
```

Rules for the filled report:

```text
- Cite exact Codex files/functions/tests.
- Distinguish local QuantZhai capture evidence from Codex-source-only possibilities.
- Do not propose model-visible memory changes yet.
- Do not implement code in this report.
- Keep recommendations practical and scoped.
```
