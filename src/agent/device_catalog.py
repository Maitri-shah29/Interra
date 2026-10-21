"""Practice catalog for the camera troubleshooting session.

These codes are Interra's own demo labels for a shown appliance. They are not
a manufacturer manual and they are not benchmark answers.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from agent.commit import RECHECK_TIMEOUT_SECONDS, SUPERSEDED, CommitGate, Recheck, recheck_call


CATALOG: dict[str, dict[str, str]] = {
    "E1": {
        "meaning": "The door or lid is not closed.",
        "check": "Close it until it latches, then start the cycle again.",
    },
    "E2": {
        "meaning": "The drain path is blocked.",
        "check": "Clear the filter and the drain hose, then run a drain cycle.",
    },
    "E3": {
        "meaning": "Water is not reaching the machine.",
        "check": "Open the inlet and check that the hose is not kinked.",
    },
}


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


class DeviceDesk:
    """One session's lookups and scheduled checks. Nothing is shared across sessions."""

    def __init__(self) -> None:
        self.visits: list[dict[str, str]] = []

    def lookup(self, code: str) -> dict[str, Any]:
        key = normalize_code(code)
        row = CATALOG.get(key)
        if row is None:
            return {
                "status": "unknown",
                "code": key,
                "message": "That code is not in the demo catalog. Ask for a closer view of the display.",
            }
        return {"status": "found", "code": key, **row}

    def schedule(self, day: str, window: str) -> dict[str, Any]:
        visit = {
            "id": f"visit-{len(self.visits) + 1}",
            "day": day.strip(),
            "window": window.strip(),
        }
        self.visits.append(visit)
        return {"status": "scheduled", "visit": visit}

    def list_visits(self) -> dict[str, Any]:
        return {"status": "ok", "visits": list(self.visits)}


class GatedAction:
    """Run a session action once the person has finished and the action still stands.

    The call is held through the commit gate. If the person said more while it
    was held, ``recheck`` decides from their complete words; without one, the
    action does not run.
    """

    def __init__(
        self, gate: CommitGate, trace: Any, room_name: str, *,
        recheck: Recheck | None = None, recheck_timeout: float = RECHECK_TIMEOUT_SECONDS,
    ) -> None:
        self.gate = gate
        self.trace = trace
        self.room_name = room_name
        self.recheck = recheck
        self.recheck_timeout = recheck_timeout

    async def run(self, name: str, arguments: dict[str, Any], action: Callable[[], dict[str, Any]]) -> str:
        mark = self.gate.mark()
        call = {"function": name, "args": arguments}
        self.trace.append(
            "tool_call_pending",
            room=self.room_name,
            call=call,
            quiet_seconds=self.gate.quiet_seconds,
        )
        words = await self.gate.hold(mark)
        if words.changed:
            verdict = await recheck_call(self.recheck, name, arguments, words, timeout=self.recheck_timeout)
            self.trace.append(
                "tool_call_rechecked", room=self.room_name, call=call,
                earlier=" ".join(words.earlier), later=" ".join(words.later),
                run=verdict.run, reason=verdict.reason,
            )
            if not verdict.run:
                self.trace.append("tool_call_dropped", room=self.room_name, call=call, reason=verdict.reason)
                return SUPERSEDED
        result = action()
        self.trace.append(
            "tool_call",
            room=self.room_name,
            status="completed",
            call={"function": name, "args": arguments},
            result=result,
        )
        return json.dumps(result, ensure_ascii=False)
