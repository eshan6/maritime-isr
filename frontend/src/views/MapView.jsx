// The map — primary view. AOI framed on the Arabian Sea, toggleable layers, a
// time scrubber across the 8-week window with play/pause, and click-to-open the
// vessel entity panel. Built on MapLibre GL with a light CARTO raster basemap
// (no key); if tiles are blocked it degrades to a clean ocean canvas.
import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate } from "../lib/format.js";
import { VesselPanel } from "../components/VesselPanel.jsx";

// AOI v1 — Arabian Sea / Indian west coast (config.py AOI_V1).
const AOI = { lonMin: 60, latMin: 5, lonMax: 78, latMax: 25 };

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
  { id: "tracks", label: "Vessel tracks", color: "#1a5fb4" },
  { id: "positions", label: "Vessel positions", color: "#1a5fb4" },
  { id: "encounter", label: "Encounters", color: "#b0221b" },
  { id: "loitering", label: "Loitering", color: "#9a6300" },
  { id: "port_visit", label: "Port visits", color: "#1f7a4d" },
  { id: "gap", label: "AIS gaps", color: "#b0221b" },
  { id: "scenes", label: "Sentinel-1 footprints", color: "#6039c4" },
  { id: "ports", label: "Ports", color: "#55636f" },
  { id: "alerts", label: "Alert markers", color: "#b0221b" },
];

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
  const [data, setData] = useState({ events: [], ports: [], scenes: [], alerts: [], vessels: [] });
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

  // ---- load data ----
  useEffect(() => {
    Promise.all([
      api.events({ limit: 20000 }),
      api.ports(),
      api.scenes(),
      api.alerts(),
      api.stats(),
    ]).then(([ev, ports, scenes, alerts, stats]) => {
      setData({
        events: ev.items,
        ports: ports.items,
        scenes: scenes.items,
        alerts: alerts.items,
      });
      const w = stats.corpus_window;
      if (w.start && w.end) {
        setWindow({ start: +new Date(w.start), end: +new Date(w.end) });
      }
    });
  }, []);

  const clock = useMemo(() => {
    if (!window_) return null;
    return window_.start + t * (window_.end - window_.start);
  }, [window_, t]);

  // ---- render sources/layers when data + map ready ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderLayers(map.current, data, visible, clock, (id) => setSelected(id));
  }, [ready, data, visible, clock]);

  // ---- play/pause the scrubber ----
  useEffect(() => {
    if (!playing) return;
    const h = setInterval(() => {
      setT((x) => {
        const nx = x + 0.008;
        return nx >= 1 ? 0 : nx;
      });
    }, 60);
    return () => clearInterval(h);
  }, [playing]);

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
            {clock ? fmtDate(new Date(clock).toISOString(), true) : "—"}
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
            {fmtDate(new Date(window_.start).toISOString())} –{" "}
            {fmtDate(new Date(window_.end).toISOString())}
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

const EVENT_COLOR = { encounter: "#b0221b", loitering: "#9a6300", port_visit: "#1f7a4d", gap: "#b0221b" };

// Build/refresh all the GeoJSON layers. Called on every data/visibility/time
// change; layers are added once then updated in place.
function renderLayers(m, data, visible, clock, onSelect) {
  const eventsBefore = clock
    ? data.events.filter((e) => !e.start_time || +new Date(e.start_time) <= clock)
    : data.events;

  // ---- events (points) ----
  for (const kind of ["encounter", "loitering", "port_visit", "gap"]) {
    const feats = eventsBefore
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
    upsertCircleLayer(m, `ev-${kind}`, feats, EVENT_COLOR[kind], visible[kind], onSelect);
  }

  // ---- ports ----
  const portFeats = data.ports
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { vessel_id: "", synthetic: p.is_synthetic ? 1 : 0, label: `Port · ${p.name || p.id}` },
    }));
  upsertCircleLayer(m, "ports", portFeats, "#55636f", visible.ports, onSelect, 4);

  // ---- alert markers ----
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

  // ---- vessel positions (from event points as a proxy for "where vessels are") ----
  const posFeats = eventsBefore
    .filter((e) => e.vessel_id && e.lat != null && e.lon != null)
    .map((e) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [e.lon, e.lat] },
      properties: { vessel_id: e.vessel_id, synthetic: e.is_synthetic ? 1 : 0, label: e.mmsi || "vessel" },
    }));
  upsertCircleLayer(m, "positions", posFeats, "#1a5fb4", visible.positions, onSelect, 3.5);

  // ---- scenes (footprint outlines from WKT bbox) ----
  const sceneFeats = data.scenes
    .map((s) => wktToFeature(s.footprint_wkt))
    .filter(Boolean);
  upsertPolyLayer(m, "scenes", sceneFeats, "#6039c4", visible.scenes);
}

function upsertCircleLayer(m, id, feats, color, on, onSelect, radius = 5, star = false) {
  const src = m.getSource(id);
  const fc = { type: "FeatureCollection", features: feats };
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "circle", source: id,
      paint: {
        "circle-radius": star ? radius + 1 : radius,
        "circle-color": ["case", ["==", ["get", "synthetic"], 1], "#6039c4", color],
        "circle-opacity": 0.82,
        "circle-stroke-width": star ? 2 : 1,
        "circle-stroke-color": "#fff",
      },
    });
    m.on("click", id, (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup({ closeButton: false, offset: 10 })
        .setLngLat(e.lngLat)
        .setHTML(`<b>${p.label}</b>${p.synthetic == 1 ? ' <span style="color:#6039c4;font-size:11px">SCENARIO</span>' : ""}`)
        .addTo(m);
      if (p.vessel_id) onSelect(p.vessel_id);
    });
    m.on("mouseenter", id, () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", id, () => (m.getCanvas().style.cursor = ""));
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertPolyLayer(m, id, feats, color, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id: id + "-fill", type: "fill", source: id,
      paint: { "fill-color": color, "fill-opacity": 0.06 },
    });
    m.addLayer({
      id: id + "-line", type: "line", source: id,
      paint: { "line-color": color, "line-width": 0.8, "line-opacity": 0.5 },
    });
  }
  for (const suff of ["-fill", "-line"]) {
    if (m.getLayer(id + suff)) m.setLayoutProperty(id + suff, "visibility", on ? "visible" : "none");
  }
}

// Parse a POLYGON WKT into a GeoJSON polygon feature (best-effort; footprints
// are simple polygons in EPSG:4326).
function wktToFeature(wkt) {
  if (!wkt || !wkt.toUpperCase().includes("POLYGON")) return null;
  const m = wkt.match(/\(\(([^)]+)\)\)/);
  if (!m) return null;
  const coords = m[1]
    .split(",")
    .map((pair) => pair.trim().split(/\s+/).map(Number))
    .filter((c) => c.length === 2 && c.every((n) => !Number.isNaN(n)));
  if (coords.length < 4) return null;
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: {} };
}
