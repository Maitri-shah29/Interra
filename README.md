# Interra

Interra is a voice agent that keeps working correctly when people pause,
interrupt or correct themselves mid-sentence. It is our entry for Samsung
PRISM GenAI Hackathon 2026, Theme 05, built as a LiveKit voice agent and scored
on [Full-Duplex-Bench v3](docs/FDB_V3.md).

**Result:** 70/100 strict passes and 100/100 turn-takes on all 100 released
FDB-v3 recordings, scored by the pinned official evaluator on 4 October 2026
([details](#results)).

- Presentation: [8-slide submission deck](docs/Interra_Theme05_submission.pptx)
- Demo video: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a)
- Evidence: [run summary, reports, traces and per-recording results](docs/results/kaggle-20261004/)

## What it does

Most voice agents listen, think, then act. That breaks when the person keeps
talking: a pause looks like the end of a request, a correction arrives after a
tool call was planned, and a regenerated reply repeats an action. Interra puts
a coordination layer between what the model wants to do and what actually
runs:

- **Unfinished-turn hold.** A turn that ends on "and", "then" or "I'm looking
  for" is held and merged with the next words instead of being answered.
- **Commit gate.** A tool call is held until the person has been quiet for
  0.6 s. If they said more meanwhile, the model re-checks the call against
  their complete words: an add-on ("track order CAT, then search for cat
  food") keeps it, and a correction or cancellation drops it before it runs or
  is logged.
- **One run per identical call.** A repeated call in the same conversation
  returns the first result instead of acting twice.
- **Speech normalizers.** Spoken values become what the backend expects:
  "p five two" becomes `P52`, "the twentieth of May" becomes `May 20`.

The 70/100 run predates the commit gate, and the gate has not been measured on
a full run. So the benchmark agent runs without it when the official runner
dispatches it, and with it when it runs under the name `interra-benchmark` for
the judge page (`INTERRA_FDB_COMMIT_GATE`, see [Configuration](#configuration)).
The camera and SmartThings home extensions always use the gate.

## Results

Official evaluator, pinned benchmark commit, all 100 recordings. The 2026-10-04
run used LiveKit Inference Deepgram Nova-3, GPT-4.1 mini (temperature 0) and
Cartesia Sonic-3.

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

Read these numbers with their limits:

- Arguments are scored by exact match; the organizer LLM judge was off, so no
  normalized organizer score is claimed.
- The LiveKit project ran out of Inference credit mid-run. The 29 recordings
  from the first quota error onward were re-recorded with the same code, cut
  by start time, not by score.
- It is one run, and hosted models are not fully deterministic.

Evidence for the 2026-10-04 run: [summary and verification
commands](docs/results/kaggle-20261004/summary.md), [strict pass
report](docs/results/kaggle-20261004/interra_elevenlabs_pass_rate_report.json),
[tool and latency report](docs/results/kaggle-20261004/interra_elevenlabs_evaluation_report.json)
and [configuration and hashes](docs/results/kaggle-20261004/run-manifest.json).
Earlier runs: [2026-10-01](docs/results/kaggle-20261001/) and the [main
baseline archive](docs/results/best-run-evidence.zip). Report filenames use the
provider id `interra_elevenlabs` because the pinned runner requires that name.

## How it works

```text
speech in
 └─► Silero VAD ─► Deepgram Nova-3 (speech to text)
      └─► unfinished-turn hold
           └─► GPT-4.1 mini plans tool calls
                ├─► commit gate: hold until quiet, re-check if the person said more
                │    └─► 12 official FDB-v3 mock tools ─► results back to the model
                └─► Cartesia Sonic-3 (text to speech) ─► speech out
```

| Stage | Model or component |
| --- | --- |
| Voice activity | Silero VAD |
| Speech recognition | LiveKit Inference `deepgram/nova-3`, falls back to AssemblyAI Universal-3.5 |
| Tool calling | LiveKit Inference `openai/gpt-4.1-mini`; Ollama `qwen3:8b` optional |
| Speech synthesis | LiveKit Inference `cartesia/sonic-3`, voice `9626c31c-bec5-4cca-baa8-f8ba9e84c8bc`, falls back to Deepgram Aura-2 |
| Tools | The benchmark's own `MockAPIRegistry`, run in worker threads |

Each LiveKit job owns one conversation, and nothing is shared between rooms.
Every tool call is logged with its room and timestamps, results come from the
official mock backend, and each session is cleaned up and drained before the
worker takes the next room.

## Quick start

You need Python 3.11, Git, `ffmpeg` on `PATH`, and a LiveKit Cloud project with
enough Inference credit for 100 recordings. Speech and the default model run
on LiveKit Inference, so the agent needs no GPU. The official scorer runs its
own speech recognition and is meant for a CUDA machine.

1. Copy the environment template and fill in the three LiveKit values:

   ```bash
   cp .env.fdb.example .env
   ```

   ```text
   LIVEKIT_URL
   LIVEKIT_API_KEY
   LIVEKIT_API_SECRET
   ```

2. Run everything with one command:

   ```bash
   python scripts/reproduce.py
   ```

   This creates `.venv-fdb`, installs the pinned dependencies, downloads the
   pinned benchmark and its released recordings, starts the agent, checks one
   recording, runs all 100, and writes the reports to `artifacts/fdb_v3/`.
   It records the commit, benchmark pin, models and seed in
   `artifacts/fdb_v3/runs/<timestamp>/run_info.txt`, never secret values.
   Values already set in the shell win over `.env`.

### Step by step

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e . --no-deps

python scripts/fdb_v3.py bootstrap --download-data   # pinned checkout and audio, under .runtime/
python scripts/fdb_v3.py check                       # settings, checkout, data, ffmpeg
python scripts/fdb_v3.py all --force                 # agent, one-recording check, full run, reports
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`. The
official runner reads tool calls from `/tmp/agent_tool_calls.log`, so run
scored benchmarks on Linux or WSL.

The stages can also run separately: `fdb_v3.py agent`, `fdb_v3.py benchmark
--force` and `fdb_v3.py evaluate`. `fdb_v3.py evaluate --use-llm` adds an
optional LLM-judged report and needs `OPENAI_API_KEY`; the organizers run the
pinned judge themselves.

If a run stops because Inference credit runs out, keep what finished and
re-record only the rest. The run's folder needs its `livekit-agent.jsonl` and
`interra-fdb-results.zip`:

```bash
python -m agent.fdb_offline outage-seed --run <run folder> --output fdb-seed
INTERRA_FDB_SEED_RESULTS=fdb-seed python scripts/fdb_v3.py all
```

## Judge pages and demo

The judge pages show a live voice session, a live camera session and the scored
run on one site. Start each worker in its own terminal, then the pages:

```bash
INTERRA_FDB_AGENT_NAME=interra-benchmark python -m agent.fdb_livekit dev   # voice, with the gate
python -m agent.extension_livekit dev                                      # camera
python -m agent.home_livekit dev                                           # SmartThings home
python -m agent.demo_page                                                  # http://127.0.0.1:8780/
```

| Page | Shows |
| --- | --- |
| Overview | The idea, the measured figures, links to the sessions |
| Voice, Camera | A live session: captions, the gate's latest decision, a switchable visualizer |
| Home | A live home session with device tiles, "Add a new device", and a walkthrough that needs no LiveKit |
| Replay | The scored housing_11 recording, played from its own trace with the evaluator's result |
| Logs | Every session in the local trace, filterable, with `.jsonl` download |
| Results | The figures and the comparison table above |
| Settings | Every setting the code reads, editable when `INTERRA_ADMIN_PASSWORD` is set |

Replay and Results work without LiveKit. Two replays need no room or camera:

```bash
python -m agent.extension_demo                                             # camera booking: Friday dropped, Saturday kept
python -m agent.home_demo                                                  # home: 22 dropped, 24 set, door after a yes
python -m agent.replay artifacts/fdb_v3/livekit-agent.jsonl -o artifacts/fdb_v3/replay.html
```

`python -m agent.serve` runs the pages and both workers in one process tree,
restarting any that exit; see [deployment](docs/DEPLOY.md) and
[AWS EC2](docs/DEPLOY_AWS.md) for hosting it.

## Camera extension

The extension is camera-assisted device troubleshooting, outside the benchmark
domains, in its own LiveKit session (`interra-camera-troubleshooter`). It keeps
only the latest frame from the linked participant's camera and attaches it to
the next spoken turn; older images leave the model's history. It looks up a
visible code in Interra's own demo catalog (`E1`, `E2`, `E3`, not a
manufacturer manual), offers one safe check, and books a technician visit
through the commit gate, so "Friday… no, Saturday" stores Saturday only.

## SmartThings home extension

A voice session for a SmartThings-style home (`interra-home`). Each device is
described the way SmartThings describes devices: capabilities, with attributes
that can be read and commands that change state. Interra generates its tools
from those descriptions, marking each one read-only or state-changing and
routine or risky, so there are no hand-written tools per device. The capability
names follow SmartThings' public model; the eight devices are simulated, and
nothing talks to a real SmartThings account.

On top of the commit gate:

- the latest value wins: "22… no, 24" never sends 22;
- a value a device already has is not sent again;
- unlocking a door or starting the oven waits for a spoken yes;
- "undo that" reverses the last reversible change.

During a call, the Home page's "Add a new device" sends an air purifier the
agent has never seen, with capability definitions it has never seen, over
LiveKit RPC. The worker validates it and adds its tools to the live session.
"Play walkthrough" runs the scripted demo through the real controller with no
LiveKit room; the person's lines and the re-check's answer are scripted there.

## Configuration

Settings are environment variables, read from the shell or `.env`
([template](.env.fdb.example)).

| Variable | Default | Effect |
| --- | --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | none | Required |
| `INTERRA_FDB_COMMIT_GATE` | `auto` | `auto`: gate on only when the worker has a dispatch name; `on` or `off` forces it |
| `INTERRA_FDB_COMMIT_QUIET_SECONDS` | `0.6` | Quiet time before a held call runs |
| `INTERRA_FDB_AGENT_NAME` | empty | Dispatch name; empty lets the official runner auto-dispatch the worker |
| `INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS` | `1.0` | Silence after which a held half-sentence is answered; `0` turns the hold off |
| `INTERRA_FDB_ENDPOINTING_MIN_DELAY`, `_MAX_DELAY` | `0.7`, `1.2` | Turn-end delays the semantic turn detector chooses between |
| `INTERRA_FDB_LLM_PROVIDER`, `INTERRA_FDB_LLM_MODEL` | `livekit`, `openai/gpt-4.1-mini` | Model route; `ollama` uses `INTERRA_OLLAMA_MODEL` |
| `INTERRA_FDB_LLM_TEMPERATURE` | `0` | Sampling temperature |
| `INTERRA_FDB_STT_FALLBACK_MODELS`, `INTERRA_FDB_TTS_FALLBACK_MODELS` | see template | Speech failover models |
| `INTERRA_FDB_SESSION_COOLDOWN_SECONDS` | `3` | Pause after each room so provider connections close |
| `INTERRA_FDB_V3_ROOT`, `INTERRA_FDB_DATA_ROOT` | under `.runtime/` | Benchmark checkout and audio |
| `INTERRA_FDB_TRACE_DIR` | `artifacts/fdb_v3` | Where workers write `livekit-agent.jsonl` |
| `INTERRA_FDB_RECORDER_RETRIES` | `1` | Re-runs of recordings whose official recorder crashed |
| `INTERRA_FDB_STALL_SECONDS` | `600` | Restart the agent when no result appears for this long |

## Docker

`Dockerfile` builds the voice worker (Python 3.11, `ffmpeg`, LiveKit Agents
1.8.3 and RTC 1.1.18, non-root). It does not include the benchmark data or the
scorer's GPU stack. After `fdb_v3.py bootstrap`:

```bash
docker build -t interra .
docker run --rm --env-file .env \
  -e INTERRA_FDB_V3_ROOT=/bench/v3 -e INTERRA_FDB_TRACE_DIR=/traces \
  -v "$PWD/.runtime/Full-Duplex-Bench:/bench:ro" \
  -v "$PWD/artifacts/fdb_v3:/traces" interra
```

`Dockerfile.deploy` builds the hosted image with the pages and both workers.

## Tests and checks

```bash
python -m unittest discover -s tests -v      # runtime, agent, pages and scripts
python scripts/check_submission.py           # evidence hashes, scores and the 8-slide deck
```

The tests cover coordination, cancellation, the agent's configuration, the
pages and the scripts. They do not replace the official 100-recording reports.

## Repository

| Path | Contents |
| --- | --- |
| `src/agent/fdb_livekit.py` | Benchmark voice agent |
| `src/agent/commit.py` | Commit gate and re-check |
| `src/agent/extension_livekit.py`, `device_catalog.py` | Camera extension |
| `src/agent/smart_home.py`, `home_livekit.py`, `home_demo.py` | SmartThings home extension |
| `src/agent/demo_page.py`, `web/`, `serve.py`, `settings.py` | Judge pages, hosting and settings |
| `src/agent/fdb_offline.py` | Offline rescoring, transcript replay and outage reseeding |
| `src/agent/runtime.py` and neighbours | Research runtime (see below) |
| `scripts/` | Benchmark runner, reproduction, Kaggle packaging, submission checks |
| `tests/` | Unit, scenario and adversarial tests |
| `docs/` | Benchmark contract, status, deployment, demo guide, AI disclosure |
| `docs/results/` | Official reports, traces and run manifests |
| `vendor/samsung_theme05/` | The original queue-based Theme 05 kit |

## Research runtime

Before the benchmark changed to FDB-v3, Interra was built for Samsung's
original queue-based kit. That runtime (`src/agent/runtime.py`) has versioned
slot state, stale-result rejection, an at-most-once write ledger and validated
audio and image input, all tested on a virtual clock. Its entry point is
`interra_submission:ParticipantAgent`. It is kept as engineering work and
regression coverage; its evidence does not substitute for an FDB-v3 run.

## Documentation

- [FDB-v3 contract and benchmark pin](docs/FDB_V3.md)
- [Status and run history](docs/STATUS.md)
- [Demo guide](docs/DEMO.md)
- [Deployment](docs/DEPLOY.md) and [AWS EC2](docs/DEPLOY_AWS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [AI usage disclosure](docs/AI_DISCLOSURE_DRAFT.md)
