"""Hold a tool call until the person has finished, then decide whether it still stands.

A call waits at the gate while the person speaks and until they have been quiet
for ``quiet_seconds``. Speech alone never drops it: if no new words arrived
(a cough, a breath, a false start), the call runs. If new words did arrive, the
call is re-checked against the complete words. A continuation that only adds
requests or details leaves it standing ("track order CAT, then search for cat
food"); one that corrects, replaces or cancels it drops it ("search for a
laptop. Wait, no, a tablet"). The re-check is an injected decider, the model in
the voice agents, whose answer is schema-validated. Without a decider, or when
it fails, the call is not run and the result asks the model to plan again from
the person's complete words.

The quiet window is short because the voice session already waits for the turn
to end before it asks for a tool.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
import time
from typing import Any, Awaitable, Callable


# Returned to the model when a held call does not run. A held call's output is
# committed to the chat history even when its turn was interrupted, so it must
# not read as a failure to report or as something to confirm: it asks the model
# to act on the person's complete words.
SUPERSEDED = json.dumps(
    {
        "status": "superseded",
        "message": (
            "Not run: the person said more before this call was carried out. Act on their "
            "complete, latest words: call the tool again, with any corrected details, if they "
            "still want it. Do not ask them to repeat or confirm."
        ),
    }
)

# A held call does not block the re-planned reply, so the bound only has to
# cover a cold first request (about 5 s measured on LiveKit Inference; ~1 s warm).
RECHECK_TIMEOUT_SECONDS = 8.0
# Earlier final transcripts sent with a re-check, enough for one spoken request.
CONTEXT_SEGMENTS = 6


@dataclass(frozen=True)
class HeldWords:
    """What the person said around one held call."""

    earlier: tuple[str, ...]
    later: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.later)


@dataclass(frozen=True)
class Verdict:
    run: bool
    reason: str


# (tool name, arguments, words) -> True if the complete words still ask for exactly this call.
Recheck = Callable[[str, dict[str, Any], HeldWords], Awaitable[bool]]


class CommitGate:
    def __init__(self, quiet_seconds: float = 0.6, clock: Any = time.monotonic) -> None:
        self.quiet_seconds = quiet_seconds
        self._clock = clock
        self.speaking = False
        self._quiet_since: float | None = self._clock()
        self._finals: list[str] = []
        self._changed = asyncio.Event()

    def set_speaking(self, speaking: bool) -> None:
        speaking = bool(speaking)
        if speaking == self.speaking:
            return
        self.speaking = speaking
        self._quiet_since = None if speaking else self._clock()
        self._changed.set()

    def note_transcript(self, text: str) -> None:
        """A final transcript: new words a held call is re-checked against."""
        text = text.strip()
        if text:
            self._finals.append(text)
        if not self.speaking:
            self._quiet_since = self._clock()
        self._changed.set()

    def mark(self) -> int:
        """Where the words stand when a call is planned; pass it to ``hold``."""
        return len(self._finals)

    def _quiet(self) -> bool:
        if self.speaking or self._quiet_since is None:
            return False
        return self._clock() - self._quiet_since >= self.quiet_seconds

    async def hold(self, mark: int) -> HeldWords:
        """Wait until the person has been quiet for the window; report what they said meanwhile."""
        while not self._quiet():
            self._changed.clear()
            if self._quiet():
                break
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.02)
            except TimeoutError:
                continue
        return HeldWords(
            earlier=tuple(self._finals[max(0, mark - CONTEXT_SEGMENTS):mark]),
            later=tuple(self._finals[mark:]),
        )


async def recheck_call(
    decide: Recheck | None,
    name: str,
    arguments: dict[str, Any],
    words: HeldWords,
    *,
    timeout: float = RECHECK_TIMEOUT_SECONDS,
) -> Verdict:
    """Decide whether a held call still stands once the person has finished."""
    if not words.changed:
        return Verdict(True, "no new words")
    if decide is None:
        return Verdict(False, "the person said more and no re-check is configured")
    try:
        keep = await asyncio.wait_for(decide(name, dict(arguments), words), timeout)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return Verdict(False, "re-check timed out")
    except Exception as exc:  # a failed re-check must not run a possibly stale call
        return Verdict(False, f"re-check failed: {type(exc).__name__}")
    if not isinstance(keep, bool):
        return Verdict(False, "re-check gave no yes/no answer")
    if keep:
        return Verdict(True, "the rest of the turn does not change this request")
    return Verdict(False, "the person changed or cancelled this request")


RECHECK_INSTRUCTIONS = """
You check one pending action of a voice assistant. The assistant planned it from
the person's earlier words, but the person kept talking before it ran. Decide
whether their complete words still ask for exactly this action with exactly
these arguments.
Answer true when the later words only add other requests, add details for
other actions, repeat or confirm this one, or are small talk.
Answer false when the later words correct, replace, cancel or cast doubt on
this action or on any of its arguments.
Reply with JSON only: {"run": true} or {"run": false}.
""".strip()


def recheck_request(name: str, arguments: dict[str, Any], words: HeldWords) -> str:
    """The user message for a re-check: the pending call and the words around it."""
    return json.dumps(
        {
            "pending_action": {"tool": name, "arguments": arguments},
            "earlier_words": " ".join(words.earlier),
            "later_words": " ".join(words.later),
        },
        ensure_ascii=False,
        default=str,
    )


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_recheck(text: str) -> bool:
    """Validate a re-check answer: a JSON object whose "run" is a boolean."""
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    data = json.loads(stripped)
    if not isinstance(data, dict) or not isinstance(data.get("run"), bool):
        raise ValueError(f"re-check answer is not {{\"run\": true|false}}: {text[:120]!r}")
    return data["run"]
