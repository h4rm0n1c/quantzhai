# Model State Signal Contract (LimbiCore)

Date: 2026-05-12

Status: design contract, not implementation.

LimbiCore is the broader model state, signal, and memory interface being grown
around QuantZhai. QuantZhai is the current implementation vehicle: Codex CLI
speaks to QuantZhai's `/v1/responses` compatibility proxy, which then drives
local llama.cpp/TurboQuant Qwen models.

This document defines the small stretchy contract LimbiCore should grow from. It
is deliberately not a complete memory system. It is the envelope that should be
able to carry coding state, roleplay continuity, Human State Machine evidence,
user preferences, runtime/tool signals, utility LLM jobs, and future active
memory tools without turning into memory soup.

---

## One-line contract

```text
LimbiCore stores scoped StateRecords with provenance, then renders small purpose-specific packets or recall results to models.
```

Short form:

```text
Store richly. Recall narrowly. Render briefly. Never cross scope by accident.
```

---

## Problem

QuantZhai/LimbiCore needs to manage several different kinds of state:

```text
coding state
roleplay continuity
Human State Machine evidence and provenance
user preferences
tool/runtime signals
future active memory tools
bounded utility LLM jobs
```

Those are not the same thing, but they share a few structural needs:

```text
scope
provenance
visibility
lifetime
confidence
importance
rendering policy
```

The backend also matters. The current model path is local Qwen via
llama.cpp/TurboQuant, reached through QuantZhai's Responses-compatible proxy.
That means llama.cpp/Qwen should be treated as an inference backend and possible
bounded utility summariser/extractor, not as the durable memory authority.
QuantZhai owns memory policy, scope, recall, rendering, promotion, and isolation.

The lumpy problem is to design one small API that can stretch across many modes
without overfitting to the current coding-agent work.

---

## Research-backed principles

### 1. Memory is tiered, not a prompt dump

Claim: useful LLM memory systems separate small working context from larger
external/archival memory.

Evidence: MemGPT/Letta frames LLM context as limited working memory and uses an
external archival memory tier that can be searched and paged into context when
needed.

Sources:

```text
https://arxiv.org/abs/2310.08560
https://docs.letta.com/guides/agents/architectures/memgpt
```

Implication for LimbiCore:

```text
SQLite/state storage is not the prompt.
Rendered state packets are the prompt-facing working set.
Large context helps, but does not justify dumping raw memory.
```

### 2. Reflection and summarisation are first-class operations

Claim: agents benefit when raw events are periodically condensed into higher
level memories or summaries.

Evidence: Generative Agents used memory streams plus reflection and planning to
produce longer-running believable behaviour. Reflexion stores verbal feedback
from prior attempts as episodic memory to improve later decisions without weight
updates.

Sources:

```text
https://arxiv.org/abs/2304.03442
https://arxiv.org/abs/2303.11366
```

Implication for LimbiCore:

```text
summarise_session, summarise_scene, extract_decisions, and propose_memory_writes
are legitimate future utility jobs.
Their outputs should usually be proposals, not silently committed durable facts.
```

### 3. Retrieval beats giant undifferentiated context

Claim: external retrieval remains useful even with long-context models because
it selects and frames relevant information instead of relying on the model to
sort a massive prompt.

Evidence: RAG work established the value of combining parametric models with
external retrievable memory. Later long-context-vs-RAG comparisons show that
long context and retrieval have tradeoffs, but retrieval remains useful for
selective recall and dynamic knowledge.

Sources:

```text
https://arxiv.org/abs/2005.11401
https://arxiv.org/abs/2407.16833
```

Implication for LimbiCore:

```text
Recall should select records by scope and purpose.
Renderers should decide what the model sees.
The DB should not be poured directly into a prompt just because Qwen can fit a large context window.
```

### 4. Tool/action loops need state signals

Claim: agents behave better when tool actions, observations, and state updates
are explicit.

Evidence: ReAct-style agents interleave reasoning and action, using observations
from tools to drive later reasoning.

Source:

```text
https://arxiv.org/abs/2210.03629
```

Implication for LimbiCore:

```text
Tool/runtime facts should be StateRecords internally.
Some runtime facts should later become small advisory ToolResultSignal messages.
Repeated-read warnings are the current obvious example.
```

### 5. Social/roleplay state is state, not trivia

Claim: believable long-running roleplay needs scene, character, relationship,
and continuity state, not just factual memory.

Evidence: Generative Agents represents believable behaviour through retrieved
experiences, reflections, and plans rather than a flat fact dump.

Source:

```text
https://arxiv.org/abs/2304.03442
```

Implication for LimbiCore:

```text
roleplay state can fit the same StateRecord envelope, but needs specialised renderers.
Scene state, relationship state, character goals, style locks, and continuity hooks are legitimate record kinds.
They must not bleed into coding or HSM state by default.
```

### 6. Provenance and confidence are not optional for HSM work

Claim: evidence-heavy memory needs source tracking, uncertainty, and
contradiction handling.

Evidence: This follows from Human State Machine requirements and from the wider
agent-memory pattern that durable memory needs source and confidence metadata.
MemoryBank-style companion memory also uses time/importance dynamics rather than
flat permanent recall.

Sources:

```text
https://ojs.aaai.org/index.php/AAAI/article/view/29946
repo context: Human State Machine / HSM project requirements
```

Implication for LimbiCore:

```text
Every durable or high-impact record needs source/provenance.
HSM records need confidence and derived_from links.
Summaries must not erase where claims came from.
```

### 7. Backend capability limits shape the renderer

Claim: model-facing memory must be adapted to the actual backend/client path.

Evidence: QuantZhai currently exposes Responses and compaction while the model
runs behind llama.cpp/TurboQuant. llama.cpp can support OpenAI-like server
endpoints, streaming, tool-call-shaped outputs, schema/JSON modes, embeddings in
some configurations, and long context depending on model/build. Those
capabilities must be tested per local profile/path, not assumed as universal.

Sources:

```text
https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
repo docs: docs/codex-0130-live-signal-capture.md
repo docs: docs/benchmark-findings-effort-tuning.md
```

Implication for LimbiCore:

```text
QuantZhai owns Responses/tool/compaction policy.
llama.cpp/Qwen generates.
Schema JSON and embeddings are possible tools, not guaranteed truth sources.
Preferred model-facing output is short bullet packets and advisory tool results.
```

---

## QuantZhai/LimbiCore-specific observations

Current repo evidence says:

```text
Codex 0.130 provides session_id, thread_id, turn_id, window id, installation id, prompt_cache_key, and workspace candidates.
QuantZhai parser helpers exist for identity, body metadata, window id parsing, workspace candidates, workspace resolution, memory_domain defaulting, qz_session_id generation, and request context extraction.
QuantZhai-owned qz_* metadata injection into forwarded request bodies was removed and regression-covered.
memory_domain is explicit config only.
Missing memory_domain means isolated.
workspace_id is resolved internally from explicit config or Codex workspace evidence.
```

Relevant repo files:

```text
docs/current-architecture-authority.md
docs/codex-context-memory-contract.md
docs/codex-0130-live-signal-capture.md
proxy/qz_codex_metadata.py
tests/test_qz_request_mutation_regression.py
```

Qwen benchmark evidence says:

```text
explicit instructions and hard tool budgets worked better than vague labels
"as needed" wording backfired
sampling changes did not meaningfully control tool use
open-ended tasks can override effort framing
repeated-read and orientation waste need runtime signals, not just prompt wishes
```

Relevant repo file:

```text
docs/benchmark-findings-effort-tuning.md
```

Compaction and repeated-read work also point in the same direction:

```text
compaction is useful but can hide prior details unless summaries preserve the right state
repeated-read v1 is advisory and stateless/input-history-seeded
repeated-read v2 must use qz_session_id, qz_turn_id/codex_turn_id, qz_request_id, workspace_id, memory_domain, and same-scope file read/write/signal facts
```

Relevant repo files:

```text
docs/compaction-bridge-plan.md
docs/repeated-read-dedup-plan.md
docs/current-architecture-authority.md
```

---

## Non-goals

This contract explicitly does not implement:

```text
full memory system
model-visible durable memory now
active memory tools now
roleplay-specific system now
Human State Machine archive system now
cross-domain sharing
automatic promotion
giant raw memory prompt dumps
embedding/vector backend selection
silent durable writes from model output
```

Phase 1 SQLite remains boring.

---

## Core contract

LimbiCore has five conceptual APIs.

### Storage API

```text
record(StateRecord) -> record_id
```

Stores structured facts/events internally. This is not automatically
model-visible.

### Recall API

```text
recall(scope, query, filters, limit) -> RecallResult
```

Returns same-scope records or summaries. Recall may later be exposed as a model
tool, but the recall layer must enforce scope first.

### Render API

```text
render(scope, purpose, backend_capabilities, budget) -> RenderedPacket
```

Turns selected records into a model-facing packet or tool result. This is the
important boundary: renderers decide what the model sees.

### Proposal/promotion API

```text
propose(StateRecordDraft) -> proposed_record_id
promote(record_id, policy_context) -> record_id
```

Allows models or utility workers to suggest memory without silently committing
it as durable truth.

### Utility LLM job API

```text
run_utility_job(job_type, scoped_inputs, schema, budget) -> proposed_records | rendered_packet | validation_error
```

Runs bounded LLM workers for summarisation, extraction, contradiction detection,
importance scoring, or packet rendering. Outputs are proposals unless a domain
policy explicitly trusts that job.

---

## Universal StateRecord envelope

StateRecord is intentionally minimal and extensible.

```json
{
  "id": "sr_...",
  "kind": "fact",
  "scope": {
    "memory_domain": "coding",
    "workspace_id": "remote:git@github.com:h4rm0n1c/quantzhai",
    "world_id": null,
    "character_id": null,
    "artifact_set_id": null,
    "qz_session_id": "qz_sid_...",
    "qz_turn_id": "qz_turn_...",
    "qz_request_id": "qz_req_..."
  },
  "subject": "docs/model-state-signal-contract.md",
  "body": {},
  "source": {
    "type": "tool_event",
    "ref": "qz_req_...",
    "description": "Parsed from Codex request context"
  },
  "visibility": "internal",
  "lifetime": "session",
  "confidence": 1.0,
  "importance": 0.5,
  "operation": "record",
  "derived_from": [],
  "created_at": "2026-05-12T00:00:00Z",
  "updated_at": "2026-05-12T00:00:00Z",
  "expires_at": null
}
```

Open-ended examples:

```text
kind:
  fact
  decision
  preference
  procedure
  workspace_state
  tool_event
  tool_signal
  summary
  scene_state
  character_state
  relationship_state
  continuity_hook
  evidence_claim
  contradiction
  open_question

scope.memory_domain:
  isolated
  coding
  roleplay
  hsm
  personal
  utility

visibility:
  internal
  recall_tool
  state_packet
  prompt_prefix
  pending_review
  never_model_visible

lifetime:
  ephemeral
  request
  turn
  session
  workspace
  world
  project
  durable

operation:
  observe
  record
  recall
  summarise
  render
  propose
  promote
  expire
  compact
  reflect
```

Rules:

```text
body is opaque JSON to the storage layer.
kind and scope drive indexing and rendering policy.
visibility controls whether records can ever become model-visible.
source and derived_from preserve provenance.
confidence and importance support ranking, pruning, contradiction handling, and later utility jobs.
```

---

## Scope model

Scope is explicit. It is not guessed from vibes.

Fields:

```text
memory_domain
workspace_id
world_id
character_id
artifact_set_id
qz_session_id / session_id
qz_turn_id / turn_id
qz_request_id / request_id
```

Rules:

```text
explicit scope beats inference
missing memory_domain means isolated
workspace_id is resolved internally by QuantZhai
roleplay, HSM, coding, and personal memory do not bleed by default
model/profile/client/tool names do not grant memory authority
cross-domain sharing requires explicit policy and should be rare
```

Examples:

```text
coding record:
  memory_domain=coding
  workspace_id=remote:git@github.com:h4rm0n1c/quantzhai

roleplay record:
  memory_domain=roleplay
  world_id=<world>
  character_id=<character>

HSM record:
  memory_domain=hsm
  artifact_set_id=<evidence bundle>
```

---

## Memory operations

| Operation | Purpose | Input | Output | Caller | Model-visible |
| --- | --- | --- | --- | --- | --- |
| record | Store scoped operational facts/events | StateRecord | record_id | system/proxy | no |
| recall | Retrieve same-scope records | scope + query + filters | RecallResult | system or future memory tool | only if rendered/tool-returned |
| summarise | Condense records into summary records | scoped records + purpose | proposed/recorded summaries | system/utility LLM | no |
| render | Produce model-facing state | scope + purpose + budget | RenderedPacket | system | yes |
| propose | Suggest a durable record | StateRecordDraft | proposed_record_id | model/utility/system | no |
| promote | Commit a proposed record | proposed_record_id + policy | record_id | user/system/policy | no |
| expire | Retire stale state | record_id or policy | status | system | no |
| compact | Merge/condense old records | scoped records + budget | summary records | system/utility LLM | no |

Important split:

```text
Storage records are not model-facing memory.
Recall results are not automatically model-facing memory.
Renderers decide what the model sees.
```

---

## Backend capability adapter

Renderers must adapt to the active backend/client path.

```json
{
  "supports_responses": true,
  "supports_streaming": true,
  "supports_tool_calls": true,
  "supports_schema_json": "possible_test_per_profile",
  "supports_embeddings": "possible_test_separately",
  "supports_long_context": true,
  "supports_compaction": true,
  "max_context": 262144,
  "preferred_signal_format": ["StatePacketText", "ToolResultSignal", "UtilityJsonPrompt"]
}
```

Current QuantZhai/llama.cpp/Qwen profile:

```text
Responses is exposed by QuantZhai, not assumed native from llama.cpp.
Compaction is exposed by QuantZhai.
Tool calls are mediated by Codex/QuantZhai.
Schema JSON may be usable for utility jobs but must be validated per model/profile/path.
Embeddings may be possible through llama.cpp or a separate local embedding model, but are not assumed for the current Qwen profile.
Long context is useful but not a reason for memory dumping.
Preferred signal format is short bullets, advisory tool results, and bounded utility JSON for worker jobs only.
```

---

## Model-facing render formats

### StatePacketText

Short bullet packet injected as context.

Coding example:

```text
Relevant State:
- Domain: coding; Workspace: h4rm0n1c/quantzhai.
- Parser helpers are internal and tested.
- Do not inject qz_* context into forwarded /v1/responses bodies.
- Phase 1 SQLite stores operational facts only.
```

Roleplay example:

```text
Scene State:
- Location: workshop after-hours.
- Blynx is relaxed but still guarded.
- Trust improved after the last apology.
- Style: grounded dialogue; avoid purple prose.
```

### ToolResultSignal

Advisory tool-result style signal.

```text
Note: you already read README.md earlier in this turn. Use existing context unless you believe it changed.
```

### UtilityJsonPrompt

Bounded schema-oriented worker input/output.

```json
{
  "summary": "Session discussed LimbiCore StateRecord design.",
  "proposed_records": [],
  "open_questions": ["Which embedding backend should be used?"]
}
```

### RecallResult

Scope-safe recall result, possibly model-callable later.

HSM example:

```text
Retrieved Evidence:
- Claim: ALB2550 was an early identity handle.
- Source: archived artifact set.
- Confidence: medium.
- Open issue: timeline needs corroboration.
```

### InternalOnly

No model-facing output. Used for raw captures, internal IDs, low-confidence
facts, or protected records.

---

## Active memory tools (deferred)

Future model-callable tools may include:

```text
memory.search
memory.propose_write
memory.get_state
memory.summarise_session
memory.explain_recall
```

Rules:

```text
search/read tools enforce memory_domain and workspace/world/artifact scope
durable writes are proposed, not silently committed
promotion requires user/system/policy approval depending on domain
memory tools must explain or expose provenance when returning high-impact facts
roleplay, coding, HSM, and personal scopes stay isolated by default
```

Do not implement these in SQLite Phase 1.

---

## Utility LLM jobs

Bounded worker jobs may include:

```text
summarise_session
summarise_scene
extract_preferences
extract_decisions
detect_contradictions
score_importance
propose_memory_writes
render_state_packet
```

Rules:

```text
outputs are proposals unless explicitly trusted
outputs must include source/provenance
schema JSON is preferred where backend supports it, but must be validated
workers never cross scope without explicit input
workers should receive bounded inputs, not the whole DB
utility failures must degrade safely
```

Use utility LLM calls when judgement or compression is useful. Prefer
deterministic code for mechanical extraction, indexing, timestamp handling,
path normalisation, and scope checks.

---

## Roleplay and Human State Machine notes

This is not a full roleplay or HSM design.

Roleplay needs:

```text
scene_state
character_state
relationship_state
continuity_hooks
style_preferences
boundary_preferences
```

Human State Machine work needs:

```text
evidence_claim
source/provenance
uncertainty/confidence
contradiction handling
artifact_set_id
summary records that preserve derived_from links
```

Both fit the StateRecord envelope. Both need stricter scope than generic coding
state. Neither should be allowed to bleed into coding state by default.

---

## Phase plan

### Phase 1: BrainCase storage substrate and memory tool API

**Update 2026-05-15:** The architecture has been clarified. Phase 1 is now
tool-mediated, not DB-first. See `docs/braincase-memory-tool-api.md` for the
current design. The "operational substrate" framing below is historical; read it
as the storage layer behind the memory tool API, not as automatic request/session
logging.

Phase 1 scope (revised):

```text
BrainCaseDB skeleton (proxy/qz_braincase_db.py) — landed.
StateRecord / memory tool API design — Slice A (JSON schema fixtures).
BrainCaseDB schema for state_records — Slice B (after Slice A settles shape).
braincase.search + inspect — Slice C.
braincase.write/update — Slice D.
braincase.render — Slice E.
```

Sessions, turns, requests, workspace candidates, and identity conflicts may
appear as source refs or provenance attached to actual stored StateRecords —
not as automatic logs for every observed request.

No model-visible durable memory. No automatic promotion. No cross-domain sharing.
No automatic ingestion.

File reads/writes and tool signals may become Phase-1-compatible operational
facts only if captured as scoped internal records. They must not become
model-visible memory in Phase 1.

### Phase 2: Rendered state packets

Render small state packets from stored facts. Still no active memory tools.

### Phase 3: Utility LLM proposal jobs

Add bounded worker calls that create proposed records or rendered packets.
Validate outputs.

### Phase 4: Active memory search/propose tools

Expose scoped `memory.search` and `memory.propose_write` style tools.
Durable writes remain proposed/promoted.

### Phase 5: Roleplay/HSM specialised renderers

Add domain-specific renderers for scene packets, relationship state, and HSM
evidence recall.

---

## Evaluation

Minimum tests/evals:

```text
no cross-domain bleed
prompt packet budget limits
repeated-read reduction
roleplay continuity retention
HSM provenance retention
utility summariser hallucination checks
structured output validation failure handling
recall precision / false-positive checks
backend capability fallback checks
```

Concrete examples:

```text
coding record must not render into roleplay packet
roleplay character state must not render into coding packet
HSM evidence recall must include provenance/confidence
UtilityJsonPrompt invalid JSON must not commit records
StatePacketText must stay within configured token/bullet budget
Repeated-read signal should reduce redundant file reads without blocking legitimate re-read after write
```

---

## Open questions

```text
Which embedding/search backend should be used?
Are local llama.cpp embeddings good enough for LimbiCore recall?
How reliable is schema JSON per Qwen profile and request path?
What state packet budget works best: 200 tokens, 500 tokens, or adaptive?
Which domains allow automatic promotion, if any?
How should shared user preferences cross domain boundaries without causing bleed?
When should utility LLM calls be used instead of deterministic summarisation?
How should roleplay worlds and Human State Machine artifact sets be named and isolated?
```

---

## Immediate effect on Phase 1 SQLite

This document does not change the immediate Phase 1 plan. The BrainCaseDB
skeleton exists. The next step is Slice A: StateRecord JSON schema fixtures.

Phase 1 should proceed as a boring, optional/non-fatal storage substrate with
explicit write paths only — not automatic ingestion of observed runtime data.
See `docs/braincase-memory-tool-api.md` for the updated design.

The contract adds one design constraint for future-proofing:

```text
If a table or event might later feed LimbiCore, store enough scope/provenance/visibility metadata to keep renderers safe.
```

That is all. No clever memory yet.
