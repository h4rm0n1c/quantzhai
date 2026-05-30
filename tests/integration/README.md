# Integration tests

These tests require a live proxy on `http://127.0.0.1:18180` with the model loaded.

```bash
# From repo root:
python3 tests/integration/test_sandbox_escalation_live.py
python3 tests/integration/test_network_escalation_live.py
```

They are NOT picked up by `pytest` (no `class`/`def test_` structure) and must
be run manually against a running proxy.
