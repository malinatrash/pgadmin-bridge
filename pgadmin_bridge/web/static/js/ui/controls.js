// Small interactive pieces shared by views: copy buttons, segmented controls, password fields, animated counters.
import { html } from "../dom.js";
import { icon } from "../icons.js";

export function copyButton(text, label = "Copy", cls = "icon-btn") {
  return html`<button class="${cls} copy-btn" type="button" data-copy="${text}" aria-label="${label}" title="${label}">
    ${icon("copy", 15, "icon-copy")}${icon("check", 15, "icon-check")}</button>`;
}

async function writeClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = Object.assign(document.createElement("textarea"), { value: text });
  area.style.cssText = "position:fixed;opacity:0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

/** One document-level handler for every [data-copy] button. */
export function installCopy() {
  document.addEventListener("click", async event => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    await writeClipboard(button.dataset.copy);
    button.classList.add("copied");
    clearTimeout(button._timer);
    button._timer = setTimeout(() => button.classList.remove("copied"), 1400);
  });
}

export function segmented(name, options, value) {
  return html`<div class="segmented" role="group" data-segmented="${name}">
    <span class="segmented-thumb"></span>
    ${options.map(o => html`<button type="button" data-value="${o.value}" aria-pressed="${String(o.value === value)}">
      ${o.label}${o.count != null ? html`<span class="n">${o.count}</span>` : ""}</button>`)}
  </div>`;
}

/** Moves the thumb under the pressed button; call after render and on every change. */
export function syncSegmented(root) {
  const thumb = root.querySelector(".segmented-thumb");
  const active = root.querySelector('[aria-pressed="true"]');
  if (!thumb || !active) return;
  thumb.style.width = `${active.offsetWidth}px`;
  thumb.style.transform = `translateX(${active.offsetLeft}px)`;
}

export function selectSegment(root, value) {
  for (const b of root.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.value === value));
  syncSegmented(root);
}

export function passwordField({ name, value = "", placeholder = "", autocomplete = "new-password", extra = "" }) {
  return html`<div class="input-wrap">
    ${icon("lock", 15)}
    <input class="input mono" type="password" name="${name}" value="${value}" placeholder="${placeholder}"
      autocomplete="${autocomplete}" spellcheck="false">
    ${extra}
    <button class="icon-btn" type="button" data-reveal aria-label="Show password">${icon("eye", 15)}</button>
  </div>`;
}

export function installReveal(root) {
  root.addEventListener("click", event => {
    const button = event.target.closest("[data-reveal]");
    if (!button) return;
    const input = button.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.innerHTML = String(icon(show ? "eyeOff" : "eye", 15));
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
  });
}

/** Animates a number from its current text to value. */
export function countTo(el, value) {
  const from = Number(el.textContent) || 0;
  if (from === value) return;
  el.classList.remove("bump");
  void el.offsetWidth;
  el.classList.add("bump");
  const start = performance.now();
  const step = now => {
    const t = Math.min(1, (now - start) / 450);
    el.textContent = String(Math.round(from + (value - from) * (1 - (1 - t) ** 3)));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
