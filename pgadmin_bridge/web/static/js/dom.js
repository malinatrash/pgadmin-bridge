// Tiny templating: html`` escapes every interpolation unless it is already html (raw), plus formatting helpers.

class Html {
  constructor(text) { this.text = text; }
  toString() { return this.text; }
}

export const raw = text => new Html(text);

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function render(value) {
  if (value instanceof Html) return value.text;
  if (Array.isArray(value)) return value.map(render).join("");
  if (value == null || value === false) return "";
  return esc(value);
}

export function html(strings, ...values) {
  return raw(strings.reduce((out, s, i) => out + s + (i < values.length ? render(values[i]) : ""), ""));
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Replaces the children of el with the given html and returns el. */
export function mount(el, content) {
  el.innerHTML = String(content);
  return el;
}

/** Element from a single-root html template. */
export function element(content) {
  const t = document.createElement("template");
  t.innerHTML = String(content).trim();
  return t.content.firstElementChild;
}

/** Delegated event listener: on(root, "click", "[data-action]", (event, target) => ...). */
export function on(root, type, selector, handler) {
  const listener = event => {
    const target = event.target.closest(selector);
    if (target && root.contains(target)) handler(event, target);
  };
  root.addEventListener(type, listener);
  return () => root.removeEventListener(type, listener);
}

export function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function hostOf(url) {
  try { return new URL(url).host; } catch { return url; }
}

/** Waits for the CSS animation or transition on el to end, with a timeout for reduced motion. */
export function settle(el, ms = 450) {
  return new Promise(resolve => {
    const done = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(done, ms);
    el.addEventListener("animationend", done, { once: true });
    el.addEventListener("transitionend", done, { once: true });
  });
}
