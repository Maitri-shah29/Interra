// Logs: pick a session, read its worker trace and the browser's own notes,
// one roomy row per event.

import { $, fmtClock, fmtDuration, fmtWhen, h, params, svgIcon, toast } from "./shell.js";
import { callNode, entryFromBrowserEvent, entryFromRecord, jsonBlock, loadBrowserEvents, onBrowserEvent } from "./trace.js";

// Four filters are enough: tool calls and the gate's decisions read together.
const FILTERS = [
  ["all", "All", null, null],
  ["speech", "Speech", "var(--heard)", ["speech"]],
  ["gate", "Tool calls", "var(--executed)", ["gate", "tool", "result"]],
  ["error", "Errors", "var(--error)", ["error"]],
];

const MAX_ROWS = 4000;
const el = {
  list: $("session-list"),
  kind: $("room-kind"), title: $("room-title"), meta: $("room-meta"), liveBadge: $("live-badge"),
  log: $("log"), scroll: $("log-scroll"), empty: $("log-empty"), paused: $("log-paused"),
  filters: $("filters"), search: $("log-search"), download: $("log-download"),
};

const view = {
  room: "",
  recorded: false,
  source: null,
  entries: [],
  base: null,
  filter: "all",
  query: "",
  showInterim: false,
  follow: true,
  unseen: 0,
  total: {},
  interim: {},
  buttons: new Map(),
  turn: 0,
  turnOpen: false,
  sessions: [],
  token: 0,
};

// ---------- sessions ----------

function sessionLabel(session) {
  if (session.recorded) return "housing_11 · scored run";
  return session.room;
}

function renderSessions() {
  const items = [];
  const recorded = view.sessions.filter((session) => session.recorded);
  const local = view.sessions.filter((session) => !session.recorded);
  if (recorded.length) {
    items.push(h("li", "session-group", "Recorded run"));
    for (const session of recorded) items.push(sessionItem(session));
  }
  items.push(h("li", "session-group", "On this machine"));
  if (!local.length) items.push(h("li", "sessions-empty", "No sessions in the local trace yet. Start one from the Voice or Camera page."));
  for (const session of local) items.push(sessionItem(session));
  el.list.replaceChildren(...items);
}

function sessionItem(session) {
  const item = h("li");
  const link = h("a", "sess");
  link.href = "/logs?room=" + encodeURIComponent(session.room);
  if (session.room === view.room) link.setAttribute("aria-current", "true");
  const icon = h("span", "sess-icon");
  icon.dataset.mode = session.recorded ? "recorded" : session.mode;
  icon.append(svgIcon(session.recorded ? "file" : { camera: "camera", voice: "audio", home: "home" }[session.mode] || "cpu"));
  if (session.live) icon.append(h("i", "live-dot"));
  const main = h("span", "sess-main");
  main.append(h("b", null, sessionLabel(session)));
  const when = session.recorded ? "4 Oct 2026" : fmtWhen(session.last);
  main.append(h("span", null, when + " · " + fmtDuration(session.last - session.first) + " · " + session.events + " events"));
  const stats = h("span", "sess-stats");
  if (session.executed) { const ok = h("em", "ok", String(session.executed)); ok.title = "Executed tool calls"; stats.append(ok); }
  if (session.dropped) { const bad = h("em", "bad", String(session.dropped)); bad.title = "Dropped tool calls"; stats.append(bad); }
  link.append(icon, main, stats);
  link.addEventListener("click", (event) => {
    event.preventDefault();
    select(session.room, true);
  });
  item.append(link);
  return item;
}

async function loadSessions() {
  try {
    const response = await fetch("/api/sessions", { cache: "no-store" });
    if (!response.ok) return;
    view.sessions = (await response.json()).sessions || [];
    renderSessions();
    updateHeader();
  } catch (error) {
    /* keep the last list */
  }
}

function currentSession() {
  return view.sessions.find((session) => session.room === view.room);
}

function updateHeader() {
  const session = currentSession();
  if (!view.room) {
    el.kind.textContent = "Logs";
    el.title.textContent = "Pick a session";
    el.meta.textContent = "";
    el.liveBadge.hidden = true;
    return;
  }
  el.title.textContent = view.recorded ? "housing_11 · scored run" : view.room;
  el.kind.textContent = view.recorded ? "Recorded run · 4 October 2026" : ({ camera: "Camera session", voice: "Voice session", home: "Home session" }[session && session.mode] || "Session");
  const live = Boolean(session && session.live);
  el.liveBadge.hidden = !live;
  el.liveBadge.dataset.on = String(live);
  el.meta.textContent = view.recorded
    ? "Room " + view.room
    : session ? fmtWhen(session.first) + " · " + fmtDuration(session.last - session.first) : "";
}

// ---------- entries ----------

function buildFilters() {
  for (const [id, label, color] of FILTERS) {
    const button = h("button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(id === view.filter));
    if (color) { button.style.setProperty("--c", color); button.append(h("i")); }
    button.append(label);
    const count = h("em", null, "");
    button.addEventListener("click", () => {
      view.filter = id;
      for (const [key, item] of view.buttons) item.button.setAttribute("aria-pressed", String(key === id));
      refilter();
    });
    el.filters.append(button);
    view.buttons.set(id, { button, count });
  }
}

function matches(entry) {
  if (entry.interim && !view.showInterim) return false;
  if (view.filter !== "all") {
    const wanted = FILTERS.find(([id]) => id === view.filter)[3];
    if (!wanted.includes(entry.cat)) return false;
  }
  if (view.query) {
    if (!entry.haystack) entry.haystack = (entry.label + " " + entry.kind + " " + entry.msg + " " + entry.sub + " " + entry.src + " " + JSON.stringify(entry.data || {})).toLowerCase();
    if (!entry.haystack.includes(view.query)) return false;
  }
  return true;
}

function turnDivider(entry) {
  // A new turn starts when you speak after the agent has answered.
  if (entry.kind === "user_state" && entry.data.state === "speaking" && !view.turnOpen) {
    view.turnOpen = true;
    view.turn += 1;
    const divider = h("li", "turn-divider");
    divider.append("Turn " + view.turn + " ", h("time", null, view.base !== null ? fmtClock(entry.t - view.base) : ""));
    return divider;
  }
  if (entry.kind === "conversation_item" && entry.data.role === "assistant") view.turnOpen = false;
  return null;
}

function renderRow(entry) {
  const row = h("li", "lrow new" + (entry.interim ? " interim" : ""));
  row.dataset.cat = entry.cat;
  row.dataset.kind = entry.kind;
  if (entry.pass !== undefined) row.dataset.pass = String(entry.pass);
  row.addEventListener("animationend", () => row.classList.remove("new"), { once: true });
  const time = h("time", null, view.base !== null ? fmtClock(entry.t - view.base) : "");
  time.title = new Date(entry.t * 1000).toLocaleString();
  const tag = h("span", "tag");
  tag.append(svgIcon(entry.icon, "ic"), entry.label);
  const body = h("div", "lrow-body");
  const msg = h("div", "lrow-msg");
  if (entry.call) msg.append(callNode(entry.call));
  else msg.textContent = entry.msg;
  body.append(msg);
  if (entry.sub) body.append(h("div", "lrow-sub", entry.sub));
  row.append(time, tag, body);
  return row;
}

function exportable(entry) {
  if (entry.trace) return entry.data;
  return { time: entry.t, kind: entry.kind, source: entry.src, message: entry.msg, ...(entry.data && entry.data.data ? { data: entry.data.data } : {}) };
}

function add(entry) {
  if (view.base === null) view.base = entry.t;
  // Browser notes and worker records interleave by time.
  let index = view.entries.length;
  while (index > 0 && view.entries[index - 1].t > entry.t) index -= 1;
  view.entries.splice(index, 0, entry);
  view.total[entry.cat] = (view.total[entry.cat] || 0) + 1;
  if (entry.interim) view.interim[entry.cat] = (view.interim[entry.cat] || 0) + 1;
  entry.row = renderRow(entry);
  entry.row.hidden = !matches(entry);
  const next = view.entries[index + 1];
  if (next) {
    el.log.insertBefore(entry.row, next.divider || next.row);
  } else {
    const divider = turnDivider(entry);
    if (divider) { entry.divider = divider; el.log.append(divider); }
    el.log.append(entry.row);
  }
  while (view.entries.length > MAX_ROWS) {
    const old = view.entries.shift();
    old.row.remove();
    if (old.divider) old.divider.remove();
    view.total[old.cat] -= 1;
    if (old.interim) view.interim[old.cat] -= 1;
  }
  updateCounts();
  if (!entry.row.hidden) {
    if (view.follow) scrollToEnd();
    else { view.unseen += 1; updatePaused(); }
  }
}

function refilter() {
  for (const entry of view.entries) entry.row.hidden = !matches(entry);
  updateCounts();
  if (view.follow) scrollToEnd();
}

function updateCounts() {
  el.empty.hidden = view.entries.length > 0;
}

function setFollow(follow, scroll) {
  view.follow = follow;
  if (follow) { view.unseen = 0; if (scroll) scrollToEnd(); }
  updatePaused();
}

function updatePaused() {
  el.paused.textContent = view.unseen + (view.unseen === 1 ? " new event" : " new events") + " · follow";
  el.paused.classList.toggle("show", !view.follow && view.unseen > 0);
}

function scrollToEnd() {
  el.scroll.scrollTop = el.scroll.scrollHeight;
}

function clearView() {
  view.entries = [];
  view.total = {};
  view.interim = {};
  view.unseen = 0;
  view.base = null;
  view.turn = 0;
  view.turnOpen = false;
  el.log.replaceChildren();
  setFollow(true, false);
  updateCounts();
}

// ---------- sources ----------

function closeSource() {
  if (view.source) { view.source.close(); view.source = null; }
}

async function select(room, push) {
  const token = ++view.token;
  closeSource();
  clearView();
  view.room = room;
  const session = view.sessions.find((item) => item.room === room);
  view.recorded = Boolean(session && session.recorded);
  if (push) history.pushState({ room }, "", "/logs?room=" + encodeURIComponent(room));
  renderSessions();
  updateHeader();
  if (!room) return;
  if (view.recorded) {
    try {
      const response = await fetch("/api/replay", { cache: "no-store" });
      const data = await response.json();
      if (token !== view.token) return;
      for (const record of data.records || []) add(entryFromRecord(record, "recorded"));
      if (data.result) {
        const passed = Boolean(data.result.passed);
        add({
          t: (data.records || []).reduce((last, record) => Math.max(last, record.time || 0), 0),
          kind: "result", cat: "result", icon: passed ? "check-circle" : "x-circle",
          label: passed ? "Passed" : "Not passed", msg: data.result.scenario_id + " · " + (data.result.title || ""),
          sub: data.result.failure_reason || "", src: "report", interim: false, trace: false, data: data.result, pass: passed,
        });
      }
    } catch (error) {
      toast("The recorded run could not be loaded");
    }
    return;
  }
  for (const event of loadBrowserEvents(room)) add(entryFromBrowserEvent(event));
  const source = new EventSource("/api/events?room=" + encodeURIComponent(room) + "&since=0");
  view.source = source;
  source.onmessage = (message) => {
    if (token !== view.token) return;
    try { add(entryFromRecord(JSON.parse(message.data), "worker")); } catch (error) { /* skip a broken line */ }
  };
}

onBrowserEvent((event) => {
  if (event && event.room === view.room && !view.recorded) add(entryFromBrowserEvent(event));
});

// ---------- controls ----------

el.search.addEventListener("input", () => { view.query = el.search.value.trim().toLowerCase(); refilter(); });
el.search.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { el.search.value = ""; view.query = ""; refilter(); el.search.blur(); }
});
document.addEventListener("keydown", (event) => {
  const typing = event.target instanceof HTMLElement && (event.target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName));
  if (event.key === "/" && !typing) { event.preventDefault(); el.search.focus(); }
});
el.scroll.addEventListener("scroll", () => {
  const atBottom = el.scroll.scrollHeight - el.scroll.scrollTop - el.scroll.clientHeight < 28;
  if (atBottom !== view.follow) setFollow(atBottom, false);
});
el.paused.addEventListener("click", () => setFollow(true, true));
el.download.addEventListener("click", () => {
  if (!view.entries.length) { toast("No events to download"); return; }
  const body = view.entries.map((entry) => JSON.stringify(exportable(entry))).join("\n") + "\n";
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([body], { type: "application/x-ndjson" }));
  link.download = "interra-" + (view.room || "logs") + ".jsonl";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 4000);
});
el.log.addEventListener("click", (event) => {
  const row = event.target.closest(".lrow");
  if (!row || event.target.closest("pre")) return;
  const entry = view.entries.find((item) => item.row === row);
  if (!entry) return;
  if (!entry.pre) { entry.pre = jsonBlock(exportable(entry)); row.append(entry.pre); }
  row.classList.toggle("open");
});
window.addEventListener("popstate", () => select(new URLSearchParams(location.search).get("room") || "", false));
window.addEventListener("beforeunload", closeSource);

buildFilters();
updateCounts();
loadSessions().then(() => {
  const wanted = params.get("room");
  const first = view.sessions.find((session) => !session.recorded) || view.sessions[0];
  select(wanted || (first ? first.room : ""), false);
});
setInterval(loadSessions, 6000);
