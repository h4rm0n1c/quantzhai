# Codex Header Capture Verdict

Date: 2026-05-12T13:59:37.605471+08:00
Source: recent `var/captures/requests/*/incoming-request-headers.json` files

Status: local protocol-discovery summary. Raw captures are not committed.

## Files inspected

- Count: 4

## Header key counts

- `accept`: 4
- `authorization`: 4
- `content-length`: 4
- `content-type`: 4
- `host`: 4
- `originator`: 4
- `session_id`: 4
- `user-agent`: 4
- `x-client-request-id`: 4
- `x-codex-turn-metadata`: 4
- `x-codex-window-id`: 4

## Interesting header verdict

### `session_id`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `019e1ac2-b40c-7b03-ade6-4c7d8814af8c`

### `session-id`

- Found: no
- Count: 0

### `thread_id`

- Found: no
- Count: 0

### `thread-id`

- Found: no
- Count: 0

### `originator`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `codex_exec`

### `authorization`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `[present; redacted in committed summary]`

### `cookie`

- Found: no
- Count: 0

### `user-agent`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `codex_exec/0.125.0 (Linux Unknown; x86_64) xterm (codex_exec; 0.125.0)`

### `User-Agent`

- Found: no
- Count: 0

### `content-type`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `application/json`

### `Content-Type`

- Found: no
- Count: 0

### `accept`

- Found: yes
- Count: 4
- Example file: `var/captures/requests/qz_req_1778565428276_fad0/incoming-request-headers.json`
- Example value: `text/event-stream`

### `Accept`

- Found: no
- Count: 0

### `openai-organization`

- Found: no
- Count: 0

### `openai-project`

- Found: no
- Count: 0

## Interpretation

- client session id: present in incoming headers. QuantZhai should map it as nullable external identity while keeping `qz_session_id` primary.
- client thread id: absent in inspected incoming headers. Phase 1 should use proxy-owned synthetic session IDs only.
- `authorization`: present in raw local captures. Keep raw header captures local/ignored; commit summaries only.

## Recommended next action

- Review this verdict before DB implementation.
- If session/thread IDs are present, update the state/memory plan to map them as external client IDs.
- If absent, keep the synthetic-session Phase 1 decision.

