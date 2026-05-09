# Codex Native First Request Capture

Status: local reference capture created 2026-05-09.

This note records the cleanest local capture we currently have of the request
shape Codex CLI sends at the start of an `exec` turn before QuantZhai normalizes
or rewrites anything.

Raw capture artifacts are intentionally kept under ignored runtime state:

```text
var/captures/reference/clean-native-codex-first-packet-2026-05-09/
```

Do not commit the raw request body without a separate redaction review. It may
contain local paths, environment context, harness text, plugin/tool metadata, or
other session-specific data.

## Capture Method

The capture was made with:

- plain `codex exec`
- a neutral working directory: `/tmp/qz-native-codex-work`
- `--ignore-user-config`
- `--ignore-rules`
- `--skip-git-repo-check`
- model `gpt-5.5`
- no `scripts/qz-codex`
- no QuantZhai proxy
- no QuantZhai model catalog
- a temporary local capture-only Responses server on `127.0.0.1:18184`

The local capture server only existed to receive and record the HTTP request
body. It returned a minimal synthetic streamed Responses completion. The capture
therefore proves Codex CLI's request envelope and tool declaration shape, not
OpenAI server behavior.

## Captured Shape

Reference files:

```text
var/captures/reference/clean-native-codex-first-packet-2026-05-09/manifest.json
var/captures/reference/clean-native-codex-first-packet-2026-05-09/request.headers.json
var/captures/reference/clean-native-codex-first-packet-2026-05-09/request.raw-client.json
```

Verification marker:

```text
clean-native-codex-capture-2026-05-09
```

Observed top-level request keys:

```text
client_metadata
include
input
instructions
model
parallel_tool_calls
prompt_cache_key
reasoning
store
stream
text
tool_choice
tools
```

Observed summary:

```text
model: gpt-5.5
top-level instructions: 21335 chars
input items: 3
tools: 18
```

Input layout:

```text
input[0]: message/developer, permissions and execution harness, 5655 chars
input[1]: message/user, environment context, 227 chars
input[2]: message/user, first user prompt, 88 chars
```

Declared tool order:

```text
0  function          exec_command
1  function          write_stdin
2  function          list_mcp_resources
3  function          list_mcp_resource_templates
4  function          read_mcp_resource
5  function          update_plan
6  function          request_user_input
7  tool_search       <native>
8  function          tool_suggest
9  custom            apply_patch
10 web_search        <native>
11 image_generation  <native>
12 function          view_image
13 function          spawn_agent
14 function          send_input
15 function          resume_agent
16 function          wait_agent
17 function          close_agent
```

## Interpretation

The native request has both:

- a large top-level `instructions` field
- a developer message inside `input`

So "the tool prompt" is not a single separate field in this capture. Codex sends
instructional/harness material through the top-level `instructions`, developer
messages, and the structured `tools` array. Any QuantZhai prompt or tool policy
must preserve that distinction:

- top-level `instructions` can be replaced or amended by prompt policy
- developer/user `input` items need explicit replay/normalization rules
- structured tools need adapter logic, not text-only prompt replacement

This is the baseline to use when checking whether QuantZhai has accidentally
changed Codex's native first-turn shape.

## Sanitized Regression Fixture

A redacted fixture based on this capture lives at:

```text
tests/fixtures/responses_input/native_codex_first_request_shape.json
```

It intentionally keeps only the structural facts needed by the normalizer tests:
top-level instructions, developer harness replay, environment-context replay,
the first user prompt, and the native tool declaration order.

`tests/test_apply_patch_adapter.py` pins the current QuantZhai behavior for that
shape:

- prompt policy replaces the native top-level `instructions` with the selected
  QuantZhai/Codex prompt
- replayed harness and environment-context messages are removed from upstream
  `input`
- `write_stdin` is hidden unless request history proves there is a live exec
  session id
- custom `apply_patch` and native `web_search` declarations are adapted to
  upstream function tools
- unsupported native-only declarations such as `tool_search` and
  `image_generation` are dropped for llama.cpp

## Related Captures

Less-clean comparison captures also exist locally:

```text
var/captures/reference/direct-codex-first-packet-2026-05-09/
var/captures/reference/codex-first-packet-2026-05-09/
```

Those are useful for seeing QuantZhai/qz-codex effects, but they are not the
native baseline. Use `clean-native-codex-first-packet-2026-05-09` when the
question is what Codex itself sends before QuantZhai's wrapper/catalog/proxy
logic gets involved.
