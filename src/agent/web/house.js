// Home page: the session's devices as tiles, drawn from the worker trace.
//
// Device states, held and dropped commands and confirmations all come from
// the records the worker writes; this page never changes a device itself.
// "Add a new device" sends a description the agent has never seen over
// LiveKit RPC, and the worker turns it into new tools during the call.

import { $, errorText, h, restartAnimation, toast } from "./shell.js";

const ADD_DEVICE_RPC = "interra.add_device";
const ICONS = {
  AirConditioner: "d-ac", Light: "d-light", Television: "d-tv", RobotCleaner: "d-vacuum",
  Washer: "d-washer", Oven: "d-oven", SmartLock: "d-lock", AirPurifier: "d-purifier",
};
const NAMES = {
  switch: "Power", coolingSetpoint: "Target", temperature: "Room", airConditionerMode: "Mode",
  level: "Brightness", volume: "Volume", robotCleanerMovement: "Status", machineState: "Cycle",
  ovenState: "Oven", ovenSetpoint: "Set to", lock: "Lock", airPurifierFanMode: "Fan", fineDustLevel: "PM2.5",
};
const UNITS = { coolingSetpoint: "°C", temperature: "°C", level: "%", volume: "%", ovenSetpoint: "°C", fineDustLevel: "µg/m³" };
const ARGUMENT_UNITS = { setpoint: "°C", level: "%", volume: "%" };
const WORDS = {
  on: "On", off: "Off", cool: "Cool", heat: "Heat", dry: "Dry", wind: "Fan only", auto: "Auto",
  cleaning: "Cleaning", homing: "Going home", idle: "Idle", charging: "Charging", pause: "Paused",
  run: "Running", stop: "Stopped", ready: "Ready", running: "Heating", locked: "Locked", unlocked: "Unlocked",
  sleep: "Sleep", low: "Low", medium: "Medium", high: "High",
};
// The attribute that says what a device is doing, in order of preference.
const PRIMARY = ["switch", "lock", "robotCleanerMovement", "machineState", "ovenState"];
const ACTIVE = { switch: "on", lock: "unlocked", robotCleanerMovement: "cleaning", machineState: "run", ovenState: "running" };
const ACTIONS = {
  turn_on: "On", turn_off: "Off", lock_door: "Lock", unlock_door: "Unlock",
  start_oven: "Start", stop_oven: "Stop", undo_last_change: "Undo",
};

const el = {
  grid: $("devices"), add: $("add-device"), addLabel: $("add-device-label"),
  walk: $("walkthrough"), walkLabel: $("walkthrough-label"), status: $("status"),
  meta: $("home-meta"), json: $("device-json"), jsonText: $("device-json-text"),
};
const home = { initial: [], room: "", devices: new Map(), tiles: new Map(), unseen: null, agent: null, adding: false, playing: false };
// How long each walkthrough record stays on screen before the next one.
const WALK_PAUSE = {
  transcript: 1700, tool_call_pending: 1000, tool_call_rechecked: 1400, tool_call_dropped: 1600, confirmation_requested: 1900,
  confirmation_given: 900, home_device_added: 2400, home_devices: 350, tool_call: 900,
};

function deviceIcon(category) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "ic device-icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#" + (ICONS[category] || "i-cpu"));
  svg.append(use);
  return svg;
}

function words(value) {
  return typeof value === "string" ? WORDS[value] || value : String(value);
}

function attributeText(name, value) {
  const unit = UNITS[name];
  return words(value) + (unit ? (unit === "%" ? "%" : " " + unit) : "");
}

function primaryOf(device) {
  return PRIMARY.find((name) => name in device.state) || Object.keys(device.state)[0];
}

function isActive(device) {
  return Object.entries(ACTIVE).some(([name, value]) => device.state[name] === value);
}

function findByLabel(label) {
  const key = String(label || "").trim().toLowerCase();
  for (const device of home.devices.values()) {
    if (device.label.toLowerCase() === key || device.id === key) return device;
  }
  return null;
}

// What a call asked for, in a few words: "24 °C", "Off", "High".
function asked(call) {
  const name = (call && call.function) || "";
  if (ACTIONS[name]) return ACTIONS[name];
  const args = Object.entries((call && call.args) || {}).filter(([key]) => key !== "device" && key !== "reverses");
  if (!args.length) return name.replace(/_/g, " ");
  return args.map(([key, value]) => words(value) + (ARGUMENT_UNITS[key] ? (ARGUMENT_UNITS[key] === "%" ? "%" : " " + ARGUMENT_UNITS[key]) : "")).join(", ");
}

function buildTile(device) {
  const tile = h("article", "device");
  tile.dataset.id = device.id;
  const top = h("div", "device-top");
  top.append(deviceIcon(device.category), h("span", "device-room", device.room));
  const note = h("p", "device-note");
  note.hidden = true;
  tile.append(top, h("h3", "device-name", device.label), h("p", "device-primary"), h("dl", "device-attrs"), note);
  return tile;
}

function fillTile(tile, device, changed) {
  const primary = primaryOf(device);
  tile.dataset.active = String(isActive(device));
  const line = tile.querySelector(".device-primary");
  line.textContent = attributeText(primary, device.state[primary]);
  line.classList.toggle("changed", changed.has(primary));
  const list = tile.querySelector(".device-attrs");
  list.replaceChildren();
  for (const [name, value] of Object.entries(device.state)) {
    if (name === primary) continue;
    const row = h("div", changed.has(name) ? "changed" : "");
    row.append(h("dt", "", NAMES[name] || name), h("dd", "", attributeText(name, value)));
    list.append(row);
  }
  if (changed.size) restartAnimation(tile, "flash");
}

function render(devices) {
  const seen = new Set();
  for (const device of devices) {
    seen.add(device.id);
    const before = home.devices.get(device.id);
    const changed = new Set(before ? Object.keys(device.state).filter((name) => before.state[name] !== device.state[name]) : []);
    home.devices.set(device.id, device);
    let tile = home.tiles.get(device.id);
    if (!tile) {
      tile = buildTile(device);
      home.tiles.set(device.id, tile);
      el.grid.append(tile);
    }
    fillTile(tile, device, changed);
  }
  for (const [id, tile] of home.tiles) {
    if (!seen.has(id)) { tile.remove(); home.tiles.delete(id); home.devices.delete(id); }
  }
  el.meta.textContent = devices.length + " devices · SmartThings capability model, simulated";
  updateAddButton();
}

function droppedWhy(reason) {
  const text = String(reason || "");
  if (text.includes("newer value")) return "a newer value replaced it";
  if (text.includes("changed or cancelled")) return "you changed it";
  if (text.startsWith("re-check") || text.includes("no re-check")) return "could not be re-checked";
  return "you said more";
}

function setNote(device, kind, text, seconds = 0) {
  const tile = device && home.tiles.get(device.id);
  if (!tile) return;
  const note = tile.querySelector(".device-note");
  clearTimeout(Number(note.dataset.timer || 0));
  note.hidden = false;
  note.dataset.kind = kind;
  note.textContent = text;
  tile.dataset.note = kind;
  restartAnimation(note, "show");
  if (seconds) {
    note.dataset.timer = String(setTimeout(() => clearNote(device), seconds * 1000));
  }
}

function clearNote(device) {
  const tile = device && home.tiles.get(device.id);
  if (!tile) return;
  const note = tile.querySelector(".device-note");
  note.hidden = true;
  delete tile.dataset.note;
}

function reset(room) {
  home.room = room;
  el.grid.replaceChildren();
  home.tiles.clear();
  home.devices.clear();
  render(home.initial);
}

function onRecord(record) {
  if (!record || typeof record !== "object") return;
  if (record.room && record.room !== home.room && record.kind === "home_devices") reset(record.room);
  const device = findByLabel(record.call && record.call.args && record.call.args.device);
  switch (record.kind) {
    case "home_devices":
      render(record.devices || []);
      break;
    case "home_device_added": {
      const label = record.device && record.device.label;
      const added = record.new_tools && record.new_tools.length ? " New tools: " + record.new_tools.join(", ") + "." : "";
      toast(label + " added." + added);
      const tile = record.device && home.tiles.get(record.device.id);
      if (tile) {
        restartAnimation(tile, "arrive");
        tile.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      setNote(findByLabel(label), "new", "New device · " + (record.new_tools || []).length + " new tool" + ((record.new_tools || []).length === 1 ? "" : "s"), 8);
      break;
    }
    case "home_device_rejected":
      toast("The agent rejected the device: " + record.error);
      break;
    case "tool_call_pending":
      setNote(device, "holding", "Holding " + asked(record.call) + " until you are quiet");
      break;
    case "tool_call_dropped":
      setNote(device, "dropped", asked(record.call) + " dropped · " + droppedWhy(record.reason), 6);
      break;
    case "tool_call_rechecked":
      setNote(device, "holding", "You said more · " + (record.run ? "still stands" : "no longer stands"));
      break;
    case "tool_call":
      if (record.call && record.call.function === "undo_last_change") setNote(device, "done", "Undone", 4);
      else if (device) setNote(device, "done", asked(record.call) + " · done", 3);
      break;
    case "home_command_skipped":
      setNote(device, "same", "Already " + asked(record.call).toLowerCase() + " · nothing sent", 4);
      break;
    case "confirmation_requested":
      setNote(device, "confirm", "Waiting for your yes: " + asked(record.call).toLowerCase());
      break;
    case "confirmation_declined":
    case "confirmation_expired":
      for (const known of home.devices.values()) {
        const tile = home.tiles.get(known.id);
        if (tile && tile.dataset.note === "confirm") setNote(known, "same", record.kind === "confirmation_declined" ? "Cancelled · nothing changed" : "Confirmation expired", 4);
      }
      break;
    default:
      break;
  }
}

function updateAddButton() {
  const present = Boolean(home.agent);
  const exists = home.unseen && home.devices.has(home.unseen.id);
  el.add.disabled = !present || exists || home.adding || !home.unseen;
  el.add.title = !present ? "Start a session first" : exists ? "Already added in this session" : "Send a device description the agent has never seen";
  el.addLabel.textContent = exists ? "Device added" : home.adding ? "Adding…" : "Add a new device";
}

function updateWalkButton() {
  el.walk.disabled = Boolean(home.agent) || home.playing;
  el.walk.title = home.agent ? "End the live session to play the walkthrough" : "Plays the scripted demo through the real home controller, without a LiveKit room";
  el.walkLabel.textContent = home.playing ? "Playing…" : "Play walkthrough";
}

// The scripted demo: the server runs it through the real controller and returns
// the records; this page plays them through the same view as a live session.
async function playWalkthrough() {
  if (home.agent || home.playing) return;
  home.playing = true;
  updateWalkButton();
  el.status.textContent = "Scripted walkthrough: your lines are scripted; every device change comes from the real controller.";
  try {
    const response = await fetch("/api/home/walkthrough", { cache: "no-store" });
    if (!response.ok) throw new Error(await response.text());
    const { events } = await response.json();
    for (const record of events) {
      if (home.agent) break;
      window.dispatchEvent(new CustomEvent("interra:inject", { detail: { ...record, time: Date.now() / 1000 } }));
      await new Promise((done) => setTimeout(done, WALK_PAUSE[record.kind] ?? 600));
    }
  } catch (error) {
    toast("The walkthrough could not run: " + errorText(error));
  } finally {
    home.playing = false;
    updateWalkButton();
  }
}

async function addDevice() {
  if (!home.agent || !home.unseen) return;
  home.adding = true;
  updateAddButton();
  try {
    const reply = await home.agent.room.localParticipant.performRpc({
      destinationIdentity: home.agent.identity,
      method: ADD_DEVICE_RPC,
      payload: JSON.stringify(home.unseen),
      responseTimeout: 10000,
    });
    const result = JSON.parse(reply);
    if (result.status !== "added") toast("Not added: " + result.message);
  } catch (error) {
    toast("Could not reach the agent: " + errorText(error));
  } finally {
    home.adding = false;
    updateAddButton();
  }
}

window.addEventListener("interra:record", (event) => onRecord(event.detail));
window.addEventListener("interra:agent", (event) => {
  home.agent = event.detail && event.detail.room ? event.detail : null;
  updateAddButton();
  updateWalkButton();
});
el.add.addEventListener("click", () => addDevice());
el.walk.addEventListener("click", () => playWalkthrough());

fetch("/api/home", { cache: "no-store" })
  .then((response) => (response.ok ? response.json() : Promise.reject(new Error(response.statusText))))
  .then((payload) => {
    home.initial = payload.devices || [];
    home.unseen = payload.unseen_device || null;
    el.jsonText.textContent = JSON.stringify(home.unseen, null, 2);
    if (!home.tiles.size) render(home.initial);
  })
  .catch((error) => { el.meta.textContent = "The home could not be loaded: " + errorText(error); });
