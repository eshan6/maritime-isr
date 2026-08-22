// The map — primary view. AOI framed on the Arabian Sea, toggleable layers, and
// a time scrubber that animates vessels ALONG THEIR AIS TRACKS: at each clock
// tick every vessel is drawn at its interpolated position, so ships glide across
// the 8-week window rather than blinking on and off. Events (encounters,
// loitering, port visits, gaps) are persistent context markers, not the time
// signal. Click a vessel to open its entity panel.
//
// Where no AIS tracks exist for the window, nothing moves and the events and
// sanctioned-vessel markers still render.
//
// **The scrubber plays the AIS window, not the corpus window.** Those are
// different spans and treating them as one is what made the player look dead:
// the laptop corpus reaches back to 2012 on a thin tail of real GFW identity
// and loitering records, while every AIS position sits in the eight-week
// narrative at the far end. Scrubbing 2012→2026 put 99% of the bar in years
// holding no positions, so the clock ticked and not one vessel ever moved.
// `/corpus-window` now returns both spans; we play `motion_*` and disclose the
// rest.
import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate } from "../lib/format.js";
import { VesselPanel } from "../components/VesselPanel.jsx";
import { ZonePanel } from "../components/ZonePanel.jsx";

// AOI v1 — Arabian Sea / Indian west coast (config.py AOI_V1).
const AOI = { lonMin: 60, latMin: 5, lonMax: 78, latMax: 25 };

//: Wall-clock seconds the animation spends on one day of corpus time. Slow
//: enough to follow a vessel's movement across a day rather than watch it
//: flicker past.
const SECONDS_PER_DAY = 7;

//: …but never more than this end to end. A fixed seconds-per-day is the right
//: pace for a window measured in weeks and an absurd one for a window measured
//: in years: at 7 s/day the 5,317-day corpus window took **ten hours** to play
//: through, which is indistinguishable from a broken button. The cap is what
//: keeps the fallback case (no AIS at all, so the scrubber spans the whole
//: corpus) honest — the clock still crosses the window while somebody watches.
const MAX_PLAYTHROUGH_S = 120;

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

const VESSEL_COLOR = "#1a5fb4";
const ALERT_COLOR = "#b0221b";

// **AIS gaps no longer share the encounter red.** Three layers were painted
// `#b0221b` — encounters, gaps and alert markers — so the key showed three
// identical swatches against three unrelated meanings and the map drew them as
// the same dot. Red stays with risk (alerts) and with the encounter, which is
// the behaviour a transfer looks like.
//
// A gap gets near-black, because what a gap IS is a silence: the broadcast
// stopped. That is the literal fact and the colour says only that much. It is
// deliberately not a statement that the silence was *intentional* — asserting
// that needs demonstrated receiver coverage at the position (ADR-005,
// CLAUDE.md §6), which mostly does not exist here.
const EVENT_COLOR = {
  encounter: "#b0221b",
  loitering: "#9a6300",
  port_visit: "#1f7a4d",
  gap: "#1f2933",
};

// The key, GROUPED — and the grouping is the whole repair. It was a flat run of
// fourteen checkboxes plus ten more under one heading: twenty-four toggles in a
// scrolling column, which is an inventory rather than a key. Nothing in it said
// that four of those layers are history and one of them is now, so a pin that
// has never moved and a ship at its position this instant read as the same kind
// of mark.
//
// The four groups are the question an operator actually asks of a mark ("what
// kind of thing is this?"), and they are the same four-way split the marks
// themselves now carry in weight — see MARK_STYLE.
const LAYER_GROUPS = [
  {
    id: "live",
    title: "Live traffic",
    hint: "Moves with the timeline. Drawn where the vessel was at the time on the clock.",
    layers: [
      { id: "positions", label: "Vessels", color: VESSEL_COLOR },
      { id: "tracks", label: "Vessel tracks", color: "#7aa8dd" },
    ],
  },
  {
    id: "history",
    title: "Past behaviour",
    // The sentence this map was missing. Events are deliberately not filtered
    // by the clock, which is a good decision that was completely invisible: the
    // pins sat still through an entire playthrough and read as vessels that
    // had stopped, rather than as places where something once happened.
    hint: "Fixed pins marking where something happened. These do not move with the timeline.",
    layers: [
      { id: "density", label: "Event density (whole corpus)", color: "#5b3fa8" },
      { id: "encounter", label: "Encounters", color: EVENT_COLOR.encounter },
      { id: "loitering", label: "Loitering", color: EVENT_COLOR.loitering },
      { id: "port_visit", label: "Port visits", color: EVENT_COLOR.port_visit },
      { id: "gap", label: "AIS gaps", color: EVENT_COLOR.gap },
      { id: "alerts", label: "Alert markers", color: ALERT_COLOR },
    ],
  },
  {
    id: "sensors",
    title: "Satellite & radar",
    hint: "What a sensor saw, whether or not the vessel was broadcasting. "
      + "A hollow mark has no AIS track associated with it.",
    // Coastal radar (ADR-028). No live radar feed stands behind these; the Radar
    // view carries that disclosure in full, so the layer names here stay plain
    // rather than repeating a caveat three times in a checkbox list.
    layers: [
      { id: "detections", label: "SAR radar contacts", color: "#00707a" },
      { id: "scenes", label: "Sentinel-1 footprints", color: "#6039c4" },
      { id: "radar_coverage", label: "Radar coverage", color: "#0b6e75" },
      { id: "radar_tracks", label: "Radar tracks", color: "#3aa0a8" },
      { id: "radar_contacts", label: "Radar dark contacts", color: "#d33682" },
    ],
  },
];

// The maritime zone layer (ADR-030), toggled INDEPENDENTLY by kind — a
// watchkeeper needs to see boundaries without losing the traffic underneath
// them, and "zones on/off" is not that. Ordered back to front: big statutory
// areas are washes at the back, small facilities are outlines at the front.
const ZONE_LAYERS = [
  { id: "eez", label: "Exclusive economic zone", color: "#3b6ea5" },
  { id: "contiguous_zone", label: "Contiguous zone", color: "#4d7fb8" },
  { id: "territorial_sea", label: "Territorial sea", color: "#5f92cb" },
  { id: "imbl", label: "Maritime boundary line", color: "#8a2f2f" },
  { id: "shipping_lane", label: "Shipping lanes", color: "#5b7a52" },
  { id: "sensitive_area", label: "Sensitive areas", color: "#8a5a2f" },
  { id: "port_limit", label: "Port areas", color: "#3f6f7a" },
  { id: "anchorage", label: "Anchorages", color: "#6a5aa0" },
  { id: "oil_terminal", label: "Terminals / SPMs", color: "#a0526a" },
  { id: "geofence", label: "My drawn areas", color: "#c2410c" },
];

//: Ports sit in the geography group, not with the behaviour pins. A port is a
//: fixed place on the coast — the same class of thing as an anchorage or a
//: terminal — and grouping it with "port visits" put a permanent feature next
//: to a dated event purely because the two share a word.
const GEOGRAPHY_EXTRA = [{ id: "ports", label: "Ports", color: "#55636f" }];

// How heavily each family of mark is drawn. **This is the clutter fix that is
// not about layer count.** Eleven layers were circles between radius 4 and 8
// with the same white stroke, so colour was the only thing separating a vessel
// at her position this instant from a pin dropped two years ago — and three of
// those colours collided.
//
// MapLibre's circle layer gives no silhouette control without sprite images, so
// the hierarchy is carried by weight instead, which is the axis that actually
// governs what the eye reaches first:
//
//   * **live** — big, saturated, thick white halo. Reads as an object.
//   * **history** — small, translucent, hairline stroke. Reads as stipple:
//     present, countable, and clearly not in the foreground.
//   * **flag** — a ring, not a disc. An alert is an annotation ABOUT a position,
//     so it is drawn around one rather than as another thing sitting at it.
//
// Sensor marks keep their own filled/hollow encoding (that distinction carries
// meaning and is not ours to flatten) and are merely nudged down in weight.
const MARK_STYLE = {
  live: { radius: 6, opacity: 0.95, strokeWidth: 2.5, strokeColor: "#ffffff" },
  history: { radius: 3.5, opacity: 0.55, strokeWidth: 0.6, strokeColor: "#ffffff" },
  //: Fixed installations — ports, radar stations. Permanent features of the
  //: coast rather than things that happened, so they sit between the two: not
  //: as loud as a vessel, not as faint as one pin among thousands.
  context: { radius: 4, opacity: 0.7, strokeWidth: 0.8, strokeColor: "#ffffff" },
  //: A ring with nothing in the middle. `opacity` here is the FILL's, so zero
  //: is what makes it an annotation drawn around a position instead of a
  //: fourteenth kind of dot sitting on one.
  flag: { radius: 9, opacity: 0, strokeWidth: 3, strokeColor: ALERT_COLOR },
};

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
  // **Eighteen of twenty-four layers used to be on at first paint.** Every one
  // of them had been defaulted on for a real reason, each recorded in its own
  // session — "the footprints were the one unambiguously real thing and they
  // were hidden", "a contact without its coverage ring invites the
  // out-of-coverage misreading" — and nobody ever added the reasons up. The
  // result was a map that opened with everything shouting, which is the same
  // as opening with nothing legible.
  //
  // The opening set is now five: where the ships are, how much has happened
  // where (density, which is strictly better than the four individual event
  // layers it summarises), what has been flagged, the real satellite coverage,
  // and the operator's own drawn areas. Everything else is one click away in
  // its group, and the groups say what they hold.
  const [visible, setVisible] = useState({
    positions: true, tracks: false, density: true,
    encounter: false, loitering: false, port_visit: false, gap: false,
    alerts: true,
    detections: false, scenes: true, ports: false,
    radar_coverage: false, radar_tracks: false, radar_contacts: false,
    z_eez: false, z_contiguous_zone: false, z_territorial_sea: false,
    z_imbl: false, z_shipping_lane: false, z_sensitive_area: false,
    z_port_limit: false, z_anchorage: false, z_oil_terminal: false,
    z_geofence: true,
  });
  // Which key groups are expanded. `live` and `history` open, because those are
  // the two an operator reads the map through; the other two are available
  // without being a wall of text on arrival.
  const [openGroups, setOpenGroups] = useState({
    live: true, history: true, sensors: false, geography: false,
  });
  const [data, setData] = useState({
    events: [], ports: [], scenes: [], alerts: [], density: [], detections: [],
    radarStations: [], radarContacts: [], zones: [], missingKinds: [],
  });
  // What the API told us it could not fit in the events response, and what the
  // detections response is. Surfaced in the corner rather than swallowed.
  const [notes, setNotes] = useState({});
  const [tracks, setTracks] = useState([]);
  const [radarTracks, setRadarTracks] = useState([]);
  const [selectedZone, setSelectedZone] = useState(null);
  const [drawing, setDrawing] = useState(null);   // null | {points: [[lon,lat]]}
  const [zoneNote, setZoneNote] = useState(null);
  // {start, end} epoch ms — the span the scrubber PLAYS, which is not
  // necessarily the span the corpus covers. `playsWholeCorpus` says whether the
  // two coincide, so the status line can admit it when they do not.
  const [window_, setWindow] = useState(null);
  const [windowError, setWindowError] = useState(null);
  const [t, setT] = useState(1); // 0..1 across the window
  const [playing, setPlaying] = useState(false);
  // Has the operator moved the clock themselves? Until they have, the playhead
  // is ours to park somewhere useful (see the parking effect below); once they
  // have, it is theirs and we never move it under them.
  const [scrubbed, setScrubbed] = useState(false);

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
    // behind `/tracks`, measured at 3.06s against the working corpus (the
    // slowest call here by 40x). The scrubber was hidden until it landed, so
    // the demo's primary control arrived seconds after everything else and
    // did it again on every navigation back. Requesting it first costs
    // nothing and puts it on screen immediately; `api.corpusWindow` is
    // session-cached, so a return visit does not even reach the network.
    api.corpusWindow().then((w) => {
      if (!live) return;
      // Play the AIS window when there is one, and fall back to the corpus
      // window only when there is not — with no positions landed nothing can
      // move anyway, and a scrubber locked to an empty span would be worse
      // than one that at least walks the clock over the events on screen.
      const play = w && w.motion_start && w.motion_end
        ? { start: w.motion_start, end: w.motion_end }
        : (w && w.start && w.end ? { start: w.start, end: w.end } : null);
      if (!play) {
        setWindowError("the corpus has no dated events to scrub through");
        return;
      }
      setWindow({
        start: +new Date(play.start),
        end: +new Date(play.end),
        playsWholeCorpus: play.start === w.start && play.end === w.end,
      });
      note("window", w.note);
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
    loadZones(set, (n) => live && setZoneNote(n));
    return () => { live = false; };
  }, []);

  // Reloaded on demand rather than only at mount, because drawing or deleting
  // an area has to change the map immediately — a geofence you cannot see is
  // a geofence you will draw again.
  function reloadZones() {
    loadZones((patch) => setData((d) => ({ ...d, ...patch })), setZoneNote);
  }

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
    renderZones(map.current, data.zones, visible, (z) => setSelectedZone(z));
  }, [ready, data, tracks, radarTracks, visible]);

  // ---- the draw tool ----
  useEffect(() => {
    if (!ready || !map.current) return;
    const m = map.current;
    if (!drawing) {
      renderDraft(m, null);
      m.getCanvas().style.cursor = "";
      return;
    }
    m.getCanvas().style.cursor = "crosshair";
    renderDraft(m, drawing.points);
    const onClick = (e) => {
      setDrawing((d) => ({ points: [...d.points, [e.lngLat.lng, e.lngLat.lat]] }));
    };
    m.on("click", onClick);
    return () => m.off("click", onClick);
  }, [ready, drawing]);

  // ---- moving vessels: interpolate each track to the clock and glide ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderVessels(map.current, tracks, clockSec, visible.positions, (id) => setSelected(id));
  }, [ready, tracks, clockSec, visible.positions]);

  // ---- park the playhead where the fleet actually is ----
  // The clock defaulted to the very END of the window, which is the one instant
  // in the whole span guaranteed to be nearly empty: a vessel is drawn only
  // while the clock sits inside her own track, and by definition almost every
  // track has ended by then. So the map opened on one dot — or, on the corpus
  // window, on none at all — and looked as dead as the player did.
  //
  // Park it instead on the busiest instant: the moment the most vessels are
  // simultaneously broadcasting. Only until the operator touches the scrubber,
  // after which the clock is theirs.
  useEffect(() => {
    if (scrubbed || !window_ || !tracks.length) return;
    const at = busiestInstant(tracks);
    if (at == null) return;
    const span = window_.end - window_.start;
    if (span <= 0) return;
    setT(Math.min(1, Math.max(0, (at * 1000 - window_.start) / span)));
  }, [scrubbed, window_, tracks]);

  // ---- play/pause ----
  // Playback runs at SECONDS_PER_DAY, derived from the window's real span, so
  // an 8-week corpus and a 6-month one advance at the same readable pace —
  // tying the step to a raw fraction instead made a long window blur past. The
  // MAX_PLAYTHROUGH_S cap catches the other end: a window of years at 7 s/day
  // is a progress bar nobody lives to see finish.
  useEffect(() => {
    if (!playing || !window_) return;
    const totalDays = Math.max(1, (window_.end - window_.start) / 86400000);
    const playthroughS = Math.min(SECONDS_PER_DAY * totalDays, MAX_PLAYTHROUGH_S);
    const tickMs = 100;
    const dt = tickMs / (playthroughS * 1000); // fraction of the window per tick
    const h = setInterval(() => {
      setT((x) => (x >= 1 ? 0 : Math.min(1, x + dt)));
    }, tickMs);
    return () => clearInterval(h);
  }, [playing, window_]);

  const movingCount = tracks.length;
  // How many of those are on screen at THIS instant — the number that tells the
  // operator whether the clock is somewhere with traffic. `movingCount` is the
  // corpus total and never changes, so on its own it cannot say that.
  const onScreen = useMemo(
    () => tracks.reduce((n, tr) => n + (posAt(tr.points, clockSec) ? 1 : 0), 0),
    [tracks, clockSec],
  );

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={mapEl} style={{ position: "absolute", inset: 0 }} />

      <div className="layerbox" style={{ maxHeight: "72vh", overflowY: "auto" }}>
        {LAYER_GROUPS.map((g) => (
          <LayerGroup
            key={g.id}
            title={g.title}
            hint={g.hint}
            open={!!openGroups[g.id]}
            onToggle={() => setOpenGroups((s) => ({ ...s, [g.id]: !s[g.id] }))}
            count={g.layers.filter((l) => visible[l.id]).length}
            total={g.layers.length}
          >
            {g.layers.map((l) => (
              <label className="layer-toggle" key={l.id}>
                <input
                  type="checkbox"
                  checked={!!visible[l.id]}
                  onChange={(e) =>
                    setVisible((v) => ({ ...v, [l.id]: e.target.checked }))}
                />
                <span className="layer-swatch" style={{ background: l.color }} />
                {l.label}
              </label>
            ))}
          </LayerGroup>
        ))}

        <LayerGroup
          title="Geography"
          hint="Fixed places and boundaries. Drawn as outlines under the traffic."
          open={!!openGroups.geography}
          onToggle={() => setOpenGroups((s) => ({ ...s, geography: !s.geography }))}
          count={GEOGRAPHY_EXTRA.filter((l) => visible[l.id]).length
                 + ZONE_LAYERS.filter((l) => visible[`z_${l.id}`]
                                             && !(data.missingKinds || []).includes(l.id)).length}
          total={GEOGRAPHY_EXTRA.length
                 + ZONE_LAYERS.filter((l) => !(data.missingKinds || []).includes(l.id)).length}
        >
          {GEOGRAPHY_EXTRA.map((l) => (
            <label className="layer-toggle" key={l.id}>
              <input
                type="checkbox"
                checked={!!visible[l.id]}
                onChange={(e) =>
                  setVisible((v) => ({ ...v, [l.id]: e.target.checked }))}
              />
              <span className="layer-swatch" style={{ background: l.color }} />
              {l.label}
            </label>
          ))}
          {ZONE_LAYERS.map((l) => {
            const missing = (data.missingKinds || []).includes(l.id);
            return (
              <label
                className="layer-toggle"
                key={l.id}
                title={missing
                  ? "Not loaded — a statutory limit this system will not derive. "
                    + "Load a published file with `maritime-isr ingest zones`."
                  : l.label}
                style={{ opacity: missing ? 0.42 : 1 }}
              >
                <input
                  type="checkbox"
                  disabled={missing}
                  checked={!missing && !!visible[`z_${l.id}`]}
                  onChange={(e) =>
                    setVisible((v) => ({ ...v, [`z_${l.id}`]: e.target.checked }))}
                />
                <span className="layer-swatch" style={{ background: l.color }} />
                {l.label}
                {missing && <span className="muted"> — not loaded</span>}
              </label>
            );
          })}
        </LayerGroup>

        {/* Draw. The requirement's "draw a box anywhere and I'll tell you who
            was in it" begins here, and the control stays in the layer box
            rather than floating, so the drawn area reads as one more layer
            rather than as a mode the map is stuck in. */}
        <div style={{ marginTop: 10, borderTop: "1px solid var(--line,#e3e8ec)",
                      paddingTop: 8 }}>
          {!drawing ? (
            <button className="btn btn-sm" onClick={() => setDrawing({ points: [] })}>
              Draw an area
            </button>
          ) : (
            <DrawControls
              points={drawing.points}
              onUndo={() => setDrawing((d) => ({ points: d.points.slice(0, -1) }))}
              onCancel={() => setDrawing(null)}
              onSave={async (name) => {
                const ring = [...drawing.points, drawing.points[0]];
                const r = await api.createGeofence({
                  name,
                  geometry: { type: "Polygon", coordinates: [ring] },
                });
                setDrawing(null);
                reloadZones();
                // Open it immediately: the point of drawing is the answer, and
                // making the operator hunt for their own box on the map first
                // is a step that exists only because it was easier to build.
                setSelectedZone({
                  zone_id: r.zone_id, name: r.name, kind: "geofence",
                  authority: "operator", method: "drawn by the operator",
                  confidence: 1.0, note: "",
                });
              }}
            />
          )}
        </div>
        {(data.missingKinds || []).length > 0 && (
          <div className="muted t-micro" style={{ marginTop: 8 }}>
            {zoneNote}
          </div>
        )}
      </div>

      {/* What the map is NOT showing, in the operator's line of sight. Every
          one of these used to be silent: a capped event query looked like an
          empty second half of the window, and an empty SAR layer looked like a
          clean scene rather than a scene we never processed. */}
      {Object.values(notes).some(Boolean) && (
        <div className="notebar map-notes">
          {Object.entries(notes).filter(([, v]) => v).map(([k, v]) => (
            <div key={k} style={{ marginBottom: 4 }}>
              <span className="note-key mono">{k}</span> — {v}
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
          onClick={() => { setScrubbed(true); setPlaying((p) => !p); }}
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
            setScrubbed(true);
            setT(+e.target.value);
          }}
        />
        <span className="muted t-meta">
          {!window_
            ? (windowError || "loading time window…")
            : movingCount > 0
              ? `${onScreen} of ${movingCount} vessel`
                + (movingCount === 1 ? "" : "s") + " moving"
                + (window_.playsWholeCorpus ? "" : " · AIS window")
              : "no AIS tracks in this window"}
        </span>
      </div>

      {selectedZone && (
        <div className="drawer">
          <div className="drawer-head">
            <div className="eyebrow">Area</div>
            <button className="iconbtn" onClick={() => setSelectedZone(null)}>
              ×
            </button>
          </div>
          <div className="drawer-body">
            <ZonePanel
              zone={selectedZone}
              onDelete={() => { setSelectedZone(null); reloadZones(); }}
            />
          </div>
        </div>
      )}

      {selected && !selectedZone && (
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

// One collapsible section of the key. The header carries `n of m` so a
// collapsed group still says whether anything inside it is drawn — collapsing
// is meant to reduce reading, not to hide state, and a closed group that gave
// no count would make "is the radar layer on?" unanswerable without opening it.
function LayerGroup({ title, hint, open, onToggle, count, total, children }) {
  return (
    <div className="layer-group">
      <button
        type="button"
        className="layer-group-head"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className={`layer-group-caret ${open ? "open" : ""}`}>▸</span>
        <span className="layer-group-title">{title}</span>
        <span className="layer-group-count muted">{count}/{total}</span>
      </button>
      {open && (
        <div className="layer-group-body">
          {hint && <div className="layer-group-hint muted">{hint}</div>}
          {children}
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

//: The epoch-second at which the most tracks are simultaneously live — a sweep
//: over track start/end events, so it is one sort rather than a scan per
//: candidate instant. Ties go to the earliest such moment.
function busiestInstant(tracks) {
  const marks = [];
  for (const tr of tracks) {
    const p = tr.points;
    if (!p || p.length < 2) continue;
    marks.push([p[0][2], 1], [p[p.length - 1][2], -1]);
  }
  if (!marks.length) return null;
  // Opens before closes at the same instant: a track that ends exactly as
  // another starts should not read as a moment when neither is present.
  marks.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  let live = 0, best = 0, at = marks[0][0];
  for (const [sec, delta] of marks) {
    live += delta;
    if (live > best) { best = live; at = sec; }
  }
  return at;
}

//: Where an alert marker belongs, and on what basis — or null if nothing on
//: this client can place it honestly.
//:
//: **The old rule was `data.events.find(e => e.vessel_id === a.subject)`**: the
//: first event of that vessel in the response, in whatever order the API
//: returned it. Events come back ordered by `start_time`, so in practice that
//: was the vessel's EARLIEST known event — a flag raised last week could be
//: pinned to a port call two months earlier, and the map asserted a position
//: the alert had never claimed.
//:
//: An alert carries a timestamp, and this client already interpolates vessel
//: positions to any instant. So:
//:
//:   1. the subject's own track, interpolated to the alert's timestamp — the
//:      only genuinely correct answer, and available whenever she has a track;
//:   2. failing that, her event NEAREST IN TIME to the alert rather than first
//:      in the list — still a proxy, and the label says so;
//:   3. failing both, nothing. A vessel with no track and no located event has
//:      no defensible position, and a marker dropped on the sea anyway is the
//:      map inventing evidence.
function alertPosition(alert, tracks, events) {
  const tSec = alert.ts ? Date.parse(alert.ts) / 1000 : null;
  if (tSec != null && !Number.isNaN(tSec)) {
    const tr = tracks.find((t) => t.vessel_id === alert.subject);
    const pos = tr && posAt(tr.points, tSec);
    if (pos) return { pos, basis: "her position at the time of the alert" };
  }
  let best = null, bestGap = Infinity;
  for (const e of events) {
    if (e.vessel_id !== alert.subject || e.lat == null || e.lon == null) continue;
    // With no alert timestamp to compare against, every candidate scores the
    // same and the first located event wins — the old behaviour, but now only
    // in the case where nothing better is knowable.
    const evSec = e.start_time ? Date.parse(e.start_time) / 1000 : null;
    const gap = (tSec == null || evSec == null || Number.isNaN(evSec))
      ? Infinity : Math.abs(evSec - tSec);
    if (gap < bestGap || best === null) { best = e; bestGap = gap; }
  }
  if (!best) return null;
  return {
    pos: [best.lon, best.lat],
    basis: `nearest known event (${best.kind.replace(/_/g, " ")})`,
  };
}

function renderVessels(m, tracks, clockSec, on, onSelect) {
  const feats = [];
  for (const tr of tracks) {
    const pos = posAt(tr.points, clockSec);
    if (!pos) continue;
    feats.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: pos },
      properties: { vessel_id: tr.vessel_id },
    });
  }
  upsertCircleLayer(m, "vessels", feats, VESSEL_COLOR, on, onSelect, MARK_STYLE.live);
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
          label: `${kind.replace(/_/g, " ")}${e.place ? " · " + e.place : ""}`,
        },
      }));
    upsertCircleLayer(m, `ev-${kind}`, feats, EVENT_COLOR[kind], visible[kind],
                      onSelect, MARK_STYLE.history);
  }

  // track polylines
  const lineFeats = tracks.map((tr) => ({
    type: "Feature",
    geometry: { type: "LineString", coordinates: tr.points.map((p) => [p[0], p[1]]) },
    properties: {},
  }));
  upsertLineLayer(m, "tracklines", lineFeats, "#7aa8dd", visible.tracks);

  // ports
  const portFeats = data.ports
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      properties: { vessel_id: "", label: `Port · ${p.name || p.id}` },
    }));
  upsertCircleLayer(m, "ports", portFeats, "#55636f", visible.ports, onSelect,
                    MARK_STYLE.context);

  // Alert markers. See `alertPosition` — the placement rule used to be "the
  // first event of this vessel we happen to hold", which is a position the
  // alert never claimed.
  const alertPts = [];
  for (const a of data.alerts) {
    const at = alertPosition(a, tracks, data.events);
    if (!at) continue;
    alertPts.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: at.pos },
      properties: {
        vessel_id: a.subject,
        label: `⚑ ${String(a.anomaly_type || "").replace(/_/g, " ")} · `
          + `${a.subject_name || ""} — ${at.basis}`,
      },
    });
  }
  upsertCircleLayer(m, "alerts", alertPts, ALERT_COLOR, visible.alerts, onSelect,
                    MARK_STYLE.flag);

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

// ---- the maritime zone layer (ADR-030) -----------------------------------

async function loadZones(set, setNote) {
  try {
    const r = await api.zones();
    set({ zones: r.items || [], missingKinds: r.missing_kinds || [] });
    setNote?.(r.note || null);
  } catch {
    set({ zones: [], missingKinds: [] });
  }
}

// One source and one pair of layers PER KIND, so each can be toggled on its
// own and each keeps its own colour. A single layer with data-driven paint
// would have been fewer lines and would have made independent toggling
// impossible — which is the requirement, not a nicety.
function renderZones(m, zones, visible, onSelect) {
  const byKind = new Map();
  for (const z of zones || []) {
    if (!byKind.has(z.kind)) byKind.set(z.kind, []);
    byKind.get(z.kind).push(z);
  }
  for (const layer of ZONE_LAYERS) {
    const on = !!visible[`z_${layer.id}`];
    const list = byKind.get(layer.id) || [];
    const feats = list.map((z) => ({
      type: "Feature",
      geometry: z.geometry,
      properties: {
        zone_id: z.zone_id, name: z.name, kind: z.kind,
        authority: z.authority, method: z.method, note: z.note,
        confidence: z.confidence,
      },
    }));
    // `is_line` comes from the API (it is a property of the KIND, and the
    // server owns that vocabulary); the constant is the fallback for a kind
    // the server has not told us about yet.
    const isLine = list.length ? !!list[0].is_line : layer.id === "imbl";
    upsertZoneLayer(m, `zone-${layer.id}`, feats, layer.color, on,
                    isLine, onSelect, zones);
  }
}

function upsertZoneLayer(m, id, feats, color, on, isLine, onSelect, zones) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    if (!isLine) {
      // A wash, not a fill. The boundaries have to be legible without hiding
      // the traffic under them, so the interior is barely tinted and the work
      // is done by the outline.
      m.addLayer({
        id: `${id}-fill`, type: "fill", source: id,
        paint: { "fill-color": color, "fill-opacity": 0.06 },
      });
    }
    m.addLayer({
      id: `${id}-line`, type: "line", source: id,
      paint: {
        "line-color": color,
        "line-width": isLine ? 2 : 1.2,
        "line-opacity": isLine ? 0.9 : 0.65,
        ...(isLine ? { "line-dasharray": [4, 2] } : {}),
      },
    });
    const clickable = isLine ? `${id}-line` : `${id}-fill`;
    m.on("click", clickable, (e) => {
      const p = e.features[0].properties;
      const z = (zones || []).find((x) => x.zone_id === p.zone_id);
      onSelect(z || {
        zone_id: p.zone_id, name: p.name, kind: p.kind,
        authority: p.authority, method: p.method, note: p.note,
        confidence: Number(p.confidence),
      });
    });
    m.on("mouseenter", clickable, () => (m.getCanvas().style.cursor = "pointer"));
    m.on("mouseleave", clickable, () => (m.getCanvas().style.cursor = ""));
  }
  for (const suff of ["-fill", "-line"]) {
    if (m.getLayer(id + suff))
      m.setLayoutProperty(id + suff, "visibility", on ? "visible" : "none");
  }
}

// The polygon under construction. Drawn as a line while it has fewer than
// three points and as a closed shape after, so the operator can see what they
// are about to save rather than discovering it on the next reload.
function renderDraft(m, points) {
  const feats = [];
  if (points && points.length >= 1) {
    feats.push({
      type: "Feature",
      geometry: points.length >= 3
        ? { type: "Polygon", coordinates: [[...points, points[0]]] }
        : { type: "LineString", coordinates: points.length >= 2 ? points
                                                                : [points[0], points[0]] },
      properties: {},
    });
    for (const p of points) {
      feats.push({ type: "Feature", geometry: { type: "Point", coordinates: p },
                   properties: {} });
    }
  }
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource("draft");
  if (src) src.setData(fc);
  else {
    m.addSource("draft", { type: "geojson", data: fc });
    m.addLayer({ id: "draft-fill", type: "fill", source: "draft",
                 filter: ["==", ["geometry-type"], "Polygon"],
                 paint: { "fill-color": "#c2410c", "fill-opacity": 0.12 } });
    m.addLayer({ id: "draft-line", type: "line", source: "draft",
                 filter: ["!=", ["geometry-type"], "Point"],
                 paint: { "line-color": "#c2410c", "line-width": 2,
                          "line-dasharray": [2, 1] } });
    m.addLayer({ id: "draft-pt", type: "circle", source: "draft",
                 filter: ["==", ["geometry-type"], "Point"],
                 paint: { "circle-radius": 4, "circle-color": "#c2410c",
                          "circle-stroke-width": 1.5,
                          "circle-stroke-color": "#fff" } });
  }
}

function DrawControls({ points, onUndo, onCancel, onSave }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const enough = points.length >= 3;
  return (
    <div className="t-meta">
      <div className="muted" style={{ marginBottom: 6 }}>
        Click the map to place corners. {points.length} placed
        {!enough && " — three or more makes an area"}.
      </div>
      <input
        className="input"
        placeholder="Name this area"
        value={name}
        onChange={(e) => setName(e.target.value)}
        style={{ width: "100%", marginBottom: 6, padding: "4px 6px" }}
      />
      {err && <div style={{ color: "var(--red)", marginBottom: 6 }}>{err}</div>}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button className="btn btn-sm btn-primary"
                disabled={!enough || !name.trim() || busy}
                onClick={async () => {
                  setBusy(true); setErr(null);
                  try { await onSave(name.trim()); }
                  catch (e) { setErr(String(e.message || e)); }
                  finally { setBusy(false); }
                }}>
          {busy ? "Saving…" : "Save and ask"}
        </button>
        <button className="btn btn-sm" disabled={!points.length} onClick={onUndo}>
          Undo point
        </button>
        <button className="btn btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
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
          `Radar station ${s.station_id} · ${s.name} — holds a small craft to ` +
          `${Math.round(s.range_small_km)} km, a large ship to ` +
          `${Math.round(s.range_large_km)} km`,
      },
    }));
  upsertCircleLayer(m, "radar-stations", stationFeats, "#0b6e75",
                    visible.radar_coverage, () => {}, MARK_STYLE.context);

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
          ? "Dark contact"
          : `Suppressed — ${String(c.status || "").replace("suppressed_", "").replace(/_/g, " ")}`) +
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
    // **Two line layers, one per band, because `line-dasharray` cannot be a
    // data expression.** MapLibre rejects one outright —
    // "layers.…-line.paint.line-dasharray: data expressions not supported" —
    // and drops the property, so both rings drew SOLID. The dashed outer ring
    // is the whole point of drawing two: solid is what the station holds for a
    // small craft, dashed is what it holds for a large one, and the gap
    // between them is "big ships only". Losing the dash silently promised
    // skiff coverage out to the tanker horizon, which is exactly the
    // misreading the two-ring design exists to prevent.
    //
    // `line-opacity` was expression-driven in the same rule and IS supported,
    // but it moves here too: one filtered layer per band is the shape that
    // holds for every property rather than only some of them.
    for (const [band, dash, opacity] of [
      ["small", null, 0.55],
      ["large", [3, 2], 0.35],
    ]) {
      m.addLayer({
        id: `${id}-line-${band}`, type: "line", source: id,
        filter: ["==", ["get", "band"], band],
        paint: {
          "line-color": "#0b6e75",
          "line-width": 0.9,
          "line-opacity": opacity,
          ...(dash ? { "line-dasharray": dash } : {}),
        },
      }, under);
    }
  }
  for (const suff of ["-fill", "-line-small", "-line-large"]) {
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

function upsertCircleLayer(m, id, feats, color, on, onSelect,
                           style = MARK_STYLE.context) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "circle", source: id,
      paint: {
        "circle-radius": style.radius,
        "circle-color": color,
        "circle-opacity": style.opacity,
        "circle-stroke-width": style.strokeWidth,
        // A ring mark strokes in its own colour; everything else strokes white
        // to separate it from whatever it overlaps.
        "circle-stroke-color": style.strokeColor === ALERT_COLOR ? color
                                                                 : style.strokeColor,
        "circle-stroke-opacity": 1,
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
