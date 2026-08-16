// The map — primary view. AOI framed on the Arabian Sea, toggleable layers, and
// a time scrubber that animates vessels ALONG THEIR AIS TRACKS: at each clock
// tick every vessel is drawn at its interpolated position, so ships glide across
// the 8-week window rather than blinking on and off. Events (encounters,
// loitering, port visits, gaps) are persistent context markers, not the time
// signal. Click a vessel to open its entity panel.
//
// Where no AIS tracks exist for the window, nothing moves and the events and
// sanctioned-vessel markers still render.
import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate } from "../lib/format.js";
import { VesselPanel } from "../components/VesselPanel.jsx";

// AOI v1 — Arabian Sea / Indian west coast (config.py AOI_V1).
const AOI = { lonMin: 60, latMin: 5, lonMax: 78, latMax: 25 };

//: Wall-clock seconds the animation spends on one day of corpus time. Slow
//: enough to follow a vessel's movement across a day rather than watch it
//: flicker past.
const SECONDS_PER_DAY = 7;

const BASEMAP = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#eef2f6" } },
    { id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 0.85 } },
  ],
};

const LAYERS = [
  { id: "positions", label: "Vessel positions", color: "#1a5fb4" },
  { id: "tracks", label: "Vessel tracks", color: "#7aa8dd" },
  { id: "density", label: "Event density (whole corpus)", color: "#5b3fa8" },
  { id: "encounter", label: "Encounters", color: "#b0221b" },
  { id: "loitering", label: "Loitering", color: "#9a6300" },
  { id: "port_visit", label: "Port visits", color: "#1f7a4d" },
  { id: "gap", label: "AIS gaps", color: "#b0221b" },
  { id: "detections", label: "SAR radar contacts", color: "#00707a" },
  { id: "scenes", label: "Sentinel-1 footprints", color: "#6039c4" },
  { id: "ports", label: "Ports", color: "#55636f" },
  { id: "alerts", label: "Alert markers", color: "#b0221b" },
  // Coastal radar (ADR-028). All three are SYNTHETIC and say so in the label,
  // because a coverage map is the most persuasive picture this system can draw
  // and there is no radar behind it.
  { id: "radar_coverage", label: "Radar coverage (synthetic)", color: "#0b6e75" },
  { id: "radar_tracks", label: "Radar tracks (synthetic)", color: "#3aa0a8" },
  { id: "radar_contacts", label: "Radar dark contacts (synthetic)", color: "#d33682" },
];

const EVENT_COLOR = { encounter: "#b0221b", loitering: "#9a6300", port_visit: "#1f7a4d", gap: "#b0221b" };

//: Events requested for the individual-dot layers. The dots are the detail
//: view; `density` is what shows the whole corpus, so this cap no longer hides
//: anything — but it is still reported, because a truncated layer that says
//: nothing is how the map came to draw a chronological prefix of the real
//: corpus and stop.
const EVENT_LIMIT = 6000;

export function MapView() {
  const mapEl = useRef(null);
  const map = useRef(null);
  const nav = useNavigate();
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState(null);
  const [visible, setVisible] = useState({
    positions: true, tracks: false, density: true, encounter: true,
    loitering: true, port_visit: true, gap: true, detections: true,
    // Sentinel-1 footprints are REAL satellite coverage — 636 of them on the
    // laptop corpus — and defaulting the layer off meant the one unambiguously
    // real thing on the map was hidden until somebody found the checkbox.
    scenes: true, ports: true, alerts: true,
    // Coverage and contacts on by default — the coverage rings are what make a
    // dark contact readable ("we could see there, and heard nothing"), and a
    // contact drawn without them invites exactly the out-of-coverage-is-not-dark
    // misreading. The 1,200-odd track polylines are off: they are a dense mat
    // over the whole coast and are for inspection, not for the headline.
    radar_coverage: true, radar_tracks: false, radar_contacts: true,
  });
  const [data, setData] = useState({
    events: [], ports: [], scenes: [], alerts: [], density: [], detections: [],
    radarStations: [], radarContacts: [],
  });
  // What the API told us it could not fit in the events response, and what the
  // detections response is. Surfaced in the corner rather than swallowed.
  const [notes, setNotes] = useState({});
  const [tracks, setTracks] = useState([]);
  const [radarTracks, setRadarTracks] = useState([]);
  const [window_, setWindow] = useState(null); // {start,end} epoch ms
  const [windowError, setWindowError] = useState(null);
  const [t, setT] = useState(1); // 0..1 across the window
  const [playing, setPlaying] = useState(false);

  // ---- init map once ----
  useEffect(() => {
    const m = new maplibregl.Map({
      container: mapEl.current,
      style: BASEMAP,
      bounds: [[AOI.lonMin, AOI.latMin], [AOI.lonMax, AOI.latMax]],
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      addAoi(m);
      setReady(true);
    });
    map.current = m;
    return () => m.remove();
  }, []);

  // ---- load data (each layer independently; one slow/failing call must not
  //      blank the rest) ----
  useEffect(() => {
    let live = true;
    const set = (patch) => live && setData((d) => ({ ...d, ...patch }));
    const note = (k, v) =>
      live && v && setNotes((n) => ({ ...n, [k]: v }));

    // ---- FIRST, and that ordering is the actual fix ----------------------
    // A browser opens ~6 connections per origin. This effect fires eight
    // requests, and the time window used to be the eighth — so it queued
    // behind `/tracks`, measured at 3.06s against the scenario corpus (the
    // slowest call here by 40x). The scrubber was hidden until it landed, so
    // the demo's primary control arrived seconds after everything else and
    // did it again on every navigation back. Requesting it first costs
    // nothing and puts it on screen immediately; `api.corpusWindow` is
    // session-cached, so a return visit does not even reach the network.
    api.corpusWindow().then((w) => {
      if (live && w && w.start && w.end) {
        setWindow({ start: +new Date(w.start), end: +new Date(w.end) });
      } else if (live) {
        setWindowError("the corpus has no dated events to scrub through");
      }
    }).catch(() => live && setWindowError("could not load the corpus window"));

    api.events({ limit: EVENT_LIMIT })
      .then((r) => { set({ events: r.items }); note("events", r.note); })
      .catch(() => {});
    // Res 4 hexes are ~22 km across — coarse enough that 24,153 loitering
    // events become a few hundred markers instead of a solid smear.
    api.eventDensity({ res: 4 })
      .then((r) => { set({ density: r.items }); note("density", r.note); })
      .catch(() => {});
    api.detections()
      .then((r) => { set({ detections: r.items }); note("detections", r.note); })
      .catch(() => {});
    api.ports().then((r) => set({ ports: r.items })).catch(() => {});
    api.scenes().then((r) => { set({ scenes: r.items }); note("scenes", r.note); }).catch(() => {});
    api.alerts().then((r) => set({ alerts: r.items })).catch(() => {});
    api.tracks({ max_points: 160 })
      .then((r) => { live && setTracks(r.items || []); note("tracks", r.note); })
      .catch(() => {});
    api.radarStations()
      .then((r) => set({ radarStations: r.items || [] }))
      .catch(() => {});
    api.radarContacts({ status: "all", limit: 2000 })
      .then((r) => { set({ radarContacts: r.items || [] }); note("radar", r.note); })
      .catch(() => {});
    // Requested last and thinned hard. This is the heaviest layer in the app
    // (270k plots behind it) and it is off by default, so it must not be in
    // front of anything the operator sees immediately.
    api.radarTracks({ max_tracks: 600, max_points: 40 })
      .then((r) => live && setRadarTracks(r.items || []))
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const clockMs = useMemo(() => {
    if (!window_) return null;
    return window_.start + t * (window_.end - window_.start);
  }, [window_, t]);
  const clockSec = clockMs ? clockMs / 1000 : null;

  // ---- static layers: events, ports, scenes, alert markers, track lines ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderStatic(map.current, data, tracks, visible, (id) => setSelected(id));
    renderDensity(map.current, data.density, visible.density);
    renderRadar(map.current, data, radarTracks, visible);
  }, [ready, data, tracks, radarTracks, visible]);

  // ---- moving vessels: interpolate each track to the clock and glide ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderVessels(map.current, tracks, clockSec, visible.positions, (id) => setSelected(id));
  }, [ready, tracks, clockSec, visible.positions]);

  // ---- play/pause ----
  // Playback runs at a fixed SECONDS_PER_DAY, derived from the window's real
  // span, so an 8-week corpus and a 6-month one advance at the same readable
  // pace. Tying the step to a raw fraction instead made a long window blur past.
  useEffect(() => {
    if (!playing || !window_) return;
    const totalDays = Math.max(1, (window_.end - window_.start) / 86400000);
    const tickMs = 100;
    const dt = tickMs / (SECONDS_PER_DAY * 1000 * totalDays); // fraction per tick
    const h = setInterval(() => {
      setT((x) => (x >= 1 ? 0 : Math.min(1, x + dt)));
    }, tickMs);
    return () => clearInterval(h);
  }, [playing, window_]);

  const movingCount = tracks.length;

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={mapEl} style={{ position: "absolute", inset: 0 }} />

      <div className="layerbox">
        <h4>Layers</h4>
        {LAYERS.map((l) => (
          <label className="layer-toggle" key={l.id}>
            <input
              type="checkbox"
              checked={!!visible[l.id]}
              onChange={(e) => setVisible((v) => ({ ...v, [l.id]: e.target.checked }))}
            />
            <span className="layer-swatch" style={{ background: l.color }} />
            {l.label}
          </label>
        ))}
      </div>

      {/* What the map is NOT showing, in the operator's line of sight. Every
          one of these used to be silent: a capped event query looked like an
          empty second half of the window, and an empty SAR layer looked like a
          clean scene rather than a scene we never processed. */}
      {Object.values(notes).some(Boolean) && (
        <div className="notebar" style={{
          position: "absolute", left: 12, bottom: 74, maxWidth: 460, zIndex: 5,
          background: "var(--surface, #fff)", border: "1px solid var(--line, #d7dde3)",
          borderRadius: 6, padding: "9px 11px", fontSize: 11.5, lineHeight: 1.5,
        }}>
          {Object.entries(notes).filter(([, v]) => v).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 4 }}>
              <b className="mono" style={{ fontSize: 10.5 }}>{k}</b> — {v}
            </div>
          ))}
        </div>
      )}

      {/* The scrubber is ALWAYS mounted — it is the demo's primary control and
          it must not appear and disappear. It used to render only once its
          window had arrived, so on the real corpus it was absent for as long
          as the slowest call in the app took, and absent again after every
          navigation away and back. A control that comes and goes reads as a
          broken page; a disabled one reads as a loading page. */}
      <div className={`scrubber ${window_ ? "" : "scrubber-waiting"}`}>
        <button
          className="play"
          disabled={!window_}
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <span className="clock">
          {clockMs ? fmtDate(new Date(clockMs).toISOString(), true) : "—"}
        </span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.001}
          value={t}
          disabled={!window_}
          onChange={(e) => {
            setPlaying(false);
            setT(+e.target.value);
          }}
        />
        <span className="muted" style={{ fontSize: 11.5 }}>
          {!window_
            ? (windowError || "loading time window…")
            : movingCount > 0
              ? `${movingCount} vessel${movingCount === 1 ? "" : "s"} on AIS`
              : "no AIS tracks in this window"}
        </span>
      </div>

      {selected && (
        <div className="drawer">
          <div className="drawer-head">
            <div className="eyebrow">Vessel</div>
            <button className="iconbtn" onClick={() => setSelected(null)}>
              ×
            </button>
          </div>
          <div className="drawer-body">
            <VesselPanel
              vesselId={selected}
              onOpenGraph={(id) => nav(`/graph?seed=${encodeURIComponent(id)}`)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function addAoi(m) {
  m.addSource("aoi", {
    type: "geojson",
    data: {
      type: "Feature",
      geometry: {
        type: "Polygon",
        coordinates: [[
          [AOI.lonMin, AOI.latMin], [AOI.lonMax, AOI.latMin],
          [AOI.lonMax, AOI.latMax], [AOI.lonMin, AOI.latMax],
          [AOI.lonMin, AOI.latMin],
        ]],
      },
    },
  });
  m.addLayer({
    id: "aoi-line", type: "line", source: "aoi",
    paint: { "line-color": "#1a5fb4", "line-width": 1.2, "line-dasharray": [3, 2], "line-opacity": 0.5 },
  });
}

// ---- interpolation: a vessel's [lon,lat] at epoch-seconds t, or null if the
//      clock is outside its track's time span (it hasn't started / has ended). ----
function posAt(points, tSec) {
  if (!points || points.length === 0 || tSec == null) return null;
  if (tSec < points[0][2] || tSec > points[points.length - 1][2]) return null;
  let lo = 0, hi = points.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid][2] <= tSec) lo = mid;
    else hi = mid;
  }
  const a = points[lo], b = points[hi];
  const span = b[2] - a[2] || 1;
  const f = (tSec - a[2]) / span;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

function renderVessels(m, tracks, clockSec, on, onSelect) {
  const feats = [];
  for (const tr of tracks) {
    const pos = posAt(tr.points, clockSec);
    if (!pos) continue;
    feats.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: pos },
      properties: { vessel_id: tr.vessel_id, synthetic: tr.is_synthetic ? 1 : 0 },
    });
  }
  upsertCircleLayer(m, "vessels", feats, "#1a5fb4", on, onSelect, 5);
}

function renderStatic(m, data, tracks, visible, onSelect) {
  // events (persistent context — not filtered by the clock, so nothing blinks)
  for (const kind of ["encounter", "loitering", "port_visit", "gap"]) {
    const feats = data.events
      .filter((e) => e.kind === kind && e.lat != null && e.lon != null)
      .map((e) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [e.lon, e.lat] },
        properties: {
          vessel_id: e.vessel_id || "",
          synthetic: e.is_synthetic ? 1 : 0,
          label: `${kind}${e.place ? " · " + e.place : ""}`,
        },
      }));
    upsertCircleLayer(m, `ev-${kind}`, feats, EVENT_COLOR[kind], visible[kind], onSelect, 4);
  }

  // track polylines
  const lineFeats = tracks.map((tr) => ({
    type: "Feature",
    geometry: { type: "LineString", coordinates: tr.points.map((p) => [p[0], p[1]]) },
    properties: { synthetic: tr.is_synthetic ? 1 : 0 },
  }));
  upsertLineLayer(m, "tracklines", lineFeats, "#7aa8dd", visible.tracks);

  // ports
  const portFeats = data.ports
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { vessel_id: "", synthetic: p.is_synthetic ? 1 : 0, label: `Port · ${p.name || p.id}` },
    }));
  upsertCircleLayer(m, "ports", portFeats, "#55636f", visible.ports, onSelect, 4);

  // alert markers (at the alert subject's first known event location)
  const alertPts = [];
  for (const a of data.alerts) {
    const ev = data.events.find((e) => e.vessel_id === a.subject && e.lat != null);
    if (ev) {
      alertPts.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [ev.lon, ev.lat] },
        properties: {
          vessel_id: a.subject, synthetic: a.is_synthetic ? 1 : 0,
          label: `⚑ ${a.anomaly_type} · ${a.subject_name || ""}`,
        },
      });
    }
  }
  upsertCircleLayer(m, "alerts", alertPts, "#b0221b", visible.alerts, onSelect, 7, true);

  // SAR radar contacts. A contact with no matched MMSI is drawn hollow — it is
  // the SHAPE of a dark vessel, not a dark vessel. Asserting darkness needs
  // demonstrated AIS reception at the position (ADR-005, CLAUDE.md §6), so the
  // map shows the contact and withholds the word.
  const detFeats = data.detections
    .filter((d) => d.lat != null && d.lon != null)
    .map((d) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [d.lon, d.lat] },
      properties: {
        vessel_id: "",
        synthetic: d.is_synthetic ? 1 : 0,
        unmatched: d.matched_mmsi ? 0 : 1,
        label: `SAR contact${d.length_m ? ` · ${Math.round(d.length_m)} m` : ""}` +
          ` · ${d.matched_mmsi ? `matched to MMSI ${d.matched_mmsi}` : "no AIS track associated"}`,
      },
    }));
  upsertDetectionLayer(m, "detections", detFeats, visible.detections);

  // Sentinel-1 footprints
  const sceneFeats = data.scenes.map((s) => wktToFeature(s.footprint_wkt)).filter(Boolean);
  upsertPolyLayer(m, "scenes", sceneFeats, "#6039c4", visible.scenes);
}

// Event density — one graduated circle per H3 cell, counted over the WHOLE
// corpus rather than over the page the dot layers requested. This is the layer
// that makes 24,153 real loitering events visible at all: as individual dots
// they were both capped and unreadable.
function renderDensity(m, cells, on) {
  const feats = cells
    .filter((c) => c.lat != null && c.lon != null)
    .map((c) => {
      const total = c.real + c.synthetic;
      const kinds = Object.entries(c.by_kind)
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => `${n} ${k.replace("_", " ")}`)
        .join(", ");
      return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [c.lon, c.lat] },
        properties: { vessel_id: "", total, label: `${total} events · ${kinds}` },
      };
    });
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource("density");
  if (src) src.setData(fc);
  else {
    m.addSource("density", { type: "geojson", data: fc });
    m.addLayer({
      id: "density",
      type: "circle",
      source: "density",
      paint: {
        // Square-root scaling on the radius, so the MARK AREA is proportional
        // to the count. Scaling the radius linearly would make a 100-event cell
        // look ten times heavier than a 10-event one instead of ten times
        // bigger, which overstates the hot cells badly.
        "circle-radius": [
          "interpolate", ["linear"], ["sqrt", ["get", "total"]],
          1, 4, 5, 10, 12, 18, 30, 30,
        ],
        "circle-color": "#5b3fa8",
        "circle-opacity": 0.28,
        "circle-stroke-width": 1,
        "circle-stroke-color": "#5b3fa8",
        "circle-stroke-opacity": 0.55,
      },
    }, m.getLayer("ev-encounter") ? "ev-encounter" : undefined);
    m.on("click", "density", (e) => {
      new maplibregl.Popup({ closeButton: false, offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${e.features[0].properties.label}</b>`)
        .addTo(m);
    });
  }
  if (m.getLayer("density"))
    m.setLayoutProperty("density", "visibility", on ? "visible" : "none");
}

// ---- coastal radar (ADR-028) ---------------------------------------------
//
// Three layers, and the ORDER they are added in is load-bearing: coverage
// underneath everything (it is context), then tracks, then contacts on top,
// because a contact hidden under a mat of track lines is a contact nobody
// clicks.
//
// The coverage ring is drawn as TWO rings, not one. A radar's reach depends on
// how tall the target is — the horizon to a 250 m tanker is roughly twice the
// horizon to a 15 m skiff — so a single circle would either promise skiff
// coverage the station does not have or hide tanker coverage it does. The inner
// solid ring is what it holds for a small target; the dashed outer ring is what
// it holds for a large one. Everything between them is "big ships only", which
// is exactly the kind of thing an operator must be able to see before believing
// a silence.
function renderRadar(m, data, radarTracks, visible) {
  const stations = data.radarStations || [];

  const ringFeats = [];
  for (const s of stations) {
    if (s.lat == null || s.lon == null) continue;
    ringFeats.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [circleRing(s.lat, s.lon, s.range_large_km)] },
      properties: { band: "large", station: s.station_id },
    });
    ringFeats.push({
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [circleRing(s.lat, s.lon, s.range_small_km)] },
      properties: { band: "small", station: s.station_id },
    });
  }
  upsertRingLayer(m, "radar-coverage", ringFeats, visible.radar_coverage);

  const stationFeats = stations
    .filter((s) => s.lat != null && s.lon != null)
    .map((s) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      properties: {
        vessel_id: "",
        label:
          `Radar station ${s.station_id} · ${s.name} (SYNTHETIC) — holds a ` +
          `small craft to ${Math.round(s.range_small_km)} km, a large ship to ` +
          `${Math.round(s.range_large_km)} km`,
      },
    }));
  upsertCircleLayer(m, "radar-stations", stationFeats, "#0b6e75",
                    visible.radar_coverage, () => {}, 4);

  const trackFeats = (radarTracks || []).map((tr) => ({
    type: "Feature",
    geometry: { type: "LineString", coordinates: tr.points.map((p) => [p[0], p[1]]) },
    properties: { station: tr.station_id },
  }));
  upsertLineLayer(m, "radar-tracklines", trackFeats, "#3aa0a8", visible.radar_tracks);

  // Contacts. Survivors are filled; suppressed verdicts are hollow and half
  // opacity, present but visibly not a finding — the same "shape of a thing
  // versus the thing" treatment the SAR layer uses for unmatched detections.
  const contacts = (data.radarContacts || []).filter(
    (c) => c.lat != null && c.lon != null);
  const contactFeats = contacts.map((c) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [c.lon, c.lat] },
    properties: {
      vessel_id: "",
      candidate: c.status === "dark_candidate" ? 1 : 0,
      label:
        (c.status === "dark_candidate"
          ? "DARK CONTACT (synthetic)"
          : `suppressed — ${String(c.status || "").replace("suppressed_", "").replace(/_/g, " ")} (synthetic)`) +
        ` · ${c.radar_track_id || ""}` +
        (c.length_m ? ` · ≈${Math.round(c.length_m)} m` : "") +
        (c.dark_minutes ? ` · ${Math.round(c.dark_minutes)} min unexplained` : "") +
        (c.went_dark_at
          ? ` · last explained by MMSI ${c.mmsi} at ${c.went_dark_at}`
          : ""),
    },
  }));
  upsertContactLayer(m, "radar-contacts", contactFeats, visible.radar_contacts);

  // The shutdown segment: a line from where the transponder was last heard to
  // where radar was still holding her. Drawn only for contacts that carry the
  // pair, which means only for contacts that really were correlated first —
  // this stroke is an assertion and it must not appear on a target nothing ever
  // identified.
  const segFeats = contacts
    .filter((c) => c.went_dark_lat != null && c.went_dark_lon != null)
    .map((c) => ({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [[c.went_dark_lon, c.went_dark_lat], [c.lon, c.lat]],
      },
      properties: { mmsi: c.mmsi },
    }));
  upsertWentDarkLayer(m, "radar-wentdark", segFeats, visible.radar_contacts);
}

//: A closed ring of [lon,lat] approximating a circle of `km` around a point.
//: Flat-earth is fine at this scale — the largest ring here is ~90 km and the
//: error is well under the width of the stroke.
function circleRing(lat, lon, km, steps = 72) {
  const out = [];
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
  for (let i = 0; i <= steps; i++) {
    const a = (2 * Math.PI * i) / steps;
    out.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return out;
}

function upsertRingLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    // Inserted BENEATH the AOI outline, which is the first layer this app adds
    // after the basemap. Coverage is context: a ring painted over the contact
    // it explains is a ring in the way. Layer order in MapLibre is creation
    // order unless a beforeId says otherwise, and these are created last.
    const under = m.getLayer("aoi-line") ? "aoi-line" : undefined;
    m.addLayer({
      id: id + "-fill", type: "fill", source: id,
      paint: {
        "fill-color": "#0b6e75",
        "fill-opacity": ["case", ["==", ["get", "band"], "small"], 0.07, 0.035],
      },
    }, under);
    m.addLayer({
      id: id + "-line", type: "line", source: id,
      paint: {
        "line-color": "#0b6e75",
        "line-width": 0.9,
        "line-opacity": ["case", ["==", ["get", "band"], "small"], 0.55, 0.35],
        "line-dasharray": ["case", ["==", ["get", "band"], "small"], ["literal", [1, 0]], ["literal", [3, 2]]],
      },
    }, under);
  }
  for (const suff of ["-fill", "-line"]) {
    if (m.getLayer(id + suff))
      m.setLayoutProperty(id + suff, "visibility", on ? "visible" : "none");
  }
}

function upsertContactLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "circle", source: id,
      paint: {
        "circle-radius": ["case", ["==", ["get", "candidate"], 1], 8, 4],
        "circle-color": ["case", ["==", ["get", "candidate"], 1], "#d33682", "rgba(0,0,0,0)"],
        "circle-opacity": 0.85,
        "circle-stroke-width": 2,
        "circle-stroke-color": "#d33682",
        "circle-stroke-opacity": ["case", ["==", ["get", "candidate"], 1], 1, 0.45],
      },
    });
    m.on("click", id, (e) => {
      new maplibregl.Popup({ closeButton: false, offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${e.features[0].properties.label}</b>`)
        .addTo(m);
    });
    m.on("mouseenter", id, () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", id, () => (m.getCanvas().style.cursor = ""));
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertWentDarkLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "line", source: id,
      paint: {
        "line-color": "#b0221b", "line-width": 2,
        "line-opacity": 0.8, "line-dasharray": [2, 1.5],
      },
    });
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertDetectionLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "circle", source: id,
      paint: {
        "circle-radius": 6,
        // Hollow when no AIS track was associated. The visual difference is the
        // whole point of the layer, so it is encoded in the mark rather than in
        // a popup the operator has to open.
        "circle-color": ["case", ["==", ["get", "unmatched"], 1], "rgba(0,0,0,0)", "#00707a"],
        "circle-stroke-width": 2,
        "circle-stroke-color": "#00707a",
      },
    });
    m.on("click", id, (e) => {
      new maplibregl.Popup({ closeButton: false, offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${e.features[0].properties.label}</b>`)
        .addTo(m);
    });
    m.on("mouseenter", id, () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", id, () => (m.getCanvas().style.cursor = ""));
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertCircleLayer(m, id, feats, color, on, onSelect, radius = 5, star = false) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "circle", source: id,
      paint: {
        "circle-radius": star ? radius + 1 : radius,
        "circle-color": color,
        "circle-opacity": 0.85,
        "circle-stroke-width": star ? 2 : 1,
        "circle-stroke-color": "#fff",
      },
    });
    m.on("click", id, (e) => {
      const p = e.features[0].properties;
      if (p.label) {
        new maplibregl.Popup({ closeButton: false, offset: 10 })
          .setLngLat(e.lngLat)
          .setHTML(`<b>${p.label}</b>`)
          .addTo(m);
      }
      if (p.vessel_id) onSelect(p.vessel_id);
    });
    m.on("mouseenter", id, () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", id, () => (m.getCanvas().style.cursor = ""));
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertLineLayer(m, id, feats, color, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "line", source: id,
      paint: {
        "line-color": color,
        "line-width": 1, "line-opacity": 0.5,
      },
    });
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertPolyLayer(m, id, feats, color, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({ id: id + "-fill", type: "fill", source: id, paint: { "fill-color": color, "fill-opacity": 0.06 } });
    m.addLayer({ id: id + "-line", type: "line", source: id, paint: { "line-color": color, "line-width": 0.8, "line-opacity": 0.5 } });
  }
  for (const suff of ["-fill", "-line"]) {
    if (m.getLayer(id + suff)) m.setLayoutProperty(id + suff, "visibility", on ? "visible" : "none");
  }
}

function wktToFeature(wkt) {
  if (!wkt || !wkt.toUpperCase().includes("POLYGON")) return null;
  const mm = wkt.match(/\(\(([^)]+)\)\)/);
  if (!mm) return null;
  const coords = mm[1]
    .split(",")
    .map((pair) => pair.trim().split(/\s+/).map(Number))
    .filter((c) => c.length === 2 && c.every((n) => !Number.isNaN(n)));
  if (coords.length < 4) return null;
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: {} };
}
