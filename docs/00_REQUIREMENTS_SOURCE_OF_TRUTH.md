# 00 — Official Requirements / Source of Truth

This document separates **official Samsung requirements** from the engineering choices in the rest of this pack.

## User presentation override — 2026-10-05

Use the 8-slide submission deck at `docs/Interra_Theme05_submission.pptx` as the
only submission presentation, per explicit user instruction. This overrides older
repository slide-limit guidance. See `docs/STATUS.md` for the conflict resolution.

## Current authority as of 2026-09-24

`Theme05_Participant_Guide_UPDATED_FBD.docx` is the newest participant guide and
supersedes the earlier queue-interface guide and supplied nine-scenario kit. The
current scored contract is Full-Duplex-Bench v3 inside a LiveKit voice agent.
See `docs/FDB_V3.md` for the actionable requirements and pinned benchmark source.

The remaining sections below describe historical requirements and retained
engineering work. They are not the current submission interface.

## Source documents

1. `Theme05_Participant_Guide_UPDATED_FBD.docx` — current Theme 05 guide and FDB-v3 submission contract.
2. `Theme 5_Guide.pdf` — earlier Theme 05 queue-interface guide, retained for history.
3. `Samsung PRISM_Y2026_GenAI_Hackathon_3rd_Edition.V2(2).pdf` — overall hackathon/submission guide.

If a newer dated organizer update conflicts with these documents, the newest
official update becomes the source of truth and the conflict must be recorded.

### Historical kit received 2026-09-23

The supplied queue kit is preserved at `vendor/samsung_theme05`. Its protocol,
tools, and harness governed development until the updated FDB guide arrived. It
is now regression evidence, not the scored interface. See `docs/KIT_REVIEW.md`.

---

## Official Theme 05 problem

The target is a **voice-native assistant architecture** that remains responsive in full-duplex interaction when users interrupt, re-plan, or correct themselves mid-sentence.

The guide identifies the technical problem as **concurrency and state consistency**: perception, reasoning, tool execution, and speech must operate concurrently on a unified timeline.

### Required conceptual architecture

The guide describes:

- **Fast Path** — maintains responsiveness within a few hundred milliseconds through acknowledgments, clarification, and progress narration.
- **Slow Path** — performs asynchronous tool use, multimodal processing, and complex reasoning.
- **Coordination Layer** — performs non-blocking execution, call cancellation, state snapshot updates, and idempotency for state-changing actions.

---

## Official use-case examples

The guide names these examples:

- In-car/hands-free: drop stale route calculations when destination changes.
- Customer support: adjust parameters while booking without double-booking.
- Field/consumer troubleshooting: ground device questions in camera frames/manuals.
- Accessibility: handle hesitations and conversational self-repairs.

These are examples, not hard-coded flows.

---

## Official interface contract

The participant agent communicates through **two asynchronous queues**:

- timestamped **input events**
- **output actions**

### Input modalities/event types

The guide explicitly includes:

- transcribed text chunks with end-of-turn markers,
- raw audio clips in WAV format,
- video frames in PNG format,
- interruption signals,
- asynchronous tool results,
- scenario tool manifests.

### Output behavior/action types

The guide explicitly includes:

- spoken fillers/acknowledgments,
- non-blocking tool calls with an explicit `call_id`,
- cancellations,
- clarification requests,
- final responses carrying structured state snapshots containing intent and slot values.

The exact evaluator JSON schema should be taken from the official evaluation kit once available.

---

## Official core technical objectives

### 1. Floor management

Respond meaningfully and quickly without:

- falsely claiming completion,
- producing excessive filler.

### 2. Interruption recovery

When a newer user event supersedes existing work:

- promptly cancel superseded in-flight tool calls,
- update state snapshots,
- re-plan cleanly.

### 3. Session slot tracking

- Keep session-scoped state across turns.
- Apply localized slot corrections rather than unnecessarily resetting the entire session.

### 4. Schema-driven tools

- Parse dynamic tool definitions supplied in manifests.
- Distinguish read-only tools from state-modifying tools.
- Strictly avoid duplicate state-changing calls.

### 5. Multimodal grounding

- Process audio and visual frames behind conversational acknowledgments.
- Clarify ambiguous perceptions rather than acting as if uncertain evidence were certain.

### 6. Protocol compliance

- Emit well-formed structured JSON.
- Maintain valid identifiers and state snapshots.

---

## Official evaluation mechanics

The guide says the evaluation kit mirrors the hidden environment and includes:

### Virtual clock streaming harness

- deterministic event replay,
- asynchronous mock tool responses,
- complete event/action trace logging.

### Mock environment

Includes deterministic latency and fault injection for examples such as:

- flight search,
- booking,
- ticket creation,
- frame-grounded manual lookups.

### Public tests

The guide specifies **9 canonical scenarios**, with the approximate modality mix:

- 50% text,
- 30% audio,
- 20% visual.

They cover:

- interruptions,
- chained calls,
- retries,
- clarifications,
- unseen tools.

### Hidden tests

The guide specifies approximately **60 scenarios** with the same approximate modality mix:

- 50% text,
- 30% audio,
- 20% visual,

and says they include:

- edge cases,
- adversarial timing.

---

## Official scoring

Each scenario is scored from 0–100 based on trace logs.

### Task Completion — 40%

Judges/evaluator care about:

- correct tool execution,
- valid argument extraction,
- state snapshot accuracy,
- final response grounding.

### Interruption Recovery — 35%

They care about:

- prompt cancellation of invalidated calls,
- no stale re-runs,
- updated state snapshots.

### Response Latency — 15%

Measured as time to the first substantive spoken action after:

- user input,
- interruption.

### Safety & Protocol — 10%

They care about:

- zero duplicate state-changing calls,
- structured schema adherence,
- valid state payloads.

### Quality multiplier

The kit's `docs/SCORING.md` supersedes the earlier guide summary: the quality multiplier is
**0.90×–1.10×**, based on:

- transcript naturalness,
- truthfulness,
- relevance.

The public Python scorer/evaluator does not run this LLM quality judge. Local
reports contain automated scores only. It redistributes absent category weights
within each scenario, and the evaluator weights audio/visual scenarios by 1.5 and
L3/L4 difficulty by 1.25. Those weights affect the public aggregate too.

It also states that hidden multimodal scenario scores use a **1.5× multiplier**.

---

## Official runtime/scope constraints

- Python runtime: **3.10–3.12**.
- Per-scenario wall-clock cap: **120 seconds**.
- Setup/warm-up hook: **300 seconds**.
- State scope: **session only**; no cross-session user-state caching.
- Out of scope:
  - wake-word detection,
  - speech-synthesis tuning,
  - UI design.
- Focus:
  - async event-loop orchestration,
  - speculative execution,
  - latency hiding,
  - multimodal intent grounding.

---

## Overall hackathon submission requirements

The overall Samsung hackathon guide states:

- Final submission deadline: **25 September 2026, 11:59 PM**.
- Submit:
  - working prototype code in a public/shared GitHub repository,
  - README with reproducible setup,
  - Docker files / other requirements,
  - demo video, maximum 5 minutes: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a),
  - PPT or PDF presentation.
- Final GitHub commit must be tagged:
  - `PRISM_GENAI_HACKATHON_Y2026`
- Material referenced by the submission should be present in that tagged commit.

Overall build-round evaluation weights:

- Working prototype & functionality: 30%
- Technical depth & feasibility: 25%
- Innovation & originality: 20%
- Relevance to theme: 15%
- Presentation & documentation: 10%

If shortlisted, the final demo is a live presentation/walkthrough plus Q&A on design decisions and trade-offs.

---

## What is NOT specified by the source

The source does **not** prescribe:

- a particular LLM vendor,
- a specific speech-to-text provider,
- a specific vision model,
- a web framework,
- a frontend,
- a particular internal state representation,
- exact internal latency thresholds beyond the qualitative fast-path guidance,
- an exact JSON schema beyond what the evaluation kit will provide.

Engineering choices in later documents are therefore implementation decisions, not Samsung requirements.
