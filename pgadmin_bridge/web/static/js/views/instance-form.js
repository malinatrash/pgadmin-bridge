// Drawer to add or edit a pgAdmin instance, with a live "Test login" before saving.
import { api, MASK } from "../api.js";
import { $, html, mount } from "../dom.js";
import { icon } from "../icons.js";
import { saveConfig, store } from "../store.js";
import { installReveal, passwordField } from "../ui/controls.js";
import { openDrawer } from "../ui/drawer.js";
import { toast, toastError } from "../ui/toast.js";
import { busy } from "./shared.js";

const BLANK = { name: "", url: "", email: "", password: "", verify_tls: true };

function read(form) {
  const data = new FormData(form);
  return {
    name: String(data.get("name")).trim(),
    url: String(data.get("url")).trim().replace(/\/+$/, ""),
    email: String(data.get("email")).trim(),
    password: String(data.get("password")),
    verify_tls: data.get("verify_tls") === "on",
  };
}

function validate(inst, others) {
  if (!inst.name) return ["name", "Give the instance a short name, e.g. prod."];
  if (inst.name.includes("/")) return ["name", "Names cannot contain '/'."];
  if (others.some(o => o.name === inst.name)) return ["name", "Another instance already uses this name."];
  if (!/^https?:\/\/.+/.test(inst.url)) return ["url", "Use the full address, starting with https://"];
  if (!inst.email) return ["email", "The email you sign in to pgAdmin with."];
  return null;
}

function field(name, label, control, hint = "") {
  return html`<label class="field" data-field="${name}">
    <span class="field-label">${label}</span>${control}
    <span class="field-hint">${hint}</span>
  </label>`;
}

function showError(form, [name, message]) {
  const box = $(`[data-field=${name}]`, form);
  box.querySelector(".input-wrap").classList.add("invalid");
  const hint = box.querySelector(".field-hint");
  hint.dataset.hint ??= hint.textContent;
  hint.textContent = message;
  hint.className = "field-error";
  box.querySelector("input").focus();
}

function clearErrors(form) {
  for (const w of form.querySelectorAll(".invalid")) w.classList.remove("invalid");
  for (const h of form.querySelectorAll(".field-error")) {
    h.className = "field-hint";
    h.textContent = h.dataset.hint ?? "";
  }
}

export function editInstance(existing = null) {
  const inst = existing ?? BLANK;
  const others = store.state.config.instances.filter(i => i.name !== existing?.name);
  const drawer = openDrawer({
    title: existing ? `Edit ${existing.name}` : "Add pgAdmin instance",
    subtitle: "The bridge signs in with this account, so clients get exactly its access.",
    body: html`<form class="form-grid" id="inst-form" novalidate autocomplete="off">
      ${field("name", "Name", html`<div class="input-wrap">${icon("layers", 15)}<input class="input" name="name" value="${inst.name}" placeholder="prod" spellcheck="false"></div>`,
        "Short label. Clients use instance/server when two instances share a server name.")}
      ${field("url", "pgAdmin URL", html`<div class="input-wrap">${icon("link", 15)}<input class="input mono" name="url" type="url" value="${inst.url}" placeholder="https://pgadmin.company.com" spellcheck="false"></div>`,
        "The address you open in the browser, without /browser.")}
      ${field("email", "Email", html`<div class="input-wrap">${icon("mail", 15)}<input class="input" name="email" type="email" value="${inst.email}" placeholder="you@company.com"></div>`)}
      ${field("password", "Password", passwordField({ name: "password", value: inst.password, placeholder: "pgAdmin password" }),
        existing?.password === MASK ? "Saved. Leave the dots to keep it." : "Stored in the data volume with mode 0600.")}
      <div class="setting" style="padding:0;grid-template-columns:1fr auto">
        <div class="setting-text"><h3>Verify TLS certificate</h3><p>Turn off only for self-signed internal pgAdmin hosts.</p></div>
        <label class="switch"><input type="checkbox" name="verify_tls" ${inst.verify_tls ? "checked" : ""}><span class="switch-track"></span></label>
      </div>
      <div id="test-result"></div>
    </form>`,
    footer: html`
      <button class="btn" type="button" data-action="test">${icon("zap", 15)} Test login</button>
      <span class="spacer"></span>
      <button class="btn btn-ghost" type="button" data-close>Cancel</button>
      <button class="btn btn-primary" type="submit" form="inst-form">${existing ? "Save changes" : "Add instance"}</button>`,
  });

  const form = $("#inst-form", drawer.el);
  const result = $("#test-result", drawer.el);
  installReveal(form);
  form.addEventListener("input", () => clearErrors(form));
  $("[data-close]", drawer.el.querySelector(".drawer-foot")).addEventListener("click", drawer.close);

  $("[data-action=test]", drawer.el).addEventListener("click", async event => {
    const data = read(form);
    const error = validate(data, others);
    if (error) return showError(form, error);
    mount(result, "");
    try {
      const { servers } = await busy(event.currentTarget, () => api.test({ ...data, was: existing?.name }));
      mount(result, html`<div class="alert green">${icon("checkCircle", 18)}<div>
        <strong>Signed in to pgAdmin</strong>${servers.length ? `${servers.length} server${servers.length === 1 ? "" : "s"} in the tree:` : "The server tree is empty for this account."}
        <div class="chips stagger">${servers.map(s => html`<span class="chip server">${s}</span>`)}</div></div></div>`);
    } catch (e) {
      mount(result, html`<div class="alert red">${icon("xCircle", 18)}<div><strong>Login failed</strong>${e.message}</div></div>`);
    }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    const data = read(form);
    const error = validate(data, others);
    if (error) return showError(form, error);
    const instances = store.state.config.instances.map(i => (i.name === existing?.name ? { ...data, was: existing.name } : i));
    if (!existing) instances.push(data);
    try {
      await busy($("[type=submit]", drawer.el), () => saveConfig({ instances }));
      toast(existing ? "Instance updated" : "Instance added", { detail: `${data.name} · ${store.state.servers.filter(s => s.instance === data.name).length} servers discovered` });
      drawer.close();
    } catch (e) {
      toastError("Could not save", e);
    }
  });
}
