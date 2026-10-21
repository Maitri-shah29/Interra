"""Camera catalog and booking gate, without a LiveKit connection."""
import asyncio
import json
import unittest

from agent.commit import CommitGate
from agent.device_catalog import DeviceDesk, GatedAction


class ListTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, kind: str, **payload) -> None:
        self.events.append({"kind": kind, **payload})


class DeviceCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trace = ListTrace()
        self.desk = DeviceDesk()

    def kinds(self):
        return [event["kind"] for event in self.trace.events]

    def test_known_code_returns_one_check_and_unknown_code_does_not_invent_one(self):
        found = self.desk.lookup("e1")
        self.assertEqual(found["status"], "found")
        self.assertIn("door", found["meaning"].lower())
        unknown = self.desk.lookup("ZZ")
        self.assertEqual(unknown["status"], "unknown")
        self.assertNotIn("meaning", unknown)

    async def booking_held_through(self, later_words, keep):
        """Plan a Friday booking, then the person says ``later_words`` before it runs."""
        clock = {"now": 0.0}
        gate = CommitGate(quiet_seconds=0.6, clock=lambda: clock["now"])
        asked = []

        async def recheck(name, arguments, words):
            asked.append(words.later)
            return keep

        actions = GatedAction(gate, self.trace, "room", recheck=recheck)
        gate.note_transcript("Can someone come on Friday morning?")
        gate.set_speaking(True)
        task = asyncio.create_task(
            actions.run("schedule_check", {"day": "Friday", "window": "morning"}, lambda: self.desk.schedule("Friday", "morning"))
        )
        await asyncio.sleep(0.05)
        gate.note_transcript(later_words)
        gate.set_speaking(False)
        await asyncio.sleep(0.05)
        self.assertFalse(task.done(), "the booking waits until the person has finished")
        clock["now"] = 0.6
        result = json.loads(await asyncio.wait_for(task, timeout=1))
        self.assertEqual(asked, [(later_words,)])
        return result

    async def test_correction_drops_a_booking_before_it_is_stored(self):
        result = await self.booking_held_through("No, wait, make it Saturday.", keep=False)
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(self.desk.list_visits()["visits"], [])
        self.assertEqual(self.kinds(), ["tool_call_pending", "tool_call_rechecked", "tool_call_dropped"])
        self.assertEqual(self.trace.events[0]["call"]["args"]["day"], "Friday")

    async def test_booking_survives_words_that_only_add_to_the_request(self):
        result = await self.booking_held_through("And please ask them to bring a spare filter.", keep=True)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual([visit["day"] for visit in self.desk.list_visits()["visits"]], ["Friday"])
        self.assertEqual(self.kinds(), ["tool_call_pending", "tool_call_rechecked", "tool_call"])

    async def test_quiet_booking_is_visible_to_a_later_read(self):
        clock = {"now": 0.0}
        gate = CommitGate(quiet_seconds=0.6, clock=lambda: clock["now"])
        gate.set_speaking(True)
        gate.set_speaking(False)
        actions = GatedAction(gate, self.trace, "room")
        task = asyncio.create_task(
            actions.run("schedule_check", {"day": "Saturday", "window": "morning"}, lambda: self.desk.schedule("Saturday", "morning"))
        )
        clock["now"] = 0.6
        await task
        listed = json.loads(await actions.run("list_scheduled_checks", {}, self.desk.list_visits))
        self.assertEqual(listed["visits"][0]["day"], "Saturday")
