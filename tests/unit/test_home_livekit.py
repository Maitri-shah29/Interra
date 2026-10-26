"""The home worker's tools, device RPC and session wiring, without a LiveKit connection."""
import asyncio
import copy
import inspect
import json
import pickle
import unittest
from types import SimpleNamespace

from agent.commit import CommitGate
from agent.home_livekit import (
    ADD_DEVICE_RPC, HOME_AGENT, HOME_INSTRUCTIONS, device_added_instructions, entrypoint, handle_add_device,
    home_llm, home_tools,
)
from agent.smart_home import AIR_PURIFIER, HomeController, SmartHome


class ListTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, kind: str, **payload) -> None:
        self.events.append({"kind": kind, **payload})


def fake_function_tool(handler, raw_schema):
    return SimpleNamespace(handler=handler, schema=raw_schema)


class HomeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 10.0
        self.gate = CommitGate(quiet_seconds=0.6, clock=lambda: self.now)
        self.trace = ListTrace()
        self.controller = HomeController(SmartHome(), self.gate, self.trace, "home-room", clock=lambda: self.now)

    async def test_each_generated_tool_runs_through_the_controller(self):
        tools = home_tools(self.controller, fake_function_tool)
        self.assertEqual([tool.schema["name"] for tool in tools], [tool.name for tool in self.controller.tools()])
        self.assertEqual(list(inspect.signature(tools[0].handler).parameters), ["raw_arguments"])
        by_name = {tool.schema["name"]: tool for tool in tools}
        self.now = 20.0
        reply = json.loads(await by_name["set_temperature"].handler(raw_arguments={"device": "Living room AC", "setpoint": 24}))
        self.assertEqual(reply["status"], "done")
        status = json.loads(await by_name["get_device_status"].handler(raw_arguments={"device": "Living room AC"}))
        self.assertEqual(status["state"]["coolingSetpoint"], 24)

    async def test_a_device_sent_by_the_page_adds_tools_and_bad_ones_are_rejected(self):
        reply, device = await handle_add_device(self.controller, json.dumps(AIR_PURIFIER))
        self.assertEqual(reply, {"status": "added", "device": "Bedroom air purifier", "new_tools": ["set_purifier_fan"]})
        self.assertIsNotNone(device)
        self.assertIn("set_purifier_fan", [tool.schema["name"] for tool in home_tools(self.controller, fake_function_tool)])
        again, none = await handle_add_device(self.controller, json.dumps(AIR_PURIFIER))
        self.assertEqual(again["status"], "rejected")
        self.assertIsNone(none)
        for payload in ("not json", "[]", json.dumps({**copy.deepcopy(AIR_PURIFIER), "id": "x"})):
            reply, _ = await handle_add_device(self.controller, payload)
            self.assertEqual(reply["status"], "rejected", payload)
        self.assertIn("home_device_rejected", [event["kind"] for event in self.trace.events])

    def test_model_allows_parallel_calls_for_several_devices(self):
        calls = []
        inference = SimpleNamespace(LLM=lambda **kwargs: calls.append(kwargs) or "model")
        config = SimpleNamespace(llm_model="openai/gpt-4.1-mini", llm_temperature=0.0)
        self.assertEqual(home_llm(inference, config), "model")
        self.assertTrue(calls[0]["extra_kwargs"]["parallel_tool_calls"])

    def test_instructions_cover_corrections_confirmation_and_undo(self):
        for phrase in ("superseded", "needs_confirmation", "confirm_action", "undo_last_change", "final value"):
            self.assertIn(phrase, HOME_INSTRUCTIONS)
        self.assertIn("Do not change it", device_added_instructions("Bedroom air purifier", ["set_purifier_fan"]))

    def test_entrypoint_wires_the_gate_rpc_and_tool_refresh(self):
        pickle.dumps(entrypoint)
        source = inspect.getsource(entrypoint)
        self.assertIn("register_rpc_method(ADD_DEVICE_RPC, on_add_device)", source)
        self.assertIn("await agent.update_tools(home_tools(controller, llm.function_tool))", source)
        self.assertIn("gate.set_speaking(True)", source)
        self.assertIn("gate.note_transcript(event.transcript)", source)
        self.assertIn("recheck=llm_recheck(recheck_llm_model(inference, config), llm)", source)
        self.assertIn("controller.publish()", source)
        self.assertEqual(HOME_AGENT, "interra-home")
        self.assertEqual(ADD_DEVICE_RPC, "interra.add_device")


if __name__ == "__main__":
    unittest.main()
