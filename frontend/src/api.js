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
      if (v === undefined || v === null || v === "") continue;
      // An array becomes a REPEATED parameter (`?context=flag&context=port`),
      // which is what FastAPI's `List[str] = Query(None)` reads. Passing the
      // array to `set` would stringify it to `flag,port` and arrive as one
      // unrecognised family name.
      if (Array.isArray(v)) v.forEach((item) => url.searchParams.append(k, item));
      else url.searchParams.set(k, v);
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

async function del(path) {
  const r = await fetch(BASE + path, {
    method: "DELETE",
    headers: authHeaders({ Accept: "application/json" }),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => "");
    throw new Error(`${r.status} ${detail.slice(0, 300)}`);
  }
  return r.json();
}

async function post(path, body) {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    // The detail matters here: creating a geofence fails with a readable
    // reason ("a geofence needs a name", "invalid geometry — a self-crossing
    // outline?") and swallowing it leaves the operator with a status code.
    const detail = await r.text().catch(() => "");
    throw new Error(`${r.status} ${detail.slice(0, 300)}`);
  }
  return r.json();
}

// The one-click incident report. It cannot be a plain <a href> — every /api
// route is token-gated and a navigation sends no headers, so the browser would
// get a 401 page instead of a file. Fetch it, then hand the blob to a
// programmatic click, honouring the filename the server chose: the server owns
// that name and whatever it encodes has to survive being forwarded.
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

// ---- session cache -------------------------------------------------------
// React Router unmounts a view when you navigate away, so returning to it
// refetches everything from scratch. For values that CANNOT change while the
// server is up — the corpus time window, the graph seed list — that meant the
// operator paid the full wait again on every visit, and the map's scrubber
// disappeared and slowly came back each time.
//
// Cached by promise, not by result, so two callers racing on a cold cache
// share one request instead of firing two. Deliberately not a general HTTP
// cache: only endpoints whose answer is fixed for the life of the process
// belong here, and anything an operator can change (alerts, dispositions) must
// keep going to the server.
const _memo = new Map();
function memo(key, fn) {
  if (!_memo.has(key)) {
    _memo.set(key, fn().catch((e) => { _memo.delete(key); throw e; }));
  }
  return _memo.get(key);
}

export const api = {
  health: () => get("/health"),
  downloadReport,
  stats: () => get("/stats"),
  // Cheap: two aggregates per event table, not the whole dashboard sweep.
  corpusWindow: () => memo("corpus-window", () => get("/corpus-window")),
  graphSeeds: (limit) => memo(`graph-seeds:${limit || 12}`,
                              () => get("/graph/seeds", { limit })),
  // The whole web. Cached: it is the default view, so every return to the
  // Graph tab would otherwise re-fetch and re-run the force layout.
  //
  // `context` is part of the cache key — each combination is a different
  // graph, and keying on `limit` alone would serve the ownership-only answer
  // to a caller that had just switched the flag layer on.
  graphAll: (limit, context) => {
    const ctx = [...(context || [])].sort();
    return memo(`graph-all:${limit || 0}:${ctx.join(",")}`,
                () => get("/graph/all", { limit, context: ctx }));
  },
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
  // ---- the MDA assistant (ADR-031) -------------------------------------
  // NOT memoised: an alert disposition changes the queue, so a cached list
  // would show a subject the operator has just dismissed.
  voi: (params) => get("/voi", params),
  // Subject ids carry colons (`contact:radar:SYN-MUM:0214`), which is why the
  // route is declared `{subject_id:path}` — encodeURIComponent percent-encodes
  // them and Starlette hands the decoded value back.
  voiDetail: (id) => get(`/voi/${encodeURIComponent(id)}`),
  voiWorkload: () => get("/voi/workload"),
  voiCatalog: () => memo("voi-catalog", () => get("/voi/catalog")),
  voiAsk: (id, question) =>
    post(`/voi/${encodeURIComponent(id)}/ask`, { question }),
  events: (params) => get("/events", params),
  // Per-H3-cell counts over the WHOLE corpus, not a page. The map uses this
  // instead of plotting every event, which on the real corpus both truncated
  // silently and rendered 27,000 dots as a smear.
  eventDensity: (params) => get("/events/density", params),
  detections: (params) => get("/detections", params),
  tracks: (params) => get("/tracks", params),
  scenes: () => get("/scenes"),
  ports: () => get("/ports"),
  // ---- coastal radar (ADR-028) -----------------------------------------
  // The station list cannot change while the process is up — it is compiled
  // into the module, not read from a table — so it is memoised like the corpus
  // window. The contacts and tracks are landed data and are not.
  radarStations: () => memo("radar-stations", () => get("/radar/stations")),
  radarContacts: (params) => get("/radar/contacts", params),
  radarTracks: (params) => get("/radar/tracks", params),
  // ---- the maritime zone layer (ADR-030) -------------------------------
  // NOT memoised, unlike the radar stations: the operator can add and remove
  // geofences, so a cached zone list would show a box they just deleted.
  zones: (params) => get("/zones", params),
  zoneVessels: (id, params) =>
    get(`/zones/${encodeURIComponent(id)}/vessels`, params),
  createGeofence: (body) => post("/geofences", body),
  deleteGeofence: (id) => del(`/geofences/${encodeURIComponent(id)}`),
};
