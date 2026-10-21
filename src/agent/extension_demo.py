"""Run the camera lookup and booking gate on one scripted correction.

No LiveKit room and no camera are required. The walkthrough looks up a visible
code, holds a Friday visit while the person keeps talking, drops it once their
complete words change the day, and stores Saturday. The live agent asks its
model whether a held call still stands; this script fixes that answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent.commit import CommitGate, HeldWords
from agent.device_catalog import DeviceDesk, GatedAction
from agent.replay import render_page


async def _scripted_recheck(name: str, arguments: dict[str, Any], words: HeldWords) -> bool:
    """Offline stand-in for the model's re-check: in this script the later words change the day."""
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


async def walkthrough() -> tuple[list[dict], list[dict]]:
    """Return the stored visits and the trace of executed and dropped calls."""
    clock = _Clock()
    gate = CommitGate(quiet_seconds=0.6, clock=clock)
    desk = DeviceDesk()
    trace = _Trace()
    actions = GatedAction(gate, trace, "camera-demo", recheck=_scripted_recheck)

    clock.now = 0.6
    await actions.run("lookup_indicator", {"code": "E1"}, lambda: desk.lookup("E1"))

    gate.note_transcript("Can someone come and look at it on Friday morning?")
    gate.set_speaking(True)
    friday = asyncio.create_task(
        actions.run(
            "schedule_check",
            {"day": "Friday", "window": "morning"},
            lambda: desk.schedule("Friday", "morning"),
        )
    )
    await asyncio.sleep(0)
    gate.note_transcript("Actually, no, make it Saturday morning.")
    gate.set_speaking(False)
    clock.now = 1.2
    dropped = json.loads(await friday)
    if dropped.get("status") != "superseded":
        raise RuntimeError("The corrected Friday visit was stored.")

    await actions.run(
        "schedule_check",
        {"day": "Saturday", "window": "morning"},
        lambda: desk.schedule("Saturday", "morning"),
    )
    listed = json.loads(await actions.run("list_scheduled_checks", {}, desk.list_visits))
    return listed["visits"], trace.events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("artifacts/extension-demo/replay.html"),
    )
    args = parser.parse_args()
    visits, events = asyncio.run(walkthrough())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_page(events, "Camera correction: Friday dropped, Saturday kept"),
        encoding="utf-8",
    )
    print(f"Visits stored: {json.dumps(visits)}")
    print(args.output)


if __name__ == "__main__":
    main()
