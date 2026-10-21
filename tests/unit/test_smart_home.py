"""SmartThings-style home: generated tools and interruption-safe commands, without LiveKit."""
import asyncio
import copy
import unittest

from agent.commit import CommitGate
from agent.smart_home import (
    AIR_PURIFIER, READ_ONLY, SAMPLE_HOME, STATE_MODIFYING, HomeController, HomeError, SmartHome,
)


class ListTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, kind: str, **payload) -> None:
        self.events.append({"kind": kind, **payload})

    def kinds(self, *only: str) -> list[str]:
        return [event["kind"] for event in self.events if not only or event["kind"] in only]


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class SmartHomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.gate = CommitGate(quiet_seconds=0.6, clock=self.clock)
        self.trace = ListTrace()
        self.home = SmartHome()
        self.controller = HomeController(self.home, self.gate, self.trace, "home-room", clock=self.clock)

    def ac(self):
        return self.home.find("Living room AC").state

    async def run_quiet(self, name, **arguments):
        self.clock.now += 1.0  # the person has been quiet for longer than the window
        return await self.controller.call(name, arguments)

    def test_tools_come_from_the_device_descriptions(self):
        tools = {tool.name: tool for tool in self.controller.tools()}
        self.assertEqual(tools["list_devices"].effect, READ_ONLY)
        self.assertEqual(tools["get_device_status"].effect, READ_ONLY)
        self.assertEqual(tools["turn_on"].effect, STATE_MODIFYING)
        self.assertIn("Living room AC", tools["turn_on"].parameters["properties"]["device"]["enum"])
        self.assertEqual(tools["set_temperature"].parameters["properties"]["device"]["enum"], ["Living room AC"])
        self.assertEqual(tools["set_temperature"].parameters["properties"]["setpoint"]["maximum"], 30)
        self.assertEqual(tools["unlock_door"].risk, "confirm")
        self.assertEqual(tools["start_oven"].risk, "confirm")
        self.assertEqual(tools["set_brightness"].risk, "routine")
        self.assertNotIn("set_purifier_fan", tools)
        # A read-only capability yields no command tool.
        self.assertFalse(any(tool.capability == "temperatureMeasurement" for tool in tools.values()))

    async def test_quiet_command_runs_once_and_updates_the_device(self):
        result = await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.ac()["coolingSetpoint"], 24)
        self.assertEqual(self.trace.kinds("tool_call_pending", "tool_call", "tool_call_dropped"),
                         ["tool_call_pending", "tool_call"])
        snapshot = self.trace.events[-1]
        self.assertEqual(snapshot["kind"], "home_devices")
        self.assertEqual(next(d for d in snapshot["devices"] if d["id"] == "living-room-ac")["state"]["coolingSetpoint"], 24)

    def with_recheck(self, answer):
        asked = []

        async def decide(name, arguments, words):
            asked.append((name, dict(arguments), words))
            return answer

        self.controller = HomeController(self.home, self.gate, self.trace, "home-room", clock=self.clock, recheck=decide)
        return asked

    async def say_more_while_held(self, name, arguments, later):
        self.gate.note_transcript("Set the living room AC to 22")
        self.gate.set_speaking(True)
        task = asyncio.create_task(self.controller.call(name, arguments))
        await asyncio.sleep(0)
        self.gate.note_transcript(later)
        self.gate.set_speaking(False)
        self.clock.now += 1.0
        return await asyncio.wait_for(task, timeout=2)

    async def test_a_correction_drops_the_stale_value_before_it_reaches_the_device(self):
        asked = self.with_recheck(False)
        stale = await self.say_more_while_held("set_temperature", {"device": "Living room AC", "setpoint": 22}, "No wait, make it 24.")
        self.assertEqual(stale["status"], "superseded")
        self.assertEqual(self.ac()["coolingSetpoint"], 26)
        self.assertEqual(asked[0][2].later, ("No wait, make it 24.",))
        fresh = await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        self.assertEqual(fresh["status"], "done")
        executed = [e["call"]["args"]["setpoint"] for e in self.trace.events if e["kind"] == "tool_call"]
        self.assertEqual(executed, [24])
        rechecked = next(e for e in self.trace.events if e["kind"] == "tool_call_rechecked")
        self.assertFalse(rechecked["run"])
        dropped = next(e for e in self.trace.events if e["kind"] == "tool_call_dropped")
        self.assertEqual(dropped["reason"], "the person changed or cancelled this request")

    async def test_words_that_only_add_a_request_keep_the_command(self):
        self.with_recheck(True)
        result = await self.say_more_while_held(
            "set_temperature", {"device": "Living room AC", "setpoint": 22}, "And turn off the lights too.")
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.ac()["coolingSetpoint"], 22)

    async def test_without_a_recheck_a_command_heard_over_does_not_run(self):
        result = await self.say_more_while_held(
            "set_temperature", {"device": "Living room AC", "setpoint": 22}, "No wait, make it 24.")
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(self.ac()["coolingSetpoint"], 26)
        dropped = next(e for e in self.trace.events if e["kind"] == "tool_call_dropped")
        self.assertIn("no re-check", dropped["reason"])

    async def test_a_cough_does_not_drop_a_command(self):
        self.gate.set_speaking(True)
        task = asyncio.create_task(self.controller.call("turn_off", {"device": "Bedroom lamp"}))
        await asyncio.sleep(0)
        self.gate.set_speaking(False)  # speech with no words
        self.clock.now += 1.0
        self.assertEqual((await asyncio.wait_for(task, timeout=2))["status"], "done")

    async def test_a_newer_value_replaces_one_still_waiting(self):
        first = asyncio.create_task(self.controller.call("set_temperature", {"device": "Living room AC", "setpoint": 22}))
        second = asyncio.create_task(self.controller.call("set_temperature", {"device": "Living room AC", "setpoint": 24}))
        await asyncio.sleep(0.05)
        self.clock.now = 1.0
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        self.assertEqual([r["status"] for r in results], ["superseded", "done"])
        self.assertEqual(self.ac()["coolingSetpoint"], 24)
        self.assertEqual(self.trace.kinds("tool_call"), ["tool_call"])
        dropped = next(e for e in self.trace.events if e["kind"] == "tool_call_dropped")
        self.assertEqual(dropped["reason"], "replaced by a newer value")

    async def test_different_devices_are_not_replaced_by_each_other(self):
        lights = asyncio.create_task(self.controller.call("turn_off", {"device": "Living room lights"}))
        lamp = asyncio.create_task(self.controller.call("turn_off", {"device": "Bedroom lamp"}))
        await asyncio.sleep(0.05)
        self.clock.now = 1.0
        results = await asyncio.wait_for(asyncio.gather(lights, lamp), timeout=2)
        self.assertEqual([r["status"] for r in results], ["done", "done"])

    async def test_a_value_already_set_is_not_sent_again(self):
        result = await self.run_quiet("lock_door", device="Front door")
        self.assertEqual(result["status"], "already")
        self.assertEqual(self.trace.kinds("tool_call", "tool_call_pending"), [])
        self.assertEqual(self.trace.kinds("home_command_skipped"), ["home_command_skipped"])
        await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        again = await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        self.assertEqual(again["status"], "already")
        self.assertEqual(len(self.trace.kinds("tool_call")), 1)

    async def test_a_risky_command_waits_for_a_spoken_yes(self):
        asked = await self.run_quiet("unlock_door", device="Front door")
        self.assertEqual(asked["status"], "needs_confirmation")
        self.assertEqual(self.home.find("Front door").state["lock"], "locked")
        self.assertEqual(self.trace.kinds("tool_call_pending"), [])
        done = await self.run_quiet("confirm_action", confirmation_id=asked["confirmation_id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.home.find("Front door").state["lock"], "unlocked")
        reused = await self.run_quiet("confirm_action", confirmation_id=asked["confirmation_id"])
        self.assertEqual(reused["status"], "invalid")

    async def test_a_declined_or_late_confirmation_changes_nothing(self):
        asked = await self.run_quiet("start_oven", device="Oven")
        declined = await self.controller.call("cancel_action", {"confirmation_id": asked["confirmation_id"]})
        self.assertEqual(declined["status"], "cancelled")
        self.assertEqual(self.home.find("Oven").state["ovenState"], "ready")
        late = await self.run_quiet("start_oven", device="Oven")
        self.clock.now += 120
        expired = await self.controller.call("confirm_action", {"confirmation_id": late["confirmation_id"]})
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(self.home.find("Oven").state["ovenState"], "ready")

    async def test_undo_reverses_the_last_reversible_change_only(self):
        await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        undone = await self.run_quiet("undo_last_change")
        self.assertEqual(undone["status"], "done")
        self.assertEqual(self.ac()["coolingSetpoint"], 26)
        self.assertEqual((await self.run_quiet("undo_last_change"))["status"], "nothing")
        asked = await self.run_quiet("unlock_door", device="Front door")
        await self.run_quiet("confirm_action", confirmation_id=asked["confirmation_id"])
        self.assertEqual((await self.run_quiet("undo_last_change"))["status"], "nothing")
        self.assertEqual(self.home.find("Front door").state["lock"], "unlocked")

    async def test_an_unseen_device_brings_its_own_capabilities_and_tools(self):
        device, added = self.controller.add_device(copy.deepcopy(AIR_PURIFIER))
        self.assertEqual(device.label, "Bedroom air purifier")
        self.assertIn("set_purifier_fan", added)
        tools = {tool.name: tool for tool in self.controller.tools()}
        self.assertIn("Bedroom air purifier", tools["turn_on"].parameters["properties"]["device"]["enum"])
        result = await self.run_quiet("set_purifier_fan", device="Bedroom air purifier", fanMode="high")
        self.assertEqual(result["status"], "done")
        status = await self.controller.call("get_device_status", {"device": "Bedroom air purifier"})
        self.assertEqual(status["state"]["fineDustLevel"], 38)
        self.assertIn("home_device_added", self.trace.kinds())

    async def test_invalid_arguments_and_descriptions_are_rejected(self):
        result = await self.run_quiet("set_temperature", device="Living room AC", setpoint=40)
        self.assertEqual(result["status"], "invalid")
        result = await self.run_quiet("set_temperature", device="Oven", setpoint=24)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual((await self.controller.call("fly_away", {}))["status"], "invalid")
        bad = copy.deepcopy(AIR_PURIFIER)
        bad["capabilities"].append("teleport")
        with self.assertRaises(HomeError):
            self.controller.add_device(bad)
        clash = copy.deepcopy(AIR_PURIFIER)
        clash["capabilityDefinitions"]["airPurifierFanMode"]["commands"]["setAirPurifierFanMode"]["tool"] = "turn_on"
        with self.assertRaises(HomeError):
            SmartHome().add_device(clash)
        out_of_range = copy.deepcopy(AIR_PURIFIER)
        out_of_range["state"]["airPurifierFanMode"] = "turbo"
        with self.assertRaises(HomeError):
            SmartHome().add_device(out_of_range)
        unknown_attribute = copy.deepcopy(AIR_PURIFIER)
        unknown_attribute["capabilityDefinitions"]["airPurifierFanMode"]["commands"]["setAirPurifierFanMode"]["sets"] = {"speed": "$fanMode"}
        with self.assertRaises(HomeError):
            SmartHome().add_device(unknown_attribute)
        with self.assertRaises(HomeError):
            self.home.add_device(copy.deepcopy(SAMPLE_HOME[0]))

    async def test_sessions_share_nothing(self):
        await self.run_quiet("set_temperature", device="Living room AC", setpoint=24)
        other = SmartHome()
        self.assertEqual(other.find("Living room AC").state["coolingSetpoint"], 26)
        self.assertEqual(SAMPLE_HOME[0]["state"]["coolingSetpoint"], 26)
        self.controller.add_device(copy.deepcopy(AIR_PURIFIER))
        self.assertIsNone(other.find("Bedroom air purifier"))
        self.assertNotIn("airPurifierFanMode", other.capabilities)


if __name__ == "__main__":
    unittest.main()
