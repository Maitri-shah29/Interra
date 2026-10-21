"""Turn holding, duplicate-call protection and diagnostics for the FDB-v3 agent."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agent.commit import RECHECK_INSTRUCTIONS, HeldWords, parse_recheck
from agent.fdb_livekit import (
    CommitGate,
    FdbConfig,
    INSTRUCTIONS,
    ToolExecutor,
    TraceWriter,
    UnfinishedTurnHold,
    apply_turn_hold,
    benchmark_turn_handling,
    llm_recheck,
    looks_unfinished,
    run_session_lifecycle,
    trace_conversation_item,
    trace_session_error,
)


class RecordingRegistry:
    def __init__(self):
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"status": "success", "echo": kwargs}


class ExecutorFixture:
    """A temporary trace and benchmark telemetry log for one test."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = patch("agent.fdb_livekit.tempfile.gettempdir", return_value=folder.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trace = TraceWriter(self.folder / "trace")
        self.registry = RecordingRegistry()

    def telemetry(self):
        path = self.folder / "agent_tool_calls.log"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def trace_records(self):
        if not self.trace.path.exists():
            return []
        return [json.loads(line) for line in self.trace.path.read_text().splitlines()]

    def trace_kinds(self):
        return [record["kind"] for record in self.trace_records()]


class ToolExecutorDeduplicationTests(ExecutorFixture, unittest.IsolatedAsyncioTestCase):
    async def test_identical_call_in_one_room_runs_and_logs_once(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        first = await executor.call("track_order", order_id="ZQ7")
        second = await executor.call("track_order", order_id="ZQ7")
        self.assertEqual(first, second)
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(len(self.telemetry()), 1)
        self.assertEqual(self.trace_kinds(), ["tool_call", "tool_call_deduplicated"])

    async def test_new_words_without_a_recheck_keep_the_call_from_running(self):
        clock = {"now": 0.0}
        gate = CommitGate(quiet_seconds=0.6, clock=lambda: clock["now"])
        executor = ToolExecutor(self.registry, "room-a", self.trace, gate=gate)
        gate.set_speaking(True)
        task = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await asyncio.sleep(0)
        gate.note_transcript("Actually, the order is ZQ8.")
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())  # held while the person is still speaking
        gate.set_speaking(False)
        clock["now"] = 0.6
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.telemetry(), [])
        # The trace shows the call held at the gate, the decision, and the drop.
        self.assertEqual(self.trace_kinds(), ["tool_call_pending", "tool_call_rechecked", "tool_call_dropped"])
        dropped = self.trace_records()[-1]
        self.assertEqual(dropped["reason"], "the person said more and no re-check is configured")

    async def test_quiet_window_releases_one_logged_call(self):
        clock = {"now": 0.0}
        gate = CommitGate(quiet_seconds=0.6, clock=lambda: clock["now"])
        gate.set_speaking(True)
        gate.set_speaking(False)
        executor = ToolExecutor(self.registry, "room-a", self.trace, gate=gate)
        task = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await asyncio.sleep(0.05)
        self.assertEqual(self.telemetry(), [])
        self.assertEqual(self.trace_kinds(), ["tool_call_pending"])
        clock["now"] = 0.6
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(self.telemetry()), 1)
        self.assertEqual(self.trace_kinds(), ["tool_call_pending", "tool_call"])
        pending = json.loads(self.trace.path.read_text().splitlines()[0])
        self.assertEqual(pending["call"], {"function": "track_order", "args": {"order_id": "ZQ7"}})
        self.assertEqual(pending["quiet_seconds"], 0.6)

    async def test_changed_arguments_run_again(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        await executor.call("search_products", query="desk lamps")
        await executor.call("search_products", query="desk lamps", max_price=40.0)
        self.assertEqual(len(self.telemetry()), 2)

    async def test_rooms_never_share_results(self):
        await ToolExecutor(self.registry, "room-a", self.trace).call("track_order", order_id="ZQ7")
        await ToolExecutor(self.registry, "room-b", self.trace).call("track_order", order_id="ZQ7")
        self.assertEqual([entry["room"] for entry in self.telemetry()], ["room-a", "room-b"])

    async def test_concurrent_duplicate_waits_for_the_first_call(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        release = asyncio.Event()
        original = executor._execute

        async def slow(name, arguments):
            await release.wait()
            return await original(name, arguments)

        executor._execute = slow
        first = asyncio.create_task(executor.call("add_to_cart", product_id="X1", quantity=1))
        second = asyncio.create_task(executor.call("add_to_cart", product_id="X1", quantity=1))
        await asyncio.sleep(0)
        release.set()
        self.assertEqual(await first, await second)
        self.assertEqual(len(self.telemetry()), 1)

    async def test_cancelled_call_does_not_block_a_retry(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        started = asyncio.Event()
        original = executor._execute

        async def hang(name, arguments):
            started.set()
            await asyncio.Event().wait()

        executor._execute = hang
        task = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        executor._execute = original
        await executor.call("track_order", order_id="ZQ7")
        self.assertEqual(len(self.telemetry()), 1)

    async def test_waiter_retries_when_first_attempt_is_cancelled(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        started = asyncio.Event()
        original = executor._execute
        attempts = []

        async def first_hangs(name, arguments):
            attempts.append(name)
            if len(attempts) == 1:
                started.set()
                await asyncio.Event().wait()
            return await original(name, arguments)

        executor._execute = first_hangs
        first = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await started.wait()
        waiter = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await asyncio.sleep(0)
        first.cancel()
        result = await waiter
        self.assertIn("ZQ7", result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.telemetry()), 1)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ScriptedRecheck:
    """Stands in for the model's re-check: records each question and gives a fixed answer."""

    def __init__(self, answer=True, release=None):
        self.answer = answer
        self.release = release
        self.asked = []

    async def __call__(self, name, arguments, words):
        self.asked.append((name, arguments, words))
        if self.release is not None:
            await self.release.wait()
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class HeldCallRecheckTests(ExecutorFixture, unittest.IsolatedAsyncioTestCase):
    """Regressions from the 2026-10-20 Kaggle run: 21 calls were dropped as "user continued".

    The person kept talking after a call was planned, the gate dropped the call
    the moment speech resumed, and the "not executed" result was committed to
    the chat history, so the re-planned turn treated the request as failed.
    """

    def start(self, recheck=None, **options):
        self.clock = FakeClock()
        self.gate = CommitGate(quiet_seconds=0.6, clock=self.clock)
        return ToolExecutor(self.registry, "room-a", self.trace, gate=self.gate, recheck=recheck, **options)

    async def held(self, executor, name, heard_before, heard_after, **arguments):
        """Plan a call on ``heard_before``; the person resumes and says ``heard_after`` before it runs."""
        for words in heard_before:
            self.gate.note_transcript(words)
        self.gate.set_speaking(True)
        task = asyncio.create_task(executor.call(name, **arguments))
        await asyncio.sleep(0.05)
        for words in heard_after:
            self.gate.note_transcript(words)
        self.gate.set_speaking(False)
        await asyncio.sleep(0.05)
        self.assertFalse(task.done(), "a call must wait out the quiet window after the person stops")
        self.clock.now += 0.6
        return task

    async def test_continuation_that_adds_a_request_still_runs_the_held_call(self):
        # ecommerce_25: track_order(CAT) was dropped when "Then search for cat food" began.
        recheck = ScriptedRecheck(True)
        executor = self.start(recheck)
        task = await self.held(
            executor, "track_order",
            ["Let me focus on what I actually need.", "Track order CAT for me."],
            ["Then search for cat food because I'm running low."],
            order_id="CAT",
        )
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(result["status"], "success")
        self.assertEqual([entry["call"]["function"] for entry in self.telemetry()], ["track_order"])
        name, arguments, words = recheck.asked[0]
        self.assertEqual((name, arguments), ("track_order", {"order_id": "CAT"}))
        self.assertEqual(words.earlier, ("Let me focus on what I actually need.", "Track order CAT for me."))
        self.assertEqual(words.later, ("Then search for cat food because I'm running low.",))
        self.assertEqual(self.trace_kinds(), ["tool_call_pending", "tool_call_rechecked", "tool_call"])
        rechecked = self.trace_records()[1]
        self.assertTrue(rechecked["run"])
        self.assertEqual(rechecked["later"], "Then search for cat food because I'm running low.")
        # The re-planned turn asks for the same call again: it is reused, not logged twice.
        again = json.loads(await asyncio.wait_for(executor.call("track_order", order_id="CAT"), timeout=1))
        self.assertEqual(again, result)
        self.assertEqual(len(self.telemetry()), 1)
        self.assertEqual(self.trace_kinds()[-1], "tool_call_deduplicated")

    async def test_held_call_and_its_replanned_twin_run_once(self):
        # ecommerce_23: track_order(GG5) was held while the person added a refund and a cart item.
        # The re-planned turn issues the same call before the held call's re-check returns.
        release = asyncio.Event()
        executor = self.start(ScriptedRecheck(True, release=release))
        task = await self.held(
            executor, "track_order",
            ["So you like first, could you track order g g five?"],
            ["Then I'd like to process a refund for it if possible.", "And also add item x one to my cart."],
            order_id="GG5",
        )
        await asyncio.sleep(0.05)  # the held call is now waiting for its re-check
        replanned = json.loads(await asyncio.wait_for(executor.call("track_order", order_id="GG5"), timeout=1))
        release.set()
        held = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(held, replanned)
        self.assertEqual(self.registry.calls, [("track_order", {"order_id": "GG5"})])
        self.assertEqual(len(self.telemetry()), 1)
        kinds = self.trace_kinds()
        self.assertEqual(kinds.count("tool_call"), 1)
        self.assertEqual(kinds[-2:], ["tool_call_rechecked", "tool_call_deduplicated"])

    async def test_correction_while_held_drops_the_call_before_it_is_logged(self):
        # ecommerce_19 shape: "search for a laptop. Wait, no, actually I need a tablet."
        executor = self.start(ScriptedRecheck(False))
        task = await self.held(
            executor, "search_products",
            ["Okay, so search for a laptop."],
            ["Wait, no, no, actually I need a tablet."],
            query="laptop",
        )
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(result["status"], "superseded")
        self.assertIn("call the tool again", result["message"])
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.telemetry(), [])
        self.assertEqual(self.trace_kinds(), ["tool_call_pending", "tool_call_rechecked", "tool_call_dropped"])
        self.assertEqual(self.trace_records()[-1]["reason"], "the person changed or cancelled this request")
        corrected = json.loads(await asyncio.wait_for(executor.call("search_products", query="tablet"), timeout=1))
        self.assertEqual(corrected["status"], "success")
        self.assertEqual([entry["call"]["args"] for entry in self.telemetry()], [{"query": "tablet"}])

    async def test_dropped_call_that_the_replanned_turn_ran_returns_its_result(self):
        # The re-check said the words changed, but the model's re-plan of the complete
        # turn still ran this exact call: report the real result instead of "not run".
        release = asyncio.Event()
        executor = self.start(ScriptedRecheck(False, release=release))
        task = await self.held(
            executor, "track_order", ["Track order CAT."], ["Yes, CAT, the cat food one."], order_id="CAT",
        )
        await asyncio.sleep(0.05)
        replanned = await asyncio.wait_for(executor.call("track_order", order_id="CAT"), timeout=1)
        release.set()
        self.assertEqual(await asyncio.wait_for(task, timeout=1), replanned)
        self.assertEqual(len(self.telemetry()), 1)
        self.assertEqual(self.trace_kinds()[-1], "tool_call_deduplicated")
        self.assertNotIn("tool_call_dropped", self.trace_kinds())

    async def test_speech_without_new_words_runs_the_call_without_a_recheck(self):
        # ecommerce_15: track_order(BOB12) was dropped the moment speech resumed.
        recheck = ScriptedRecheck(False)
        executor = self.start(recheck)
        task = await self.held(
            executor, "track_order", ["So, well, can you track order b o b one two for me?"], [], order_id="BOB12",
        )
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(result["status"], "success")
        self.assertEqual(recheck.asked, [])
        self.assertEqual(self.trace_kinds(), ["tool_call_pending", "tool_call"])

    async def test_a_failed_recheck_never_runs_the_held_call(self):
        hangs = asyncio.Event()  # never set
        cases = [
            (ScriptedRecheck(ValueError("not JSON")), {}, "re-check failed: ValueError"),
            (ScriptedRecheck("yes"), {}, "re-check gave no yes/no answer"),
            (ScriptedRecheck(True, release=hangs), {"recheck_timeout": 0.05}, "re-check timed out"),
        ]
        for recheck, options, reason in cases:
            with self.subTest(reason=reason):
                executor = self.start(recheck, **options)
                task = await self.held(executor, "add_to_cart", ["Add item K2."], ["Just one of them."],
                                       product_id="K2", quantity=1)
                result = json.loads(await asyncio.wait_for(task, timeout=1))
                self.assertEqual(result["status"], "superseded")
                self.assertEqual(self.trace_records()[-1]["reason"], reason)
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.telemetry(), [])

    async def test_cancelling_a_held_call_propagates_and_logs_nothing(self):
        executor = self.start(ScriptedRecheck(True))
        self.gate.set_speaking(True)
        task = asyncio.create_task(executor.call("track_order", order_id="ZQ7"))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.trace_kinds(), ["tool_call_pending"])


class RecheckAnswerTests(unittest.IsolatedAsyncioTestCase):
    def test_only_a_boolean_run_is_accepted(self):
        self.assertTrue(parse_recheck('{"run": true}'))
        self.assertFalse(parse_recheck('```json\n{"run": false}\n```'))
        for bad in ['yes', '{"run": "yes"}', '{"keep": true}', '[true]', '']:
            with self.subTest(answer=bad), self.assertRaises(ValueError):
                parse_recheck(bad)

    async def test_model_recheck_sends_the_call_and_the_words_and_validates_the_answer(self):
        from livekit.agents import llm

        asked = []

        class Stream:
            def __init__(self, pieces):
                self.pieces = pieces

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                return self._chunks()

            async def _chunks(self):
                for piece in self.pieces:
                    yield SimpleNamespace(delta=None if piece is None else SimpleNamespace(content=piece))

        class Model:
            def __init__(self, pieces):
                self.pieces = pieces

            def chat(self, *, chat_ctx, **options):
                asked.append(chat_ctx)
                return Stream(self.pieces)

        words = HeldWords(earlier=("Track order CAT for me.",), later=("Then search for cat food.",))
        decide = llm_recheck(Model([None, '{"run":', " true}"]), llm)
        self.assertTrue(await decide("track_order", {"order_id": "CAT"}, words))
        system, user = asked[0].items
        self.assertEqual((system.role, user.role), ("system", "user"))
        self.assertEqual(system.text_content, RECHECK_INSTRUCTIONS)
        payload = json.loads(user.text_content)
        self.assertEqual(payload["pending_action"], {"tool": "track_order", "arguments": {"order_id": "CAT"}})
        self.assertEqual(payload["later_words"], "Then search for cat food.")
        with self.assertRaises(ValueError):
            await llm_recheck(Model(["Sure, run it."]), llm)("track_order", {"order_id": "CAT"}, words)


class UnfinishedTranscriptTests(unittest.TestCase):
    def test_observed_mid_sentence_commits_are_unfinished(self):
        # Turns committed in the 2026-10-01 trace just before an early tool call.
        for text in [
            "So, like,", "I'm looking for", "track order b o b for me first, then",
            "Then", "And then", "Then search for apartments in Dallas at",
            "I'd like to", "So, um", "my new number is v -",
        ]:
            with self.subTest(text=text):
                self.assertTrue(looks_unfinished(text))

    def test_complete_requests_are_finished(self):
        for text in [
            "Track order CAT for me.", "Could you look up flights for the day?",
            "That's what I'm looking for.", "A desk.", "Search in Seattle for a one bedroom",
            "", "Thanks!", "Yes",
        ]:
            with self.subTest(text=text):
                self.assertFalse(looks_unfinished(text))

    def test_conjunction_still_signals_more_after_a_full_stop(self):
        self.assertTrue(looks_unfinished("Book it for Example Person and."))
        self.assertTrue(looks_unfinished("Check my order. Then."))


class ManualSleep:
    """Deterministic stand-in for asyncio.sleep: the test decides when time passes."""

    def __init__(self):
        self.pending = []

    async def __call__(self, seconds):
        future = asyncio.get_running_loop().create_future()
        self.pending.append((seconds, future))
        await future

    def elapse(self):
        pending, self.pending = self.pending, []
        for _, future in pending:
            if not future.done():
                future.set_result(None)


class UnfinishedTurnHoldTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.released = []
        self.trace = Mock()
        self.sleep = ManualSleep()
        self.hold = UnfinishedTurnHold(
            self.released.append, self.trace, "room-a", 1.0, sleep=self.sleep
        )

    async def asyncTearDown(self):
        await self.hold.aclose()

    def kinds(self):
        return [call.args[0] for call in self.trace.append.call_args_list]

    async def test_complete_turn_passes_through(self):
        self.assertEqual(self.hold.on_turn_completed(" Track order ZQ7. "), "Track order ZQ7.")
        self.assertEqual(self.kinds(), [])

    async def test_unfinished_turn_is_merged_into_the_next_turn(self):
        self.assertIsNone(self.hold.on_turn_completed("Track order ZQ7 first, then"))
        self.hold.on_user_speaking()
        await asyncio.sleep(0)
        self.sleep.elapse()
        await asyncio.sleep(0)
        self.assertEqual(self.released, [])
        self.assertEqual(
            self.hold.on_turn_completed("search for desk lamps."),
            "Track order ZQ7 first, then search for desk lamps.",
        )
        self.assertEqual(self.hold.held_text, "")
        self.assertEqual(self.kinds(), ["turn_held"])

    async def test_silence_releases_held_text_as_a_turn(self):
        self.assertIsNone(self.hold.on_turn_completed("I want flights to"))
        await asyncio.sleep(0)
        self.assertEqual(self.sleep.pending[0][0], 1.0)
        self.sleep.elapse()
        await asyncio.sleep(0)
        self.assertEqual(self.released, ["I want flights to"])
        self.assertEqual(self.kinds(), ["turn_held", "turn_released"])
        self.assertEqual(self.trace.append.call_args.kwargs["reason"], "silence")

    async def test_participant_leaving_flushes_held_text(self):
        self.hold.on_turn_completed("and then")
        self.hold.flush("participant_left")
        self.assertEqual(self.released, ["and then"])
        self.hold.flush("participant_left")
        self.assertEqual(self.released, ["and then"])

    async def test_several_unfinished_turns_accumulate(self):
        self.hold.on_turn_completed("So, like,")
        self.hold.on_turn_completed("I'm looking for")
        self.assertEqual(self.hold.held_text, "So, like, I'm looking for")

    async def test_disabled_hold_never_defers(self):
        hold = UnfinishedTurnHold(self.released.append, self.trace, "room-a", 0.0)
        self.assertEqual(hold.on_turn_completed("and then"), "and then")

    async def test_release_failure_is_traced(self):
        def fail(text):
            raise RuntimeError("session closing")

        hold = UnfinishedTurnHold(fail, self.trace, "room-a", 1.0, sleep=self.sleep)
        hold.on_turn_completed("and then")
        hold.flush("participant_left")
        self.assertEqual(self.kinds()[-1], "turn_release_failed")
        await hold.aclose()

    async def test_close_cancels_the_timer(self):
        self.hold.on_turn_completed("and then")
        await asyncio.sleep(0)
        await self.hold.aclose()
        self.assertFalse(any(
            task.get_name() == "interra-turn-hold" for task in asyncio.all_tasks()
        ))
        self.assertEqual(self.released, [])

    async def test_agent_hook_stops_held_turn_and_rewrites_merged_turn(self):
        class Stop(Exception):
            pass

        first = SimpleNamespace(text_content="Track order ZQ7 and", content=["Track order ZQ7 and"])
        with self.assertRaises(Stop):
            apply_turn_hold(self.hold, first, Stop)
        second = SimpleNamespace(text_content="then add X1 to my cart.", content=["then add X1 to my cart."])
        apply_turn_hold(self.hold, second, Stop)
        self.assertEqual(second.content, ["Track order ZQ7 and then add X1 to my cart."])
        third = SimpleNamespace(text_content="Thanks.", content=["Thanks."])
        apply_turn_hold(self.hold, third, Stop)
        self.assertEqual(third.content, ["Thanks."])


class DiagnosticsTests(unittest.TestCase):
    def test_conversation_items_record_role_and_text(self):
        trace = Mock()
        item = SimpleNamespace(role="assistant", text_content="Done.", interrupted=False)
        trace_conversation_item(trace, "room-a", item)
        trace.append.assert_called_once_with(
            "conversation_item", room="room-a", role="assistant", text="Done.", interrupted=False
        )
        trace.reset_mock()
        trace_conversation_item(trace, "room-a", SimpleNamespace(type="agent_handoff"))
        trace.append.assert_not_called()

    def test_errors_record_source_and_recoverability(self):
        trace = Mock()

        class FakeLLM:
            pass

        error = SimpleNamespace(error=RuntimeError("MaxGatewayCredits"), recoverable=False)
        trace_session_error(trace, "room-a", SimpleNamespace(error=error, source=FakeLLM()))
        trace.append.assert_called_once_with(
            "session_error", room="room-a", source="FakeLLM",
            error="MaxGatewayCredits", recoverable=False,
        )


class TurnConfigurationTests(unittest.TestCase):
    def test_turn_handling_reads_config_and_keeps_semantic_detector(self):
        config = FdbConfig(
            fdb_v3_root=Path("."), endpointing_min_delay=0.6, endpointing_max_delay=1.8
        )
        handling = benchmark_turn_handling(config)
        self.assertEqual(handling["endpointing"]["min_delay"], 0.6)
        self.assertEqual(handling["endpointing"]["max_delay"], 1.8)
        # Leaving turn_detection unset selects LiveKit's hosted TurnDetector.
        self.assertNotIn("turn_detection", handling)

    def test_environment_overrides(self):
        with patch.dict(os.environ, {
            "INTERRA_FDB_LLM_TEMPERATURE": "0.2",
            "INTERRA_FDB_ENDPOINTING_MIN_DELAY": "0.5",
            "INTERRA_FDB_ENDPOINTING_MAX_DELAY": "2.0",
            "INTERRA_FDB_UNFINISHED_TURN_HOLD_SECONDS": "0",
        }, clear=True):
            config = FdbConfig.from_env()
        self.assertEqual(config.llm_temperature, 0.2)
        self.assertEqual((config.endpointing_min_delay, config.endpointing_max_delay), (0.5, 2.0))
        self.assertEqual(config.unfinished_turn_hold_seconds, 0.0)

    def test_invalid_delays_are_rejected(self):
        with self.assertRaises(ValueError):
            FdbConfig(fdb_v3_root=Path("."), endpointing_min_delay=2.0, endpointing_max_delay=1.0)
        with self.assertRaises(ValueError):
            FdbConfig(fdb_v3_root=Path("."), unfinished_turn_hold_seconds=-1)

    def test_prompt_forbids_clarification_iso_dates_and_repeats(self):
        self.assertIn("Never ask for", INSTRUCTIONS)
        self.assertIn("Never use ISO format", INSTRUCTIONS)
        self.assertIn("never repeat a call", INSTRUCTIONS)
        self.assertNotRegex(INSTRUCTIONS, r"\d{4}-\d{2}-\d{2}")


class LifecycleHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_held_turn_is_flushed_before_the_drain(self):
        handlers = {}
        room = SimpleNamespace(
            name="room-a",
            on=lambda name, callback: handlers.setdefault(name, callback),
            off=lambda name, callback: None,
        )
        order = []
        started = asyncio.Event()

        async def start(**kwargs):
            started.set()

        async def drain():
            order.append("drain")

        session = SimpleNamespace(
            start=AsyncMock(side_effect=start), drain=AsyncMock(side_effect=drain),
            aclose=AsyncMock(), on=lambda *args: None, off=lambda *args: None,
        )
        ctx = SimpleNamespace(room=room, shutdown=Mock())
        task = asyncio.create_task(run_session_lifecycle(
            ctx, session, object(), object(), Mock(), participant_identity="recorder",
            on_participant_left=lambda: order.append("flush"),
        ))
        await started.wait()
        handlers["participant_disconnected"](SimpleNamespace(identity="recorder"))
        await task
        self.assertEqual(order, ["flush", "drain"])


if __name__ == "__main__":
    unittest.main()
