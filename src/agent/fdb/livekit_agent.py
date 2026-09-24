"""Custom LiveKit voice agent; SessionRuntime owns decisions and concurrency."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from livekit import agents
from livekit.agents import Agent, AgentSession, AgentServer, StopResponse
from livekit.plugins import openai, silero

from ..clock import RealClock
from ..runtime import SessionRuntime
from ..providers.ollama import OllamaProvider
from ..providers.openai_chat import OpenAIChatProvider
from .backend import FDBBackend
from .bridge import VoiceBridge
from .kitchen import KitchenBackend


ROOT = Path(__file__).resolve().parents[3]


class InterraVoiceAgent(Agent):
    def __init__(self, bridge):
        super().__init__(instructions="Interra voice transport. The session coordinator generates responses.")
        self.bridge = bridge

    async def on_user_turn_completed(self, turn_ctx, new_message):
        self.bridge.user_turn(new_message.text_content or "")
        # All reasoning and speech is owned by the runtime/bridge, not an SDK LLM.
        raise StopResponse()


def configuration():
    path = Path(os.environ.get("INTERRA_FDB_CONFIG", ROOT / "config/fdb.json"))
    config = json.loads(path.read_text(encoding="utf-8"))
    config["planner"] = os.environ.get("INTERRA_PLANNER", config["planner"])
    config["planner_model"] = os.environ.get("INTERRA_MODEL", config["planner_model"])
    return config


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    config = configuration()
    clock = RealClock()
    if config["planner"] == "ollama":
        planner = OllamaProvider(config["planner_model"], os.environ.get("INTERRA_OLLAMA_URL", "http://localhost:11434"))
    elif config["planner"] == "openai":
        planner = OpenAIChatProvider(config["planner_model"], os.environ.get("OPENAI_API_KEY"), seed=config["seed"])
    else:
        raise ValueError("INTERRA_PLANNER must be openai or ollama")
    try:
        runtime = SessionRuntime(uuid4().hex, planner, clock, planner_repairs=1)
        session = AgentSession(vad=silero.VAD.load(min_silence_duration=0.55),
            stt=openai.STT(model=config["stt_model"], language="en"),
            tts=openai.TTS(model=config["tts_model"], voice=config["tts_voice"]),
            min_endpointing_delay=0.5, max_endpointing_delay=5.0)
        speak = lambda text: session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
        use_case = os.environ.get("INTERRA_USE_CASE", "benchmark")
        if use_case == "kitchen":
            backend = KitchenBackend(clock, speak, runtime.trace)
        elif use_case == "benchmark":
            backend = FDBBackend(os.environ["INTERRA_FDB_ROOT"], ROOT / "config/fdb_tool_effects.json", clock, config["seed"])
        else:
            raise ValueError("INTERRA_USE_CASE must be benchmark or kitchen")
    except BaseException:
        await planner.aclose()
        raise
    bridge = VoiceBridge(runtime, backend, speak, room_name=ctx.room.name,
                         tool_log=os.environ.get("INTERRA_FDB_TOOL_LOG", "/tmp/agent_tool_calls.log"))
    bridge.send("TOOL_MANIFEST", tools=[s.model_dump(mode="json") for s in backend.specs])
    stop = asyncio.Event()
    async def supervise():
        async with asyncio.TaskGroup() as group:
            runtime_task = group.create_task(runtime.run())
            bridge_task = group.create_task(bridge.run())
            await stop.wait()
            runtime_task.cancel()
            bridge_task.cancel()
    owner = asyncio.create_task(supervise())

    @session.on("user_state_changed")
    def on_state(event):
        if event.new_state == "speaking" and (bridge.busy or session.agent_state == "speaking"):
            bridge.interrupt()
            session.interrupt()

    trace_dir = Path(os.environ.get("INTERRA_TRACE_DIR", ROOT / "artifacts/fdb-runtime-traces"))
    closed = False
    async def shutdown(reason=""):
        nonlocal closed
        if closed:
            return
        closed = True
        stop.set()
        try:
            await owner
        finally:
            await planner.aclose()
            await asyncio.to_thread(runtime.trace.save, trace_dir / f"{runtime.session_id}.jsonl")
    def owner_finished(task):
        if not task.cancelled() and task.exception() is not None:
            runtime.trace.record("VOICE_RUNTIME_FAILED", error=str(task.exception()))
            ctx.shutdown(reason="Interra runtime failed")
    owner.add_done_callback(owner_finished)
    ctx.add_shutdown_callback(shutdown)
    try:
        await session.start(room=ctx.room, agent=InterraVoiceAgent(bridge))
    except BaseException:
        await shutdown()
        raise


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.local")
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
