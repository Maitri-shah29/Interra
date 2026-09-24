import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from agent.clock import VirtualClock
from agent.fdb.backend import load_manifest
from agent.fdb.bridge import VoiceBridge
from agent.fdb.kitchen import KitchenBackend
from agent.runtime import SessionRuntime
from scripts.reproduce_fdb import pin_judge, validate_results
from tests.helpers import Harness, ScriptedPlanner, plan, spec


class Backend:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.closed = False

    async def execute(self, name, args):
        self.calls.append((name, args))
        await self.clock.sleep(10)
        return {"found": True}

    async def aclose(self):
        self.closed = True


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_fences_queued_speech_before_runtime_consumes_interrupt(self):
        r = SessionRuntime("voice", ScriptedPlanner(), VirtualClock())
        spoken = []
        bridge = VoiceBridge(r, Backend(r.clock), spoken.append, room_name="room")
        r.emit("FINAL", text="old answer", state_snapshot={"intent": None, "slots": {}})
        old = r.output.get_nowait()
        bridge.interrupt()
        await bridge.handle(old)
        self.assertEqual(spoken, [])
        self.assertIn("STALE_OUTPUT_DISCARDED", [e.kind for e in r.trace.entries])

    async def test_interrupt_cancels_backend_and_duplicate_dispatch_is_ignored(self):
        async with Harness(ScriptedPlanner(plan()), "voice-cancel") as h:
            backend = Backend(h.clock)
            bridge = VoiceBridge(h.runtime, backend, lambda text: None, room_name="room")
            await h.send("TOOL_MANIFEST", tools=[spec()])
            await h.text("Look up Delhi")
            action = await h.action("TOOL_CALL")
            await bridge.handle(action)
            await bridge.handle(action)
            await asyncio.sleep(0)
            self.assertEqual(len(backend.calls), 1)
            bridge.interrupt()
            await h.runtime.input.join()
            cancel = await h.action("CANCEL_TOOL_CALL")
            task = bridge.tasks[action.payload["call_id"]]
            await bridge.handle(cancel)
            await asyncio.gather(task, return_exceptions=True)
            await h.runtime.input.join()
            self.assertIn("CANCEL_ACKNOWLEDGED", h.kinds())
            self.assertIn("TOOL_EXECUTION_CANCELLED", h.kinds())
            self.assertIn("DUPLICATE_DISPATCH_DISCARDED", h.kinds())
            self.assertEqual(h.clock.pending, 0)

    async def test_vad_fences_queued_tool_before_side_effect(self):
        async with Harness(ScriptedPlanner(plan()), "voice-stale") as h:
            backend = Backend(h.clock)
            bridge = VoiceBridge(h.runtime, backend, lambda text: None, room_name="room")
            await h.send("TOOL_MANIFEST", tools=[spec()])
            await h.text("Look up Delhi")
            action = await h.action("TOOL_CALL")
            bridge.interrupt()
            await bridge.handle(action)
            self.assertEqual(backend.calls, [])
            self.assertIn("STALE_OUTPUT_DISCARDED", h.kinds())

    async def test_interrupt_between_dispatch_and_first_backend_timeslice(self):
        async with Harness(ScriptedPlanner(plan()), "voice-dispatch-race") as h:
            backend = Backend(h.clock)
            bridge = VoiceBridge(h.runtime, backend, lambda text: None, room_name="room")
            await h.send("TOOL_MANIFEST", tools=[spec()])
            await h.text("Look up Delhi")
            action = await h.action("TOOL_CALL")
            await bridge.handle(action)
            task = bridge.tasks[action.payload["call_id"]]
            bridge.interrupt()
            await asyncio.gather(task, return_exceptions=True)
            await h.runtime.input.join()
            self.assertEqual(backend.calls, [])
            self.assertIn("CANCEL_ACKNOWLEDGED", h.kinds())

    async def test_cancel_before_backend_task_starts_is_acknowledged(self):
        async with Harness(ScriptedPlanner(plan()), "voice-prestart-cancel") as h:
            backend = Backend(h.clock)
            bridge = VoiceBridge(h.runtime, backend, lambda text: None, room_name="room")
            await h.send("TOOL_MANIFEST", tools=[spec()])
            await h.text("Look up Delhi")
            action = await h.action("TOOL_CALL")
            await bridge.handle(action)
            task = bridge.tasks[action.payload["call_id"]]
            # Coordinator cancellation can be queued before the new task ever runs.
            h.runtime.scheduler.invalidate(h.runtime.calls[action.payload["call_id"]], "test cancellation")
            cancel = await h.action("CANCEL_TOOL_CALL")
            await bridge.handle(cancel)
            await asyncio.gather(task, return_exceptions=True)
            await h.runtime.input.join()
            self.assertEqual(backend.calls, [])
            self.assertIn("CANCEL_ACKNOWLEDGED", h.kinds())

    async def test_bridge_shutdown_owns_backend_cleanup(self):
        r = SessionRuntime("shutdown", ScriptedPlanner(), VirtualClock())
        backend = Backend(r.clock)
        bridge = VoiceBridge(r, backend, lambda text: None, room_name="room")
        task = asyncio.create_task(bridge.run())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(backend.closed)
        self.assertTrue(bridge.closed)

    async def test_kitchen_cancel_replace_expire_and_session_isolation(self):
        r = SessionRuntime("kitchen", ScriptedPlanner(), VirtualClock())
        spoken = []
        kitchen = KitchenBackend(r.clock, spoken.append, r.trace)
        other = KitchenBackend(r.clock, spoken.append, r.trace)
        first = await kitchen.execute("start_kitchen_timer", {"label": "tea", "seconds": 30})
        await asyncio.sleep(0)
        await kitchen.execute("cancel_kitchen_timer", {"timer_id": first["timer_id"]})
        await kitchen.execute("start_kitchen_timer", {"label": "tea", "seconds": 5})
        await asyncio.sleep(0)
        self.assertEqual((await other.execute("list_kitchen_timers", {}))["timers"], [])
        r.clock.advance_to(5)
        await asyncio.sleep(0)
        self.assertEqual(spoken, ["Your tea timer has finished."])
        r.clock.advance_to(40)
        await asyncio.sleep(0)
        self.assertEqual(len(spoken), 1)
        kinds = [e.kind for e in r.trace.entries]
        self.assertEqual(kinds.count("KITCHEN_TIMER_STARTED"), 2)
        self.assertEqual(kinds.count("KITCHEN_TIMER_CANCELLED"), 1)
        self.assertEqual(kinds.count("KITCHEN_TIMER_FINISHED"), 1)
        await kitchen.aclose()
        await other.aclose()
        self.assertEqual(r.clock.pending, 0)
        r.trace.save("artifacts/traces/fdb-kitchen.jsonl")

    async def test_kitchen_full_runtime_tool_chain_and_final(self):
        planner = ScriptedPlanner(
            {"state_patch": {"intent": "kitchen"}, "tool_requests": [{"tool_name": "start_kitchen_timer",
                "arguments": {"label": "tea", "seconds": 5}, "operation_key": "tea-first"}]},
            {"tool_requests": [{"tool_name": "list_kitchen_timers", "arguments": {}}]},
            {"final_response": "Your tea timer is running."})
        r = SessionRuntime("kitchen-chain", planner, VirtualClock())
        final = asyncio.Event()
        spoken = []
        def speak(text):
            spoken.append(text)
            if text == "Your tea timer is running.":
                final.set()
        backend = KitchenBackend(r.clock, speak, r.trace)
        bridge = VoiceBridge(r, backend, speak, room_name="kitchen-demo")
        runtime_task = asyncio.create_task(r.run())
        bridge_task = asyncio.create_task(bridge.run())
        try:
            bridge.send("TOOL_MANIFEST", tools=[s.model_dump(mode="json") for s in backend.specs])
            bridge.user_turn("Start a five second tea timer and tell me which timers are running")
            await asyncio.wait_for(final.wait(), 3)
            self.assertEqual(len(backend.timers), 1)
            self.assertEqual(len(planner.contexts), 3)
            self.assertEqual([e.kind for e in r.trace.entries].count("RESULT_ACCEPTED"), 2)
            r.clock.advance_to(5)
            await asyncio.sleep(0)
            self.assertIn("Your tea timer has finished.", spoken)
        finally:
            runtime_task.cancel()
            bridge_task.cancel()
            await asyncio.gather(runtime_task, bridge_task, return_exceptions=True)
            r.trace.save("artifacts/traces/fdb-kitchen-chain.jsonl")


class ReproductionTests(unittest.TestCase):
    def test_partial_results_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            item = data / "sample"
            item.mkdir()
            (item / "input.wav").touch()
            result = item / "result_interra.json"
            for value in ({"status": "error"}, {"status": "completed", "transcript": ""}):
                result.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    validate_results(data, "interra")
            result.write_text(json.dumps({"status": "completed", "transcript": "Finished"}))
            self.assertEqual(validate_results(data, "interra"), 1)

    def test_judge_patch_fails_on_changed_source(self):
        self.assertEqual(pin_judge('model="gpt-4o"', "pinned", 1), 'model="pinned"')
        with self.assertRaises(ValueError):
            pin_judge('model="different"', "pinned", 1)

    def test_manifest_schema_and_effects_fail_closed(self):
        source = '''class AssistantFnc:
    @tool(description="Look up an item")
    async def novel_lookup(self, item: str, count: int = 1):
        pass
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template.py"
            path.write_text(source)
            tools = load_manifest(path, {"novel_lookup": "READ_ONLY"})
            self.assertEqual(tools[0].argument_schema["required"], ["item"])
            self.assertEqual(tools[0].argument_schema["properties"]["count"]["default"], 1)
            with self.assertRaises(ValueError):
                load_manifest(path, {})
