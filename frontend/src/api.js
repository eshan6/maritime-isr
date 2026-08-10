// Thin API client. All requests go to /api on the same origin.
//
// Two serving modes, one code path:
//   * Python-only demo — the FastAPI backend serves this bundle and the /api
//     routes itself, and injects window.__MISR_TOKEN__ into the page.
//   * Vite dev server — proxies /api to the backend (see vite.config.js).
// Either way we send the token header ourselves, defaulting to the dev token.

const BASE = "/api";
const TOKEN =
  (typeof window !== "undefined" && window.__MISR_TOKEN__) || "maritime-isr-dev";

function authHeaders(extra) {
  return { "X-API-Token": TOKEN, ...(extra || {}) };
}

async function get(path, params) {
  const url = new URL(BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    }
  }
  const r = await fetch(url.pathname + url.search, {
    headers: authHeaders({ Accept: "application/json" }),
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
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

// The one-click incident report. It cannot be a plain <a href> — every /api
// route is token-gated and a navigation sends no headers, so the browser would
// get a 401 page instead of a file. Fetch it, then hand the blob to a synthetic
// click, honouring the filename the server chose (it carries the SCENARIO-
// prefix for generated vessels, which is the label that must survive being
// forwarded).
async function downloadReport(vesselId) {
  const path = `${BASE}/vessels/${encodeURIComponent(vesselId)}/report`;
  const r = await fetch(path, { headers: authHeaders() });
  if (!r.ok) throw new Error(`${r.status} report`);

  const disp = r.headers.get("Content-Disposition") || "";
  const m = disp.match(/filename="([^"]+)"/);
  const name = m ? m[1] : `incident-report-${vesselId}.html`;

  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoking immediately can cancel the download in some browsers; one tick is
  // enough for the click to have been handed off.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return name;
}

export const api = {
  health: () => get("/health"),
  downloadReport,
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
  findings: (params) => get("/findings", params),
  events: (params) => get("/events", params),
  // Per-H3-cell counts over the WHOLE corpus, not a page. The map uses this
  // instead of plotting every event, which on the real corpus both truncated
  // silently and rendered 27,000 dots as a smear.
  eventDensity: (params) => get("/events/density", params),
  detections: (params) => get("/detections", params),
  tracks: (params) => get("/tracks", params),
  scenes: () => get("/scenes"),
  ports: () => get("/ports"),
};
