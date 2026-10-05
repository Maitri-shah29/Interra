# Kaggle hosted run review — 2026-10-04

Kernel: `adityavardhankochar/interra-fdb-v3-benchmark`, started from the Kaggle
editor. Models: LiveKit Inference Deepgram Nova-3, GPT-4.1 mini (temperature 0),
Cartesia Sonic-3. Benchmark pin: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.

## Latest measured results

| Metric | Result |
| --- | ---: |
| Strict pass | **70/100** (43/100 on 2026-10-01; main baseline 31/100) |
| Wrong tools | 13 |
| Wrong arguments | 17 |
| Turn-taken recordings | 100/100 |
| Silent recordings | 0 |
| Completed tool calls | 152/152 |
| Cleanup / completed cooldown events | 100 / 100 |
| Session errors in the final trace | 0 |
| Tool selection, all recordings | 93.4% |
| Argument accuracy, all recordings | 76.3% |
| Early interruptions | 3/100 |
| Average response latency, excluding interruptions | 4.452 seconds |

Evidence, hashes and verification commands are in
[`results/kaggle-20261004/`](results/kaggle-20261004/) (`summary.md`,
`run-manifest.json`). All 100 per-recording results are archived; the official
evaluator and `agent.fdb_offline rescore` both reproduce 70/100. The LiveKit
project ran out of Inference credits partway through, so the 29 recordings from
the first quota error onward were re-recorded with the same code. The optional
organizer LLM judge was disabled, so no normalized overall hackathon score is
claimed.

Weakest areas: housing and travel argument accuracy (69.9% and 68.3%).

# Previous Kaggle hosted run review — 2026-10-01

Latest kernel: `adityavardhankochar/interra-fdb-v3-benchmark`, version 3.
Models: LiveKit Inference Deepgram Nova-3, GPT-4.1 mini, Cartesia Sonic-3.
Benchmark pin: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.

## Measured results

| Metric | Result |
| --- | ---: |
| Strict pass | 43/100 (previous main baseline 31/100) |
| Wrong tools | 28 |
| Wrong arguments | 29 |
| Turn-taken recordings | 90/100 |
| Silent recordings | 10 |
| Completed tool calls | 147/147 |
| Cleanup / completed cooldown events | 101 / 101 |
| Tool selection, turn-taken | 85.7% |
| Argument accuracy, turn-taken | 56.3% |
| Tool selection, all recordings | 77.2% |
| Argument accuracy, all recordings | 50.7% |
| Early interruptions | 11/90 |
| Average response latency, excluding interruptions | 4.175 seconds |

Evidence, source hashes, and complete caveats are in
[`results/kaggle-20261001/`](results/kaggle-20261001/). The aggregate official
reports cover all 100 recordings, but only 15 per-recording JSONs were retrievable.
This was one run; LiveKit logged LLM credit-quota errors, no STT 429 appeared in
the agent trace, and the optional organizer LLM judge was disabled. Therefore,
no normalized overall hackathon score is claimed. Turn-taken selection and
argument accuracy are lower than the previous run despite the better strict
pass, turn-take, and latency results. Two repeats after quota restoration remain
part of the completion criteria.

## Previous run and implementation history

The following 2026-09-30 review is retained as historical evidence. It documents
the 31/100 main baseline and the earlier canceled-run investigation; those notes
are superseded by the completed kernel run above where status differs.

# Previous Kaggle hosted run review — 2026-09-30

Notebook: `maitrishah29/interra-fdb-v3-http-tts-smoke`.
Latest run: COMPLETE. Models: LiveKit Inference Deepgram Nova-3,
GPT-4.1 mini, Cartesia Sonic-3. SDK: LiveKit Agents 1.8.3.
Official benchmark pin: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`.

## Measured results

| Metric | Result |
| --- | --- |
| Strict pass | 31/100 |
| Wrong tools | 50 |
| Wrong arguments | 19 |
| Turn-taken recordings | 58/100 |
| Silent recordings | 42 |
| Completed inference attempts | 93 |
| Inference failures | 7 |
| STT rate-limited rooms | 35; all silent |
| Tool selection, turn-taken samples | 88.5% |
| Argument accuracy, turn-taken samples | 58.6% |
| Tool selection, all samples | 51.3% |
| Argument accuracy, all samples | 34.0% |
| Early interruptions, turn-taken samples | 6/58 |
| Average response latency, excluding interruptions | 4.545 seconds |
| Full batch duration | 93.61 minutes |

Sources downloaded to `artifacts/kaggle-inference-latest/`:

- `Interra/artifacts/fdb_v3/interra_elevenlabs_pass_rate_report.json`
- `Interra/artifacts/fdb_v3/interra_elevenlabs_evaluation_report.json`
- `Interra/artifacts/fdb_v3/livekit-agent.jsonl`
- The 100 `result_interra_elevenlabs.json` files under the released data folder.

Notebook logs and embedded source are in `artifacts/kaggle-latest-source/`.
Repeated Kaggle stdout/stderr records were deduplicated before counting errors.
The optional semantic judge was disabled; no normalized overall hackathon
score or post-fix improvement is claimed.

## Causes and implemented fixes

1. Sessions stayed open after participant disconnect without an explicit
   session close or job shutdown. STT 429 errors affected 35 recordings.
   Added a participant-bound lifecycle with a bounded drain, explicit close,
   job shutdown, event-handler removal, and awaited cancellation of owned tasks.
   The leak is proven in the old source; the exact share of quota failures it
   caused remains an inference until the corrected hosted run completes.
2. OpenAI plugin imports blocked the job event loop for hundreds of milliseconds.
   Warm Silero and benchmark code before accepting a job; import the optional
   plugin only for the fallback model profile.
3. Apartment search omitted requested pet constraints. Forward the optional
   pet filter through the official backend's supported keyword arguments.
4. Search-filter values had a string-only schema. Expose string/number/boolean
   values and restore explicit numeric and boolean types.
5. Spoken identifier spelling remained spaced words in tool arguments. Join
   explicitly spelled single letters and digits; preserve literal identifiers
   with punctuation and avoid guessing misheard letters.

Some failures still need model-level improvement: misheard identifiers, lost
address words, incorrect currency direction, extra tool calls, and incomplete
chains. General transcription instructions now reinforce ordinary written
numbers/identifiers, faithful product wording, and typed values. No expected
answers are inserted into the prompt or runtime.

## Verification and rerun

### Submission audit follow-up — 2026-09-30

The final CLI check returned `CANCEL_ACKNOWLEDGED` after an earlier running
status. The log ends around recording 16/100 with repeated TTS
`no audio frames were pushed` errors. No cause of cancellation is inferred.
The notebook source downloaded in `artifacts/kaggle-submission-source/` embeds
the older ElevenLabs/Ollama runtime, without prewarm or explicit session cleanup;
it is not the corrected runtime described above. CLI source represents the
published notebook, and may differ from unsaved interactive editor changes.

Regenerated `artifacts/kaggle-fixed/interra_setup.ipynb` from the current working
tree, with metadata targeting the existing private Maitri Shah notebook. It uses
LiveKit Inference and the cleanup/argument fixes. An embedded runtime now takes
precedence over any attached historical source dataset; a regression test proves
the old dataset cannot silently replace an updated notebook. The package CLI
also accepts `--kernel-id` so it can target the existing account explicitly.
This notebook has not been uploaded or started from this session.

To regenerate it after further changes:

```bash
python scripts/kaggle_package.py artifacts/kaggle-fixed --kernel-id maitrishah29/interra-fdb-v3-http-tts-smoke
```

126 local tests passed on Python 3.11, including 12 new lifecycle/argument
regressions. Lifecycle tests assert cleanup traces and no orphaned waiters.
Checked supported APIs in the exact LiveKit Agents 1.8.3 wheel from PyPI.
Local mocks do not prove hosted speech quality or quota recovery.

Import `artifacts/kaggle-fixed/interra_setup.ipynb` in the existing Kaggle
notebook using File → Import Notebook. Keep T4 and Internet enabled and attach
`LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` through Kaggle Secrets.
Use Save Version → Save & Run All. The packaged notebook embeds the updated
runtime and has the full benchmark enabled.

After the speech gate, verify each room produces a `session_cleanup` trace.
Compare strict pass, silent recordings, inference failures, and STT 429 rooms
against the table above. If 429s remain despite clean sessions, check the
LiveKit project's inference quota and other active consumers. Avoid running
multiple notebooks against the same project concurrently.

The current browser integration reports `unsupported Codex auth method:
apikey`, so importing and starting this version could not be performed from
this session. Kaggle CLI status/log/report reads succeeded.
