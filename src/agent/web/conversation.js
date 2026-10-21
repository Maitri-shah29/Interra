// One conversation on screen: the orb, the bars, the gate card and the
// transcript, driven by worker trace records. Live sessions and the recorded
// replay feed it the same records; neither invents one.

import { Orb, mountStylePicker } from "./orb.js";
import { $, clamp, fmtClock, fmtNumber, fmtShort, h, nowSeconds, reduceMotion, restartAnimation, svgIcon } from "./shell.js";
import { callBlock, callDuration, callKey, callText, resultSummary } from "./trace.js";
import { TranscriptView } from "./transcript.js";

const BAR_COUNT = 24;

let audioContext = null;
export function getAudioContext() {
  if (!audioContext) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    audioContext = new Context();
  }
  if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
  return audioContext;
}

export class Meter {
  constructor(track) {
    this.ok = false;
    this.level = 0;
    const context = getAudioContext();
    if (!context || !track) return;
    try {
      this.source = context.createMediaStreamSource(new MediaStream([track]));
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 1024;
      this.analyser.smoothingTimeConstant = 0.55;
      this.analyser.minDecibels = -92;
      this.analyser.maxDecibels = -18;
      this.source.connect(this.analyser);
      this.samples = new Float32Array(this.analyser.fftSize);
      this.spectrum = new Uint8Array(this.analyser.frequencyBinCount);
      this.binHz = context.sampleRate / this.analyser.fftSize;
      this.ok = true;
    } catch (error) {
      this.ok = false;
    }
  }

  sample() {
    if (!this.ok) return 0;
    this.analyser.getFloatTimeDomainData(this.samples);
    let sum = 0;
    for (let i = 0; i < this.samples.length; i++) sum += this.samples[i] * this.samples[i];
    const decibels = 20 * Math.log10(Math.sqrt(sum / this.samples.length) + 1e-9);
    const target = clamp((decibels + 58) / 46);
    this.level += (target - this.level) * (target > this.level ? 0.55 : 0.12);
    return this.level;
  }

  bands(out) {
    if (!this.ok) return out.fill(0);
    this.analyser.getByteFrequencyData(this.spectrum);
    const low = 90;
    const high = 5200;
    for (let i = 0; i < out.length; i++) {
      const from = Math.max(1, Math.floor((low * Math.pow(high / low, i / out.length)) / this.binHz));
      const to = Math.max(from + 1, Math.ceil((low * Math.pow(high / low, (i + 1) / out.length)) / this.binHz));
      let peak = 0;
      for (let bin = from; bin < to && bin < this.spectrum.length; bin++) peak = Math.max(peak, this.spectrum[bin]);
      out[i] = Math.pow(peak / 255, 1.7);
    }
    return out;
  }

  close() {
    try { if (this.source) this.source.disconnect(); } catch (error) { /* already closed */ }
    this.ok = false;
  }
}

const NOW_ICONS = {
  Heard: "ear", Held: "typing", Holding: "timer", Released: "corner",
  Dropped: "ban", Executed: "check-circle", Reused: "history", Said: "volume", Error: "alert",
};

const GATE_LABELS = {
  Waiting: "Waiting for a request",
  Heard: "Heard",
  Held: "Turn held",
  Released: "Turn released",
  Holding: "Holding the call",
  Executed: "Executed",
  Dropped: "Dropped before it was logged",
  Reused: "Reused, not run again",
  Error: "Tool failed",
  Said: "Said",
};

// Which of the four steps a gate state lights. "skip" marks a turn that never needed a tool.
const STEPS = {
  Heard: { heard: "on" },
  Held: { heard: "done", hold: "on" },
  Released: { heard: "done", hold: "done" },
  Holding: { heard: "done", hold: "on" },
  Executed: { heard: "done", hold: "done", decide: "on" },
  Dropped: { heard: "done", hold: "done", decide: "on" },
  Reused: { heard: "done", hold: "done", decide: "on" },
  Error: { heard: "done", hold: "done", decide: "on" },
};

const OUTCOMES = {
  dropped: { chip: "dropped", title: "Dropped", word: "Dropped", icon: "ban", now: "Dropped", pulse: "dropped" },
  executed: { chip: "executed", title: "Executed", word: "Executed", icon: "check-circle", now: "Executed", pulse: "executed" },
  failed: { chip: "dropped", title: "Tool failed", word: "Failed", icon: "alert", now: "Error", pulse: "dropped" },
  reused: { chip: "reused", title: "Reused", word: "Reused", icon: "history", now: "Reused", pulse: "reused" },
};

const SOURCES = { LLM: "language model", TTS: "voice", STT: "speech recognition" };

// Why a held call did not run, in the person's terms.
function droppedReason(reason) {
  const text = String(reason || "");
  if (text.includes("changed or cancelled")) return "Your later words changed or cancelled it, so it was not logged.";
  if (text.includes("newer value")) return "A newer value for the same device replaced it before it ran.";
  if (text.startsWith("re-check") || text.includes("no re-check")) return "It could not be re-checked, so it did not run; the agent plans again from your words.";
  return "You said more before it ran, so it was not logged.";
}

/** What a worker error means for the person, or null when it fixes itself. */
export function workerProblem(record) {
  const text = String(record.error || "");
  const part = SOURCES[record.source] || "model";
  if (text.includes("inference_quota_exceeded") || /\b429\b|Too Many Requests/.test(text)) {
    return "LiveKit Inference credit for the " + part + " is used up on this project, so the agent can hear you but cannot answer.";
  }
  if (record.recoverable !== false) return null;
  return "The agent's " + part + " failed: " + text.slice(0, 140);
}

function wobble(time, seed) {
  return 0.5 + 0.25 * Math.sin(time * 7.3 + seed * 1.7) + 0.25 * Math.sin(time * 3.1 + seed * 0.9);
}

export class Conversation {
  /**
   * @param {{mode: string, onEvent?: Function, clock?: Function}} options
   *   onEvent receives browser-side notes ({kind, message, cat, icon, data}).
   */
  constructor({ mode, onEvent = () => {}, clock = nowSeconds }) {
    this.mode = mode;
    this.onEvent = onEvent;
    this.clock = clock;
    this.el = {
      root: document.querySelector(".session"),
      pill: $("status-pill"), pillText: $("status-text"),
      orbWrap: $("orb-wrap"), orbCanvas: $("orb"), orbPulse: $("orb-pulse"),
      frameNote: $("frame-note"),
      bars: $("bars"), hint: $("status"),
      now: $("now"), nowKind: $("now-kind"), nowText: $("now-text"), nowTime: $("now-time"), nowIcon: $("now-icon"),
      pipeline: $("pipeline"), toolStep: $("tool-step"),
      gateWindow: $("gate-window"), gateHistory: $("gate-history"), gateLog: $("gate-log"),
      quiet: $("quiet"), quietBar: $("quiet-bar"), quietValue: $("quiet-value"), quietLabel: $("quiet-label"),
      micWave: $("mic-wave"), elapsed: $("elapsed"),
    };
    this.transcript = new TranscriptView();
    this.orb = new Orb(this.el.orbCanvas, { reduceMotion });
    this.orb.radius = mode === "camera" ? 0.6 : 0.56;
    if (!this.orb.ok) this.el.orbWrap.classList.add("no-webgl");
    mountStylePicker($("viz-picker"), this.orb);
    this.bars = [];
    for (let i = 0; i < BAR_COUNT; i++) {
      const bar = document.createElement("i");
      this.el.bars.append(bar);
      this.bars.push(bar);
    }
    this.bandBuffer = new Float32Array(BAR_COUNT / 2);
    this.barHeights = new Float32Array(BAR_COUNT);
    this.micHistory = new Array(26).fill(0);
    this.micTick = 0;
    this.micDrawnEmpty = false;
    this.status = { tone: "", text: "", shimmer: null };
    this.lastFrame = performance.now();
    this.lastUser = "";
    this.lastElapsed = "";
    this.phase = "idle";
    this.endText = "";
    this.meters = { mic: null, agent: null };
    this.micMuted = false;
    this.connectedAt = 0;
    this.agentLabel = "the agent";
    this.statusPrefix = "";
    this.quietSeconds = 0.6;
    this.waitingOverride = "";
    this.pending = new Map();
    this.quiet = { active: false, seconds: 0.6, state: "holding", shownUntil: 0, value: 0, tool: "" };
    this.readPalette();
    this.orb.light = document.documentElement.dataset.theme === "light" ? 1 : 0;
    window.addEventListener("interra:theme", (event) => { this.orb.light = event.detail === "light" ? 1 : 0; this.readPalette(); });
    this.reset(null);
  }

  // ---------- lifecycle ----------

  reset(base) {
    clearTimeout(this.txTimer);
    this.agentPresent = false;
    this.agentState = null;
    this.agentFromAttribute = false;
    this.userSpeaking = false;
    this.quietSince = null;
    this.turnHadTool = false;
    this.turnOutcome = null;
    this.turnHeld = false;
    this.lastDropped = null;
    this.txSource = null;
    this.txBuffer = [];
    this.txTimer = 0;
    this.traceUserSegment = 0;
    this.traceAgentSegment = 0;
    this.traceSeen = false;
    this.sequence = 0;
    this.lastNowKind = "";
    this.pending.clear();
    Object.assign(this.quiet, { active: false, value: 0, state: "holding", shownUntil: 0, tool: "" });
    this.el.quiet.classList.remove("keep");
    this.transcript.clear();
    this.transcript.setBase(base);
    this.base = base;
    if (this.el.gateHistory) this.el.gateHistory.replaceChildren();
    if (this.el.gateLog) this.el.gateLog.hidden = true;
    this.setGate("Waiting");
  }

  /** The configured quiet window, shown before the first held call reports its own. */
  setQuietSeconds(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value <= 0) return;
    this.quietSeconds = value;
    if (this.el.gateWindow) this.el.gateWindow.textContent = fmtNumber(value) + " s";
    if (this.lastNowKind === "Waiting") this.setGate("Waiting");
  }

  start() {
    requestAnimationFrame((now) => this.frame(now));
  }

  hint(text, isError = false) {
    this.el.hint.textContent = text;
    this.el.hint.classList.toggle("err", isError);
  }

  note(kind, message, { cat = "session", icon = "activity", data, label } = {}) {
    this.onEvent({ time: this.clock(), kind, message, cat, icon, data, label });
  }

  // ---------- records ----------

  onRecord(record, source) {
    if (!record || typeof record !== "object") return;
    this.traceSeen = true;
    switch (record.kind) {
      case "user_state": {
        const speaking = record.state === "speaking";
        if (speaking && !this.userSpeaking && this.lastNowKind === "Said") { this.turnHadTool = false; this.turnOutcome = null; this.turnHeld = false; }
        this.userSpeaking = speaking;
        this.quietSince = speaking ? null : record.time;
        break;
      }
      case "agent_state":
        this.setAgentState(record.state, source === "replay" ? "replay" : "trace");
        break;
      case "transcript":
        if (record.is_final && !this.userSpeaking) this.quietSince = record.time;
        if (record.transcript) this.setGate("Heard", { text: record.transcript, quote: true, time: record.time });
        this.traceTranscript(record);
        break;
      case "conversation_item":
        if (record.role === "assistant" && record.text) {
          this.setGate("Said", { text: record.text, quote: true, time: record.time });
          if (this.txSource === "stream" && record.interrupted) this.transcript.markInterrupted("agent");
        }
        this.traceTranscript(record);
        break;
      case "turn_held":
        this.turnHeld = true;
        this.setGate("Held", { text: record.transcript || "", quote: true, time: record.time, note: "It sounded unfinished, so the agent waits for the rest before it plans a call." });
        this.transcript.chip("held:" + ++this.sequence, "held", "Turn held", "", { icon: "typing", time: record.time });
        this.pulse("held");
        break;
      case "turn_released":
        this.setGate("Released", { text: record.transcript || "", quote: true, time: record.time, note: "Released after " + (record.reason || "silence") + "." });
        break;
      case "tool_call_pending":
        this.startPending(record);
        break;
      case "tool_call_rechecked":
        this.setGate("Holding", {
          call: record.call, time: record.time,
          note: "You said more: “" + (record.later || "") + "”. " + (record.run ? "It still stands." : "It no longer stands."),
        });
        break;
      case "tool_call_dropped":
        this.resolvePending(record, "dropped");
        break;
      case "tool_call_deduplicated":
        this.resolvePending(record, "reused");
        break;
      case "tool_call":
        this.resolvePending(record, record.status === "failed" ? "failed" : "executed");
        break;
      case "camera_frame":
        this.showFrame(record);
        break;
      case "session_error": {
        const message = source === "replay" ? null : workerProblem(record);
        if (message) this.hint(message, true);
        break;
      }
      default:
        break;
    }
  }

  setAgentState(state, from) {
    if (!state) return;
    if (from === "attribute") this.agentFromAttribute = true;
    else if (from === "trace" && this.agentFromAttribute && this.phase === "live") return;
    const previous = this.agentState;
    this.agentState = state;
    if (state === "speaking" && this.txSource === "trace") this.transcript.showDots(this.clock());
    if (previous === "speaking" && state !== "speaking") this.transcript.removeDotsSoon();
  }

  startPending(record) {
    this.turnHadTool = true;
    this.turnHeld = true;
    const key = callKey(record.call);
    const chipKey = "call:" + ++this.sequence;
    const seconds = Number(record.quiet_seconds ?? 0.6) || 0.6;
    this.transcript.chip(chipKey, "holding", "Holding", (record.call && record.call.function) || "tool", { icon: "timer", time: record.time });
    const waiting = this.pending.get(key) || [];
    waiting.push(chipKey);
    this.pending.set(key, waiting);
    Object.assign(this.quiet, { active: true, seconds, state: "holding", value: 0, tool: (record.call && record.call.function) || "the call" });
    this.el.quiet.dataset.state = "holding";
    this.el.quiet.classList.add("keep");
    this.setQuietSeconds(seconds);
    this.setGate("Holding", {
      call: record.call, changed: this.changedFrom(record.call), time: record.time,
      note: "Runs after " + fmtNumber(seconds) + " s of quiet. If you say more, it is re-checked against everything you said.",
    });
  }

  resolvePending(record, outcome) {
    this.turnHadTool = true;
    const meta = OUTCOMES[outcome];
    const key = callKey(record.call);
    const waiting = this.pending.get(key);
    const held = Boolean(waiting && waiting.length);
    const chipKey = held ? waiting.shift() : "call:" + ++this.sequence;
    if (waiting && !waiting.length) this.pending.delete(key);
    const droppedNote = droppedReason(record.reason);
    const notes = {
      dropped: droppedNote,
      reused: "Identical call in this room; the first result was reused.",
      executed: [callDuration(record.call), resultSummary(record.result)].filter(Boolean).join(" · "),
      failed: resultSummary(record.result),
    };
    this.transcript.chip(chipKey, meta.chip, meta.title, (record.call && record.call.function) || "tool", { icon: meta.icon, time: record.time });
    const focusNotes = {
      dropped: droppedNote,
      reused: "Identical call; the first result was reused.",
      executed: notes.executed,
      failed: notes.failed,
    };
    this.turnOutcome = outcome;
    const changed = outcome === "dropped" ? new Set() : this.changedFrom(record.call);
    this.setGate(meta.now, { call: record.call, changed, time: record.time, note: focusNotes[outcome] });
    this.addHistory(outcome, record.call, record.time, changed);
    if (outcome === "dropped") this.lastDropped = record.call;
    this.pulse(meta.pulse);
    if (held && this.pending.size === 0) {
      this.quiet.active = false;
      this.quiet.state = outcome === "failed" ? "dropped" : outcome;
      if (outcome !== "dropped") this.quiet.value = 1;
      this.el.quiet.dataset.state = this.quiet.state;
      this.el.quiet.classList.add("keep");
      this.quiet.shownUntil = performance.now() + 2800;
      this.updateQuiet();
    }
  }

  showFrame(record) {
    const text = record.attached
      ? "Frame attached to your turn" + (record.width ? " · " + record.width + " × " + record.height : "")
      : "No camera frame reached this turn";
    if (this.el.frameNote) {
      this.el.frameNote.textContent = text;
      this.el.frameNote.classList.add("flash");
      clearTimeout(this.frameTimer);
      this.frameTimer = setTimeout(() => {
        this.el.frameNote.classList.remove("flash");
        this.el.frameNote.textContent = "Your latest frame joins your next spoken turn.";
      }, 2400);
    }
    this.transcript.chip("frame:" + ++this.sequence, record.attached ? "frame" : "system", record.attached ? "Frame attached" : "No frame", "", { icon: "image", time: record.time });
    if (record.attached) this.pulse("frame");
  }

  // ---------- transcript sources ----------

  useStreamTranscript() {
    if (this.txSource === "stream") return false;
    const first = this.txSource === null;
    this.txSource = "stream";
    this.txBuffer = [];
    clearTimeout(this.txTimer);
    this.txTimer = 0;
    this.transcript.removeDots();
    return first;
  }

  traceTranscript(record) {
    if (this.txSource === "stream") return;
    if (this.txSource === null) {
      this.txBuffer.push(record);
      if (!this.txTimer) {
        this.txTimer = setTimeout(() => {
          this.txTimer = 0;
          if (this.txSource !== null) return;
          this.txSource = "trace";
          this.note("transcript_source", "No LiveKit transcription arrived; the transcript is built from the worker trace", { icon: "info", label: "Transcript" });
          const buffered = this.txBuffer;
          this.txBuffer = [];
          for (const item of buffered) this.applyTraceTranscript(item);
        }, 2500);
      }
      return;
    }
    this.applyTraceTranscript(record);
  }

  applyTraceTranscript(record) {
    if (record.kind === "transcript") {
      const text = (record.transcript || "").trim();
      if (!text) return;
      this.transcript.upsert("tu-" + this.traceUserSegment, "user", text, { final: Boolean(record.is_final), time: record.time });
      if (record.is_final) this.traceUserSegment += 1;
    } else if (record.kind === "conversation_item" && record.role === "assistant" && record.text) {
      this.transcript.upsert("ta-" + this.traceAgentSegment++, "agent", record.text, { final: true, time: record.time, interrupted: Boolean(record.interrupted) });
    }
  }

  // ---------- gate card ----------

  waitingText() {
    if (this.waitingOverride) return this.waitingOverride;
    return "A tool call waits here until you have been quiet for " + fmtNumber(this.quietSeconds) + " s.";
  }

  /** Show one gate state: a quote, a structured call, or the waiting explanation. */
  /** Argument keys that differ from the call the gate dropped last, for the same tool. */
  changedFrom(call) {
    const previous = this.lastDropped;
    const changed = new Set();
    if (!previous || !call || previous.function !== call.function) return changed;
    const before = previous.args || {};
    const after = call.args || {};
    for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
      if (JSON.stringify(before[key]) !== JSON.stringify(after[key])) changed.add(key);
    }
    return changed;
  }

  setGate(kind, { text = "", call = null, changed = new Set(), note = "", time, label, quote = false } = {}) {
    const el = this.el;
    const kindChanged = this.lastNowKind !== kind;
    this.lastNowKind = kind;
    el.now.dataset.kind = kind;
    el.nowKind.textContent = label || GATE_LABELS[kind] || kind;
    const before = el.nowText.textContent;
    const parts = [];
    if (call) parts.push(callBlock(call, changed));
    else parts.push(kind === "Waiting" ? this.waitingText() : text);
    if (note) parts.push(h("div", "focus-note", note));
    el.nowText.className = "focus-body" + (kind === "Waiting" ? " muted" : "") + (quote && text && !call ? " quote" : "");
    el.nowText.replaceChildren(...parts);
    if (kindChanged && before !== el.nowText.textContent) restartAnimation(el.nowText, "swap");
    if (kindChanged) {
      // Waiting has no state to show, so it gets no icon.
      el.nowIcon.hidden = kind === "Waiting";
      el.nowIcon.replaceChildren(...(kind === "Waiting" ? [] : [svgIcon(NOW_ICONS[kind] || "check-circle")]));
    }
    el.nowTime.textContent = typeof time === "number" && this.base !== null ? fmtClock(time - this.base) : "";
    this.updateSteps(kind);
  }

  updateSteps(kind) {
    const tool = this.turnHadTool;
    const steps = { ...(kind === "Said"
      ? { heard: "done", hold: tool ? "done" : "skip", decide: tool ? "done" : "skip", said: "on" }
      : STEPS[kind] || {}) };
    // A call that ran without being held (no gate, or a run from before it) skips the hold step.
    if (steps.hold === "done" && !this.turnHeld) steps.hold = "skip";
    const outcome = steps.decide === "on" || steps.decide === "done" ? this.turnOutcome : null;
    for (const step of this.el.pipeline.children) {
      step.dataset.state = steps[step.dataset.step] || "";
      if (step.dataset.step === "decide") {
        if (outcome) step.dataset.outcome = outcome;
        else delete step.dataset.outcome;
      }
    }
    this.el.toolStep.textContent = outcome ? OUTCOMES[outcome].word : "Decision";
  }

  addHistory(outcome, call, time, changed = new Set()) {
    const list = this.el.gateHistory;
    if (!list) return;
    const meta = OUTCOMES[outcome];
    const item = h("li");
    item.dataset.outcome = outcome;
    const tag = h("span", "h-tag");
    tag.append(svgIcon(meta.icon, "ic xs"), meta.word);
    const name = h("code", "h-fn", (call && call.function) || "tool");
    const detail = h("span", "h-args");
    const entries = Object.entries((call && call.args) || {});
    // What changed since the dropped call leads, so it survives truncation.
    entries.sort(([a], [b]) => Number(changed.has(b)) - Number(changed.has(a)));
    entries.forEach(([key, value], index) => {
      if (index) detail.append(" · ");
      const text = key.replace(/_/g, " ") + " " + (typeof value === "string" ? value : JSON.stringify(value));
      detail.append(changed.has(key) ? h("b", null, text) : text);
    });
    if (!entries.length) detail.textContent = "no arguments";
    item.title = callText(call);
    item.append(tag, name, h("time", null, typeof time === "number" && this.base !== null ? fmtClock(time - this.base) : ""), detail);
    list.prepend(item);
    while (list.children.length > 12) list.lastElementChild.remove();
    if (this.el.gateLog) this.el.gateLog.hidden = false;
  }

  pulse(kind) {
    const color = { executed: "var(--executed)", dropped: "var(--dropped)", reused: "var(--reused)", held: "var(--held)", frame: "var(--cool)" }[kind];
    if (!color || !this.el.orbPulse) return;
    this.el.orbPulse.style.setProperty("--pulse", color);
    restartAnimation(this.el.orbPulse, "go");
  }

  updateQuiet() {
    const quiet = this.quiet;
    const el = this.el;
    if (!quiet.active) {
      if (el.quiet.classList.contains("keep") && performance.now() > quiet.shownUntil) el.quiet.classList.remove("keep");
      if (!el.quiet.classList.contains("keep")) return;
    } else if (this.userSpeaking || this.quietSince === null) {
      quiet.value = 0;
      el.quietLabel.textContent = "Waiting for you to finish";
    } else {
      quiet.value = clamp((this.clock() - this.quietSince) / quiet.seconds);
      el.quietLabel.textContent = "Quiet window";
    }
    el.quietBar.style.transform = "scaleX(" + quiet.value.toFixed(3) + ")";
    el.quietValue.textContent = (quiet.value * quiet.seconds).toFixed(1) + " / " + quiet.seconds.toFixed(1) + " s";
  }

  // ---------- visuals ----------

  readPalette() {
    const style = getComputedStyle(document.documentElement);
    this.palette = { warm: style.getPropertyValue("--warm").trim() || "#ededed", muted: style.getPropertyValue("--text-3").trim() || "#666" };
    this.micDrawnEmpty = false;
  }

  visualState() {
    if (this.phase === "connecting") return "connecting";
    if (this.phase === "live") {
      if (!this.agentPresent || this.agentState === "initializing") return "connecting";
      if (this.quiet.active) return "holding";
      return this.agentState || "listening";
    }
    if (this.phase === "replay") {
      if (this.quiet.active) return "holding";
      return this.agentState || "idle";
    }
    return "idle";
  }

  orbMix(state) {
    if (state === "speaking") return 1;
    if (state === "thinking") return 0.64;
    if (state === "holding") return 0.42;
    if (state === "listening") return this.userSpeaking ? 0.02 : 0.32;
    return this.userSpeaking ? 0.04 : 0.5;
  }

  drawBars(time, state, input, output) {
    let tone = "idle";
    let meter = null;
    let level = 0;
    const highlight = new Set();
    const heights = this.barHeights;
    heights.fill(0.12);
    if (state === "speaking") { tone = "agent"; meter = this.meters.agent; level = output; }
    else if (this.userSpeaking && (this.phase === "live" || this.phase === "replay")) { tone = "user"; meter = this.micMuted ? null : this.meters.mic; level = input; }
    if (tone !== "idle") {
      const half = BAR_COUNT / 2;
      if (meter && meter.ok) meter.bands(this.bandBuffer);
      for (let i = 0; i < half; i++) {
        const value = meter && meter.ok ? this.bandBuffer[i] : wobble(time, i) * (0.35 + level * 0.9);
        const height = 0.12 + 0.88 * clamp(value * (1.15 - i / (half * 2.2)));
        heights[half - 1 - i] = height;
        heights[half + i] = height;
      }
    } else if (state === "connecting") {
      const step = Math.floor((time * 1000) / (1600 / (BAR_COUNT / 2))) % (BAR_COUNT / 2);
      highlight.add(step).add(BAR_COUNT - 1 - step);
      for (const index of highlight) heights[index] = 0.42;
    } else if (state === "listening") {
      if (Math.floor(time * 2) % 2 === 0) highlight.add(BAR_COUNT / 2 - 1).add(BAR_COUNT / 2);
    } else if (state === "thinking" || state === "holding") {
      tone = "think";
      const offset = Math.floor((time * 1000) / 150) % 4;
      highlight.add(BAR_COUNT / 2 - 1 - offset).add(BAR_COUNT / 2 + offset);
      for (const index of highlight) heights[index] = 0.3;
    } else {
      for (let i = 0; i < BAR_COUNT; i++) heights[i] = 0.12 + 0.06 * (0.5 + 0.5 * Math.sin(time * 1.4 + i * 0.45));
    }
    if (this.el.bars.dataset.tone !== tone) this.el.bars.dataset.tone = tone;
    for (let i = 0; i < BAR_COUNT; i++) {
      this.bars[i].style.transform = "scaleY(" + heights[i].toFixed(3) + ")";
      const on = highlight.has(i);
      if (this.bars[i].classList.contains("hl") !== on) this.bars[i].classList.toggle("hl", on);
    }
  }

  drawMicWave(dt, level, active) {
    const canvas = this.el.micWave;
    if (!canvas) return;
    this.micTick += dt;
    if (this.micTick >= 0.05) {
      this.micTick = 0;
      this.micHistory.shift();
      this.micHistory.push(active ? level : 0);
    }
    const empty = this.micHistory.every((value) => value < 0.005);
    if (empty && this.micDrawnEmpty) return;
    this.micDrawnEmpty = empty;
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    context.clearRect(0, 0, width, height);
    const count = this.micHistory.length;
    const gap = 2.4;
    const barWidth = (width - gap * (count - 1)) / count;
    context.fillStyle = this.micMuted || !active ? this.palette.muted : this.palette.warm;
    context.globalAlpha = this.micMuted || !active ? 0.55 : 1;
    for (let i = 0; i < count; i++) {
      const barHeight = Math.max(4, this.micHistory[i] * (height - 6));
      const x = i * (barWidth + gap);
      const y = (height - barHeight) / 2;
      if (context.roundRect) { context.beginPath(); context.roundRect(x, y, barWidth, barHeight, barWidth / 2); context.fill(); }
      else context.fillRect(x, y, barWidth, barHeight);
    }
    context.globalAlpha = 1;
  }

  updateStatus() {
    const state = this.visualState();
    let tone = "idle";
    let text = "Ready";
    let shimmer = false;
    if (this.phase === "ended") {
      text = this.endText || "Call ended";
    } else if (this.phase !== "idle") {
      if (state === "connecting") {
        tone = "wait";
        shimmer = true;
        text = this.phase === "connecting" ? "Connecting…" : this.agentPresent ? "Agent is starting…" : "Waiting for " + this.agentLabel + " to join…";
      } else if (state === "holding") {
        tone = "hold";
        text = "Holding " + this.quiet.tool + " until you are quiet";
      } else if (state === "thinking") {
        tone = "think";
        shimmer = true;
        text = "Thinking…";
      } else if (state === "speaking") {
        tone = "agent";
        text = "Speaking";
      } else if (this.userSpeaking) {
        tone = "user";
        text = "Listening to you";
      } else if (this.phase === "replay" && state === "idle") {
        tone = "wait";
        text = "Starting the recording";
      } else {
        tone = "live";
        text = "Listening";
      }
    }
    if (this.statusPrefix) { text = this.statusPrefix + text; shimmer = false; }
    const status = this.status;
    if (!this.el.pill) return; // the live pages show state through the orb, bars and hint instead
    if (status.tone !== tone) { status.tone = tone; this.el.pill.dataset.tone = tone; }
    if (status.text !== text) { status.text = text; this.el.pillText.textContent = text; }
    if (status.shimmer !== shimmer) { status.shimmer = shimmer; this.el.pillText.classList.toggle("on", shimmer && !reduceMotion); }
  }

  frame(now) {
    const dt = Math.min(0.1, Math.max(0, (now - this.lastFrame) / 1000));
    this.lastFrame = now;
    const time = now / 1000;
    const state = this.visualState();
    const micLevel = this.meters.mic ? this.meters.mic.sample() : 0;
    const agentLevel = this.meters.agent ? this.meters.agent.sample() : 0;
    let input = this.micMuted ? 0 : micLevel;
    let output = agentLevel;
    if (!this.meters.agent && state === "speaking") output = 0.3 + 0.35 * wobble(time, 3);
    if (!this.meters.mic && this.userSpeaking) input = 0.25 + 0.3 * wobble(time * 1.2, 7);
    if (state === "idle" && !this.userSpeaking) output = Math.max(output, 0.05 + 0.04 * Math.sin(time * 1.3));
    const think = state === "thinking" ? 1 : state === "holding" ? 0.55 : state === "connecting" ? 0.35 : 0;
    this.orb.set({ input, output, mix: this.orbMix(state), think });
    this.orb.render(dt);
    if (!this.orb.ok) this.el.orbWrap.style.setProperty("--lvl", Math.max(input, output).toFixed(3));
    const user = this.userSpeaking || (this.phase === "live" && micLevel > 0.32) ? "speaking" : "quiet";
    if (user !== this.lastUser) { this.lastUser = user; this.el.orbWrap.dataset.user = user; }
    this.drawBars(time, state, input, output);
    this.drawMicWave(dt, micLevel, this.phase === "live" && !this.micMuted);
    this.updateQuiet();
    this.updateStatus();
    if (this.el.elapsed && this.phase === "live" && this.connectedAt) {
      const elapsed = fmtShort((now - this.connectedAt) / 1000);
      if (elapsed !== this.lastElapsed) { this.lastElapsed = elapsed; this.el.elapsed.textContent = elapsed; }
    }
    if (this.onFrame) this.onFrame(now);
    requestAnimationFrame((next) => this.frame(next));
  }
}

