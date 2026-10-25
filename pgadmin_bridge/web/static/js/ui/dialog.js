// Modal confirmation for destructive actions, on top of the native <dialog>.
import { element, html } from "../dom.js";
import { icon } from "../icons.js";

export function confirm({ title, message, action = "Confirm", danger = true }) {
  const dialog = element(html`
    <dialog class="dialog">
      <form method="dialog">
        <div class="dialog-body">
          <div class="dialog-icon">${icon(danger ? "alert" : "info", 20)}</div>
          <div><h2>${title}</h2><p>${message}</p></div>
        </div>
        <div class="dialog-foot">
          <button class="btn" value="cancel">Cancel</button>
          <button class="btn ${danger ? "btn-danger-solid" : "btn-primary"}" value="ok">${action}</button>
        </div>
      </form>
    </dialog>`);
  document.body.append(dialog);
  dialog.showModal();
  dialog.querySelector('[value="ok"]').focus();
  return new Promise(resolve => {
    dialog.addEventListener("close", () => {
      resolve(dialog.returnValue === "ok");
      dialog.remove();
    }, { once: true });
  });
}
