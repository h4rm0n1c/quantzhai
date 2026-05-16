# BrainCase Retention and Lifetime Policy

Date: 2026-05-16

Status: design doc for #54 Slice A. No runtime pruning implementation yet.

---

## 1. Purpose

BrainCase retention policy defines how StateRecords age, become stale, or get
retired. It answers: when should a memory record stop being actively surfaced,
and when should it be retired from the candidate queue or active memory?

This policy covers QuantZhai / LimbiCore agent-memory records:

- project constraints and decisions
- reusable procedures
- diagnostic observations
- open questions and uncertainties
- recent topics and working state
- artifact and source references
- preferences and constraints

**This is not HSM psychological lifecycle modelling.** It does not model
quiescent decay, affective memory cycles, anchor loss, identity continuity,
or human psychological state. See `docs/braincase-architecture-landscape-and-scope.md`
for the scope boundary.

---

## 2. Core rules (hard constraints)

These rules are non-negotiable regardless of configuration.

```text
NO automatic ingestion at any step.
NO automatic promotion of candidate records.
NO model-facing prune or promote tools.
NO raw DELETE of any record.
All pruning must use retire_state_record() or supersede_state_record(),
  producing a revision entry in qz_braincase_record_revisions.
Policy enforcement is operator CLI / manual action only.
  No automatic DB background jobs.
  No scheduled tasks.
memory_domain remains config/caller-owned.
  BrainCaseDB does not decide or authorise memory_domain values.
Candidate/internal records must NOT become active/renderable through retention policy.
  Retention policy may retire candidates; it may not promote them.
Durable records must NOT be auto-retired by age alone.
  Durable means operator-decided lifetime.
```

---

## 3. Retention values (intent definitions)

The `retention` field in StateRecord is an **intent** value, not a timer.
Policy interprets what that intent means in practice.

### ephemeral

Temporary working-state or short-lived diagnostic context.

```text
Intent:    record is expected to expire quickly; owner knows it is transient.
Examples:  working_state/recent_topic, temporary diagnostic notes,
           in-progress analysis that may be superseded by morning.
Policy v1: stale after 24h since last update; retire after 7d if unreviewed/low-importance.
```

### session

Useful across a work session or short run, but not expected to outlive the context.

```text
Intent:    record is relevant to the current project window, not permanently.
Examples:  session_state notes, open questions raised mid-session,
           recent decisions that may be refined or superseded.
Policy v1: stale after 3d; candidate retire after 14d; active records: mark stale only.
```

### project

Useful while the project remains active; expected to have project-scale longevity.

```text
Intent:    owner expects this to remain relevant across many sessions.
Examples:  project_state, project_decision, diagnostic, constraint,
           open_question with long-range resolution expected.
Policy v1: surface stale-review hints after 30–90d; no automatic retirement of active records.
           Candidates may be retired after configurable threshold if unreviewed.
```

### durable

Long-lived constraint, preference, procedure, or source-backed decision.

```text
Intent:    owner expects permanent or very long-term relevance.
Examples:  preference_constraint_memory, procedural_memory, semantic_memory,
           durable project decisions backed by evidence.
Policy v1: never auto-retire by age alone; keep until explicit operator retire/supersede;
           surface review-overdue hints after configurable threshold.
```

---

## 4. Policy matrix (v1 default)

Retention policy is **multi-axis**. The same retention value may have different
behaviour depending on tier, record_type, status, and importance/confidence.
No single global expiry applies to all records.

### Candidate records

Candidates are treated more aggressively than active records.
They have not been reviewed; allowing them to accumulate indefinitely creates noise.

| Retention | Default stale_after | Default expire_after | Action |
|---|---|---|---|
| ephemeral | 24h | 7d | retire (status→retired, reason="retention_expired_candidate") |
| session | 3d | 14d | retire |
| project | 14d | 60d | retire |
| durable | 30d | null | review-overdue hint only; operator must explicitly retire |

**Candidate expiry means:**
- `status = "candidate"` → `status = "retired"`
- `reason = "retention_expired_candidate"`
- Revision recorded in `qz_braincase_record_revisions`

**Candidate expiry does NOT mean:**
- raw DELETE
- promotion to active
- visibility change to renderable
- any model-facing action

### Active records (renderable)

Active records represent promoted, reviewed memory. Handle more carefully.

| Retention | Default stale_after | Default expire_after | Action |
|---|---|---|---|
| ephemeral | 48h | 7d | retire after operator explicit prune --apply |
| session | 7d | 30d | surface stale hint only; operator retires manually |
| project | 90d | null | surface stale-review hint only; no auto-retire |
| durable | null | null | keep; only explicit operator retire/supersede |

**Stale is a computed/reporting state in v1, not a DB-stored status.**
(Option A: dry-run/report only; no schema change needed for v1.)

The StateRecord schema has no "stale" status field. Rather than adding one in
v1, the prune command will report stale records without writing to the DB unless
`--apply` is given. Stale marking via metadata may be added in a future slice.

### Active records (internal)

Records that are active but visibility=internal follow the same retention matrix
as renderable records. They are not visible to render/recall but are still
live memory. Handle identically to active/renderable for retention purposes.

---

## 5. Retention decision model

Future implementation must expose a pure function:

```python
def retention_decision_for_record(
    record: dict,
    *,
    now_ms: int,
    policy: dict,
) -> dict:
    """Return a retention decision for a single StateRecord.

    Pure function: no DB writes, no side effects.
    Deterministic given the same inputs.
    Not model-facing.

    Returns:
      {
        "record_id": str,
        "action": "keep" | "stale" | "retire",
        "reason": str,
        "age_ms": int,
        "age_since_update_ms": int,
        "matched_rule": str | None,
        "dry_run": bool,
        "warnings": list[str],
      }
    """
```

Rules for the function:

- `keep`: record is within policy thresholds; no action needed.
- `stale`: record is past stale_after threshold; surface for review.
- `retire`: record is past expire_after threshold; eligible for retirement.

The function must never:
- write to the DB
- delete records
- promote candidates
- change visibility
- make model-facing calls

Operator CLI calls this function per record, then applies `retire_state_record()`
for records where `action == "retire"` when `--apply` is given.

---

## 6. Operator CLI design (future implementation)

These commands are planned for Slice B/C of #54. Not yet implemented.

```bash
# Report what would be pruned (default: dry-run)
scripts/qz-braincase-review prune --dry-run

# Apply pruning (retires eligible records via retire_state_record())
scripts/qz-braincase-review prune --apply

# Filter by scope
scripts/qz-braincase-review prune --dry-run --memory-domain coding
scripts/qz-braincase-review prune --dry-run --retention ephemeral
scripts/qz-braincase-review prune --dry-run --status candidate
scripts/qz-braincase-review prune --dry-run --older-than 7d

# Show stale records (not retiring, just reporting)
scripts/qz-braincase-review retention-report
scripts/qz-braincase-review retention-report --memory-domain coding --json
```

**Behaviour rules:**

- Default is always `--dry-run`. Writes require `--apply`.
- `--apply` uses `retire_state_record()` only. No raw DELETE.
- Active durable records are reported but never retired by `--apply`.
- Active project records are reported stale but not auto-retired by `--apply`.
- All retirements produce a revision in `qz_braincase_record_revisions`.
- `--older-than` measures from `updated_at_ms`, not `created_at_ms`.

---

## 7. Policy configuration shape

Policy is operator/config-owned, not DB-owned. BrainCaseDB does not enforce
policy by itself; the prune operator tool loads policy and applies it.

Default policy fixture: `docs/fixtures/braincase/retention/default-policy.json`

Schema identifier: `braincase/retention-policy@1`

### Rule match fields

Rules may match any combination of:

| Field | Type | Notes |
|---|---|---|
| `status` | string | candidate, active, retired, superseded |
| `retention` | string | ephemeral, session, project, durable |
| `tier` | string | any valid tier name |
| `record_type` | string | any valid record_type |
| `visibility` | string | internal, renderable, never_model_visible |
| `memory_domain` | string | operator-configured value (no enum, no registry) |
| `min_importance` | number | threshold for importance field |
| `max_importance` | number | threshold |
| `source_refs_required` | bool | record must have source_refs if true |

`memory_domain` in rules is a match value, not a registry entry. Any domain
string may be used. BrainCaseDB does not grant or authorise domain values.

### Rule action values

```text
keep    — no action; record is within policy.
stale   — surface for review; no DB write in v1.
retire  — eligible to retire; writes retire_state_record() when --apply.
```

Actions that are explicitly forbidden in policy rules:

```text
delete          — never allowed
promote         — never allowed (candidates remain candidates)
render          — never allowed (render boundary is separate from retention)
activate        — never allowed (status change is operator promote, not retention)
```

---

## 8. Memory tier retention guidance

Some tiers have strong natural retention affinity:

| Tier | Natural retention | Notes |
|---|---|---|
| working_state | ephemeral | Short-lived working context |
| session_state | session | Useful within a session window |
| project_state | project | Project-scoped decisions/status |
| semantic_memory | project / durable | Generalised knowledge |
| procedural_memory | durable | Reusable how-to; should not auto-expire |
| episodic_memory | project / durable | Evidence-backed events with provenance |
| artifact_memory | durable | Source references; must not auto-expire |
| perceptual_index | session / ephemeral | Short-lived signal summaries |
| preference_constraint_memory | durable | Stable user preferences; must not auto-expire |

**Important:** Tier provides guidance, not enforcement. A project_state record
may be marked `retention=durable` by the operator if it is permanently relevant.
Retention on the record overrides tier default hints.

---

## 9. LimbiCore / HSM boundary

This retention policy is for **LimbiCore agent-memory records** in QuantZhai.

It does **not** model:
- Quiescent decay (HSM baseline restoration)
- Affective memory clusters (HSM trigger cycle)
- Anchor loss and recovery (HSM stabilisation model)
- Identity continuity over time (HSM human-emulation goal)
- Human psychological lifecycle state

If HSM later uses BrainCaseDB with `memory_domain="hsm"`, it may supply its
own HSM-specific retention policy rules above this layer — for example, rules
that prevent automatic retirement of episodic memory regardless of age, or that
apply different thresholds to evidence-backed vs. inferred records.

The retention framework here is the substrate. Domain-specific policy is the
operator/project's responsibility to configure.

---

## 10. Failure modes to avoid

These failure modes are documented so future implementors can design against them.

| Failure mode | Description | Prevention |
|---|---|---|
| Global-expiry deletes durable memory | One expiry rule retires procedural/durable records | Multi-axis rules; durable → never auto-retire |
| Candidates accumulate forever | No expiry on candidate queue | candidate_ephemeral/session expiry rules |
| Stale project records stay active | project-tier records never surface for review | stale-review hints at 30–90d threshold |
| Pruning raw-deletes evidence | retire_state_record() replaced by raw DELETE | Hard rule: no DELETE; revisions always recorded |
| Policy promotes candidates | Retention logic changes status to active | Forbidden action in policy rules |
| Policy changes visibility | Retention sets visibility=renderable | Forbidden action in policy rules |
| One-size-fits-all expiry | Same threshold applied to working_state and procedural_memory | Policy matrix by tier+retention |
| memory_domain registry creep | Policy rules become an authoritative domain list | memory_domain in rules is match-only, not registry |
| Auto-retirement of active durable | Prune CLI retires durable records without explicit operator intent | action=keep for durable; no --apply writes for durable |

---

## 11. Slice B — COMPLETE: pure retention evaluator

Slice B implemented `proxy/qz_braincase_retention.py`:

```text
parse_duration_ms(value) -> int | None
  Parses "60s", "15m", "24h", "7d", "30d" etc. into milliseconds.
  Returns None for None, invalid, negative, or unsupported unit.
  No exceptions for ordinary invalid input.

load_default_retention_policy() -> dict
  Loads docs/fixtures/braincase/retention/default-policy.json.
  Returns a fresh copy on each call.
  Falls back to minimal safe policy if fixture unavailable.

retention_decision_for_record(record, *, now_ms, policy) -> dict
  Pure function. Deterministic. No DB writes. Not model-facing.
  Returns: {record_id, action, reason, age_ms, age_since_update_ms,
            matched_rule, dry_run=True, warnings}
  action is always keep | stale | retire.
  delete/promote/activate/render are forced to keep with a warning.
  Active durable records are always kept (hard override).
  Retired/superseded records return already_inactive.
  Threshold logic: expire_after → rule action; stale_after (pre-expiry) → stale;
    neither → keep ("within_retention_window").
```

Tests: tests/test_qz_braincase_retention.py — 62 tests. Full suite: 2341.

Slice C should add a dry-run/report surface:
  qz-braincase-review retention-report (dry-run output only)
  qz-braincase-review prune --dry-run (candidate/ephemeral expiry reporting)

Slice D should add the apply path (retire_state_record() calls, operator --apply).

**Schema change recommendation:** The v1 design avoids schema changes.
If stale marking is needed in a future slice, consider adding
`metadata.retention_stale_at_ms` rather than a new top-level status value,
to avoid a schema migration.

---

## Cross-references

- `docs/braincase-architecture-landscape-and-scope.md` — scope boundary, LimbiCore positioning
- `docs/braincase-memory-tool-api.md` — tool plane design, slice history
- `docs/fixtures/braincase/retention/default-policy.json` — v1 default policy fixture
- `docs/fixtures/braincase/retention/sample-decisions.json` — example decision outputs
- `proxy/qz_braincase_db.py` — retire_state_record(), promote_state_record()
- `proxy/qz_braincase_review.py` — review helpers (future: prune helpers)
- `scripts/qz-braincase-review` — operator CLI (future: prune subcommand)
- Issue #54 — retention/lifetime policy (parent issue)
