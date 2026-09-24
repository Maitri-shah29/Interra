"""Offline smoke check using the installed LiveKit SDK and pinned public tool adapter."""
import asyncio
from pathlib import Path
from livekit.agents import StopResponse, llm
from agent.clock import VirtualClock
from agent.fdb.backend import FDBBackend
from agent.fdb.livekit_agent import InterraVoiceAgent


async def main():
    class Bridge:
        text = None
        def user_turn(self, text):
            self.text = text
    bridge = Bridge()
    agent = InterraVoiceAgent(bridge)
    try:
        await agent.on_user_turn_completed(llm.ChatContext(), llm.ChatMessage(role="user", content=["Test utterance"]))
    except StopResponse:
        pass
    else:
        raise AssertionError("SDK generation was not stopped")
    assert bridge.text == "Test utterance"
    root = Path(__file__).resolve().parents[1]
    backend = FDBBackend(root / "artifacts/fdb-upstream/v3", root / "config/fdb_tool_effects.json", VirtualClock())
    assert len(backend.specs) == 12
    await backend.aclose()
    print("PASS: actual SDK transcript handoff and 12 public schemas. No live model/audio inference was performed.")


if __name__ == "__main__":
    asyncio.run(main())
