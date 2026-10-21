"""A SmartThings-style home whose devices become the voice agent's tools.

Devices are described the way SmartThings describes them: a device lists
capabilities, a capability lists attributes (values that can be read) and
commands (actions that change state). Interra turns those descriptions into
tools when a session starts, and again whenever a device is added, so a
device the agent has never seen works as soon as its description arrives.

The capability definitions follow the names in SmartThings' public capability
model; the devices themselves are simulated for the demo. Nothing here talks
to a real SmartThings account.

Every command that changes state goes through the coordination rules the
benchmark agent proves:

* the commit gate holds it until the person has been quiet; if they said more
  meanwhile, the model re-checks it against their complete words and it is
  dropped only if they corrected or cancelled it;
* the latest value wins: a newer value for the same device and capability
  replaces one still waiting at the gate;
* a value that is already set is not sent again;
* risky commands (unlocking a door, starting an oven) need a spoken yes;
* the last reversible change can be undone by voice.
"""
from __future__ import annotations

import copy
import itertools
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from agent.commit import RECHECK_TIMEOUT_SECONDS, CommitGate, HeldWords, Recheck, Verdict, recheck_call

READ_ONLY = "READ_ONLY"
STATE_MODIFYING = "STATE_MODIFYING"
UNDO_SECONDS = 120.0
CONFIRM_SECONDS = 60.0
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,47}$")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,47}$")


# ---------- the capability model ----------
#
# Each capability: attributes, each with a JSON Schema for its value, and
# commands. A command names the tool it becomes, its arguments (JSON Schema)
# and what it sets: an attribute to a fixed value, or to "$argument".
# "risk" is "routine" or "confirm"; "undo" says whether undo may reverse it.

CORE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "switch": {
        "attributes": {"switch": {"type": "string", "enum": ["on", "off"]}},
        "commands": {
            "on": {"tool": "turn_on", "description": "Turn a device on.", "sets": {"switch": "on"}},
            "off": {"tool": "turn_off", "description": "Turn a device off.", "sets": {"switch": "off"}},
        },
    },
    "switchLevel": {
        "attributes": {"level": {"type": "integer", "minimum": 0, "maximum": 100, "unit": "%"}},
        "commands": {
            "setLevel": {
                "tool": "set_brightness",
                "description": "Set a light's brightness in percent.",
                "arguments": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                "sets": {"level": "$level"},
            },
        },
    },
    "thermostatCoolingSetpoint": {
        "attributes": {"coolingSetpoint": {"type": "number", "minimum": 16, "maximum": 30, "unit": "°C"}},
        "commands": {
            "setCoolingSetpoint": {
                "tool": "set_temperature",
                "description": "Set an air conditioner's target temperature in degrees Celsius.",
                "arguments": {"setpoint": {"type": "number", "minimum": 16, "maximum": 30}},
                "sets": {"coolingSetpoint": "$setpoint"},
            },
        },
    },
    "airConditionerMode": {
        "attributes": {"airConditionerMode": {"type": "string", "enum": ["auto", "cool", "dry", "wind", "heat"]}},
        "commands": {
            "setAirConditionerMode": {
                "tool": "set_ac_mode",
                "description": "Set an air conditioner's mode.",
                "arguments": {"mode": {"type": "string", "enum": ["auto", "cool", "dry", "wind", "heat"]}},
                "sets": {"airConditionerMode": "$mode"},
            },
        },
    },
    "temperatureMeasurement": {
        "attributes": {"temperature": {"type": "number", "unit": "°C"}},
        "commands": {},
    },
    "robotCleanerMovement": {
        "attributes": {"robotCleanerMovement": {"type": "string", "enum": ["cleaning", "homing", "idle", "charging", "pause"]}},
        "commands": {
            "setRobotCleanerMovement": {
                "tool": "set_vacuum",
                "description": "Start, pause or send home a robot vacuum.",
                "arguments": {"movement": {"type": "string", "enum": ["cleaning", "homing", "pause"]}},
                "sets": {"robotCleanerMovement": "$movement"},
            },
        },
    },
    "washerOperatingState": {
        "attributes": {"machineState": {"type": "string", "enum": ["run", "pause", "stop"]}},
        "commands": {
            "setMachineState": {
                "tool": "set_washer",
                "description": "Run, pause or stop the washer.",
                "arguments": {"state": {"type": "string", "enum": ["run", "pause", "stop"]}},
                "sets": {"machineState": "$state"},
            },
        },
    },
    "ovenOperatingState": {
        "attributes": {"ovenState": {"type": "string", "enum": ["ready", "running"]}},
        "commands": {
            "start": {
                "tool": "start_oven", "description": "Start heating the oven.",
                "sets": {"ovenState": "running"}, "risk": "confirm", "undo": False,
            },
            "stop": {"tool": "stop_oven", "description": "Stop the oven.", "sets": {"ovenState": "ready"}},
        },
    },
    "ovenSetpoint": {
        "attributes": {"ovenSetpoint": {"type": "integer", "minimum": 50, "maximum": 250, "unit": "°C"}},
        "commands": {
            "setOvenSetpoint": {
                "tool": "set_oven_temperature",
                "description": "Set the oven's target temperature in degrees Celsius.",
                "arguments": {"setpoint": {"type": "integer", "minimum": 50, "maximum": 250}},
                "sets": {"ovenSetpoint": "$setpoint"},
            },
        },
    },
    "lock": {
        "attributes": {"lock": {"type": "string", "enum": ["locked", "unlocked"]}},
        "commands": {
            "lock": {"tool": "lock_door", "description": "Lock a door.", "sets": {"lock": "locked"}, "undo": False},
            "unlock": {
                "tool": "unlock_door", "description": "Unlock a door.",
                "sets": {"lock": "unlocked"}, "risk": "confirm", "undo": False,
            },
        },
    },
    "audioVolume": {
        "attributes": {"volume": {"type": "integer", "minimum": 0, "maximum": 100, "unit": "%"}},
        "commands": {
            "setVolume": {
                "tool": "set_volume",
                "description": "Set a speaker or TV's volume in percent.",
                "arguments": {"volume": {"type": "integer", "minimum": 0, "maximum": 100}},
                "sets": {"volume": "$volume"},
            },
        },
    },
}

# What one device looks like in this home, as SmartThings lists it.
SAMPLE_HOME: list[dict[str, Any]] = [
    {"id": "living-room-ac", "label": "Living room AC", "room": "Living room", "category": "AirConditioner",
     "capabilities": ["switch", "airConditionerMode", "thermostatCoolingSetpoint", "temperatureMeasurement"],
     "state": {"switch": "off", "airConditionerMode": "cool", "coolingSetpoint": 26, "temperature": 29.5}},
    {"id": "living-room-lights", "label": "Living room lights", "room": "Living room", "category": "Light",
     "capabilities": ["switch", "switchLevel"], "state": {"switch": "on", "level": 80}},
    {"id": "bedroom-lamp", "label": "Bedroom lamp", "room": "Bedroom", "category": "Light",
     "capabilities": ["switch", "switchLevel"], "state": {"switch": "on", "level": 40}},
    {"id": "living-room-tv", "label": "Living room TV", "room": "Living room", "category": "Television",
     "capabilities": ["switch", "audioVolume"], "state": {"switch": "off", "volume": 18}},
    {"id": "robot-vacuum", "label": "Robot vacuum", "room": "Hallway", "category": "RobotCleaner",
     "capabilities": ["robotCleanerMovement"], "state": {"robotCleanerMovement": "charging"}},
    {"id": "washer", "label": "Washer", "room": "Laundry", "category": "Washer",
     "capabilities": ["washerOperatingState"], "state": {"machineState": "stop"}},
    {"id": "oven", "label": "Oven", "room": "Kitchen", "category": "Oven",
     "capabilities": ["ovenOperatingState", "ovenSetpoint"], "state": {"ovenState": "ready", "ovenSetpoint": 180}},
    {"id": "front-door", "label": "Front door", "room": "Entrance", "category": "SmartLock",
     "capabilities": ["lock"], "state": {"lock": "locked"}},
]

# The shape a capability definition must have before Interra accepts it.
_VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"enum": ["string", "integer", "number", "boolean"]},
        "enum": {"type": "array", "minItems": 1, "maxItems": 24, "items": {"type": ["string", "number", "boolean"]}},
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "unit": {"type": "string", "maxLength": 12},
    },
    "required": ["type"],
    "additionalProperties": False,
}
CAPABILITY_SCHEMA = Draft202012Validator({
    "type": "object",
    "properties": {
        "attributes": {
            "type": "object", "minProperties": 1, "maxProperties": 12,
            "additionalProperties": _VALUE_SCHEMA,
        },
        "commands": {
            "type": "object", "maxProperties": 12,
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "pattern": TOOL_NAME.pattern},
                    "description": {"type": "string", "minLength": 3, "maxLength": 200},
                    "arguments": {"type": "object", "maxProperties": 4, "additionalProperties": _VALUE_SCHEMA},
                    "sets": {"type": "object", "minProperties": 1, "maxProperties": 6},
                    "risk": {"enum": ["routine", "confirm"]},
                    "undo": {"type": "boolean"},
                },
                "required": ["tool", "description", "sets"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["attributes", "commands"],
    "additionalProperties": False,
})
DEVICE_SCHEMA = Draft202012Validator({
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9-]{1,47}$"},
        "label": {"type": "string", "minLength": 2, "maxLength": 48},
        "room": {"type": "string", "minLength": 1, "maxLength": 48},
        "category": {"type": "string", "pattern": NAME.pattern},
        "capabilities": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string", "pattern": NAME.pattern}},
        "state": {"type": "object"},
        "capabilityDefinitions": {"type": "object", "maxProperties": 8},
    },
    "required": ["id", "label", "room", "capabilities", "state"],
    "additionalProperties": False,
})


def _value_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """The JSON Schema for one value, without presentation keys such as unit."""
    return {key: value for key, value in spec.items() if key != "unit"}


@dataclass(frozen=True)
class ToolDef:
    """One tool generated from the device descriptions."""

    name: str
    description: str
    parameters: dict[str, Any]
    effect: str
    capability: str | None = None
    command: str | None = None
    risk: str = "routine"

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": copy.deepcopy(self.parameters)}


@dataclass
class Device:
    id: str
    label: str
    room: str
    category: str
    capabilities: list[str]
    state: dict[str, Any]

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "room": self.room, "category": self.category,
            "capabilities": list(self.capabilities), "state": copy.deepcopy(self.state),
        }


@dataclass
class Change:
    """One applied command, kept so it can be undone."""

    at: float
    device_id: str
    tool: str
    arguments: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    undoable: bool


@dataclass
class Pending:
    """A risky command waiting for the person's yes."""

    id: str
    tool: str
    arguments: dict[str, Any]
    device_id: str
    asked_at: float


class HomeError(ValueError):
    """A device or capability description that Interra will not accept."""


def _snake(text: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()


class SmartHome:
    """One session's home: devices, capabilities and the tools they become."""

    def __init__(self, devices: list[dict[str, Any]] | None = None,
                 capabilities: dict[str, dict[str, Any]] | None = None) -> None:
        self.capabilities: dict[str, dict[str, Any]] = {}
        for name, definition in (capabilities or CORE_CAPABILITIES).items():
            self._register_capability(name, definition)
        self.devices: dict[str, Device] = {}
        for raw in copy.deepcopy(devices if devices is not None else SAMPLE_HOME):
            self.add_device(raw)

    # ----- descriptions -----

    def _register_capability(self, name: str, definition: dict[str, Any]) -> None:
        if not NAME.match(name):
            raise HomeError(f"capability name {name!r} is not valid")
        try:
            CAPABILITY_SCHEMA.validate(definition)
        except ValidationError as exc:
            raise HomeError(f"capability {name}: {exc.message}") from exc
        known = {command["tool"] for capability in self.capabilities.values() for command in capability["commands"].values()}
        for command_name, command in definition["commands"].items():
            if command["tool"] in known or command["tool"] in _FIXED_TOOLS:
                raise HomeError(f"tool name {command['tool']} is already used")
            known.add(command["tool"])
            arguments = command.get("arguments", {})
            for attribute, value in command["sets"].items():
                if attribute not in definition["attributes"]:
                    raise HomeError(f"{name}.{command_name} sets unknown attribute {attribute}")
                if isinstance(value, str) and value.startswith("$") and value[1:] not in arguments:
                    raise HomeError(f"{name}.{command_name} uses unknown argument {value}")
        self.capabilities[name] = copy.deepcopy(definition)

    def add_device(self, raw: dict[str, Any]) -> Device:
        """Accept a device description, and any capability definitions it brings."""
        try:
            DEVICE_SCHEMA.validate(raw)
        except ValidationError as exc:
            raise HomeError(f"device: {exc.message}") from exc
        if raw["id"] in self.devices:
            raise HomeError(f"device {raw['id']} already exists")
        if any(device.label.lower() == raw["label"].lower() for device in self.devices.values()):
            raise HomeError(f"a device is already called {raw['label']}")
        for name, definition in raw.get("capabilityDefinitions", {}).items():
            if name in self.capabilities:
                if self.capabilities[name] != definition:
                    raise HomeError(f"capability {name} is already defined differently")
                continue
            self._register_capability(name, definition)
        state: dict[str, Any] = {}
        for capability in raw["capabilities"]:
            if capability not in self.capabilities:
                raise HomeError(f"capability {capability} is not defined")
            for attribute, spec in self.capabilities[capability]["attributes"].items():
                if attribute not in raw["state"]:
                    raise HomeError(f"{raw['label']} has no value for {attribute}")
                try:
                    Draft202012Validator(_value_schema(spec)).validate(raw["state"][attribute])
                except ValidationError as exc:
                    raise HomeError(f"{raw['label']} {attribute}: {exc.message}") from exc
                state[attribute] = raw["state"][attribute]
        device = Device(raw["id"], raw["label"], raw["room"], raw.get("category", "Device"),
                        list(raw["capabilities"]), state)
        self.devices[device.id] = device
        return device

    def find(self, name: str) -> Device | None:
        key = str(name).strip().lower()
        for device in self.devices.values():
            if key in (device.id, device.label.lower()):
                return device
        return None

    def views(self) -> list[dict[str, Any]]:
        return [device.view() for device in self.devices.values()]

    # ----- tools -----

    def manifest(self) -> list[ToolDef]:
        """Tools for the current devices: one per command, plus fixed helpers."""
        labels = [device.label for device in self.devices.values()]
        tools = [
            ToolDef("list_devices", "List every device in the home with its room and current state.",
                    {"type": "object", "properties": {}, "additionalProperties": False}, READ_ONLY),
            ToolDef("get_device_status", "Read one device's current state. Use it to answer any question about a device.",
                    {"type": "object", "properties": {"device": {"type": "string", "enum": labels}},
                     "required": ["device"], "additionalProperties": False}, READ_ONLY),
        ]
        for capability_name, capability in self.capabilities.items():
            owners = [device.label for device in self.devices.values() if capability_name in device.capabilities]
            if not owners:
                continue
            for command_name, command in capability["commands"].items():
                properties: dict[str, Any] = {"device": {"type": "string", "enum": owners}}
                for argument, spec in command.get("arguments", {}).items():
                    properties[argument] = _value_schema(spec)
                risk = command.get("risk", "routine")
                note = " Asks the person to confirm first." if risk == "confirm" else ""
                tools.append(ToolDef(
                    command["tool"], command["description"] + note,
                    {"type": "object", "properties": properties, "required": list(properties),
                     "additionalProperties": False},
                    STATE_MODIFYING, capability_name, command_name, risk,
                ))
        tools += [
            ToolDef("confirm_action", "Carry out a risky action after the person clearly says yes.",
                    {"type": "object", "properties": {"confirmation_id": {"type": "string"}},
                     "required": ["confirmation_id"], "additionalProperties": False}, STATE_MODIFYING),
            ToolDef("cancel_action", "Drop a risky action the person declined.",
                    {"type": "object", "properties": {"confirmation_id": {"type": "string"}},
                     "required": ["confirmation_id"], "additionalProperties": False}, READ_ONLY),
            ToolDef("undo_last_change", "Reverse the most recent change, when the person says undo or put it back.",
                    {"type": "object", "properties": {}, "additionalProperties": False}, STATE_MODIFYING),
        ]
        return tools


_FIXED_TOOLS = {"list_devices", "get_device_status", "confirm_action", "cancel_action", "undo_last_change"}


def _superseded(reason: str) -> dict[str, Any]:
    return {
        "status": "superseded",
        "message": reason + " It was not carried out. Act on the person's latest words and call again if "
                   "they still want it; do not say it happened.",
    }


class HomeController:
    """Runs tool calls against one session's home under the coordination rules.

    ``trace`` needs ``append(kind, **payload)``. Records reuse the benchmark
    agent's kinds (``tool_call_pending``, ``tool_call_dropped``, ``tool_call``)
    so the judge page's gate panel shows home commands too.
    """

    def __init__(self, home: SmartHome, gate: CommitGate, trace: Any, room: str,
                 clock: Any = time.monotonic, *, recheck: Recheck | None = None,
                 recheck_timeout: float = RECHECK_TIMEOUT_SECONDS) -> None:
        self.home = home
        self.gate = gate
        self.recheck = recheck
        self.recheck_timeout = recheck_timeout
        self.trace = trace
        self.room = room
        self._clock = clock
        self._tools = {tool.name: tool for tool in home.manifest()}
        self._validators = {name: Draft202012Validator(tool.parameters) for name, tool in self._tools.items()}
        self._waiting: dict[tuple[str, str], int] = {}
        self._tokens = itertools.count(1)
        self._confirm_ids = itertools.count(1)
        self.pending: dict[str, Pending] = {}
        self.history: list[Change] = []

    # ----- tool list -----

    def refresh(self) -> list[ToolDef]:
        manifest = self.home.manifest()
        self._tools = {tool.name: tool for tool in manifest}
        self._validators = {name: Draft202012Validator(tool.parameters) for name, tool in self._tools.items()}
        return manifest

    def tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def add_device(self, raw: dict[str, Any]) -> tuple[Device, list[str]]:
        """Add a device mid-session; returns it and the names of the new tools."""
        before = set(self._tools)
        device = self.home.add_device(raw)
        added = [tool.name for tool in self.refresh() if tool.name not in before]
        self.trace.append("home_device_added", room=self.room, device=device.view(), new_tools=added)
        self.publish()
        return device, added

    def publish(self) -> None:
        self.trace.append("home_devices", room=self.room, devices=self.home.views())

    # ----- calls -----

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = dict(arguments or {})
        tool = self._tools.get(name)
        if tool is None:
            return {"status": "invalid", "message": f"There is no tool called {name}."}
        try:
            self._validators[name].validate(arguments)
        except ValidationError as exc:
            return {"status": "invalid", "message": exc.message}
        if name == "list_devices":
            return {"status": "ok", "devices": [self._summary(device) for device in self.home.devices.values()]}
        if name == "get_device_status":
            return {"status": "ok", **self._summary(self.home.find(arguments["device"]))}
        if name == "cancel_action":
            return self._cancel(arguments["confirmation_id"])
        if name == "confirm_action":
            return await self._confirm(arguments["confirmation_id"])
        if name == "undo_last_change":
            return await self._undo()
        return await self._command(tool, arguments)

    def _summary(self, device: Device | None) -> dict[str, Any]:
        if device is None:
            return {"device": None}
        return {"device": device.label, "room": device.room, "state": copy.deepcopy(device.state)}

    def _targets(self, tool: ToolDef, arguments: dict[str, Any]) -> dict[str, Any]:
        command = self.home.capabilities[tool.capability]["commands"][tool.command]
        return {
            attribute: arguments[value[1:]] if isinstance(value, str) and value.startswith("$") else value
            for attribute, value in command["sets"].items()
        }

    async def _command(self, tool: ToolDef, arguments: dict[str, Any], *, confirmed: bool = False) -> dict[str, Any]:
        device = self.home.find(arguments["device"])
        if device is None:
            return {"status": "invalid", "message": f"There is no device called {arguments['device']}."}
        targets = self._targets(tool, arguments)
        call = {"function": tool.name, "args": arguments}
        if all(device.state.get(key) == value for key, value in targets.items()):
            self.trace.append("home_command_skipped", room=self.room, call=call, reason="already set")
            return {"status": "already", "device": device.label, "state": copy.deepcopy(device.state),
                    "message": "Nothing was sent; it is already set that way."}
        if tool.risk == "confirm" and not confirmed:
            pending = Pending(f"c{next(self._confirm_ids)}", tool.name, arguments, device.id, self._clock())
            self.pending[pending.id] = pending
            self.trace.append("confirmation_requested", room=self.room, call=call, confirmation_id=pending.id)
            return {"status": "needs_confirmation", "confirmation_id": pending.id,
                    "message": f"Ask the person to confirm: {tool.description.split('.')[0].lower()} for {device.label}."}

        key = (device.id, tool.capability or tool.name)
        token = next(self._tokens)
        self._waiting[key] = token
        mark = self.gate.mark()
        self.trace.append("tool_call_pending", room=self.room, call=call, quiet_seconds=self.gate.quiet_seconds)
        verdict: Verdict | None = None
        try:
            words = await self.gate.hold(mark)
            # A newer value can still arrive while the re-check runs; it wins.
            if words.changed and self._waiting.get(key) == token:
                verdict = await self._recheck(tool.name, arguments, words)
        finally:
            latest = self._waiting.get(key) == token
            if latest:
                del self._waiting[key]
        if not latest:
            self.trace.append("tool_call_dropped", room=self.room, call=call, reason="replaced by a newer value")
            return _superseded("A newer value for this device replaced it.")
        if verdict is not None and not verdict.run:
            self.trace.append("tool_call_dropped", room=self.room, call=call, reason=verdict.reason)
            return _superseded("The person said more and " + verdict.reason + ".")
        return self._apply(device, tool, arguments, targets)

    async def _recheck(self, name: str, arguments: dict[str, Any], words: HeldWords) -> Verdict:
        """Re-check a held command against the person's complete words; trace the decision."""
        verdict = await recheck_call(self.recheck, name, arguments, words, timeout=self.recheck_timeout)
        self.trace.append(
            "tool_call_rechecked", room=self.room, call={"function": name, "args": arguments},
            earlier=" ".join(words.earlier), later=" ".join(words.later), run=verdict.run, reason=verdict.reason,
        )
        return verdict

    def _apply(self, device: Device, tool: ToolDef, arguments: dict[str, Any], targets: dict[str, Any]) -> dict[str, Any]:
        before = {key: device.state.get(key) for key in targets}
        device.state.update(targets)
        command = self.home.capabilities[tool.capability]["commands"][tool.command] if tool.capability else {}
        self.history.append(Change(self._clock(), device.id, tool.name, dict(arguments), before, dict(targets),
                                   command.get("undo", True)))
        started = time.time()
        result = {"status": "done", "device": device.label, "state": copy.deepcopy(device.state)}
        self.trace.append("tool_call", room=self.room, status="completed",
                          call={"function": tool.name, "args": arguments, "timestamp_start": started,
                                "timestamp_end": time.time()}, result=result)
        self.publish()
        return result

    async def _confirm(self, confirmation_id: str) -> dict[str, Any]:
        pending = self.pending.pop(confirmation_id, None)
        if pending is None:
            return {"status": "invalid", "message": "Nothing is waiting for that confirmation."}
        if self._clock() - pending.asked_at > CONFIRM_SECONDS:
            self.trace.append("confirmation_expired", room=self.room, confirmation_id=confirmation_id)
            return {"status": "expired", "message": "That confirmation expired; ask again."}
        self.trace.append("confirmation_given", room=self.room, confirmation_id=confirmation_id)
        return await self._command(self._tools[pending.tool], pending.arguments, confirmed=True)

    def _cancel(self, confirmation_id: str) -> dict[str, Any]:
        pending = self.pending.pop(confirmation_id, None)
        if pending is None:
            return {"status": "invalid", "message": "Nothing is waiting for that confirmation."}
        self.trace.append("confirmation_declined", room=self.room, confirmation_id=confirmation_id)
        return {"status": "cancelled", "message": "Dropped; nothing was changed."}

    async def _undo(self) -> dict[str, Any]:
        change = next((item for item in reversed(self.history) if item.undoable), None)
        if change is None or self._clock() - change.at > UNDO_SECONDS:
            return {"status": "nothing", "message": "There is no recent change that can be undone."}
        device = self.home.devices.get(change.device_id)
        if device is None or any(device.state.get(key) != value for key, value in change.after.items()):
            return {"status": "nothing", "message": "That device has changed since; nothing was undone."}
        call = {"function": "undo_last_change", "args": {"device": device.label, "reverses": change.tool}}
        mark = self.gate.mark()
        self.trace.append("tool_call_pending", room=self.room, call=call, quiet_seconds=self.gate.quiet_seconds)
        words = await self.gate.hold(mark)
        if words.changed and not (verdict := await self._recheck("undo_last_change", call["args"], words)).run:
            self.trace.append("tool_call_dropped", room=self.room, call=call, reason=verdict.reason)
            return _superseded("The person said more and " + verdict.reason + ".")
        device.state.update(change.before)
        self.history.remove(change)
        result = {"status": "done", "device": device.label, "state": copy.deepcopy(device.state), "undid": change.tool}
        self.trace.append("tool_call", room=self.room, status="completed", call=call, result=result)
        self.publish()
        return result


def tool_result_text(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


# A device the agent has never seen, with capability definitions it has never
# seen either. The judge page sends this over RPC during a live session.
AIR_PURIFIER: dict[str, Any] = {
    "id": "bedroom-air-purifier",
    "label": "Bedroom air purifier",
    "room": "Bedroom",
    "category": "AirPurifier",
    "capabilities": ["switch", "airPurifierFanMode", "dustSensor"],
    "state": {"switch": "off", "airPurifierFanMode": "auto", "fineDustLevel": 38},
    "capabilityDefinitions": {
        "airPurifierFanMode": {
            "attributes": {"airPurifierFanMode": {"type": "string", "enum": ["auto", "sleep", "low", "medium", "high"]}},
            "commands": {
                "setAirPurifierFanMode": {
                    "tool": "set_purifier_fan",
                    "description": "Set an air purifier's fan mode.",
                    "arguments": {"fanMode": {"type": "string", "enum": ["auto", "sleep", "low", "medium", "high"]}},
                    "sets": {"airPurifierFanMode": "$fanMode"},
                },
            },
        },
        "dustSensor": {
            "attributes": {"fineDustLevel": {"type": "integer", "minimum": 0, "maximum": 999, "unit": "µg/m³"}},
            "commands": {},
        },
    },
}
