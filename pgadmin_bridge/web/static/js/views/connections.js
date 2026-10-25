// Connections: live table of client sessions; rows are keyed so they animate in and out instead of flickering.
import { api } from "../api.js";
import { $, duration, element, html, mount, on, settle } from "../dom.js";
import { icon } from "../icons.js";
import { setState, store } from "../store.js";
import { confirm } from "../ui/dialog.js";
import { toast, toastError } from "../ui/toast.js";

let off = [];

function states(c) {
  return html`<div class="conn-states">
    ${c.backend ? html`<span class="badge green"><span class="dot green pulse"></span>Backend open</span>`
      : html`<span class="badge" title="Released while idle; reopened on the next query">Released</span>`}
    ${c.in_tx ? html`<span class="badge blue">In transaction</span>` : ""}
  </div>`;
}

function newRow(c) {
  return element(html`<table><tbody><tr class="enter" data-key="${c.key}">
    <td><div class="conn-client"><span>${c.application || "unknown client"}</span><small>${c.client}</small></div></td>
    <td><span class="conn-target">${c.target}</span></td>
    <td data-col="state"></td>
    <td class="num" data-col="idle"></td>
    <td class="num" data-col="age"></td>
    <td class="num" data-col="queries"></td>
    <td class="num"><button class="icon-btn danger" data-action="kill" aria-label="Disconnect" title="Disconnect">${icon("unplug", 15)}</button></td>
  </tr></tbody></table>`).querySelector("tr");
}

/** Only touches what changed, so badges keep animating smoothly between polls. */
function fill(row, c) {
  const state = String(states(c));
  if (row._state !== state) mount(row.querySelector("[data-col=state]"), (row._state = state));
  row.querySelector("[data-col=idle]").textContent = duration(c.idle_s);
  row.querySelector("[data-col=age]").textContent = duration(c.age_s);
  row.querySelector("[data-col=queries]").textContent = c.queries;
}

function empty() {
  return html`<div class="empty">
    <div class="empty-icon">${icon("plug", 24)}</div>
    <h3>No clients connected</h3>
    <p>When psql, DataGrip or an application connects, it shows up here with its backend and transaction state.</p>
    <a class="btn" href="#/overview">${icon("arrowRight", 15)} How to connect</a>
  </div>`;
}

function update(root) {
  const conns = store.state.connections;
  const box = $("#conn-box", root);
  $("#conn-count", root).textContent = conns.length ? `${conns.length} connected` : "";
  if (!conns.length) {
    if (!box.querySelector(".empty")) mount(box, empty());
    return;
  }
  let body = box.querySelector("tbody");
  if (!body) {
    mount(box, html`<div class="table-wrap"><table class="table">
      <thead><tr><th>Client</th><th>User / database</th><th>State</th><th class="num">Idle</th><th class="num">Connected</th><th class="num">Queries</th><th></th></tr></thead>
      <tbody></tbody></table></div>`);
    body = box.querySelector("tbody");
  }
  const keys = new Set(conns.map(c => c.key));
  for (const row of [...body.rows]) {
    if (!keys.has(row.dataset.key) && !row.classList.contains("leave")) {
      row.classList.add("leave");
      settle(row, 300).then(() => row.remove());
    }
  }
  for (const c of conns) {
    let row = body.querySelector(`tr[data-key="${CSS.escape(c.key)}"]`);
    if (!row) body.append((row = newRow(c)));
    fill(row, c);
  }
}

async function kill(row) {
  const c = store.state.connections.find(x => x.key === row.dataset.key);
  if (!c) return;
  const ok = await confirm({
    title: "Disconnect this client?",
    message: `${c.application || c.client} on ${c.target}. An open transaction is rolled back and its Query Tool is closed.`,
    action: "Disconnect",
  });
  if (!ok) return;
  try {
    setState(await api.kill(c.key));
    toast("Client disconnected", { detail: c.target });
  } catch (e) {
    toastError("Could not disconnect", e);
  }
}

export default {
  id: "connections",
  title: "Connections",
  icon: "plug",

  render(root) {
    mount(root, html`
      <header class="page-head">
        <div><h1>Connections <span class="badge green" id="conn-count" style="vertical-align:middle"></span></h1>
          <p>Each client is one pgAdmin Query Tool, which is one Postgres backend. Idle backends are released and reopened on the next query.</p></div>
      </header>
      <section class="card" id="conn-box"></section>`);
    off = [on(root, "click", "[data-action=kill]", (_, b) => kill(b.closest("tr")))];
    update(root);
  },

  update,

  destroy() {
    off.forEach(f => f());
    off = [];
  },
};
