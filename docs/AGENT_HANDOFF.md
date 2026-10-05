# Interra Theme 05 — agent handoff

## Objective and current truth

The active requirement is the updated Theme 05 guide: a **Full-Duplex-Bench
v3 (FDB-v3) LiveKit voice agent**. The prior queue-based Samsung kit remains
as regression evidence only. Do not report its historical 49.5 score as an
FDB-v3 result.

This branch adds a LiveKit agent with ElevenLabs Scribe/Turbo, local Ollama
Qwen3 8B reasoning, official mock-tool integration, trace collection, the FDB
bootstrap/evaluation wrapper, old-runtime fixes, tests, Docker/local setup,
and submission documentation. The FDB-v3 score is **not yet measured**.

Official benchmark pin: `DanielLin94144/Full-Duplex-Bench` commit
`3e799c45a045256f47d5f1c9cda90157e2d2ec9e`, `v3/`.

## What is complete

- `src/agent/fdb_livekit.py`: LiveKit agent, Silero VAD, ElevenLabs Scribe v2
  Realtime, Qwen3 8B via local Ollama, ElevenLabs Turbo TTS, official mock
  tools, result grounding, traces, and cancellation-aware tool execution.
- `scripts/fdb_v3.py`: one-command FDB bootstrap, preflight, run, and report
  workflow. It obtains the official code/data outside Git.
- `scripts/kaggle_setup.py`: a reproducible Kaggle T4 worker setup that keeps
  dependencies in a virtual environment and reads secrets solely from Kaggle
  Secrets.
- Existing queue-runtime work: interruption recovery, state snapshots,
  stale-result rejection, source-bound image embeddings, local providers, and
  trace replay.
- Tests: `python -m unittest discover -s tests -v` previously passed **89/89**.

## What remains, in priority order

1. Run a genuine FDB-v3 baseline on Kaggle with LiveKit and ElevenLabs
   credentials, then preserve raw logs/results and report the exact model and
   benchmark commit.
2. Diagnose actual failures by category: ASR loss of corrections, endpointing,
   wrong tool or arguments, chained-call grounding, interruption latency, and
   final-answer latency. Improve from measured failures only; never encode
   benchmark examples or expected answers.
3. Run repeatable seeded evaluations. Record pass rate, normalized score,
   latency reports, zero-crash/protocol-error checks, and environment versions.
4. Finish the extension: wire camera-assisted device troubleshooting through
   the LiveKit session, demonstrate it end-to-end, and include its source and
   limitations in the README/video.
5. Demo video: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a).
   Finish the 8-slide organizer deck, disclosure, team details, and release
   package.

## Credentials and inputs still needed

Do not put values in Git, notebooks, logs, issue comments, or this document.
Use only the variable/secret names below.

| Required for | Secret / input |
| --- | --- |
| LiveKit connection | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| Speech-to-text and text-to-speech | `ELEVEN_API_KEY` |
| Optional local scoring report | `OPENAI_API_KEY` |
| Kaggle execution | Kaggle account with GPU and Internet enabled |

The LiveKit and Kaggle credentials previously pasted into chat should be
rotated in their respective dashboards. A working Kaggle CLI session is already
present on the current PC; do not overwrite it merely to add another token.

## PC setup and responsibilities

The local machine is useful for editing, deterministic tests, packaging, and
Kaggle orchestration. It has insufficient free RAM for dependable Qwen3 8B
inference, so use Kaggle T4 for real-model FDB runs.

```powershell
git clone https://github.com/Maitri-shah29/Interra.git D:\projects\samsungprism\Interra
Set-Location D:\projects\samsungprism\Interra
git fetch origin codex/fdb-v3-livekit-kaggle-handoff
git switch --track origin/codex/fdb-v3-livekit-kaggle-handoff

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[fdb]"
python -m unittest discover -s tests -v
```

Install `ffmpeg` if it is not on `PATH`. For a local smoke test, install
Ollama, run `ollama pull qwen3:8b`, start `ollama serve`, copy
`.env.fdb.example` to `.env`, fill the required values locally, then run:

```powershell
python scripts/fdb_v3.py bootstrap --download-data
python scripts/fdb_v3.py check
```

Do not use the local PC for timing claims if it is paging or competing with
other applications.

### Kaggle CLI on the PC

The CLI is installed and authenticated here (`Kaggle CLI 2.2.4` and private
kernel access were verified). Verify without printing a credential:

```powershell
python -m kaggle --version
python -m kaggle kernels list --mine --page-size 1
```

For a fresh machine, install with `py -m pip install --upgrade kaggle`, then
run `python -m kaggle auth login` and complete its interactive login. Never
place an API token in a command history, `.env`, source file, or repository.

## Kaggle T4 workflow

The current private resources are:

- Kernel: `adityavardhankochar/interra` (older script kernel; latest run ERROR, missing source)
- Prepared self-contained benchmark kernel: `adityavardhankochar/interra-fdb-v3-benchmark` (must be a real `.ipynb`, not a raw `.py` uploaded as a notebook)
- Source dataset: `adityavardhankochar/interra-fdb-v3-private-source` (optional; `maitrishah29` is an ADMIN collaborator; **do not attach it** — it triggers Kaggle's `datasetVersionInfo` editor crash)
- Accelerator: Nvidia Tesla T4; Internet: enabled

Do **not** use `maitrishah29/notebook67853281`. That tab is Kaggle's empty
starter notebook. Its Secrets panel is valid, but Failed to save draft /
`datasetVersionInfo` means the data-source metadata never resolved. Remove
every Input/data source from that notebook, or start a **new** notebook with
no data attached, then import the packaged `interra_setup.ipynb`.

Use the following workflow from the PC after every meaningful code change.

1. Create a clean temporary source archive that excludes `.git`, `.venv`,
   `.runtime`, `artifacts`, `.env*`, and credentials. Upload a new version of
   the existing **private** Kaggle dataset with `python -m kaggle datasets
   version`. Confirm its files with `python -m kaggle datasets files`.
2. Make an upload folder containing exactly the packaged benchmark worker and
   its metadata. The package embeds the tracked `src`, `scripts`, and
   `pyproject.toml` files as a fallback for a missing private dataset mount:

   ```powershell
   $upload = Join-Path $env:TEMP "interra-kaggle-upload"
   New-Item -ItemType Directory -Force $upload | Out-Null
   python scripts\kaggle_package.py $upload
   ```

   Confirm `$upload\interra_setup.ipynb` is valid JSON and
   `$upload\kernel-metadata.json` has `"kernel_type": "notebook"` and
   `"dataset_sources": []`. Import that notebook in the Kaggle editor; do not
   upload the raw `.py` as a notebook.

3. A CLI push checks dependencies, but it does **not** carry over Secrets
   attached in the Kaggle editor. The 2026-09-29 CLI runs verified this: even
   after the four Secrets were added, a fresh CLI-pushed run reported all four
   missing. Do not use `kernels push` to start a credentialed evaluation.
4. Open the **packaged notebook** (`interra_setup.ipynb`), not a blank Kaggle
   template and not a raw `.py` file uploaded as a notebook. Confirm the first
   code cell contains `RUN_FULL_BENCHMARK = True` and a nonempty
   `EMBEDDED_SOURCE_B64`. Do not add the private source dataset. Keep GPU and
   Internet on. In **Add-ons → Secrets**, add and attach `LIVEKIT_URL`,
   `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `ELEVEN_API_KEY` to this exact
   notebook. Choose **Save Version → Save & Run All** in the editor. The worker
   checks that all four are readable before launching the benchmark. If the
   editor shows `datasetVersionInfo` or Failed to save draft, remove all data
   sources and save again before running.
5. Monitor the run with the CLI and download selected reports:

   ```powershell
   $env:PYTHONUTF8 = "1"
   python -m kaggle kernels status adityavardhankochar/interra-fdb-v3-benchmark
   python -m kaggle kernels logs adityavardhankochar/interra-fdb-v3-benchmark
   python -m kaggle kernels output adityavardhankochar/interra-fdb-v3-benchmark -p .\artifacts\kaggle-output --file-pattern 'interra-setup-report.json|fdb_v3.*\.json|fdb_v3.*\.jsonl'
   ```

6. Inspect `interra-setup-report.json`. It must show `livekit_ready: true` and
   `full_benchmark_run: true`. If the first check still lists missing Secret
   names, confirm they are attached to this notebook and rerun from the editor.

If the kernel cannot install Ollama, first inspect its logs. The provided setup
installs `zstd` before the Ollama installer because Kaggle images can omit it.

## Evaluation loop

1. On Kaggle, run `python scripts/fdb_v3.py all --force` with the default
   local `qwen3:8b` profile.
2. Download and commit raw result JSON, trace JSONL, tool-call telemetry, and
   a concise median report. Do not commit audio, credentials, or generated
   model weights.
3. Group failures by the cause listed above. Make one general fix at a time;
   add a regression test; rerun the affected fixed seeds and then the full set.
4. Evaluate three repetitions at `time_scale=1`. Treat benchmark output as the
   only score source. `OPENAI_API_KEY` is optional for local latency analysis,
   not necessary for the agent’s local Qwen3 reasoning.
5. Before release, reproduce setup in a clean environment, run the full unit
   suite, and verify no stale results, duplicate writes, crashes, or protocol
   errors.

## Useful entry points

- FDB requirements and submission rules: `docs/FDB_V3.md`
- Current state: `docs/STATUS.md`
- LiveKit agent: `src/agent/fdb_livekit.py`
- FDB commands: `scripts/fdb_v3.py --help`
- Kaggle worker: `scripts/kaggle_setup.py`
- Environment template: `.env.fdb.example`
- Presentation source: `docs/SUBMISSION_DECK.md`

## Guardrails

- Keep hosted providers declared and use their environment variable names;
  never commit their values.
- Do not hardcode FDB recordings, scenario answers, tool outputs, or prompts
  tailored to individual benchmark cases.
- Preserve official harness/scorer behavior. Change the agent and its adapter,
  not the benchmark.
- Keep the previous queue runtime as historical evidence, separate from FDB-v3
  score claims.
