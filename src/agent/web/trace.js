// How worker trace records read on screen. Used by the session pages and the
// Logs page, so a record is described the same way everywhere.

import { fmtNumber, h } from "./shell.js";

export function fmtValue(value) {
  if (typeof value === "string") return "“" + value + "”";
  if (typeof value === "number") return fmtNumber(value);
  if (value === null || value === undefined) return "none";
  return JSON.stringify(value);
}

function argBits(call) {
  const args = (call && call.args) || {};
  return Object.entries(args).map(([key, value]) => key.replace(/_/g, " ") + " " + fmtValue(value));
}

export function callText(call) {
  const name = (call && call.function) || "tool";
  const bits = argBits(call);
  return bits.length ? name + " · " + bits.join(" · ") : name;
}

/** The function name as code, then its arguments. */
export function callNode(call) {
  const fragment = document.createDocumentFragment();
  fragment.append(h("code", null, (call && call.function) || "tool"));
  const bits = argBits(call);
  if (bits.length) fragment.append(" · " + bits.join(" · "));
  return fragment;
}

/** The call as a block: the function name, then one cell per argument.
 *  Keys in ``changed`` differ from the call the gate dropped just before. */
export function callBlock(call, changed = new Set()) {
  const wrap = h("div", "call");
  wrap.append(h("code", "call-fn", (call && call.function) || "tool"));
  const list = h("dl", "call-args");
  const entries = Object.entries((call && call.args) || {});
  if (!entries.length) list.append(h("div", "none", "No arguments"));
  for (const [key, value] of entries) {
    const cell = h("div", changed.has(key) ? "changed" : null);
    const shown = typeof value === "string" ? value : typeof value === "number" ? fmtNumber(value) : JSON.stringify(value);
    cell.append(h("dt", null, key.replace(/_/g, " ")), h("dd", null, shown));
    list.append(cell);
  }
  wrap.append(list);
  return wrap;
}

export function callKey(call) {
  return ((call && call.function) || "") + " " + JSON.stringify((call && call.args) || {});
}

export function callDuration(call) {
  if (!call || typeof call.timestamp_start !== "number" || typeof call.timestamp_end !== "number") return "";
  return Math.max(0, Math.round((call.timestamp_end - call.timestamp_start) * 1000)) + " ms";
}

export function resultSummary(result) {
  if (result === null || result === undefined) return "no result";
  if (typeof result !== "object") return String(result);
  const parts = [];
  if (result.status) parts.push(String(result.status));
  const list = Object.values(result).find(Array.isArray);
  if (list) parts.push(list.length + (list.length === 1 ? " item" : " items"));
  if (result.message) parts.push(String(result.message));
  return parts.join(" · ") || "done";
}

/** The pinned evaluator's own explanation; nothing here re-scores a call. */
export function describeResult(result) {
  if (result.passed) return "Tool selection and arguments matched.";
  const parts = [];
  const checks = result.checks || {};
  if (checks.tool_selection) parts.push(checks.tool_selection.passed ? "tool selection matched" : "tool selection did not match");
  for (const detail of (checks.argument_accuracy && checks.argument_accuracy.details) || []) {
    if (!detail.passed && detail.explanation) parts.push(detail.explanation);
  }
  return parts.join("; ") || result.failure_reason || "not passed";
}

export function jsonBlock(value) {
  const pre = document.createElement("pre");
  const text = JSON.stringify(value, null, 2) ?? String(value);
  const pattern = /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) pre.append(text.slice(last, match.index));
    const token = match[0];
    let className = "j-num";
    if (token[0] === "\"") className = match[2] ? "j-key" : "j-str";
    else if (token === "true" || token === "false") className = "j-bool";
    else if (token === "null") className = "j-null";
    pre.append(h("span", className, token));
    last = match.index + token.length;
  }
  if (last < text.length) pre.append(text.slice(last));
  return pre;
}

export const CATEGORIES = [
  ["all", "All", null],
  ["speech", "Speech", "var(--heard)"],
  ["gate", "Gate", "var(--held)"],
  ["tool", "Tools", "var(--executed)"],
  ["agent", "Agent", "var(--cool)"],
  ["session", "Session", "var(--session)"],
  ["error", "Errors", "var(--error)"],
];

const USER_STATE = { speaking: "Started speaking", listening: "Stopped speaking", away: "Away" };
const AGENT_STATE = { initializing: "Starting", idle: "Idle", listening: "Listening", thinking: "Thinking", speaking: "Speaking" };
const pick = (value, record) => (typeof value === "function" ? value(record) : value);

const KINDS = {
  session_started: { cat: "session", icon: "login", label: "Session", msg: () => "Worker session started" },
  session_closed: {
    cat: (r) => (r.failed ? "error" : "session"), icon: "logout", label: "Closed",
    msg: () => "Session closed",
    sub: (r) => [r.reason ? String(r.reason).replace("CloseReason.", "").toLowerCase().replace(/_/g, " ") : "", r.failed ? "failed" : ""].filter(Boolean).join(" · "),
  },
  session_cleanup: { cat: "session", icon: "logout", label: "Cleanup", msg: () => "Speech providers released" },
  session_drain_started: { cat: "session", icon: "hourglass", label: "Drain", msg: () => "Participant left; finishing pending speech" },
  session_drain_completed: { cat: "session", icon: "check", label: "Drained", msg: () => "Pending speech finished" },
  session_drain_timeout: { cat: "error", icon: "alert", label: "Drain timeout", msg: () => "Pending speech did not finish in time" },
  participant_disconnected: { cat: "session", icon: "logout", label: "Left", msg: () => "The participant left the room" },
  provider_cooldown_started: { cat: "session", icon: "hourglass", label: "Cooldown", msg: (r) => "Waiting " + fmtNumber(r.seconds ?? 0) + " s before the next room" },
  provider_cooldown_completed: { cat: "session", icon: "check", label: "Ready", msg: () => "Worker ready for the next room" },
  user_state: { cat: "speech", icon: "user", label: "You", msg: (r) => USER_STATE[r.state] || String(r.state) },
  agent_state: { cat: "agent", icon: "bot", label: "Agent", msg: (r) => AGENT_STATE[r.state] || String(r.state) },
  transcript: { cat: "speech", icon: "ear", label: (r) => (r.is_final ? "Heard" : "Hearing"), msg: (r) => r.transcript || "" },
  conversation_item: {
    cat: (r) => (r.role === "assistant" ? "agent" : "speech"),
    icon: (r) => (r.role === "assistant" ? "volume" : "message"),
    label: (r) => (r.role === "assistant" ? "Said" : "Turn"),
    msg: (r) => r.text || "",
    sub: (r) => (r.role === "assistant" ? (r.interrupted ? "Interrupted" : "") : "Committed user turn"),
  },
  turn_held: { cat: "gate", icon: "typing", label: "Turn held", msg: (r) => "“" + (r.transcript || "") + "”", sub: () => "Sounds unfinished; waiting for the rest" },
  turn_released: { cat: "gate", icon: "corner", label: "Released", msg: (r) => "“" + (r.transcript || "") + "”", sub: (r) => "Held turn released after " + (r.reason || "silence") },
  turn_release_failed: { cat: "error", icon: "alert", label: "Release failed", msg: (r) => r.error || "", sub: (r) => r.reason || "" },
  tool_call_pending: {
    cat: "gate", icon: "timer", label: "Holding", call: true,
    sub: (r) => "Runs after " + fmtNumber(r.quiet_seconds ?? 0.6) + " s of quiet",
  },
  tool_call_rechecked: {
    cat: "gate", icon: "ear", label: "Re-checked", call: true,
    sub: (r) => (r.run ? "Still stands" : "Does not run") + " · heard “" + (r.later || "") + "”",
  },
  tool_call_dropped: { cat: "gate", icon: "ban", label: "Dropped", call: true, sub: (r) => "Not logged · " + (r.reason || "user continued") },
  tool_call_deduplicated: { cat: "gate", icon: "history", label: "Reused", call: true, sub: () => "Identical call in this room · not run again" },
  tool_call: {
    cat: (r) => (r.status === "failed" ? "error" : "tool"),
    icon: (r) => (r.status === "failed" ? "alert" : "check-circle"),
    label: (r) => (r.status === "failed" ? "Tool failed" : "Executed"),
    call: true,
    sub: (r) => [callDuration(r.call), resultSummary(r.result)].filter(Boolean).join(" · "),
  },
  camera_frame: {
    cat: "agent", icon: "image", label: "Frame",
    msg: (r) => (r.attached ? "Camera frame attached to this turn" : "No camera frame for this turn"),
    sub: (r) => (r.attached && r.width ? r.width + " × " + r.height : ""),
  },
  session_error: {
    cat: "error", icon: "alert", label: "Error",
    msg: (r) => String(r.error || "unknown error"),
    sub: (r) => [r.source, r.recoverable === false ? "not recoverable" : r.recoverable ? "recoverable" : ""].filter(Boolean).join(" · "),
  },
};

export function entryFromRecord(record, source) {
  const meta = KINDS[record.kind];
  if (!meta) {
    const { time, kind, room, ...rest } = record;
    return {
      t: typeof time === "number" ? time : Date.now() / 1000, kind: String(kind || "event"), cat: "session", icon: "activity",
      label: String(kind || "event").replace(/_/g, " "), msg: JSON.stringify(rest).slice(0, 300), sub: "",
      src: source, interim: false, trace: true, data: record,
    };
  }
  return {
    t: typeof record.time === "number" ? record.time : Date.now() / 1000,
    kind: record.kind,
    cat: pick(meta.cat, record),
    icon: pick(meta.icon, record),
    label: pick(meta.label, record),
    msg: meta.call ? callText(record.call) : pick(meta.msg, record),
    call: meta.call ? record.call : null,
    sub: meta.sub ? pick(meta.sub, record) : "",
    src: source,
    interim: record.kind === "transcript" && !record.is_final,
    trace: true,
    data: record,
  };
}

// Browser-side events (room connected, agent joined, mic muted) are kept per
// room so a Logs tab opened later still shows them next to the worker trace.
const CHANNEL = "interra-browser-events";
const KEY = "interra:browser:";
const LIMIT = 400;
let channel = null;

function openChannel() {
  if (!channel && "BroadcastChannel" in window) channel = new BroadcastChannel(CHANNEL);
  return channel;
}

export function storeBrowserEvent(room, event) {
  if (!room) return;
  const record = { room, ...event };
  try {
    const list = JSON.parse(localStorage.getItem(KEY + room) || "[]");
    list.push(record);
    localStorage.setItem(KEY + room, JSON.stringify(list.slice(-LIMIT)));
  } catch (error) {
    /* storage is optional */
  }
  try { openChannel()?.postMessage(record); } catch (error) { /* no other tabs */ }
}

export function loadBrowserEvents(room) {
  try { return JSON.parse(localStorage.getItem(KEY + room) || "[]"); } catch (error) { return []; }
}

export function onBrowserEvent(callback) {
  const live = openChannel();
  if (live) live.addEventListener("message", (message) => callback(message.data));
}

export function entryFromBrowserEvent(event, source = "browser") {
  return {
    t: event.time, kind: event.kind, cat: event.cat || "session", icon: event.icon || "activity",
    label: event.label || String(event.kind).replace(/_/g, " "), msg: event.message || "", sub: event.sub || "",
    src: source, interim: false, trace: false, data: event,
  };
}
