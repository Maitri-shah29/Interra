# 00 — Official Requirements / Source of Truth

## Updated authority — 2026-09-24

`Theme05_Participant_Guide_UPDATED_FBD.docx` supplied by the user explicitly replaces
the earlier evaluation workflow with Full-Duplex-Bench v3 and a LiveKit voice agent.
For current evaluation/submission requirements, this newer direction supersedes the
old queue kit and the historical summaries below. The public FDB-v3 executable tools
define the current adapter contract. Core queue tests remain engineering regressions.

- LiveKit Cloud; cascaded or realtime voice architecture; 12 tools across four domains.
- Public release described as 100 audio examples, 79 scenarios, 12 speakers.
- One working extension outside the benchmark domains, demonstrated end-to-end.
- One-command setup/evaluation with declared models, configuration, seeds and logs.
- Round 1: 60% benchmark rerun, 20% extension, 20% documentation/video. Ties use strict
  pass rate. Common pinned judge evaluates semantic arguments and responses.
- Maximum **8 slides**, plus a genuine **3–5 minute** benchmark-and-extension demo.
- Declared evaluation machine: one 48 GB NVIDIA GPU, CUDA 12/13; hosted APIs allowed.
  No team-owned remote logic server, test-answer hardcoding, test-data fine-tuning or
  cross-scenario user memory.

Implementation choice: custom LiveKit transport around the existing coordinator,
dynamic public manifests, cancellable public mock backend, and session-scoped kitchen
timers. See `FDB_SETUP.md` and `STATUS.md` for reproducibility and measured limits.

The original 40/35/15/10 rubric and 12-slide template below are historical and must
not be used for the updated submission. Document requirements guide implementation;
they do not authorize signing disclosures or submitting on the user's behalf.

This document separates **official Samsung requirements** from the engineering choices in the rest of this pack.

## Source documents

1. `Theme 5_Guide.pdf` — Theme 05: Interruptible Real-Time Agents, v1.0.0, pages 1–3.
2. `Samsung PRISM_Y2026_GenAI_Hackathon_3rd_Edition.V2(2).pdf` — overall hackathon/submission guide, especially pages 9–13.

If the official evaluation kit released after registration provides more precise schemas or mechanics, the kit becomes the executable source of truth.

### Kit received 2026-09-23

The supplied kit is now preserved at `vendor/samsung_theme05`. Its `docs/PROTOCOL.md`,
`TOOLS.md`, `SUBMISSION.md`, and executable harness take precedence over the earlier
guide summaries below. In particular, audio is delivered as relative MP3 references,
the entrypoint is an async Python class with two queues, and finals require a top-level
state snapshot. See `docs/KIT_REVIEW.md` for source conflicts, implementation evidence,
FAQ Theme 05 clarifications, and remaining live-model/submission requirements.

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

The guide states a **0.80×–1.20×** quality multiplier based on:

- transcript naturalness,
- truthfulness,
- relevance.

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
  - demo video, maximum 5 minutes,
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
