// Settings: proxy password and session guardrails, with a floating save bar while there are unsaved changes.
import { MASK } from "../api.js";
import { $, $$, element, html, mount, on, settle } from "../dom.js";
import { icon } from "../icons.js";
import { saveConfig, store } from "../store.js";
import { copyButton, installReveal, passwordField } from "../ui/controls.js";
import { toast, toastError } from "../ui/toast.js";
import { busy } from "./shared.js";

const TIMEOUTS = [[0, "Off"], [30, "30s"], [120, "2 min"], [600, "10 min"]];
const IDLE = [[0, "Never"], [300, "5 min"], [600, "10 min"], [1800, "30 min"]];
const LANES = [[1, "1"], [2, "2"], [4, "4"], [8, "8"]];
const CACHE = [[0, "Off"], [30, "30s"], [60, "1 min"], [300, "5 min"]];
let off = [];
let bar = null;

function presets(name, list) {
  return html`<div class="presets" data-presets="${name}">${list.map(([v, label]) =>
    html`<button type="button" class="chip" data-value="${v}">${label}</button>`)}</div>`;
}

function setting(title, text, control, inline = false) {
  return html`<div class="setting">
    <div class="setting-text"><h3>${title}</h3><p>${text}</p></div>
    <div class="setting-control ${inline ? "inline" : ""}">${control}</div>
  </div>`;
}

function values(root) {
  const form = $("#settings-form", root);
  return {
    password: form.password.value,
    read_only: form.read_only.checked,
    timeout: Math.max(0, parseInt(form.timeout.value, 10) || 0),
    idle: Math.max(0, parseInt(form.idle.value, 10) || 0),
    lanes: Math.min(16, Math.max(1, parseInt(form.lanes.value, 10) || 1)),
    cache: Math.max(0, parseInt(form.cache.value, 10) || 0),
  };
}

function saved() {
  const c = store.state.config;
  return { password: c.password, read_only: c.read_only, timeout: c.timeout, idle: c.idle, lanes: c.lanes, cache: c.cache };
}

function fill(root) {
  const form = $("#settings-form", root);
  const c = saved();
  form.password.value = c.password;
  form.read_only.checked = c.read_only;
  form.timeout.value = c.timeout;
  form.idle.value = c.idle;
  form.lanes.value = c.lanes;
  form.cache.value = c.cache;
  sync(root);
}

const dirty = root => JSON.stringify(values(root)) !== JSON.stringify(saved());

/** Highlights matching presets and shows or hides the save bar. */
function sync(root) {
  const v = values(root);
  for (const group of $$("[data-presets]", root)) {
    for (const chip of group.children) chip.setAttribute("aria-pressed", String(+chip.dataset.value === v[group.dataset.presets]));
  }
  $("#ro-badge", root).hidden = !v.read_only;
  bar.classList.toggle("show", dirty(root));
}

function generate() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
  return Array.from(crypto.getRandomValues(new Uint32Array(24)), n => alphabet[n % alphabet.length]).join("");
}

async function save(root, button) {
  try {
    await busy(button, () => saveConfig(values(root)));
    fill(root);
    toast("Settings saved", { detail: "Applied to new backends right away, no restart needed." });
  } catch (e) {
    toastError("Could not save settings", e);
  }
}

export default {
  id: "settings",
  title: "Settings",
  icon: "sliders",

  render(root) {
    mount(root, html`
      <header class="page-head">
        <div><h1>Settings</h1><p>Applied live to every new backend, without restarting the bridge.</p></div>
      </header>
      <form class="settings" id="settings-form" autocomplete="off" onsubmit="return false">
        <section class="card">
          <div class="card-head"><div><h2>Client access</h2><p>What database clients send to connect</p></div></div>
          ${setting("Proxy password", "Clients send this as the password. It is not your pgAdmin password, so you can share the bridge without sharing your account.",
            html`${passwordField({ name: "password", placeholder: "not set", extra: html`
              <button class="icon-btn" type="button" data-action="generate" aria-label="Generate a strong password" title="Generate">${icon("sparkles", 15)}</button>` })}
              <span class="field-hint" id="pw-hint"></span>`)}
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Guardrails</h2><p>Protect production from forgotten and runaway queries</p></div>
            <span class="badge amber" id="ro-badge" hidden>${icon("shield", 12)} Read-only</span></div>
          ${setting("Read-only sessions", "Every backend starts with default_transaction_read_only = on. Writes fail unless a client explicitly overrides it.",
            html`<label class="switch"><input type="checkbox" name="read_only"><span class="switch-track"></span></label>`, true)}
          ${setting("Statement timeout", "Cancels statements running longer than this (statement_timeout). 2 minutes is a sensible production default.",
            html`<div class="input-wrap">${icon("timer", 15)}<input class="input" type="number" min="0" name="timeout"><span class="suffix">sec</span></div>
              ${presets("timeout", TIMEOUTS)}`)}
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Idle backends</h2><p>Forgotten DataGrip tabs should not hold database connections</p></div></div>
          ${setting("Release idle backend after", "Closes the Query Tool of a client that is silent and not in a transaction. The next query reopens it and replays the session's SET commands. Also used as idle_in_transaction_session_timeout.",
            html`<div class="input-wrap">${icon("clock", 15)}<input class="input" type="number" min="0" name="idle"><span class="suffix">sec</span></div>
              ${presets("idle", IDLE)}`)}
        </section>
        <section class="card">
          <div class="card-head"><div><h2>Performance</h2><p>Fewer round trips to pgAdmin, more of them in parallel</p></div></div>
          ${setting("Parallel pgAdmin sessions", "Logins the bridge keeps per pgAdmin instance. pgAdmin serializes requests within one login, so this is how many statements can run at the same time. Each one holds its own pgAdmin connections to a server.",
            html`<div class="input-wrap">${icon("layers", 15)}<input class="input" type="number" min="1" max="16" name="lanes"></div>
              ${presets("lanes", LANES)}`)}
          ${setting("Catalog cache", "Keeps the results of read-only pg_catalog and information_schema queries for this long, shared by all clients. Cuts the introspection storm when an IDE connects. Off keeps every query live.",
            html`<div class="input-wrap">${icon("zap", 15)}<input class="input" type="number" min="0" name="cache"><span class="suffix">sec</span></div>
              ${presets("cache", CACHE)}`)}
        </section>
      </form>`);

    bar = element(html`<div class="savebar" role="region" aria-label="Unsaved changes">
      <span class="savebar-text"><span class="dot amber"></span>Unsaved changes</span>
      <button class="btn btn-ghost btn-sm" type="button" data-action="discard">Discard</button>
      <button class="btn btn-primary btn-sm" type="button" data-action="save">Save changes</button>
    </div>`);
    document.body.append(bar);

    const form = $("#settings-form", root);
    installReveal(form);
    off = [
      on(form, "input", "input", () => sync(root)),
      on(form, "click", "[data-presets] .chip", (_, chip) => {
        form[chip.parentElement.dataset.presets].value = chip.dataset.value;
        sync(root);
      }),
      on(form, "click", "[data-action=generate]", () => {
        const pw = generate();
        form.password.value = pw;
        form.password.type = "text";
        mount($("#pw-hint", root), html`<span style="display:inline-flex;align-items:center;gap:6px">New password generated ${copyButton(pw, "Copy password", "icon-btn")}</span>`);
        sync(root);
      }),
      on(bar, "click", "[data-action=discard]", () => { fill(root); mount($("#pw-hint", root), ""); }),
      on(bar, "click", "[data-action=save]", (_, b) => save(root, b)),
    ];
    this.keys = event => {
      if ((event.metaKey || event.ctrlKey) && event.key === "s") {
        event.preventDefault();
        if (dirty(root)) save(root, $("[data-action=save]", bar));
      }
    };
    document.addEventListener("keydown", this.keys);
    fill(root);
    mount($("#pw-hint", root), store.state.config.password === MASK ? "Saved. Leave the dots to keep it." : "Not set: clients cannot connect yet.");
  },

  update(root) {
    if (!dirty(root)) fill(root);
  },

  destroy() {
    off.forEach(f => f());
    off = [];
    document.removeEventListener("keydown", this.keys);
    const old = bar;
    bar = null;
    old.classList.remove("show");
    settle(old, 400).then(() => old.remove());
  },
};
