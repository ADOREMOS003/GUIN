const BASE = "/api/v1";

async function request(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok || data.status === "error") {
    throw new Error(data.message || `Request failed: ${res.status}`);
  }
  return data.data;
}

export const api = {
  status: () => request("/status"),
  tools: () => request("/tools"),
  run: (payload) => request("/run", { method: "POST", body: JSON.stringify(payload) }),
  validate: (dataset_path) =>
    request("/validate", { method: "POST", body: JSON.stringify({ dataset_path }) }),
  provenance: () => request("/provenance"),
  provenanceDiff: (record_a, record_b) =>
    request("/provenance/diff", {
      method: "POST",
      body: JSON.stringify({ record_a, record_b }),
    }),
  replay: (provenance_file) =>
    request("/replay", { method: "POST", body: JSON.stringify({ provenance_file }) }),
  getConfig: () => request("/config"),
  putConfig: (payload) =>
    request("/config", { method: "PUT", body: JSON.stringify(payload) }),
  runTool: (name, payload) =>
    request(`/tools/${name}`, { method: "POST", body: JSON.stringify({ payload }) }),
};
