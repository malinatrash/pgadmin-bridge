// Shared client state: the last /api/state answer, the log tail and reachability. Views subscribe to changes.
import { api } from "./api.js";

export const store = { state: null, lines: [], online: true };
const listeners = new Set();

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  for (const listener of listeners) listener(store);
}

export function setState(state) {
  store.state = state;
  store.online = true;
  emit();
}

export async function refresh() {
  try {
    setState(await api.state());
  } catch {
    store.online = false;
    emit();
  }
}

export async function refreshLog() {
  try {
    const { lines } = await api.log();
    if (lines.length !== store.lines.length || lines.at(-1) !== store.lines.at(-1)) {
      store.lines = lines;
      emit();
    }
  } catch { /* reported by refresh() */ }
}

/** Saves the config with the given changes applied; masked passwords are kept by the server. */
export async function saveConfig(changes) {
  const state = await api.save({ ...store.state.config, ...changes });
  setState(state);
  return state;
}

/** Name a client puts in the User field for a discovered server. */
export const userFor = server => (server.unique ? server.server : `${server.instance}/${server.server}`);

/** Where clients connect: the advertised address, or the browser's host when listening on all interfaces. */
export function endpoint(state) {
  const host = state?.listen?.host;
  return {
    host: !host || host === "0.0.0.0" || host === "::" ? location.hostname : host,
    port: state?.listen?.port ?? 5432,
  };
}
