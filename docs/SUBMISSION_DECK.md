# Submission deck source — maximum 8 slides

Updated Theme05 guide governs this outline. Export only after replacing team/link
placeholders and adding genuine measurements. This is not a completed submission deck.

## 1. Interra / Theme 05
- Interruptible real-time voice agents.
- Team and college: TODO — provided by submitters.
- Repository, commit and demo link: TODO — verified final links.

## 2. Problem and evaluation
- Users correct slots or change goals during reasoning, tools and speech.
- Late results and duplicate writes threaten correctness.
- Current benchmark: Full-Duplex-Bench v3; LiveKit voice interface.
- Round 1: 60% benchmark, 20% extension, 20% documentation/video.

## 3. Architecture
- Speech → LiveKit STT → session mailbox → validated planner proposals.
- Fast acknowledgments; asynchronous reasoning and tool execution.
- Versioned state, dependency checks, cancellation acknowledgments and write ledger.
- Current speech actions → LiveKit TTS; generation checks suppress obsolete output.

## 4. Interruption evidence
- VAD immediately fences queued output before the coordinator consumes interruption.
- Cancellable async tool delays preserve event-loop responsiveness.
- Cancellation before dispatch, during a call and late results have regression coverage.
- Insert an actual benchmark trace/audio segment; label deterministic tests separately.

## 5. FDB-v3 results
- Declared planner/STT/TTS/judge and pinned upstream revision from config/fdb.json.
- TODO: completed input count, tool/argument accuracy, strict pass and latency.
- Include run configuration and report links; explain any failures.
- No current FDB score exists. The old 48.9 queue-kit score is not an FDB result.

## 6. Extension: kitchen timers
- New domain: real session-owned timer start/list/cancel, plus expiry notification.
- Correct a timer through cancel-and-replace; no state shared across sessions.
- Automated trace covers chained tools, truthful final and timed notification.
- TODO: genuine voice demo of start → correction → cancellation → replacement expiry.

## 7. Reproduction and deployment
- One command: python3.11 scripts/reproduce_fdb.py --data-dir <released-data>.
- LiveKit project and hosted API credentials kept outside Git.
- Python 3.11, Linux NVIDIA evaluation environment, dependency/model/source pins.
- Input hashes, configuration, commands, recordings and traces retained per run.
- Judge pin and fail-closed exception adaptations explicitly disclosed.

## 8. Limits and next work
- Current proof: offline correctness and real SDK import/transcript handoff.
- Pending: GPU clean install, live voice/benchmark quality, full release rerun.
- Cascaded endpointing and local small-model latency need measurement.
- Submitters must complete identities, demo link and reviewed AI disclosure.
