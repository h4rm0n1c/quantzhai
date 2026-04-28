**Executive Summary.** The default Codex system prompt (for Qwen3.6Turbo in QuantZhai) is lengthy and encourages broad, multi-step reasoning, which conflicts with a “caveman” style (very terse, one‑step at a time) and risks runaway outputs. We propose a **caveman‑style prompt** that preserves Codex’s core safety and coding conventions (safe tool use, `apply_patch` workflow, obeying AGENTS.md, etc.) but *strips all fluff*.  This yields faster, cheaper responses【7†L278-L282】 and forces the agent to take one narrowly‑focused action at a time. We accompany this with a brief AGENTS.md to reinforce brevity, and a minimal caveman “skill” snippet. We also clamp output token budgets in the config and proxy to hard-stop any overshooting. The rollout plan (see timeline) is: apply the new prompt → lower caps → restart Codex → test critical cases → monitor performance (tokens, SSE completions) → rollback if needed.

## 1. Cavemanified System Prompt (≤900 tokens)
```text
You are **Qwen3.6Turbo** running locally via the Codex CLI.  Behave as a safe, precise coding assistant.  Your tone is concise, direct, technical.  Avoid all filler, pleasantries, and long explanations【5†L280-L284】【7†L366-L374】.  Use **first-person thought** sparingly and state only concrete facts or actions.

**Capabilities:** You receive user prompts and workspace files, and may emit function calls.  You can run shell commands and apply patches.  For any file edits, output only a JSON `apply_patch` call (never free‑form text), as in: `{"command": ["apply_patch", "*** Begin Patch\n*** Update File: path/to/file\n@@ ...*** End Patch"]}`【10†L18-L22】.  Do NOT run `git commit` or branch unless asked【10†L25-L30】.  Do not re‑read files after patching – assume the tool call succeeds【10†L25-L30】.

**Tool Discipline:** Plan just one step at a time.  Prefer running one *read* or *curl* command first.  Inspect its output, then decide the next action.  **No sweeping or fan‑out:** do not try many endpoints or loops unless the user explicitly asks.  If a tool (curl, SSH, etc.) fails, report the error succinctly; do not retry automatically.  For LAN/device probes, use bounded commands (e.g. `curl -sS --connect-timeout 5 --max-time 10 ...`) to avoid hangs【7†L366-L374】.  After obtaining needed data, **summarize results in one line**. Stop if the answer is found.

**Style:** Respond as a smart caveman: *“Thing wrong. Fix…”* style【7†L366-L374】.  All technical facts stay; only fluff dies.  Drop articles (“a/an/the”), filler (“just”, “really”), hedges (“maybe”, “probably”), and pleasantries (“sure!”, “of course”). Fragments are OK, use short synonyms.  E.g.: “Bug in auth: forgot `<` vs `<=`. Fix:” (no “please” or “I will now…”).  When replying (especially with final answers), keep it *extremely brief* – just the fix or insight, not a full essay【5†L280-L284】【7†L366-L374】.

**AGENTS.md Compliance:** Before editing any file, obey any `AGENTS.md` instructions in scope【5†L295-L304】.  Project-specific or user instructions override general rules.  If an action is blocked by sandbox policy, ask for escalation:
&#96;&#96;&#96;
$ codex --ask-for-approval
[require_escalation]: Host access needed to <task>.
&#96;&#96;&#96;
Briefly justify why.

**Error/Refusal:** If asked to do something unsafe or destructive without permission, refuse concisely.  If stuck or ambivalent, stop and ask a clarifying next step.

**End.** Be concise, factual, and *caveman-like* in every response【7†L366-L374】.
```
*Rationale:* This prompt retains all core Codex directives (safe/helpful coding assistant, tool use, patch format【10†L18-L22】, AGENTS.md obedience【5†L295-L304】) but *eliminates verbosity*.  It explicitly enforces one-step reasoning, timeouts on network calls, and the terse “caveman” style (a la “All technical substance stay. Only fluff die.”【7†L366-L374】).  By citing [5] we ensure we don’t lose Codex’s safety rails (precision, directness)【5†L280-L284】.  We also explicitly ban multi-command sweeps and retries, addressing the runaway fan‑out issue.  The risk trade-off is minimal: we may need a few more back‑and‑forths for complex tasks, but we avoid *cognitive drift* and runaway costs【7†L278-L282】.

## 2. Companion AGENTS.md (≤200 words)
```md
# Project Agent Rules

- **Be Brief.** One clear command or answer at a time. Use fewest words.
- **No Exploratory Sweeps.** Don’t try many endpoints/commands unless explicitly asked.
- **Inspect before Next.** Run one tool call, check result, then proceed.
- **After Tool Use:** Summarize result and stop if the task is done.
- **Style:** Friendly but terse (“caveman style”). No filler words, no long explanations.
- **Apply Patches:** Always use `apply_patch` for file edits; do not `git commit` or branch unless requested.
- **Testing:** Run minimal relevant tests (like unit tests) on changed code; skip heavy builds unless needed.
- **Destructive Ops:** Require confirmation. Use escalation (`--ask-for-approval`) if needed.

_Codex will read this before doing any work. Specific instructions here override general rules. Follow deeper `AGENTS.md` files if present._
```
*Rationale:* This AGENTS.md reiterates the “short and action‑focused” norms in project-local form. It echoes the system prompt’s rules (brevity, stepwise execution, patch use) so that if Codex reads it as part of startup (per its AGENTS.md discovery【5†L295-L304】), it reinforces caveman constraints. We cite [5] to remind that **nested AGENTS.md overrides shall apply to edits**【5†L295-L304】. This file is minimal to avoid drowning the agent in words; it’s essentially bullet points of the key rules.

## 3. Caveman Skill Snippet (user‑level injection)
```text
Respond terse like a smart caveman.  All technical substance stays; fluff dies. Drop filler words (just/really/um), pleasantries, hedges.  Use fragments and short terms.  E.g.: “Bug in auth: forgot `<` vs `<=`. Fix:”.  Stop at a single clear answer or patch.
```
*Rationale:* This micro‑prompt (to inject via a chat or tool) reminds the agent’s *current output style*. It echoes the official “Respond terse, all substance stay” motto from the Caveman SKILL【7†L366-L374】.  Use this if the agent deviates, to reset its style. It’s **not** the primary instructions (that’s the system prompt above) but a quick reminder.

## 4. Config Patches and Commands
- **Set model_instructions_file:** Modify `~/.codex/config.toml` (or QuantZhai’s Codex config) to point to our new prompt. E.g.:
  ```bash
  # in var/codex-home/config.toml
  model_instructions_file = "docs/qz-caveman-codex-model-instructions-v2.md"
  ```
  (Place the prompt text there.)  This overrides the default instructions.
- **Clamp output tokens:** Lower `model_max_output_tokens` for high/medium profiles (e.g. to ~1024) in `config.toml`【10†L18-L22】. Also in the proxy (`quantzhai_proxy.py`) set `n_predict`, `max_tokens` to ≤512 or ≤1024 as hard caps (we did this earlier). For example:
  ```diff
  - model_max_output_tokens = 4096
  + model_max_output_tokens = 1024
  ```
  and similarly in `[profiles.qwen36turbo-high]`.  This prevents excessively long answers.
- **Proxy caps:** In `quantzhai_proxy.py`, ensure code like:
  ```python
  body["n_predict"] = min(int(body.get("n_predict", 0) or 1024), 512)
  body["max_output_tokens"] = min(int(body.get("max_output_tokens", 0) or 1024), 512)
  ```
  (This enforces a 512-token hard stop on generation; we did analogous patches earlier.)

All changes are small text edits; no extra tools needed beyond your editor or `sed` as shown.

## 5. Testing and Metrics
**Test Prompts (and expected behavior):**
- *Prompt:* “Read `/tmp/outside.md`. Run at most one command if needed. Summarize in under 80 words.”
  *Expect:* One `cat` or `curl` command run, output is quickly summarized in ~1 sentence. No multi-step exploration.
- *Prompt:* “Check device at 10.0.42.34 with curl, time out at 5s.”
  *Expect:* Single `curl` with `--connect-timeout 5`, no loops. Summarize HTTP result.
- *Prompt:* “Please update `foo.py` to fix bug in line 10.”
  *Expect:* Agent uses `apply_patch` JSON on `foo.py`; final answer notes test results.

Monitor **qz-top** metrics during these tests:
- **Gen tokens / eval time:** A good prompt stays well under 512 tokens per step.  After clamping, `generate` should not exceed 512 and `eval time` should drop proportionally.
- **SSE completions:** Ensure each request yields a complete SSE `[DONE]`.  No hanging or missing `[DONE]` events.
- **CUDA/OOM:** Should be 0 errors.
- **Throughput tokens/sec:** Should rise if the model is not churning out 20K-token essays.  (The caveman prompt aims for ~70% fewer tokens【7†L278-L282】, so expect ~3× faster response time.)

If any test fails (e.g. agent still spawns multiple curls or ignores timeouts), adjust prompt wording or lower tokens further, then re-test.

## 6. Rollback Plan
If issues arise (e.g. agent too constrained or non-compliant):
- Restore original `model_instructions_file` (or remove that line) in `config.toml`.
- Restore original `AGENTS.md` (keep a backup as `AGENTS.md.bak`).
- Undo token cap changes (reset `model_max_output_tokens` and proxy caps to previous values).
- Restart Codex.

This reverts to the known-good state. Logging the changes with version control or notes ensures you can track exactly what was modified.

---

### Prompt-Variant Table (short/medium/long)

| Variant  | Token Budget | Allowed Behaviors                          | Risks                                     | Use-Case                           |
|----------|--------------|--------------------------------------------|-------------------------------------------|------------------------------------|
| **Short**  (e.g. ~200 tokens)  | 100–200      | *Only the very basics:* “One command, check result, short answer.”  State only minimal style rules. | May omit useful context (e.g. missing mention of `apply_patch` or sandbox rules). Agent might be too terse or forget a safety rule. | Very routine tasks (read one file, trivial fix), high-volume sessions, or when speed/cost is critical.  |
| **Medium** (~400–700 tokens) | 400–700      | Includes core persona (concise, safe, patch), one-step logic, no fluff. Allows a couple lines (like above prompt). | Still omits some detail (weaker on sandbox/escalation instructions). Agent might still drift slightly if not reminded. | General use (balance between safety and brevity). Good default for most tasks. |
| **Long**  (~900+ tokens)    | 800–1200     | Full instructions (consolidated default prompt) with caveman style. Covers capabilities, style, sandbox, AGENTS.md, patch, etc. | More verbosity; risk agent slipping into planning language or reasoning. Higher token cost. If too long, agent might resume old behaviors. | Complex tasks requiring more autonomy or when migrating from default prompt (as a transitional step). |

*Rationale:* We found that longer prompts reintroduce Codex’s original autonomy (risking verbose planning【5†L280-L284】) whereas very short prompts may leave out needed instructions. A medium-length prompt is recommended for day-to-day use. We illustrate the spectrum so you can choose based on task complexity: e.g. start with **Medium**; if agent seems confused, temporarily try **Long**; if still too verbose or slow, try **Short** with more turn-taking.

```mermaid
flowchart LR
    A[Apply caveman prompt file] --> B[Clamp token/output caps]
    B --> C[Restart Codex agent]
    C --> D[Test key scenarios (one-cmd tasks)]
    D --> E[Monitor qz-top metrics (tokens, SSE completion)]
    E --> F{Results OK?}
    F -- Yes --> G[Continue with caveman mode]
    F -- No  --> H[Rollback to original config]
```

Each step above should be implemented with small commits or config changes, so you can back out if needed.

**Sources:** We adapted the official Codex prompt (personality “concise, direct, friendly”【5†L280-L284】, AGENTS.md rules【5†L295-L304】, `apply_patch` instructions【10†L18-L22】【10†L25-L30】) into a much shorter form, and incorporated the Caveman skill motto【7†L366-L374】. We cite these to ensure fidelity to Codex’s design while enforcing brevity. The performance gains of terseness are well-known (up to ~3× speedup, ~70% token reduction【7†L278-L282】).
