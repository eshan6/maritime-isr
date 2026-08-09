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
  { id: "encounter", label: "Encounters", color: "#b0221b" },
  { id: "loitering", label: "Loitering", color: "#9a6300" },
  { id: "port_visit", label: "Port visits", color: "#1f7a4d" },
  { id: "gap", label: "AIS gaps", color: "#b0221b" },
  { id: "scenes", label: "Sentinel-1 footprints", color: "#6039c4" },
  { id: "ports", label: "Ports", color: "#55636f" },
  { id: "alerts", label: "Alert markers", color: "#b0221b" },
];

const EVENT_COLOR = { encounter: "#b0221b", loitering: "#9a6300", port_visit: "#1f7a4d", gap: "#b0221b" };

export function MapView() {
  const mapEl = useRef(null);
  const map = useRef(null);
  const nav = useNavigate();
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState(null);
  const [visible, setVisible] = useState({
    positions: true, tracks: false, encounter: true, loitering: true,
    port_visit: true, gap: true, scenes: false, ports: true, alerts: true,
  });
  const [data, setData] = useState({ events: [], ports: [], scenes: [], alerts: [] });
  const [tracks, setTracks] = useState([]);
  const [window_, setWindow] = useState(null); // {start,end} epoch ms
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
    api.events({ limit: 4000 }).then((r) => set({ events: r.items })).catch(() => {});
    api.ports().then((r) => set({ ports: r.items })).catch(() => {});
    api.scenes().then((r) => set({ scenes: r.items })).catch(() => {});
    api.alerts().then((r) => set({ alerts: r.items })).catch(() => {});
    api.tracks({ max_points: 160 }).then((r) => live && setTracks(r.items || [])).catch(() => {});
    api.stats().then((s) => {
      const w = s.corpus_window || {};
      if (live && w.start && w.end) {
        setWindow({ start: +new Date(w.start), end: +new Date(w.end) });
      }
    }).catch(() => {});
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
  }, [ready, data, tracks, visible]);

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

      {window_ && (
        <div className="scrubber">
          <button className="play" onClick={() => setPlaying((p) => !p)}>
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
            onChange={(e) => {
              setPlaying(false);
              setT(+e.target.value);
            }}
          />
          <span className="muted" style={{ fontSize: 11.5 }}>
            {movingCount > 0
              ? `${movingCount} vessel${movingCount === 1 ? "" : "s"} on AIS`
              : "no AIS tracks in this window"}
          </span>
        </div>
      )}

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

  // Sentinel-1 footprints
  const sceneFeats = data.scenes.map((s) => wktToFeature(s.footprint_wkt)).filter(Boolean);
  upsertPolyLayer(m, "scenes", sceneFeats, "#6039c4", visible.scenes);
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
