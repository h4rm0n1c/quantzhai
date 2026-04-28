# Patch Tool Roadmap

Date: 2026-04-28

## Goal

Add `apply_patch` support without turning QuantZhai into an unsafe filesystem executor.

The near-term target is a protocol adapter: let the local model emit patch intent, translate that intent into the shape Codex expects, and let Codex keep authority over workspace writes, sandboxing, and approvals.

## Current State

- `proxy/quantzhai_proxy.py` recognizes native `{"type": "apply_patch"}` and custom `apply_patch` tools during tool normalization.
- Patch tools are translated into a normal function tool named `apply_patch` for llama.cpp.
- The model-facing function schema prefers an `operation` object with `type`, `path`, and optional `diff`.
- Follow-up `apply_patch_call` and `apply_patch_call_output` history items are translated back into function-call-shaped history before forwarding to llama.cpp.
- Current Codex CLI custom `apply_patch` traffic is also supported: custom declarations are translated to a function tool, and model output is translated back to `custom_tool_call` patch envelopes when Codex asked for that shape.
- Model output `function_call` items named `apply_patch` are translated back into native `apply_patch_call` items or custom `apply_patch` calls when they contain a valid operation.
- Streaming synthesis can emit `apply_patch_call` and `custom_tool_call` output items.
- The local tool loop currently executes `web_search` only.
- There is no local `apply_patch` executor.
- Patch adapter unit tests exist under `tests/test_apply_patch_adapter.py`.
- Proxy smoke coverage exists under `tests/smoke_apply_patch_proxy.py`.
- Codex CLI smoke coverage exists under `tests/smoke_apply_patch_codex_exec.py`.

This means QuantZhai now has a first-pass protocol adapter for native and current-Codex custom patch calls. It still does not apply files itself. Codex remains responsible for workspace writes.

## References

- OpenAI Apply Patch guide: `https://developers.openai.com/api/docs/guides/tools-apply-patch`
- OpenAI Responses API reference: `https://platform.openai.com/docs/api-reference/responses`
- Codex apply patch prompt grammar: `https://github.com/openai/codex/blob/main/codex-rs/core/prompt_with_apply_patch_instructions.md`
- OpenClaw apply patch docs: `https://docs.openclaw.ai/tools/apply-patch`
- OpenClaude repo: `https://github.com/Gitlawb/openclaude`
- OpenClaude path traversal advisory context: `https://www.sentinelone.com/vulnerability-database/cve-2026-35570/`

Treat OpenAI/Codex behavior as normative. Treat OpenClaw as practical implementation reference. Treat OpenClaude as a useful cautionary reference, not something to copy blindly.

## Design Principles

- Codex remains the authority for filesystem writes.
- QuantZhai should adapt protocol shapes before it executes filesystem changes itself.
- Workspace-only behavior is the default.
- Absolute paths, `..` traversal, symlinks that escape the workspace, and hidden alternate path encodings must be rejected if QuantZhai ever executes patches directly.
- Patch handling must be fixture-tested before being considered supported.
- Capture exact request and response shapes before implementing compatibility logic.
- Do not bypass Codex approvals or sandbox behavior.

## Phase 1: Capture Real Codex Traffic

Status: partly superseded by the first protocol adapter and smoke tests.

Create captures for a small Codex task that should naturally use `apply_patch`.

Capture:

- Raw incoming Responses request from Codex.
- Normalized forwarded request sent to llama.cpp.
- Raw upstream response from llama.cpp.
- Final response returned to Codex.
- Any dropped tools.

Useful capture files:

```text
var/captures/latest-request.json
var/captures/latest-forwarded.json
var/captures/latest-upstream-response.raw
var/captures/latest-dropped-tools.txt
```

Outcome:

- Confirmed the current Codex CLI uses `custom_tool_call` and `custom_tool_call_output` for `apply_patch`.
- Confirmed the proxy must remember whether the client asked for native or custom patch shape before normalizing tools for llama.cpp.
- Confirmed Codex can consume QuantZhai's custom patch output and apply a file edit in a temp workspace.

## Phase 2: Protocol Adapter

Status: first pass implemented.

Implement a tool adapter that can:

- Accept native OpenAI/Codex `apply_patch` tool declarations.
- Accept custom `apply_patch` declarations.
- Present a model-friendly function schema to llama.cpp when needed.
- Convert local model `function_call` output named `apply_patch` back to the Codex-compatible patch output shape.
- Preserve the original client tool shape in per-request metadata so the response can match what the client asked for.

Implemented:

- Native and custom tool declaration normalization.
- Native tool-choice normalization.
- Custom apply-patch tool-choice normalization.
- Patch-call history normalization.
- Patch-call-output history normalization.
- Function-call-to-`apply_patch_call` output normalization for valid operations.
- Function-call-to-`custom_tool_call` output normalization for the current Codex CLI.
- Streaming event synthesis for `apply_patch_call` and `custom_tool_call`.

Still pending:

- Preserve the original client tool shape in explicit per-request metadata.
- Capture and inspect a real live-model Codex patch workflow end to end.
- Confirm whether live Qwen reliably emits valid patch operations from the model-facing schema.
- Add more negative fixtures for invalid patch operations.

The first implementation does not write files. It only translates tool-call shape.

## Phase 3: Optional Local Patch Harness

Status: later, only if required.

Add a local parser and validator only if Codex cannot consume a returned patch call directly.

Required behavior:

- Parse the Codex freeform patch envelope.
- Support add, update, delete, and move operations.
- Validate all paths against the configured workspace root.
- Reject absolute paths and traversal.
- Reject patches that touch ignored runtime state unless explicitly allowed.
- Return structured success and failure output.
- Record changed paths and reject reasons in captures.

The local harness must be smaller and stricter than a general shell patch command. It should not run arbitrary commands.

## Phase 4: Tests

Status: started.

Add focused fixtures before broad refactoring:

- Tool declaration normalization: native patch, custom patch, function patch.
- Valid patch envelope conversion.
- Invalid patch grammar rejection.
- Absolute path rejection.
- `..` traversal rejection.
- Move operation handling.
- Duplicate or retried patch-call handling.
- Streaming event shape for patch calls.

These tests should not require Docker, Codex, GPU, or a live model.

Implemented so far:

- Unit tests for native/custom tool normalization, output conversion, input history conversion, invalid operations, and streaming event synthesis.
- Proxy smoke test with fake upstream covering native and custom output styles.
- Codex CLI smoke test with fake upstream proving current Codex consumes the translated custom `apply_patch` call and creates the expected temp file.

## Phase 5: Documentation

Status: started.

Once implemented, document:

- Whether QuantZhai only forwards patch calls or can execute them.
- What paths are allowed.
- What captures are written.
- What failure modes users should expect.
- How to smoke test patch support safely.

## Non-Goals

- Do not add arbitrary shell patch execution.
- Do not implement a general file manager through the proxy.
- Do not bypass Codex sandboxing or approval prompts.
- Do not vendor another project's patch implementation.
- Do not claim patch support until a real Codex patch workflow succeeds end to end.

## Maturity

Current maturity: alpha protocol adapter with passing smoke coverage.

The protocol path is now proven with fake upstreams and local Codex CLI. It is not fully supported until a live Qwen/TurboQuant run reliably emits valid patch operations and continues correctly from Codex's patch result.
