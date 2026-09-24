# Live voice and FDB-v3: beginner setup

The updated Theme05 guide replaces the old queue benchmark with Full-Duplex-Bench v3.
The old 48.9/100 fallback result is **not** an FDB-v3 score. Installing a model alone
does not prove task completion: run a real voice session, then the benchmark.

There are three separate pieces:

| Piece | Purpose | Current choice |
| --- | --- | --- |
| LiveKit Cloud | Connects the microphone/benchmark audio to the agent | Your own project |
| Models | Understand text, transcribe audio, generate speech | Hosted OpenAI by default; optional Ollama text planner |
| Evaluator | Transcribes recordings and scores tools, answers and timing | Pinned FDB-v3, NVIDIA Parakeet and a hosted judge |

Your Windows laptop can develop and try the voice agent. The full upstream evaluator
calls CUDA directly; it needs a Linux NVIDIA GPU machine. The guide specifies one
48 GB NVIDIA GPU, CUDA 12/13. An Intel integrated GPU cannot replace that GPU.
The Linux dependency lock resolves, but its installation and full evaluation have not
been verified here. Do not represent this as a completed clean-machine reproduction.

## 1. Create a LiveKit project

1. Open [LiveKit Cloud](https://cloud.livekit.io/) and sign up/sign in.
2. Create a project for Interra. Use a dedicated project so another running agent does
   not receive benchmark rooms intended for Interra.
3. Find the project's API keys/settings. Copy its project URL (`wss://...`), API key,
   and API secret into the local file described below. Do not send them in chat.

See [LiveKit's official quickstart](https://docs.livekit.io/agents/start/voice-ai/).
This repository already supplies the agent; you do not need to create a second starter project.

## 2. Configure models and credentials

The default pipeline uses OpenAI for planning, speech recognition and speech output.
Create an API key in your OpenAI API account and enable API billing as required by
that account. Keep usage limits appropriate for a benchmark run. This setup does not
use credentials from your coding assistant.

In PowerShell, open the project and create the ignored local settings file:

```powershell
cd C:\Users\Lenovo\Interra
Copy-Item .env.example .env.local
notepad .env.local
```

Do the copy only once; if `.env.local` already exists, open it directly. Fill the four
blank settings with your own values, save, and close Notepad:

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your-project-key
LIVEKIT_API_SECRET=your-project-secret
OPENAI_API_KEY=your-api-key
```

These are placeholders. Never commit `.env.local`. Model IDs, seed and upstream source
revision are declared in `config/fdb.json`. Planner JSON is validated by Interra before
any tool executes. The hosted API uses JSON mode with local strict validation because
arbitrary tool argument maps cannot use a closed schema unchanged; see
[OpenAI structured output guidance](https://developers.openai.com/api/docs/guides/structured-outputs).

## 3. Install and try the timer extension on Windows

With Python 3.11 and Git installed, run from the project directory:

```powershell
py -3.11 scripts/reproduce_fdb.py --prepare-only
.\.venv-fdb\Scripts\python.exe -m agent.fdb.livekit_agent download-files
$env:INTERRA_USE_CASE = "kitchen"
.\.venv-fdb\Scripts\python.exe -m agent.fdb.livekit_agent console
```

If `py` is unavailable on this existing checkout, the first command can use
`.\.venv\Scripts\python.exe` instead of `py -3.11`.
The setup creates `.venv-fdb`, installs pinned dependencies and checks out pinned
public tool definitions. `download-files` obtains the voice activity detector.
Allow microphone access, use headphones, and speak when console audio mode is active.
If the console begins in text mode, use its displayed audio/text toggle.

Try: “Start a 15 second tea timer.” Then: “Which timers are running?” Then:
“Cancel the tea timer and make it five seconds instead.” The replacement should finish
once, with no later notification from the canceled timer. Timers last only for that
session. The backend starts real timers; the automated evidence uses virtual time.
Live speech quality and this microphone flow still need your credentialed run.

For a Cloud session, run `dev` instead of `console`, then connect through the
LiveKit agent console using the same project. The worker runs locally; a Cloud agent
deployment is not required for this benchmark. Save the trace and a genuine recording.

## 4. Optional local text model

This is optional: it replaces the planner, not speech recognition, speech synthesis,
or the benchmark judge. Those still use the configured hosted key in this implementation.

1. Install Ollama using its [Windows instructions](https://docs.ollama.com/windows).
2. Open a new PowerShell window and run:

```powershell
ollama pull qwen2.5:3b
ollama run qwen2.5:3b
```

3. Ask a short question. A response confirms the model runs. Enter `/bye` to leave
   the chat; keep the Ollama app running.
4. Add these lines to `.env.local`:

```dotenv
INTERRA_PLANNER=ollama
INTERRA_MODEL=qwen2.5:3b
INTERRA_OLLAMA_URL=http://localhost:11434
```

5. Restart the voice agent and repeat the timer test. Remove these three settings to
   return to the declared hosted planner.

[This model download is about 1.9 GB](https://ollama.com/library/qwen2.5:3b). It is a
small starting point for this laptop, not a validated benchmark recommendation. CPU
responses can be slow and schema/tool accuracy must be measured. It does not interpret
images. No local model was downloaded during implementation. Record `ollama list`
and `ollama show qwen2.5:3b` with results because model tags can change.

## 5. Run the full benchmark on the evaluation machine

Prepare Linux x86-64, Python 3.11 with venv support, Git, ffmpeg, libsndfile, PortAudio,
and a working NVIDIA driver (`nvidia-smi` must work). Keep enough disk space for CUDA
libraries, the Parakeet model, released audio and output recordings; the laptop's
remaining disk space is not a tested evaluation capacity. Configure `.env.local` there.

Download and extract the release linked in the pinned
[FDB-v3 README](https://github.com/DanielLin94144/Full-Duplex-Bench/tree/3e799c45a045256f47d5f1c9cda90157e2d2ec9e/v3).
The directory should contain sample folders, each with `input.wav` and `metadata.json`.
The current guide describes 100 examples, 79 scenarios and 12 speakers. Use the full
release for submission; the command records the actual input count and hashes and
does not relabel a subset as a full run.

From the checkout, run this one command, replacing the dataset path:

```bash
python3.11 scripts/reproduce_fdb.py --data-dir /absolute/path/fdb_v3_data_released
```

It installs the Linux evaluation lock, checks pinned upstream source, makes a fresh
input-only copy, downloads VAD assets, starts the custom worker, runs inference and
three evaluators, and stops its worker. No previous result files are reused. Do not
run competing unnamed agents on the same LiveKit project while benchmarking.

Results go under `artifacts/fdb-runs/<run-id>/`: configuration, input hashes, commands,
dependency versions, logs, recordings, tool results, runtime traces and three reports.
Every input must have completed inference and a nonempty transcript. Report counts
must match. A failed run exits with an error and is not recorded as completed.

The wrapper copies evaluators into the run folder, pins their judge model and changes
exception handlers to re-raise rather than silently falling back or skipping failures.
Successful scoring formulas and prompts stay intact; these two explicit adaptations
are recorded in `run.json`. Keep both upstream source and generated evaluators for review.
The local judge pin is our declared setting, **not a claim about the organizers' final
common judge**. Align it with their published setting when supplied.

Seed 17 controls mock latency and hosted planner requests. Hosted inference and real
network/audio timing are not guaranteed bit-for-bit reproducible. The runtime never
receives benchmark answer annotations; only the evaluator/driver reads metadata.

## Troubleshooting and evidence

- `--check` is a read-only prerequisite report. It does not validate credentials.
- Missing settings: open `.env.local`; do not paste keys into terminal commands or chat.
- Worker fails to register: inspect `worker.log`, project URL/keys and network access.
- Unauthorized/model unavailable: check API access and declared model IDs, then record
  any configuration change before rerunning.
- No microphone: check Windows microphone permissions and input device; use headphones.
- Empty audio or partial evaluation: inspect the run logs. Do not call it a score.
- Linux install/ASR failure: preserve logs and resolve on the declared GPU machine.

Offline checks (no paid requests):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -q
.\.venv-fdb\Scripts\python.exe scripts/check_fdb_adapter.py
```

The second check imports the real SDK and all 12 public tool schemas; it does not
establish audio/model quality. See `STATUS.md` for what has actually been run.

## Attribution

FDB-v3 is developed by National Taiwan University, with NVIDIA research discussion
and advisory contribution, as stated upstream. The pinned repository uses CC BY-NC 4.0;
preserve its LICENSE and attribution when sharing its data/code or generated evaluator
copies. This project references the upstream release rather than redistributing its dataset.
