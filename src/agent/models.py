"""Strict local contracts. External evaluator contracts belong in protocol adapters."""
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Envelope(Model):
    session_id: str = Field(min_length=1)
    timestamp: float = Field(ge=0)


class Event(Envelope):
    event_id: str = Field(min_length=1)
    type: Literal["TEXT_CHUNK", "INTERRUPTION", "TOOL_MANIFEST", "TOOL_RESULT",
                  "AUDIO_CLIP", "VIDEO_FRAME", "CANCEL_ACK"]
    payload: dict[str, JsonValue]


class Action(Envelope):
    action_id: str = Field(min_length=1)
    generation: int = Field(default=0, ge=0)
    type: Literal["SPEAK", "CLARIFY", "TOOL_CALL", "CANCEL_TOOL_CALL", "FINAL"]
    payload: dict[str, JsonValue]


class State(Model):
    session_id: str
    version: int = Field(default=0, ge=0)
    intent: str | None = None
    slots: dict[str, JsonValue] = Field(default_factory=dict)
    pending_clarification: str | None = None


class Patch(Model):
    intent: str | None = None
    set_slots: dict[str, JsonValue] = Field(default_factory=dict)
    remove_slots: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def disjoint(self):
        if set(self.set_slots) & set(self.remove_slots):
            raise ValueError("slots cannot be set and removed together")
        return self


class ToolSpec(Model):
    name: str = Field(min_length=1)
    description: str
    argument_schema: dict[str, JsonValue]
    effect_type: Literal["READ_ONLY", "STATE_MODIFYING"]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=0, ge=0, le=3)
    retry_delay: float = Field(default=0.1, ge=0)


class ToolRequest(Model):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    # None means conservatively depend on every slot; explicit bindings are checked.
    bindings: dict[str, str] | None = None
    operation_key: str | None = None


class Proposal(Model):
    state_patch: Patch = Field(default_factory=Patch)
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    clarification: str | None = None
    final_response: str | None = None

    @model_validator(mode="after")
    def exclusive(self):
        if sum(bool(x) for x in (self.tool_requests, self.clarification, self.final_response)) > 1:
            raise ValueError("choose tools, clarification, or final, not a mixture")
        return self


class CallStatus(str, Enum):
    CREATED = "CREATED"
    DISPATCHED = "DISPATCHED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    STALE = "STALE"


class Call(Model):
    call_id: str
    request: ToolRequest
    spec: ToolSpec
    created_at: float
    state: State
    status: CallStatus = CallStatus.CREATED
    attempt: int = 0
    operation_key: str | None = None


class TraceEntry(Envelope):
    trace_id: str
    kind: str
    data: dict[str, Any]
