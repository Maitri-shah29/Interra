"""Interra for a SmartThings home: a LiveKit voice worker over simulated devices.

The person talks to their home. Every device is described the SmartThings
way, and each description becomes the agent's tools (see ``agent.smart_home``).
State changes go through the commit gate: a command is held until the person
has been quiet, and if they said more meanwhile, the same model re-checks it
against their complete words, so a value the person corrects never reaches
the device. A risky command waits for a spoken yes.

The judge page can add a device the agent has never seen during the call,
over LiveKit RPC; its tools are added to the live session at once.

    python -m agent.home_livekit start

Dispatch name: ``interra-home``. Needs ``LIVEKIT_URL``, ``LIVEKIT_API_KEY``
and ``LIVEKIT_API_SECRET``.
"""
from __future__ import annotations

import json
import os
from typing import Any

from agent.commit import CommitGate
from agent.fdb_livekit import (
    FdbConfig,
    TraceWriter,
    UnfinishedTurnHold,
    apply_turn_hold,
    livekit_speech_models,
    llm_recheck,
    load_local_env,
    recheck_llm_model,
    run_session_lifecycle,
    trace_conversation_item,
    trace_session_error,
)
from agent.smart_home import HomeController, HomeError, SmartHome, ToolDef, tool_result_text

HOME_AGENT = "interra-home"
ADD_DEVICE_RPC = "interra.add_device"

HOME_INSTRUCTIONS = """
You are Interra, the voice assistant for a Samsung SmartThings home. The
devices are simulated for this demo; your tools are generated from their
SmartThings descriptions.

- Wait for the complete request. If the person corrects themselves ("no wait",
  "actually", "make it ..."), use only the final value. Never send a value the
  person replaced.
- One request may change several devices: call one tool per device.
- Use device names exactly as the tools list them.
- A result with status "superseded" did not run because the person said more
  first. Act on their complete words: call again, with any corrected value, if
  they still want it. Never say it happened and never report it as a failure.
- Say a change happened only when a tool result says "done". "already" means
  it was already that way; say so.
- "needs_confirmation": ask one short yes or no question that names the action
  and the device. Call confirm_action only after a clear yes, and
  cancel_action after a no.
- "Undo", "put it back" or "never mind that" calls undo_last_change.
- Answer questions about a device from get_device_status or list_devices,
  never from memory.
- Reply in one short spoken sentence. No lists, no markdown.
""".strip()


def home_tools(controller: HomeController, function_tool: Any) -> list[Any]:
    """LiveKit tools for the controller's current devices, one per ToolDef."""

    def build(definition: ToolDef) -> Any:
        name = definition.name

        async def handler(raw_arguments: dict[str, Any]) -> str:
            return tool_result_text(await controller.call(name, raw_arguments))

        return function_tool(handler, raw_schema=definition.schema())

    return [build(definition) for definition in controller.tools()]


def home_llm(inference: Any, config: FdbConfig) -> Any:
    """The benchmark's model, with parallel calls so one request can change several devices."""
    return inference.LLM(
        model=config.llm_model,
        extra_kwargs={"temperature": config.llm_temperature, "parallel_tool_calls": True},
    )


def device_added_instructions(label: str, new_tools: list[str]) -> str:
    detail = f" New controls: {', '.join(new_tools)}." if new_tools else ""
    return (
        f"A new device was just added to the home: {label}.{detail} "
        "Say one short sentence that you can control it now. Do not change it."
    )


async def handle_add_device(controller: HomeController, payload: str) -> tuple[dict[str, Any], Any]:
    """Parse and add a device sent by the page. Returns the reply and the device, or None."""
    try:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise HomeError("the payload must be one device description")
        device, new_tools = controller.add_device(raw)
    except (json.JSONDecodeError, HomeError) as exc:
        controller.trace.append("home_device_rejected", room=controller.room, error=str(exc))
        return {"status": "rejected", "message": str(exc)}, None
    return {"status": "added", "device": device.label, "new_tools": new_tools}, device


async def entrypoint(ctx: Any) -> None:
    load_local_env()
    missing = [name for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing LiveKit configuration: " + ", ".join(missing))

    from livekit.agents import Agent, AgentSession, StopResponse, inference, llm, room_io
    from livekit.plugins import silero

    config = FdbConfig.from_env()
    trace = TraceWriter(config.trace_dir)
    gate = CommitGate(config.commit_quiet_seconds)
    controller = HomeController(
        SmartHome(), gate, trace, ctx.room.name,
        recheck=llm_recheck(recheck_llm_model(inference, config), llm),
    )
    session: Any = None

    def release_held_turn(text: str) -> None:
        session.generate_reply(user_input=text, input_modality="audio")

    hold = UnfinishedTurnHold(release_held_turn, trace, ctx.room.name, config.unfinished_turn_hold_seconds)

    class HomeAgent(Agent):
        def __init__(self) -> None:
            super().__init__(instructions=HOME_INSTRUCTIONS, tools=home_tools(controller, llm.function_tool))

        async def on_enter(self) -> None:
            self.session.generate_reply(
                instructions="Say one short sentence: you can control the home, and ask what they would like."
            )

        async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
            apply_turn_hold(hold, new_message, StopResponse)

    agent = HomeAgent()
    speech_stt, speech_tts = livekit_speech_models(inference, config)
    session = AgentSession(
        vad=silero.VAD.load(min_speech_duration=0.05, min_silence_duration=0.3),
        stt=speech_stt,
        llm=home_llm(inference, config),
        tts=speech_tts,
        max_tool_steps=8,
    )

    @session.on("user_state_changed")
    def on_user_state(event: Any) -> None:
        trace.append("user_state", room=ctx.room.name, state=str(event.new_state))
        if str(event.new_state) == "speaking":
            hold.on_user_speaking()
            gate.set_speaking(True)
        elif str(event.new_state) in {"listening", "away"}:
            gate.set_speaking(False)

    @session.on("user_input_transcribed")
    def on_transcript(event: Any) -> None:
        if event.is_final:
            gate.note_transcript(event.transcript)
        trace.append("transcript", room=ctx.room.name, transcript=event.transcript, is_final=event.is_final)

    @session.on("agent_state_changed")
    def on_agent_state(event: Any) -> None:
        trace.append("agent_state", room=ctx.room.name, state=str(event.new_state))

    @session.on("conversation_item_added")
    def on_item(event: Any) -> None:
        trace_conversation_item(trace, ctx.room.name, event.item)

    @session.on("error")
    def on_error(event: Any) -> None:
        trace_session_error(trace, ctx.room.name, event)

    async def on_add_device(data: Any) -> str:
        reply, device = await handle_add_device(controller, data.payload)
        if device is not None:
            await agent.update_tools(home_tools(controller, llm.function_tool))
            session.generate_reply(instructions=device_added_instructions(device.label, reply["new_tools"]))
        return json.dumps(reply)

    trace.append("session_started", room=ctx.room.name)
    await ctx.connect()
    ctx.room.local_participant.register_rpc_method(ADD_DEVICE_RPC, on_add_device)
    controller.publish()
    participant = await ctx.wait_for_participant()
    try:
        await run_session_lifecycle(
            ctx, session, agent,
            room_io.RoomOptions(close_on_disconnect=False, participant_identity=participant.identity),
            trace,
            participant_identity=participant.identity,
            on_participant_left=lambda: hold.flush("participant_left"),
        )
    finally:
        await hold.aclose()


def main() -> None:
    load_local_env()
    from livekit import agents
    from livekit.agents import AgentServer

    server = AgentServer()
    server.rtc_session(entrypoint, agent_name=HOME_AGENT)
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
