"""Camera-assisted device troubleshooting in a separate LiveKit voice session.

The benchmark agent exposes only the official tools. This extension samples the
current participant's camera frame and attaches it to the next completed voice
turn. Each session owns and closes its video reader tasks.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from agent.device_catalog import DeviceDesk, GatedAction
from agent.fdb_livekit import (
    CommitGate,
    FdbConfig,
    TraceWriter,
    agent_server_options,
    livekit_llm_model,
    livekit_speech_models,
    llm_recheck,
    load_local_env,
    recheck_llm_model,
    trace_conversation_item,
    trace_session_error,
)


EXTENSION_INSTRUCTIONS = """
You help a person troubleshoot a device they show on camera.
Use the current image and the person's words together. Describe only details
you can actually see. Ask for a closer view when labels or controls are unclear.
When a code is visible, look it up with lookup_indicator. Do not invent a meaning.
Offer one safe check from that result. If they want a person to come, schedule
it with schedule_check and answer "did it go through?" from list_scheduled_checks.
If they correct the day, the device, or the symptom, use the correction and the latest frame. A call
that was not executed comes back as superseded; do not say it happened.
If no camera frame is available, ask the person to turn on the camera.
Never claim that a repair has been completed.
""".strip()


class LatestFrameSource:
    """Own one active video stream and keep only its latest unconsumed frame."""

    def __init__(self) -> None:
        self._stream: Any | None = None
        self._latest: Any | None = None
        self._reader_task: asyncio.Task[Any] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._close_task: asyncio.Task[Any] | None = None
        self._errors: list[BaseException] = []

    def attach(self, stream: Any) -> None:
        if self._closed:
            raise RuntimeError("Frame source is closed")
        previous_stream = self._stream
        self._stream = stream
        self._latest = None
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        if previous_stream is not None:
            self._own(asyncio.create_task(previous_stream.aclose()))
        self._reader_task = asyncio.create_task(self._read(stream))
        self._own(self._reader_task)

    def take(self) -> Any | None:
        frame, self._latest = self._latest, None
        return frame

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    def detach(self) -> None:
        """Invalidate a camera immediately, including an unconsumed frame."""
        stream, self._stream = self._stream, None
        self._latest = None
        if self._reader_task is not None:
            self._reader_task.cancel()
        if stream is not None:
            self._own(asyncio.create_task(stream.aclose()))

    async def _close(self) -> None:
        self._closed = True
        self.detach()
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        if self._errors:
            raise self._errors[0]

    def _own(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._errors.append(error)

    async def _read(self, stream: Any) -> None:
        try:
            async for event in stream:
                if self._stream is not stream or self._closed:
                    return
                # A newer frame replaces one captured before a correction.
                self._latest = event.frame
        except asyncio.CancelledError:
            raise


def clear_previous_images(turn_ctx: Any, image_type: type) -> None:
    """Keep conversational text while removing visual evidence from older turns."""
    for message in turn_ctx.items:
        if isinstance(getattr(message, "content", None), list):
            message.content = [item for item in message.content if not isinstance(item, image_type)]


def camera_matches(participant: Any, publication: Any, identity: str, camera_source: Any) -> bool:
    return participant.identity == identity and publication.source == camera_source


def trace_frame_decision(trace: Any, room_name: str, frame: Any | None) -> None:
    """Record whether this turn carried a camera frame, so the judge page can show it."""
    if frame is None:
        trace.append("camera_frame", room=room_name, attached=False)
        return
    trace.append(
        "camera_frame",
        room=room_name,
        attached=True,
        width=getattr(frame, "width", None),
        height=getattr(frame, "height", None),
    )


async def entrypoint(ctx: Any) -> None:
    load_local_env()
    missing = [
        name for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError("Missing LiveKit configuration: " + ", ".join(missing))

    from livekit import rtc
    from livekit.agents import Agent, AgentSession, inference, llm, room_io
    from livekit.plugins import silero

    config = FdbConfig.from_env()
    frames = LatestFrameSource()
    trace = TraceWriter(config.trace_dir)
    gate = CommitGate(config.commit_quiet_seconds)
    desk = DeviceDesk()
    model = livekit_llm_model(inference, config)
    actions = GatedAction(
        gate, trace, ctx.room.name, recheck=llm_recheck(recheck_llm_model(inference, config), llm),
    )
    ctx.add_shutdown_callback(frames.close)
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    identity = participant.identity

    class CameraTools:
        def __init__(self) -> None:
            pass

        @llm.function_tool(description="Look up a code you can see on the device display.")
        async def lookup_indicator(self, code: str) -> str:
            return await actions.run(
                "lookup_indicator", {"code": code}, lambda: desk.lookup(code)
            )

        @llm.function_tool(description="Schedule one follow-up visit. Use the day the person settled on.")
        async def schedule_check(self, day: str, window: str) -> str:
            return await actions.run(
                "schedule_check",
                {"day": day, "window": window},
                lambda: desk.schedule(day, window),
            )

        @llm.function_tool(description="List visits that were actually scheduled. Use this to confirm a booking.")
        async def list_scheduled_checks(self) -> str:
            return await actions.run(
                "list_scheduled_checks", {}, lambda: desk.list_visits()
            )

    class CameraTroubleshooter(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=EXTENSION_INSTRUCTIONS,
                tools=llm.find_function_tools(CameraTools()),
            )
            self._on_track: Any | None = None
            self._on_untrack: Any | None = None
            self._track_sid: str | None = None

        async def on_enter(self) -> None:
            for remote in ctx.room.remote_participants.values():
                for publication in remote.track_publications.values():
                    track = publication.track
                    if (track is not None and track.kind == rtc.TrackKind.KIND_VIDEO
                            and camera_matches(remote, publication, identity, rtc.TrackSource.SOURCE_CAMERA)):
                        self._track_sid = publication.sid
                        frames.attach(rtc.VideoStream(track))
                        break

            def on_track_subscribed(
                track: rtc.Track,
                publication: rtc.RemoteTrackPublication,
                participant: rtc.RemoteParticipant,
            ) -> None:
                if (track.kind == rtc.TrackKind.KIND_VIDEO
                        and camera_matches(participant, publication, identity, rtc.TrackSource.SOURCE_CAMERA)):
                    self._track_sid = publication.sid
                    frames.attach(rtc.VideoStream(track))

            def on_track_unsubscribed(track: Any, publication: Any, participant: Any) -> None:
                if participant.identity == identity and publication.sid == self._track_sid:
                    self._track_sid = None
                    frames.detach()

            self._on_track = on_track_subscribed
            ctx.room.on("track_subscribed", on_track_subscribed)
            self._on_untrack = on_track_unsubscribed
            ctx.room.on("track_unsubscribed", on_track_unsubscribed)
            self.session.generate_reply(
                instructions=(
                    "Say one short sentence: you can hear them, and ask them to hold the code up to the camera."
                )
            )

        async def on_user_turn_completed(
            self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
        ) -> None:
            clear_previous_images(turn_ctx, llm.ImageContent)
            frame = frames.take()
            trace_frame_decision(trace, ctx.room.name, frame)
            if frame is not None:
                content = new_message.content
                if not isinstance(content, list):
                    content = [content]
                    new_message.content = content
                content.append(llm.ImageContent(image=frame))
            else:
                new_message.content.append("[No current camera frame is available. Ask for the camera or a clearer view.]")

        async def on_exit(self) -> None:
            if self._on_track is not None:
                ctx.room.off("track_subscribed", self._on_track)
                self._on_track = None
            if self._on_untrack is not None:
                ctx.room.off("track_unsubscribed", self._on_untrack)
                self._on_untrack = None
            await frames.close()

    speech_stt, speech_tts = livekit_speech_models(inference, config)
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=speech_stt,
        llm=model,
        tts=speech_tts,
    )

    @session.on("user_state_changed")
    def on_user_state(event: Any) -> None:
        gate.set_speaking(str(event.new_state) == "speaking")
        trace.append("user_state", room=ctx.room.name, state=str(event.new_state))

    @session.on("user_input_transcribed")
    def on_transcript(event: Any) -> None:
        if event.is_final:
            gate.note_transcript(event.transcript)
        trace.append(
            "transcript",
            room=ctx.room.name,
            transcript=event.transcript,
            is_final=event.is_final,
        )

    @session.on("agent_state_changed")
    def on_agent_state(event: Any) -> None:
        trace.append("agent_state", room=ctx.room.name, state=str(event.new_state))

    @session.on("conversation_item_added")
    def on_item(event: Any) -> None:
        trace_conversation_item(trace, ctx.room.name, event.item)

    @session.on("error")
    def on_error(event: Any) -> None:
        trace_session_error(trace, ctx.room.name, event)

    @session.on("close")
    def on_close(event: Any) -> None:
        trace.append(
            "session_closed", room=ctx.room.name,
            reason=str(event.reason), failed=event.error is not None,
        )

    trace.append("session_started", room=ctx.room.name)
    await session.start(
        room=ctx.room,
        agent=CameraTroubleshooter(),
        room_options=room_io.RoomOptions(close_on_disconnect=True, participant_identity=identity),
    )


def main() -> None:
    load_local_env()
    from livekit import agents
    from livekit.agents import AgentServer

    server = AgentServer(**agent_server_options())
    server.rtc_session(agent_name="interra-camera-troubleshooter")(entrypoint)
    agents.cli.run_app(server)


if __name__ == "__main__":
    main()
