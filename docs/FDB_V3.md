# Full Duplex Bench v3 migration

The updated Theme 05 participant guide received on 2026-09-24 replaces the
earlier queue protocol with Full-Duplex-Bench v3 and a LiveKit voice-agent
runtime. This document records the requirements that now govern the submission.

## Official benchmark

- Repository: `DanielLin94144/Full-Duplex-Bench`, `v3/` directory.
- Pinned repository commit for local reproduction:
  `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.
- Released set: 100 recordings, 79 unique scenarios, 12 speakers.
- Domains: travel and identity, finance and billing, housing and location,
  and ecommerce support.
- Difficulty: one, two, or three chained tool calls.
- Speech conditions: fillers, pauses, hesitation, false starts, and
  self-correction.

The benchmark code and released audio remain external and are not copied into
the submission repository. `scripts/fdb_v3.py bootstrap` checks out the official
source at the pinned revision, and `--download-data` obtains the published data.

## Submission contract

The repository must provide:

1. A LiveKit voice agent using any supported realtime or cascaded architecture.
2. Exact setup and run steps plus a one-command end-to-end reproduction path.
3. FDB-v3 results and logs including seeds and model configuration.
4. One working extension beyond the benchmark domains.
5. Demo video: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a).
6. The 12-slide organizer deck, per explicit user instruction (2026-10-05).

Round 1 weighting is 60 percent official normalized benchmark score, 20 percent
extension, and 20 percent documentation, architecture, and video. Ties use the
strict pass-rate component. The organizers re-run the submission on one NVIDIA
48 GB GPU with CUDA 12.x or 13.x, or against declared hosted APIs.

## Interra provider profile

Fresh-checkout install, download, inference and evaluation:
`python scripts/reproduce.py` after filling `.env` from `.env.fdb.example`.
Use a CUDA machine for the official recorder/scorer. Agents 1.8.3 and RTC 1.1.18
match the archived hosted run. Hosted sampling is nondeterministic; that run did
not record a seed. The reproduction wrapper sets Python hash seed 0.
`python scripts/check_submission.py` verifies the committed evidence and deck
without calling model providers. See `results/run-manifest.json` for hashes and
the distinction between measured source and later unscored fixes.

The current FDB-v3 profile is a cascaded LiveKit session:

- Silero VAD for speech boundaries.
- LiveKit Inference Deepgram Nova-3 for STT.
- LiveKit Inference GPT-4.1 mini for tool selection and chained reasoning.
- LiveKit Inference Cartesia Sonic-3 for TTS.
- Cross-provider STT/TTS failover and a three-second gap after session cleanup
  to release provider connections before the next recording.
- Optional local fallback: Ollama Qwen 3 8B.
- The official `MockAPIRegistry` loaded from the pinned benchmark checkout.

Required hosted secret names are `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and
`LIVEKIT_API_SECRET`. `OPENAI_API_KEY` is needed only to
reproduce the optional local LLM-judge reports; the organizers use their pinned
judge for official scoring.

STT and TTS fallback model IDs and the post-session cooldown can be changed with
`INTERRA_FDB_STT_FALLBACK_MODELS`, `INTERRA_FDB_TTS_FALLBACK_MODELS`, and
`INTERRA_FDB_SESSION_COOLDOWN_SECONDS`. The current defaults use AssemblyAI
Universal-3.5 after Nova-3 fails, and Deepgram Aura-2 after Sonic-3 fails.

Turn and argument handling can be tuned without code changes:
`INTERRA_FDB_LLM_TEMPERATURE` (default 0), `INTERRA_FDB_ENDPOINTING_MIN_DELAY`
and `INTERRA_FDB_ENDPOINTING_MAX_DELAY` (0.7 and 1.2 s; LiveKit's hosted
semantic turn detector picks between them), `INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS`
(1.0 s; 0 disables holding turns that end mid-sentence) and
`INTERRA_FDB_RECORDER_RETRIES` (1; re-runs recordings whose official recorder
crashed before writing a result).

Offline checks need no audio or GPU: `python -m agent.fdb_offline rescore --run
docs/results/kaggle-20261001` re-scores archived tool calls with the current
normalizers, and `python -m agent.fdb_offline llm --run ... --provider livekit`
replays archived transcripts through the current prompt and tools. Scenario
labels are read from the pinned checkout for scoring only.

## Rules preserved in implementation

- No benchmark examples or expected answers are placed in prompts or code.
- No state is cached across scenarios.
- Hosted model APIs are declared; no team-owned server is called at evaluation.
- Tool results, not model memory, ground the final response.
- Versions, model IDs, benchmark commit, configuration, and reports are recorded.
- Credentials remain outside Git.

## Extension

Camera-assisted device troubleshooting remains the selected extension because
the repository already contains asynchronous image observations, source-bound
embeddings, correction-aware state, and stale-result rejection. It is not complete
for the new guide until it is reachable through the LiveKit session and appears
in a genuine end-to-end video.

## Historical work

The queue adapter, nine-scenario Samsung kit, `submission.yaml`, and prior 49.5
score belong to the superseded evaluation contract. They are retained as
engineering evidence and regression coverage, but they must not be reported as
FDB-v3 results.
