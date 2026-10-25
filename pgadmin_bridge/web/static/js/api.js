// JSON API of the bridge. Every call resolves to the parsed body or throws with the server's error text.

export const MASK = "••••••••";

async function request(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, init);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`HTTP ${response.status}`);
  }
  if (data.error) throw new Error(data.error);
  return data;
}

export const api = {
  state: () => request("/api/state"),
  log: () => request("/api/log"),
  save: config => request("/api/config", config),
  test: instance => request("/api/test", instance),
  rescan: () => request("/api/rescan", {}),
  kill: key => request("/api/kill", { key }),
};
