# Codex Request Signal Inventory

**Verdict: Needs one more parser slice**

# Executive summary
Codex sends a rich set of session, thread, turn, and workspace signals to the `/v1/responses` proxy. QuantZhai currently parses basic session and turn metadata from headers but misses critical body-level signals like `previous_response_id` and `prompt_cache_key`, as well as specialized headers for sub-agents and memory generation. Before implementing the Phase 1 SQLite substrate, QuantZhai should extend its parser to capture these missing signals to ensure session continuity and correct routing.

# Codex signals found

| Name | Location | Meaning | Handling | Action | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `session_id` | Header / Body | Primary client session ID. | Parsed | Persist in Phase 1 DB | High |
| `thread_id` | Header / Body | Primary client thread ID. | Parsed | Persist in Phase 1 DB | High |
| `turn_id` | Metadata Header | ID for a single user interaction (turn). | Parsed | Persist in Phase 1 DB | High |
| `x-client-request-id` | Header | Client-generated trace ID for the request. | Parsed | Capture raw | High |
| `x-codex-window-id` | Header | `{thread_id}:{window_generation}`. | Parsed | Persist in Phase 1 DB | High |
| `x-codex-turn-state` | Header | Turn-scoped sticky routing token (Base64). | Ignored | Capture raw | High |
| `x-codex-installation-id` | Header | Unique ID for the Codex installation. | Ignored | Capture raw | High |
| `x-codex-parent-thread-id`| Header | Links current thread to a parent thread. | Ignored | Capture raw | High |
| `x-openai-subagent` | Header | Identifies sub-agent (e.g. `generalist`). | Ignored | Capture raw | High |
| `x-openai-memgen-request` | Header | Flags request as memory generation task. | Ignored | Capture raw | High |
| `client_metadata` | Body | W3C tracing and client-side context. | Ignored | Parse & Persist | High |
| `prompt_cache_key` | Body | Pinning request to server cache instance. | Ignored | Parse & Persist | High |
| `previous_response_id` | Body | Links to previous response (stateful). | Ignored | Parse & Persist | High |
| `service_tier` | Body | Requested service tier (e.g. `pro`). | Ignored | Parse | High |
| `reasoning` | Body | Reasoning effort (`low`, `medium`, `high`).| Ignored | Parse | High |
| `verbosity` | Body | Controls output length/detail. | Ignored | Parse | High |
| `output_types` | Body | Requested output types (e.g. `["text"]`). | Ignored | Parse | High |
| `workspaces` | Metadata Header | Local repo roots, remotes, and git state. | Parsed | Persist (diagnostic) | High |
| `turn_started_at_unix_ms`| Metadata Header | Turn start timestamp. | Parsed | Persist | High |

# Highest-value missing signals
1.  **`previous_response_id`**: Critical for rebuilding conversation state and supporting the stateful `/v1/responses` contract.
2.  **`prompt_cache_key`**: Essential for efficient local caching and routing consistency.
3.  **`client_metadata`**: Contains tracing and detailed client context (e.g. `cwd`, `personality`) not present in headers.
4.  **`x-openai-subagent`**: Necessary for distinguishing between main agent turns and sub-agent work.

# Recommended parser changes before SQLite
- **`proxy/qz_codex_metadata.py`**:
  - Add `extract_codex_body_metadata(body: dict)` to parse `previous_response_id`, `prompt_cache_key`, `service_tier`, `reasoning`, and `client_metadata`.
  - Update `CodexIdentity` to include `installation_id`, `subagent`, and `is_memgen`.
  - Update `extract_codex_identity` to capture `x-codex-turn-state`, `x-codex-installation-id`, `x-openai-subagent`, and `x-openai-memgen-request`.
  - Add logic to merge body-level metadata with header-level identity.

# Recommended DB columns/tables
- **`sessions`**:
  - Add `client_installation_id TEXT NULL`
- **`requests`**:
  - Add `previous_response_id TEXT NULL`
  - Add `prompt_cache_key TEXT NULL`
  - Add `subagent TEXT NULL`
  - Add `is_memgen INTEGER DEFAULT 0`
  - Add `service_tier TEXT NULL`
  - Add `reasoning_effort TEXT NULL`
  - Add `client_metadata_json TEXT NULL`
  - Add `turn_state_raw TEXT NULL` (Capture `x-codex-turn-state`)

# Things to defer
- **WebSocket-only behavior**: QuantZhai currently focuses on HTTP/SSE. Defer full WebSocket state machine.
- **`previous_response_id` chain resolution**: Capture the ID, but defer the logic to walk the chain for context injection.
- **`x-codex-turn-state` decoding**: Capture the raw token, but do not attempt to decode its internal schema.
- **Cross-workspace memory sharing**: Focus on single-workspace isolation for Phase 1.

# Tests to add
- `test_parse_body_metadata_extracts_response_id_and_cache_key`: Proves the new body parser works.
- `test_extract_identity_captures_subagent_header`: Proves sub-agent signaling is captured.
- `test_extract_identity_captures_turn_state_token`: Proves the sticky routing token is captured.
- `test_merge_identity_header_and_body_consistency`: Proves session/thread IDs in headers match those in body metadata.

# Open questions
- Does `responsesapi_client_metadata` in the body always match the content of `client_metadata` or are they used for different layers (SDK vs CLI)?
- How does `x-codex-turn-state` interact with local model switching if the proxy routes to a different backend?
- Is `prompt_cache_key` derived by the client from the prompt content, or is it a random affinity token?
