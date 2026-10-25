// Instances: one card per pgAdmin with its account, TLS mode and discovered servers.
import { api, MASK } from "../api.js";
import { $, hostOf, html, mount, on } from "../dom.js";
import { icon } from "../icons.js";
import { saveConfig, store } from "../store.js";
import { confirm } from "../ui/dialog.js";
import { toast, toastError } from "../ui/toast.js";
import { editInstance } from "./instance-form.js";
import { busy, rescan } from "./shared.js";

let off = [];

function card(inst) {
  const servers = store.state.servers.filter(s => s.instance === inst.name);
  return html`<article class="card inst-card" data-name="${inst.name}">
    <div class="inst-top">
      <div class="inst-title"><h3>${inst.name}</h3><a href="${inst.url}" target="_blank" rel="noreferrer">${hostOf(inst.url)}</a></div>
      ${servers.length ? html`<span class="badge green"><span class="dot green"></span>Online</span>` : html`<span class="badge amber">No servers</span>`}
    </div>
    <div class="inst-meta">
      <div>${icon("mail", 14)}<span>${inst.email || "no account"}</span></div>
      <div>${icon(inst.verify_tls ? "shieldCheck" : "shieldOff", 14)}<span>${inst.verify_tls ? "TLS certificate verified" : "TLS verification off"}</span></div>
      <div>${icon("key", 14)}<span>${inst.password === MASK ? "Password saved" : "No password"}</span></div>
    </div>
    <div class="inst-servers">
      <h4>Servers · ${servers.length}</h4>
      <div class="chips">${servers.length ? servers.map(s => html`<span class="chip server" title="User = ${s.unique ? s.server : `${s.instance}/${s.server}`}">${s.server}</span>`)
        : html`<span class="faint" style="font-size:var(--text-sm)">Nothing discovered. Test the login or rescan.</span>`}</div>
    </div>
    <div class="inst-actions">
      <button class="btn btn-ghost btn-sm" data-action="test">${icon("zap", 14)} Test</button>
      <button class="btn btn-ghost btn-sm" data-action="edit">${icon("pencil", 14)} Edit</button>
      <span class="spacer"></span>
      <button class="icon-btn danger" data-action="remove" aria-label="Remove ${inst.name}" title="Remove">${icon("trash", 15)}</button>
    </div>
  </article>`;
}

function render(root) {
  const instances = store.state.config.instances;
  mount($("#inst-list", root), instances.length ? html`<div class="inst-grid">
      ${instances.map(card)}
      <button class="inst-add" data-action="add"><span class="empty-icon">${icon("plus", 22)}</span>Add another pgAdmin</button>
    </div>` : html`<div class="card"><div class="empty">
      <div class="empty-icon">${icon("layers", 24)}</div>
      <h3>Connect your first pgAdmin</h3>
      <p>Add the pgAdmin web address and the account you sign in with. The bridge discovers every server in its tree.</p>
      <button class="btn btn-primary" data-action="add">${icon("plus", 15)} Add pgAdmin instance</button>
    </div></div>`);
}

async function test(name, button) {
  const inst = store.state.config.instances.find(i => i.name === name);
  try {
    const { servers } = await busy(button, () => api.test({ ...inst, was: name }));
    toast(`${name}: login OK`, { detail: servers.length ? servers.join(", ") : "The server tree is empty for this account." });
  } catch (e) {
    toastError(`${name}: login failed`, e);
  }
}

async function remove(name) {
  const ok = await confirm({
    title: `Remove ${name}?`,
    message: "Clients using its servers will not be able to connect anymore. Open sessions keep running until they disconnect.",
    action: "Remove instance",
  });
  if (!ok) return;
  try {
    await saveConfig({ instances: store.state.config.instances.filter(i => i.name !== name) });
    toast(`${name} removed`);
  } catch (e) {
    toastError("Could not remove", e);
  }
}

export default {
  id: "instances",
  title: "Instances",
  icon: "layers",

  render(root) {
    mount(root, html`
      <header class="page-head">
        <div><h1>pgAdmin instances</h1><p>The pgAdmin servers the bridge signs in to. Clients connect with User = a server name from their trees.</p></div>
        <div class="page-actions">
          <button class="btn" data-action="rescan">${icon("refresh", 15)} Rescan</button>
          <button class="btn btn-primary" data-action="add">${icon("plus", 15)} Add instance</button>
        </div>
      </header>
      <div id="inst-list"></div>`);
    const nameOf = el => el.closest("[data-name]").dataset.name;
    off = [
      on(root, "click", "[data-action=add]", () => editInstance()),
      on(root, "click", "[data-action=rescan]", (_, b) => rescan(b)),
      on(root, "click", "[data-action=edit]", (_, b) => editInstance(store.state.config.instances.find(i => i.name === nameOf(b)))),
      on(root, "click", "[data-action=test]", (_, b) => test(nameOf(b), b)),
      on(root, "click", "[data-action=remove]", (_, b) => remove(nameOf(b))),
    ];
    this.sig = "";
    this.update(root);
  },

  update(root) {
    const sig = JSON.stringify([store.state.config.instances, store.state.servers]);
    if (sig === this.sig || root.querySelector(".loading")) return;
    this.sig = sig;
    render(root);
  },

  destroy() {
    off.forEach(f => f());
    off = [];
  },
};
