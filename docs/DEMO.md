# Demo guide

## Current submission recording — 3–5 minutes

Use `FDB_SETUP.md` to configure real voice and run FDB-v3 first. A recording remains
pending: the offline timeline below does not replace the requested live voice demo.

1. 0:00–0:30: show the declared models, source revision and architecture. Explain that
   LiveKit transports speech while the coordinator owns state and tool execution.
2. 0:30–2:00: show an actual benchmark interaction with a correction/interruption.
   Show its recording and trace together, including cancellation/stale-result evidence.
   Use a genuine run; do not script a pretend successful transcript.
3. 2:00–3:30: demonstrate the kitchen extension with a real microphone. Start a timer,
   ask which timers run, cancel it and create a shorter replacement. Show one expiry
   notification, timer IDs, accepted results and no notification from the canceled one.
4. 3:30–4:30: show report counts, tool/argument accuracy, strict pass and latency from
   the saved run. State observed limitations and the one-command reproduction path.

The extension has automated runtime-to-tool-to-final trace coverage, but its real voice
recording and benchmark quality remain unverified until credentialed execution.

## Historical offline engineering demo

## Run

From an installed development environment:

```bash
python -m agent.demo
```

Or:

```bash
docker build -t interra-runtime .
docker run --rm interra-runtime
```

## What the timeline proves

The demo uses `SessionRuntime`, `VirtualClock`, `LocalProtocol`, `ToolScheduler`,
`SafetyLedger` and `TraceRecorder` directly. Only the deterministic provider and external
tool-result injection are demo edge adapters.

Expected sequence:

1. A dynamic read-only lookup manifest arrives.
2. The user requests Chennai to Delhi options.
3. The runtime emits a truthful fast acknowledgment and a tool call with `call_id`.
4. At virtual time 0.50, the user interrupts.
5. The old call is invalidated and cancellation is emitted.
6. The user corrects only the destination to Mumbai; origin remains Chennai.
7. The obsolete Delhi result arrives and is logged as `STALE_RESULT_DISCARDED`.
8. The current Mumbai result is accepted.
9. The final response is emitted from canonical state version 2.

The output must show the old call as `CANCEL_REQUESTED`, the current call as `COMPLETED`,
`destination=Mumbai`, one stale result discarded and zero duplicate operation dispatches.

## Suggested five-minute recording

- 0:00–0:25: explain why listen-think-speak fails under barge-in.
- 0:25–0:50: show Fast Path, Slow Path and coordination architecture.
- 0:50–2:20: run the console hero demo and point to cancellation plus stale rejection.
- 2:20–3:15: run `tests.scenarios.test_safety` and explain logical operation keys.
- 3:15–4:05: run `tests.scenarios.test_multimodal` and show fused provenance.
- 4:05–4:35: show the full test result and adversarial timing matrix traces.
- 4:35–5:00: state the official-kit and live-provider limitations precisely.

Do not edit the trace output or claim an official score. Record the actual command output and
keep the final video at or below five minutes.
