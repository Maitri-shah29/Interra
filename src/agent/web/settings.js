// Settings: sign in, edit the saved overrides, save and wait for the restart.
// Secret values arrive masked; a secret is only sent when a new one is typed.

import { $, h, svgIcon, toast, errorText } from "./shell.js";

let rows = [];
const edits = new Map();   // name -> new value
const resets = new Set();  // names whose dashboard override is removed

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const error = new Error((await response.text()) || response.statusText);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body || {}) });

function show(id) {
  for (const name of ["off", "login", "editor"]) $(name).hidden = name !== id;
}

function notice(message, kind = "info") {
  const node = $("notice");
  node.textContent = message;
  node.dataset.kind = kind;
  node.hidden = !message;
}

function sourceBadge(row) {
  const label = row.pending ? "restart pending" : { dashboard: "dashboard", deployment: "deployment", unset: "code default" }[row.source];
  const badge = h("span", "badge", label);
  badge.dataset.source = row.source;
  if (row.pending) badge.dataset.pending = "true";
  return badge;
}

function renderRow(row) {
  const tr = h("tr");
  tr.dataset.name = row.name;

  const nameCell = h("td");
  nameCell.append(h("span", "name mono", row.name));
  if (row.description) nameCell.append(h("span", "desc", row.description));

  const current = h("td");
  const wrap = h("div", "current");
  wrap.append(sourceBadge(row));
  if (row.value) wrap.append(h("span", "mono", row.value));
  current.append(wrap);

  const input = h("input");
  input.type = row.secret ? "password" : "text";
  input.autocomplete = row.secret ? "new-password" : "off";
  input.spellcheck = false;
  input.setAttribute("aria-label", "New value for " + row.name);
  if (row.secret) input.placeholder = row.value ? "unchanged" : "not set";
  else if (row.source === "unset") input.placeholder = "code default";
  else input.value = edits.has(row.name) ? edits.get(row.name) : row.value;
  if (row.secret && edits.has(row.name)) input.value = edits.get(row.name);
  input.addEventListener("input", () => {
    const changed = row.secret ? input.value !== "" : input.value !== row.value;
    if (changed) edits.set(row.name, input.value);
    else edits.delete(row.name);
    resets.delete(row.name);
    update();
  });
  const editCell = h("td");
  editCell.append(input);

  const resetCell = h("td");
  if (row.source === "dashboard") {
    const reset = h("button", "icon-btn sm");
    reset.type = "button";
    reset.title = "Remove the dashboard value";
    reset.setAttribute("aria-label", "Remove the dashboard value of " + row.name);
    reset.append(svgIcon("trash"));
    reset.addEventListener("click", () => {
      if (resets.has(row.name)) resets.delete(row.name);
      else { resets.add(row.name); edits.delete(row.name); }
      render();
    });
    resetCell.append(reset);
  }

  tr.append(nameCell, current, editCell, resetCell);
  tr.dataset.changed = String(edits.has(row.name));
  tr.dataset.reset = String(resets.has(row.name));
  return tr;
}

function render() {
  $("rows").replaceChildren(...rows.map(renderRow));
  update();
}

function update() {
  for (const tr of $("rows").children) {
    tr.dataset.changed = String(edits.has(tr.dataset.name));
    tr.dataset.reset = String(resets.has(tr.dataset.name));
  }
  const count = edits.size + resets.size;
  $("change-count").textContent = count ? count + (count === 1 ? " change" : " changes") : "No changes";
  $("save").disabled = count === 0;
}

async function load() {
  const data = await request("/api/admin/settings");
  rows = data.settings;
  edits.clear();
  resets.clear();
  $("file-meta").textContent = "Saved to " + data.file;
  if (data.file_error) notice("The saved file could not be read and will be replaced: " + data.file_error, "error");
  else if (!data.restart_available) notice("This page was not started by agent.serve, so a save is written but nothing restarts. Restart the processes yourself.");
  else notice("");
  render();
}

async function waitForRestart() {
  // The page goes away and comes back; wait for it to answer again.
  await new Promise((resolve) => setTimeout(resolve, 1500));
  for (let attempt = 0; attempt < 90; attempt += 1) {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (response.ok) return true;
    } catch (error) { /* still restarting */ }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return false;
}

async function save() {
  const button = $("save");
  button.disabled = true;
  const body = { set: Object.fromEntries(edits), unset: [...resets] };
  try {
    const result = await post("/api/admin/settings", body);
    if (!result.restarting) {
      toast("Saved");
      await load();
      return;
    }
    notice("Saved. Restarting the page and both workers…");
    const back = await waitForRestart();
    await load();
    notice(back ? "Restarted with the saved settings." : "Saved, but the page did not come back in time. Check the deployment logs.", back ? "info" : "error");
  } catch (error) {
    if (error.status === 401) { show("login"); return; }
    notice(errorText(error), "error");
    update();
  }
}

function addVariable() {
  const name = $("new-name").value.trim().toUpperCase();
  const value = $("new-value").value;
  if (!/^[A-Z][A-Z0-9_]{0,63}$/.test(name)) { toast("Use A-Z, 0-9 and _, starting with a letter"); return; }
  if (!rows.some((row) => row.name === name)) {
    const secret = /KEY|SECRET|TOKEN|PASSWORD/.test(name);
    rows.push({ name, description: "", secret, value: "", source: "unset", pending: false });
  }
  edits.set(name, value);
  resets.delete(name);
  $("new-name").value = "";
  $("new-value").value = "";
  render();
}

async function start() {
  let status;
  try {
    status = await request("/api/admin/status");
  } catch (error) {
    show("off");
    return;
  }
  if (!status.enabled) { show("off"); return; }
  if (!status.signed_in) { show("login"); $("password").focus(); return; }
  show("editor");
  try { await load(); } catch (error) {
    if (error.status === 401) show("login");
    else notice(errorText(error), "error");
  }
}

$("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  const button = event.submitter || $("login").querySelector("button");
  button.disabled = true;
  try {
    await post("/api/admin/login", { password: $("password").value });
    $("password").value = "";
    await start();
  } catch (error) {
    $("login-error").textContent = errorText(error);
  } finally {
    button.disabled = false;
  }
});

$("logout").addEventListener("click", async () => {
  try { await post("/api/admin/logout"); } catch (error) { /* signed out either way */ }
  show("login");
});
$("save").addEventListener("click", save);
$("add").addEventListener("click", addVariable);
$("new-value").addEventListener("keydown", (event) => { if (event.key === "Enter") addVariable(); });

start();
