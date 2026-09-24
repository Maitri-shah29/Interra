"""Single owner of session mutations; slow work returns through the input mailbox."""
import asyncio
from dataclasses import dataclass

from .models import Action, Call, CallStatus, Event
from .planner import propose
from .protocol import LocalProtocol
from .providers.base import PlanningContext
from .state import StateManager
from .tools.registry import ToolRegistry
from .trace import IDs, TraceRecorder
from .scheduler import ToolScheduler, Timer
from jsonschema.exceptions import ValidationError as SchemaError
from jsonschema.exceptions import SchemaError as InvalidSchema
from .coordinator import compatible
from .safety import SafetyLedger
from .floor import FloorManager
from .multimodal.fusion import PerceptionManager, PerceptionResult, PerceptionDeadline


@dataclass
class Completion:
    token: int
    value: object = None
    error: str | None = None


@dataclass
class PlannerDeadline:
    token: int


class SessionRuntime:
    def __init__(self, session_id, provider, clock, input_queue=None, output_queue=None,
                 planner_repairs=0, planner_timeout=60., audio_provider=None, vision_provider=None,
                 perception_timeout=60., media_loader=None, retain_frame_context=False):
        self.session_id, self.provider, self.clock = session_id, provider, clock
        self.input = input_queue if input_queue is not None else asyncio.Queue()
        self.output = output_queue if output_queue is not None else asyncio.Queue()
        self.ids = IDs(session_id)
        self.trace = TraceRecorder(session_id, clock, self.ids)
        self.protocol = LocalProtocol()
        self.state = StateManager(session_id)
        self.registry = ToolRegistry()
        self.scheduler = ToolScheduler(self)
        self.ledger = SafetyLedger()
        self.calls = self.scheduler.calls
        self.results = {}
        self.tasks = set()
        self.planner_task = None
        self.planner_timer = None
        self.active_plan_token = None
        self.planner_repairs = planner_repairs
        self.planner_timeout = planner_timeout
        self.perception_timeout = perception_timeout
        self.media_loader = media_loader
        self.retain_frame_context = retain_frame_context
        self.token = 0
        self.chunks = []
        self.seen = set()
        self.last_input = {}
        self.text_input = {}
        self.floor = FloorManager()
        self.user_pending = False
        self.deferred_retries = set()
        self.epoch = 0
        self.latest_user_timestamp = None
        self.perception = PerceptionManager(self, audio_provider, vision_provider)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def emit(self, kind, *, generation=None, **payload):
        action = Action(action_id=self.ids.new("action"), session_id=self.session_id,
                        timestamp=self.clock.now(), type=kind, payload=payload,
                        generation=self.epoch if generation is None else generation)
        self.protocol.encode(action)
        self.trace.record("ACTION_EMITTED", action=action.model_dump(mode="json"))
        self.output.put_nowait(action)

    async def run(self):
        try:
            while True:
                item = await self.input.get()
                try:
                    if item is None:
                        break
                    if isinstance(item, Completion):
                        self.complete(item)
                    elif isinstance(item, Timer):
                        self.scheduler.timer(item)
                    elif isinstance(item, PerceptionResult):
                        self.perception.complete(item)
                    elif isinstance(item, PerceptionDeadline):
                        self.perception.timeout(item)
                    elif isinstance(item, PlannerDeadline):
                        if item.token == self.active_plan_token:
                            self.active_plan_token = None
                            self.token += 1
                            if self.planner_task:
                                self.planner_task.cancel()
                            self.trace.record("PLANNER_TIMED_OUT", token=item.token)
                            self.emit("CLARIFY", question="Reasoning took too long. Please restate the next step.")
                    else:
                        try:
                            event = self.protocol.decode(item.model_dump() if isinstance(item, Event) else item)
                            self.handle(event)
                        except (ValueError, TypeError, KeyError, SchemaError, InvalidSchema) as exc:
                            self.trace.record("INPUT_REJECTED", error=str(exc))
                finally:
                    self.input.task_done()
        finally:
            tasks = list(self.tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.trace.record("SESSION_CLOSED", state=self.state.snapshot.model_dump())

    def handle(self, event):
        if event.session_id != self.session_id:
            self.trace.record("INPUT_REJECTED", event_id=event.event_id, error="wrong session")
            return
        if event.event_id in self.seen:
            self.trace.record("DUPLICATE_EVENT", event_id=event.event_id)
            return
        self.seen.add(event.event_id)
        self.trace.record("INPUT_RECEIVED", event=event.model_dump())
        self.trace.record(
            "INPUT_QUEUE_LATENCY",
            event_id=event.event_id,
            source_timestamp=event.timestamp,
            latency=self.clock.now() - event.timestamp,
        )
        if event.type in {
            "TEXT_CHUNK",
            "INTERRUPTION",
            "AUDIO_CLIP",
            "VIDEO_FRAME",
        }:
            if self.latest_user_timestamp is not None and event.timestamp < self.latest_user_timestamp:
                self.trace.record("STALE_INPUT_DISCARDED", event_id=event.event_id,
                                  source_timestamp=event.timestamp, watermark=self.latest_user_timestamp)
                return
            self.latest_user_timestamp = event.timestamp
        acknowledgment = self.floor.acknowledge(event)
        if acknowledgment:
            advances = (event.type == "INTERRUPTION" or
                        (event.type == "TEXT_CHUNK" and not self.chunks) or
                        (event.type == "AUDIO_CLIP" and self.retain_frame_context and
                         not self.perception.audio_turn_open))
            self.emit("SPEAK", text=acknowledgment, generation=self.epoch + int(advances))
            self.trace.record("FIRST_RESPONSE_LATENCY", event_id=event.event_id,
                              source_timestamp=event.timestamp, latency=self.clock.now() - event.timestamp)
        if event.type == "TOOL_MANIFEST":
            before = self.registry.specs
            self.registry.replace(event.payload["tools"])
            self.trace.record("MANIFEST_UPDATED")
            if before != self.registry.specs:
                current = {s.name: s for s in self.registry.specs}
                for call in list(self.calls.values()):
                    if current.get(call.spec.name) != call.spec:
                        self.scheduler.invalidate(call, "manifest changed")
                if self.active_plan_token is not None:
                    self.cancel_planning()
                    self.emit("CLARIFY", question="The available tools changed. Please restate the next step.")
        elif event.type == "TEXT_CHUNK":
            if not self.chunks:
                self.epoch += 1
                self.perception.invalidate(preserve_vision=self.retain_frame_context)
                self.user_pending = True
                self.cancel_planning()
            self.chunks.append(event.payload["text"])
            if event.payload["end_of_turn"]:
                self.text_input = {"text": "".join(self.chunks), "event_id": event.event_id}
                self.chunks.clear()
                self.perception.refresh_context()
                self.start_plan()
        elif event.type == "TOOL_RESULT":
            self.result(event.payload)
        elif event.type == "INTERRUPTION":
            self.epoch += 1
            self.perception.invalidate()
            self.cancel_planning()
            self.user_pending = True
            self.chunks.clear()
            self.text_input = {}
            self.scheduler.reconcile(self.state.snapshot, all_calls=True)
            self.trace.record("INTERRUPTED", event_id=event.event_id)
            if event.payload.get("text"):
                self.text_input = {"text": event.payload["text"], "event_id": event.event_id}
                self.perception.refresh_context()
                self.start_plan()
        elif event.type in {"AUDIO_CLIP", "VIDEO_FRAME"}:
            if (event.type == "AUDIO_CLIP" and self.retain_frame_context
                    and not self.perception.audio_turn_open):
                self.epoch += 1
                self.perception.invalidate(preserve_vision=True)
                self.text_input = {}
                self.chunks.clear()
            self.cancel_planning()
            self.user_pending = True
            self.perception.start(event)
        elif event.type == "CANCEL_ACK":
            call = self.calls.get(event.payload["call_id"])
            if call and call.status == CallStatus.CANCEL_REQUESTED:
                self.calls[call.call_id] = call.model_copy(update={"status": CallStatus.CANCELLED})
                self.trace.record("CANCEL_ACKNOWLEDGED", call_id=call.call_id)
            else:
                self.trace.record("CANCEL_ACK_IGNORED", call_id=event.payload["call_id"])
        else:
            self.trace.record("UNSUPPORTED_INPUT", event_id=event.event_id)

    def cancel_planning(self):
        self.token += 1
        self.active_plan_token = None
        if self.planner_task and not self.planner_task.done():
            self.planner_task.cancel()
        if self.planner_timer:
            self.planner_timer.cancel()

    def start_plan(self):
        self.cancel_planning()
        token = self.token
        self.active_plan_token = token
        context = PlanningContext(state=self.state.snapshot, input=self.last_input,
                                  tools=self.registry.specs, results=self.results)
        self.trace.record("PLANNER_STARTED", token=token, version=context.state.version)
        async def work():
            try:
                value = await propose(self.provider, context, repairs=self.planner_repairs,
                    on_invalid=lambda attempt, error: self.trace.record("MODEL_SCHEMA_REJECTED", attempt=attempt, error=error))
                self.input.put_nowait(Completion(token, value))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.input.put_nowait(Completion(token, error=str(exc)))
        self.planner_task = self.spawn(work())
        deadline = self.clock.now() + self.planner_timeout
        async def timeout():
            remaining = deadline - self.clock.now()
            if remaining > 0:
                await self.clock.sleep(remaining)
            self.input.put_nowait(PlannerDeadline(token))
        self.planner_timer = self.spawn(timeout())

    def complete(self, completion):
        if completion.token != self.active_plan_token:
            self.trace.record("STALE_PLAN_DISCARDED", token=completion.token)
            return
        self.active_plan_token = None
        if self.planner_timer:
            self.planner_timer.cancel()
        if completion.error is not None:
            self.trace.record("PLANNER_FAILED", error=completion.error)
            self.emit("CLARIFY", question="I could not interpret that request safely. What should I do next?")
            return
        proposal = completion.value
        try:
            candidate, _, _ = self.state.preview(proposal.state_patch)
            specs = [self.registry.validate(req, candidate) for req in proposal.tool_requests]
        except Exception as exc:
            self.trace.record("PROPOSAL_REJECTED", error=str(exc))
            self.emit("CLARIFY", question="The proposed tool arguments were invalid. Please clarify the request.")
            return
        self.trace.record("PROPOSAL_ACCEPTED", proposal=proposal.model_dump())
        old = self.state.snapshot
        state, changed, switched = self.state.apply(proposal.state_patch)
        if state.version != old.version:
            self.trace.record("STATE_UPDATED", before=old.model_dump(), after=state.model_dump(),
                              changed=sorted(changed), intent_changed=switched)
        self.scheduler.reconcile(state)
        self.user_pending = bool(self.chunks) or self.perception.unresolved
        for call_id in list(self.deferred_retries):
            self.deferred_retries.discard(call_id)
            self.scheduler.timer(Timer(call_id, "retry"))
        for request, spec in zip(proposal.tool_requests, specs):
            self.dispatch(request, spec)
        if proposal.clarification:
            self.emit("CLARIFY", question=proposal.clarification)
        if proposal.final_response:
            if self.perception.unresolved or self.chunks:
                self.trace.record("FINAL_DEFERRED_PERCEPTION")
            elif self.ledger.uncertain:
                self.trace.record("FINAL_BLOCKED_UNCERTAIN")
                self.emit("CLARIFY", question="A previous change may have completed. Please verify its outcome before another change.")
            elif any(c.status == CallStatus.DISPATCHED for c in self.calls.values()):
                self.trace.record("FINAL_DEFERRED")
            elif any(c.state.intent == state.intent for c in self.calls.values()) and not self.results:
                self.trace.record("FINAL_BLOCKED_NO_EVIDENCE")
                self.emit("CLARIFY", question="No current tool result supports completion. What should I check next?")
            else:
                self.emit("FINAL", text=proposal.final_response,
                          state_snapshot={"intent": state.intent, "slots": state.slots})

    def dispatch(self, request, spec):
        return self.scheduler.dispatch(request, spec)

    def result(self, payload):
        call = self.calls.get(payload["call_id"])
        if call is None:
            self.trace.record("UNKNOWN_RESULT", call_id=payload["call_id"])
            return
        if call.status in {CallStatus.STALE, CallStatus.CANCEL_REQUESTED, CallStatus.CANCELLED} or not compatible(call, self.state.snapshot):
            if call.operation_key and payload["ok"]:
                self.ledger.update(call.operation_key, "COMMITTED")
                self.trace.record("STALE_WRITE_RECONCILED", call_id=call.call_id, operation_key=call.operation_key)
            self.trace.record("STALE_RESULT_DISCARDED", call_id=call.call_id)
            return
        if call.status != CallStatus.DISPATCHED:
            if call.operation_key and payload["ok"] and call.status in {CallStatus.TIMED_OUT, CallStatus.FAILED}:
                self.ledger.update(call.operation_key, "COMMITTED")
                self.trace.record("LATE_WRITE_RECONCILED", call_id=call.call_id, operation_key=call.operation_key)
            self.trace.record("DUPLICATE_RESULT_DISCARDED", call_id=call.call_id)
            return
        self.scheduler.disarm(call.call_id)
        if not payload["ok"]:
            self.scheduler.failure(call, CallStatus.FAILED, payload.get("error"))
            return
        self.calls[call.call_id] = call.model_copy(update={"status": CallStatus.COMPLETED})
        self.ledger.update(call.operation_key, "COMMITTED")
        self.results[call.call_id] = payload
        self.trace.record("RESULT_ACCEPTED", call_id=call.call_id, result=payload)
        if not self.user_pending:
            self.start_plan()
        else:
            self.trace.record("RESULT_PLANNING_DEFERRED", call_id=call.call_id)
