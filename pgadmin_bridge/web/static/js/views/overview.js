// Overview: live stats, the connection-string builder and a getting-started checklist / activity feed.
import { $, html, mount, on } from "../dom.js";
import { icon } from "../icons.js";
import { highlight, parse } from "../logparse.js";
import { endpoint, store, userFor } from "../store.js";
import { copyButton, countTo, segmented, selectSegment, syncSegmented } from "../ui/controls.js";
import { rescan } from "./shared.js";

const FORMATS = [{ value: "psql", label: "psql" }, { value: "jdbc", label: "JDBC" }, { value: "uri", label: "URI" }, { value: "env", label: "libpq env" }];
let form = { server: "", db: "", format: "psql" };
let lastFeed = "";
let off = [];

function snippet(format, { host, port }, user, db) {
  const dbname = db || "postgres";
  const u = encodeURIComponent(user);
  switch (format) {
    case "jdbc": return `jdbc:postgresql://${host}:${port}/${dbname}?user=${u}&sslmode=disable`;
    case "uri": return `postgresql://${u}@${host}:${port}/${dbname}?sslmode=disable`;
    case "env": return `PGHOST=${host} PGPORT=${port} PGUSER='${user}' PGDATABASE=${dbname} PGSSLMODE=disable`;
    default: return `psql "host=${host} port=${port} user=${user} dbname=${dbname} sslmode=disable"`;
  }
}

function colorize(format, text) {
  if (format === "psql") return html`<span class="tok-k">psql</span> <span class="tok-s">${text.slice(5)}</span>`;
  if (format === "env") return html`${text.split(" ").map((kv, i) => {
    const [k, ...v] = kv.split("=");
    return html`${i ? " " : ""}<span class="tok-k">${k}</span><span class="tok-p">=</span><span class="tok-s">${v.join("=")}</span>`;
  })}`;
  return html`<span class="tok-s">${text}</span>`;
}

function stat(id, label, iconName, sub) {
  return html`<div class="card stat">
    <div class="stat-label">${icon(iconName, 15)} ${label}</div>
    <div class="stat-value" id="${id}">0</div>
    <div class="stat-sub" id="${id}-sub">${sub}</div>
  </div>`;
}

function renderConnect(root) {
  const s = store.state;
  const servers = s.servers;
  const box = $("#connect-body", root);
  if (!servers.length) {
    return mount(box, html`<div class="empty">
      <div class="empty-icon">${icon("database", 24)}</div>
      <h3>No servers discovered yet</h3>
      <p>Add the pgAdmin you sign in to. Every server in its tree becomes something a client can connect to.</p>
      <a class="btn btn-primary" href="#/instances">${icon("plus", 15)} Add pgAdmin instance</a>
    </div>`);
  }
  if (!servers.some(x => userFor(x) === form.server)) form.server = userFor(servers[0]);
  const ep = endpoint(s);
  const code = snippet(form.format, ep, form.server, form.db);
  mount(box, html`
    <div class="connect-controls">
      <label class="field"><span class="field-label">Server (User)</span>
        <div class="input-wrap select-wrap">${icon("server", 15)}
          <select class="input" name="server">${servers.map(x => html`
            <option value="${userFor(x)}" ${userFor(x) === form.server ? "selected" : ""}>${userFor(x)}${x.unique ? html` — ${x.instance}` : ""}</option>`)}
          </select></div></label>
      <label class="field"><span class="field-label">Database</span>
        <div class="input-wrap">${icon("database", 15)}
          <input class="input mono" name="db" value="${form.db}" placeholder="maintenance db" spellcheck="false" autocomplete="off"></div></label>
    </div>
    <dl class="params">
      ${param("Host", ep.host)}${param("Port", String(ep.port))}${param("User", form.server)}
      ${param("Database", form.db || "(server's maintenance database)", !form.db)}
      <div class="param"><dt>Password</dt><dd class="note">The proxy password from <a href="#/settings">Settings</a></dd><span></span></div>
      ${param("SSL mode", "disable")}
    </dl>
    <div class="snippet">
      <div class="snippet-head"><span class="field-label">Connection string</span>${segmented("format", FORMATS, form.format)}</div>
      <pre class="code" id="snippet">${colorize(form.format, code)}${copyButton(code, "Copy connection string")}</pre>
    </div>`);
  syncSegmented($("[data-segmented]", box));
}

function param(label, value, muted = false) {
  return html`<div class="param"><dt>${label}</dt><dd class="${muted ? "note" : ""}">${value}</dd>${muted ? html`<span></span>` : copyButton(value, `Copy ${label}`)}</div>`;
}

function renderSide(root) {
  const s = store.state;
  const steps = [
    { done: s.config.instances.length > 0, title: "Add a pgAdmin instance", text: "The URL and the account you sign in with.", href: "#/instances" },
    { done: s.servers.length > 0, title: "Discover servers", text: "Every server in the pgAdmin tree becomes a User.", href: "#/instances" },
    { done: Boolean(s.config.password), title: "Set the proxy password", text: "This is what database clients send.", href: "#/settings" },
    { done: s.connections.length > 0, title: "Connect a client", text: "psql, DataGrip, DBeaver, JDBC — anything that speaks Postgres.", href: "#/overview" },
  ];
  const left = steps.filter(x => !x.done).length;
  const checklist = $("#checklist", root);
  checklist.hidden = left === 0;
  if (left) {
    mount(checklist, html`
      <div class="card-head"><div><h2>Getting started</h2><p>${steps.length - left} of ${steps.length} done</p></div></div>
      <div class="progress" style="margin-top:var(--space-4)"><span style="width:${((steps.length - left) / steps.length) * 100}%"></span></div>
      <div class="checklist">${steps.map((x, i) => html`
        <a class="check-item ${x.done ? "done" : ""}" href="${x.href}" style="color:inherit;text-decoration:none">
          <span class="check-mark">${x.done ? icon("check", 13) : i + 1}</span>
          <span><span class="check-title">${x.title}</span><p>${x.text}</p></span>
        </a>`)}</div>`);
  }
}

function renderFeed(root) {
  const items = store.lines.slice(-60).map(parse).filter(x => x.kind !== "http").slice(-9).reverse();
  const key = items.map(x => x.time + x.text).join("\n");
  if (key === lastFeed) return;
  const seen = lastFeed ? new Set(lastFeed.split("\n")) : null;
  lastFeed = key;
  const icons = { sql: "zap", error: "alert", session: "plug", system: "info" };
  mount($("#feed", root), items.length ? html`${items.map(x => html`
    <div class="feed-item ${x.kind} ${seen && !seen.has(x.time + x.text) ? "enter" : ""}">
      ${icon(icons[x.kind], 14)}
      <span class="msg mono">${x.sql ? html`<b>${x.sql.tag}</b> ${highlight(x.sql.sql)}` : x.text}</span>
      <time>${x.time}</time>
    </div>`)}` : html`<div class="empty" style="padding:var(--space-8)"><p>Nothing yet. Queries show up here as they run.</p></div>`);
}

function updateStats(root) {
  const s = store.state;
  const open = s.connections.filter(c => c.backend).length;
  countTo($("#st-inst", root), s.config.instances.length);
  countTo($("#st-srv", root), s.servers.length);
  countTo($("#st-conn", root), s.connections.length);
  countTo($("#st-back", root), open);
  $("#st-back-sub", root).textContent = `${s.connections.length - open} released while idle`;
  $("#st-conn-sub", root).textContent = `${s.connections.filter(c => c.in_tx).length} in a transaction`;
}

export default {
  id: "overview",
  title: "Overview",
  icon: "overview",
  logs: true,

  render(root) {
    lastFeed = "";
    mount(root, html`
      <header class="page-head">
        <div><h1>Overview</h1><p>Point any PostgreSQL client at the bridge. Every query runs through pgAdmin's Query Tool with your own pgAdmin account.</p></div>
        <div class="page-actions"><button class="btn" data-action="rescan">${icon("refresh", 15)} Rescan servers</button></div>
      </header>
      <section class="stats">
        ${stat("st-inst", "pgAdmin instances", "layers", "configured")}
        ${stat("st-srv", "Servers", "server", "reachable as User")}
        ${stat("st-conn", "Clients", "plug", "")}
        ${stat("st-back", "Open backends", "activity", "")}
      </section>
      <div class="overview-grid">
        <section class="card">
          <div class="card-head"><div><h2>Connect a client</h2><p>Pick a server and copy the settings into DataGrip, DBeaver or psql.</p></div></div>
          <div class="card-body" id="connect-body"></div>
        </section>
        <div class="stack">
          <section class="card" id="checklist" hidden></section>
          <section class="card">
            <div class="card-head"><div><h2>Recent activity</h2><p>Statements and sessions, newest first</p></div>
              <a class="btn btn-ghost btn-sm" href="#/logs">All logs ${icon("arrowRight", 14)}</a></div>
            <div class="feed" id="feed"></div>
          </section>
        </div>
      </div>`);

    off = [
      on(root, "click", "[data-action=rescan]", (_, b) => rescan(b)),
      on(root, "change", "select[name=server]", (e) => { form.server = e.target.value; renderConnect(root); }),
      on(root, "input", "input[name=db]", (e) => {
        form.db = e.target.value.trim();
        const pos = e.target.selectionStart;
        renderConnect(root);
        const input = $("input[name=db]", root);
        input.focus();
        input.setSelectionRange(pos, pos);
      }),
      on(root, "click", "[data-segmented=format] button", (_, b) => {
        form.format = b.dataset.value;
        selectSegment(b.parentElement, form.format);
        const ep = endpoint(store.state);
        const code = snippet(form.format, ep, form.server, form.db);
        mount($("#snippet", root), html`${colorize(form.format, code)}${copyButton(code, "Copy connection string")}`);
      }),
    ];
    this.update(root, true);
  },

  update(root, full = false) {
    const s = store.state;
    const sig = JSON.stringify([s.servers, s.listen]);
    // the builder is re-rendered only when servers or the address change, and never under the cursor
    if (full || (sig !== this.sig && !$("input[name=db]", root)?.matches(":focus"))) {
      this.sig = sig;
      renderConnect(root);
    }
    updateStats(root);
    renderSide(root);
    renderFeed(root);
  },

  destroy() {
    off.forEach(f => f());
    off = [];
  },
};
