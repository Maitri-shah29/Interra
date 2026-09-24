# Interra

**Current evaluation: Full-Duplex-Bench v3 via LiveKit**, per the updated Theme05 guide.
Start with [beginner voice/model setup](docs/FDB_SETUP.md). The new adapter connects
speech to the session runtime, dynamically loads the 12 public tools, and includes
a real kitchen-timer extension. Live benchmark and microphone results are still pending
credentials and the evaluation GPU. The earlier 48.9 queue-kit result is historical.

After configuring credentials and downloading the released dataset, the evaluation
machine entrypoint is:

```bash
python3.11 scripts/reproduce_fdb.py --data-dir /absolute/path/fdb_v3_data_released
```

Use `--prepare-only` for Windows voice development, or `--check` for prerequisites.
The original core/queue-adapter instructions below remain useful for offline regression
tests; they are not the updated official submission workflow.

Interra is a provider-agnostic, interruptible real-time agent runtime for Samsung PRISM
Theme 05. Its core contribution is deterministic coordination under concurrent user input,
reasoning, multimodal perception and tool results—not a chatbot persona or UI.

## What it proves

- One session-owned asynchronous event consumer and output-action queue.
- Fast, truthful acknowledgments while slow planning/perception remains cancellable.
- Versioned intent/slot state with localized corrections.
- Dependency-aware tool cancellation and stale-result rejection.
- Dynamic manifest-driven tools with strict argument validation.
- Logical operation keys that block duplicate state-changing calls.
- Asynchronous WAV and PNG adapters with source provenance.
- Deterministic virtual-clock scenarios and complete JSONL traces.

## Requirements

- Python 3.11 (the package accepts Python 3.11–3.12).
- No model or network is required for the deterministic suite or demo.
- Optional live adapters require explicitly configured Ollama/audio/vision services.

## Setup

PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -v
```

POSIX shell:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -v
```

## Run the actual-runtime demo

```bash
python -m agent.demo
# or, after installation:
interra-demo
```

The console timeline uses the production `SessionRuntime`. It dispatches a read-only call,
accepts a barge-in and destination correction, emits cancellation, receives the obsolete
result late, rejects it, and completes only the current call. It displays state versions,
call statuses and trace-derived safety/latency metrics. See `docs/DEMO.md`.

## Docker

```bash
docker build -t interra-runtime .
docker run --rm interra-runtime
docker run --rm interra-runtime python -m unittest discover -v
```

The image runs as an unprivileged user and defaults to the same actual-runtime demo.

## Architecture

```text
timestamped events
        |
        v
SessionRuntime / serialized coordination
   |            |                    |
Fast Path   State + Planner   Audio/Vision tasks
   |            |                    |
   +------------+---------+----------+
                          v
        ToolScheduler + SafetyLedger
                          |
                          v
                 structured actions
```

The model proposes validated plans; it never controls cancellation, result admissibility,
state mutation, write retries or completion. See `docs/ARCHITECTURE.md` and the numbered
specifications in `docs/`.

## Internal provisional protocol

Input events include text chunks, interruptions, dynamic manifests, tool results, WAV audio,
PNG frames and cancellation acknowledgments. Output actions include speech, clarification,
tool calls, cancellation and final responses with intent/slot snapshots. Every envelope and
tool call has an explicit ID and timestamp.

`src/agent/protocol.py` isolates the internal schema. The supplied official Samsung
kit is preserved under `vendor/samsung_theme05`; `interra_submission:ParticipantAgent`
implements its external queue contract. See [kit review and evaluation instructions](docs/KIT_REVIEW.md).
No live-model task-completion score is claimed.

## Optional provider adapters

- `OllamaProvider(model, endpoint)` posts schema-constrained planning requests.
- `HTTPAudioProvider(endpoint)` expects WAV bytes and returns an `AudioObservation` JSON body.
- `HTTPVisionProvider(endpoint)` expects PNG bytes and returns a `VisualObservation` JSON body.

The official entrypoint reads explicit `INTERRA_MODEL`, `INTERRA_OLLAMA_URL`,
`INTERRA_AUDIO_URL`, `INTERRA_VISION_URL`, and `INTERRA_MEDIA_ROOT` configuration.
MP3 decoding requires ffmpeg (`INTERRA_FFMPEG` can select its path). No model is
downloaded automatically. Core correctness is tested with deterministic mocks.

From the repository root, `python scripts/run_samsung.py` saves complete public
harness traces. Configure models first; `--allow-unconfigured` explicitly tests
the safe fallback. For the official three-repetition procedure run:

```bash
python vendor/samsung_theme05/eval_submission.py . --reps 3 --time-scale 1 --out artifacts/samsung-evaluation.json
```

Set `INTERRA_MEDIA_ROOT` to the absolute path of `vendor/samsung_theme05` for public
media, or the supplied hidden-kit root during evaluation. Replace the team
placeholder in `submission.yaml` before submission.

## Test organization

- `tests/unit/`: models, protocol, manifests, virtual clock, metrics and packaging.
- `tests/scenarios/`: text, scheduling, safety, providers, floor management, audio, vision,
  fusion, performance traces and the actual-runtime demo.
- `tests/adversarial/`: interruption/result timing boundaries and stale-work races.
- `artifacts/traces/`: generated per-test JSONL traces (ignored by Git).

Run a focused module with:

```bash
python -m unittest tests.adversarial.test_interruptions -v
```

## Known limitations

- The official queue integration is tested; a configured reasoning/ASR/vision model
  and live three-repetition quality evaluation remain outstanding.
- Live model, speech and vision quality/latency have not been benchmarked.
- A dispatched write can have an unknown external outcome; Interra blocks a blind retry but
  cannot guarantee rollback without tool-specific reconciliation semantics.
- Local media envelopes use bounded base64 data; the official adapter resolves MP3/WAV
  and PNG file references within its configured media root.
- Safety-ledger state is session/process scoped; crash-durable side-effect recovery is outside
  the supplied requirements.
- A recorded demo video and team-completed presentation export are human submission tasks and
  are not fabricated by this repository.

Current evidence, blockers and exact next work are maintained in `docs/STATUS.md`.
