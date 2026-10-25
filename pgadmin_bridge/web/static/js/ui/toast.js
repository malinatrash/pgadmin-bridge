// Transient notifications in the bottom-right corner.
import { $, element, html, settle } from "../dom.js";
import { icon } from "../icons.js";

const ICONS = { success: "checkCircle", error: "xCircle", info: "info" };

export function toast(title, { kind = "success", detail = "", timeout = kind === "error" ? 7000 : 3500 } = {}) {
  const el = element(html`
    <div class="toast ${kind}">
      ${icon(ICONS[kind], 18)}
      <div class="toast-text"><strong>${title}</strong>${detail ? html`<span>${detail}</span>` : ""}</div>
      <button class="icon-btn" type="button" aria-label="Dismiss" style="width:24px;height:24px">${icon("x", 14)}</button>
    </div>`);
  const close = async () => {
    if (el.classList.contains("leave")) return;
    el.classList.add("leave");
    await settle(el, 300);
    el.remove();
  };
  el.querySelector("button").addEventListener("click", close);
  $("#toasts").append(el);
  setTimeout(close, timeout);
}

export const toastError = (title, error) => toast(title, { kind: "error", detail: error?.message ?? String(error) });
