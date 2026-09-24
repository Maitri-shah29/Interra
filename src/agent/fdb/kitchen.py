"""Extension: real, session-scoped kitchen timers with owned expiry tasks."""
import asyncio
from copy import deepcopy
from ..models import ToolSpec


def spec(name, description, effect, properties, required):
    return ToolSpec(name=name, description=description, effect_type=effect,
        argument_schema={"type": "object", "properties": properties, "required": required,
                         "additionalProperties": False})


class KitchenBackend:
    def __init__(self, clock, notify, trace):
        self.clock, self.notify, self.trace = clock, notify, trace
        self.timers, self.tasks = {}, {}
        self.sequence = 0
        self.specs = [
            spec("start_kitchen_timer", "Start a real kitchen timer. Keep the returned timer_id. "
                 "To change a timer, cancel its ID first, then start the replacement.", "STATE_MODIFYING",
                 {"label": {"type": "string", "minLength": 1},
                  "seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 86400}}, ["label", "seconds"]),
            spec("list_kitchen_timers", "List this session's actual timers and remaining seconds.",
                 "READ_ONLY", {}, []),
            spec("cancel_kitchen_timer", "Cancel a timer by the timer_id returned by start or list.",
                 "STATE_MODIFYING", {"timer_id": {"type": "string"}}, ["timer_id"])]

    async def execute(self, name, args):
        if name == "start_kitchen_timer":
            self.sequence += 1
            timer_id = f"timer-{self.sequence}"
            timer = {"timer_id": timer_id, "label": args["label"],
                     "deadline": self.clock.now() + args["seconds"], "status": "running"}
            self.timers[timer_id] = timer
            self.trace.record("KITCHEN_TIMER_STARTED", **timer)
            async def expire():
                await self.clock.sleep(max(0, timer["deadline"] - self.clock.now()))
                timer["status"] = "finished"
                self.trace.record("KITCHEN_TIMER_FINISHED", **timer)
                self.notify(f"Your {timer['label']} timer has finished.")
            task = asyncio.create_task(expire())
            self.tasks[timer_id] = task
            def finished(done):
                self.tasks.pop(timer_id, None)
                if not done.cancelled() and done.exception() is not None:
                    self.trace.record("KITCHEN_NOTIFICATION_FAILED", timer_id=timer_id,
                                      error=str(done.exception()))
            task.add_done_callback(finished)
            return {"status": "success", **deepcopy(timer)}
        if name == "list_kitchen_timers":
            return {"status": "success", "timers": [{**deepcopy(t),
                "remaining_seconds": max(0, t["deadline"] - self.clock.now()) if t["status"] == "running" else 0}
                for t in self.timers.values()]}
        if name == "cancel_kitchen_timer":
            timer = self.timers.get(args["timer_id"])
            if timer is None:
                return {"status": "error", "message": "unknown timer"}
            task = self.tasks.pop(args["timer_id"], None)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            timer["status"] = "cancelled"
            self.trace.record("KITCHEN_TIMER_CANCELLED", **timer)
            return {"status": "success", **deepcopy(timer)}
        raise ValueError("unknown kitchen tool")

    async def aclose(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
