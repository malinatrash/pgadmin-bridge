// Actions used by more than one view.
import { api } from "../api.js";
import { setState } from "../store.js";
import { toast, toastError } from "../ui/toast.js";

/** Runs an async action with the button in its loading state. */
export async function busy(button, action) {
  button?.classList.add("loading");
  button && (button.disabled = true);
  try {
    return await action();
  } finally {
    button?.classList.remove("loading");
    button && (button.disabled = false);
  }
}

export async function rescan(button) {
  try {
    const state = await busy(button, api.rescan);
    setState(state);
    toast("Server trees rescanned", { detail: `${state.servers.length} server${state.servers.length === 1 ? "" : "s"} discovered` });
  } catch (e) {
    toastError("Rescan failed", e);
  }
}
