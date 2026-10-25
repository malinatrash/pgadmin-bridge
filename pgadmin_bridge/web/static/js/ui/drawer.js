// A panel that slides in from the right over a dimmed page; closes on Escape, overlay click or the X.
import { element, html, settle } from "../dom.js";
import { icon } from "../icons.js";

export function openDrawer({ title, subtitle = "", body, footer }) {
  const overlay = element(html`<div class="overlay"></div>`);
  const drawer = element(html`
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="${title}">
      <header class="drawer-head">
        <div><h2>${title}</h2>${subtitle ? html`<p>${subtitle}</p>` : ""}</div>
        <button class="icon-btn" type="button" data-close aria-label="Close">${icon("x", 18)}</button>
      </header>
      <div class="drawer-body">${body}</div>
      <footer class="drawer-foot">${footer}</footer>
    </aside>`);
  const previous = document.activeElement;
  document.body.append(overlay, drawer);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    overlay.classList.add("open");
    drawer.classList.add("open");
  }));
  setTimeout(() => drawer.querySelector("input, select, textarea, button:not([data-close])")?.focus(), 80);

  const onKey = event => { if (event.key === "Escape") close(); };
  let closed = false;
  const close = async () => {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("hashchange", close);
    overlay.classList.remove("open");
    drawer.classList.remove("open");
    await settle(drawer, 450);
    overlay.remove();
    drawer.remove();
    previous?.focus?.();
  };
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", close);
  overlay.addEventListener("click", close);
  drawer.querySelector("[data-close]").addEventListener("click", close);
  return { el: drawer, close };
}
