// Thin API client. All requests go to /api (the Vite proxy rewrites to the
// FastAPI backend and injects the auth token — see vite.config.js), so nothing
// here holds a secret and there is no CORS to negotiate.

const BASE = "/api";

async function get(path, params) {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  const r = await fetch(url.pathname + url.search, {
    headers: { Accept: "application/json" },
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => "");
    throw new Error(`${r.status} ${path} ${detail.slice(0, 200)}`);
  }
  return r.json();
}

async function post(path, body) {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

export const api = {
  health: () => get("/health"),
  stats: () => get("/stats"),
  vessels: (params) => get("/vessels", params),
  vessel: (id) => get(`/vessels/${encodeURIComponent(id)}`),
  track: (id, params) => get(`/vessels/${encodeURIComponent(id)}/track`, params),
  neighbourhood: (id, hops) =>
    get(`/vessels/${encodeURIComponent(id)}/neighbourhood`, { hops }),
  alerts: (params) => get("/alerts", params),
  alert: (id) => get(`/alerts/${encodeURIComponent(id)}`),
  dispose: (id, disposition) =>
    post(`/alerts/${encodeURIComponent(id)}/disposition`, {
      alert_id: id,
      disposition,
    }),
  events: (params) => get("/events", params),
  scenes: () => get("/scenes"),
  ports: () => get("/ports"),
};
