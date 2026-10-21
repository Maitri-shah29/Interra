"""Run the home demo script through the real controller, without LiveKit.

The walkthrough corrects an AC setpoint mid-sentence, turns off a light,
unlocks a door only after a yes, adds an air purifier the agent has never
seen and controls it, then undoes that change. It writes a replay page and
prints what each device ended up as.

The person's lines are scripted. The live agent asks its model whether a held
command still stands after the person said more; this script fixes that answer
(the later words correct the setpoint). Every device change comes from the
real controller.

    python -m agent.home_demo
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from agent.commit import CommitGate, HeldWords
from agent.replay import render_page
from agent.smart_home import AIR_PURIFIER, HomeController, SmartHome


async def _scripted_recheck(name: str, arguments: dict, words: HeldWords) -> bool:
    """Offline stand-in for the model's re-check: here the later words correct the setpoint."""
    return False


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, kind: str, **payload: object) -> None:
        self.events.append({"kind": kind, **payload})


async def walkthrough() -> tuple[dict[str, dict], list[dict]]:
    """Return the final state of every device and the trace."""
    clock = _Clock()
    gate = CommitGate(quiet_seconds=0.6, clock=clock)
    trace = _Trace()
    controller = HomeController(SmartHome(), gate, trace, "home-demo", clock=clock, recheck=_scripted_recheck)
    controller.publish()

    def heard(text: str) -> None:
        trace.append("transcript", room="home-demo", transcript=text, is_final=True)
        gate.note_transcript(text)

    async def quiet(name: str, **arguments: object) -> dict:
        clock.now += 1.0
        return await controller.call(name, arguments)

    # "Set the living room AC to 22 and turn off the lights. No wait, make it 24."
    heard("Set the living room AC to 22 and turn off the lights.")
    gate.set_speaking(True)
    stale = asyncio.create_task(controller.call("set_temperature", {"device": "Living room AC", "setpoint": 22}))
    await asyncio.sleep(0)
    heard("No wait, make it 24.")
    gate.set_speaking(False)
    clock.now += 1.0
    if (await stale)["status"] != "superseded":
        raise RuntimeError("The corrected setpoint reached the AC.")
    results = await asyncio.gather(
        controller.call("set_temperature", {"device": "Living room AC", "setpoint": 24}),
        controller.call("turn_on", {"device": "Living room AC"}),
        controller.call("turn_off", {"device": "Living room lights"}),
    )
    if any(result["status"] != "done" for result in results):
        raise RuntimeError(f"A requested change did not run: {results}")

    # "Unlock the front door." "Yes."
    heard("Unlock the front door.")
    asked = await quiet("unlock_door", device="Front door")
    heard("Yes.")
    await quiet("confirm_action", confirmation_id=asked["confirmation_id"])

    # A device the agent has never seen, then "Set the air purifier to high."
    controller.add_device(copy.deepcopy(AIR_PURIFIER))
    heard("Set the air purifier to high.")
    await quiet("set_purifier_fan", device="Bedroom air purifier", fanMode="high")

    # "Undo that."
    heard("Undo that.")
    await quiet("undo_last_change")

    states = {device.label: dict(device.state) for device in controller.home.devices.values()}
    return states, trace.events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=Path("artifacts/home-demo/replay.html"))
    args = parser.parse_args()
    states, events = asyncio.run(walkthrough())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_page(events, "Home: 22 dropped, 24 set, door unlocked after a yes, purifier added"),
        encoding="utf-8",
    )
    for label in ("Living room AC", "Living room lights", "Front door", "Bedroom air purifier"):
        print(f"{label}: {json.dumps(states[label])}")
    print(args.output)


if __name__ == "__main__":
    main()
