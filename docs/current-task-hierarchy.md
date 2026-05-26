# QuantZhai Current Task Hierarchy

Date: 2026-05-26
Status: active control sheet — #61 is the immediate unblocked work; #52 is upstream-blocked; #8 is long-term RFC; 3641 tests pass.

## Live apply_patch probe — linuxstreamtools /tmp clone (2026-05-26)

```text
Status: COMPLETE — live Codex/QuantZhai probe captured real apply_patch shape.

QuantZhai commit: ff74216.
Target: https://github.com/h4rm0n1c/linuxstreamtools, disposable clone at
        /tmp/qz-apply-patch-live/linuxstreamtools, origin/main 1864b99.
Model: Qwen3.6-27B-NEO-CODE-HERE-2T-OT-IQ4_XS.gguf.

Attempts:
  1. Tiny create-file prompt. Codex emitted apply_patch, but the local Codex
     sandbox was read-only, so the tool was rejected before the file was
     written.
  2. Explicit apply_patch prompt with workspace-write. Codex emitted
     apply_patch and created untracked QZ_APPLY_PATCH_PROBE.txt containing the
     requested single line.

Observed shape: operation_object in both attempts.
Fallback shapes: none observed; no sibling_patch_promoted, legacy_patch_envelope,
legacy_patch_with_path, or partial_custom_envelope occurred.

Telemetry:
  - coercion_succeeded fired in both attempts.
  - apply_patch.coercion_strategy=operation_object.
  - patch_present=false, path_present=true, diff_present=true,
    operation_type=create_file.
  - Telemetry stayed metadata-only; no raw patch body, raw diff, or raw file path
    leaked into telemetry.
  - Codex-visible forwarded SSE stayed custom_tool_call +
    response.custom_tool_call_input.*; no apply_patch_call,
    apply_patch_call_output, or response.apply_patch_call.* appeared.

Advisory decision: no model-visible advisory is needed for canonical
operation_object. #62 current scope complete. Future fallback-shape advisory
should be a new issue if telemetry later proves need.
```

## #62 closeout decision (2026-05-26)

```text
Status: COMPLETE — close #62 for the current apply_patch advisory audit scope.

Decision:
  - Advisory not implemented by design.
  - Telemetry is sufficient for the currently observed canonical
    operation_object shape.
  - Hard errors remain model-visible for invalid apply_patch calls.
  - Future advisory requires repeated live fallback evidence such as
    sibling_patch_promoted, legacy_patch_*, or other bad-but-coerced patterns.
  - Future fallback-shape advisory should be a new issue if telemetry later
    proves need.
```

## Recently completed — #62 Slice B.2 corrected (stream telemetry helper tests) (2026-05-26)

```text
Status: COMPLETE — tests/test_apply_patch_telemetry.py now exercises production telemetry helper.

Problem: Commit 6c8e7ac claimed stream telemetry integration coverage, but the
test simulated qz_responses_stream.py payload construction. It could pass even
if ResponsesStreamRuntime stopped emitting coercion telemetry.

Changes:
  - Extracted build_tool_coercion_telemetry_payload() in proxy/qz_responses_stream.py.
  - ResponsesStreamRuntime uses the helper at the existing coercion telemetry call site.
  - Replaced simulated tests with focused tests using real completed_call_decision()
    decisions plus the production helper.
  - Validated coercion_succeeded telemetry for sibling_patch_promoted path.
  - Validated coercion_failed telemetry for failed_missing_diff path.
  - Confirmed telemetry payload safety (exclusion of raw patch/path/diff content).
  - Confirmed no telemetry when coercion_applied is false.
  - Confirmed non-apply_patch coercion payloads do not attach apply_patch metadata.

Runtime behaviour: unchanged except telemetry construction is factored into the
helper used by both stream runtime and tests.

Remaining: none for #62 current scope. Future fallback-shape advisory should be
a new issue if telemetry later proves need.
```

## Remaining open work (tracked in dedicated issues)                                                    
                                                                                                           
   Gap                                                                      │ Issue / status
  ──────────────────────────────────────────────────────────────────────────┼────────────────────────────
   Advisory signals for native exec patterns (write loops, excessive calls) │ #61 OPEN — next immediate
   Backend-confirmed VRAM allocator metrics                                │ #52 OPEN — upstream-blocked
   Survival-weighted compaction RFC                                         │ #8 OPEN — long-term RFC

```text
Status: COMPLETE — commits 2bda8ca, 315103f, 76aca3c; 3629 tests pass.

Problem (issue #72):
  - ready logic was duplicated between ModelRouter and qz_control_plane
  - backend_reasoning_budget was missing from /qz/control-plane profile section
  - qz-top was falling back to local environment for rbudget instead of proxy value
  - status_snapshot() always returned ready=False when llama.cpp /v1/models returns
    status.value=null (backend_state="unknown") even when the model was loaded

Fixes:
  2bda8ca: Fix qz status readiness mismatch
    - status_snapshot() adds BackendManager snapshot fallback after llama.cpp check
    - When backend_state unknown but HTTP 200 + BackendManager phase=healthy +
      backend_health_ok=True + no launch_model_error → promote backend_state to
      "loaded" and ready=True
    - Mirrors existing override logic in build_control_plane_status()
    - 8 regression tests (StatusSnapshotBackendManagerFallbackTests)

  315103f: Surface backend_reasoning_budget in /qz/control-plane and qz-top
    - profile.backend_reasoning_budget added to /qz/control-plane payload
    - qz-top: model_status_from_control_plane prefers proxy rbudget (line 381-382)
    - 1 new test (test_profile_section_has_reasoning_budget)

  76aca3c: Fix qz-top backend reasoning budget parsing
    - Corrected parsing from control-plane profile section
    - 2 new tests (test_model_status_from_control_plane_success/fallback)

Residual drift documented (not fixed, acceptable):
  - State labels (unknown / not_loaded / loading / ready / loaded) are not fully
    normalised across ModelRouter, model_load_state, and backend_state. This is
    observational drift only; no routing or safety impact. Track in a future
    audit if it becomes confusing.

Validation:
  - python3 -m pytest: 3629 passed
  - StatusSnapshotBackendManagerFallbackTests: 8/8 passed
  - test_profile_section_has_reasoning_budget: passed
  - test_model_status_from_control_plane_*: 2/2 passed
  - git diff --check: clean

#72 CLOSED at HEAD 76aca3c.
```

## Recently completed — Thinking budget controls (2026-05-26, commits 45cb522–6a1139d)

```text
Status: COMPLETE — 3 commits, 62+ tests added, 3629 total pass.

Two-axis reasoning control system implemented:

  thinking_mode: auto | thinking | non_thinking
    - normalize_thinking_mode() normalises aliases
      (think/on/true → thinking; instruct/off/none → non_thinking)
    - Source: explicit profile override > model-name heuristic > auto
    - Coder/Instruct names → non_thinking
    - Qwen3.6/A3B/thinking names → thinking
    - Profile runtime.thinking_mode flows through catalog entry overrides

  reasoning_effort × thinking_mode → per-block token budgets:
    low=16384  medium=24576  high=32768  xhigh=49152

  OAI path fix (commit 53eff6b):
    server-common.cpp reads thinking_budget_tokens (not reasoning_budget_tokens)
    on the /v1/responses OAI path. Without thinking_budget_tokens the budget was
    silently ignored (QZ_REASONING_BUDGET=-1 keeps reasoning_budget at -1 until
    per-request override fires). Fix: mirror resolved budget to both field names.

  Documentation (commit 6a1139d):
    - docs/thetom-oai-responses-compat.md: NEW — two-path field name mismatch,
      server-common.cpp budget logic, startup budget interaction
    - docs/qwen-reasoning-effort-policy.md: updated (old policy said strip
      thinking_budget_tokens; new policy says mirror both field names)
    - docs/README.md: indexed new doc

Key invariants:
  - QZ_REASONING_BUDGET=-1 must stay as default for per-request control to work
  - non_thinking models: no budget fields forwarded
  - auto mode: safe fallback, no injection
  - Caller overrides preserved via setdefault (caller value wins)
```

## Recently completed — Codex tool contract audit close-out (2026-05-26, #66–#70)

```text
Status: COMPLETE — commit 5c23924; 3565 tests passed at audit close; qz-live-smoke passed.

Completed audit slices:
  #66: fake lifecycle removal + real Codex source contracts
  #67: local_shell + tool_search contracts
  #68: shell + container.exec contracts
  #69: document-only bucket audit
  #70: final audit pass — CODEX_NATIVE_TOOL_NAMES final at 12 tools

Adapter backlog is demand-driven; no further audit slices planned.

Key rules locked:
  - Item type is in SSE DATA PAYLOAD (item.type), not in event name
  - No fake web_search_call.* sub-lifecycle events
  - apply_patch always emits custom_tool_call (not apply_patch_call)
  - Deterministic C1 smoke confirms web_search contract without live model
```

## Recently completed — Fix web_search contract smoke proof (2026-05-25, issue #66 follow-up)

```text
Status: COMPLETE — deterministic C1 smoke + 23 tests + docs; 3506 tests pass.

Problem (f94d967 follow-up):
  qz-web-search-codex-contract-smoke Section C detected web_search by scanning SSE
  event names for "web_search_call". Under the corrected Codex contract, the events
  are response.output_item.added/done with item.type=web_search_call in the DATA
  PAYLOAD — not in the event name. The scan always returned [] and Section C always
  SKIPped with "model did not call web_search on this run".

Root cause:
  web_search_events = [e for e in events if "web_search_call" in e]
  Wrong: looks at event names.  No event name contains "web_search_call".
  Correct: check payload["item"]["type"] == "web_search_call".

Changes:
  scripts/qz_web_search_contract_check.py (NEW):
    parse_sse_events_with_payloads(stream_text) — payload-aware SSE parser
    check_contract(events, mode) — enforces Codex contract checks against parsed events
    run_deterministic(repo_root) — uses ResponsesStreamRuntime + _FakeStream +
      _FakeWebRuntime; always pass/fail, never skip
    run_live(proxy_base, model, timeout) — HTTP check against real proxy; may skip
    run_self_test() — 6 internal test groups
    CLI: --mode=deterministic|live|self-test

  scripts/qz-web-search-codex-contract-smoke:
    Section C replaced with:
      C1: Deterministic — calls qz_web_search_contract_check.py --mode=deterministic
          Always PASS or FAIL, never SKIP. No live model required.
      C2: Opportunistic live — calls helper --mode=live
          SKIP if model does not call web_search (opportunistic, not a proof).
    Updated wording: no more "lifecycle", "lifecycle events missing".
    C2 WAIT_BACKEND polling and 503 classification preserved.

  tests/test_qz_web_search_contract_check.py (NEW, 23 tests):
    ParseSSEEventsTests (5):
      parser returns (event_name, payload) tuples
      no event name contains "web_search_call" (it's in item.type not event name)
      [DONE] sentinel parsed correctly
      multiple events all parsed
      fallback to payload.type when no event: line
    CheckContractTests (11):
      correct contract → pass
      detection via item.type, not event name → pass
      fake in_progress/searching/completed → fail (each)
      missing added/done/completed → fail (each)
      no web_search_call in deterministic → fail
      no web_search_call in live → no_search (not fail)
      message item does not count as web_search_call
      all fake absent flags true when none present
    DeterministicContractTests (4, integration):
      run_deterministic() → pass
      mode field = "deterministic"
      all checks True
      _FakeWebRuntime returns correct structure
    FakeStreamTests (2): reads all lines, close() sets closed

Contract rule confirmed:
  Item type is in the SSE DATA PAYLOAD (item.type), not in the event name.
  Live model call is prompt-dependent and cannot be the only smoke proof.
  Deterministic smoke is required for issue #66 closure.

Full suite: 3506 passed.
```

## Recently completed — Replace fake lifecycle with Codex source contracts (2026-05-25, issue #66)

```text
Status: COMPLETE — commit f94d967.

Removed fake ToolLifecycleSpec subevent system (lifecycle_event_prefix, lifecycle_start_stages,
lifecycle_done_stages). Removed ProxyLocalToolRegistry fake lifecycle methods.
Removed public_tool_lifecycle_event() / web_search_call_lifecycle_event() from qz_streaming.py.

Added custom_tool_call_input_events() for real apply_patch streaming (custom_tool_call_input.delta/done).
Removed computer from CODEX_NATIVE_TOOL_NAMES (reserved namespace only, not a handler).

Created docs/codex-source-tool-contract.md (authoritative Codex-source event contract).
Renamed scripts/qz-web-search-lifecycle-smoke → scripts/qz-web-search-codex-contract-smoke.

All fake web_search_call.* assertions converted to assertNotIn.
3483 tests passed after f94d967.
See docs/codex-source-tool-contract.md for full contract.
```

## Recently completed — Remove fake apply_patch_call contract (2026-05-25)

```text
Status: COMPLETE — proxy + tests + smoke + docs updated; 3484 tests pass.

Output:
  proxy/qz_tool_apply_patch.py — removed _apply_patch_output_style, _apply_patch_output_to_function_output,
    _function_call_to_apply_patch_call, _extract_partial_native_operation;
    output_to_codex() always returns custom_tool_call; apply_patch_output_style removed from policy
  proxy/qz_proxy_tools.py — removed apply_patch_output_style param from completed_call_decision
  proxy/qz_responses_stream.py — removed apply_patch_output_style param from run() and completed_call_decision
  proxy/qz_request_router.py — removed _apply_patch_output_style import and all call sites
  proxy/qz_sse.py — removed apply_patch_call branch from make_response_stream_events
  proxy/qz_responses.py — removed apply_patch_call / apply_patch_call_output from FUNCTION_CALL_TYPES / FUNCTION_OUTPUT_TYPES
  tests/test_apply_patch_adapter.py — removed native-mode tests, updated all assertions to custom_tool_call
  tests/test_qz_responses_stream.py — removed native-mode tests, removed apply_patch_output_style from helpers
  tests/test_qz_proxy_tools.py — updated apply_patch_call → custom_tool_call assertion
  tests/test_qz_tools.py — removed apply_patch_output_style arg, updated assertion
  tests/test_qz_tool_lifecycle.py — updated three apply_patch_call → custom_tool_call assertions
  tests/test_qz_tool_request.py — removed apply_patch_output_style assertion
  tests/smoke_apply_patch_proxy.py — check custom_tool_call, fail on apply_patch_call
  tests/fixtures/responses_input/*.json — removed apply_patch_output_style from expected_policy
  docs/apply-patch-codex-lifecycle-audit.md — contract history note, updated all sections

Why: Codex source audit confirmed Codex expects custom_tool_call with name=apply_patch
and input="*** Begin Patch...". apply_patch_call was a mistaken/hallucinated contract.
PatchApplyBegin/Updated/End are Codex-internal UI events, NOT Responses SSE event names.
Zero users of the native path. No legacy preservation.

Contract change: apply_patch always emits custom_tool_call. apply_patch_output_style
removed from policy. History normalisation uses custom_tool_call_output (not apply_patch_call_output).
```

## Recently completed — Add safe apply_patch telemetry metadata (2026-05-25)

```text
Status: COMPLETE — helper + telemetry enrichment + tests; no runtime behaviour changes.

Output:
  proxy/qz_tool_apply_patch.py — inspect_apply_patch_arguments() helper
  proxy/qz_responses_stream.py — "apply_patch" nested dict in coercion events
  tests/test_apply_patch_adapter.py — ApplyPatchAP3InspectTests (16 tests)
  tests/test_qz_responses_stream.py — ApplyPatchStreamingAP3Tests (10 tests)
Reference: docs/apply-patch-codex-lifecycle-audit.md §AP3

Changes:
- inspect_apply_patch_arguments(arguments: str) -> dict added to qz_tool_apply_patch.py.
  Returns only safe fields: booleans and fixed enum strings. No raw content escapes.
  Safe fields: args_shape, operation_present, patch_present, path_present, diff_present,
  destination_present, operation_type, coercion_strategy.
  operation_type is enum-clamped: known values, "unknown", or "missing" — never raw string.
- coercion_succeeded / coercion_failed telemetry in qz_responses_stream.py now
  includes "apply_patch" nested dict when tool == "apply_patch". Other tools unaffected.
- No Codex-visible lifecycle changes. No model-visible error text changes.
  BrainCase remains inactive. Watchdog remains disabled. Web_search unchanged.

Total tests added this pass: 26. Full suite: 363 passed (adapter + stream files).

AP3 adapter tests (ApplyPatchAP3InspectTests):
  empty string → args_shape=empty; failed strategy
  invalid JSON → args_shape=invalid_json; no raw args
  missing diff → operation_type=update_file; path_present; coercion_strategy=failed_missing_diff
  missing destination → operation_type=move_file; coercion_strategy=failed_missing_destination
  sibling patch → operation_present; patch_present; coercion_strategy=sibling_patch_promoted; no raw body
  legacy envelope → patch_present; coercion_strategy=legacy_patch_envelope; no raw body
  operation_object → coercion_strategy=operation_object; no raw content
  unknown type → operation_type="unknown" (not raw value)
  delete_file → operation_object; no diff/destination required; safe types

AP3 streaming tests (ApplyPatchStreamingAP3Tests):
  coercion_failed includes "apply_patch" nested dict with required fields and correct strategy
  coercion_succeeded includes "apply_patch" nested dict with correct strategy
  no raw patch body/path/diff in nested dict on failure or success
  all leaf values are bool or str
  web_search telemetry has no "apply_patch" key
  "apply_patch" key appears only when tool == "apply_patch"

Deferred slices (require live Codex capture or non-trivial changes):
- AP4: live Codex capture to observe apply_patch_call rendering.
- AP5: optional sub-lifecycle if AP4 proves benefit (requires protocol_adapter → proxy_local mode change).
```

## Recently completed — Enforce apply_patch lifecycle contract (2026-05-25)

```text
Status: COMPLETE — tests only; no runtime behaviour changes.

Output: tests/test_qz_responses_stream.py (ApplyPatchLifecycleContractTests extended,
        ApplyPatchStreamingAP2Tests added), tests/test_apply_patch_adapter.py (non-streaming AP1 test).
Reference: docs/apply-patch-codex-lifecycle-audit.md (gap matrix and slice sections updated).

Changes:
- AP1 lifecycle contract locked with 8 additional streaming assertions:
    status progression in_progress → completed
    call_id preserved in both native and custom mode
    operation field present
    diff no unified diff headers
    no file_search_call.* or code_interpreter_call.* events
    custom mode input contains *** Begin Patch envelope
    custom mode call_id preserved
- AP1 non-streaming path: test_ap1_non_streaming_path_emits_no_apply_patch_sub_lifecycle_events
  confirms make_response_stream_events emits none of the forbidden sub-lifecycle event names.
- AP2 streaming integration class (ApplyPatchStreamingAP2Tests, 10 tests):
    invalid JSON → function_call_output error injected on next hop
    invalid JSON → no output_item.* for failed call
    missing diff → specific repair text in error; path not echoed; call_id preserved
    missing destination → specific repair text; path not echoed; call_id preserved
    unknown operation type → specific repair text; path not echoed; call_id preserved
    sibling patch promotion → coercion_succeeded; apply_patch_call in stream; single hop
    legacy *** Begin Patch envelope → coercion_succeeded; operation extracted; single hop
    coercion_failed telemetry: no raw patch body; error_summary ≤200 chars; safe fields only

Total tests added this pass: 19. Full suite: 3477 passed.

Closed gaps from docs/apply-patch-codex-lifecycle-audit.md §8:
- G-AP1 no sub-lifecycle locking test → CLOSED (AP1 streaming tests)
- G-AP2 no safety test for raw arg leakage → CLOSED (AP2 streaming tests)
- Sibling patch promotion streaming → CLOSED
- Legacy Begin Patch envelope streaming → CLOSED
- Invalid JSON / missing diff / missing dest / unknown op streaming → CLOSED
- Non-streaming path sub-lifecycle absence → CLOSED
```

## Recently completed — Audit apply_patch Codex lifecycle (2026-05-25)

```text
Status: COMPLETE — audit only; no runtime behaviour changes.

Output: docs/apply-patch-codex-lifecycle-audit.md

Key findings:
- apply_patch uses execution="protocol_adapter" (not proxy_local) — Codex applies
  the patch locally; QuantZhai only translates the shape.
- No official response.apply_patch_call.* event family exists in the Responses API.
  QuantZhai must not invent these events without live Codex client proof.
- Only response.output_item.added/done are emitted (item types: apply_patch_call or
  custom_tool_call depending on output style). No sub-lifecycle stages.
- No tool_call_started/tool_call_completed telemetry — those are proxy_local only.
- coercion_succeeded/coercion_failed emitted via tool_adapter source.
- Error messages are specific (bad JSON / unknown op / missing diff / missing dest);
  no raw argument content echoed.
- call_id preserved in function_call_output errors.
- Partial native/custom envelope fallback is intentional (triggers Codex V4A error
  rather than proxy silent drop).

Slices added:
- AP1: ApplyPatchLifecycleContractTests (13 tests) — locks that no sub-lifecycle
  events are emitted; covers both native and custom modes.
- AP2: ApplyPatchAP2CoercionSafetyTests (12 tests) — locks error specificity,
  call_id preservation, and no raw arg leakage.

Slice L3 from codex-visible-tool-lifecycle-audit.md marked done (superseded by AP1).

Total new tests: 25. Full suite: 3458 passed.
```

## Recently completed — Clarify search backend smoke status (2026-05-25)

```text
Status: COMPLETE

Problem: stocktake printed SEARXNG_BASE_URL=http://10.0.42.222:8085 with no label,
making it look equally authoritative. First post-qz-up smoke run failed with HTTP 503
(backend still starting) but reported as a hard lifecycle failure.

Changes:
1. scripts/qz-agent-infra-stocktake:
   - Feature flags section split: BrainCase/watchdog vars stay as simple list;
     search backend vars shown in a separate block with resolution priority.
   - SEARXNG_BASE_URL annotated as "(legacy/lower-priority; not used while
     QZ_SEARCHENGINES_BASE_URL is set)" when it differs from resolved base.
   - "Resolved Searchengines Agent Base" row shows the actual URL QuantZhai uses.
   - _resolve_agent_api_base() moved to module level (no local duplicate).

2. scripts/qz-web-search-lifecycle-smoke:
   - Banner now shows "legacy_searxng=... (ignored for Agent API smoke)" only when
     SEARXNG_BASE_URL is set and differs from resolved AGENT_BASE.
   - Added --wait-backend SECONDS: polls /qz/model/status until ready, then runs C.
   - Section C Python snippet: urllib.error.HTTPError 503 now handled separately.
     Queries /qz/model/status (timeout=5) to classify:
       not_ready                → model not ready → skip (startup timing, not failure)
       error_503_despite_ready  → model ready but 503 → fail (admission bug)
   - Bash case statement: added not_ready (skip) and error_503_despite_ready (fail)
     cases. Existing partial/error/no_search behaviour unchanged.

3. tests/test_qz_search_config.py: added StocktakeLabelTests (5 tests) and
   Lifecycle503ClassificationTests (3 tests) covering:
   - canonical var wins and shows correct legacy label
   - alias var wins and shows correct legacy label
   - same value → "same as resolved Agent API base"
   - no legacy → "(unset)"
   - 503 + model_ready=False/None → not_ready
   - 503 + model_ready=True → error_503_despite_ready

4. docs: updated codex-visible-tool-lifecycle-audit.md and
   web-search-provider-architecture-audit.md with env var priority table,
   503 classification logic, and --wait-backend documentation.

All tests pass. bash -n on smoke: clean.
```

## Recently completed — Use searchengines Agent API facade as canonical web_search backend (2026-05-25)

```text
Status: COMPLETE

Problem: web_search was hitting http://10.0.42.222:8085 (raw SearXNG, no /guidance)
because SEARXNG_BASE_URL was set to raw SearXNG, and the proxy/smoke used only that var.

Changes:
1. proxy/qz_search_config.py: added QZ_SEARCHENGINES_DEFAULT_BASE_URL constant and
   resolve_searchengines_base_url(env) helper.
   Priority: QZ_SEARCHENGINES_BASE_URL > SEARXNG_AGENT_API_BASE > SEARXNG_BASE_URL
   > http://127.0.0.1:8890. load_search_config() env-override block now uses same
   priority chain.

2. proxy/quantzhai_proxy.py: --searxng-base-url default now calls
   _resolve_searchengines_base_url() (not plain SEARXNG_BASE_URL). Fallback in
   _initialize_proxy_state also calls resolver.

3. proxy/qz_tool_web.py: build_web_search_capabilities() providers key renamed
   "searxng" → "searchengines_agent"; added guidance_available field.
   provider_id changed from "searxng" to "searchengines-private" (consistent
   with guidance response).

4. scripts/qz-env: added QZ_SEARCHENGINES_BASE_URL and SEARXNG_AGENT_API_BASE to
   _qz_env_names override array and default exports. SEARXNG_BASE_URL now defaults
   to QZ_SEARCHENGINES_BASE_URL (8890) instead of being independently set.

5. scripts/qz-web-search-lifecycle-smoke: AGENT_BASE now always resolved from
   QZ_SEARCHENGINES_BASE_URL > SEARXNG_AGENT_API_BASE > 8890 default (never
   SEARXNG_BASE_URL which may point to raw SearXNG). Removed unreachable
   if [[ -z "$AGENT_BASE" ]] guard from Section B.

6. scripts/qz-agent-infra-stocktake: guidance check now uses same 4-step priority
   resolution; labeled "Searchengines Agent Guidance"; shows resolved URL. Feature
   flag section now shows QZ_SEARCHENGINES_BASE_URL and SEARXNG_AGENT_API_BASE.

7. tests/test_qz_tool_web.py: updated 3 tests asserting providers["searxng"] →
   providers["searchengines_agent"].

8. tests/test_qz_search_config.py: added SearchenginesBaseUrlResolutionTests (8 tests)
   covering all resolution priority combinations and load_search_config integration.

9. docs/web-search-provider-architecture-audit.md: updated Section 10 capabilities
   schema to show searchengines_agent key (not searxng) with guidance_available field.

241 tests pass. bash -n on smoke script: clean.

Key invariant: QuantZhai never directly addresses raw SearXNG. All outbound
search/guidance/retrieve calls go to the searchengines Agent API facade.
SEARXNG_BASE_URL remains available for backward compat but is now treated as an
alias for the Agent API facade, not raw SearXNG.
```

## P0/P1: Web Search Provider Boundary Decoupling — COMPLETE

```text
Changes:
1. qz_tool_web.py: _profile_source_strict — added guidance_source_strict parameter.
   Priority: local config → provider guidance → deprecated compat fallbacks
   (furry_fse by name, explicit engines=["fse"]).

2. qz_tool_web.py: WebSearchRuntime — added _fetch_provider_guidance_cached() and
   _get_guidance_source_strict(). Generic /guidance endpoint; any Agent API may
   expose it. Failures are warnings, not fatal. Cached for 120s.

3. qz_tool_web.py: _search_web — now passes guidance_source_strict to source_strict
   computation from cached guidance (no network per search).

4. qz_tool_web.py: build_web_search_capabilities — removed all searchengines-specific
   hard-coded lore:
   - Removed sofurry_in_probe → SoFurry warning block
   - Removed "SoFurry is not configured..." from usage_notes
   - Removed "furry_fse: source-strict...", "furry_images: image metadata...",
     "furry: mixed convenience..." from usage_notes
   - Removed hard-coded provider_preference=["fse_direct", "searxng_fse"] for furry_fse
   - Removed fse_direct from static providers_info
   - Removed fse_direct-specific probe warning
   Added:
   - Generic "source-strict profiles enforce exact engine matching..." usage note
   - provider_guidance section in return dict (available, provider_id, schema,
     profiles_present, warnings, fetch_warnings)
   - Per-profile provider_guidance fields merged from guidance (purpose, use_when,
     do_not_use_for, hard_rules, retrieval_guidance, source_strict, provider_preference)
   - Guidance-provided providers merged into providers section

5. docs/search-provider-boundary-audit.md: full KEEP_GENERIC / KEEP_FALLBACK /
   MOVE_TO_PROVIDER_GUIDANCE / REMOVE_LEAK classification for all searchengines-
   specific items. Documents boundary rules and /guidance expected schema.

6. 11 new tests in WebSearchProviderGuidanceTests. 209 total in test_qz_tool_web.py.
   Updated 8 existing tests to reflect removed hard-coded lore.

Key principle: QuantZhai is a generic search/retrieval orchestrator. Provider-specific
guidance (FSE quirks, SoFurry restrictions, fse_direct availability, per-profile
usage prose) belongs in searchengines-private /guidance endpoint. QuantZhai fetches
and passes it through without hard-coding the content.
```

## P0/P1: Web Search Provider Architecture Audit + Hardening — COMPLETE

```text
Changes:
1. qz_tool_web.py: _query_searxng — add provider trace metadata to every result:
   provider_id, provider_reported_count (DIAGNOSTIC ONLY, never routing),
   parsed_result_count, count_mismatch, warnings.
   Log latest-web-search-provider-raw-summary.json after each SearXNG call.
2. qz_tool_web.py: _search_web — add:
   - latest-web-search-request.json before provider call (engines before/after filter)
   - latest-web-search-normalized.json after source-strict filtering
   - accepted_result_count in all routing decisions
   - warnings list merged from provider + source-strict filter
   - route_log now includes provider_id, all count fields, warnings
3. qz_tool_web.py: build_web_search_capabilities — add providers section:
   searxng, fse_direct (unavailable), agent_retrieve.
   furry_fse gets provider_preference: ["fse_direct", "searxng_fse"].
   Warning when fse_direct absent AND SearXNG fse absent from probe.
4. New audit doc: docs/web-search-provider-architecture-audit.md
   - Full architecture map and data-flow diagram
   - Honest fse_direct finding: no direct FSE search in ~/searchengines/
   - Known-good direct invocation: searxng-query.sh --engine fse
   - Operator debugging commands for same-query comparison
   - Count semantics table
   - fse_direct future contract
5. 17 new tests. 198 total in test_qz_tool_web.py.

Key findings from audit:
- ~/searchengines/ has no direct FSE search script. FSE searching runs through
  SearXNG's fse engine module. fetch-fse-story.py is retrieval-only (single story).
- SearXNG number_of_results=0 metadata bug was not causing routing failures
  (routing already used parsed count) but was invisible. Now captured as
  count_mismatch with warning.
- Debugging was blind: raw provider request/response counts, engine filter
  decisions, and source-strict discard reasons were not captured in trace logs.
  All four trace log files now written on every search.
```

## P0/P1 Fix: furry_fse source-strict search — COMPLETE

```text
Changes:
1. config/default/search.json: furry_fse now declares source_strict=true,
   expected_engines=["fse"], expected_domains=["fse.anthro.fr"],
   expected_retrieval_sources=["fse"], fallback_profiles=[].
2. qz_tool_web.py: _profile_source_strict() — true for furry_fse by name,
   any profile with source_strict=true, or explicit_engines=["fse"] only.
3. qz_tool_web.py: _result_matches_expected_source() — validates result
   against expected engines/domains/retrieval_sources.
4. qz_tool_web.py: _search_web — enforces no-fallback + wrong-source
   result filtering for source-strict profiles. All wrong-source results
   are discarded with a clear warning; SearXNG engine fallback to
   DuckDuckGo/general cannot silently broaden an FSE search.
5. qz_tool_web.py: capabilities now advertise source_strict=true and
   fallback_profiles=[] for furry_fse. Warning emitted when fse engine
   is probe-absent. usage_notes updated.
6. route_log: now includes source_strict, expected_engines,
   expected_domains, expected_retrieval_sources,
   wrong_source_results_discarded, fallback_suppressed_reason.
7. 28 new tests. 3313 total tests pass.
```

## P0/P1 Fix: Control-plane direct backend readiness — COMPLETE

```text
Changes:
1. qz_control_plane.py: Move readiness dict + _overall_status computation to
   AFTER model_status processing.  Previously readiness was built with stale
   router-probe values, then model_status updated backend_ready/backend_reachable
   locally but didn't update the already-frozen readiness dict.  This caused:
     status="model_not_loaded" while backend.ready=True (internally inconsistent)
     readiness.backend_ready=False while service_status.model_state=loaded
     stale "no model is loaded" operator hint
2. qz_control_plane.py: Use model_status.model_switch_state directly (already
   carries the bd41930 "loaded" override) instead of re-deriving from
   _derive_model_switch_state (which lacked the override).
3. qz_model_router.py: _persist_model_state: when source="status_snapshot",
   never overwrite a canonical existing source (operator, qz_codex, fallback, etc.)
   regardless of backend_id match.  Status_snapshot is observational; it must
   not demote deliberate operator selections.
4. scripts/qz-top: Defensive fallback in model_status_from_control_plane:
   if selected_state="not_loaded" but service_status.model_state="loaded",
   use "loaded" so STATE is never stale when service_status is authoritative.
5. 15 new tests. 3285 total tests pass.
```

## P0 Fix: Direct -m loaded model reconciliation — COMPLETE

```text
Changes:
1. qz_backend_manager.py: GPU log check now combines stdout+stderr.
   docker logs sends container-stderr to client-stderr; llama-server writes
   GPU offload messages to stderr.  Combining both streams means "offloaded N/N
   layers to GPU" and "CUDA0 model buffer size" are now correctly detected.
   PRIMARY fix: resolves phase never reaching HEALTHY due to unknown_after_retries
   when docker logs returned empty stdout and GPU lines were in stderr.
2. qz_model_status.py: Remove last_load_result != "failed" gate from
   selected_model_ready in direct mode.  BackendManager health IS the load
   confirmation for direct -m launches; router-era last_load_result history
   must not block a currently-healthy backend.
3. qz_model_status.py: Override model_switch_state to "loaded"/"none" when
   selected_model_ready is True.  Prevents stale "idle" state from appearing
   even when the backend is confirmed healthy.
4. qz_model_status.py: _recommended_action now detects active loading phase
   (backend_phase=running + launch_model_key populated) and returns "loading"
   message instead of "POST /qz/model/reload" instruction.
5. qz_model_status.py: Added backend_loaded_model_source field to output.
   "direct_launch" when backend_loaded_model comes from BackendManager health;
   "unknown" when no loaded model.
6. quantzhai_proxy.py: Normalize status_snapshot source in _preload_last_model.
   On proxy restart, if model-state.json has selected_source=status_snapshot
   (reconciliation-written, non-canonical), upgrade to "fallback" so status_snapshot
   does not persist as a permanent authority label.
7. scripts/qz-up: Bounded wait (up to 8s) for backend status to leave idle
   before printing startup message.  Prevents premature "no launch model resolved"
   when autostart hasn't triggered yet from a stale immediate read.
8. 56 new tests covering all new behaviors.
3270 total tests pass.

Known root cause: llama-server writes all model-load diagnostics to stderr.
docker logs routes container-stderr to subprocess-stderr, which the GPU log
checker was discarding.  After fix, GPU offload patterns are found and the
health loop completes, transitioning phase to HEALTHY.
```

## P0 Fix: Backend autostart and model preload contract — COMPLETE

```text
Changes:
1. quantzhai_proxy.py: Synchronized launch model resolution in _initialize_proxy_state.
   _preload_last_model() now called before marking proxy as ready.
   Ensures /health waits for resolution and autostart enqueuing.
2. quantzhai_proxy.py: _resolve_launch_model_entry uses load_model_state
   for consistent resolution with migration support (re-admitting QZ_MODEL_KEY
   as seed when persisted selection is missing or empty).
3. qz_backend_manager.py: Enforce launch model check in start() and restart().
   Returns immediate error if no launch model is set, preventing background
   failure state.
4. qz_model_status.py: Tightened model_switch_state derivation.
   Prevents fake "loaded" state when backend is idle/stopped.
5. qz_model_router.py: Prevented status reconciliation from persisting
   empty/drifting selections during startup/scan races.
6. scripts/qz-up: Honest backend startup message based on proxy status.
   Distinguishes starting vs idle vs GPU-blocked.
7. scripts/qz-top: Fixed PROXY OFFLINE label to only show when proxy fetch
   actually fails, not when backend is unreachable.
8. qz-codex: Extended model preflight to interactive mode.
   Verifies model choice against backend active selection before launch.
9. 3 new tests: backend manager invariants and synchronous start failure.
3256 total tests pass.
```

## P0 Fix: GPU launch contract and admission gate — COMPLETE

```text
Changes:
1. BackendManager: retry GPU log check (default 5×2s) when require_gpu=True and
   state is "unknown" — closes race where health passes before model-load logs appear.
   After retries: unknown_after_retries → FAIL (not admitted).
2. BackendManager: gpu_observed field added to BackendState/snapshot.
3. qz_model_status.py: selected_model_ready gates on gpu_offload_state.
   cpu_fallback/failed/unknown_after_retries → not ready.
   request_admission_state="failed_gpu_not_available" for these states.
   gpu_required/gpu_offload_state/gpu_observed exposed in /qz/model/status.
4. 27 new tests: launch contract drift detection, admission gate, retry logic.
5. Smoke plan: mandatory GPU preflight gate added.
3253 total tests pass.
```

## Fix Pass M2: live smoke — RED (GPU not loaded)

```text
Status: RED — GPU not loading after restart in this agent session.

Fresh proxy started from c5799bc HEAD.
Fix Pass K/L freshness confirmed.
19 PASS (code/unit), 1 FAIL (P0: GPU not loaded).

P0 FAIL: After qz-down --force && qz-up, both GPUs show ~0 MiB VRAM.
Model ran CPU-only. S2.1 eventually completed but system is unusable
at this speed. Not a code regression — operator must investigate
qz-docker-quantzhai GPU flag handling in agent session context.

Results: docs/end-to-end-smoke-results.md
Previous stale run (c5799bc): marked INVALID.

Operator actions required:
1. Run scripts/qz-up from an operator terminal (not agent).
2. Confirm VRAM rises to ~23 GB total.
3. Re-run Groups 1/2/3/7 from docs/end-to-end-smoke-plan.md.
4. Upgrade verdict to YELLOW/GREEN if GPU confirmed.
```

## Fix Pass M: final live smoke rerun — COMPLETE (YELLOW)

```text
Status: COMPLETE — automated portion done. Operator live run required for GREEN.

Pytest preflight: 3226 PASS, 0 FAIL.
Smoke matrix: 14 PASS, 5 PASS_WITH_NOTE, 1 FAIL, 16 SKIP, 1 BLOCKED.

Results: docs/end-to-end-smoke-results.md

FAIL (1):
  S1.4 — selected_model_ready=None because running proxy is pre-Fix-Pass-K.
  Fix: scripts/qz-down --force && scripts/qz-up.
  Not a code regression — stale running process.

Key verified (via tests/code path):
  ✅ Tool schema replacement/dedup (H)
  ✅ Coercion/advice telemetry (H)
  ✅ response.id threading no-tool and multi-hop (I)
  ✅ Zero-usage synthetic telemetry (I)
  ✅ output_text artifact detection all paths (J)
  ✅ qz-thoughts new event rendering (K)
  ✅ furry_images retrieval_expected=False (L)
  ✅ Capabilities probe availability warnings (L)
  ✅ SoFurry absent confirmed (L)
  ✅ explicit FSE engines override (L)

Not yet live-verified (require operator):
  - Groups 1/2/3/7 require backend loaded + observer terminals
  - Control-plane profile fields require proxy restart
  - FSE/furry_images require local SearXNG with engines

Overall: YELLOW — code-complete, test-complete, operator live run needed
before declaring GREEN for production use.
```

## Recently completed — Fix Pass L: search profile/capabilities fixes

```text
Status: COMPLETE

Changes:
1. config/default/search.json: furry_images gains retrieval_expected=false
   and retrieval_kind="image_metadata". Prevents capabilities from claiming
   prose retrieval for image tag/rating engines (e926/furbooru).

2. proxy/qz_tool_web.py build_web_search_capabilities:
   - Per-profile: engine_availability_known, effective_engine_count,
     blocked_engines, probe_unavailable_engines (when probe data is present).
   - Global warning: "Engine availability has not been probed" when no probe.
   - Per-profile warning: "Profile X has no available engines" when all engines
     are filtered after policy+probe.
   - SoFurry: if probe detects sofurry but no profile is configured, warning
     "SoFurry engine detected but not configured". Silent otherwise.
   - usage_notes: explicit engines override note, furry_fse/furry_images
     retrieval distinction, SoFurry absent note.

Confirmed correct:
- e926/furbooru/fse are NOT in non_text suppression list (verified).
- furry_fse retrieval_expected remains True (FSE Agent API prose).
- furry remains documented as mixed convenience profile.
- SoFurry absent from config; no fake profile added.
- FSE explicit override profile="furry", engines=["fse"] works correctly.

15 new tests. 3226 total pass.

Next: Fix Pass M — final live smoke rerun.
```

## Recently completed — Fix Pass K: qz-top/qz-thoughts observability fixes

```text
Status: COMPLETE

Changes:
1. proxy/qz_control_plane.py: added "profile" section to payload with
   prompt_files, reasoning_level, reasoning_policy, sampling,
   selected_context_length, backend_context_length, profile_symlink.
   Extracted from router.status_summary() backend + prompt sections.

2. scripts/qz-top:
   - model_status_from_control_plane now reads new "profile" section;
     no more "not exposed by /qz/control-plane yet" for these fields.
   - draw_profile() and once(): PROXY OFFLINE label when ModelStatus is all-empty
     (proxy unreachable → _proxy_offline detection → red/bold state, "PROXY OFFLINE"
     in loaded field and "offline" in state field).
   - Rates.cached_tokens + reasoning_tokens fields added.
   - _apply_request_completed_telemetry: reads input_tokens_details.cached_tokens
     and output_tokens_details.reasoning_tokens.
   - once() LIVE THROUGHPUT: prints "details  cached=N reasoning=N" when non-zero.

3. scripts/qz-thoughts:
   - response.completed sse_event: extracts usage and appends compact
     "usage in=N out=N [cached=N] [reason=N]" activity row.
   - New event handlers: responses_rejected_model_missing (renders "rejected:
     model not found"), responses_rejected_proxy_not_ready (renders "rejected:
     model not ready") — distinct from generic request_failed error rows.
   - New event handlers: tool_schema_replaced, coercion_succeeded,
     coercion_failed, output_text_artifact_aborted, usage_synthetic.
     All compact, no raw args/patches/URLs.

14 new tests. 3211 total pass.

Next: Fix Pass L — search profile/capabilities fixes.
```

## Recently completed — Fix Pass J: output_text tool artifact detection

```text
Status: COMPLETE

Changes:
1. _looks_like_output_tool_artifact(text): new detector, stricter than reasoning
   artifact. Tier 1: exact patch envelope markers (single strong indicator).
   Tier 2: two+ diff header markers. Tier 3: JSON starting with '{' + multiple
   specific structural markers (function_call JSON, apply_patch operation JSON,
   named-tool-with-arguments, web_search action object).
   Does NOT flag ordinary JSON, prose, code blocks, or explanations.

2. StreamHopState.output_text_artifact_sample: bounded accumulator (2048 chars
   default, QZ_OUTPUT_TEXT_ARTIFACT_SCAN_LIMIT env override) for output_text
   artifact detection per hop.

3. response.output_text.delta handler: accumulates sample on each delta, checks
   detector. On detection: suppresses delta, emits fallback message ("I stopped
   a malformed tool payload..."), emits output_text_artifact_aborted telemetry
   with chars_scanned and model (no raw artifact text), returns completed terminal
   using canonical response_id from Fix Pass I.

4. _emit_output_text_artifact_aborted: new abort method, same pattern as
   _emit_reasoning_only_aborted.

Telemetry event: output_text_artifact_aborted {reason, chars_scanned, model}
False positive guard: only triggers on high-confidence structural markers;
requires '{' as starting character for all JSON-based detection.

30 new tests: 20 detector unit tests + 10 streaming tests.
3197 total pass.

Next: Fix Pass K — qz-top/qz-thoughts observability fixes.
```

## Recently completed — Fix Pass I: response.id threading and fallback usage handling

```text
Status: COMPLETE

Changes:
1. StreamRunState.upstream_response_id: new field stores the response.id from the
   first non-suppressed response.created for the logical client-visible response.
   Populated when response.created is forwarded (not suppressed as duplicate).

2. rewrite_sse_payload: new response_id parameter rewrites response.id in forwarded
   response.completed payloads when rs.upstream_response_id is set.
   _transformed_chunks threads response_id through from run().

3. _emit_completed: new response_id parameter uses upstream response.id when known,
   falls back to resp_local_{request_id_prefix}_{uuid12} (UUID avoids timestamp
   collisions; request_id prefix aids correlation).
   All 10 call sites in fallback/abort/timeout methods updated.

4. usage_synthetic telemetry: _emit_completed emits "usage_synthetic" event with
   usage_unknown=True and usage_source="synthetic_empty" when usage is all-zeros.
   Normal upstream completions do not trigger this event.

5. response.failed synthesis in proxy_json_api: synthetic id now uses
   resp_failed_{request_id[:8]}_{timestamp} for correlation.

Rules documented:
- response.id in response.completed always matches response.created visible to Codex.
- Duplicate response.created on later hops does not overwrite the canonical id.
- Zero usage in synthesised terminals: protocol-valid object emitted + telemetry marker.
- No invented non-zero token counts.

9 new tests. 3167 total pass.

Next: Fix Pass J — output_text tool artifact detection.
```

## Recently completed — Fix Pass H: B2 tool schema/coercion/advice fixes

```text
Status: COMPLETE

Changes:
1. ToolCoercionResult __post_init__: rejects both-set, neither-set, empty error_message.
   No path can produce {"ok":false,"error":""} through this interface any more.

2. WebSearchProxyToolExecutor.coerce(): delegates to WEB_SEARCH_TOOL_ADAPTER.coerce().
   Malformed web_search JSON now triggers pre-execution coercion error and
   coercion_failed telemetry instead of falling through to in-band error.

3. Non-streaming dropped/unknown gap fixed: _run_responses_locally now handles
   kind="error" for non-proxy-local items (dropped/unknown tools get error result
   injected into next_input, not the original call).

4. tool_schema_replaced telemetry: emitted in proxy_json_api after initial tool
   normalisation when any replacement/translation/drop occurred.

5. coercion_succeeded / coercion_failed telemetry: emitted from
   ResponsesStreamRuntime (streaming) and _run_responses_locally (non-streaming)
   when completed_call_decision.coercion_applied is set.
   Payload: tool, call_id, correction_applied, error_summary (no raw args).

6. CompletedToolCallDecision: coercion_applied + coercion_error fields added.
   completed_call_decision populates these when coerce() runs.

Tests added: 19 new across test_qz_tools, test_qz_proxy_tools,
test_qz_responses_stream. Covers: ToolCoercionResult guards, coercion info
fields, streaming coercion telemetry, non-streaming gap, tool_schema_replaced.

3158 tests total pass.

Next: Fix Pass I — response.id threading + zero-usage fallback documentation.
```

## Recently completed — End-to-end smoke plan (Slice G — final audit slice)

```text
Status: COMPLETE. Audit series A–G complete. Code freeze audit phase complete.

Output: docs/end-to-end-smoke-plan.md

37 smoke IDs across 7 groups:
  Group 1: backend/model startup (7 tests)
  Group 2: basic Codex flows (5 tests)
  Group 3: web_search including FSE/furry_images/retrieval (8 tests)
  Group 4: tool schema/coercion (8 tests)
  Group 5: leak vectors (4 tests)
  Group 6: metadata propagation (7 tests)
  Group 7: failure/reconnect (6 tests)

Fix passes ordered:
  H (B2): ToolCoercionResult guard, non-streaming dropped-tool, coercion/schema telemetry
  I: response.id threading, zero-usage documentation
  J: output_text tool artifact detection
  K: qz-top proxy-offline label, cached/reasoning token display, control-plane fields
  L: furry_images retrieval_expected fix, capabilities probe warnings
  M: final live smoke rerun

Code freeze complete. Begin fix pass H.
```

## Recently completed — Search profile granularity audit (Slice F)

```text
Status: COMPLETE — final audit slice. Audit series A–F complete.

Output: docs/search-profile-granularity-audit.md

Key findings:
- FSE-only search: YES. profile="furry_fse" works; FSE not blocked by any policy.
- SoFurry: ABSENT from all config. Cannot be searched. Needs live SearXNG probe to confirm.
- furry_images: likely works if e926/furbooru are in local SearXNG; not policy-blocked.
- P1: furry_images.retrieval_expected=True is misleading — image metadata, not prose.
- P1: capabilities shows configured engines but not probe-availability — zero results
  with no warning when engines not in local SearXNG.
- non_text suppression: does NOT block e926, furbooru, or fse (correct — they are
  specialized local engines, not mainstream image search engines).
- All profiles in VALID_WEB_SEARCH_PROFILES, all in capabilities. ✓

Fix-pass order:
1. B2 (coercion/schema telemetry — existing plan)
2. Fix furry_images.retrieval_expected misleading value
3. Add probe-availability warning to capabilities
4. Add 5 missing engine suppression/profile tests
5. Live-probe local SearXNG for SoFurry (future separate slice)
```

## Recently completed — qz-thoughts/qz-top observability audit (Slice E)

```text
Status: COMPLETE

Output: docs/observability-ui-audit.md

Key findings:
- P0: none (UI tools are read-only, cannot corrupt protocol state).
- P1: proxy offline looks identical to "no model loaded" in qz-top (ModelStatus()
  from None control-plane).
- P1: qz-thoughts cannot distinguish "request rejected: model not ready" from
  "upstream failed" — both render as request_failed error row.
- P2: no usage/token display in qz-thoughts at all; cached/reasoning tokens not
  in qz-top rates; prompt_files/reasoning/context missing from qz-top when
  control-plane is the active source (fields not in control-plane payload).
- P2: coercion/schema telemetry missing (B2 gap — no telemetry events yet);
  active tool call state not live in qz-top.
- Reconnect behaviour: correct (confirmed by tests). qz-thoughts SSE reconnect
  resets last_seq on full reconnect, preserves on idle reconnect.
- DEATH label: correctly shown in qz-top for backend_died_after_healthy.

Fix pass order:
1. B2 (coercion/schema telemetry — existing plan)
2. P2a: add usage row to qz-thoughts (in/out tokens from request_completed)
3. P2b: add cached/reasoning tokens to qz-top rates panel
4. P2c: add missing fields to /qz/control-plane (prompt_files, reasoning_level, etc.)
5. P1a from Slice D: response.id threading through StreamRunState
6. E1: distinguish proxy-offline from no-model in qz-top
```

## Recently completed — Metadata propagation audit (Slice D)

```text
Status: COMPLETE

Output: docs/metadata-propagation-audit.md

Key findings:
- P1: response.id mismatch in multi-hop streaming — synthesised _emit_completed uses
  resp_local_{timestamp} instead of upstream response.id. Codex SDK may see two IDs
  for one logical exchange.
- P1: zero usage in synthesised fallback terminals — if _drain_stream_for_usage fails
  (upstream sends no response.completed after tool break), Codex receives all-zeros usage.
- P2: cached_tokens and reasoning_tokens not visible in qz-thoughts or qz-top.
- qz_* metadata is forwarded to llama.cpp (harmless for local deployment, worth noting).
- call_id matching is correct for normal upstream tool calls.
- Usage normalisation is well-tested (handles OpenAI/llama.cpp field name variants).

Fix pass order:
1. B2 (coercion/schema telemetry — already planned)
2. response.id threading through StreamRunState
3. Document zero-usage in fallbacks + test
4. C2 output_text artifact detection
5. P2 token observability in qz-thoughts
```

## Recently completed — Streaming event mapper audit (Slice C)

```text
Status: COMPLETE

Output: docs/streaming-event-mapper-audit.md

Key findings:
- CORRECTION of Slice A: qz-thoughts IS updated in real time during Responses streaming.
  ResponsesStreamRuntime calls _emit_sse_telemetry via chunk_writer → _write_sse_chunk.
  The blank panel bug was a false positive — it does not exist.
- Critical: model-output tool JSON in output_text is NOT detected (L4 gap).
  _looks_like_reasoning_tool_artifact only runs for reasoning channel.
  This is the actual tool-leak-as-assistant-text vector.
- All function_call stream events correctly suppressed from Codex. No false positives.
- Tool continuation flow is correct (buffer → lifecycle → next_input → completion).
- Reasoning modes (raw/summary/hidden) are correct.
- DeepSeek think-tag handling: none implemented (not needed for current Qwen).
- 10 missing fixture tests identified precisely.

Fix pass order:
1. B2 fixes (already planned: ToolCoercionResult guard, non-streaming gap, telemetry).
2. L4: add output_text tool artifact detection.
3. Slice E: 4 streaming coercion fixture tests.
4. Slice D audit: metadata propagation.
```

## Recently completed — Tool schema/coercion/advice audit (Slice B)

```text
Status: COMPLETE

Output: docs/tool-schema-coercion-audit.md

Key findings:
- Tool schema replacement: CORRECT (ebdf87b). function-typed web_search replaced.
  Dedup deterministic. Stale Codex schema cannot reach upstream.
- Coercion paths: all implemented. web_search, apply_patch, dropped, unknown all correct.
- ToolCoercionResult neither-set case: constructible, produces empty error string. Fix needed.
- Non-streaming path gap: dropped/unknown errors not applied for non-proxy-local items
  when web_search is also present in the same response.
- Telemetry: no tool_schema_replaced, no coercion_success, no coercion_failed events.
  qz-thoughts has zero visibility into coercion or schema replacement.
- 5 missing streaming fixture tests for coercion error paths.

Slice B2 target (fixes):
1. Guard ToolCoercionResult neither-set case (__post_init__ assertion).
2. Apply dropped/unknown errors in non-streaming hop loop for non-proxy-local items.
3. Emit tool_schema_replaced telemetry from proxy_json_api after normalisation.
4. Emit coercion_succeeded/coercion_failed telemetry at decision point.
5. Add 8 missing tests (adapter_for_name, streaming coerce fixture, neither-set, etc.)
```

## Recently completed — Streaming/tool/reasoning/metadata contract discovery (Slice A)

```text
Status: COMPLETE

Output: docs/runtime-streaming-tool-contract-audit.md

Key findings:
- Critical: qz-thoughts thought/answer panels blank during Responses streaming —
  ResponsesStreamRuntime does not call _emit_sse_telemetry; sse_event telemetry only
  emitted from legacy chat/completions path.
- Tool leak is a model-output problem (model produces tool JSON as output_text);
  proxy correctly suppresses all function_call stream events.
- Tool schema dedup/replacement fixed (ebdf87b); telemetry observability still missing.
- Zero coercion success/failure observability.
- All function_call stream events suppressed from Codex correctly.
- Reasoning routing correct; reasoning-only abort logic working.

Slice B target:
- Add sse_event telemetry to ResponsesStreamRuntime for reasoning/output text deltas.
- Add coercion_success/coercion_failed/tool_schema_replaced telemetry.
- Guard ToolCoercionResult neither-set case.
```

## Recently completed — Streaming tool contract code review + search profile fixes

See `docs/current-stocktake.md` for the full point-in-time state summary.

## Recently completed — web_search capabilities introspection

```text
Status: COMPLETE

Code:
- proxy/qz_tool_web.py adds action="capabilities" and
  build_web_search_capabilities(runtime)
- proxy/qz_request_router.py adds GET /qz/web-search/capabilities

Contract:
- capabilities returns qz.web_search.capabilities.v1
- live config/runtime metadata is the source of truth for profiles and budgets
- no automatic keyword routing
- no SearXNG search or Agent API retrieve call during introspection
- no search/open/retrieve budget consumption
- no retrieval endpoint or localhost leak

Tests:
- tests/test_qz_tool_web.py covers schema, action handling, safe profile/budget
  exposure, retrieval metadata, telemetry, and no endpoint leaks
- tests/test_qz_request_router.py covers the read-only endpoint
```

This document turns the current planning docs into an execution order. It does
not replace the architecture contracts. If this file conflicts with
`docs/current-architecture-authority.md` or
`docs/codex-context-memory-contract.md`, those documents win.

## Current hard rules

```text
Use memory_domain, not profile_family.
Missing memory_domain means isolated.
Capability detection from tools never grants durable memory access.
QuantZhai-owned qz_* context stays internal and must not be injected into
forwarded /v1/responses request bodies.
BrainCaseDB stores explicit memory/state records only (not operational facts).
SQLite is not a telemetry warehouse, config authority, or memory_domain registry.
Sessions/turns/requests are SourceRef/provenance only when attached to a StateRecord;
they are not automatic operational logs.
No automatic ingestion. No clever memory. No cross-domain sharing.
```

## Active — model-selection authority cleanup (post #65)

```text
Goal: make the proxy the single authority for active model selection;
remove split authority across env / state files / qz-codex / catalog /
backend observations.  No new shell scripts for model selection.

Design: docs/proxy-model-selection-authority.md

Slice A-design — ✅ audit + final design
Slice B-state — ✅ qz.model_state.v1 in proxy/qz_model_state.py;
  load_last_selected_model no longer reads loaded_model;
  _persist_model_state routed through new module; 32 new tests;
  2988 total pass
Slice C-endpoints — ✅ /qz/model/status, /qz/model/select,
  /qz/model/reload, /qz/model/select-and-restart;
  proxy/qz_model_status.py builds qz.model_status.v1;
  ModelCatalog.refresh() now enforces precedence
  (persisted > QZ_MODEL_KEY seed > catalog default);
  /qz/control-plane exposes configured_env_model, selected_source,
  selected_loaded_mismatch, load-failure surface, operator hints;
  44 new tests; 3032 total pass
Slice D-qz-codex — ✅ qz_codex_exec_preflight in scripts/qz-codex-common;
  GET /qz/model/status, compare active model, mismatch error includes
  literal curl /qz/model/select-and-restart; QZ_CODEX_AUTO_SELECT_MODEL=1
  opts into select+restart with source=qz_codex; old visibility-only
  qz-wait-ready preflight removed; 17 new tests; 3049 total pass.
  Note: qz-codex no longer treats /v1/models visibility as active backend selection.
Slice E-load-failure — ✅ proxy/qz_model_load_failure.py classifier;
  BackendManager.fetch_recent_logs(); /qz/model/reload and
  /qz/model/select-and-restart run classifier post-load and update
  qz.model_state.v1 last_load_* fields; failed loads return HTTP 409
  with classified payload (insufficient_vram / context_creation_failed
  / unknown); selection authority preserved on failure; successful
  reload clears previous error; 22 new tests; 3071 total pass
Slice F-smoke — ✅ Cold-start smoke on real hardware (kuato good /
  27B Q5_K_M too-large) exercised every endpoint and surface; uncovered
  4 bugs (classifier false-positives on benign retry/user-override
  lines, stale last_load_* across restart, selected_source downgrade
  by legacy _persist_model_state, qz-codex exit-code propagation);
  all fixed with regression tests; 3081 total pass; results recorded
  in docs/proxy-model-selection-authority.md §16.

## Model-selection authority cleanup — COMPLETE

All slices (A-design, B-state, C-endpoints, D-qz-codex, E-load-failure,
F-smoke) closed.  No new shell scripts added (`scripts/qz-model*`
invariant intact).

### Direct backend launch + runtime observability (2026-05-22)

- BackendManager launches the container with `-m /models/<selected>.gguf`.
  `QZ_BACKEND_MODEL_MODE` is deprecated compatibility only; router
  `--models-dir` mode is not supported.
- `BackendManager.set_launch_model()` + `_do_start()` safety gate.
- `/qz/model/{reload,select-and-restart}` restart the container with the
  selected model. `/qz/model/select-and-restart` is the canonical runtime
  mutation path.
- Legacy `/qz/models/select` and `/qz/models/load` return `410 Gone`.
- `/qz/model/status` + `/qz/control-plane.models` surface
  `backend_model_mode=direct`, `launch_model_*`, `selected_model_ready`,
  `request_admission_state`, `model_switch_state`, `active_load_operation`,
  runtime failure fields, plus the existing `last_good_*` /
  `failed_candidate_*` recovery fields.
- qz-top: new `MODEL` and `RCVRY` rows in both static and TUI render
  paths. qz-thoughts consumes readiness/admission fields when available.

### Post-F polish (2026-05-22)

- **Failed-model recovery**: qz.model_state.v1 gains last_good_* and
  failed_candidate_* observation fields; mark_load_success /
  mark_load_failure helpers; /qz/model/select-and-restart accepts
  rollback_on_failure (default true); /qz/model/status surface adds
  rollback_performed / recovery_available / recommended_recovery_action;
  control-plane operator hints for rollback and no-last-good;
  qz-codex auto-select failure prints classified failure block.
- **Codex catalog freshness**: /qz/codex/client-config exposes
  model_catalog.freshness {catalog_mtime_ms, source_mtime_ms,
  catalog_age_seconds, stale, reason, remediation} plus refresh_url;
  new POST /qz/codex/model-catalog/refresh (metadata only — no backend
  mutation); qz-codex bootstrap auto-refreshes when stale via
  QZ_CODEX_REFRESH_CATALOG=1 (default; set 0 to suppress).
- 26 new tests; 3103 total pass.

Key hard rules from A-design:
  - QZ_MODEL_KEY is a one-shot seed; never overrides persisted operator selection.
  - loaded_model is observation only, never selection authority.
  - qz-codex visibility-only preflight is not enough; must check
    selected/loaded mismatch via /qz/model/status.
  - Backend HTTP /health is not enough; classify model load/fit failures.
  - No scripts/qz-model, qz-select-model, qz-model-status, qz-load-model.
```

## Recently completed (2026-05-21 run — #65 D.1 GPU regression fix)

```text
#65 D.1 — GPU offload regression in BackendManager (proxy-owned backend)
  - Root cause: BackendManager's docker run lacked explicit NVIDIA env vars;
    proxy-started containers silently fell back to CPU with no detection.
  - Fix: added NVIDIA_VISIBLE_DEVICES=all and NVIDIA_DRIVER_CAPABILITIES=compute,utility
    to every docker run invocation.
  - Added QZ_EXPLICIT_NVIDIA_DEVICES=1 fallback for hosts where --gpus all
    does not propagate CUDA context (explicit --device passthrough).
  - Added post-health GPU log check: _check_gpu_offload_from_logs() parses
    container logs for hard failure and success patterns; sets gpu_offload_state.
  - QZ_REQUIRE_GPU=1 (default): phase=failed if CPU fallback/CUDA init failure
    detected. HTTP health alone is no longer sufficient.
  - BackendState gains gpu_required, gpu_offload_state, gpu_error.
  - New env: QZ_REQUIRE_GPU (default 1), QZ_GPU_LOG_TAIL (default 1000),
    QZ_EXPLICIT_NVIDIA_DEVICES (default 0).
  - 27 new tests; 2953 total PASS.
  - docs/backend-lifecycle-control-plane.md updated (§15.8, §15.5, §14 roadmap).
```

## Recently completed (2026-05-19 run — #5/#57 close-out + #58 always-HTTP + #56 Slices B/B.1)

```text
#56 Slice B — path-helper abstraction (commit eff2555)
  - proxy/qz_paths.py: qz_root, qz_var_dir, model_inventory_path,
    codex_home_dir, codex_model_catalog_path, codex_config_path
  - Replaced inline generated artifact paths in 5 modules
  - 11 tests; py_compile/shell syntax PASS

#56 Slice B.1 — CODEX_HOME/server-path audit
  - Removed 3 stale CODEX_HOME overrides from server/proxy runtime code:
    qz_request_router.py:_refresh_codex_catalog(),
    qz_request_router.py:/qz/models/refresh,
    qz_control_plane.py:_codex_catalog_info()
  - All server Codex artifact paths now use qz_paths (QZ_VAR_DIR), not CODEX_HOME
  - 6 new tests for CODEX_HOME independence
  - 2592 tests PASS
```

## Recently completed (2026-05-19 run — #5/#57 close-out + #58 always-HTTP bootstrap)

```text
Config/var/script cleanup (#5, now closed)
  - /qz/config/effective: file metadata (mtime/size/sha256_12/hash_skipped)
  - prompt-file source labelling (referenced list, source_layers, referenced_by)
  - generated artifact staleness warnings:
      stale_model_inventory_cache, stale_codex_catalog, stale_codex_config
  - stale_against precision; close-out audit verdict: CLOSED
  - Follow-ups: #56 (path migration design), #57 (qz-codex thinning)

qz-codex remote bootstrap (#57, now closed; superseded by #58)
  - GET /qz/codex/client-config — Codex client bootstrap metadata
  - GET /qz/codex/model-catalog — generated catalog served to remote clients
  - QZ_CODEX_REMOTE=1 explicit launcher remote mode in qz-codex-common
  - Writes local CODEX_HOME catalog/config.toml atomically with TOML escaping
  - Co-located mode unchanged; no API key values written or printed
  - Verified Codex CLI 0.130.0 model_catalog_json is local-file-only

Always-HTTP qz-codex bootstrap (#58, now closed)
  - qz-codex always uses HTTP; QZ_CODEX_REMOTE removed as branch/gate
  - Server-local CODEX_HOME, config template copy, TOML provider parse removed
  - POST /qz/models/refresh removed from launcher (client is read-only)
  - qz-up recovery coupling removed; error messages bounded
  - CODEX_HOME default: $HOME/.qz-codex/codex-home
  - 28 focused tests; 2576 total; py_compile/shell syntax PASS

#37 stream seam Slices 2F + 2F.1 (paused, not closed)
  - stream_timeout_kind() combiner extracted to qz_stream_watchdog.py
  - 2566 tests total
```

## Recently completed (2026-05-18 run — BrainCase + repeated-read + #37)

```text
BrainCase memory tool API (#53, now closed)
  - render/recall/write_candidate tools (feature-flagged, default disabled)
  - Operator review CLI (qz-braincase-review), retention policy, prune --apply
  - BrainCaseDB is the first concrete LimbiCore technology
  - Slices A–I.1 complete; no automatic ingestion; 2465 tests

BrainCase retention/lifetime policy (#54, now closed)
  - Multi-axis policy matrix, pure evaluator, dry-run report, prune --apply
  - Slices A–D complete; operator-controlled, not automatic

Repeated-read v1 advisory signal (#3/#4/#43, now closed)
  - proxy/qz_file_signal.py: parser, state, RepeatedReadState
  - Integration: qz_proxy_tools.py, qz_responses_stream.py, qz_request_router.py
  - Live smoke: scripts/qz-smoke-repeated-read
  - Advisory, stateless, input-history-seeded; no BrainCase writes; no persistence

#37 stream seam Slices 1–2E.1 (paused, not closed)
  - StreamHopState (per-hop mutable state object)
  - StreamDecision (vocabulary dataclass, not yet broadly consumed)
  - 4 pure module-level decision helpers extracted
  - No decide_stream_event() exists; qz_responses_stream.py remains side-effect owner
  - 2465 tests; paused before delicate seams (tool lifecycle, terminal, watchdog)
```

## Recently completed (2026-05-15 run — VRAM/recovery/docs)

```text
VRAM telemetry (#6, now closed)
  - provenance-labelled component panel live in qz-top
  - MODEL_RUNTIME calibrated from process_used − KV_ALLOC
  - MODEL_FILE (GGUF size) retained as non-subtractive provenance
  - KV_ALLOC from QZ_CACHE_RAM (runtime budget) > GGUF formula > unknown
  - Quant registry with documented effective bytes/element (35+ types)
  - docs/patterns/provenance-telemetry.md: doctrine locked
  - AGENTS.md: telemetry/status doctrine added
  - #52 opened: upstream-blocked follow-up for allocator metrics

Backend control-plane and recovery (#44, #47-#50, #45, now closed)
  - /qz/control-plane is the live status authority
  - Full recovery trigger/plan/backoff/async-job API (six actions)
  - recovery_state.py: in-memory backoff/attempt counts (sufficient as-is; #51 CLOSED not-planned)
  - Legacy catalog fallback removed (#45)

Stream watchdog and signal planning (#40, #42, now closed)
  - qz_stream_terminal.py classifies stream terminal outcomes
  - qz_stream_watchdog.py detects no-output and terminal-after-output timeouts
  - qz-thoughts renders non-ok stream_terminal_classified rows
  - qz_feedback.py exists for bounded future signal adoption
  - Runtime signal migration is deferred; needs a concrete bug or new scoped issue

Foundation audit before SQLite
  - docs/foundation-audit-before-sqlite.md maps gravity wells, signal paths,
    duplicated/underused signals, and Codex feedback gaps
  - #2 CLOSED not-planned; BrainCaseDB (#53/#54) and OperationalStore (#46) deliver the substrate
```

## Recently completed (2026-05-14 run — profiles/sandbox/smoke)

```text
qz.profiles.v1 active config + split default/example profiles (#26/PR#27)
  - config/default/profiles.json + profiles/*.json  (shipped defaults)
  - config/user/profiles.json + profiles/*.json     (local user config)
  - model-overrides.json preserved as legacy fallback per layer

memory_domain config plumbing (#23/PR#24, PR#25)
  - memory_domain read from profile overrides, stored on catalog entries
  - exposed in /v1/models, /qz/status, /qz/config/effective
  - memory.domain in qz.profiles.v1 maps to memory_domain internally
  - missing memory_domain resolves to isolated at request time
  - no inference from model/profile/client/tool names

Simplified reasoning-effort prompts (#29/PR#30)
  - short depth-only prompts; removed hard tool-call caps and cross-file mandates
  - high/xhigh preserve final-answer obligation

Sandbox/tool-failure telemetry and guidance (#28/PRs#31-34)
  - Slice 1: tool_escalation_requested on outgoing require_escalated calls
  - Slice 2: native tool-output classifier before normalization
    - tool_sandbox_denied  (Read-only file system)
    - tool_connection_failed  (Connection refused)
  - Slice 3: harness guidance in codex-core-qwenified.md
  - qz-thoughts renders denied/conn-fail/escalation activity rows

Live stack smoke test (#35/PR#36)
  - scripts/qz-live-smoke validates proxy health, config, qz-thoughts,
    unit guards, normal Codex path, and sandbox-denied telemetry end-to-end
```

## Dependency chain

```text
authority/docs cleanup (ongoing)
  -> [DONE] explicit memory_domain config plumbing
    -> [DONE] stream watchdog and foundation audit before SQLite
      -> [DONE] BrainCaseDB + StateRecord/write/render slices A–F
      -> next: braincase.recall semantics / operator write exposure
        -> rendered state packets / LimbiCore recall (future)
```

---

## P0: Authority and task cleanup (ongoing)

Goal: stop agents from re-planning old decisions or following stale language.

Tasks:

```text
1. Keep this file updated as the short task DAG.
2. Update docs/README.md when new active docs are added.
3. Update docs/progress-snapshot.md after major implementation passes.
4. Mark old profile_family language as historical when encountered.
5. Keep docs/current-architecture-authority.md as the final conflict resolver.
```

Acceptance:

```text
A new agent can read docs/README.md, current-architecture-authority.md,
codex-context-memory-contract.md, and this file, then know what to do next.
```

---

## P1: BrainCase memory tool API and storage substrate

Goal: design and build the tool-mediated memory plane above BrainCaseDB.
See `docs/braincase-memory-tool-api.md` for the architecture and slice plan.

The goal is a superhuman memory console:

```text
LLM thinks -> uses memory tools -> helpers accelerate/constrain mechanics
-> storage/indexes return exact evidence -> LLM reasons again
```

Not automatic request logging. Not a DB-of-everything.

Current slice status:

```text
Slice 1 landed: optional/non-fatal SQLite state/memory storage substrate skeleton only.
Module: proxy/qz_braincase_db.py
BrainCaseDB is the low-level storage case — not a policy layer.
It stores parser-boundary identity/scoping facts, not runtime telemetry.
Env: QZ_STATE_DB_ENABLED, QZ_STATE_DB_PATH
Default: disabled; enabling is explicit via QZ_STATE_DB_ENABLED.
Schema: version metadata only, PRAGMA user_version = 1.
No parser facts, runtime signal history, stream telemetry, recovery/backoff
state, or model-visible memory are persisted yet.

#2 CLOSED not-planned — original automatic-session/request-log shape rejected.
BrainCaseDB (explicit writes, model-facing memory) delivered by #53/#54.
OperationalStore (runtime events/facts) delivered by #46.
All existing doctrine still applies: no automatic ingestion, explicit writes only.
```

Scope:

```text
Optional/non-fatal DB open.
Parser-boundary only.
Consume extract_codex_request_context().
Store parser-boundary scoping facts and summaries, not giant raw request bodies.
DB write failure logs/telemeters but does not break proxy responses.
Follow docs/foundation-audit-before-sqlite.md.
```

Memory-domain authority:

```text
memory_domain definitions stay in config/profile policy.
SQLite may later record which configured memory_domain applied to a stored fact.
SQLite is not the memory_domain registry or policy authority.
SQLite must not infer or create domains.
```

Substrate file:

```text
proxy/qz_braincase_db.py
```

Likely tests:

```text
tests/test_qz_state_db.py
tests/test_qz_request_state_integration.py
```

Historical / superseded — do NOT implement as BrainCaseDB tables:

```text
The earlier "Phase 1 SQLite operational facts" plan listed:
  sessions, turns, requests, workspace_candidates,
  resolved_workspaces, session_workspace_bindings, identity_conflicts

This framing is superseded by BrainCase doctrine.
BrainCaseDB stores StateRecords and SourceRefs only.
If sessions/turns/requests ever appear in BrainCaseDB, they must be
SourceRef provenance attached to an actual stored StateRecord — never
as automatic session/request logs.
```

Must not implement:

```text
model-visible durable memory
learned global preferences
roleplay/profile-private memory
HSM/archive memory
automatic promotion
cross-domain sharing
repeated-read v2 persistence
forwarded qz_* request-body metadata injection
```

Current acceptance (slice 1 only):

```text
test_qz_braincase_db.py: 11 tests pass
BrainCaseDB skeleton exists, disabled by default, schema metadata initialised.
No automatic ingestion.
```

Slice A acceptance (COMPLETE):

```text
docs/schemas/braincase/source-ref.schema.json    — SourceRef schema
docs/schemas/braincase/state-record.schema.json  — StateRecord schema (memory_domain=string, no enum)
docs/schemas/braincase/render-packet.schema.json — RenderPacket schema
docs/fixtures/braincase/source-refs/             — 4 source ref fixtures
docs/fixtures/braincase/state-records/           — 7 state records (all mandatory tiers covered)
docs/fixtures/braincase/render-packets/          — 1 render packet fixture
tests/test_braincase_schema_fixtures.py          — 44 tests, all passing
```

Slice B acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — schema v3, 5 new tables, 7 new methods
tests/test_qz_braincase_db.py — BrainCaseDBSliceBTests: 33 new tests (44 total)
All 1645 tests passing.
```

Slice C acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — schema v3, FTS5 table, query_plan/search/inspect helpers
tests/test_qz_braincase_db.py — BrainCaseDBSliceCTests: 36 new tests (80 total)
All 1681 tests passing.
```

Slice C.1 acceptance (COMPLETE):

```text
proxy/qz_braincase_db.py — rebuild_fts_index, _sync_fts_for_record, _maybe_backfill_fts_index
  init() auto-backfills FTS when state_records has rows and FTS is empty
tests/test_qz_braincase_db.py — BrainCaseDBSliceC1Tests: 12 new tests (92 total)
All 1693 tests passing.
```

Slice D acceptance (COMPLETE):

```text
proxy/qz_braincase_write.py — new module:
  scope_resolve, redaction_check, dedup_check, conflict_check, source_link
  braincase_write_state_record, braincase_update_state_record (retire + supersede)
tests/test_qz_braincase_write.py — 51 tests, all passing
Full suite: 1744 tests passing
```

Slice E acceptance (COMPLETE):

```text
proxy/qz_braincase_render.py — new module:
  render_budget_chars, make_render_packet_id, eligible_for_render,
  render_record_line, render_pack, braincase_render_packet
tests/test_qz_braincase_render.py — 53 tests, all passing
Full suite: 1819 tests passing
```

Blocked by for Slice F:

```text
Slice E is complete. Slice F (harness/tool exposure) may now start.
Renders exist internally; they are not yet wired to harness or model tools.
```

Best resource:

```text
docs/braincase-memory-tool-api.md
docs/model-state-signal-contract.md
```

---

## P2: Repeated-read signal

Goal: reduce wasted tool calls from redundant file reads without suppressing
legitimate re-reads.

Current rule:

```text
V1 COMPLETE (#3/#4/#43 closed).
  proxy/qz_file_signal.py — parser, state, RepeatedReadState
  Integration live in qz_proxy_tools.py, qz_responses_stream.py, qz_request_router.py
  Live smoke: scripts/qz-smoke-repeated-read
  Advisory, stateless, input-history-seeded. No BrainCase writes. No persistence.

Do not implement persistent v2 without a new scoped issue defining session identity,
retention, non-goals, and explicit non-BrainCaseDB/non-OperationalStore boundaries.
```

V1 likely files:

```text
proxy/qz_file_signal.py
proxy/qz_tool_lifecycle.py
proxy/qz_proxy_tools.py
proxy/qz_responses_stream.py
proxy/qz_request_router.py
tests/test_qz_file_signal.py
tests/test_qz_proxy_tools.py
```

V1 behaviour:

```text
Seed read/write state from body["input"] function_call/function_call_output items.
Detect conservative read commands such as cat/head/tail/sed/nl/rg/grep/wc.
Do not scan normal message text.
Do not parse ls/find as file reads.
Signal once per path per request/run.
After a warning in the same run, allow the next repeat through.
Suppress warning after a write to that path.
```

V2 behaviour:

```text
Use same-scope file read/write/signal facts from SQLite.
Scope by qz_session_id, qz_turn_id/codex_turn_id, qz_request_id, workspace_id,
and memory_domain.
Never cross workspace or memory_domain.
```

Blocked by:

```text
V1: not blocked. Start any time; integration is cleaner after P1 SQLite.
V2: blocked by P1 SQLite substrate and scope queries.
```

Best resource:

```text
DeepSeek can draft parser tests. Codex/Claude should do integration.
```

---

## P3: Telemetry filter ergonomics / qz-live-smoke refinements

Goal: reduce friction when diagnosing tool-failure events in a noisy telemetry
stream.

Scope:

```text
Optional /qz/telemetry/recent?type=tool_sandbox_denied query parameter.
Optional /qz/telemetry/recent?types=A,B filter for multiple event types.
qz-live-smoke --model flag default confirmation.
Consider a per-request telemetry endpoint (/qz/telemetry/request?request_id=...).
```

Blocked by:

```text
Not blocked. Low priority; implement when the noisy-window problem recurs.
```

---

## P4: Config/var/script ownership cleanup

Goal: reduce second truths and script-owned policy.

Current problem:

```text
source defaults, examples, user overrides, generated Codex config, runtime state,
captures, logs, cache, model inventory, and profile symlinks are still too easy
to confuse.
```

Rules:

```text
Do not move model files, profile symlinks, or Codex-visible slugs casually.
Do not add new one-off shell scripts unless strongly justified.
Do not make generated Codex catalog files into routing authority.
Proxy policy remains the source of truth.
```

Likely tasks:

```text
1. Improve /qz/config/effective coverage.
2. Surface missing prompt files and override warnings in one place.
3. Make /qz/models/refresh regenerate the Codex catalog file too.
4. Move more generated output toward var/generated/ only after report coverage.
5. Thin qz-codex-common after proxy owns catalog generation.
```

Blocked by:

```text
Not strictly blocked, but avoid broad refactor before P1/P2 unless fixing a
specific live breakage.
```

Best resource:

```text
Codex/Claude, after tests are strong.
```

---

## P5: Observability polish — VRAM DONE; remaining items

VRAM telemetry (#6) and the stream/compaction hang watchdog (#40) are closed.
The VRAM panel is live and provenance-labelled. Stream terminal/no-output
classifications are live. See docs/patterns/provenance-telemetry.md for
telemetry doctrine and docs/runtime-observability-notes.md for stream/VRAM
runtime notes.

Remaining observability work:

```text
#52  backend allocator metrics (upstream-blocked; QuantZhai already wired)
first-status correctness tests
long-running TUI validation
profile prompt/config ownership review
fixed profile-eval prompt set in benchmark harness
```

Blocked by:

```text
#52: blocked by TurboQuant emitting allocator metrics; no QuantZhai action.
```

---

## P6: LimbiCore rendered state packets and future memory

Goal: eventually render small purpose-specific state packets or recall results
from scoped records.

Not now.

Future work includes:

```text
StateRecord envelope
rendered coding state packets
utility LLM proposal jobs
memory.search / memory.propose_write tools
roleplay specialised renderers
HSM evidence/provenance renderers
```

Blocked by:

```text
P1 memory_domain config
P2 SQLite substrate
explicit render policy
cross-domain isolation tests
```

---

## #58 completed: always-HTTP qz-codex bootstrap (D2/D2.1/D3)

**CLOSED.** qz-codex now always uses HTTP bootstrap via `/qz/codex/client-config`.

Implementation (Slices D2/D2.1/D3):
- `QZ_CODEX_REMOTE` branching removed — HTTP is the only path
- Server-local `CODEX_HOME` removed; default is `$HOME/.qz-codex/codex-home`
- `config/example/codex-config.toml` copy removed from launcher
- TOML provider parse removed; provider comes from HTTP client-config
- `POST /qz/models/refresh` removed from launcher (client is read-only)
- qz-up reference removed from error messages
- Proxy-down and missing-catalog errors bounded with no traceback/qz-up
- Atomic writes, TOML escaping, no-secret guarantees preserved
- No #56 path migration mixed in

See `docs/edge-case-config-contract-plan.md` §qz-codex always-HTTP bootstrap design.

## #56 D-design complete: generated artifact path migration (Slices A–D-design)

**Slice A-design:** CLOSED. Inventory/plan complete. No path moves yet.

Key finding: After #58, qz-codex clients do NOT read server-local generated
paths. The `/qz/codex/model-catalog` endpoint is the stable boundary. This
makes server-side path migration safe — no client changes needed.

**Slice B (commit eff2555):** CLOSED. `proxy/qz_paths.py` added. Replaced
inline generated artifact paths with helpers. 11 tests. No physical moves.

**Slice B.1 (audit/polish):** CLOSED. Audited all server/proxy CODEX_HOME
usage after #58. Three fixes:
- `qz_request_router.py:_refresh_codex_catalog()` — removed CODEX_HOME override
- `qz_request_router.py:/qz/models/refresh` — removed CODEX_HOME override
- `qz_control_plane.py:_codex_catalog_info()` — replaced inline CODEX_HOME path
  with `codex_model_catalog_path()` from qz_paths
- Removed unused `_codex_home_dir` imports from qz_config_report.py,
  qz_control_plane.py, qz_request_router.py
- Added 6 tests for CODEX_HOME independence
- Updated control plane test for new behaviour
- 2592 tests PASS. py_compile/shell syntax PASS.

Doctrine confirmed: CODEX_HOME is client-local qz-codex state.
Server/proxy generated artifact paths come from qz_paths / QZ_VAR_DIR.
No physical file moves. No var/generated/ created.

**Slice C-design:** CLOSED. First migration target selected and compatibility
plan defined. See `docs/edge-case-config-contract-plan.md` §Slice C-design.

Key decisions:
- **Chosen target:** A1 (`var/model-inventory.json`) → `var/generated/model-inventory.json`
- Strategy: change one helper return value, all consumers follow
- No symlink shim, no old-path deletion, QZ_MODEL_INVENTORY_CACHE preserved
- A2/A3 migration deferred to Slice D-design (coupled artifacts)

**Slice C-impl (commit fb28945):** CLOSED. `model_inventory_path()` now returns
`qz_var_dir() / "generated" / "model-inventory.json"`. QZ_MODEL_INVENTORY_CACHE
override preserved. Staleness warnings and write_cache() follow the helper.
No A2/A3 changes. Full suite 2592 tests PASS.

**Slice C.1 (commit 3ec719e):** CLOSED. A1 migration audit/polish. Table separator
fix in current-stocktake.md. Stale docs updated. Two focused tests added
(generated path default, QZ_MODEL_INVENTORY_CACHE override).

**Slice D-design (this commit):** CLOSED. A2/A3 coupling audited. Chosen target: D2.

Key decisions:
- **A2 target:** `var/generated/codex/qwenzhai-models.json`
- **A3 target:** `var/generated/codex/config.toml`
- **Move together** — generate() writes A3 with A2's absolute path; splitting breaks consistency
- **New helper:** `codex_generated_dir()` → `qz_var_dir() / "generated" / "codex"`
- **Changed helpers:** `codex_model_catalog_path()` and `codex_config_path()` route through it
- **Kept deprecated:** `codex_home_dir()` and `codex_model_catalog_dir()` (unchanged return values)
- **No shim** — after #58, no client reads server A2/A3 paths
- **No old-path deletion** — `var/codex-home/` stays until user cleans up
- **No script changes** — qz-codex-common uses client-local CODEX_HOME, not server paths
- **Staleness names unchanged** — `stale_codex_catalog`, `stale_codex_config`

See `docs/edge-case-config-contract-plan.md` §Slice D-design for full analysis.

**Slice D-impl (this commit):** CLOSED. `codex_generated_dir()` added.
`codex_model_catalog_path()` → `var/generated/codex/qwenzhai-models.json`.
`codex_config_path()` → `var/generated/codex/config.toml`.
`codex_home_dir()` and `codex_model_catalog_dir()` deprecated (unchanged return values).
No shim. No old-path deletion. No qz-codex-common changes.
qz-doctor and config/example/codex-config.toml updated.
6 new path tests. All A2/A3 test fixtures updated. 2600 tests PASS.

**Slice D.1 (this commit):** CLOSED. A2/A3 migration audited. Stale paths fixed in
`docs/edge-case-config-contract-plan.md` (generated files list, current path map, contract
risks). Stale path in `scripts/qz-codex` exec error message fixed. 3 new tests confirm
`/qz/config/effective` reports new A2/A3 paths and old `codex-home` path not present.
2603 tests PASS.

**Slice E (this commit):** CLOSED. Audited runtime callers — none found.
`codex_home_dir()` and `codex_model_catalog_dir()` removed from `proxy/qz_paths.py`.
4 deprecated tests removed; 1 negative regression test added
(`test_no_deprecated_codex_home_helpers_exported`). 2600 tests PASS.

**Close-out (this commit):** CLOSED. All acceptance criteria verified. Proxy docstrings
for `codex-home` path references updated. Artifact inventory table A2/A3 paths fixed.
`docs/config-data-path-audit.md` migration note extended to cover A2/A3. 2600 tests PASS.
#56 closed.

**#51/#46 Slice A-design (commit 69b2ba9) + A.2 correction (this commit):** CLOSED (design only).

OperationalStore: lightweight SQLite for runtime events and operational facts.
NOT BrainCaseDB. NOT model-visible memory. NOT recovery policy. NOT repeated-read v2.

Key decisions:
- Path: `$QZ_VAR_DIR/state/operational.sqlite3` (env: `QZ_OPERATIONAL_DB_PATH`)
- Module: `proxy/qz_operational_store.py` (future)
- Phase 1 tables: schema_meta, runtime_events, runtime_facts ONLY
- sessions/workspaces/recovery_state/repeated_read_state: removed — no Phase 1 consumer
- qz-write-runtime-state: dual-write in Slice C; JSON file stays for compatibility
- #51: CLOSED not-planned — backoff/cooldown persistence rejected; in-memory RecoveryState is sufficient
- #46: primary Phase 1 consumer (runtime_events replaces JSON trace)

Slices B-impl → B.1 → C-impl → C.1 complete. Close-out next.

**Slice C.1 audit (this commit):** CLOSED. Audit clean.
- JSON write always first: confirmed
- OperationalStore gated + non-fatal: confirmed
- No routing consumer of qz-runtime-state.json: confirmed
- qz-doctor does NOT read the JSON: confirmed
- /qz/control-plane is live authority: confirmed
- var/model-state.json + var/backend-state.json untouched: confirmed

**Slice D (this commit):** CLOSED. operational_store section added to
/qz/config/effective payload. Shows enabled, available, schema_version,
recent_events (limit 10), runtime_facts (5 standard keys). Disabled mode
is a clean no-op. 6 new tests. 2664 tests PASS.

All three #46 close-out conditions are now met:
  - /qz/config/effective shows OperationalStore runtime events ✅
  - qz-doctor does not read qz-runtime-state.json ✅
  - Zero routing consumers of qz-runtime-state.json ✅

**#46 close-out (this commit):** CLOSED. qz-runtime-state.json no longer written.
QZ_RUNTIME_STATE_PATH removed from qz-env. runtime_state_snapshot record removed
from /qz/config/effective. qz-write-runtime-state updated to write solely to
OperationalStore. Model carryover uses OperationalStore effective_model fact.
2660 tests PASS. #46 closed.

## #37 next stream seam: proxy-local terminal suppression (Slice 2G)

**#37 Slices 1–2F.1 complete. Post-#56 stocktake complete.**

Completed helpers:
- `StreamHopState` (Slice 1) — per-hop state bundling
- `StreamDecision` (Slice 2B) — vocabulary dataclass
- `_reasoning_only_abort_reason()` (Slice 2B) — 14 tests
- `_should_suppress_duplicate_response_start()` (Slice 2C) — 6 tests
- `_should_inject_hop_budget_signal()` (Slice 2D) — 11 tests
- `_should_inject_context_pressure_signal()` (Slice 2E) — 12 tests
- `stream_timeout_kind()` (Slice 2F) — 5 tests; in `qz_stream_watchdog.py`

No `decide_stream_event()`. No tool lifecycle, terminal, continuation/repair extraction.

Next safe seam: **proxy-local terminal suppression** (Slice 2G).

Selected in `docs/stream-reducer-boundary-design.md §9J`.

Key facts:
- 3-condition boolean check at `qz_responses_stream.py:~1779`
- Pure: `is_terminal_stream_event(event_type, payload) and completed_call is not None and is_proxy_local`
- No state mutation, no SSE rendering, no tool execution
- Test gap: 4 unit tests needed before or alongside extraction
- Do NOT touch outer-loop conditions at lines ~2001 and ~2016

Proposed helper:
```python
def _should_suppress_proxy_local_terminal(
    event_type, payload, completed_call, is_proxy_local
) -> bool:
```

**Slice 2G (this commit):** CLOSED. `_should_suppress_proxy_local_terminal()` extracted.
4 unit tests added in `ProxyLocalTerminalSuppressionHelperTests`. 2604 tests PASS.
Outer-loop conditions at ~2021/~2037 untouched. Side-effect block unchanged.

**Slice 2G.1 (this commit):** CLOSED. Audit found semantic drift: helper used
`completed_call is not None` but original used `and hs.completed_call` (truthiness).
Fixed to `bool(completed_call)`. Call-site guard updated from `is not None` to
truthiness; `is_proxy_local` extracted to local variable. 1 boundary test added.
2605 tests PASS. No behaviour change in real flow.

**Slice 2H-design (this commit):** CLOSED. Outer-loop state inventory complete.

Candidate assessment:
  StreamRunState (terminal flags only) — Low risk; **next**
  Terminal event seam — High risk; skip
  Tool lifecycle seam — High risk; skip
  Continuation/repair — Very high risk; skip

First safe StreamRunState cluster: `sent_response_start`, `sent_terminal`, `sent_done`.
Must NOT include: `sequence`, `public_trace`, `working_body`, `repair_hops_used`.

Coverage gaps to fill in 2H-impl: `StreamRunStateTests` (defaults + independence + persistence).

See `docs/stream-reducer-boundary-design.md §9M` for full inventory and acceptance criteria.

**Slice 2H-impl (this commit):** CLOSED. `StreamRunState` added with
`sent_response_start`, `sent_terminal`, `sent_done`. In `run()`, 3 locals
replaced with `rs = StreamRunState.fresh()`. All uses updated to `rs.*`.
3 unit tests in `StreamRunStateTests`. 2608 tests PASS. No behaviour change.

**Slice 2H.1 (this commit):** CLOSED. Audit clean. `rs` confirmed outside hop loop,
all three terminal flag reads/writes correct, timeout handlers receive values not rs object,
no extra fields moved. One identity test added (`test_fresh_returns_new_instance_each_time`).
2609 tests PASS. No behaviour change.

**Finish-plan (this commit):** Slices 2I → 2I.1 → 2J-close-out defined.

#37 finish line: per-hop state (StreamHopState ✓), cross-hop terminal flags
(StreamRunState ✓), cross-hop timing/index arithmetic (StreamRunState after 2I),
pure helpers (7 extracted ✓), remaining side-effects explicitly bounded in place.
No decide_stream_event() required.

**Slice 2I-impl (this commit):** CLOSED. `StreamRunState` expanded with
`started_at`, `first_output_at`, `final_usage`, `output_index_offset`.
`fresh()` now requires `started_at: float`. 62 lines in `run()` updated to `rs.*`.
`completed_at`/sequence/public_trace/summary_started/working_body/repair remain locals.
6 new tests (total 11 in `StreamRunStateTests`). 2615 tests PASS.

**Slice 2I.1 (this commit):** CLOSED. Audit clean. All 7 StreamRunState fields confirmed.
No bare `started_at`/`first_output_at`/`final_usage`/`output_index_offset` in `run()`.
Method signatures unchanged. `completed_at`/sequence/public_trace/summary_started/
working_body/repair all remain locals. No code changes needed. 2615 tests PASS.

**Slice 2J close-out (this commit):** CLOSED. All acceptance criteria PASS.
Remaining locals (sequence, public_trace, summary_started, working_body, repair state)
documented as intentionally bounded in place. No decide_stream_event() required.
#37 CLOSED.

**#39 Slice A-design (this commit):** CLOSED (design only). search-config-contract.md
defines: config/default/search.json schema, precedence rules (QZ_SEARCH_CONFIG_PATH
> user > default > legacy SEARXNG_POLICY), compatibility with all existing env vars,
/qz/config/effective exposure plan, qz_tool_web.py integration path, slice roadmap.

**#39 Slice B-impl (this commit):** CLOSED. config/default/search.json,
config/example/search.json, proxy/qz_search_config.py loader, 23 tests.
Loader not yet wired into proxy startup. All validation PASS.

**#39 Slice B.1:** CLOSED. Fixed QZ_SEARCH_CONFIG_PATH to not inherit tracked default profiles. 1 new test.

**#39 Slice C-impl (this commit):** CLOSED. Wired into proxy startup and /qz/config/effective.
- quantzhai_proxy.py: _initialize_proxy_state() calls load_search_config(); stores as ProxyHandler.search_config_result
- qz_request_router.py: passes search_config_profiles to WebSearchRuntime
- qz_tool_web.py: search_config_profiles param; _valid_profiles() + profile resolution use v1 profiles as fallback
- qz_config_report.py: active_search_config section via effective_summary(); never exposes base URL
- 7 new tests (ActiveSearchConfigReportTests). 2691 tests PASS.

**#39 Slice C.1 (this commit):** CLOSED. Audit clean with 5 new profile-precedence tests.
  - Legacy web_search_profiles wins when present: confirmed
  - v1 profiles are fallback only when legacy has no entry: confirmed and tested
  - Same-name profile: legacy wins (tested)
  - None/empty search_config_profiles: handled safely
  - active_search_config stable and base-URL-free: confirmed
  - SEARXNG_* env compat: confirmed
  2696 tests PASS.

**#39 Slice D (this commit):** CLOSED. qz.profiles.v1 bundle search.default_profile
wired into SearchPolicySelection. Precedence: per-model override > bundle default >
search.json defaults.profile > auto. Routing rules stay in search.json/legacy policy.
8 new tests. 2704 tests PASS.

**#39 close-out (this commit):** CLOSED. All acceptance criteria PASS.
QZ_SEARCH_CONFIG_PATH added to qz-env. .env.example updated.
search-policy.json deprecation note added — remains as legacy compat only.
search.json is primary. Future removal → new issue. 2704 tests PASS.

## Reference: BrainCase Slice completion history

**Slices A through I.1 are complete (#53 CLOSED). #54 CLOSED.**

Read `docs/braincase-memory-tool-api.md` for the full slice history.

```text
Slice A:   COMPLETE — schemas + fixtures + 44 tests
Slice B:   COMPLETE — BrainCaseDB schema v3 + put/get/list/retire/supersede
Slice C:   COMPLETE — query_plan/search/inspect + FTS5 (80 total)
Slice C.1: COMPLETE — rebuild_fts_index / FTS backfill (92 total)
Slice D:   COMPLETE — qz_braincase_write.py helpers + write/update paths (1744 total)
Slice D.1: COMPLETE — conflict marker detection tightened
Slice E:   COMPLETE — qz_braincase_render.py + render_pack/braincase_render_packet + 53 tests (1819 total)
Slice F:   COMPLETE — qz_braincase_tools.py + braincase.render tool surface + 64 tests (1906 total)
Slice G:   COMPLETE — braincase.recall semantics + tier routing + 124 tests (1966 total)
Slice G.1: COMPLETE — tier-bounded retrieval + deterministic enum + 141 tests (1983 total)
Slice G.2: COMPLETE — proxy-local dispatch for render+recall + 176 tests (2018 total)
Slice G.3: COMPLETE — dispatch test hardening + env param for factory (2021 total)
Slice H:   COMPLETE — candidate-only write exposure design (41 structural tests, 2062 total)
Slice H.1: COMPLETE — doctrine polished (68 design tests, 2089 total)
Slice H.2: COMPLETE — braincase.write_candidate runtime (57 runtime tests, 2146 total)
Slice H.3: COMPLETE — runtime polish: tier/record_type validation, case-insensitive markers (2164 total)
Slice H.4: COMPLETE — BrainCase smoke-test script scripts/qz-braincase-smoke (2183 total)
Slice I:   COMPLETE — operator review/promote CLI scripts/qz-braincase-review (2223 total)
Slice I.1: COMPLETE — status-filtered candidate listing; hidden candidates now surface (2239 total)
```

Slice G: what was done
- RECALL_MODE_TIERS dict: 5 predefined modes (task/project/procedure/artifact/open_loops)
- tiers_for_recall_mode() → returns bounded tier list or None for unknown modes
- BRAINCASE_RECALL_TOOL_DEF: recall_mode enum, required purpose/memory_domain
- braincase_recall_packet(): validates mode, resolves tiers (intersection-only narrowing),
  calls braincase_render_packet() — no raw records, no duplicate render logic
- braincase_recall_tool(): executor dispatching to braincase_recall_packet()
- BRAINCASE_HARNESS_POLICY: updated for both render and recall
- get_braincase_tool_definitions(): now returns [render_def, recall_def] when enabled
- Unknown mode → warning packet; empty tier intersection → warning packet; no fallback to all memory
- No automatic ingestion. No raw StateRecords. Disabled DB → safe warning.
- Tests: 124 tests in test_qz_braincase_tools.py, all passing

Slice H.4 adds the smoke-test script.

#54 BrainCase retention/lifetime policy — CLOSED after audit:
  Slice A: COMPLETE — policy matrix design + fixtures + 40 tests (2279 total)
  Slice B: COMPLETE — pure evaluator (2341 total)
  Slice B.1: COMPLETE — fail-closed rule matching (2346 total)
  Slice C: COMPLETE — retention-report + prune --dry-run (2372 total)
  Slice D: COMPLETE — prune --apply retire path (2404 total)
Do not expose braincase.write/update/search/inspect/promote_candidate directly.
No automatic ingestion at any step.

Full reference below for Slice C context:

```text
PARKED REFERENCE — do not implement until memory-write API design exists.

Previous slice 2 intent (for design reference only):
- consume extract_codex_request_context(); do not add another parser
- store sessions, turns, requests, workspace candidates, resolved workspaces,
  session workspace bindings, and identity conflicts as provenance/scoping
  references attached to explicit memory/state records — not as a request log
- store structured metadata/digests/summaries, not giant raw request bodies
- DB open/write failures must not break proxy request handling
- record which configured memory_domain applied to stored facts, but do not
  infer/create domains or treat SQLite as the memory_domain registry
- do not change model-visible behaviour
- do not persist broad runtime signal history, stream telemetry, or recovery
  backoff state
- do not implement learned preferences, durable memory, profile-private memory,
  HSM/archive memory, promotion, recall, renderers, or repeated-read v2

Add tests for inserts, workspace resolution, unknown workspace, identity
conflict storage, request-body non-mutation, and non-fatal DB failure.

Keep the patch boring.
```

## Recently completed (2026-05-21 run — #63 retrieve action)

```text
#63  web_search retrieve action     — CLOSED (all slices delivered; live smoke passed)
     Slices: A-design, B-impl, B.1-audit, C-live-smoke
     Bug fixed: FSE freshness now uses fields.updated_at/published_at as fallback
     Known limit: max_results=8 and max_retrieved_chars=12000 ceilings → #64
```

## Active next chain: tool policy improvement

BrainCase work is paused until tool policy is improved and audited.

```text
#59  Tool coercion/advice policy audit   — CLOSED. Umbrella audit refreshed 2026-05-26 after
                                           #60/#63/#64/#67–#70/#72 closures. All original gaps
                                           resolved, closed, or handed off to #61. docs/tool-policy-audit.md updated.
#60  web_search quality improvements     — CLOSED. All slices A–close-out delivered.
#61  Native exec/tool advisory policy    — OPEN; depends on #59 (unblocked)
#62  apply_patch coercion audit          — CLOSED. Current scope complete; future fallback advisory needs a new issue.
#63  web_search retrieve action          — CLOSED. All slices delivered.
#64  Research-grade web_search budgets   — CLOSED. All slices A–D + capabilities introspection delivered.
```

After tool policy chain:
- #8  RFC/research — later
- #52 Upstream-blocked on TurboQuant — no action needed

## #65 Backend lifecycle control plane

```text
Goal: move Docker/backend lifecycle out of qz-up shell script into
      proxy BackendManager; qz-up starts proxy only; proxy autostarts
      backend; /qz/backend/* endpoints expose control.

Design doc: docs/backend-lifecycle-control-plane.md

Slices:
  A-design  — ✅ COMPLETE. Full design in docs/backend-lifecycle-control-plane.md.
  B1-impl   — ✅ COMPLETE. BackendManager skeleton + Docker command builder; 49 tests; 2870 pass.
  B2-impl   — ✅ COMPLETE. Lifecycle + proxy integration + endpoints + control-plane. 2892 pass.
  B3-impl   — ✅ COMPLETE. qz-up stripped; qz-down graceful+force; qz-backend added. 2924 pass.
  B.1-audit — ✅ COMPLETE. 4 bugs fixed; docker_cmd documented; 2929 pass.
  C-doc     — ✅ COMPLETE. Operator guide added; QZ_DOCKER_CMD guidance; duplicates cleaned.
  D-smoke   — ✅ COMPLETE. Cold-start confirmed (per issue comment); design doc not updated at close time.
  D.1       — ✅ COMPLETE. GPU offload gate; QZ_REQUIRE_GPU; log check; 2953 pass.
  D.2       — ✅ COMPLETE. Remove -e/--device flags; qz-docker-root-helper compat.
  D.3       — ✅ COMPLETE. CPU_Mapped false-positive fix; latest-signal-wins; 6 new tests.
  D.4       — ✅ COMPLETE. Wired OperationalStore to BackendManager in proxy main().
              Added _load_operational_store_optional() helper (fail-open, testable).
              BackendManager constructor now receives operational_store= at startup.
              7 new tests (BackendManagerConstructorStoreWiringTests × 3,
              ProxyLoadOperationalStoreTests × 4). 3636 tests pass. #65 closed.

Current audit: docs/backend-lifecycle-control-plane.md §0 (2026-05-26)
Design doc: docs/backend-lifecycle-control-plane.md §15 (operator guide)
```

## #64 Research-grade web_search budgets and modes

```text
Goal: replace hard-coded ceilings (max_results=8, max_retrieved_chars=12000)
      with named budget modes and operator-configurable absolute limits.

Budget modes:
  quick:  max_results=8,  max_searches=4,  max_opens=3,  max_retrievals=2,  max_chars=6000
  normal: max_results=12, max_searches=8,  max_opens=8,  max_retrievals=4,  max_chars=12000
  deep:   max_results=25, max_searches=20, max_opens=20, max_retrievals=10, max_chars=30000
  audit:  max_results=50, max_searches=40, max_opens=40, max_retrievals=20, max_chars=60000

web_search gains budget_mode argument.
Flat routing.max_* fields remain as compatibility fallback.
Operator-configurable absolute_max_* replace hardcoded ceilings.
Telemetry budget-exceeded events include budget_mode.
Default when no mode given: normal (document this decision in Slice A-design).

Slices:
  A-design  — ✅ COMPLETE. docs/search-config-contract.md §64 written.
  B-impl    — ✅ COMPLETE. budget_mode wired; hard ceilings removed; 2804 tests pass.
  B.1-audit — ✅ COMPLETE. Dead code removed; 10 new edge-case tests; 2814 pass.
  C-doc     — ✅ COMPLETE. Tool description expanded; §64.8 updated; budget table added.
  D-live-smoke — ✅ COMPLETE. deep=25 results, audit=50, chars per-mode. docs/search-config-contract.md §64.12.

Key code targets:
  proxy/qz_tool_web.py line 798  — WEB_SEARCH_MAX_RESULTS hard ceiling to remove
  proxy/qz_tool_web.py line 515  — WEB_SEARCH_RETRIEVE_MAX_CHARS_CEILING to replace
  config/default/search.json     — add budget_modes + absolute_max_* to routing
  config/example/search.json     — document new fields
```

## Maintenance rule

When a task changes direction, update this file in the same commit as the doc or
implementation change that caused it.

A stale task DAG is just a roadmap wearing novelty glasses.
