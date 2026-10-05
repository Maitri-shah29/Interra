# Kaggle run — 2026-10-04

- Kernel: `adityavardhankochar/interra-fdb-v3-benchmark`, started from the Kaggle
  editor with Save & Run All. Agent code from `a770259` (unchanged through
  `515b315`). Models: LiveKit Inference Deepgram Nova-3, GPT-4.1 mini
  (temperature 0), Cartesia Sonic-3.
- Benchmark: pinned Full-Duplex-Bench v3 commit
  `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`; 100 recordings; organizer LLM
  judge off (exact-match arguments).
- Method: the run's LiveKit project ran out of Inference credits partway
  through. The 29 recordings from the first quota error onward were re-recorded
  with the same code (`agent.fdb_offline outage-seed`, cut by start time, not by
  score); the other 71 results were kept unchanged. The official evaluator
  scored all 100.

## Results (official evaluator, all 100 recordings)

| Metric | Value |
| --- | --- |
| Strict pass | **70/100** |
| Turn-take | 100/100 (no response: 0) |
| Tool selection accuracy | 93.4% |
| Argument accuracy | 76.3% |
| Average response latency | 4.452 s (std 3.002 s) |
| Early interruptions | 3 |

By domain (tool selection / argument accuracy): ecommerce 98.3% / 80.5%,
finance 89.3% / 84.7%, housing 91.2% / 69.9%, travel 94.1% / 68.3%.
Failures: 13 wrong tools, 17 wrong arguments.

## Trace health (final 100 rooms)

- 152 tool calls, all completed; 1 duplicate call returned from cache;
  22 turns held mid-sentence; 100 session cleanups and 100 provider cooldowns;
  0 session errors.
- Recorder crashes (exit -6 in the pinned `livekit_inference.py`): 6 in the
  re-recording session, each retried once and completed; none still failed.
- No stall restarts were needed.

## Verification

```bash
python -m agent.fdb_offline rescore --run docs/results/kaggle-20261004   # 70/100 from the agent traces
unzip -q docs/results/kaggle-20261004/interra-fdb-results.zip -d /tmp/verify
cd .runtime/Full-Duplex-Bench/v3 && python evaluate_pass_rate.py \
  --benchmark benchmark_data_v2.json --results-dir /tmp/verify/per-recording \
  --provider interra_elevenlabs --output /tmp/verify/pass_rate.json       # 70/100, identical per recording
```

## Files

- `interra_elevenlabs_pass_rate_report.json`, `interra_elevenlabs_evaluation_report.json`:
  official evaluator reports over all 100 recordings.
- `interra-fdb-results.zip`: the 100 final per-recording results (no audio).
- `seeded-results.json`: which 71 results were kept and which 29 re-recorded, and why.
- `kaggle-kernel-logs.json`, `livekit-agent.jsonl`, `recorder-retries.json`,
  `interra-setup-report.json`: first session. The `-resume` files: the
  re-recording session. The LiveKit project URL is redacted in both logs.
- `tool-calls.jsonl`: tool-call events of the final 100 rooms. `rescore.json`:
  the offline rescore.

No audio, credentials or model weights are included.
