"""Turn holding, duplicate-call protection and diagnostics for the FDB-v3 agent."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agent.fdb_livekit import (
    FdbConfig,
    INSTRUCTIONS,
    ToolExecutor,
    TraceWriter,
    UnfinishedTurnHold,
    apply_turn_hold,
    benchmark_turn_handling,
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


class ToolExecutorDeduplicationTests(unittest.IsolatedAsyncioTestCase):
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

    def trace_kinds(self):
        return [json.loads(line)["kind"] for line in self.trace.path.read_text().splitlines()]

    async def test_identical_call_in_one_room_runs_and_logs_once(self):
        executor = ToolExecutor(self.registry, "room-a", self.trace)
        first = await executor.call("track_order", order_id="ZQ7")
        second = await executor.call("track_order", order_id="ZQ7")
        self.assertEqual(first, second)
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(len(self.telemetry()), 1)
        self.assertEqual(self.trace_kinds(), ["tool_call", "tool_call_deduplicated"])

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
