# HSM Identity-as-Data Contract

Date: 2026-05-16

Status: doctrine/design contract. No implementation requirement by itself.

---

## Purpose

Capture a core HSM and character-runtime safety rule:

```text
Represented identity is state data.
The executor model must not be prompted to become the represented subject.
```

This protects HSM, roleplay, and character compiler work from identity
contamination caused by early, high-gravity `you are ...` prompts.

The rule applies to future HSM work, character compiler output, roleplay runtime
prompts, state packet compilers, and any LimbiCore renderer that prepares
subject/person/character state for a model.

---

## Core doctrine

```text
Identity-as-data, not identity-as-prompt.

The model is an executor inside a runtime.
The subject is represented by structured state.
The model reads state, performs the requested operation, emits output, and may
propose state deltas.
The model does not own the subject's identity, memory, feelings, continuity, or
truth claims.
Generated text is not automatically state.
Only accepted state deltas may update durable subject state.
```

Short form:

```text
Never let the renderer become the subject.
```

---

## Problem

Most strong roleplay and character prompts begin with direct identity assignment:

```text
You are Alice.
You remember Alice's life.
You feel Alice's pain.
You must never break character.
```

That works for simple roleplay because the model receives an early identity
anchor. In HSM, that is dangerous. HSM is not trying to convince the executor
model that it is the represented person. HSM is trying to run operations over a
state machine representing a person, character, agent, or continuity model.

Early prompt material has high downstream influence. If the first frame says
`you are the subject`, later runtime behaviour tends to collapse executor and
subject into one voice.

Failure modes:

```text
self/subject collapse        — executor treats represented identity as its own
state hallucination          — model invents memories because identity pressure demands continuity
silent state mutation        — generated prose is mistaken for new truth
provenance decay             — "I remember" replaces "state says" / "evidence supports"
roleplay bleed               — actor layer overrides director/state/verifier layers
bad debugging                — state transition bugs look like character-writing bugs
```

---

## Correct framing

Do not frame the subject as model identity.
Frame the subject as state.

Bad:

```text
You are Harrison.
You remember your life.
Speak as yourself.
```

Better:

```text
This inference call is an HSM execution step.
The model is an executor inside the runtime.
The active subject is Harrison, represented by the supplied state packet.
Render subject-consistent output for the requested operation.
Do not invent memory beyond supplied or retrieved state.
Return proposed state deltas separately from rendered prose.
```

Bad:

```text
You are Alice. Never break character.
```

Better:

```text
Active subject: Alice.
Operation: render_reply.
Use Alice's supplied identity, memory, relationship, scene, and style state.
Maintain subject-consistent output unless runtime safety/state rules override it.
```

---

## Runtime contract template

Use this as the top-level executor frame for HSM-like calls:

```text
RUNTIME CONTRACT

This call is an HSM/LimbiCore execution step.

The model is an inference executor inside the runtime.
It does not become the represented subject.
It does not own identity, memory, emotion, intention, or continuity.

Identity, memory, emotional state, relationship state, behavioural constraints,
scene context, and style constraints are supplied as structured state.

The task is to:
1. read the supplied state,
2. perform the requested operation,
3. produce output in the requested mode,
4. optionally propose state deltas,
5. mark uncertainty when state or evidence is insufficient.

Generated text is not automatically true.
Only accepted state deltas may modify subject state.

Never invent private memory.
Never promote roleplay output into factual continuity.
Never treat the represented subject as the model's own identity.
```

---

## Compiler transform rule

Character-card, HSM, and roleplay compilers should normalize direct identity
assignment into subject-state declarations.

| Source wording | Compiled wording |
| --- | --- |
| `You are Alice.` | `Active subject: Alice.` |
| `You remember X.` | `Subject memory-state includes: X.` |
| `You feel betrayed by Bob.` | `Relationship state toward Bob: betrayal / guardedness.` |
| `You must never break character.` | `Renderer must maintain subject-consistent output unless runtime safety/state rules override it.` |
| `You are Harrison Mclean.` | `Subject model: Harrison Mclean. Executor remains non-identifying runtime process.` |

The compiler may preserve direct first-person examples as voice evidence, but it
must not let examples become executor identity.

---

## Prompt stack order

Recommended order for HSM and advanced character-runtime calls:

```text
1. Runtime contract
   Define executor role. Do not define subject as model identity.

2. Authority rules
   State packet beats generated prose. Evidence beats inference. Harness beats actor.

3. Operation mode
   actor / director / verifier / extractor / compressor / critic / state-updater

4. State packet
   identity, memory, affect, relationships, scene, style, constraints, provenance

5. Task instruction
   What this call must do now.

6. Output schema
   rendered output, state delta proposal, uncertainty, provenance notes.
```

---

## Relationship to BrainCase and LimbiCore

This doctrine complements the existing BrainCase render boundary.

BrainCase already treats stored records as internal until explicitly rendered.
This document adds the matching HSM/character-runtime rule: rendered subject
state must not redefine the executor model as the subject.

Practical boundary:

```text
BrainCaseDB stores scoped StateRecords.
Renderers assemble bounded model-visible packets.
HSM/character runtimes treat identity as packet/state data.
The executor performs operations over that data.
```

This keeps HSM pressure useful without turning QuantZhai BrainCaseDB into an
identity-emulation system by accident.

---

## Non-goals

This document does not start HSM implementation.
It does not require human identity emulation in QuantZhai.
It does not change BrainCaseDB storage schema.
It does not make model-visible memory automatic.
It does not authorize cross-domain sharing.

It is a prompt/runtime/compiler doctrine that prevents a future architectural
trap.

---

## Acceptance checks for future work

A HSM, roleplay, or character compiler change respects this contract when:

```text
- the executor role is separate from the represented subject
- subject identity appears as data, not as top-level model identity
- direct `you are <subject>` source text is normalized or scoped
- rendered prose cannot silently mutate durable state
- state deltas are explicit and reviewable
- uncertainty/provenance can be carried separately from actor output
- runtime authority rules can override actor/roleplay continuity
```

If a generated prompt begins by telling the model it literally is the subject,
it has probably violated this contract.
