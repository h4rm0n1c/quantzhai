# BrainCase Architecture: Landscape and Scope Assessment

Date: 2026-05-15

Status: assessment document. Not a design spec or implementation guide.

---

## Purpose

After #53 closed, BrainCaseDB has grown from a design doc into a working memory
substrate with model-facing tools, operator CLI, and ~2239 tests. This document
audits what it has become, where it sits in the broader memory/RAG landscape,
and what scope it should and should not take on next.

This document does **not** propose new runtime features. It is an architectural
review for future decision-making.

---

## 1. What BrainCaseDB is now

### The working system

After slices A–I.1, BrainCaseDB is a working proof-of-concept memory substrate
for agent runtimes. It has:

**Storage layer:**
- SQLite-backed `qz_braincase_state_records` — the core state/memory store
- `qz_braincase_source_refs` — provenance attachments
- `qz_braincase_record_sources` — record→source join table
- `qz_braincase_record_revisions` — retire/supersede/promote history
- FTS5 full-text search index (optional, graceful fallback)
- Schema version tracking

**Memory model:**
- StateRecord: claim, summary, tier, record_type, status, visibility, confidence,
  importance, retention, source_refs, tags, metadata
- SourceRef: provenance links (file path, URL, commit hash, capture path)
- RenderPacket: bounded model-facing output (rendered_text + source_record_ids only)
- 9 memory tiers: working_state, session_state, project_state, semantic_memory,
  procedural_memory, episodic_memory, artifact_memory, perceptual_index,
  preference_constraint_memory
- memory_domain isolation (coding / hsm / roleplay / personal / utility / isolated)

**Model-facing tools (behind feature flags):**

| Tool | Flag(s) required | Output |
|---|---|---|
| `braincase.render` | `QZ_BRAINCASE_TOOLS_ENABLED` | RenderPacket |
| `braincase.recall` | `QZ_BRAINCASE_TOOLS_ENABLED` | RenderPacket |
| `braincase.write_candidate` | both flags | WriteCandidateResult |

**Candidate-only write discipline:**
- `braincase.write_candidate` forces `status=candidate, visibility=internal`
- Model cannot create active or renderable memory directly
- Operator must use `scripts/qz-braincase-review promote` for active/renderable
- Rejected candidates are retired via `scripts/qz-braincase-review reject`

**Deterministic helper layer:**
- `redaction_check()` — rejects forbidden raw-content fields (hard block)
- `scope_resolve()` — confirms memory_domain within caller-supplied allowed set
- `dedup_check()` — near-duplicate detection (hint only, non-blocking)
- `conflict_check()` — opposing constraint marker detection (hint only, non-blocking)
- `source_link()` — provenance linking; missing refs are warnings not errors
- `render_pack()` — token-budget-bounded RenderPacket assembly with tier filtering
- `_recall_candidate_records()` — tier-bounded retrieval before limit

**Operator tooling:**
- `scripts/qz-braincase-review` — list / inspect / promote / reject
- `scripts/qz-braincase-smoke` — 12-check tool-loop smoke test
- Promotion records a revision with `operation=promote`
- Reject records a revision via the existing `retire_state_record()` path

### Core properties that define the system

1. **Explicit write discipline.** No record is created without explicit intent.
   The model can nominate candidates; it cannot make them active.

2. **Render boundary.** Records are invisible until explicitly rendered.
   `eligible_for_render()` requires `status=active, visibility=renderable`.
   Candidates, internal, superseded, and retired records are excluded.

3. **Source provenance is first-class.** SourceRefs are not optional metadata;
   they are stored entities with their own table and retrieval path.

4. **memory_domain isolation.** No cross-domain access by default.
   BrainCaseDB stores the configured value as-is and never infers domains.

5. **Bounded model-visible output.** The model sees only RenderPacket or
   WriteCandidateResult — never raw StateRecords, source blobs, or SQL.

6. **Deterministic helpers accelerate, not judge.** Dedup/conflict are hints.
   The LLM reasons about relevance; helpers handle mechanics.

---

## 2. What BrainCaseDB is not

Understanding boundaries is as important as understanding capabilities.

**Not HSM:**
BrainCaseDB is a coding-agent memory substrate. It does not implement quiescent
state, affective trigger modelling, anchor theory, or human identity emulation.
It is not an upload project. It is not character continuity infrastructure.

**Not a human upload system:**
BrainCaseDB stores durable project facts, constraints, procedures, and
preferences — not reconstructed personhood, not identity state, not
psychological profile data.

**Not a character card:**
A character card flattens a person into fixed traits. BrainCaseDB intentionally
preserves uncertainty, tier diversity, and source provenance to avoid this.

**Not a diary:**
Records are explicit memory writes, not automatic transcriptions of every
session or conversation turn.

**Not a telemetry warehouse:**
Stream events, tool calls, session metadata, and telemetry are not stored in
BrainCaseDB. They belong in captures and the telemetry module.

**Not a request/session/recovery store:**
Request IDs, session IDs, turn IDs, and recovery backoff state may appear as
SourceRef provenance attached to a StateRecord — never as automatic logs.

**Not a memory_domain registry:**
BrainCaseDB stores the domain value supplied by the caller. It does not define,
infer, normalize, grant, or authorize domain values. That authority belongs to
operator/profile configuration.

**Not an automatic self-writing model memory:**
The model cannot write active/renderable memory directly. It can only create
candidate records for operator review. This is the key departure from most
current agent memory systems.

---

## 3. How it differs from typical memory/RAG systems

### Vector-store RAG memory

Typical RAG stores document chunks as embeddings, retrieves by cosine similarity,
and injects retrieved chunks into context.

**Where BrainCaseDB differs:**
- No vector embeddings (FTS + exact/tag search instead)
- Explicit tiers and memory_domain scoping vs. flat corpus
- Render boundary: records are not context-injected automatically
- Provenance is first-class; RAG systems typically don't track source discipline
- No "save every chunk" — explicit writes only

**Where BrainCaseDB is similar:**
- Uses search to find relevant records before rendering
- Recall produces a bounded context injection (the RenderPacket)
- FTS is conceptually similar to keyword/hybrid retrieval

### Conversation summary memory

These systems (OpenAI's built-in memory, basic ChatGPT memory) extract facts
from conversations and write them to a flat key-value or paragraph store.
They inject summaries back into system prompts.

**Where BrainCaseDB differs:**
- No automatic extraction on every conversation turn
- Candidate discipline: the model must explicitly call write_candidate
- Operator promotion gate: model-suggested memories don't become active without review
- Structured tiers and record types, not flat paragraphs
- Source provenance attached to each record

**Where BrainCaseDB is similar:**
- End goal is similar: surface relevant memory into context
- RenderPacket injection is conceptually close to summary injection

### MemGPT-style archival/working memory

MemGPT (Packer et al., 2023) introduced explicit memory tiers: working memory
(context window), archival memory (long-term storage), and recall memory
(recent history). The LLM uses tools to read/write/search these tiers.

**Where BrainCaseDB is most similar:**
- Explicit memory tiers (BrainCaseDB has 9, MemGPT has 3)
- LLM uses tools to interact with memory
- Memory is not automatically injected — retrieval is explicit
- Search-backed recall

**Where BrainCaseDB differs:**
- Candidate-only writes with operator promotion gate (MemGPT writes directly)
- Source provenance as a first-class table (MemGPT doesn't model this)
- Render boundary (MemGPT writes/reads are symmetric)
- memory_domain isolation (MemGPT doesn't have multi-domain isolation)
- Conflict/dedup hint layer
- Operator review CLI for promotion

### Graph memory systems

Graph memory (e.g., Zep's temporal knowledge graph) stores entities, relations,
and events as graph nodes with timestamps. Retrieval uses graph traversal.

**Where BrainCaseDB differs:**
- Flat relational store, not a graph (record_links exist but are sparse)
- No entity-relation modelling
- Tag/FTS search, not graph traversal

**Where BrainCaseDB might grow toward:**
- The `record_links` table and `supersedes`/`superseded_by` fields are the
  embryo of a provenance graph. Future work could grow this without changing
  the flat storage model for most records.

### Commercial agent memory systems (Mem0, Zep-style)

Systems like Mem0 or Zep Cloud auto-extract memories from conversations,
embed them, and inject relevant ones back via search.

**Where BrainCaseDB differs:**
- No auto-extraction from conversations (no automatic ingestion)
- Candidate-only writes — model nominates, operator approves
- FTS + exact search, not embedding search
- Render boundary vs. direct system prompt injection
- Much smaller and more controllable

**Where BrainCaseDB is behind:**
- No semantic similarity search (FTS is weaker for fuzzy recall)
- No automatic memory hygiene (retention/lifetime policy is #54, not yet implemented)
- Smaller scale; not cloud-deployed

### Summary comparison table

| Property | VectorRAG | Conversation Summary | MemGPT-style | BrainCaseDB |
|---|---|---|---|---|
| Write trigger | Auto | Auto (extraction) | Explicit tool call | Candidate-only + operator gate |
| Retrieval | Semantic similarity | Keyword/pattern | Search + archival | FTS + exact + tag |
| Source provenance | Typically no | No | No | Yes (first-class table) |
| Render boundary | No (direct injection) | No (direct injection) | No (symmetric read/write) | Yes (RenderPacket only) |
| Memory tiers | None (flat) | None (flat) | 3 (working/archival/recall) | 9 |
| Domain isolation | No | No | No | Yes (memory_domain) |
| Conflict/dedup | No | No | No | Hint layer (non-blocking) |
| Operator review | No | No | No | Yes (CLI promote/reject) |
| Candidate state | No | No | No | Yes (status=candidate) |

---

## 4. HSM influence without HSM scope creep

HSM (Human State Machine) is an evidence-backed human-emulation architecture
for a different and larger project. BrainCaseDB borrows conceptual discipline
from HSM without taking on its scope.

### What BrainCaseDB has borrowed from HSM principles

**Evidence vs inference boundaries:**
HSM insists on distinguishing raw evidence from extracted fact from model
inference. BrainCaseDB's candidate-only write path enforces a version of this:
the model proposes (candidate, internal); the operator confirms (active).
"Generated output is not automatically memory" is the shared rule.

**Update gate:**
HSM's integrity loop gates durable state updates after checking. BrainCaseDB's
candidate→operator promotion is a simpler version of the same discipline.

**Source/provenance discipline:**
HSM's provenance graph and SourceRef design directly influenced BrainCaseDB's
`source_refs` table and the `SourceRef` schema. Every durable claim should be
traceable to its origin.

**Uncertainty/contradiction preservation:**
HSM preserves contradictions rather than flattening them. BrainCaseDB's
`conflict_check()` surfaces opposing constraint markers as hints — it does not
silently resolve them.

**Explicit state over vibes:**
HSM rejects "character card" simplification. BrainCaseDB's tiers and record
types (constraint, preference, episode, open_question, etc.) encode more
structure than a flat paragraph store.

**Render boundary:**
HSM's runtime packet is compiled for the task; the archive is not dumped into
context. BrainCaseDB's RenderPacket and `eligible_for_render()` enforce the
same boundary.

### What BrainCaseDB must not yet implement (HSM scope)

The following are HSM scope, not QuantZhai agent-memory scope. They should
not be built into BrainCaseDB or QuantZhai without a separate explicit design
decision and a separate dedicated repo/issue:

**Quiescent state modelling:**
Baseline activation, resting-state anchors, destabilising cues, recovery
patterns. This requires understanding a human subject's psychological baseline.
It has no natural home in a coding agent memory substrate.

**Affective trigger state:**
Trigger cycle (incident → reaction → partial activation → behaviour → delayed
reasoning → explanation), affective memory clusters, reaction-vs-explanation
separation. This is human psychology modelling, not coding-agent task state.

**Anchor modelling:**
Stabilising anchors (person, role, routine, place, project, value). These are
human continuity constructs, not project-state records.

**Human runtime packet compiler:**
Compiling a state packet for human-emulation tasks (quiescent + affective +
evidence + identity). BrainCaseDB's RenderPacket is a bounded tool output, not
a human state packet.

**Identity emulation:**
"The model is not the person" — this is an HSM constraint specifically because
HSM is trying to emulate a human. BrainCaseDB does not emulate a person; it
stores coding-agent project memory.

**Upload-style continuity claims:**
HSM is designed around the question of whether a human's state can be preserved
and run. BrainCaseDB does not address this and should not.

### The correct mental model

```text
HSM:    evidence archive → extract → provenance → human state compiler
          → model execution → integrity loop → durable state update
          Goal: coherent emulation of a human subject over time

BrainCaseDB:  LLM + harness → memory tools → deterministic helpers
              → BrainCaseDB → renderers → scoped model-visible packets
              Goal: reliable, explicit, bounded agent project memory
```

BrainCaseDB is a building block that HSM may eventually use as part of its
storage substrate (using `memory_domain="hsm"` for isolation). It is not HSM.

---

## 5. Current architectural judgement

### Are we on a good path?

**Yes, on balance.**

The candidate-only write path is the most important design decision made in
#53. Compared to auto-ingesting agent memory systems, the explicit write +
operator gate pattern prevents:

- model hallucinations becoming durable memory
- session noise accumulating as "memory"
- model self-authoring its own constraints and preferences without oversight
- poisoned recall (a model could write misleading candidates, but they don't
  become renderable until a human reviews them)

The render boundary is the second important decision. Records being invisible
until explicitly rendered prevents unsolicited context injection and gives
the operator control over what the model sees.

### What risks have appeared?

**Risk 1: FTS-only retrieval is weak for fuzzy recall.**
Full-text search returns exact token matches. For coding facts like "use typed
returns" this is fine. For conceptual recall ("what did we decide about
authentication?"), FTS may miss relevant records. Semantic/vector search would
help but adds significant complexity.

**Risk 2: No retention/lifetime policy yet.**
Candidates accumulate. Active records don't decay. A long-running session will
grow a review queue with no automatic pruning. This is #54 scope and should be
addressed before the system is used seriously.

**Risk 3: Complexity vs. value gap.**
BrainCaseDB now has ~2239 tests, multiple helper modules, and a complete CLI.
The system is more complex than most agent memory systems. The value must justify
the complexity. In practice, the complexity is doctrinal and well-bounded — but
it should not grow further without clear use-case pressure.

**Risk 4: memory_domain discipline requires operator configuration.**
memory_domain is config-owned, which is the right design. But it means the
system is useless without explicit domain configuration. Future onboarding docs
should make this clear.

### What should stay boring?

**Keep:**
- SQLite as the storage engine (not a graph DB, not vector DB)
- FTS + exact + tag search (not vector embeddings, not LLM-graded retrieval)
- Flat StateRecord schema (not entity-relation graph modelling)
- Candidate-only writes from the model
- Operator-only promotion
- memory_domain as a simple string (not a registry)
- Render budget enforcement
- Bounded output (no raw record dumps)

The system's value comes from its discipline, not from features. Adding vector
search, graph edges, auto-extraction, or semantic scoring would all be
incremental complexity with diminishing returns at this scale.

### What should be deferred?

**Defer:**
- Vector/semantic search (FTS is sufficient for explicit project memory)
- Graph traversal (record_links can stay sparse)
- Multi-tenant / cross-agent memory (memory_domain isolation handles this)
- HSM constructs (quiescent state, affective triggers, anchor modelling)
- Automatic retention decay (operator CLI prune commands per #54 are sufficient)
- Automatic memory extraction from conversations (#53 explicitly excluded this)
- Embedding-based dedup (exact claim comparison is sufficient for v1)

### What is the next rational engineering step?

**#54: Retention and lifetime policy.**

The retention field (ephemeral/session/project/durable) exists in the schema
but is not enforced. The review queue will grow without a prune path. This is
the lowest-risk, highest-value next slice.

Specific v1 scope for #54:
- `qz-braincase-review prune --retention ephemeral --older-than 24h` style command
- Candidate expiry: auto-retire unreviewed candidates after configurable age
- Active record aging: mark stale project-tier records for review
- Pruning uses `retire_state_record()`, not raw DELETE
- No automatic ingestion

---

## 6. Recommended next work

### Immediate

**#54 — BrainCase retention and lifetime policy** (open, not yet started).

Scope per #54: retention enforcement, candidate expiry, operator prune commands,
no automatic ingestion, no raw DELETE. This should treat BrainCaseDB as a
QuantZhai agent memory substrate — not HSM state machinery.

### Short-term (new issues only if clear use-case pressure)

- **BrainCase search quality polish:** if FTS recall misses too many relevant
  records in practice, a hybrid approach (FTS + re-ranking) could help. Do not
  add this until there is evidence of the problem.

- **BrainCase source-link graph evolution:** the `record_links` table and
  `supersedes`/`superseded_by` fields are the embryo of a light provenance graph.
  If source tracing becomes important, these could be evolved. Low priority.

- **BrainCase multi-domain review tooling:** if `coding` and `personal` and
  `hsm` memory_domains all have candidates, a cross-domain review view could
  help. Low priority; current CLI works per domain.

### Long-term / not yet

- **HSM storage use of BrainCaseDB:** If HSM project decides to use BrainCaseDB
  as its storage layer, it should use `memory_domain="hsm"` for isolation and
  build its own higher-level tooling on top. This is a future decision for the
  HSM repo, not a QuantZhai decision now.

- **Vector/semantic recall:** only if FTS-based recall is demonstrably
  insufficient for the use cases in practice. The additional complexity of
  embedding storage and search is only worth it with evidence.

- **Automatic memory extraction:** a future "memory extractor" pipeline could
  run over session captures and propose write_candidate calls automatically.
  This must remain candidate-only (extraction proposes, operator reviews).
  Not part of BrainCaseDB itself — a separate layer above it.

### Do not create issues for

- Quiescent state / affective triggers / anchor modelling (HSM scope)
- Human identity emulation (HSM scope)
- Upload-style continuity (HSM scope)
- Automatic ingestion of any kind
- model-facing braincase.write / update / search / inspect / promote_candidate

---

## 7. Scope boundary definition

BrainCaseDB is a **proof-of-concept memory substrate for agent runtimes**.

It is specifically designed for:
- Local coding-agent memory (project constraints, procedures, decisions)
- Opt-in, explicit writes with operator oversight
- Scoped retrieval by memory_domain and memory tier
- Bounded, auditable model-visible output

It is not designed for:
- Human state modelling or emulation
- Psychological state preservation
- Automatic memory extraction
- Large-scale semantic search
- Production multi-tenant memory systems

### The right question for evaluating new features

Before adding any feature to BrainCaseDB, ask:

> Does this help a coding agent maintain reliable, explicit, bounded project
> memory? Or does it push BrainCaseDB toward becoming something it isn't?

If the answer is "this is about human state modelling" or "this is about
automatic ingestion" or "this makes the system bigger without clear use-case
pressure" — defer it.

---

## Cross-references

- `docs/braincase-memory-tool-api.md` — tool plane design, slice history, open questions
- `docs/current-task-hierarchy.md` — active execution order
- `docs/current-stocktake.md` — point-in-time state
- `proxy/qz_braincase_db.py` — storage substrate
- `proxy/qz_braincase_tools.py` — model-facing tool plane
- `proxy/qz_braincase_review.py` — operator review helpers
- `scripts/qz-braincase-review` — operator CLI
- `scripts/qz-braincase-smoke` — 12-check smoke test
- Issue #54 — retention/lifetime policy (next active slice)
- `h4rm0n1c/HSM` — Human State Machine project (separate repo, different scope)
