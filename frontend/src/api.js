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

// ---- request gate --------------------------------------------------------
// The map asks for eleven things the instant it opens — 6,000 events, 2,000
// radar contacts, 600 radar tracks, event density, detections, scenes, alerts,
// ports, tracks — each a cold scan over Parquet. On the laptop that is fine.
// On a free host with a tenth of a CPU core it is not: fired together they
// saturate the box for long enough that the host's OWN health probe misses its
// window (Render allows 5 seconds), and the host concludes the service is dead
// and restarts it. The restart is cold, the map reconnects and fires the same
// eleven again, and the service never converges. What the operator sees is
// "API unreachable" — a connectivity story for what is really a load story.
//
// So cap how many are in flight. The work is identical and the wall-clock wait
// is close to it, because a tenth of a core was never going to run eleven
// scans in parallel anyway — it was going to interleave them and finish no
// sooner. What changes is that the server keeps a slice free to answer
// anything else, its own liveness check included. The map fills in
// progressively rather than arriving at once.
//
// Three, not one: the requests are server-bound rather than CPU-bound at this
// end, so a little overlap hides latency, and the queue still drains in order.
const MAX_IN_FLIGHT = 3;

let inFlight = 0;
const waiting = [];

function acquire() {
  if (inFlight < MAX_IN_FLIGHT) {
    inFlight += 1;
    return Promise.resolve();
  }
  return new Promise((resolve) => waiting.push(resolve));
}

function release() {
  // Hand the slot straight to the next waiter rather than decrementing and
  // letting it re-test: that keeps exactly MAX_IN_FLIGHT live with no gap, and
  // no chance of two waiters both seeing the same free slot.
  const next = waiting.shift();
  if (next) next();
  else inFlight -= 1;
}

async function gated(fn, signal) {
  await acquire();
  try {
    // A scrub of the timeline queues dozens of prediction requests and aborts
    // all but the last. Without this check the aborted ones still reach the
    // server once their slot comes up — the exact pile-on the gate exists to
    // prevent. Honour the abort that already happened while queued.
    if (signal && signal.aborted) {
      throw new DOMException("aborted while queued", "AbortError");
    }
    return await fn();
  } finally {
    release();
  }
}

// `signal` is optional and only one caller needs it: the map re-asks for
// forward projections every time the clock crosses a refresh boundary, and a
// scrub drags the clock across dozens of them in a second. Without an abort the
// answers arrive out of order and the map settles on whichever slow response
// landed last, which is a picture of a moment the operator has already left.
async function get(path, params, signal) {
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
  return gated(async () => {
    const r = await fetch(url.pathname + url.search, {
      headers: authHeaders({ Accept: "application/json" }),
      signal,
    });
    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      throw new Error(`${r.status} ${path} ${detail.slice(0, 200)}`);
    }
    return r.json();
  }, signal);
}

async function del(path) {
  return gated(async () => {
    const r = await fetch(BASE + path, {
      method: "DELETE",
      headers: authHeaders({ Accept: "application/json" }),
    });
    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      throw new Error(`${r.status} ${detail.slice(0, 300)}`);
    }
    return r.json();
  });
}

async function post(path, body) {
  return gated(async () => {
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
  });
}

// The one-click incident report. It cannot be a plain <a href> — every /api
// route is token-gated and a navigation sends no headers, so the browser would
// get a 401 page instead of a file. Fetch it, then hand the blob to a
// programmatic click, honouring the filename the server chose: the server owns
// that name and whatever it encodes has to survive being forwarded.
async function downloadReport(vesselId) {
  const path = `${BASE}/vessels/${encodeURIComponent(vesselId)}/report`;
  // Only the network half takes a slot; handing the blob to a click is local
  // work and holding a slot across it would stall other requests for nothing.
  const { blob, name } = await gated(async () => {
    const r = await fetch(path, { headers: authHeaders() });
    if (!r.ok) throw new Error(`${r.status} report`);

    const disp = r.headers.get("Content-Disposition") || "";
    const m = disp.match(/filename="([^"]+)"/);
    return {
      blob: await r.blob(),
      name: m ? m[1] : `incident-report-${vesselId}.html`,
    };
  });

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
  // ---- the rule modules, three-valued (ADR-032/035/036/037) ------------
  // NOT memoised: an operator opens a subject, reads the checks, and may come
  // back to her after a re-run. A cached "not checkable" would outlive the
  // corpus that made it true.
  vesselChecks: (id) => get(`/vessels/${encodeURIComponent(id)}/checks`),
  // Activity, the local baseline, and the projection — three capabilities that
  // all read motion and nothing else.
  vesselMotion: (id, params) =>
    get(`/vessels/${encodeURIComponent(id)}/motion`, params),
  // The three-way split over the whole corpus. Memoised: it is a sweep over
  // landed rows and cannot change while the server is up.
  checksCoverage: () => memo("checks-coverage", () => get("/checks/coverage")),
  // ---- the contact nobody can name (ADR-033) ---------------------------
  contactProfile: (id) =>
    get(`/radar/contacts/${encodeURIComponent(id)}/profile`),
  // ---- the electro-optical loop (ADR-037) ------------------------------
  // There is no camera. Every response carries `simulated: true` and the
  // disclosure string, and the UI prints it on the capture itself.
  eoCaptures: (params) => get("/eo/captures", params),
  eoSummary: () => memo("eo-summary", () => get("/eo/summary")),
  // ---- what motion can and cannot separate -----------------------------
  // `compute` is deliberately opt-in and NOT memoised on this side: the server
  // caches the measurement for the life of its process, and memoising the
  // un-computed answer here would make the button do nothing on a second press.
  vesselTypeModel: (params) => get("/analysis/vessel-type", params),
  interactionCapability: () =>
    memo("interactions", () => get("/analysis/interactions")),
  baselines: (params) => get("/baselines", params),
  events: (params) => get("/events", params),
  // Per-H3-cell counts over the WHOLE corpus, not a page. The map uses this
  // instead of plotting every event, which on the real corpus both truncated
  // silently and rendered 27,000 dots as a smear.
  eventDensity: (params) => get("/events/density", params),
  detections: (params) => get("/detections", params),
  tracks: (params) => get("/tracks", params),
  // Forward projection of every vessel broadcasting at `at` (ADR-039). Asked
  // fresh as the clock advances rather than precomputed, because a projection
  // is made from one fix at one moment: there is no single answer to cache.
  // Never memoised for the same reason.
  predictions: (params, signal) => get("/predictions", params, signal),
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
