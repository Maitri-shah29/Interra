# Interra

Interra is a voice agent that stays in the conversation while it works. A person
can hesitate, interrupt, or correct themselves in the middle of a sentence. The
agent answers out loud and runs tool calls outside the event loop. The deployed
LiveKit agent uses SDK turn interruption and tool calling; its prompt asks it to
ground completion claims in tool results. The separate coordination runtime has
tested stale-result rejection and duplicate-write protection. Those guarantees
are not yet integrated into the FDB-v3 adapter.

Most assistants listen, then think, then speak. That falls apart when the
destination changes while a route is still being looked up, or when a booking
is corrected before the first change has been committed. Interra is built for
that kind of overlap.

The scored evaluation is Full-Duplex-Bench v3: 100 recorded human
conversations, 79 scenarios, and 12 mock tools, including fillers, pauses,
false starts, and self-corrections. Interra runs that benchmark as a LiveKit
voice agent. A second LiveKit session handles camera-assisted device
troubleshooting, which is outside those benchmark domains.

Speech and tool calls go through one cascaded LiveKit session:

```text
recorded or live speech
        |
        v
LiveKit room and Silero VAD
        |
        v
LiveKit Inference: Deepgram Nova-3
        |
        v
LiveKit Inference: GPT-4.1 mini
        |
        +----> 12 official FDB-v3 mock tools
        |
        v
LiveKit Inference: Cartesia Sonic-3
```

Tool execution runs outside the event loop. Each call is stored with its room
identifier and timestamps. Results come from the official mock backend, not
from model memory. Ollama Qwen 3 8B is an optional local fallback
(`INTERRA_FDB_LLM_PROVIDER=ollama`).

The earlier queue runtime remains in the repository as coordination research.
Its retained entry point is `interra_submission:ParticipantAgent`; its evidence
does not substitute for an FDB-v3 run.

## Presentation and demo

- Presentation: [8-slide submission deck](docs/Interra_Theme05_submission.pptx)
- Demo video: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a)

The recording is three to five minutes: one benchmark interruption or
self-correction, then the camera troubleshooting session.

## Repository

| Path | Contents |
| --- | --- |
| `src/agent/fdb_livekit.py` | FDB-v3 LiveKit agent |
| `src/agent/extension_livekit.py` | Camera troubleshooting session |
| `src/agent/` | Coordination runtime, tools, and multimodal code |
| `scripts/fdb_v3.py` | Benchmark checkout, run, and evaluation |
| `requirements.txt` | Python dependencies for the FDB profile |
| `pyproject.toml` | Package metadata; LiveKit Agents 1.8.3 and RTC 1.1.18 pinned |
| `Dockerfile` | Python 3.11 voice-worker image with `ffmpeg` |
| `tests/` | Deterministic runtime and agent tests |
| `.env.fdb.example` | Environment variable names, with empty secrets |
| `docs/FDB_V3.md` | Benchmark pin, models, and scoring contract |
| `docs/AI_DISCLOSURE_DRAFT.md` | AI usage disclosure |
| `docs/Interra_Theme05_submission.pptx` | Audited 8-slide submission deck |
| `docs/results/kaggle-20261004/` | Latest reports, logs, traces, per-recording results, and run manifest |
| `vendor/samsung_theme05/` | Superseded queue kit |

## Models

| Stage | Model |
| --- | --- |
| Voice activity | Silero VAD |
| Speech recognition | LiveKit Inference `deepgram/nova-3` |
| Tool calling | LiveKit Inference `openai/gpt-4.1-mini` |
| Speech synthesis | LiveKit Inference `cartesia/sonic-3`, voice `9626c31c-bec5-4cca-baa8-f8ba9e84c8bc` |
| Optional local LLM | Ollama `qwen3:8b` |

Benchmark pin: `DanielLin94144/Full-Duplex-Bench` commit
`3e799c45a045256f47d5f1c9cda90157e2d2ec9e`, directory `v3/`.

Report filenames use the provider id `interra_elevenlabs` because the pinned
runner requires that name. The speech models in the table above are the ones
this agent loads.

## Reproducible setup

Use Python 3.11 or 3.12, Git, and `ffmpeg` on `PATH`. The agent calls LiveKit
Cloud for speech and the default language model, so those calls do not need a
local GPU. The official 100-recording scorer uses the benchmark ASR stack and
is meant to run on a CUDA machine.

From a fresh checkout, fill `.env` using `.env.fdb.example`, then run:

```bash
python scripts/reproduce.py
```

This command reads `.env`, creates `.venv-fdb`, installs dependencies, downloads
the pinned benchmark and released recordings, starts the agent, runs a speech
gate and all recordings, and evaluates the results. Existing shell values take
precedence. Add `--use-llm` and an `OPENAI_API_KEY` for the optional local judge.
`--seed 0` is the default Python hash seed; hosted models are not deterministic.
Git and ffmpeg must already be installed, and LiveKit inference quota must be
available. The best measured run did not record a sampling seed.

The following manual steps are useful when running or debugging each stage.

Create a virtual environment and install the pinned dependencies, then this
package:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps
```

On Windows PowerShell, use `.venv\Scripts\python.exe` in place of
`.venv/bin/python`.

Activate that environment before the manual `python` commands below:
`source .venv/bin/activate` in Bash, or `.venv\Scripts\Activate.ps1` in PowerShell.

Copy the environment template and set the three LiveKit values. `.env` is
gitignored.

```bash
cp .env.fdb.example .env
```

```powershell
Copy-Item .env.fdb.example .env
```

Required variables:

```text
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
```

The configured speech models use provider failover: Nova-3 falls back to
AssemblyAI Universal-3.5, and Cartesia Sonic-3 falls back to Deepgram Aura-2.
The worker waits three seconds after cleaning up a room before accepting the
next one, so completed provider connections have time to release. Override the
fallback IDs or gap with `INTERRA_FDB_STT_FALLBACK_MODELS`,
`INTERRA_FDB_TTS_FALLBACK_MODELS`, or
`INTERRA_FDB_SESSION_COOLDOWN_SECONDS`. The language model remains
`openai/gpt-4.1-mini`, the best measured configuration in the archived report;
set `INTERRA_FDB_LLM_MODEL` to compare another LiveKit Inference model.

Load them into the shell before any benchmark command.

```bash
set -a
source .env
set +a
```

```powershell
Get-Content .env | Where-Object { $_ -and -not $_.StartsWith('#') } | ForEach-Object {
  $name, $value = $_.Split('=', 2)
  Set-Item -Path "Env:$name" -Value $value
}
```

`OPENAI_API_KEY` is used only with `--use-llm` for a local semantic judge. The
organizers score the run with their own judge.

Check out the pinned benchmark. Add `--download-data` to fetch the released
audio through the published Google Drive id. Both land under `.runtime/`,
which is not part of this repository.

```bash
python scripts/fdb_v3.py bootstrap --download-data
python scripts/fdb_v3.py check
```

Set `INTERRA_FDB_V3_ROOT` and `INTERRA_FDB_DATA_ROOT` when the checkout or the
audio lives somewhere else. The defaults are in `.env.fdb.example`.

## Run the benchmark

One command starts the LiveKit agent, runs one recording as a speech gate,
runs the released set, and writes the tool and strict pass-rate reports:

```bash
python scripts/fdb_v3.py all --force
```

LiveKit credit: every recording uses LiveKit Inference for speech-to-text, the
LLM and text-to-speech, under the LiveKit project named by `LIVEKIT_*`. On
2026-10-04 a free-tier project ran out of Inference credit after 71 of 100
recordings (HTTP 429 `inference_quota_exceeded`), so use a project with enough
credit for a full run. The runner retries a recording whose official recorder
crashes, and restarts the agent if no result appears for
`INTERRA_FDB_STALL_SECONDS` (default 600).

Separate steps:

```bash
python scripts/fdb_v3.py agent
python scripts/fdb_v3.py benchmark --force
python scripts/fdb_v3.py evaluate
```

Optional local semantic report (needs `OPENAI_API_KEY`; the organizers run the
benchmark's pinned LLM judge themselves, so this is only a self-check):

```bash
python scripts/fdb_v3.py evaluate --use-llm
```

Reports are written to `artifacts/fdb_v3/`. The agent also appends the official
tool telemetry the benchmark reads from `/tmp/agent_tool_calls.log` on Linux.
On Windows that file is under the temp directory.

Docker builds the hosted voice worker, with the pinned LiveKit SDK and ffmpeg.
After bootstrapping the official checkout, start it with the checkout and trace
directory mounted (Bash example):

```bash
docker build -t interra .
docker run --rm --env-file .env \
  -e INTERRA_FDB_V3_ROOT=/bench/v3 \
  -e INTERRA_FDB_TRACE_DIR=/traces \
  -v "$PWD/.runtime/Full-Duplex-Bench:/bench:ro" \
  -v "$PWD/artifacts/fdb_v3:/traces" interra
```

The default command is `python -m agent.fdb_livekit start`. Run the official
CUDA scorer separately with the benchmark commands above. The worker image
does not include the scorer's GPU stack or benchmark data. On 2026-10-05 both
`Dockerfile` and `Dockerfile.local` built from a clean checkout (Docker 29.6).
The worker image runs as the non-root `interra` user with Python 3.11, ffmpeg,
LiveKit Agents 1.8.3 and LiveKit RTC 1.1.18, and all unit tests pass inside it
(`docker run --rm interra sh -c 'python -m unittest discover -s tests'`).

## Camera extension

Device troubleshooting is a second LiveKit session. It subscribes to the
participant camera, keeps the latest frame, and attaches that frame once to
the next spoken turn. Only the linked participant's camera is accepted;
disconnect discards its pending frame, and older images are removed from model
history. It does not register the 12 benchmark tools. Live end-to-end camera
behavior remains to be demonstrated.

```bash
python -m agent.extension_livekit start
```

The same `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` variables
are required. In the LiveKit project, dispatch this worker with the agent
name `interra-camera-troubleshooter`.

## Tests

```bash
python -m unittest discover -s tests -v
```

These tests check orchestration, cancellation, and the agent configuration.
They do not replace the official 100-recording reports.

Verify both the historical baseline archive, latest run hashes, score comparison,
and 8-slide deck with `python scripts/check_submission.py`. An incomplete review archive can be made
with `python scripts/package_review.py --allow-missing-demo`; supply `--demo`
with the actual video for a complete archive.

## Measured result

Latest official run (2026-10-04): LiveKit Inference Deepgram Nova-3, GPT-4.1 mini
(temperature 0), and Cartesia Sonic-3 on all 100 FDB-v3 recordings, scored by the
pinned official evaluator: **70/100 strict passes** and 100/100 turn-takes.

- [Run summary and verification commands](docs/results/kaggle-20261004/summary.md)
- [Strict pass report](docs/results/kaggle-20261004/interra_elevenlabs_pass_rate_report.json)
- [Tool and latency report](docs/results/kaggle-20261004/interra_elevenlabs_evaluation_report.json)
- [Run configuration and evidence hashes](docs/results/kaggle-20261004/run-manifest.json)
- [Per-recording results, traces and logs](docs/results/kaggle-20261004/)
- [2026-10-01 run](docs/results/kaggle-20261001/) and
  [previous completed run archive](docs/results/best-run-evidence.zip)

The pinned evaluator report filenames use `interra_elevenlabs` as the provider
id; this run used LiveKit Inference models.

| Metric | 2026-10-04 | 2026-10-01 | Previous main baseline |
| --- | ---: | ---: | ---: |
| Strict pass | **70/100** | 43/100 | 31/100 |
| Turn-take | 100/100 | 90/100 | 58/100 |
| No response | 0/100 | 10/100 | 42/100 |
| Wrong tools | 13 | 28 | 50 |
| Wrong arguments | 17 | 29 | 19 |
| Tool selection, turn-taken | 93.4% | 85.7% | 88.5% |
| Argument accuracy, turn-taken | 76.3% | 56.3% | 58.6% |
| Tool selection, all recordings | 93.4% | 77.2% | 51.3% |
| Argument accuracy, all recordings | 76.3% | 50.7% | 34.0% |
| Average response latency, excluding interruptions | 4.452 seconds | 4.175 seconds | 4.545 seconds |
| Early interruptions among turn-taken recordings | 3/100 | 11/90 | 6/58 |

The LiveKit project ran out of Inference credits partway through the 2026-10-04
run; the 29 recordings from the first quota error onward were re-recorded with
the same code and the official evaluator scored all 100. The organizer LLM
judge was disabled, so no normalized organizer score is claimed. See
[status and run history](docs/STATUS.md) and
[completion record](docs/10_DEFINITION_OF_DONE.md).

## Documentation

- [Slide deck](docs/Interra_Theme05_submission.pptx)
- [Official reports](docs/results/)
- [FDB-v3 contract](docs/FDB_V3.md)
- [Status and run history](docs/STATUS.md)
- [Demo sequence](docs/DEMO.md)
- [AI usage disclosure](docs/AI_DISCLOSURE_DRAFT.md)
