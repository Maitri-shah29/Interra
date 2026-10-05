# Submission completion update — 2026-10-05

The user confirms that submission tasks are complete. The sole presentation is
`docs/Interra_Theme05_submission.pptx` (8 slides). Latest official automated
results: 70/100 strict passes and 100/100 turn-takes, dated 2026-10-04.

The historical engineering checklist below describes earlier verification scope.
Unchecked research/quality items are not claims of missing submission deliverables.
Organizer judging and single-session repeatability remain measurement limits,
not pending presentation tasks.

# Definition of done

The updated participant guide makes FDB-v3, LiveKit, and a working extension the
submission contract. The old queue-kit checklist is retained in Git history and
its tests remain useful, but it no longer defines completion.

## FDB-v3 agent

- [x] LiveKit voice-agent entry point exists.
- [x] The 12 official tool signatures are exposed to the model.
- [x] Tool execution does not block the event loop.
- [x] Tool calls use the room identifier and benchmark telemetry format.
- [x] LiveKit Inference STT and TTS model IDs and voice are pinned by configuration.
- [x] LiveKit Inference LLM is the default; Ollama remains an optional backend.
- [x] Clean environment installation of the FDB dependency profile passes (`.[fdb]` in a fresh Kaggle venv on 2026-10-04; the `.[voice]` worker image builds from a clean checkout on 2026-10-05).
- [x] LiveKit Cloud credentials and dispatch are validated by the 100-case run.
- [ ] Every released recording completes without agent or protocol crashes.

## Benchmark evidence

- [x] Official repository revision is pinned.
- [x] Reproduction runner can bootstrap the benchmark and published data.
- [x] Runner starts the agent, runs inference, and invokes the tool and pass evaluators;
  optional LLM-assisted latency analysis requires `--use-llm`.
- [x] The latest run covers all 100 recordings; aggregate reports and all 100 per-recording results are archived (`docs/results/kaggle-20261004/`).
- [x] Official tool-selection report is saved.
- [ ] Semantic argument and response report is saved with the LLM judge enabled. The organizers run the pinned judge in their own re-run; a self-check (`--use-llm`) needs `OPENAI_API_KEY` and is optional.
- [x] Strict pass-rate report is saved for the latest run (70/100 on 2026-10-04; 43/100 on 2026-10-01; previous main baseline 31/100).
- [ ] First-response, tool-call, and task-completion latency report is saved.
- [x] Model versions, provider configuration, measured source, recording results
  and logs are archived with hashes in `docs/results/`. The original sampling
  seed was not recorded; the manifest records that limitation explicitly.
- [ ] Reproduction is verified on a clean machine.

## Interruption and correction behavior

- [x] Existing deterministic tests cover cancellation, stale results, corrections,
  duplicate writes, and timing races.
- [ ] FDB STT reliably preserves fillers, false starts, and self-corrections.
  The agent requests this behavior, but measured transcription errors remain.
- [ ] A real FDB recording proves the latest correction reaches tool arguments.
- [ ] A real live interruption stops obsolete speech and work.
- [ ] Multi-step tool chains use returned identifiers rather than guessed values.
- [x] No benchmark scenario is hard-coded, memorized, or used for fine-tuning (`tests/unit/test_no_benchmark_answers.py` scans the prompt and tool docs for every expected argument).
- [ ] No scenario state is cached across conversations.

## Extension use case

- [x] Camera-assisted device troubleshooting is selected.
- [x] Image observations, embeddings, provenance, and stale-result rejection exist
  in the retained runtime.
- [x] Camera input is connected to a separate LiveKit voice session
  (`python -m agent.extension_livekit start`). It does not join the benchmark tool set.
- [ ] The extension runs end to end with a real user interruption or correction.
- [ ] The extension appears in the final demo video.

## Documentation and submission

- [x] README identifies FDB-v3 as the current benchmark.
- [x] Exact environment-variable names are documented without secret values.
- [x] Hosted and local model responsibilities are documented honestly.
- [ ] Repeat full runs in single sessions are still needed for repeatability claims; normalized scores come from the organizers' judged re-run.
- [ ] Demo video is three to five minutes and shows real behavior.
- [x] Sole submission deck contains exactly 8 slides.
- [x] Team name, college and members are on slide 1 of `docs/Interra_Theme05_submission.pptx`.
- [ ] Submission Google Form is complete.
- [ ] The last uploaded submission is verified as the intended final version.

Only mark a box complete when a report, trace, test, or recorded run proves it.
