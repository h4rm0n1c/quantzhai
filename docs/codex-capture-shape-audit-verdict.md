# Codex Capture Shape Audit Verdict

Date: 2026-05-12T12:53:14.416294+08:00
Source: docs/codex-capture-shape-audit.md

## Signal counts

- `"previous_response_id"`: 0
- `session_id`: 102
- `session-id`: 0
- `thread_id`: 0
- `thread-id`: 0
- `"function_call"`: 3250
- `"function_call_output"`: 1217
- `"exec_command"`: 3283
- `"shell_command"`: 0
- `"local_shell_call"`: 0
- `"shell_call"`: 0
- `"compaction"`: 2
- `README.md`: 129

## Decisions

- `previous_response_id`: absent
- `session_id/session-id`: present / absent
- `thread_id/thread-id`: absent / absent
- `function_call/function_call_output`: present / present
- `exec_command/shell_command`: present / absent
- `local_shell_call/shell_call`: absent / absent
- `README.md` repeated-read probe marker: present

## Interpretation

- Repeated-read v1 can remain input-history-seeded: function_call/function_call_output evidence exists in captures.
- previous_response_id does not appear in inspected captures. Phase 1 should not rely on it.
- Session/thread-like strings appear. Inspect whether they are headers/body metadata or just tool output/text before trusting them.

## Recommended next action

- Manually inspect the matching examples for previous_response_id/session_id/thread_id to classify them as real metadata vs text noise.
- If they are not real metadata, Phase 1 DB should use proxy-owned synthetic session IDs.
- Keep repeated-read v1 stateless and input-history-seeded.
