# Demo guide

Demo video: [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a)

The final video must be a three-to-five-minute recording of real system behavior.
The historical deterministic replay can explain cancellation internals, but it
cannot replace the required benchmark and extension demonstrations.

## Required sequence

1. Show the active LiveKit agent and identify the STT, LLM, and TTS providers.
2. Run one FDB-v3 recording containing an interruption or self-correction.
3. Show the resulting tool calls and explain how the corrected value reached them.
4. Run camera-assisted device troubleshooting end to end.
5. Interrupt or correct the extension while perception or reasoning is active.
6. Show the final FDB-v3 report values and state one measured limitation.

## Commands

Check the environment before recording:

```bash
python scripts/fdb_v3.py check
```

Start the agent independently when rehearsing:

```bash
python scripts/fdb_v3.py agent
```

Open the judge pages after both workers are running. Voice uses the name
`interra-benchmark`. Camera keeps its own name. `/voice` and `/camera` show
live captions and the gate decision; the session's Logs button opens its worker
trace in a second tab. Without workers, `/replay` plays the scored 4 October
2026 run from its recorded trace; label it as a replay if it appears in the
video:

```bash
# voice worker
INTERRA_FDB_AGENT_NAME=interra-benchmark python -m agent.fdb_livekit dev
# camera worker, second terminal
python -m agent.extension_livekit dev
# home worker, third terminal
python -m agent.home_livekit dev
# page
python -m agent.demo_page
```

Start the camera extension in a separate terminal. It is not the benchmark agent:

```bash
python -m agent.extension_livekit start
```

The SmartThings home extension runs on `/home`. A one-minute take: "Set the
living room AC to 22 and turn off the lights. No wait, make it 24." Then
"Unlock the front door", "Yes". Then press "Add a new device" and say "Set the
air purifier to high", then "Undo that". If LiveKit is unavailable, "Play
walkthrough" runs the same script through the real controller with no room;
label it as a scripted walkthrough if it appears in the video.

Replay the booking correction without a camera or a LiveKit room:

```bash
python -m agent.extension_demo
```

After a rehearsal, open the trace beside the recording. Executed, dropped, and reused calls are labeled:

```bash
python -m agent.replay artifacts/fdb_v3/livekit-agent.jsonl -o artifacts/fdb_v3/replay.html
```

Run and evaluate the released benchmark:

```bash
python scripts/fdb_v3.py all --force --use-llm
```

The scripted coordination replay remains available for engineering review:

```bash
python -m agent.demo
```

Its `STALE_RESULT_DISCARDED` event proves a deterministic safety property only;
label it clearly if any excerpt appears in the final video.

## Recording outline

- 0:00–0:35: problem, benchmark, and architecture.
- 0:35–2:05: real FDB-v3 interruption or correction and tool telemetry.
- 2:05–3:35: camera-assisted extension with a live correction.
- 3:35–4:25: benchmark scores and latency.
- 4:25–5:00: limitations and next work.

Use an unedited single take where practical. Do not claim the historical
queue-kit score as an FDB-v3 result and do not show API keys on screen.
