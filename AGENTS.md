# AGENTS.md — Repository Instructions

## Mission

Build a robust **interruptible real-time agent runtime** for Samsung PRISM Theme 05.

The project must remain responsive while reasoning and executing tools, recover cleanly when a user interrupts or changes goals, preserve relevant session state, reject stale work, prevent duplicate side effects, and support text, WAV audio, and PNG visual inputs.

This repository is evaluated primarily on **behavior under event timing**, not on frontend polish.

## Source priority

Use this order of authority:

1. Official Samsung evaluation kit and schemas, once available.
2. `docs/00_REQUIREMENTS_SOURCE_OF_TRUTH.md`.
3. Other `docs/*.md` engineering specifications.
4. Existing code/tests.
5. Agent assumptions.

If two sources conflict, follow the higher-priority source and document the resolution in `docs/STATUS.md`.

## Required runtime philosophy

The system is split conceptually into:

- **Fast Path** — rapid, truthful acknowledgments/clarifications/progress narration.
- **Slow Path** — intent/slot reasoning, multimodal interpretation, planning, and asynchronous tool use.
- **Coordination Layer** — event ordering, state snapshots, cancellation/invalidation, tool lifecycle, stale-result rejection, idempotency, and protocol serialization.

The LLM is a component, not the controller of concurrency correctness.

## Engineering constraints

- Primary runtime: Python 3.11 unless the official kit requires another version within Python 3.10–3.12.
- Core orchestration: `asyncio` and explicit typed domain models.
- Keep third-party dependencies minimal.
- Use deterministic clocks/schedulers in tests.
- No global mutable session state.
- No cross-session memory/caching of user state.
- No hard-coded tool names, intents, or benchmark scenarios.
- All tool calls require explicit `call_id`.
- All internally meaningful events/actions require IDs and timestamps.
- State changes must be versioned.
- Model outputs must be schema-validated before use.
- Invalid model output must fail safely, not silently.
- Logs must never fabricate successful completion.
- A canceled/stale tool result must not modify active state or trigger a final answer for the obsolete plan.
- State-modifying calls must have duplicate protection.
- Read-only and state-modifying tools must be distinguished from the manifest/adapter layer.

## Required repository shape

Prefer the following layout unless the official kit imposes another one:

```text
.
├── AGENTS.md
├── MASTER_AGENT_PROMPT.md
├── README.md
├── pyproject.toml
├── Dockerfile
├── src/
│   └── agent/
│       ├── app.py
│       ├── runtime.py
│       ├── events.py
│       ├── actions.py
│       ├── state.py
│       ├── coordinator.py
│       ├── floor.py
│       ├── planner.py
│       ├── scheduler.py
│       ├── safety.py
│       ├── protocol.py
│       ├── trace.py
│       ├── clock.py
│       ├── tools/
│       │   ├── manifest.py
│       │   ├── registry.py
│       │   └── executor.py
│       ├── multimodal/
│       │   ├── audio.py
│       │   ├── vision.py
│       │   └── fusion.py
│       └── providers/
│           ├── base.py
│           └── ...
├── tests/
│   ├── unit/
│   ├── scenarios/
│   ├── adversarial/
│   └── fixtures/
└── docs/
```

## Coding standards

- Prefer small explicit functions over framework magic.
- Every asynchronous task must have an owner and lifecycle.
- Never create background tasks that cannot be canceled/awaited.
- Never swallow `CancelledError`.
- Use `try/finally` for lifecycle cleanup.
- Distinguish:
  - cancellation requested,
  - cancellation acknowledged,
  - call already completed,
  - call may have committed a side effect.
- Avoid polling where event-driven notification is available.
- Serialize actions only at the protocol boundary.
- Keep domain models provider-agnostic.

## Testing standards

Every bug in orchestration requires a regression test.

Minimum tests must include:

- normal single-turn call,
- chained tool calls,
- slot correction during read-only call,
- goal/intent switch during call,
- interrupt immediately before result,
- interrupt immediately after result,
- stale result after cancellation,
- duplicate tool result,
- duplicate state-changing request,
- tool timeout/failure,
- clarification,
- unseen tool manifest,
- malformed/invalid model output,
- audio-only,
- visual-only,
- audio + visual,
- multimodal interruption,
- out-of-order asynchronous results.

Tests must assert traces, not only final text.

## Forbidden shortcuts

Do not:

- implement one giant prompt that “handles everything”;
- keep state only in chat history;
- use string matching as the sole interruption strategy;
- cancel all tasks blindly on every new chunk;
- retry state-changing tools blindly;
- accept a tool result solely because it returned successfully;
- use a UI event loop as the core scheduler;
- optimize speech synthesis, wake words, or frontend styling before the evaluator is strong.

## Documentation discipline

Update `docs/STATUS.md` after every implementation phase with:

- completed work,
- tests added,
- test counts/results,
- known limitations,
- next tasks,
- architectural decisions that changed.

Do not mark a requirement complete without a test or trace proving it.

## Cursor Cloud specific instructions

The Cloud Agent image provides Python 3.12 as `python3`. Use the project virtualenv, which the environment install creates with Python 3.11:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m agent.demo
.venv/bin/python scripts/check_submission.py
```

`python3 -m unittest discover -s tests` can hang on Python 3.12 inside `LatestFrameSource.close` (`test_disconnect_discards_unconsumed_frame`). On 3.12, `asyncio.gather` of already-finished tasks returns without yielding, so that cleanup loop never lets the done callbacks remove the tasks. Python 3.11.16 finishes the same 139 tests.

The environment install is `pip install -e ".[voice]"` (LiveKit Agents 1.8.3 and RTC 1.1.18). The `fdb` extra and `requirements.txt` pull the CUDA scorer (`nemo_toolkit`) and are outside this environment. `ffmpeg` is on `PATH`. The Cloud Agent environment injects `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` for hosted FDB-v3 and the camera worker. Read them from the process environment. Do not write them into `.env`, logs, or the repository. The recorded interruption demo is [Interra demo](https://cursor.com/artifacts/v/art-65efdeb4-9b6f-4416-839a-875b82833f5a). The unit tests and `agent.demo` run without calling LiveKit. A hosted readiness check can list rooms with `livekit.api.LiveKitAPI`; the full 100-recording benchmark still needs the pinned checkout and released audio, which this environment does not download.

## Presentation policy — latest user instruction, 2026-10-05

The sole presentation is `docs/Interra_Theme05_submission.pptx`, exactly 8 slides.
All links and submission archives must use it. Do not recreate the retired 12-slide
presentation. This replaces the earlier request to keep 12 slides.
