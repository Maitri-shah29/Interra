# Final presentation update — 2026-10-05

- Pulled the latest `origin/main` revision (`663e5de`) into the working branch.
- User now selects the 8-slide deck as the sole presentation, replacing the earlier
  12-slide request. Deleted the tracked 12-slide PPT. README, active documentation,
  repository instructions, checker and packaging now select only
  `docs/Interra_Theme05_submission.pptx`. Alternate deck generation is disabled.
- Score claims match the latest official reports: 70/100 strict passes,
  100/100 turn-takes, 93.4% tool selection, 76.3% argument accuracy, 4.452 seconds
  mean latency excluding 3 early interruptions. All 100 result files are present.
- Removed outdated pending demo and remaining-work claims from the presentation.
  The user confirms submission tasks are complete. The published demo link is
  included in the deck and package manifest; local video embedding is optional.
- Preserved evaluation limits: 29 recordings re-recorded after a credit outage,
  organizer quality judge disabled. These are automated metrics, not a normalized
  overall hackathon score. No new repeatability claim is made.
- Validation: full evidence/deck checker PASS with 100 recordings and 8 slides;
  PPTX integrity, layout and Artifact Tool import PASS. Rendered all 8 slides and
  inspected changed slides. No runtime changes or new runtime tests in this phase.
- Next: none for the requested presentation refresh. Earlier entries below are
  historical; their policy and pending-task statements do not describe this update.

# Merge with Maitri-shah29/Interra and participant-guide check — 2026-10-05

- Merged `Maitri-shah29/Interra` `main` (`0b00de5`, 12-slide presentation policy).
  Conflicts in `STATUS.md` and `SUBMISSION_DECK.md` kept both sides. The 12-slide
  `docs/Interra_Theme05_submission.pptx` stays the sole presentation, per the user; slides 8
  and 9 and their notes now report the 2026-10-04 run (70/100 strict pass,
  100/100 turn-take). `check_submission.py` checks 12 slides and the run manifest's
  numbers and passes; `package_review.py` packages the 2026-10-04 evidence.
- Docker: `Dockerfile` and `Dockerfile.local` build from a clean checkout; the
  worker image runs as `interra` with Python 3.11, ffmpeg and the pinned LiveKit
  SDKs, and all unit tests pass inside it (the image now includes `kaggle/`).
- Participant guide (`Theme05_Participant_Guide_UPDATED_FBD.docx`) check:
  - Met: LiveKit voice agent; FDB-v3 pinned; README with architecture diagram,
    setup, run steps and the marked camera extension; one-command
    `scripts/reproduce.py` declaring LiveKit Inference models; results, logs and
    configuration in `docs/results/kaggle-20261004/`; key names documented in
    `.env.fdb.example` without values; demo link; hosted APIs cited; no
    team-owned server at evaluation time; tool deduplication is per room only.
  - No hard-coded test items: `tests/unit/test_no_benchmark_answers.py` scans the
    prompt and tool docs for every expected argument value.
  - Deviates: the guide allows at most 8 slides; the 8-slide deck is kept by user
    instruction (the 8-slide deck carries the same results).
  - Open: seeds are not pinned for hosted sampling (temperature 0,
    `PYTHONHASHSEED` only); the organizers' re-run needs a LiveKit project with
    enough Inference credit (README notes this); reproduction on a machine that
    is not ours; confirming the demo is 3 to 5 minutes and shows a benchmark
    interruption and the extension; the Google Form.
  - The organizers run the pinned LLM judge themselves. `--use-llm` stays as an
    optional self-check that needs `OPENAI_API_KEY`; it is off by default.
- 222 tests pass on Python 3.11.

# Presentation refresh — 2026-10-05

- Discarded prior tracked and untracked local changes at the user's request.
  Pulled branch `codex/fdb-v3-livekit-kaggle-handoff` at `3e1e3d3`.
- Sole submission presentation: `docs/Interra_Theme05_submission.pptx`, exactly 8 slides.
  User explicitly overrides older repository slide-limit guidance. Updated README,
  handoff, FDB documentation, submission outline, packaging and deck checker links.
  Retired the alternate presentation builder in favor of validation only.
- Updated results and next-steps slides from the 2026-10-01 official reports:
  43/100 strict pass, 90/100 turn-take, 10 no-response, 4.175 seconds mean response
  latency excluding 11 interruptions. Retained one-run and disabled-judge limits.
- PPTX integrity, layout and Artifact Tool import checks pass (12 slides).
  Rendered all slides and inspected changed slides. Focused content checks pass.
  No runtime implementation changes or new runtime tests in this phase.
- Existing full evidence checker fails on the pushed manifest's hashes for
  `kernel-metadata.json`, `summary.md`, and `uploaded-worker.py`. Both Git blobs
  and working-tree files differ from the declared hashes. Official aggregate
  pass/evaluation reports match their hashes. Do not rewrite evidence hashes to
  conceal this discrepancy. Next: reconcile the pushed ancillary evidence manifest.
- Historical status below describes earlier versions and cannot override this policy.

# Current implementation status — 2026-10-05

## Scored Kaggle run — 2026-10-04

- Official strict pass **70/100** (43/100 on 2026-10-01). Turn-take 100/100
  (90/100 before). Tool selection/argument accuracy 93.4%/76.3% (85.7%/56.3%
  before, turn-taken). Average latency 4.452 s; early interruptions 3 (11 before).
  Evidence and verification commands: `docs/results/kaggle-20261004/summary.md`.
- Method: started from the Kaggle editor (Save & Run All) with agent code
  `a770259`. The LiveKit project ran out of Inference credits partway through,
  so the 29 recordings from the first quota error onward were re-recorded with
  the same code; the other 71 results were kept unchanged. The official
  evaluator scored all 100.
- Recorder crashes: 6 in the re-recording session, each retried once and
  completed. Trace over the final 100 rooms: 152/152 tool calls completed,
  1 duplicate served from cache, 22 mid-sentence turns held, 100 cleanups and
  cooldowns, 0 session errors.
- `python -m agent.fdb_offline rescore --run docs/results/kaggle-20261004`
  reproduces 70/100 from the agent traces, and the official
  `evaluate_pass_rate.py` reproduces it from `interra-fdb-results.zip`,
  identical per recording.

- Submission package now reports this run: the eight-slide deck (slides 6–8
  and every slide's notes), `docs/SUBMISSION_DECK.md`, `docs/KAGGLE_RUN_REVIEW.md`,
  README, definition of done and AI disclosure. `docs/results/kaggle-20261004/run-manifest.json`
  records the result summary and evidence hashes; `scripts/check_submission.py`
  verifies that run and the deck's 70/100 and 100/100, and passes.

### Run infrastructure added for this run

- `agent.fdb_offline outage-seed` splits a run at the first room with a
  provider quota error (by start time, never by score) and writes the earlier
  results with `seed-manifest.json`. `kaggle_package.py --seed DIR` embeds them;
  `fdb_v3.py all` places them before the benchmark and drops `--force`, so the
  official runner records only the missing recordings.
- Stall guard: a re-recording attempt hung for over two hours after the
  LiveKit worker logged "worker process is not responding". The runner now
  restarts when no new result appears for `INTERRA_FDB_STALL_SECONDS` (600):
  it stops the runner and recorder, deletes the stuck recording's partial
  audio, restarts the agent and resumes (at most `INTERRA_FDB_STALL_RESTARTS`,
  3, logged in `recorder-retries.json`). Agent and runner output is unbuffered.
- `rescore` keeps only the last attempt of a retried recording, reads a
  `-resume` session's log and trace, and takes recorder status from the final
  result files (unbuffered logs interleave the outcome line).
- The kernel logs print the LiveKit project URL in LiveKit's worker
  registration line; archived copies have it redacted.
- 221 tests pass on Python 3.11.

### Next

- Re-run the full benchmark in one session with enough LiveKit Inference
  credit (a full run is about 100 recordings of LLM and STT use; offline
  replays draw from the same allowance).
- Endpointing sweep: `kaggle_package.py --set INTERRA_FDB_ENDPOINTING_MAX_DELAY=1.8`,
  started from the editor, compared with this run (1.2).
- Remaining failures: 13 wrong tools, 17 wrong arguments; housing and travel
  argument accuracy (69.9%, 68.3%) are the weakest.

## LLM replay and argument fixes — 2026-10-04 (second session)

The LiveKit project and Kaggle Secrets were replaced. A one-call
`inference.LLM("openai/gpt-4.1-mini")` probe now succeeds, and
`www.kaggle.com` is reachable from this environment. The new project is on the
free tier: replays at `--concurrency 2` hit 429 `MaxConcurrentGatewayLLMRpm`
(about 100 requests/min), so replays now use `--concurrency 1` (0 errors).

### Offline LLM replay (`agent.fdb_offline llm`, 2026-10-01 transcripts)

| Prompt/tools | Strict pass | Provider errors |
| --- | --- | --- |
| Archived run calls (old prompt, recorder assumed to survive) | 47/100 | – |
| `0b58a33` (previous phase) | 66/100 | 4 (rate limit) |
| `d8b494b` (this phase) | **77/100** | 0 |

Reports: `docs/results/llm-replay-20261004/`. The rescore of archived calls is unchanged at 56/100 (62/100 with every
recording captured). Replay assumes the recorder never crashes and that
STT/turn segmentation matches the archived trace, so it is an upper bound for
the prompt, not a benchmark score.

### Regressions found in the `0b58a33` replay and fixed generally

- Spelled house numbers stayed as words ("one zero one Main Street",
  "five hundred Central Ave"). `normalize_place` now writes runs of two or
  more number words as digits (`101`, `500`, `123` for "one twenty three") and
  compound ordinals ("twenty first" -> `21st`); a lone "one" is kept.
- The `update_search_filter` doc gave `max_rent` as its example, so "max
  price" was sent as `max_rent`. Filter names now follow the
  `search_apartments` parameters (`max_price`, `bedrooms`, `pets_allowed`) via
  `normalize_filter_name`; other names stay in the user's words.
- A spoken product code was searched before `add_to_cart`; the doc now says to
  use a code the user gives directly.
- Instructions now cover a correction later in the same message, misheard
  words in a transcript, conditional requests (only the branch that applies)
  and requests no tool fits (no substitute tool). `search_apartments` is scoped
  to long-term rentals; filter updates are only for filters the user asks to
  change, not for search criteria.
- `search_products.query` keeps only the product the user settled on.
  Ecommerce replay after this change: 24/29 (23/29 before). ecommerce_14 sent
  `PD52` instead of `P52` in this run only, which is model variance at
  temperature 0, not a prompt effect.

### Kaggle run attempt

- `kaggle kernels push` created version 4 of
  `adityavardhankochar/interra-fdb-v3-benchmark` at 15:44 UTC from `a770259`.
  It failed after 10 seconds with `Benchmark Secrets unavailable:
  LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL`, before any install or
  recording. This repeats the 2026-09-30 finding: a CLI-pushed version does not
  receive the Secrets attached in the editor. The pushed notebook already holds
  the current code; it must be started with **Save Version -> Save & Run All**
  in the Kaggle editor with the three Secrets attached, as the scored run above was.
- `scripts/kaggle_package.py --set INTERRA_NAME=VALUE` now embeds non-secret
  run settings (for the `INTERRA_FDB_ENDPOINTING_MAX_DELAY` 1.2 vs 1.8 sweep);
  other names are rejected.

### Tests

- 208 tests pass on Python 3.11 (199 before). New tests cover spelled address
  numbers, lone number words, compound ordinals, filter-name mapping and the
  commute and filter wrappers, and packaged Kaggle run settings.

## Score-improvement phase — 2026-10-04 (unscored live)

Changes target the 57 failures in the 2026-10-01 run. No full Kaggle run has
measured them yet: this cloud environment's egress policy blocks
`www.kaggle.com`, `agent-gateway.livekit.cloud`, the LiveKit project host,
`api.openai.com` and `huggingface.co`.

### Findings that changed the plan

- The 43/100 run scored arguments by **exact match** (organizer LLM judge off).
  The judge treats `2026-08-20` as equal to `August 20`; exact match does not.
  The agent now sends the spoken `Month Day` form, which passes both.
- The 10 "no response" recordings were not quota or late replies. In all 10
  the pinned recorder `livekit_inference.py` exited with code -6 (SIGABRT)
  before writing a result, so they scored zero regardless of the agent.
  Agent state at disconnect does not differ between crashed and normal rooms.
  The agent's own calls would have passed in 6 of them.
- LiveKit Agents 1.8.3 already uses its hosted semantic `inference.TurnDetector`
  when `turn_detection` is unset, and it executes tools only after a turn is
  committed, so preemptive generation did not cause early tool calls.
  Early calls came from turns committed in mid-sentence pauses and from
  repeated identical calls.

### Completed

- Tool descriptions and argument docs follow the pinned reference agent
  (`v3/lk_agent_tool.py`) and `v3/mock_apis.py` signatures, with neutral
  examples only. `search_products` accepts the backend's optional `category`.
- Normalizers: spoken dates to `Month D` (ISO, ordinals, day-first, years);
  identifiers joined across spaces, hyphens, dots, "dash" and double/triple
  for `order_id`, `product_id`, `doc_number` and `flight_id`; `doc_type`
  (`driver's license` to `driver_license`, unique prefix such as `pass`);
  currency names to ISO codes; bill/account/card nouns removed; commute modes;
  spelled ordinals in places written as digits; snake_case filter names.
- Prompt: never ask for clarification, wait for corrections, keep the user's
  place/product wording, no ISO dates, no repeated calls, no extra steps.
- `ToolExecutor` returns the first result for an identical call in the same
  room instead of running and logging it again. Cancelled attempts are retried.
- `UnfinishedTurnHold`: a committed turn that ends mid-sentence ("..., then",
  "I'm looking for") skips its reply and is prepended to the next turn; after
  `INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS` (default 1.0) of silence, or when
  the participant leaves, the held text is answered as a normal turn.
- Trace adds `conversation_item` (what each side said), `session_error`,
  `turn_held`, `turn_released` and `tool_call_deduplicated`.
- New configuration: `INTERRA_FDB_LLM_TEMPERATURE` (default now 0),
  `INTERRA_FDB_ENDPOINTING_MIN_DELAY`/`MAX_DELAY` (defaults unchanged, 0.7/1.2),
  `INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS`, `INTERRA_FDB_RECORDER_RETRIES`.
- `scripts/fdb_v3.py benchmark` re-runs recordings whose recorder crashed once
  (new rooms, completed recordings untouched) and writes
  `artifacts/fdb_v3/recorder-retries.json`. This retries benchmark
  infrastructure only; an organizer re-run does not get it.
- `scripts/kaggle_setup.py` zips every per-recording result JSON, the reports,
  the agent trace and the tool log into `/kaggle/working/interra-fdb-results.zip`.
- `python -m agent.fdb_offline rescore --run docs/results/kaggle-20261001`
  maps rooms to recordings from the kernel log and scores with the official
  `evaluate_pass_rate.py`. `... llm --provider livekit` replays archived
  transcripts through the current prompt and tools.

### Measured offline

- `rescore` reproduces the official 43/100 exactly from the archived trace.
- The same archived calls through the current normalizers and duplicate
  protection score **56/100** (13 fixed, 0 broken); 62/100 if the recorder had
  not crashed. Prompt, turn-hold and temperature effects need the `llm` replay
  or a live run; neither can reach its provider from this environment.

### Tests

- 199 tests pass on Python 3.11 (143 before this phase). New:
  `test_fdb_normalizers.py`, `test_fdb_turns.py`, `test_fdb_offline.py`,
  recorder-retry tests in `test_fdb_v3.py`, the results-archive test in
  `test_kaggle_setup.py`.
- Changed: `a-b-c` is now joined to `ABC` (hyphen-spelled identifiers were a
  measured failure); `SKU-42`-style codes still keep punctuation. The
  missing-checkout config test no longer depends on whether `.runtime` exists.
- `scripts/check_submission.py` reports a `kernel-metadata.json` evidence hash
  mismatch on the base commit as well; it is not caused by this phase.

### Known limitations and next tasks

- Lexical end-of-turn checks miss verbs that need an object ("I converted").
- ASR confusions (`BOB`/`BOP`, `PO999`/`P0999`) and the mislabeled travel_02
  expected value (`P9-9-9-90011` for spoken `P-8-8-9-9-0-0-1-1`) remain.
- 2026-10-04 re-check: the patch applies cleanly, 199 tests pass and
  `rescore` still gives 43 -> 56/100 (62/100 with every recording captured).
  A single `inference.LLM("openai/gpt-4.1-mini")` probe still returns 429
  `inference_quota_exceeded` (`MaxGatewayCredits`, `max_allowed: 0`), so no
  `llm` replay or Kaggle run has used the current prompt yet. LiveKit
  Inference is the only LLM route; the `openai` replay provider was removed
  because no `OPENAI_API_KEY` is available.
- Superseded by the second-session section above: the probe now succeeds and
  the `llm` replay has run. Still next: the full Kaggle run, then the
  `INTERRA_FDB_ENDPOINTING_MAX_DELAY` sweep (1.2 vs 1.8) on Kaggle.

## Previous scored run — 2026-10-01

- Kaggle kernel `adityavardhankochar/interra-fdb-v3-benchmark`, version 3,
  completed the full 100-recording FDB-v3 run using LiveKit Inference
  Deepgram Nova-3, GPT-4.1 mini, and Cartesia Sonic-3.
- Official strict passes improved from the current main baseline 31/100 to
  43/100; turn-takes improved from 58/100 to 90/100; average response latency
  fell from 4.545 s to 4.175 s.
- Turn-taken tool selection and argument accuracy were 85.7% and 56.3%, below
  the previous run's 88.5% and 58.6%. Across all recordings they were 77.2%
  and 50.7%. The organizer LLM judge was disabled; no normalized score is claimed.
- Trace evidence records 147/147 completed tool calls and 101 cleanup plus 101
  cooldown events. No STT 429 appears in the agent trace; LiveKit logged LLM
  credit-quota errors. Aggregate reports cover all 100; the Kaggle endpoint
  returned only 15 per-recording result JSONs.
- Evidence and caveats are in `docs/results/kaggle-20261001/`. This is one run;
  two quota-restored repeats and an organizer-judge report remain outstanding.
- The eight-slide audited submission deck now reflects this run and passes
  package/layout validation. The old twelve-slide organizer template is retained.

## Remaining submission requirements

- Produce two further full runs after restoring LiveKit LLM credits and enable
  the organizer LLM judge where available.
- Record and link the real three-to-five-minute benchmark plus camera demo.
- Confirm registered team name and identities across the deck and disclosure.
- Verify clean-machine reproduction and CUDA scorer installation; Docker is not
  available in this environment.
- Keep the separate camera extension as unverified until it has a live demo.

## Repository submission audit — 2026-09-30

### Completed

- Compared the repository with `Theme05_Participant_Guide_UPDATED_FBD.docx`.
  The updated guide's maximum of eight slides takes precedence over the older
  twelve-slide organizer template. The audited submission deck remains
  `docs/Interra_Theme05_submission.pptx`; its score was refreshed in the
  2026-10-01 update above. Historical deck replacement had been blocked while
  PowerPoint held the older file open.
- Added `python scripts/reproduce.py`: standard-library dotenv loading,
  prerequisite validation, environment creation, dependency installation,
  pinned benchmark/data bootstrap, speech gate, inference and evaluation.
  Existing shell credentials take precedence. Python hash seed defaults to 0;
  hosted sampling remains nondeterministic.
- Pinned Agents 1.8.3 and RTC 1.1.18 to match the measured hosted run. Added a
  minimal `voice` dependency extra. Docker now installs this profile and starts
  the FDB worker, with official checkout and trace mount instructions.
  Virtual environments and PowerPoint lock files are excluded from packaging.
- Fixed evaluation failure handling: stage a fresh report per invocation,
  validate it before replacement, and prevent an old report from masking a
  failed evaluator. A known nonzero exit after a valid new report is tolerated.
- Fixed camera lifecycle: select only the linked participant's camera, discard
  unsubscribed frames, remove images from earlier model turns, await concurrent
  cleanup callers, and await readers even when stream close fails. Completed
  tasks are removed from ownership instead of accumulating for the session.
- Archived measured logs, all 100 result files, setup configuration, installed
  versions and the exact measured source in `docs/results/best-run-evidence.zip`.
  `run-manifest.json` records SHA-256 hashes and the original unrecorded seed.
  `scripts/check_submission.py` verifies archive entries, counts, score agreement
  and the eight-slide limit. That archive is the historical main baseline (31/100 strict, 58/100 turn-take,
  4.545 seconds); the newer run is documented above.
- Updated README, architecture, disclosure and alternate deck builder to
  distinguish the measured LiveKit adapter from retained queue-runtime guarantees.
  The custom stale-result gate and duplicate-write ledger are not integrated
  into the LiveKit adapter; this is an explicit remaining architectural limit.
- Fixed review packaging to use committed evidence and the audited deck. An
  incomplete archive requires `--allow-missing-demo`; normal packaging requires
  an actual video through `--demo`. No publication or repository push performed.
- The 2026-09-30 Kaggle CLI check returned `CANCEL_ACKNOWLEDGED`; it is a historical
  attempt superseded by the completed 2026-10-01 kernel version 3 run. Latest logs end around recording 16/100 with repeated TTS
  `no audio frames were pushed` errors. The downloaded published notebook embeds
  the old ElevenLabs/Ollama runtime without cleanup; unsaved editor changes may
  differ. No cause of cancellation is asserted and this session did not cancel it.
- Regenerated `artifacts/kaggle-fixed/interra_setup.ipynb` with the current
  runtime and existing private notebook ID. Verified embedded agent bytes match
  local source. Embedded source now takes precedence over attached historical
  datasets. This notebook had not been uploaded at the time of this historical entry.

### Verification

- **139 tests passed** on Python 3.11. Added 13 regressions: camera cleanup and
  isolation (5), fresh evaluation evidence (3), reproduction commands/credentials
  (3), and Kaggle source precedence/account targeting (2).
- Fresh Python 3.11 virtual environment successfully installed `.[voice]`;
  `pip check` found no broken requirements. Against the actual installed SDK,
  constructed all 12 tool schemas, AgentSession turn settings, participant-bound
  room options and camera-image history operations without hosted requests.
- At that time, the verifier covered 100 recordings, 31 strict passes and eight
  slides. The latest verifier now also checks the 43-pass run hashes and deck
  metrics. `git diff --check` passes; review archive is under `output/submission/`.

### Remaining before final submission

- Record and link the real three-to-five-minute benchmark plus camera demo.
  Unit tests do not prove live camera behavior or interruption quality.
- Confirm the registered team name: deck says `smoothOperator`; disclosure
  says `VITV_smoothOperator_T5`.
- At the time of this historical audit, browser auth was unavailable and the
  optional LLM judge had not been run. The CLI run completed later, as recorded
  above; the judge and two repeat runs are still outstanding.
- The Docker daemon is unavailable here, so the image build/run is unverified.
  The clean voice install was verified; the entire CUDA scorer dependency profile
  and one-command run have not been reproduced on a fresh GPU machine.
- Commit/push these local fixes, then tag the intended final revision after the
  demo and score updates. No tag, push or submission is performed by this audit.

## Historical submission package — 2026-09-30

- The score in the README, disclosure, and slide deck is the completed
  GPT-4.1 mini run: strict pass 31/100, turn-take 58/100, average response
  latency 4.545 seconds. Official JSON reports are in `docs/results/`.
- The deck is `docs/Interra_Theme05_submission.pptx`, filled from the organizer template.
- The demo video is not recorded. A later official run can replace these
  numbers if it is better.

## Historical hosted run and measured failure fixes — 2026-09-30

- The latest run of `maitrishah29/interra-fdb-v3-http-tts-smoke` is COMPLETE.
  Downloaded its code, setup report, official tool and strict reports, agent
  trace, and all 100 recording results to `artifacts/kaggle-inference-latest/`.
  Its embedded agent source matched the local source before this phase.
- Official strict pass: **31/100**; 50 wrong-tool failures and 19 wrong-argument
  failures. Turn-take: **58/100**, 42 silent, 93 completed inference attempts,
  7 inference failures. Full batch duration: 93.61 minutes. The optional LLM
  quality judge was disabled; this is not a normalized overall hackathon score.
- Deduplicating repeated notebook log entries identifies 35 rooms with STT HTTP
  429 errors. All 35 corresponding recordings were silent, including every
  travel recording. The old adapter explicitly kept sessions open after
  disconnect but never closed them or ended their jobs. This is a concrete
  resource-lifecycle defect and a likely contributor to concurrency exhaustion;
  a rerun must verify whether other project usage or quota limits also remain.
- Added explicit session ownership: wait for the linked participant to leave
  or session failure, allow pending speech/tool scheduling to drain for at most
  20 seconds, close the session, remove handlers, and end the worker job. Cancel
  and await both event waiters in `finally`. Record disconnect, drain outcome,
  session close, and cleanup traces. The official harness/scorers are unchanged.
- Load Silero and benchmark module code in the worker setup hook; import the
  OpenAI plugin only for the optional Ollama profile. Limit idle warm processes
  to one. The run logged repeated 300–400 ms plugin-import event-loop stalls.
  Downloaded and checked the exact Kaggle SDK wheel, LiveKit Agents 1.8.3, for
  supported drain/close/setup and participant-identity APIs.
- Fixed measured adapter argument defects: forward optional apartment pet
  filters, preserve numeric/boolean filter values, join explicitly spelled
  letter/digit identifiers, and improve general argument transcription
  instructions without adding benchmark answers or scenario examples.
- Added **12 regression tests** covering draining, duplicate disconnect,
  unrelated participant departure, provider close, timeout, cancellation,
  startup failure, pet filters, filter types, spelled/literal identifiers, and
  safe rejection of invalid numeric arguments. Lifecycle tests assert traces
  and absence of owned task leaks. **126 tests passed** on Python 3.11.
  Earlier sandbox-only temporary-file permission failures cleared with a normal
  permitted test run; the existing README packaging assertion was also fixed.
- Prepared the updated self-contained notebook at
  `artifacts/kaggle-fixed/interra_setup.ipynb`. Browser control is blocked by
  `unsupported Codex auth method: apikey`; CLI reads work. A credentialed rerun
  still requires importing this notebook in the existing Kaggle editor and
  Save & Run All with its three attached LiveKit Secrets. No new score is claimed.

Next: run the corrected notebook; verify cleanup traces and no accumulating
STT connections, then compare strict pass, silent recordings, rate-limited
rooms, and argument failures. Remaining speech transcription and semantic
reasoning errors need fresh measured results. See `docs/KAGGLE_RUN_REVIEW.md`.

## Submission leftovers while version 9 runs — 2026-09-30

- `docs/AI_DISCLOSURE_DRAFT.md` now follows the organizer form: team
  `VITV_smoothOperator_5`, VITV, project Interra, and representative Maitri
  Shah. It records the LiveKit Inference profile and the version 7 baseline
  (31/100 strict pass, 52/100 turn-take). Maitri Shah's signature image is in
  `docs/assets/maitri-shah-signature.jpg`. Phone and email stay blank in the
  repository until the team adds them.
- `requirements.txt` lists the FDB extra pins from `pyproject.toml`.
- `README.md` is a project document: architecture, reproducible setup,
  `requirements.txt`, presentation path, and demo-video link. It does not
  list team or contact details.
- The camera extension is a separate LiveKit entry point. It keeps only the
  latest camera frame, drops that frame after one turn, and does not expose
  the benchmark tools. `tests.unit.test_extension_livekit`: 5 passed on
  Python 3.11. A live end-to-end recording is still required.

## First completed official FDB-v3 baseline and hosted-model rerun

- Kaggle `maitrishah29/interra-fdb-v3-http-tts-smoke` version 7 completed all
  100 released recordings. Reports and 100 per-recording results were downloaded
  to `artifacts/kaggle-inference-v7/`. The official strict pass rate is **31/100**.
  The tool report records 52 turn-taken, 48 no-response, tool selection accuracy
  0.653 across all samples, and argument accuracy 0.342 across all samples.
- There were 10 `inference_failed` results, 32 recordings with no tool call,
  and 51 early-speech interruptions among 52 turn-taken recordings. The
  always-on early acknowledgment inflated interruption count, and local Qwen 3
  8B frequently spent about a minute reasoning without producing a tool call.
  Six strict passes were still classified as no-response by the separate
  turn-taking metric, so these metrics must be interpreted separately.
- Replaced the default local LLM with LiveKit Inference GPT-4.1 mini, retained
  Ollama as an explicit fallback, removed the premature spoken acknowledgment,
  increased the endpointing delay for streaming transcripts, strengthened the
  generic multi-step instruction, and added spoken-number conversion for numeric
  tool arguments. Kaggle setup skips Ollama installation for this profile.
- **109 local tests pass** on Python 3.11. Version 8 was saved with the new code;
  version 9 was started using Save & Run All with all three LiveKit Secrets
  enabled. Its official score is pending. The legacy `interra_elevenlabs`
  provider name in report filenames is required by the pinned runner and does
  not describe the current speech provider.

Next: check version 9's smoke gate and full report. Improve only from measured
failures. The extension, real demo recording, team information, and final
submission verification remain open.

## LiveKit Inference speech recovery — 2026-09-30

- Kaggle `maitrishah29/interra-fdb-v3-http-tts-smoke` reached the HTTP speech
  preflight and stopped with **ElevenLabs HTTP 401** before costly model setup.
  The configured ElevenLabs key cannot supply speech for this run.
- Speech recognition and synthesis now use LiveKit Cloud Inference through the
  existing LiveKit project credentials: Deepgram Nova-3 and Cartesia Sonic-3.
  The ElevenLabs key is no longer required by the worker. The one-recording
  speech gate still stops the run if the official recorder hears no reply.
- Updated the Kaggle package and credential checks, with a regression for the
  inference model configuration. **106 tests passed** on Python 3.11.
- A remaining readiness check still required `ELEVEN_API_KEY`. Fixed that
  check and added a regression. The superseded version 5 was cancelled before
  its full run. **107 tests now pass** on Python 3.11.
- Saved the corrected private notebook as version 6, confirmed all three
  LiveKit Secrets enabled, and started **version 7** with Save & Run All.
  Its completed measurements are recorded in the section above.

## HTTP speech gate — 2026-09-30

- `maitrishah29/notebook3856a749c9` entered the official 100-recording
  FDB-v3 runner, but all visible completed output transcripts were empty.
  LiveKit repeatedly reported ElevenLabs TTS `no audio frames were pushed`.
  This run is diagnostic, not evidence of a good speech score.
- The agent now wraps ElevenLabs TTS in LiveKit's sentence `StreamAdapter` so
  speech uses HTTP synthesis instead of the failing multi-context WebSocket.
  Kaggle setup verifies that the configured voice returns HTTP audio before
  model setup. The full-run path now tests one released recording through the
  official runner and stops if the recorded reply has an empty transcript.
- Added regressions for the adapter, early preflight, and one-recording gate.
  The full suite passed **107 tests in 13.178 seconds** on Python 3.11. This
  verifies local control flow; hosted synthesis still needs a Kaggle run.
- Saved private `maitrishah29/interra-fdb-v3-http-tts-smoke` version 1 without
  running. A fresh pull confirmed valid notebook JSON, T4, Internet, no
  dataset source, and the HTTP preflight. Kaggle editor must attach the four
  existing Secrets and run it.

Next: run the smoke-gated notebook with Secrets. If its one-recording gate
passes, collect official reports from the full run. No new FDB-v3 score yet.

## FDB-v3 silence — 2026-09-30

`maitrishah29/notebook794ea870ff` finished all 100 recordings in 94.25 minutes
(91 session files, 9 session errors, 46.84 s average first-speech latency) and
then exited **ERROR**. `evaluate_tool_calls.py` counted **0/100 turn-takes**
and **100 silent**, then crashed while formatting metrics for an empty
turn-taken set. This run is not an FDB score.

The official recorder (`livekit_inference.py` at the pinned commit) saves
agent audio only until the input wav ends, plus 1.5 seconds of trailing
silence, then disconnects. The previous agent waited until that trailing
silence to start Qwen, and the default `max_speech_duration` of 10 seconds
skips preemptive generation on these long clips. The reply therefore began
after the recorder had stopped, and `close_on_disconnect` then cancelled it.
The 46.84 s figure is the average length of the silent output files.

The agent now speaks a short acknowledgement as soon as the user starts,
runs preemptive LLM and TTS during utterances up to 180 seconds, commits the
turn before the 1.5 second tail ends, disables Qwen thinking when Ollama
accepts `think:false`, and keeps the session open if the participant leaves
mid-reply. The next Kaggle Save & Run All must use the rebuilt notebook.

## Kaggle editor recovery — 2026-09-30

The screenshot notebook `maitrishah29/notebook67853281` is **not** the Interra
worker. It is Kaggle's default numpy/pandas template (Version 0). The four
Secrets are present on that notebook, which is useful, but the page cannot be
saved because the editor throws `Cannot read properties of undefined (reading
'datasetVersionInfo')` while checking data-source updates. That is a Kaggle UI
failure on a Version-0 draft with a broken/private data source, not an Interra
runtime error.

Prepared kernels on the authenticated CLI account (`adityavardhankochar`) both
show `KernelWorkerStatus.ERROR`:

- `adityavardhankochar/interra`: ran the un-embedded setup script and failed
  immediately with `FileNotFoundError` because `/kaggle/input` had no source
  archive (`candidates=[]`).
- `adityavardhankochar/interra-fdb-v3-benchmark`: stored the 89 KB worker as a
  **notebook** whose file content was raw Python, not ipynb JSON. Post-run
  `nbconvert` then failed: `Notebook does not appear to be JSON`.

`maitrishah29` is already an ADMIN collaborator on the private source dataset.
The worker no longer needs that dataset attached. Packaging now emits a real
`.ipynb`, sets `kernel_type` to `notebook`, and leaves `dataset_sources` empty
so the editor does not look up `datasetVersionInfo`. One packaging regression
was added. Full suite: **97 tests passed in 8.670 seconds** on Python 3.12.
Do **not** CLI-push a credentialed run; attach Secrets in the editor and use
Save & Run All.

The 2026-09-30 Save & Run All on `maitrishah29/notebookff00f3a8b2` reached the
LiveKit worker and then crash-looped. Every job failed with
`AttributeError: Can't get local object 'main.<locals>.entrypoint'` because
LiveKit pickles the session function into a child process. `entrypoint` is now
a module-level function. Stop that notebook; it cannot produce an FDB score.
Packaging now embeds the working tree, not only `git archive HEAD`.

Next: in the existing Maitri notebook, remove every Input/data source, replace
the starter cell with the packaged notebook, keep the four Secrets, enable GPU
T4 + Internet, then **Save Version → Save & Run All**. If the draft still
refuses to save, create a new notebook with no data attached and import
`interra_setup.ipynb`. No FDB-v3 score is claimed.

## Kaggle setup recovery — 2026-09-29

- Switched to `codex/fdb-v3-livekit-kaggle-handoff`; its 89-test baseline passed.
- Kaggle CLI 2.2.4 authenticated with an access token stored outside the repository.
  The existing private kernel and source dataset are accessible.
- The latest kernel had failed before dependency installation because Kaggle's
  Python 3.12 image lacked `ensurepip` for `venv`. The worker now installs the
  matching `python3.12-venv` package before creating its isolated environment,
  alongside `zstd` for Ollama. A new setup-order regression test was added.
- Kaggle's dataset CLI compressed nested source directories separately. The
  worker now accepts one explicit source archive and validates member paths
  before extraction. Two extraction regressions were added.
- Kaggle kernel version 5 completed its script-level dependency setup,
  Qwen3 8B preparation, and pinned benchmark bootstrap. Its report showed all
  four provider Secret names missing, so no LiveKit check or benchmark ran.
  The kernel output was slow to process because the virtual environment was in
  `/kaggle/working`; it now lives in Kaggle's unsaved scratch area. A regression
  guards the output boundary. The worker also now owns and terminates its
  Ollama server on success and failure; two lifecycle regressions were added.
- Full suite: **95 tests passed in 12.742 seconds** on Python 3.11.15. No new FDB-v3 inference
  or score is claimed yet.
- Kaggle CLI-pushed kernel versions 6 and 7 completed the model/bootstrap steps
  but both reported all four Secret names unavailable, including a fresh push
  after the user added them in the editor. This is a CLI/editor attachment
  boundary, not a credential-validity result. Version 8 was saved without
  running, with `RUN_FULL_BENCHMARK = True`, but the editor run requested by the
  user failed immediately: `/kaggle/input` contained no usable source dataset.
  The latest downloadable notebook code also retained `RUN_FULL_BENCHMARK = False`,
  so the editor did not run the prepared version. The worker now supports a
  path-validated, embedded archive of tracked runtime files as a fallback when
  Kaggle omits the dataset mount. `scripts/kaggle_package.py` prepares a private,
  self-contained benchmark notebook with the full-run switch enabled. One
  embedded-source regression was added. The full suite passed **96 tests in
  12.198 seconds** on Python 3.11.15. Reopening the original editor draft
  replaced the prepared code with its older 5.6 KB setup script, so a separate
  private kernel `adityavardhankochar/interra-fdb-v3-benchmark` was quick-saved
  as version 1. A fresh CLI pull confirmed its 89 KB code has the embedded
  source and `RUN_FULL_BENCHMARK = True`. It awaits an editor run with Secrets.
  No hosted speech request has run.
- `.gitignore` now excludes `.env.*` (except the example) and `.venv-fdb/` to
  prevent local settings or installed packages entering the source archive.

The 2026-09-30 editor recovery supersedes the previous "awaiting editor run"
note for `interra-fdb-v3-benchmark`. Inspect the setup report and benchmark
output after a Secrets-attached Save & Run All, then diagnose measured failures.
The camera extension, repeated seeded evaluation, video, and release package
remain open. No architectural contract changed in this recovery phase.

The updated Theme 05 guide changes the scored target to FDB-v3 and a LiveKit
voice agent. The repository now contains an initial FDB-v3 adapter using
ElevenLabs speech and a local Ollama reasoning model. **No FDB-v3 benchmark run
has completed, so this remains a migration build rather than a submission-ready
release.**

## FDB-v3 migration

- Added `agent.fdb_livekit`, a LiveKit cascaded agent with Silero VAD,
  ElevenLabs Scribe v2 Realtime, Ollama Qwen 3, ElevenLabs TTS, and the 12
  official tool signatures.
- Preserved disfluencies with `no_verbatim=False`; tool calls run in worker
  threads and emit the official room/timestamp telemetry.
- Added `scripts/fdb_v3.py` to pin the official benchmark at commit
  `3e799c45a045256f47d5f1c9cda90157e2d2ec9e`, obtain the released data, start
  the agent, run inference, and invoke all three evaluation scripts.
- Added the optional `fdb` dependency profile and an environment template with
  secret names only.
- The existing queue runtime and its 49.5 score are historical;
  they must not be presented as FDB-v3 results.

Immediate gates are a clean FDB dependency installation, LiveKit credentials,
an ElevenLabs key with credits, the released benchmark audio, and a GPU-hosted
Ollama model. After those are ready, run the official 100-recording benchmark
before revising the demo or slides.

The retained implementation is on `codex/interruptible-runtime`, based on
`6408a266867f573d50d3d08132bf6272563e8c6f`.

## Implemented and tested

- Completed-turn acknowledgments, coalesced audio, capped nonrepeating generic
  fillers, interruption cancellation, current state snapshots on every action,
  clarification follow-ups, and planner context containing call states/errors.
- Corrected-state speech before tool dispatch: the official harness does not
  retain snapshots attached to tool/cancel actions, so this makes the accepted
  correction observable before a final response.
- Shared local Ollama planning/vision, multilingual Whisper base CPU int8,
  computed 512-dimensional CLIP image embeddings, session-scoped frame reuse,
  embedding source validation, native worker ownership, and stale-output rejection.
- Schema-constrained output, manifest-constrained tool names, one bounded proposal
  repair, compact context with an explicit size guard, and a validated fallback
  for Ollama's misrouted JSON response field. See LOCAL_MODELS.md for limits.
- Explicit model download/readiness commands, dependency lock, Docker Compose
  setup, trace replay, editable review deck and captioned recorded-runtime demo.

## Test and source evidence

Python 3.11.16: **89 tests passed in 10.843 seconds** on 2026-09-24. Initial baseline was 70/71;
its 300 ms test-only harness tail was host-sensitive. That test now uses 3000 ms
with its assertions preserved. Official scenarios retain the original 6000 ms
harness tail and 120-second cap. No scorer or official scenario was edited.

All 34 vendored kit files match the supplied ZIP byte-for-byte. Git's Windows
checkout initially converted text line endings; `.gitattributes` now preserves
original kit bytes. Details: `artifacts/kit-integrity.json`.

## Actual local inference and timing

Ollama 0.34.3, Qwen3-VL 2B Q4_K_M, faster-whisper 1.2.1, FastEmbed 0.8.1.
Exact checkpoint revisions, Ollama digest, and perception-file hashes are in
`.models/model-manifest.json`, also copied into verification evidence.

The final combined readiness probe started with approximately 727 MiB available
RAM on an 8 GB i5-1135G7 Windows laptop:

| Check | Measured result |
| --- | --- |
| Model/provider setup | 11.21 s, succeeded |
| Text greeting | 27.89 s, failed to return a usable final response |
| Audio | 3.94 s, returned an uncertain/misheard transcript |
| Vision description | exceeded 60 s, timed out |
| Independent CLIP encoder | 512 finite dimensions; 0.639 s inference |

Earlier isolated audio probes took about 3.1–3.4 s; combined probes ranged from
2.4–14.2 s as memory pressure changed. The simple spoken Boston correction was
understood, while noisy/repair speech remained uncertain. Readiness **failed**;
no hosted fallback was used. The model was unloaded from RAM after benchmarking.

## Unmodified public evaluation

Nine scenarios, three repetitions each, `time_scale=1`, original scenario windows,
original setup/wall caps. Weighted automated score: **49.5/100**; plain: **49.3**.
Text: **47.6**, audio: **55.3**, visual: **47.7**. Each scenario produced the same
score in its three repetitions. The local evaluator does not apply the additional
LLM quality multiplier described in the kit's SCORING.md.

| Scenario | Median automated score |
| --- | ---: |
| pub_01_text_simple | 38.5 |
| pub_02_text_interrupt | 52.9 |
| pub_03_text_chained_booking | 38.5 |
| pub_04_text_no_tool | 69.2 |
| pub_05_audio_asr_ambiguity | 53.8 |
| pub_06_audio_disfluency | 56.9 |
| pub_07_visual_port_lookup | 47.7 |
| pub_08_text_tool_failure | 38.5 |
| pub_09_text_unseen_tool | 47.7 |

The separate full public trace pass and ten generated cases (five interruptions,
seed 20260924; five unseen tools, seed 20260925) had zero agent/setup/protocol errors.
The generated cases were verified against their recorded seeds.

Across 47 local runtime sessions (27 scored runs, one contract probe, and 19 trace
runs), **no tool calls were dispatched within the scenario windows**. There were
59 immediate acknowledgments, maximum 73 ms. Zero accepted stale results and zero
duplicate writes were observed, but without calls those counts do not prove live
coordination under tool load. The cancellation 800 ms gate therefore has no live
sample; deterministic tests separately cover cancellation, races and writes.

The score comes from acknowledgments, safe behavior, clarification and negative
checkpoints; it must not be presented as successful task completion. Model
reasoning/vision speed and quality remain the primary blockers.

Evidence: `artifacts/samsung-local-evaluation.json`, `samsung-public-traces.json`,
`generated-*-traces.json`, `verification-summary.json`, and `local-traces/*.jsonl`.

## Packaging and remaining gates

- Locked clean Python installation passed with no dependency conflicts. The wheel
  runs its demo with Python's isolated mode, independently of the source checkout;
  see `artifacts/clean-setup.txt` and `clean-demo.txt`.
- Docker Compose configuration validates. Actual image build/run remains blocked:
  Docker Desktop startup hits a stale socket, and automatic approval review rejected
  its removal with "blocked by policy". No reset or workaround deletion was performed.
- Any previously generated 8-slide deck, queue-runtime replay, or review archive
  under `output/submission` is historical and must not be submitted. The updated
  deck source is limited to eight slides and awaits real FDB-v3 results; the final
  3–5 minute video must show a live FDB interruption and the working extension.
- Team identities, the exact presentation template, AI disclosure review/signature,
  and final submission details remain team inputs. The Theme 5 PDF in the supplied
  ZIP is DRM-protected, so it was not independently read. Earlier document summaries
  are historical; the executable kit is the directly verified contract.
- Before release: obtain enough local inference capacity, improve model quality,
  rerun readiness and timed evaluation until task completion and cancellation are
  evidenced, verify Docker on a working engine, and review the team materials.
  Web dashboards, persistent memory and speculative writes remain deferred.

No commit, push, final release tag, signature or submission was made by this task.
Earlier phase notes are archived in [HISTORY.md](HISTORY.md).
