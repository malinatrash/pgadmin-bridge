// Entry point: hash router, sidebar (nav, listener status, theme) and polling of the bridge's state.
import { $, $$, html, mount } from "./dom.js";
import { icon } from "./icons.js";
import { endpoint, refresh, refreshLog, store, subscribe } from "./store.js";
import { installCopy } from "./ui/controls.js";
import connections from "./views/connections.js";
import instances from "./views/instances.js";
import logs from "./views/logs.js";
import overview from "./views/overview.js";
import settings from "./views/settings.js";

const VIEWS = [overview, instances, connections, settings, logs];
const POLL_MS = 2000;
let current = null;

function renderNav() {
  mount($("#nav"), html`<span class="nav-indicator" id="nav-indicator"></span>
    ${VIEWS.map((v, i) => html`
      <a class="nav-link" href="#/${v.id}" data-view="${v.id}" title="${v.title} (${i + 1})">
        ${icon(v.icon, 17)}<span class="label">${v.title}</span><span class="count" id="count-${v.id}"></span>
      </a>`)}`);
}

function moveIndicator() {
  const active = $(".nav-link[aria-current=page]");
  const indicator = $("#nav-indicator");
  if (!active || !indicator) return;
  indicator.style.transform = `translateY(${active.offsetTop}px)`;
  indicator.style.height = `${active.offsetHeight}px`;
}

function route() {
  const id = location.hash.replace(/^#\/?/, "") || "overview";
  const next = VIEWS.find(v => v.id === id) ?? overview;
  if (next === current) return;
  current?.destroy?.();
  current = next;
  for (const link of $$(".nav-link")) {
    if (link.dataset.view === next.id) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  moveIndicator();
  document.title = `${next.title} · pgadmin-bridge`;
  const root = $("#view");
  root.classList.remove("view-enter");
  void root.offsetWidth;
  root.classList.add("view-enter");
  if (!store.state) return skeleton(root);
  next.render(root);
  if (next.logs) refreshLog();
  window.scrollTo({ top: 0 });
}

function skeleton(root) {
  mount(root, html`
    <div class="page-head"><div style="display:grid;gap:10px"><div class="skeleton" style="width:220px;height:26px"></div>
      <div class="skeleton" style="width:420px;max-width:80vw;height:14px"></div></div></div>
    <div class="stats">${[1, 2, 3, 4].map(() => html`<div class="skeleton" style="height:108px;border-radius:var(--radius-lg)"></div>`)}</div>
    <div class="skeleton" style="height:320px;border-radius:var(--radius-lg)"></div>`);
}

function renderChrome() {
  const s = store.state;
  const ep = endpoint(s);
  mount($("#listener"), store.online
    ? html`<span class="dot green pulse"></span><span class="listener-text"><strong>Accepting connections</strong><code>${ep.host}:${ep.port}</code></span>`
    : html`<span class="dot red"></span><span class="listener-text"><strong>Bridge unreachable</strong><code>retrying…</code></span>`);
  if (!s) return;
  $("#version").textContent = `v${s.version}`;
  const counts = { instances: s.config.instances.length || "", connections: s.connections.length || "" };
  for (const [id, n] of Object.entries(counts)) {
    const el = $(`#count-${id}`);
    if (el.textContent !== String(n)) el.textContent = n;
    el.classList.toggle("live", id === "connections" && n > 0);
  }
}

function renderTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  mount($("#theme"), icon(dark ? "sun" : "moon", 17));
  $("#theme").title = dark ? "Light theme" : "Dark theme";
  return dark;
}

function installTheme() {
  renderTheme();
  $("#theme").addEventListener("click", () => {
    const theme = renderTheme() ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("theme", theme); } catch { /* private mode */ }
    renderTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderTheme);
}

function installShortcuts() {
  document.addEventListener("keydown", event => {
    if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea, select, dialog, .drawer")) return;
    const view = VIEWS[Number(event.key) - 1];
    if (view) location.hash = `#/${view.id}`;
  });
}

async function poll() {
  if (!document.hidden) {
    await Promise.all([refresh(), current?.logs ? refreshLog() : null]);
  }
  setTimeout(poll, POLL_MS);
}

subscribe(() => {
  renderChrome();
  const root = $("#view");
  if (!current || !store.state) return;
  if (!root.dataset.ready) {
    root.dataset.ready = "1";
    current.render(root);
    if (current.logs) refreshLog();
  } else {
    current.update(root);
  }
});

renderNav();
installTheme();
installCopy();
installShortcuts();
window.addEventListener("hashchange", route);
window.addEventListener("resize", moveIndicator);
route();
renderChrome();
poll();
