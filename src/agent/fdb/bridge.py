"""Session-owned transport between voice callbacks, runtime and async tools."""
import asyncio
import json
from pathlib import Path
import time

from ..models import CallStatus, Event


class VoiceBridge:
    def __init__(self, runtime, backend, speak, *, room_name, tool_log=None):
        self.runtime, self.backend, self.speak = runtime, backend, speak
        self.room_name = room_name
        self.tool_log = Path(tool_log) if tool_log else None
        self.tasks = {}
        self.started_ids = set()
        self.minimum_generation = 0
        self.closed = False

    def send(self, kind, **payload):
        if self.closed:
            return
        r = self.runtime
        r.input.put_nowait(Event(event_id=r.ids.new("voice-event"), session_id=r.session_id,
                                timestamp=r.clock.now(), type=kind, payload=payload))

    def user_turn(self, text):
        if text.strip():
            self.send("TEXT_CHUNK", text=text, end_of_turn=True)

    def interrupt(self):
        # The fence is immediate, even if the coordinator has not consumed the event.
        self.minimum_generation = max(self.minimum_generation, self.runtime.epoch + 1)
        self.send("INTERRUPTION", reason="voice activity during active work")

    @property
    def busy(self):
        r = self.runtime
        return r.active_plan_token is not None or any(
            c.status == CallStatus.DISPATCHED for c in r.calls.values())

    async def run(self):
        try:
            while True:
                action = await self.runtime.output.get()
                try:
                    await self.handle(action)
                finally:
                    self.runtime.output.task_done()
        finally:
            self.closed = True
            tasks = list(self.tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.tasks.clear()
            await self.backend.aclose()

    async def handle(self, action):
        r, p = self.runtime, action.payload
        if action.type == "CANCEL_TOOL_CALL":
            task = self.tasks.get(p["call_id"])
            if task:
                task.cancel()
            elif p["call_id"] not in self.started_ids:
                # A fenced dispatch never reached the backend, so cancellation is certain.
                self.send("CANCEL_ACK", call_id=p["call_id"])
            return
        if action.generation < max(self.minimum_generation, r.epoch):
            r.trace.record("STALE_OUTPUT_DISCARDED", action_id=action.action_id)
            return
        if action.type == "TOOL_CALL":
            call = r.calls.get(p["call_id"])
            if call is None or call.status != CallStatus.DISPATCHED:
                r.trace.record("STALE_OUTPUT_DISCARDED", action_id=action.action_id)
                return
            if p["call_id"] in self.started_ids:
                r.trace.record("DUPLICATE_DISPATCH_DISCARDED", call_id=p["call_id"])
                return
            self.started_ids.add(p["call_id"])
            task = asyncio.create_task(self.execute(action))
            self.tasks[p["call_id"]] = task
            def finished(done, cid=p["call_id"]):
                self.tasks.pop(cid, None)
                if done.cancelled():
                    # Also covers cancellation before execute gets its first timeslice.
                    self.send("CANCEL_ACK", call_id=cid)
                elif done.exception() is not None:
                    r.trace.record("TOOL_EXECUTION_WORKER_FAILED", call_id=cid, error=str(done.exception()))
            task.add_done_callback(finished)
        elif action.type in {"SPEAK", "CLARIFY", "FINAL"}:
            text = p["question"] if action.type == "CLARIFY" else p["text"]
            # LiveKit owns the returned speech handle. Never await playout in this pump.
            self.speak(text)
            r.trace.record("VOICE_OUTPUT_SUBMITTED", action_id=action.action_id,
                           generation=action.generation, type=action.type)

    async def execute(self, action):
        p, r = action.payload, self.runtime
        start = time.time()
        status = "started"
        r.trace.record("TOOL_EXECUTION_STARTED", call_id=p["call_id"])
        try:
            if action.generation < max(self.minimum_generation, r.epoch):
                raise asyncio.CancelledError()
            result = await self.backend.execute(p["tool_name"], p["arguments"])
            ok = not (isinstance(result, dict) and result.get("status") == "error")
            status = "success" if ok else "error"
            self.send("TOOL_RESULT", call_id=p["call_id"], ok=ok, result=result,
                      error=None if ok else str(result.get("message", "tool failed")))
        except asyncio.CancelledError:
            status = "cancelled"
            r.trace.record("TOOL_EXECUTION_CANCELLED", call_id=p["call_id"])
            raise
        except Exception as exc:
            status = "error"
            self.send("TOOL_RESULT", call_id=p["call_id"], ok=False, result={},
                      error=f"{type(exc).__name__}: {exc}")
        finally:
            record = {"room": self.room_name, "call": {"call_id": p["call_id"],
                "function": p["tool_name"], "args": p["arguments"], "status": status,
                "timestamp_start": start, "timestamp_end": time.time()}}
            r.trace.record("TOOL_EXECUTION_FINISHED", **record)
            if self.tool_log:
                await asyncio.to_thread(self.append_log, record)

    def append_log(self, record):
        self.tool_log.parent.mkdir(parents=True, exist_ok=True)
        with self.tool_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
