// Voice, camera and home pages: one LiveKit room per session, the worker trace
// for that room, and the agent's own transcription streams.

import { $, errorText, loadConfig, nowSeconds } from "./shell.js";
import { Conversation, Meter, getAudioContext } from "./conversation.js";
import { storeBrowserEvent } from "./trace.js";

const LIVEKIT_CLIENT = "https://cdn.jsdelivr.net/npm/livekit-client@2.15.8/+esm";
const mode = document.querySelector(".session").dataset.mode;
const el = {
  start: $("start"), startLabel: $("start-label"), elapsed: $("elapsed"),
  mic: $("mic"), cam: $("cam"), speaker: $("speaker"),
  preview: $("preview"), videoFrame: $("video-frame"),
  agentAudio: $("agent-audio"), txBadge: $("tx-badge"), txBadgeText: $("tx-badge-text"),
};

const cfg = { voice_agent: "interra-benchmark", camera_agent: "interra-camera-troubleshooter", home_agent: "interra-home", livekit_host: "" };
const app = {
  session: 0,
  room: null,
  roomName: "",
  events: null,
  agentIdentity: "",
  camOff: false,
  speakerMuted: false,
  audioBlocked: false,
  traceWatch: 0,
  link: "",
  quality: "",
};

const convo = new Conversation({ mode, onEvent: (event) => storeBrowserEvent(app.roomName, event) });
const agentName = () => ({ camera: cfg.camera_agent, home: cfg.home_agent }[mode] || cfg.voice_agent);
const HINTS = {
  voice: { idle: "Press Start and say the line. Headphones help.", live: "Listening. Say the line, pause, then correct it." },
  camera: { idle: "Press Start and hold the code up to the webcam.", live: "Listening. Hold E1 in view, then say the line." },
  home: { idle: "Press Start and talk to your home. Headphones help.", live: "Listening. Ask for a change, then correct it." },
};

// Other scripts on the page (the home tiles) follow the session through these events.
function announce(type, detail) {
  window.dispatchEvent(new CustomEvent("interra:" + type, { detail }));
}

function setBadge(text, on) {
  // Shown only once captions flow; an idle session has nothing to label.
  el.txBadgeText.textContent = text;
  el.txBadge.dataset.on = String(on);
  el.txBadge.hidden = !text;
}

function setPhase(phase) {
  convo.phase = phase;
  const live = phase === "live";
  const connecting = phase === "connecting";
  el.start.dataset.state = live ? "live" : connecting ? "connecting" : "idle";
  el.startLabel.textContent = live ? "End" : connecting ? "Connecting" : "Start";
  el.start.querySelector(".phone use").setAttribute("href", live ? "#i-phone-off" : "#i-phone");
  el.start.setAttribute("aria-label", live ? "End the session" : "Start a " + mode + " session");
  el.elapsed.hidden = !live;
  el.mic.disabled = !live;
  if (el.cam) el.cam.disabled = !live;
  app.link = live ? "live" : connecting ? "connecting" : "";
  showRoom();
  if (!live) setBadge("", false);
}

function showRoom() {
  // A poor link is the only connection detail worth a word on screen.
  if (app.link === "reconnecting") convo.hint("Connection lost; reconnecting…", true);
  else if (app.link === "live" && app.quality === "poor") convo.hint("The connection is poor; speech may arrive late.", true);
}

function setRoom(name) {
  app.roomName = name;
  app.quality = "";
}

function randomId(length = 8) {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (const byte of crypto.getRandomValues(new Uint8Array(length))) out += alphabet[byte % alphabet.length];
  return out;
}

function hostOf(url) {
  try { return new URL(url.replace(/^ws/, "http")).host; } catch (error) { return url; }
}

async function startCall() {
  if (convo.phase === "connecting") return;
  if (convo.phase === "live") { await endCall("You left the room."); return; }
  const session = ++app.session;
  getAudioContext();
  setRoom("demo-" + mode + "-" + randomId(8));
  convo.reset(nowSeconds());
  convo.agentLabel = agentName();
  convo.micMuted = false;
  el.mic.setAttribute("aria-pressed", "false");
  setPhase("connecting");
  convo.hint("Opening a room for " + agentName() + ".");
  convo.note("connecting", "Opening room " + app.roomName, { icon: "link", label: "Connecting", data: { room: app.roomName, mode, agent: agentName() } });
  try {
    const LK = await import(LIVEKIT_CLIENT);
    if (session !== app.session) return;
    openTrace(app.roomName, session);
    const response = await fetch("/api/token?mode=" + mode + "&room=" + encodeURIComponent(app.roomName), { cache: "no-store" });
    if (!response.ok) throw new Error((await response.text()) || "could not open a room (" + response.status + ")");
    const { url, token } = await response.json();
    if (session !== app.session) return;
    convo.note("token", "Token minted on this machine; dispatching " + agentName(), { icon: "check", label: "Token", data: { room: app.roomName, agent: agentName(), livekit: hostOf(url) } });
    const room = new LK.Room({
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    app.room = room;
    wireRoom(LK, room, session);
    room.registerTextStreamHandler("lk.transcription", (reader, participant) => onTranscription(room, session, reader, participant));
    await room.connect(url, token);
    if (session !== app.session) { room.disconnect(); return; }
    convo.connectedAt = performance.now();
    setPhase("live");
    setBadge("Live", true);
    convo.note("room_connected", "Connected to " + hostOf(url), { icon: "link", label: "Connected", data: { room: app.roomName, livekit: hostOf(url) } });
    try { await room.startAudio(); } catch (error) { /* allowed after the next click */ }
    await room.localParticipant.setMicrophoneEnabled(true);
    if (session !== app.session) return;
    const microphone = room.localParticipant.getTrackPublication(LK.Track.Source.Microphone);
    if (microphone && microphone.track && microphone.track.mediaStreamTrack) convo.meters.mic = new Meter(microphone.track.mediaStreamTrack);
    if (mode === "camera") {
      await room.localParticipant.setCameraEnabled(true);
      if (session !== app.session) return;
      attachPreview(room, LK);
    }
    for (const participant of room.remoteParticipants.values()) if (participant.isAgent) agentJoined(participant);
    convo.hint(HINTS[mode].live);
  } catch (error) {
    if (session !== app.session) return;
    convo.note("start_failed", "Did not start: " + errorText(error), { cat: "error", icon: "alert", label: "Start failed" });
    app.session += 1;
    await teardown();
    convo.endText = "Did not start";
    setPhase("ended");
    convo.hint("Did not start: " + errorText(error), true);
  }
}

function wireRoom(LK, room, session) {
  const E = LK.RoomEvent;
  const alive = () => session === app.session && app.room === room;
  room.on(E.ParticipantConnected, (participant) => { if (alive() && participant.isAgent) agentJoined(participant); });
  room.on(E.ParticipantDisconnected, (participant) => {
    if (!alive() || !participant.isAgent || participant.identity !== app.agentIdentity) return;
    convo.agentPresent = false;
    convo.agentState = null;
    convo.note("agent_left", "Agent left · " + participant.identity, { cat: "agent", icon: "logout", label: "Agent left" });
  });
  room.on(E.ParticipantAttributesChanged, (changed, participant) => {
    if (!alive() || !participant.isAgent || !("lk.agent.state" in changed)) return;
    convo.setAgentState(changed["lk.agent.state"], "attribute");
  });
  room.on(E.TrackSubscribed, (track, publication, participant) => {
    if (alive() && track.kind === LK.Track.Kind.Audio) attachAgentAudio(room, track, participant);
  });
  room.on(E.TrackUnsubscribed, (track) => {
    if (!alive() || track.kind !== LK.Track.Kind.Audio) return;
    track.detach();
    if (convo.meters.agent) { convo.meters.agent.close(); convo.meters.agent = null; }
  });
  room.on(E.AudioPlaybackStatusChanged, () => { if (alive()) setAudioBlocked(!room.canPlaybackAudio); });
  room.on(E.Reconnecting, () => {
    if (!alive()) return;
    app.link = "reconnecting";
    showRoom();
    convo.note("reconnecting", "Connection lost; reconnecting", { cat: "error", icon: "alert", label: "Reconnecting" });
  });
  room.on(E.Reconnected, () => {
    if (!alive()) return;
    app.link = "live";
    showRoom();
    convo.note("reconnected", "Reconnected", { icon: "check", label: "Reconnected" });
  });
  room.on(E.Disconnected, (reason) => {
    if (!alive()) return;
    convo.note("disconnected", "The room closed" + (reason !== undefined ? " (" + reason + ")" : ""), { icon: "logout", label: "Disconnected" });
    app.session += 1;
    teardown().then(() => {
      convo.endText = "Call ended";
      setPhase("ended");
      convo.hint("The room closed.");
    });
  });
  room.on(E.ConnectionQualityChanged, (quality, participant) => {
    if (alive() && participant === room.localParticipant) { app.quality = String(quality); showRoom(); }
  });
  room.on(E.MediaDevicesError, (error) => {
    if (!alive()) return;
    convo.note("device_error", "Microphone or camera error: " + errorText(error), { cat: "error", icon: "alert", label: "Device error" });
    convo.hint("Microphone or camera error: " + errorText(error), true);
  });
  room.on(E.LocalTrackPublished, (publication) => {
    if (!alive()) return;
    const camera = publication.source === LK.Track.Source.Camera;
    convo.note(camera ? "camera_on" : "mic_on", (camera ? "Camera" : "Microphone") + " published", { icon: camera ? "video" : "mic", label: camera ? "Camera" : "Microphone" });
  });
}

function agentJoined(participant) {
  if (app.agentIdentity === participant.identity && convo.agentPresent) return;
  app.agentIdentity = participant.identity;
  convo.agentPresent = true;
  announce("agent", { room: app.room, identity: participant.identity });
  convo.note("agent_joined", "Agent joined · " + participant.identity, { cat: "agent", icon: "bot", label: "Agent joined", data: { identity: participant.identity, name: participant.name, attributes: participant.attributes } });
  const state = participant.attributes && participant.attributes["lk.agent.state"];
  if (state) convo.setAgentState(state, "attribute");
  const session = app.session;
  clearTimeout(app.traceWatch);
  app.traceWatch = setTimeout(() => {
    if (session !== app.session || convo.traceSeen) return;
    convo.note("trace_missing", "No worker trace for this room yet. The worker may run on another machine, or INTERRA_FDB_TRACE_DIR differs from this page's. Transcription and audio still work.", { icon: "info", label: "Trace" });
  }, 10000);
}

function attachAgentAudio(room, track, participant) {
  const element = track.attach();
  element.autoplay = true;
  element.muted = app.speakerMuted;
  el.agentAudio.replaceChildren(element);
  if (convo.meters.agent) convo.meters.agent.close();
  convo.meters.agent = new Meter(track.mediaStreamTrack);
  convo.note("agent_audio", "Agent audio subscribed" + (participant ? " · " + participant.identity : ""), { cat: "agent", icon: "volume", label: "Audio" });
  room.startAudio()
    .then(() => element.play())
    .then(() => setAudioBlocked(false))
    .catch((error) => {
      setAudioBlocked(true);
      convo.hint("Sound is blocked: " + errorText(error) + ". Press the speaker button.", true);
    });
}

function attachPreview(room, LK) {
  const camera = room.localParticipant.getTrackPublication(LK.Track.Source.Camera);
  if (camera && camera.track && el.preview) {
    camera.track.attach(el.preview);
    el.videoFrame.classList.add("has-video");
  }
}

function setAudioBlocked(blocked) {
  app.audioBlocked = blocked;
  el.speaker.hidden = !blocked;
  el.speaker.classList.toggle("attention", blocked);
  el.speaker.title = blocked ? "Allow agent audio" : app.speakerMuted ? "Unmute agent audio" : "Mute agent audio";
}

async function onTranscription(room, session, reader, participant) {
  if (session !== app.session) return;
  const info = reader.info || {};
  const attributes = info.attributes || {};
  const local = room.localParticipant && participant && participant.identity === room.localParticipant.identity;
  const role = local ? "user" : "agent";
  const id = "s-" + (attributes["lk.segment_id"] || info.id);
  const time = info.timestamp ? info.timestamp / 1000 : nowSeconds();
  if (convo.useStreamTranscript()) {
    convo.note("transcript_source", "Live transcription from LiveKit text streams", { icon: "message", label: "Transcript" });
    setBadge("Live captions", true);
  }
  let text = "";
  try {
    for await (const chunk of reader) {
      if (session !== app.session) return;
      text += decodeChunk(chunk);
      convo.transcript.upsert(id, role, text, { final: false, time });
    }
  } catch (error) {
    if (session === app.session) convo.note("transcription", "A transcription stream ended early: " + errorText(error), { cat: "error", icon: "alert", label: "Transcript" });
  }
  if (session !== app.session) return;
  const closing = (reader.info && reader.info.attributes) || attributes;
  convo.transcript.upsert(id, role, text, { final: role === "agent" || closing["lk.transcription_final"] === "true", time });
}

function decodeChunk(chunk) {
  if (typeof chunk !== "string") return "";
  if (!chunk.startsWith("{")) return chunk;
  let out = "";
  for (const part of chunk.split("\n")) {
    if (!part) continue;
    try {
      const value = JSON.parse(part);
      if (typeof value.text !== "string") return chunk;
      out += value.text;
    } catch (error) {
      return chunk;
    }
  }
  return out;
}

function openTrace(roomName, session) {
  closeTrace();
  const source = new EventSource("/api/events?room=" + encodeURIComponent(roomName));
  app.events = source;
  let warned = false;
  source.addEventListener("hello", (message) => {
    if (session !== app.session) return;
    let info = {};
    try { info = JSON.parse(message.data); } catch (error) { return; }
    if (info.resumed) return;
    convo.note("trace", info.exists ? "Reading worker trace " + info.trace : "Waiting for worker trace " + info.trace, { icon: "file", label: "Trace", data: info });
  });
  source.onmessage = (message) => {
    if (session !== app.session) return;
    let record;
    try { record = JSON.parse(message.data); } catch (error) { return; }
    convo.onRecord(record, "worker");
    announce("record", record);
    if (record.kind === "transcript" || record.kind === "conversation_item") {
      if (convo.txSource === "trace") setBadge("From the worker trace", true);
    }
  };
  source.onerror = () => {
    if (session !== app.session || warned || source.readyState !== EventSource.CONNECTING) return;
    warned = true;
    convo.note("trace_retry", "Trace stream interrupted; it resumes from the last event it delivered", { cat: "error", icon: "alert", label: "Trace" });
  };
}

function closeTrace() {
  if (app.events) { app.events.close(); app.events = null; }
}

async function teardown() {
  closeTrace();
  clearTimeout(app.traceWatch);
  for (const name of ["mic", "agent"]) {
    if (convo.meters[name]) { convo.meters[name].close(); convo.meters[name] = null; }
  }
  el.agentAudio.replaceChildren();
  if (el.preview) el.preview.srcObject = null;
  if (el.videoFrame) el.videoFrame.classList.remove("has-video");
  setAudioBlocked(false);
  const room = app.room;
  app.room = null;
  announce("agent", null);
  convo.agentPresent = false;
  convo.agentState = null;
  convo.userSpeaking = false;
  convo.quiet.active = false;
  convo.transcript.removeDots();
  if (room) {
    try { await room.disconnect(); } catch (error) { /* already closed */ }
  }
}

async function endCall(message) {
  app.session += 1;
  await teardown();
  convo.note("left", message, { icon: "logout", label: "Left" });
  convo.endText = "Call ended";
  setPhase("ended");
  convo.hint(message);
}

async function toggleMic() {
  const room = app.room;
  if (!room || convo.phase !== "live") return;
  const muted = !convo.micMuted;
  try {
    await room.localParticipant.setMicrophoneEnabled(!muted);
    convo.micMuted = muted;
    el.mic.setAttribute("aria-pressed", String(muted));
    el.mic.title = muted ? "Unmute microphone" : "Mute microphone";
    convo.note(muted ? "mic_muted" : "mic_unmuted", muted ? "Microphone muted" : "Microphone unmuted", { icon: muted ? "mic-off" : "mic", label: "Microphone" });
  } catch (error) {
    convo.hint("Could not change the microphone: " + errorText(error), true);
  }
}

async function toggleCamera() {
  const room = app.room;
  if (!room || convo.phase !== "live" || mode !== "camera") return;
  const off = !app.camOff;
  try {
    await room.localParticipant.setCameraEnabled(!off);
    app.camOff = off;
    el.cam.setAttribute("aria-pressed", String(off));
    el.cam.title = off ? "Turn camera on" : "Turn camera off";
    if (off) el.videoFrame.classList.remove("has-video");
    else attachPreview(room, await import(LIVEKIT_CLIENT));
    convo.note(off ? "camera_off" : "camera_on", off ? "Camera off; no frame reaches the next turn" : "Camera on", { icon: off ? "video-off" : "video", label: "Camera" });
  } catch (error) {
    convo.hint("Could not change the camera: " + errorText(error), true);
  }
}

function toggleSpeaker() {
  if (app.audioBlocked && app.room) {
    app.room.startAudio().then(() => {
      setAudioBlocked(false);
      const audio = el.agentAudio.querySelector("audio");
      if (audio) audio.play().catch(() => {});
      convo.hint("Agent audio is on.");
    }).catch((error) => convo.hint("Sound is still blocked: " + errorText(error), true));
    return;
  }
  app.speakerMuted = !app.speakerMuted;
  el.speaker.setAttribute("aria-pressed", String(app.speakerMuted));
  el.speaker.title = app.speakerMuted ? "Unmute agent audio" : "Mute agent audio";
  for (const audio of el.agentAudio.querySelectorAll("audio")) audio.muted = app.speakerMuted;
}

// A page can play records it made itself (the home walkthrough) through the same view.
window.addEventListener("interra:inject", (event) => {
  if (convo.txSource === null) convo.txSource = "trace";
  convo.onRecord(event.detail, "replay");
  announce("record", event.detail);
});

el.start.addEventListener("click", () => startCall());
el.mic.addEventListener("click", () => toggleMic());
if (el.cam) el.cam.addEventListener("click", () => toggleCamera());
el.speaker.addEventListener("click", () => toggleSpeaker());
window.addEventListener("beforeunload", () => {
  if (app.room) app.room.disconnect();
  closeTrace();
});

setPhase("idle");
convo.hint(HINTS[mode].idle);
loadConfig().then((config) => {
  Object.assign(cfg, config);
  convo.agentLabel = agentName();
  convo.setQuietSeconds(config.commit_quiet_seconds);
  if (convo.phase === "idle") setPhase("idle");
  if (config.livekit_configured === false) {
    convo.hint("LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env.", true);
  }
});
convo.start();
