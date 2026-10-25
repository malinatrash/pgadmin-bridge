// Logs: the bridge's log tail as a terminal, with kind filters, search, pause and follow-tail.
import { $, html, mount, on } from "../dom.js";
import { icon } from "../icons.js";
import { mark, parse, sqlBody } from "../logparse.js";
import { store } from "../store.js";
import { segmented, selectSegment, syncSegmented } from "../ui/controls.js";

const KINDS = [
  { value: "all", label: "All" },
  { value: "sql", label: "SQL" },
  { value: "error", label: "Errors" },
  { value: "session", label: "Sessions" },
  { value: "http", label: "pgAdmin HTTP" },
];
const LABELS = { sql: "sql", error: "error", http: "http", session: "conn", system: "info" };
const view = { kind: "all", query: "", paused: false, follow: true, rendered: "" };
let off = [];

function counts(entries) {
  const n = { all: entries.length };
  for (const e of entries) n[e.kind] = (n[e.kind] || 0) + 1;
  return n;
}

function line(e) {
  const body = e.sql ? sqlBody(e.sql, view.query) : view.query ? mark(html`${e.text}`, view.query) : e.text;
  return html`<div class="log-line k-${e.kind}"><time>${e.time}</time><span class="log-kind">${LABELS[e.kind]}</span><span class="log-text">${body}</span></div>`;
}

function render(root) {
  if (view.paused) return;
  const entries = store.lines.map(parse);
  const n = counts(entries);
  for (const b of $("[data-segmented=kind]", root).children) {
    const count = b.querySelector(".n");
    if (count) count.textContent = n[b.dataset.value] || 0;
  }
  syncSegmented($("[data-segmented=kind]", root));
  const q = view.query.toLowerCase();
  const shown = entries.filter(e => (view.kind === "all" || e.kind === view.kind) && (!q || e.text.toLowerCase().includes(q)));
  const key = `${view.kind}|${view.query}|${store.lines.length}|${store.lines.at(-1)}`;
  if (key === view.rendered) return;
  view.rendered = key;
  const panel = $("#log-panel", root);
  mount($("#log-lines", root), shown.length ? html`${shown.map(line)}`
    : html`<div class="log-empty">${store.lines.length ? "No lines match the filter." : "The log is empty."}</div>`);
  if (view.follow) panel.scrollTop = panel.scrollHeight;
}

function setPaused(root, paused) {
  view.paused = paused;
  const b = $("[data-action=pause]", root);
  mount(b, html`${icon(paused ? "play" : "pause", 14)} ${paused ? "Resume" : "Pause"}`);
  b.setAttribute("aria-pressed", String(paused));
  if (!paused) render(root);
}

export default {
  id: "logs",
  title: "Logs",
  icon: "terminal",
  logs: true,

  render(root) {
    view.rendered = "";
    view.paused = false;
    view.follow = true;
    mount(root, html`
      <header class="page-head">
        <div><h1>Logs</h1><p>Every statement with its timing and result, sessions opening and releasing, and every HTTP call to pgAdmin.</p></div>
      </header>
      <div class="log-toolbar">
        <label class="input-wrap">${icon("search", 15)}
          <input class="input" type="search" id="log-search" placeholder="Filter lines" value="${view.query}" spellcheck="false">
          <kbd style="margin-right:8px">/</kbd></label>
        ${segmented("kind", KINDS.map(k => ({ ...k, count: 0 })), view.kind)}
        <span class="spacer"></span>
        <button class="btn btn-sm" data-action="pause" aria-pressed="false">${icon("pause", 14)} Pause</button>
      </div>
      <div class="log-panel" id="log-panel">
        <div class="log-lines" id="log-lines"></div>
        <button class="log-jump" data-action="jump" hidden>${icon("arrowDown", 14)} Jump to latest</button>
      </div>`);
    const panel = $("#log-panel", root);
    syncSegmented($("[data-segmented]", root));
    off = [
      on(root, "click", "[data-segmented=kind] button", (_, b) => {
        view.kind = b.dataset.value;
        selectSegment(b.parentElement, view.kind);
        render(root);
      }),
      on(root, "input", "#log-search", e => { view.query = e.target.value; render(root); }),
      on(root, "click", "[data-action=pause]", () => setPaused(root, !view.paused)),
      on(root, "click", "[data-action=jump]", () => panel.scrollTo({ top: panel.scrollHeight, behavior: "smooth" })),
    ];
    panel.addEventListener("scroll", () => {
      view.follow = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 40;
      $("[data-action=jump]", root).hidden = view.follow;
    });
    this.keys = event => {
      if (event.key === "/" && !event.target.closest("input, textarea, select")) {
        event.preventDefault();
        $("#log-search", root).focus();
      }
    };
    document.addEventListener("keydown", this.keys);
    render(root);
  },

  update: render,

  destroy() {
    off.forEach(f => f());
    off = [];
    document.removeEventListener("keydown", this.keys);
  },
};
