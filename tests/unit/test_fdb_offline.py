"""Offline FDB-v3 loops: log parsing, turn rebuilding, re-scoring and LLM replay."""
import asyncio
import json
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

from livekit.agents import llm

from agent.fdb_offline import (
    build_outage_seed,
    load_recordings,
    outage_rooms,
    renormalize_calls,
    replay_llm,
    rescore,
    room_map_from_kernel_logs,
    run_conversation,
    user_turns,
)

LOG = textwrap.dedent("""\
    [1/1] Processing Speaker=aaaa1111... Example=shop_01...
      🚀 Running LiveKit inference with provider=interra_elevenlabs...
      🔗 Streaming via livekit_inference.py into room: eval-00000001
      ✅ livekit_inference.py finished successfully.
    [1/2] Processing Speaker=aaaa1111... Example=shop_01...
      🚀 Running LiveKit inference with provider=interra_elevenlabs...
      🔗 Streaming via livekit_inference.py into room: eval-00000002
      ✅ livekit_inference.py finished successfully.
    [2/2] Processing Speaker=bbbb2222... Example=trip_01...
      🚀 Running LiveKit inference with provider=interra_elevenlabs...
      🔗 Streaming via livekit_inference.py into room: eval-00000003
      ❌ livekit_inference.py failed with exit code -6
""")

MOCK_APIS = textwrap.dedent("""\
    class MockAPIRegistry:
        def call(self, name, **kwargs):
            return {"status": "success", "flights": [{"flight_id": "FL9"}], **kwargs}
""")

# Minimal stand-in with the official evaluate_scenario_pass contract.
SCORER = textwrap.dedent("""\
    def evaluate_scenario_pass(scenario, actual_calls, use_llm=False):
        expected = scenario["expected_tool_calls"]
        names = sorted(c["function"] for c in actual_calls)
        if names != sorted(c["function"] for c in expected):
            return {"passed": False, "failure_reason": "tools"}
        for want in expected:
            got = next(c for c in actual_calls if c["function"] == want["function"])
            for key, value in want["args"].items():
                if str(got["args"].get(key)).lower() != str(value).lower():
                    return {"passed": False, "failure_reason": "args"}
        return {"passed": True, "failure_reason": ""}
""")

SCENARIOS = {"scenarios": [
    {"id": "shop_01", "expected_tool_calls": [
        {"function": "track_order", "args": {"order_id": "ZQ7"}}]},
    {"id": "trip_01", "expected_tool_calls": [
        {"function": "search_flights", "args": {"destination": "Example City", "date": "October 14"}}]},
]}


def event(room, kind, time, **payload):
    return {"room": room, "kind": kind, "time": time, **payload}


def tool_event(room, time, function, args):
    return event(room, "tool_call", time, status="completed",
                 call={"function": function, "args": args})


class RoomMapTests(unittest.TestCase):
    def test_batch_filter_drops_smoke_room_and_marks_recorder_crash(self):
        rooms = room_map_from_kernel_logs(LOG, batch_size=2)
        self.assertEqual(sorted(rooms), ["eval-00000002", "eval-00000003"])
        self.assertEqual(rooms["eval-00000002"].scenario_id, "shop_01")
        self.assertTrue(rooms["eval-00000002"].recorder_ok)
        self.assertFalse(rooms["eval-00000003"].recorder_ok)
        self.assertEqual(len(room_map_from_kernel_logs([{"data": LOG}])), 3)

    def test_retried_recording_keeps_only_its_last_attempt(self):
        retried = LOG + textwrap.dedent("""\
            [2/2] Processing Speaker=bbbb2222... Example=trip_01...
              🚀 Running LiveKit inference with provider=interra_elevenlabs...
              🔗 Streaming via livekit_inference.py into room: eval-00000004
              ✅ livekit_inference.py finished successfully.
        """)
        rooms = room_map_from_kernel_logs(retried, batch_size=2)
        self.assertEqual(sorted(rooms), ["eval-00000002", "eval-00000004"])
        self.assertTrue(rooms["eval-00000004"].recorder_ok)


QUOTA_ERROR = "Error code: 429 - {'type': 'inference_quota_exceeded', 'category': 'MaxGatewayCredits'}"


class OutageSeedTests(unittest.TestCase):
    def write_run(self, root: Path) -> Path:
        run = root / "run"
        run.mkdir()
        trace = [
            event("eval-a", "session_started", 10.0),
            event("eval-a", "session_error", 11.0, source="STT", error="429 Too Many Requests"),
            event("eval-b", "session_started", 20.0),
            event("eval-c", "session_started", 30.0),
            event("eval-c", "session_error", 31.0, source="LLM", error=QUOTA_ERROR),
            event("eval-d", "session_started", 40.0),
        ]
        (run / "livekit-agent.jsonl").write_text("\n".join(json.dumps(item) for item in trace) + "\n")
        with zipfile.ZipFile(run / "interra-fdb-results.zip", "w") as bundle:
            for folder, room in [("shop_01_a", "eval-a"), ("shop_02_b", "eval-b"),
                                 ("trip_01_c", "eval-c"), ("trip_02_d", "eval-d")]:
                bundle.writestr(f"per-recording/{folder}/result_interra_elevenlabs.json",
                                json.dumps({"room_name": room, "status": "completed"}))
            bundle.writestr("reports/agent_tool_calls.log", "")
        return run

    def test_cut_starts_at_first_quota_error_by_start_time(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.write_run(Path(directory))
            cutoff, rooms = outage_rooms(run / "livekit-agent.jsonl")
        self.assertEqual(cutoff, "eval-c")
        self.assertEqual(rooms, {"eval-c", "eval-d"})  # a plain STT 429 is not an outage

    def test_seed_keeps_results_before_the_outage_only(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.write_run(Path(directory))
            seed = Path(directory) / "seed"
            manifest = build_outage_seed(run, seed)
            kept = sorted(path.parent.name for path in seed.glob("*/result_interra_elevenlabs.json"))
            saved = json.loads((seed / "seed-manifest.json").read_text())
        self.assertEqual(kept, ["shop_01_a", "shop_02_b"])
        self.assertEqual(manifest["rerun"], ["trip_01_c", "trip_02_d"])
        self.assertEqual(saved["cutoff_room"], "eval-c")

    def test_run_without_outage_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "run"
            run.mkdir()
            (run / "livekit-agent.jsonl").write_text(json.dumps(event("eval-a", "session_started", 1.0)) + "\n")
            with self.assertRaises(ValueError):
                build_outage_seed(run, Path(directory) / "seed")


class UserTurnTests(unittest.TestCase):
    def test_long_pause_splits_turns_and_unfinished_text_merges_forward(self):
        events = [
            event("r", "user_state", 0.0, state="speaking"),
            event("r", "transcript", 1.0, transcript="Hi there.", is_final=True),
            event("r", "user_state", 1.1, state="listening"),
            event("r", "user_state", 4.0, state="speaking"),
            event("r", "transcript", 5.0, transcript="Track order z q seven and", is_final=True),
            event("r", "transcript", 5.1, transcript="ignored partial", is_final=False),
            event("r", "user_state", 5.2, state="listening"),
            event("r", "user_state", 7.0, state="speaking"),
            event("r", "transcript", 8.0, transcript="then search for", is_final=True),
            event("r", "user_state", 8.1, state="listening"),
            event("r", "user_state", 8.5, state="speaking"),
            event("r", "transcript", 9.0, transcript="desk lamps.", is_final=True),
        ]
        self.assertEqual(user_turns(events, pause_s=1.2), [
            "Hi there.", "Track order z q seven and then search for desk lamps.",
        ])

    def test_trailing_unfinished_text_is_still_a_turn(self):
        events = [event("r", "transcript", 1.0, transcript="Book it for", is_final=True)]
        self.assertEqual(user_turns(events), ["Book it for"])


class OfflineRunFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.v3 = root / "v3"
        self.v3.mkdir()
        (self.v3 / "mock_apis.py").write_text(MOCK_APIS)
        (self.v3 / "evaluate_pass_rate.py").write_text(SCORER)
        (self.v3 / "benchmark_data_v2.json").write_text(json.dumps(SCENARIOS))
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "kaggle-kernel-logs.json").write_text(json.dumps([{"data": LOG}]))
        trace = [
            event("eval-00000002", "transcript", 1.0, transcript="Track order z q seven.", is_final=True),
            tool_event("eval-00000002", 2.0, "track_order", {"order_id": "z-q-7"}),
            tool_event("eval-00000002", 3.0, "track_order", {"order_id": "ZQ7"}),
            event("eval-00000003", "transcript", 1.0, transcript="Flights to Example City on October 14th.", is_final=True),
            tool_event("eval-00000003", 2.0, "search_flights", {"destination": "Example City", "date": "2026-10-14"}),
        ]
        (self.run_dir / "livekit-agent.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in trace)
        )


class RescoreTests(OfflineRunFixture):
    async def test_rescore_matches_official_view_then_applies_current_wrappers(self):
        report = await rescore(self.run_dir, self.v3, batch_size=2)
        self.assertEqual(report["archived"]["passed"], 0)
        # Hyphen join plus duplicate protection fix the first room.
        self.assertEqual(report["current"]["passed"], 1)
        # The second room's recorder crashed; its calls only count counterfactually.
        self.assertEqual(report["current_if_recorded"]["passed"], 2)
        self.assertEqual(report["recorder_failures"], 1)
        first = report["recordings"][0]
        self.assertEqual(first["current_calls"], [
            {"function": "track_order", "args": {"order_id": "ZQ7"}},
        ])

    async def test_invalid_recorded_arguments_are_skipped(self):
        from agent.fdb_livekit import load_benchmark_module

        registry = load_benchmark_module(self.v3).MockAPIRegistry()
        calls = await renormalize_calls([
            {"function": "search_apartments", "args": {"city": "X", "bedrooms": "many", "max_price": 1}},
            {"function": "unknown_tool", "args": {}},
            {"function": "track_order", "args": {"order_id": "a b 1"}},
        ], registry, "room")
        self.assertEqual(calls, [{"function": "track_order", "args": {"order_id": "AB1"}}])

    async def test_resume_session_replaces_rerecorded_room_and_result_status_wins(self):
        # The crashed trip_01 recording was re-recorded in a resume session whose
        # interleaved log has no outcome line next to the room line.
        resume_log = textwrap.dedent("""\
            [2/2] Processing Speaker=bbbb2222... Example=trip_01...
              🚀 Running LiveKit inference with provider=interra_elevenlabs...
              🔗 Streaming via livekit_inference.py into room: eval-00000009
            {"message": "received job request", "room": "eval-00000009"}
              ✅ livekit_inference.py finished successfully.
        """)
        (self.run_dir / "kaggle-kernel-logs-resume.json").write_text(json.dumps([{"data": resume_log}]))
        (self.run_dir / "livekit-agent-resume.jsonl").write_text(json.dumps(tool_event(
            "eval-00000009", 5.0, "search_flights", {"destination": "Example City", "date": "October 14"}
        )) + "\n")
        with zipfile.ZipFile(self.run_dir / "interra-fdb-results.zip", "w") as bundle:
            for folder, room in [("shop_01_aaaa", "eval-00000002"), ("trip_01_bbbb", "eval-00000009")]:
                bundle.writestr(f"per-recording/{folder}/result_interra_elevenlabs.json",
                                json.dumps({"room_name": room, "status": "completed"}))
        report = await rescore(self.run_dir, self.v3, batch_size=2)
        self.assertEqual([row["room"] for row in report["recordings"]], ["eval-00000002", "eval-00000009"])
        self.assertEqual(report["recorder_failures"], 0)
        self.assertEqual(report["current"]["passed"], 2)

    def test_rooms_without_trace_events_are_kept_as_empty_recordings(self):
        rooms = room_map_from_kernel_logs(LOG, batch_size=2)
        empty = self.run_dir / "empty.jsonl"
        empty.write_text("")
        recordings = load_recordings(empty, rooms)
        self.assertEqual([r.tool_calls for r in recordings], [[], []])


class ScriptedLLM:
    """Returns pre-scripted chunks for each chat() call and records the context it saw."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.contexts = []

    def chat(self, *, chat_ctx, tools=None, **kwargs):
        self.contexts.append(chat_ctx.copy())
        chunks = self.scripts.pop(0)

        class Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for chunk in chunks:
                    yield chunk

        return Stream()

    async def aclose(self):
        pass


def text_chunk(text):
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


def call_chunk(name, arguments, call_id):
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(
        role="assistant",
        tool_calls=[llm.FunctionToolCall(name=name, arguments=json.dumps(arguments), call_id=call_id)],
    ))


class ConversationReplayTests(OfflineRunFixture):
    async def test_tool_loop_runs_wrappers_and_feeds_results_back(self):
        from agent.fdb_livekit import load_benchmark_module

        model = ScriptedLLM([
            [text_chunk("Hello!")],
            [call_chunk("search_flights", {"destination": "Example City", "date": "2026-10-14"}, "1")],
            [call_chunk("book_flight", {"passenger_name": "Example Person", "flight_id": "FL9"}, "2")],
            [text_chunk("Booked.")],
        ])
        registry = load_benchmark_module(self.v3).MockAPIRegistry()
        calls, replies = await run_conversation(
            model, ["Hi.", "Book a flight to Example City on October 14 for Example Person."],
            registry, "room",
        )
        self.assertEqual(calls, [
            {"function": "search_flights", "args": {"destination": "Example City", "date": "October 14"}},
            {"function": "book_flight", "args": {"passenger_name": "Example Person", "flight_id": "FL9"}},
        ])
        self.assertEqual(replies, ["Hello!", "Booked."])
        last = model.contexts[-1]
        self.assertEqual([item.type for item in last.items][-4:], [
            "function_call", "function_call_output", "function_call", "function_call_output",
        ])
        self.assertIn("FL9", last.items[-3].output)

    async def test_replay_scores_each_recording_and_reports_provider_errors(self):
        class FailingLLM(ScriptedLLM):
            def chat(self, **kwargs):
                raise RuntimeError("quota")

        report = await replay_llm(self.run_dir, self.v3, FailingLLM([]), batch_size=2)
        self.assertEqual(report["errors"], 2)
        self.assertEqual(report["current"]["passed"], 0)
        self.assertEqual(report["recordings"][0]["error"], "RuntimeError: quota")

    async def test_replay_filters_scenarios(self):
        model = ScriptedLLM([
            [call_chunk("track_order", {"order_id": "z q 7"}, "1")],
            [text_chunk("On its way.")],
        ])
        report = await replay_llm(self.run_dir, self.v3, model, batch_size=2, only={"shop_01"})
        self.assertEqual(report["current"], {"passed": 1, "total": 1, "pass_rate": 1.0})


if __name__ == "__main__":
    unittest.main()
