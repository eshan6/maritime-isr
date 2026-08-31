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
// **The map draws one vessel's motion in three parts and no vessel's history.**
// The old `tracks` layer drew every vessel's entire eight-week polyline at once
// — a static mat of two hundred lines that was on-or-off, told you nothing
// about *when*, and did not change as the clock ran. It is gone. In its place,
// for the vessels broadcasting at the instant on the clock:
//
//   * **the vessel**, at her interpolated position (as before);
//   * **the trail** — where she has been *in this reporting session*, drawn
//     only up to the clock, so it grows behind her as she moves;
//   * **the projection** — where dead reckoning says she is going, from the
//     API (ADR-039), with the uncertainty cone on the selected vessel.
//
// A *session* is an unbroken run of broadcasting: consecutive fixes no more
// than `AIS_SESSION_BREAK_HOURS` apart, segmented server-side on the full
// series (`/tracks` returns the break indices). This is not decoration. It is
// what stops the animation drawing a vessel gliding steadily across a five-day
// silence nobody observed — the map has been doing exactly that, straight-lining
// every gap in the corpus, and the trail layer would have drawn that invention
// as a travelled path.
//
// It is deliberately NOT a voyage and is not labelled as one. A trawler working
// a ground for four days broadcasts continuously and gets one long tangled
// session; a merchant crossing the Gulf gets one clean leg. Both are honestly
// "the run of broadcasting she is in", which is a fact about the data. Calling
// it a voyage would be a claim about her intent that nothing here measures.
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
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate, fmtDateTime } from "../lib/format.js";
import { useTheme } from "../lib/theme.js";
import { VesselPanel } from "../components/VesselPanel.jsx";
import { ZonePanel } from "../components/ZonePanel.jsx";

// AOI v1 — Arabian Sea / Indian west coast (config.py AOI_V1).
const AOI = { lonMin: 60, latMin: 5, lonMax: 78, latMax: 25 };

//: Wall-clock seconds the animation spends on one HOUR of corpus time, at 1x.
//:
//: **Twenty seconds an hour, where it used to be 0.29.** The old pace was
//: 7 s/day, which meant a vessel making 12 knots crossed the whole Arabian Sea
//: in the time it takes to read this sentence: nothing on screen was a
//: *movement*, it was a streak. At 20 s/h a merchant covers about four nautical
//: miles per wall-clock second of watching, which is slow enough that an
//: operator can follow one hull, see her overhaul another, and watch her leave
//: the cone drawn ahead of her.
//:
//: The cost is stated rather than capped: the eight-week AIS window is 1,344
//: hours and takes seven and a half hours to play end to end at 1x. That is
//: what the speed control is for, and why the scrubber says the rate out loud.
//: A silent cap — which is what MAX_PLAYTHROUGH_S was — would have quietly
//: overridden the requested pace and left nobody able to tell why.
const SECONDS_PER_HOUR = 20;

//: Multipliers on that pace. 1x is the requested rate and the default; the rest
//: exist because "watch one hull manoeuvre" and "see where the fleet got to by
//: Thursday" are both real questions and no single rate answers both. Capped at
//: 60x — that is back to roughly the old streaking pace, and above it the
//: animation stops being one.
const SPEEDS = [1, 2, 5, 15, 60];

//: How often the animation recomputes positions, in wall-clock milliseconds.
//: Driven by requestAnimationFrame and throttled to this, rather than a
//: setInterval: a fixed interval drifts against the frame clock and shows as a
//: stutter, and at 20 s/h the whole point is that the motion is smooth enough
//: to follow.
const FRAME_MS = 40;

//: How far the clock has to move, in seconds of corpus time, before the map
//: asks the API for fresh forward projections. Thirty minutes: at 1x that is a
//: request every ten wall-clock seconds, and a projection made from a fix up to
//: half an hour stale is still the projection the system would have been
//: asserting at that moment.
const PREDICTION_REFRESH_S = 1800;

//: How far ahead the drawn projection reaches. Three hours is the lead the
//: projection module's own measurements are quoted at, and at merchant speeds
//: it is about 36 nm — long enough to see where she is headed, short enough
//: that the cone is still narrower than the gap to the next ship.
const PREDICTION_LEAD_HOURS = 3;

//: Zoom at which vessel names appear beside their marks.
//:
//: 8.5 is about 150 km across the viewport on a laptop — close enough that the
//: handful of hulls on screen can each carry a name without the labels
//: colliding, and far enough out that it is still a picture of an area rather
//: than of one ship. Below it the names are suppressed entirely: ninety-two
//: overlapping labels is not more information than none, it is less.
const LABEL_MIN_ZOOM = 8.5;

//: Ceiling on how many names are drawn at once, whatever the zoom. A viewport
//: over a crowded anchorage can hold dozens of hulls at any zoom, and the
//: labels are DOM nodes: the cap is what keeps a pan into Kandla from putting
//: two hundred of them on the page. Reported on screen when it bites, because a
//: label layer that silently shows some of the fleet is a layer that quietly
//: answers "who is that" wrong.
const LABEL_MAX = 40;

//: The size of a drawn name, for the collision test. Measured in the browser
//: against this corpus (names render 6.6-7.55 px per character at 11px medium,
//: 16.5 px tall), rounded UP: a box estimated too small lets two labels each
//: decide they fit and puts the collision straight back.
const LABEL_CHAR_PX = 7.7;
const LABEL_H_PX = 17;

//: How much of the travelled trail is drawn at full strength. The trail fades
//: back toward the start of the session so the recent movement reads first;
//: without it a trawler working one ground for four days draws a solid scribble
//: in which nothing says which end is now.
const TRAIL_FADE = 0.55;

// ---- basemaps -------------------------------------------------------------
//
// **The old source stopped serving and the map filled with "API KEY REQUIRED"
// stamped across every tile.** It pointed at `basemaps.cartocdn.com/light_all`,
// which used to be keyless and is not any more, so the tiles that came back
// were CARTO's watermark image. Nothing about the map itself was wrong: it is a
// real slippy map, every mark on it is placed from a lat/lon, and the vessels
// were plotted correctly the whole time. It was the picture UNDER them that had
// been replaced with a notice.
//
// So: keyless sources only, and more than one, because a single hardcoded
// provider is exactly how this failed. Each is served under an attribution the
// layer carries.
//
// **The default basemap follows the theme, and that is the point of it.**
// Setting the app to dark used to leave the map a bright plate of land with the
// tiles turned down to 62% — a light map wearing a filter, which is how it read.
// A dark screen wants a basemap that was *drawn* dark: near-black water, dark
// grey land, restrained labels, so the marks on top are the brightest thing on
// screen. That is the whole reason an operations map goes dark in the first
// place, and dimming a light raster gets you a muddy plate instead.
//
// So a basemap may carry `tiles` as one array (theme-independent, e.g. imagery)
// or as `{light, dark}`. `canvas` is the default and is the only entry with
// both — Esri's Light Gray / Dark Gray canvases, which are the same cartography
// in two values.
//
// **Keyless sources only, and more than one**, because a single hardcoded
// provider is exactly how this failed before: the map once pointed at
// `basemaps.cartocdn.com/light_all`, which stopped being keyless, and every
// tile came back stamped "API KEY REQUIRED". Nothing about the map was wrong
// then and nothing is now — every mark is placed from a lat/lon whether or not
// a tile arrives. Each source is served under the attribution its layer carries.
const BASEMAPS = [
  {
    id: "canvas",
    label: "Canvas",
    hint: "Dark or light with the theme. Least ink under the traffic.",
    tiles: {
      light: ["https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
      dark: ["https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
    },
    // Names only, on transparent tiles, so they go over the base rather than
    // replacing it — and they are drawn for their own value, which is why the
    // reference layer has to switch with the base.
    labels: {
      light: ["https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
      dark: ["https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
    },
    attribution: "Esri, HERE, Garmin, OpenStreetMap contributors",
    maxzoom: 16,
    // Already dark where it needs to be. Dimming it further would push the land
    // into the background colour and lose the coastline entirely.
    dimInDark: false,
  },
  {
    id: "ocean",
    label: "Ocean",
    hint: "Bathymetry and coastline. Depth contours and shelf edges.",
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"],
    labels: ["https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, GEBCO, NOAA, National Geographic and other contributors",
    maxzoom: 13,
    dimInDark: true,
  },
  {
    id: "streets",
    label: "Streets",
    hint: "Ports, terminals and coastal infrastructure.",
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
    dimInDark: true,
  },
  {
    id: "satellite",
    label: "Satellite",
    hint: "Imagery. Berths and structures are visible.",
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
    maxzoom: 18,
    // Imagery of the real world is already dark over water at night-time
    // palettes and dimming it makes a berth unreadable.
    dimInDark: false,
  },
];

const DEFAULT_BASEMAP = "canvas";

//: Pick the theme's variant of a field that may be one array or `{light, dark}`.
function forTheme(v, themeKey) {
  if (!v) return null;
  return Array.isArray(v) ? v : (v[themeKey] || v.light || null);
}

//: Built per basemap and per theme rather than held as one constant, so
//: switching either is a style swap and not a special case.
//:
//: `--map-ground` is the colour under the tiles and it matters more than it
//: looks: it is what the operator sees wherever a tile has not arrived, which
//: on a slow link is most of the screen for the first second and on a blocked
//: one is all of it, forever. It has to be the right value for the theme on its
//: own, with no tile on top.
//:
//: `--map-tile-opacity` dims a basemap that was drawn for a light page. It is
//: applied only to sources that say they need it (`dimInDark`): dimming a
//: basemap that is already dark washes the land into the background and takes
//: the coastline with it.
function basemapStyle(id, themeKey) {
  const b = BASEMAPS.find((x) => x.id === id) || BASEMAPS[0];
  const css = getComputedStyle(document.documentElement);
  const ground = css.getPropertyValue("--map-ground").trim() || "#eef2f6";
  const dim = parseFloat(css.getPropertyValue("--map-tile-opacity"));
  const opacity = b.dimInDark && Number.isFinite(dim) ? dim : 1;
  const tiles = forTheme(b.tiles, themeKey);
  const labels = forTheme(b.labels, themeKey);
  const sources = {
    base: { type: "raster", tiles, tileSize: 256,
            maxzoom: b.maxzoom || 19, attribution: b.attribution },
  };
  const layers = [
    { id: "bg", type: "background", paint: { "background-color": ground } },
    { id: "base", type: "raster", source: "base",
      paint: { "raster-opacity": opacity } },
  ];
  if (labels) {
    sources.baselabels = { type: "raster", tiles: labels, tileSize: 256,
                           maxzoom: b.maxzoom || 19 };
    layers.push({ id: "baselabels", type: "raster", source: "baselabels",
                  paint: { "raster-opacity": Math.min(1, opacity + 0.2) } });
  }
  return { version: 8, sources, layers };
}

//: Read from the stylesheet so the dark theme can reach them. Hardcoded, the
//: vessels stayed light-mode blue on a dark sea.
function themeColor(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return (v && v.trim()) || fallback;
}
const VESSEL_COLOR = "#1a5fb4";
const ALERT_COLOR = "#b0221b";
const TRAIL_COLOR = "#4a7fbe";
const PREDICT_COLOR = "#0f7bd1";

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
    hint: "Only vessels broadcasting at the time on the clock. Click one to see "
      + "her own track and projection; the switches below draw them for the "
      + "whole fleet at once.",
    layers: [
      { id: "positions", label: "Vessels broadcasting now", color: VESSEL_COLOR },
      { id: "trails", label: "Travelled track, every vessel", color: TRAIL_COLOR },
      { id: "predicted", label: "Predicted track, every vessel", color: PREDICT_COLOR },
      { id: "cone", label: "Uncertainty cone", color: PREDICT_COLOR },
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
  //: `ownStroke` is what makes it a ring rather than a haloed disc: the stroke
  //: takes the layer's own colour instead of the white separator every filled
  //: mark gets. It replaced a `strokeColor === ALERT_COLOR` comparison, which
  //: only worked while exactly one layer wanted the behaviour and silently
  //: gave the second one a hardcoded, un-themed stroke.
  flag: { radius: 9, opacity: 0, strokeWidth: 3, ownStroke: true },
  //: The far end of a projection. Hollow, and smaller than the vessel mark
  //: that is its origin — it is a claim about where she will be, and it must
  //: never outweigh the fix she actually broadcast.
  predicted: { radius: 4, opacity: 0, strokeWidth: 1.6, ownStroke: true },
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
  const [params, setParams] = useSearchParams();
  // **A COUNTER, not a boolean, and the difference is a bug that emptied the
  // map.** Every layer effect here is gated on "the style is up"; the swap
  // effect used to drop that flag to false, call `setStyle`, and raise it again
  // from `style.load`. React batches state updates, and MapLibre parses an
  // inline style fast enough that both updates land in one batch — so `ready`
  // went true → false → true, React compared the ends, saw no change, and
  // re-ran nothing. Switching basemap therefore threw away every source this
  // app had added (a full `setStyle` does) and never rebuilt one: no vessels,
  // no marks, no error, permanently.
  //
  // A number that only ever goes up cannot be collapsed by batching. Each
  // completed style load is a new epoch and every dependent effect re-runs.
  const [styleEpoch, setStyleEpoch] = useState(0);
  const ready = styleEpoch > 0;
  const [selected, setSelected] = useState(null);
  //: How the map came to be looking at this hull, when it was sent here from
  //: somewhere else. Held so the arrival can be *explained*: flying to a
  //: position without saying how old it is presents a stale fix as a current
  //: one, and the hulls worth finding are disproportionately the ones that
  //: stopped reporting — going quiet is the finding.
  const [locate, setLocate] = useState(null);
  //: Has the tracks request come back? Not the same as having tracks.
  const [tracksReady, setTracksReady] = useState(false);
  //: Why there are no tracks, when the reason is us rather than the sea.
  const [trackError, setTrackError] = useState(null);
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
  // **The map opens on one layer: where the ships are.** Nothing else — no
  // trails, no projections, no event pins, no density, no footprints, no
  // areas.
  //
  // Every previous opening set was assembled by adding one more well-argued
  // default to the last one, and each addition was individually right: the
  // footprints were the only unambiguously real thing, a radar contact without
  // its coverage ring invites a misreading, density summarises four event
  // layers better than they do. Nobody ever added the reasons up, and a map
  // that opens with everything shouting is a map that opens illegible.
  //
  // The floor is now the question the screen exists to answer — *what is out
  // there right now* — and everything else is one click away in a group that
  // says what it holds. The three per-vessel layers below (trail, projection,
  // cone) are the "for every vessel at once" switches; a SELECTED vessel gets
  // hers drawn whatever they say, because asking about one hull is exactly
  // when her past and her predicted track are worth the ink.
  const [visible, setVisible] = useState({
    positions: true, trails: false, predicted: false, cone: false,
    density: false,
    encounter: false, loitering: false, port_visit: false, gap: false,
    alerts: false,
    detections: false, scenes: false, ports: false,
    radar_coverage: false, radar_tracks: false, radar_contacts: false,
    z_eez: false, z_contiguous_zone: false, z_territorial_sea: false,
    z_imbl: false, z_shipping_lane: false, z_sensitive_area: false,
    z_port_limit: false, z_anchorage: false, z_oil_terminal: false,
    z_geofence: false,
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
  //: Forward projections for the instant on the clock, re-asked as it advances
  //: (ADR-039). `null` until the first answer, so the readout can distinguish
  //: "not asked yet" from "asked, and nobody is broadcasting".
  const [predictions, setPredictions] = useState(null);
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
  //: Multiplier on SECONDS_PER_HOUR. 1x is the honest default and the rate the
  //: readout quotes; anything else is the operator saying "faster than real
  //: watching", which is their call to make and not one to bury in a constant.
  const [speed, setSpeed] = useState(1);
  // Has the operator moved the clock themselves? Until they have, the playhead
  // is ours to park somewhere useful (see the parking effect below); once they
  // have, it is theirs and we never move it under them.
  const [scrubbed, setScrubbed] = useState(false);
  //: Which basemap, remembered. An operator who works in bathymetry should not
  //: have to reselect it on every page load. The DEFAULT is now the canvas,
  //: which is the one entry that follows the theme — so a dark app opens a dark
  //: map without the operator having to know that is a setting.
  const [basemap, setBasemap] = useState(
    () => localStorage.getItem("misr.basemap") || DEFAULT_BASEMAP);
  //: Set when a tile request fails, so a dead provider says so once instead of
  //: leaving a blank sea that reads as "no data here".
  const [tileError, setTileError] = useState(false);
  //: Current zoom, mirrored into React so the name labels can switch on at
  //: LABEL_MIN_ZOOM. MapLibre holds the authoritative value; this is a copy
  //: that re-renders, and it is updated on `zoom` rather than `zoomend` so the
  //: names appear during the gesture rather than after it.
  const [zoom, setZoom] = useState(0);
  //: Bumped on `moveend`. The label set depends on the viewport, which is not
  //: React state; this is the signal that it changed.
  const [viewTick, setViewTick] = useState(0);
  //: How many names the cap withheld at the current view, so it can say so.
  const [labelsHidden, setLabelsHidden] = useState(0);
  //: Is the disclosure chip expanded? Closed on arrival: the notes are a
  //: reference an operator opens when a layer looks wrong, not a preamble they
  //: have to read past to reach the map.
  const [notesOpen, setNotesOpen] = useState(false);
  //: Live DOM markers, keyed by vessel id, reused across renders rather than
  //: torn down and rebuilt — recreating forty nodes on every clock tick is what
  //: would make this layer cost more than it is worth.
  const labelMarkers = useRef(new Map());
  //: The map paints from JavaScript, so a theme flip has to rebuild its style
  //: object. CSS custom properties do not reach a MapLibre paint expression.
  const { resolved: themeKey } = useTheme();

  // ---- init map once ----
  useEffect(() => {
    const initialBasemap = localStorage.getItem("misr.basemap") || DEFAULT_BASEMAP;
    // The baseline the swap effect compares against. Recorded here, where the
    // style is actually built, so the two can never disagree about what is on
    // screen.
    appliedStyle.current = `${initialBasemap}|${themeKey}`;
    const m = new maplibregl.Map({
      container: mapEl.current,
      // `themeKey` from the first render. If the system preference resolves
      // later, the swap effect below rebuilds the style — it compares against
      // `basemap|themeKey` and this mount records that pair.
      style: basemapStyle(initialBasemap, themeKey),
      bounds: [[AOI.lonMin, AOI.latMin], [AOI.lonMax, AOI.latMax]],
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    // **`style.load`, not `load`.** `load` does not fire until the map has
    // completed a first render including its sources, so a basemap host that is
    // unreachable holds it forever. Every operational layer this app draws was
    // gated behind it, which meant an unreachable tile server did not degrade
    // the picture, it deleted it: no vessels, no alerts, no area of interest,
    // on a screen whose entire job is showing where things are. None of those
    // marks need a basemap. They need the style to exist so layers can be added
    // to it, which is what `style.load` means.
    // The label layer keys off zoom and off which vessels are in the viewport.
    //
    // Zoom is mirrored live, because the names appearing as you push in is the
    // interaction. **The viewport is only re-read on `moveend`**, deliberately:
    // `move` fires every frame of every pan and flyTo, and a React state
    // update per frame to rebuild a marker list would make the pan itself
    // stutter. Existing labels stay glued to their vessels during the gesture —
    // a MapLibre marker is anchored to a lng/lat, not to a pixel — so the only
    // thing that waits for the gesture to finish is which hulls are eligible.
    const onZoom = () => setZoom(m.getZoom());
    m.on("zoom", onZoom);
    m.on("moveend", () => setViewTick((n) => n + 1));
    m.on("style.load", () => {
      addAoi(m);
      setStyleEpoch((n) => n + 1);
    });
    // A basemap that will not load is a fact about the network, and the map
    // must say so. Silently drawing an empty sea invites the reading that
    // there is nothing there.
    m.on("error", (e) => {
      if (String(e?.error?.message || "").toLowerCase().includes("tile")
          || e?.sourceId === "base") setTileError(true);
    });
    map.current = m;
    return () => m.remove();
  }, []);

  // ---- basemap and theme swaps ------------------------------------------
  //
  // `setStyle` throws away every layer this app added, so everything is
  // re-added on the next `styledata`. The layer effects below key off `ready`,
  // which is why it is dropped and re-raised rather than left true: a redraw
  // that ran against a half-built style silently lost the vessels.
  // **Seeded by the init effect with the pair the map was actually built from,
  // not left null for this effect to consume.** The old shape was "if the ref
  // is still null, this is the mount run, so record and return" — and it was
  // wrong, because the mount run never reached that line: the `!ready` guard
  // above it returned first, `ready` was not in the dependency list, so the
  // effect's *next* run was the operator's first basemap click. That click hit
  // the null branch and was swallowed. Picking a basemap did nothing until you
  // picked it twice.
  //
  // Seeding removes the branch entirely: there is a baseline from the moment
  // the map exists, and every run is the same comparison.
  const appliedStyle = useRef(null);
  useEffect(() => {
    if (!map.current || !ready) return;
    const want = `${basemap}|${themeKey}`;
    // **Never restyle to the style already on screen.** `setStyle` throws away
    // every layer this app added; doing it for a style that is already up
    // rebuilds them for nothing, and blanks the map when the rebuild cannot
    // finish.
    if (appliedStyle.current === want) return;
    appliedStyle.current = want;
    localStorage.setItem("misr.basemap", basemap);
    setTileError(false);
    const m = map.current;
    // **`diff: false`, and it is load-bearing.** MapLibre's default is to DIFF
    // the incoming style against the live one and apply the difference — and a
    // basemap swap here is perfectly diffable, because both styles carry the
    // same source ids with different tile URLs. Two consequences, both silent:
    // the diff strips every source and layer this app added (they are not in
    // the incoming style), and **`style.load` never fires** for a diffed
    // update. So `ready` never came back, the layer effects never re-ran, and
    // switching basemap emptied the map of vessels, permanently, with no error
    // anywhere. A full reload is what the rest of this code already assumes.
    m.setStyle(basemapStyle(basemap, themeKey), { diff: false });
    // `style.load`, not `styledata` plus `isStyleLoaded()`. The latter is only
    // true once every SOURCE has loaded, so a basemap whose tiles never arrive
    // leaves it false forever, `ready` never comes back, and the vessels are
    // never redrawn: an unreachable tile server took the whole picture with it,
    // including the marks that do not depend on tiles at all.
    m.once("style.load", () => {
      addAoi(m);
      setStyleEpoch((n) => n + 1);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // `ready` IS a dependency, and its absence is half of why the swallowed
    // click above went unnoticed: without it, a basemap or theme change made
    // while the map was still building was dropped and never reconsidered.
    // With the ref seeded, re-running on `ready` is free when nothing changed.
  }, [basemap, themeKey, styleEpoch]);

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
      // **A failed request is not an empty sea.** This was swallowed, and the
      // status line then read "no AIS tracks in this window", which is a claim
      // about the DATA made on the strength of a request that never returned.
      // The operator gets a blank map and a sentence telling them the blankness
      // is real. Same class of error as a silent truncation.
      .catch((e) => { live && setTrackError(String(e?.message || e)); })
      // Settled either way. "Still loading" and "loaded, and she is not in it"
      // look identical from `tracks.length === 0`, and a deep link that cannot
      // tell them apart waits forever on an empty corpus — silently, which is
      // the failure mode this project treats as worse than an error.
      .finally(() => { live && setTracksReady(true); });
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

  // Every disclosure the map owes the operator, as one list the chip renders.
  //
  // The API notes (truncation, empty layers, which window is being played) plus
  // the two this client owns: what the projection layer actually is, and how
  // many vessel names the label cap withheld. Gathering them here is what lets
  // the chip say "4 notes" — a count nobody has to maintain by hand, and one
  // that cannot drift from what is inside it.
  const mapNotes = useMemo(() => {
    const out = Object.entries(notes)
      .filter(([, v]) => v)
      .map(([key, text]) => ({ key, text }));
    if (predictions) {
      out.push({
        key: "predicted track",
        text: predictions.failed
          ? `Projections unavailable: ${predictions.failed}. The vessels and `
            + "their trails are unaffected."
          : `${predictions.items.length} projection`
            + `${predictions.items.length === 1 ? "" : "s"}, `
            + `${PREDICTION_LEAD_HOURS} h ahead. ${predictions.basis} `
            + `${predictions.caveat}`
              + (predictions.note ? ` ${predictions.note}` : ""),
      });
    }
    if (labelsHidden > 0) {
      out.push({
        key: "vessel names",
        text: `${labelsHidden} name${labelsHidden === 1 ? " is" : "s are"} not `
          + "drawn at this view: the mark is there but its name would have "
          + "landed on top of another one. Zoom in to separate them.",
      });
    }
    return out;
  }, [notes, predictions, labelsHidden]);

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

  // Each track with its reporting sessions resolved once, rather than on every
  // frame. `breaks` arrives from the API as indices into `points`; this turns
  // them into [firstIndex, lastIndex] pairs, which is the form both the vessel
  // mark and the trail need sixty times a second.
  const trackList = useMemo(
    () => tracks.map((tr) => ({ ...tr, sessions: sessionsOf(tr) })), [tracks]);

  //: The trail is rebuilt on a coarser clock than the vessel mark. A trail is
  //: up to a few hundred points per hull across a hundred hulls, and handing
  //: MapLibre that collection twenty-five times a second is the one thing on
  //: this screen that would actually cost frames — while the visible
  //: difference is the last two minutes of a line measured in days. The mark
  //: itself still moves every frame, which is what the eye is following.
  const trailClockSec = clockSec == null ? null
    : Math.floor(clockSec / 120) * 120;

  //: Which projection request the clock is currently inside. Quantised so that
  //: advancing the clock re-asks on a stated cadence instead of on every frame,
  //: and so that a scrub across the window collapses to one request per
  //: boundary rather than one per pixel dragged.
  const predictionBucket = clockSec == null ? null
    : Math.floor(clockSec / PREDICTION_REFRESH_S);

  // ---- forward projections, re-asked as the clock advances ---------------
  //
  // Asked of the API rather than computed here, and that is a deliberate
  // constraint rather than a convenience: the projection model is a calibrated
  // rule in `tracks/projection.py` with a measured error budget behind every
  // constant in it. Reimplementing dead reckoning in JavaScript would put a
  // second copy of that rule on the other side of the wire, free to drift from
  // the first the day either is touched — which is the same "a collector that
  // started detecting" mistake the assistant is built to avoid.
  useEffect(() => {
    // Asked whenever anything could draw a projection: the fleet-wide switch,
    // or a selected vessel — who gets hers drawn regardless. Gating this on the
    // switch alone is what would make clicking a hull on the default map show
    // her trail and nothing ahead of her.
    if (predictionBucket == null || !(visible.predicted || selected)) return;
    const ctrl = new AbortController();
    // A short delay so dragging the scrubber across a week fires one request at
    // the end rather than forty on the way.
    const h = setTimeout(() => {
      api.predictions({ at: Math.round(clockSec),
                        lead_hours: PREDICTION_LEAD_HOURS }, ctrl.signal)
        .then((r) => setPredictions(r))
        .catch((e) => {
          if (e?.name === "AbortError") return;
          // Same rule as `/tracks`: a failed request is not an empty sea. An
          // empty projection layer with no explanation reads as "no vessel
          // here is going anywhere", which is a claim we did not make.
          setPredictions({ items: [], failed: String(e?.message || e) });
        });
    }, 180);
    return () => { clearTimeout(h); ctrl.abort(); };
    // `clockSec` is deliberately not a dependency — the bucket is. Keying on
    // the raw clock would re-fire this on every animation frame. `selected` is
    // reduced to a boolean: the projections come back for the whole
    // broadcasting fleet in one answer, so changing WHICH hull is selected
    // needs no new request, only a redraw.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [predictionBucket, visible.predicted, !!selected]);

  // ---- static layers: events, ports, scenes, alert markers, track lines ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderStatic(map.current, data, tracks, visible, (id) => setSelected(id));
    renderDensity(map.current, data.density, visible.density);
    renderRadar(map.current, data, radarTracks, visible);
    renderZones(map.current, data.zones, visible, (z) => setSelectedZone(z));
  }, [styleEpoch, data, tracks, radarTracks, visible]);

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
  }, [styleEpoch, drawing]);

  // ---- moving vessels: interpolate each track to the clock and glide ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderVessels(map.current, trackList, clockSec, visible.positions,
                  (id) => setSelected(id));
  }, [styleEpoch, trackList, clockSec, visible.positions]);

  // ---- the trail: where she has been in THIS session, up to the clock ----
  useEffect(() => {
    if (!ready || !map.current) return;
    renderTrails(map.current, trackList, trailClockSec, visible.trails, selected);
  }, [styleEpoch, trackList, trailClockSec, visible.trails, selected]);

  // ---- her name, beside her mark, once you are close enough --------------
  //
  // **DOM markers, not a MapLibre symbol layer, and that is a deliberate
  // trade.** A symbol layer would be GPU-drawn and would declutter itself,
  // which is the better mechanism — but it needs a `glyphs` URL, and this
  // app's style is raster-only with no font server behind it. Adding one would
  // put the vessel names behind a THIRD external host, on a screen where the
  // operator is already watching basemap tiles fail to arrive. A glyph server
  // that does not answer does not error visibly: the layer just renders
  // nothing, which is this project's least favourite kind of failure.
  //
  // So the names are HTML, served from the same origin as the app, and they
  // appear or they do not for reasons visible in the page. The cost is DOM
  // nodes, which is what LABEL_MIN_ZOOM and LABEL_MAX are for.
  useEffect(() => {
    if (!ready || !map.current) return;
    const m = map.current;
    const live = labelMarkers.current;
    const wanted = new Map();

    if (visible.positions && zoom >= LABEL_MIN_ZOOM && clockSec != null) {
      const b = m.getBounds();
      const cands = [];
      for (const tr of trackList) {
        if (!tr.name) continue;      // no name is not a reason to invent one
        const pos = posAt(tr, clockSec);
        if (!pos) continue;
        if (pos[0] < b.getWest() || pos[0] > b.getEast()
            || pos[1] < b.getSouth() || pos[1] > b.getNorth()) continue;
        cands.push({ id: tr.vessel_id, name: tr.name, pos });
      }
      // **Decluttered in screen space, because nothing else will do it.** A
      // MapLibre symbol layer hides colliding labels for free; DOM markers
      // (see the note above on why these are DOM) happily draw all of them on
      // top of each other, and an anchorage came out as one grey smear of
      // overlapping names — strictly worse than no names, because it looks
      // like information.
      //
      // Greedy, in a fixed priority: the selected hull first, then by id so
      // the same ships keep their labels as the fleet moves rather than the
      // set flickering between neighbours on every tick.
      cands.sort((x, y) => (x.id === selected ? -1 : y.id === selected ? 1
                            : x.id < y.id ? -1 : 1));
      const placed = [];
      let over = 0;
      for (const c of cands) {
        if (wanted.size >= LABEL_MAX) { over++; continue; }
        const p = m.project(c.pos);
        // The marker sits 10px right of the mark, 11px type. Width is
        // estimated from the character count rather than measured: measuring
        // means laying the node out, and doing that for forty candidates on
        // every tick is a synchronous reflow per frame.
        //
        // LABEL_CHAR_PX is measured, not guessed — rendered names in this
        // corpus run 6.6 to 7.55 px per character (they are upper case, which
        // is the wide end), and the constant sits above the top of that range
        // deliberately. Under-estimating is the expensive direction: it lets
        // two boxes both claim they fit and puts the overlap back, which is
        // exactly what a first pass at 6.1 px did.
        const r = { x1: p.x + 8, y1: p.y - LABEL_H_PX / 2 - 1,
                    x2: p.x + 12 + c.name.length * LABEL_CHAR_PX,
                    y2: p.y + LABEL_H_PX / 2 + 1 };
        if (placed.some((q) => r.x1 < q.x2 && r.x2 > q.x1
                            && r.y1 < q.y2 && r.y2 > q.y1)) { over++; continue; }
        placed.push(r);
        wanted.set(c.id, { name: c.name, pos: c.pos });
      }
      if (over !== labelsHidden) setLabelsHidden(over);
    } else if (labelsHidden !== 0) {
      setLabelsHidden(0);
    }

    for (const [id, mk] of live) {
      if (!wanted.has(id)) { mk.remove(); live.delete(id); }
    }
    for (const [id, { name, pos }] of wanted) {
      let mk = live.get(id);
      if (!mk) {
        const el = document.createElement("div");
        el.className = "vessel-label";
        el.textContent = name;
        // Clicking the name selects the hull. A label you cannot click is a
        // smaller target sitting next to the one you can, which reads as a
        // dead spot on the map.
        el.addEventListener("click", (e) => { e.stopPropagation(); setSelected(id); });
        mk = new maplibregl.Marker({ element: el, anchor: "left", offset: [10, 0] })
          .setLngLat(pos).addTo(m);
        live.set(id, mk);
      } else {
        mk.setLngLat(pos);
      }
      mk.getElement().classList.toggle("is-selected", id === selected);
    }
  }, [styleEpoch, trackList, clockSec, zoom, viewTick, visible.positions, selected,
      labelsHidden]);

  // Markers live outside React's tree, so nothing else removes them when the
  // view unmounts.
  useEffect(() => () => {
    for (const mk of labelMarkers.current.values()) mk.remove();
    labelMarkers.current.clear();
  }, []);

  // ---- the projection: where dead reckoning says she is going ------------
  useEffect(() => {
    if (!ready || !map.current) return;
    renderPredictions(map.current, predictions, selected,
                      visible.predicted, visible.cone);
  }, [styleEpoch, predictions, selected, visible.predicted, visible.cone]);

  // ---- the selection ring, redrawn wherever the subject is --------------
  useEffect(() => {
    if (!ready || !map.current) return;
    renderSelection(map.current, selected, trackList, clockSec, data);
  }, [styleEpoch, selected, trackList, clockSec, data]);

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
  //
  // **`trackList`, not `tracks`.** `busiestInstant` counts reporting sessions,
  // and only `trackList` carries them: handed the raw API rows it found no
  // sessions on any of them, returned null, and this effect gave up — so the
  // playhead stayed at the end of the window exactly as it had before the
  // parking was written. It looked like it worked, because pressing play wraps
  // straight back to the start, and the start of an AIS window does have a
  // handful of vessels in it. Three of two hundred, in the run that caught it.
  useEffect(() => {
    if (scrubbed || !window_ || !trackList.length) return;
    const at = busiestInstant(trackList);
    if (at == null) return;
    const span = window_.end - window_.start;
    if (span <= 0) return;
    setT(Math.min(1, Math.max(0, (at * 1000 - window_.start) / span)));
  }, [scrubbed, window_, trackList]);

  // ---- "find her on the map" -------------------------------------------
  //
  // Arrived from a vessel card anywhere in the product with `?vessel=<id>`. The
  // operator's next question after "why is she flagged" is "where is she", and
  // the only previous answer was to leave the screen and hunt for one hull in a
  // picture holding thousands.
  //
  // **The clock moves to her last fix, and the notice says when that was.** In
  // the deployed product most hulls have a current position and this lands on
  // it; the interesting ones frequently do not, because a hull that stops
  // reporting is the thing this system exists to find. Flying to a six-hour-old
  // position with the playhead left where it was would draw her at a place she
  // is not, at a time she was not there, with nothing on screen to say so —
  // which is the map inventing evidence, the failure `alertPosition` already
  // refuses. So: go to the fix, state its age, and let the operator decide what
  // a four-day-old position is worth.
  useEffect(() => {
    const want = params.get("vessel");
    // Gated on the tracks request having SETTLED, not on the map having
    // painted: her record and the notice are useful even where the basemap
    // never loads, and the camera move below is the only part that needs a map.
    if (!want || !tracksReady) return;

    // The same cascade `alertPosition` walks, and for the same reason: a
    // subject worth finding is often one AIS cannot place. An AIS track is the
    // best answer; a radar contact is the next best and is frequently the ONLY
    // one for exactly the hulls this system exists to flag, since a target with
    // no transponder has no AIS track by definition; a located past event is
    // the last resort and is labelled as the proxy it is.
    let found = null;
    const tr = tracks.find((t) => t.vessel_id === want);
    const last = tr?.points?.[tr.points.length - 1];
    if (last) {
      found = { lon: last[0], lat: last[1], at: last[2], basis: "ais" };
    } else {
      const key = String(want).startsWith("contact:radar:")
        ? String(want).slice("contact:radar:".length) : null;
      const rc = key && data.radarContacts.find((c) => c.radar_track_id === key);
      if (rc && rc.lat != null && rc.lon != null) {
        const at = rc.ts ? Date.parse(rc.ts) / 1000 : null;
        found = { lon: rc.lon, lat: rc.lat, at, basis: "radar" };
      } else {
        // Anything located we hold about her, most recent first.
        const ev = data.events
          .filter((e) => e.vessel_id === want && e.lat != null && e.lon != null)
          .sort((a, b) => String(b.start_time || "").localeCompare(String(a.start_time || "")))[0];
        if (ev) {
          const at = ev.start_time ? Date.parse(ev.start_time) / 1000 : null;
          found = { lon: ev.lon, lat: ev.lat, at, basis: "event",
                    kind: (ev.kind || "").replace(/_/g, " ") };
        }
      }
    }

    if (!found) {
      setLocate({ id: want, found: false });
      setSelected(want);                     // her record still opens
      setParams({}, { replace: true });
      return;
    }

    const { lon, lat, at: tSec } = found;
    if (window_ && tSec != null && !Number.isNaN(tSec)) {
      const span = window_.end - window_.start;
      if (span > 0) {
        setT(Math.min(1, Math.max(0, (tSec * 1000 - window_.start) / span)));
        // The playhead is the operator's once they have touched it, but they
        // asked to be taken to this hull — that IS them moving it.
        setScrubbed(true);
      }
    }
    if (ready && map.current) {
      map.current.easeTo({ center: [lon, lat],
                           zoom: Math.max(map.current.getZoom(), 8.5),
                           duration: 900 });
    }
    setSelected(want);
    setLocate({ id: want, found: true, at: tSec, lon, lat,
                basis: found.basis, kind: found.kind });
    // Consume the parameter so a later pan does not fly back here, and so a
    // refresh does not re-trigger a flight the operator has moved on from.
    setParams({}, { replace: true });
  }, [params, ready, tracks, tracksReady, data.radarContacts,
      data.events, window_, setParams]);

  // ---- play/pause ----
  //
  // **The clock advances by measured wall time, not by a fixed step per tick.**
  // The old loop added a constant fraction every 100 ms, which ties the pace of
  // the simulation to how fast the browser gets round to the timer: a busy
  // frame silently slowed the fleet down, and a backgrounded tab (where
  // setInterval is throttled to once a second) made every vessel jump ten
  // minutes at a time. Reading the real elapsed time each frame means the rate
  // is what SECONDS_PER_HOUR says it is, whatever the frame rate does.
  //
  // Throttled to FRAME_MS rather than run at full rAF: each tick rebuilds the
  // vessel layer, and at 20 s/h a hull moves well under a pixel between frames,
  // so 25 updates a second is already far more than the eye can use.
  useEffect(() => {
    if (!playing || !window_) return;
    const spanMs = window_.end - window_.start;
    if (spanMs <= 0) return;
    //: Corpus milliseconds per wall-clock millisecond.
    const rate = (3600_000 * speed) / (SECONDS_PER_HOUR * 1000);
    let raf = 0;
    let last = performance.now();
    const frame = (now) => {
      raf = requestAnimationFrame(frame);
      const elapsed = now - last;
      if (elapsed < FRAME_MS) return;
      last = now;
      const dt = (elapsed * rate) / spanMs;   // fraction of the window
      setT((x) => (x >= 1 ? 0 : Math.min(1, x + dt)));
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [playing, window_, speed]);

  const movingCount = trackList.length;
  // How many of those are on screen at THIS instant — the number that tells the
  // operator whether the clock is somewhere with traffic. `movingCount` is the
  // corpus total and never changes, so on its own it cannot say that.
  //
  // Counted on the coarse clock, not on every frame: the number cannot change
  // between two frames forty milliseconds apart at any speed this player runs,
  // and recomputing it over every track sixty times a second is the kind of
  // work that shows up as a stutter in the thing it is describing.
  const onScreen = useMemo(
    () => trackList.reduce((n, tr) => n + (posAt(tr, trailClockSec) ? 1 : 0), 0),
    [trackList, trailClockSec],
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
                  ? "Not loaded. A statutory limit this system will not derive. "
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
                {missing && <span className="muted"> (not loaded)</span>}
              </label>
            );
          })}
        </LayerGroup>

        {/* Draw. The requirement's "draw a box anywhere and I'll tell you who
            was in it" begins here, and the control stays in the layer box
            rather than floating, so the drawn area reads as one more layer
            rather than as a mode the map is stuck in. */}
        {/* Basemap. In the layer panel because it IS a layer, and the one an
            operator changes for a real reason: bathymetry to read a track
            against depth, imagery to see a berth. */}
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)",
                      paddingTop: 8 }}>
          <div className="eyebrow">Basemap</div>
          <div className="basemap-pick">
            {BASEMAPS.map((b) => (
              <button key={b.id} title={b.hint}
                      className={basemap === b.id ? "on" : ""}
                      onClick={() => setBasemap(b.id)}>
                {b.label}
              </button>
            ))}
          </div>
          {tileError && (
            <div className="muted t-micro" style={{ marginTop: 6 }}>
              Basemap tiles failed to load. Marks are still positioned
              correctly. Try another basemap.
            </div>
          )}
        </div>

        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)",
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

      {/* **One positioned column, several notes.** Each of these used to
          position itself absolutely at the same corner, so any two on screen
          together sat exactly on top of each other and the later one in the DOM
          silently covered the earlier. Two were rare enough together to hide
          it; the projection disclosure below is always on, which would have
          made it permanent. They stack now. */}
      <div className="map-notes">
      {/* Sent here from a vessel card. States which fix the map flew to and
          how old it is, because "her position" and "the last position she
          broadcast" are different claims and the gap between them is often the
          finding. Dismissable, and it never covers the hull it is describing. */}
      {locate && (
        <div className="notebar" style={{ borderLeft: "3px solid var(--blue)" }}>
          {locate.found ? (
            <>
              <strong>
                {locate.basis === "ais"
                  ? "Showing her last known position."
                  : locate.basis === "radar"
                    ? "Showing where radar last held her."
                    : "Showing a proxy position."}
              </strong>{" "}
              {locate.basis === "ais"
                && "This is the last position she broadcast. "}
              {locate.basis === "radar"
                && "Nothing was broadcasting there. This is a radar contact, "
                   + "so it is where the array saw a target, not a reported "
                   + "position. "}
              {locate.basis === "event"
                && `She has no track and no radar contact here. This is where `
                   + `her most recent located event (${locate.kind || "event"}) `
                   + `happened, which is a proxy and not a position report. `}
              {locate.at != null && !Number.isNaN(locate.at)
                ? <>Recorded {fmtDateTime(new Date(locate.at * 1000).toISOString())}.
                   {" "}The clock has been moved to that moment. If she has been
                   seen since, she is not there now.</>
                : <>The record carries no time, so the clock has not been moved
                   and the age of this position is unknown.</>}
            </>
          ) : (
            <>
              <strong>No position to show.</strong> Nothing in this corpus places
              this subject anywhere: no AIS track, no radar contact, and no
              located event. Her record is open on the right; the map is not
              guessing at a pin.
            </>
          )}
          <button className="btn-link" style={{ marginLeft: 8 }}
                  onClick={() => setLocate(null)}>dismiss</button>
        </div>
      )}

      {/* **Folded to a chip.** These two panels covered a third of the sea.
          Each sentence in them earns its place — a capped event query looks
          exactly like an empty second half of the window, an empty SAR layer
          looks like a clean scene rather than one nobody processed, and a
          dashed line reaching ahead of a ship reads as knowledge unless
          something says it is dead reckoning — but "the disclosure must be
          available" was being read as "the disclosure must be permanently in
          front of the map", and those are different requirements. A wall of
          text nobody can dismiss is one nobody reads either.

          So: a chip that states how many notes there are, opening to the full
          text on click. Nothing is deleted and nothing is summarised away. */}
      {(mapNotes.length > 0) && (
        <div className={`notechip ${notesOpen ? "is-open" : ""}`}>
          <button type="button" className="notechip-head"
                  aria-expanded={notesOpen}
                  onClick={() => setNotesOpen((v) => !v)}>
            <span className="notechip-dot" />
            {mapNotes.length} note{mapNotes.length === 1 ? "" : "s"} on what
            this map is showing
            <span className="notechip-caret">{notesOpen ? "▾" : "▸"}</span>
          </button>
          {notesOpen && (
            <div className="notechip-body">
              {mapNotes.map((n) => (
                <div key={n.key} className="notechip-item">
                  <span className="note-key mono">{n.key}</span>: {n.text}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      </div>

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
          {clockMs ? fmtDate(new Date(clockMs).toISOString(), true) : "-"}
        </span>
        {/* The pace, and the fact that it is a choice. An animation whose rate
            is invisible cannot be read: "she has gone twenty miles" means
            nothing until you know whether that took an hour or a week. 1x is
            the honest default and the label says what it buys.

            Left of the scrubber bar, with the play button and the clock, and
            not out at the right-hand end with the status line: the vessel
            drawer is an overlay and it covers the right third of this control
            whenever a hull is selected — which is most of the time an operator
            is actually watching one move. */}
        <div className="speed-pick" title={
          `One hour of the corpus takes ${SECONDS_PER_HOUR} wall-clock seconds `
          + `at 1x. Higher covers more of the window per second and shows less `
          + `of each vessel's movement.`}>
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? "on" : ""}
                    disabled={!window_}
                    onClick={() => setSpeed(s)}>{s}x</button>
          ))}
        </div>
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
                + (movingCount === 1 ? "" : "s") + " broadcasting"
                + ` · ${(SECONDS_PER_HOUR / speed).toFixed(
                    SECONDS_PER_HOUR / speed < 1 ? 1 : 0)} s per hour`
                + (window_.playsWholeCorpus ? "" : " · AIS window")
              : trackError
                ? `could not load tracks: ${trackError}`
                : !tracksReady
                  ? "loading tracks…"
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
              showFindOnMap={false}
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

// ---- reporting sessions -------------------------------------------------
//
// `breaks` comes from `/tracks` as indices into `points` where a new session
// starts, measured server-side on the full-resolution series (see
// `AIS_SESSION_BREAK_HOURS`). This turns them into inclusive [first, last]
// index pairs.
//
// A single-fix session is dropped: one point cannot be interpolated, and
// drawing a vessel for the one instant she reported and nowhere either side of
// it would be a flicker, not a position. She is still projected — the
// projection endpoint works from a single fix, which is exactly the case it is
// for.
function sessionsOf(tr) {
  const pts = tr.points || [];
  const breaks = tr.breaks || [];
  const out = [];
  let s = 0;
  for (const b of breaks) {
    if (b > s) out.push([s, b - 1]);
    s = b;
  }
  if (pts.length - 1 > s) out.push([s, pts.length - 1]);
  return out.filter(([a, b]) => b > a);
}

//: The session the clock falls inside, as [first, last] indices, or null if she
//: was not broadcasting then. Linear over sessions on purpose: the corpus
//: averages under two per hull and the median vessel has one, so a binary
//: search would be more code for no measurable difference.
function sessionAt(tr, tSec) {
  if (tSec == null) return null;
  const pts = tr.points;
  for (const s of tr.sessions || []) {
    if (tSec >= pts[s[0]][2] && tSec <= pts[s[1]][2]) return s;
  }
  return null;
}

//: Index of the last fix at or before `tSec`, within [lo, hi]. Returns `lo`
//: when the clock sits on the session's first fix.
function fixBefore(points, lo, hi, tSec) {
  let a = lo, b = hi;
  while (a < b - 1) {
    const mid = (a + b) >> 1;
    if (points[mid][2] <= tSec) a = mid;
    else b = mid;
  }
  return a;
}

// ---- interpolation: a vessel's [lon,lat] at epoch-seconds t, or null when she
//      was not broadcasting then. ----
//
// **The session gate is the correctness fix, not an optimisation.** This used
// to interpolate between whichever two fixes bracketed the clock anywhere in
// the track, which across a silence draws a vessel steaming smoothly along a
// line nobody observed — 206 of the gaps in the landed corpus are over six
// hours and the longest is five days. During a gap the honest answer to "where
// is she" is that we do not know, and the honest picture is that she is not
// drawn.
function posAt(tr, tSec) {
  const s = sessionAt(tr, tSec);
  if (!s) return null;
  const points = tr.points;
  const lo = fixBefore(points, s[0], s[1], tSec);
  const a = points[lo], b = points[Math.min(lo + 1, s[1])];
  const span = b[2] - a[2] || 1;
  const f = Math.max(0, Math.min(1, (tSec - a[2]) / span));
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

//: The epoch-second at which the most vessels are simultaneously broadcasting —
//: a sweep over session start/end events, so it is one sort rather than a scan
//: per candidate instant. Ties go to the earliest such moment.
//:
//: Counts SESSIONS, not tracks. Counting whole tracks put the busiest instant
//: wherever the most hulls had a first and a last fix either side of it,
//: silences included — so the playhead could park on a moment when half the
//: "live" fleet had not been heard from in days.
function busiestInstant(tracks) {
  const marks = [];
  for (const tr of tracks) {
    for (const [a, b] of tr.sessions || []) {
      marks.push([tr.points[a][2], 1], [tr.points[b][2], -1]);
    }
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
    // Null when the alert falls inside a silence, which is correct and is the
    // whole reason the session gate exists: "she was here when we flagged her"
    // must not be answered by interpolating across a gap. The cascade below
    // then falls through to a labelled proxy rather than to an invention.
    const pos = tr && posAt(tr, tSec);
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
    const pos = posAt(tr, clockSec);
    if (!pos) continue;
    feats.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: pos },
      properties: { vessel_id: tr.vessel_id },
    });
  }
  upsertCircleLayer(m, "vessels", feats, themeColor("--mark-vessel", VESSEL_COLOR),
                    on, onSelect, MARK_STYLE.live);
}

// ---- the trail: where she has been on this trip ---------------------------
//
// One line per broadcasting vessel, running from the start of the session she
// is currently in to where she is on the clock — so it grows behind her as the
// animation plays and disappears the moment she stops reporting.
//
// **Bounded by the session, not by the window.** The layer this replaces drew
// every vessel's whole eight-week polyline whether or not the clock was
// anywhere near it, which is a picture of the corpus rather than of the sea:
// two hundred static lines that said nothing about when, crossed everything,
// and never changed. What an operator asks of a vessel on a moving map is
// "where has she come from", and the answer to that is bounded by the trip she
// is on.
//
// The trail is drawn to the interpolated position rather than to her last fix,
// so its head sits under the vessel mark instead of trailing it by up to a
// decimated sample.
// **The selected vessel always gets hers**, whatever the fleet-wide switch
// says. The map opens showing positions only, and "click a ship to see where
// she has been and where she is going" has to work from that state without the
// operator first finding a checkbox — the click IS the request.
function renderTrails(m, tracks, clockSec, on, selected) {
  const feats = [];
  if (clockSec != null) {
    for (const tr of tracks) {
      if (!on && tr.vessel_id !== selected) continue;
      const s = sessionAt(tr, clockSec);
      if (!s) continue;
      const pts = tr.points;
      const i = fixBefore(pts, s[0], s[1], clockSec);
      const coords = [];
      for (let k = s[0]; k <= i; k++) coords.push([pts[k][0], pts[k][1]]);
      const head = posAt(tr, clockSec);
      if (head) coords.push(head);
      if (coords.length < 2) continue;
      feats.push({
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords },
        properties: { vessel_id: tr.vessel_id },
      });
    }
  }
  // Visible whenever there is anything in it. Keying visibility off `on` would
  // build the selected vessel's trail and then hide the layer holding it.
  upsertTrailLayer(m, "trails", feats, feats.length > 0);
}

//: `line-gradient` fades each trail toward the start of its own session, so on
//: a trawler that has worked one ground for four days the recent movement is
//: still the part that reads. It needs `lineMetrics` on the source, and it
//: cannot be combined with a data-driven colour — which is fine, every trail
//: means the same thing.
function upsertTrailLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc, lineMetrics: true });
    m.addLayer({
      id, type: "line", source: id,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-width": 1.6,
        "line-color": themeColor("--mark-trail", TRAIL_COLOR),
        "line-gradient": [
          "interpolate", ["linear"], ["line-progress"],
          0, `rgba(0,0,0,0)`,
          TRAIL_FADE, themeColor("--mark-trail", TRAIL_COLOR),
          1, themeColor("--mark-trail", TRAIL_COLOR),
        ],
      },
    });
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

// ---- the projection: where she is predicted to go -------------------------
//
// Drawn from `/predictions` (ADR-039), which computes it with the same module
// the rest of the system reasons about departures with. Three marks:
//
//   * a **dashed line** from her position now to where dead reckoning puts her
//     three hours on. Dashed because it has not happened: solid behind the
//     vessel and dashed ahead of her is the one convention on this map that
//     needs no key entry to read.
//   * a **hollow ring** at the far end — the predicted position itself, which
//     is the actual assertion the line is a path to.
//   * the **uncertainty cone**, on the selected vessel only. A hundred
//     overlapping circles is not a picture of uncertainty, it is a fog; the
//     cone belongs on the hull the operator is asking about, which is what
//     selecting one means. The radius comes from the API — it is the cone
//     `tracks/projection.py` computed, not one re-derived here.
//
// **The selected vessel is drawn whatever the fleet-wide switch says**, for
// the same reason her trail is: the map opens on positions alone, and clicking
// a hull is the operator asking where she has been and where she is going.
function renderPredictions(m, predictions, selected, on, coneOn) {
  const items = (predictions && predictions.items) || [];
  const lines = [];
  const ends = [];
  const cones = [];
  {
    for (const p of items) {
      const mine = p.vessel_id === selected;
      if (!on && !mine) continue;
      const path = p.path || [];
      if (path.length < 2) continue;
      lines.push({
        type: "Feature",
        geometry: { type: "LineString",
                    coordinates: path.map((q) => [q[0], q[1]]) },
        properties: { vessel_id: p.vessel_id },
      });
      const last = path[path.length - 1];
      ends.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [last[0], last[1]] },
        properties: {
          vessel_id: p.vessel_id,
          label: `Predicted position · ${fmtDateTime(
            new Date(last[2] * 1000).toISOString())} · within `
            + `${(last[3] / 1852).toFixed(1)} nm · dead reckoning from `
            + `${p.from.sog_kn} kn on ${p.from.cog_deg}°`
            + (p.stale_minutes >= 30
              ? ` · last heard ${Math.round(p.stale_minutes)} min ago`
              : ""),
        },
      });
      // The cone follows the selection, and it is drawn for her whether or not
      // the fleet-wide cone switch is on — that switch exists to put cones on
      // everybody, which is a fog, not to withhold one from the hull under the
      // question.
      if (mine || (coneOn && on)) {
        // One ring per projected step: the cone is not a single circle, it is
        // a circle that widens along the path, and drawing only the end of it
        // would hide that the near end is tight.
        for (const q of path) {
          if (q[3] < 200) continue;   // tighter than the mark, nothing to draw
          cones.push({
            type: "Feature",
            geometry: { type: "Polygon",
                        coordinates: [circleRing(q[1], q[0], q[3] / 1000)] },
            properties: { confidence: q[4] },
          });
        }
      }
    }
  }
  // Each layer is visible when it holds something, rather than when its switch
  // is on: with the switches off and a hull selected these hold exactly her.
  upsertPredictionLayer(m, "prediction-line", lines, lines.length > 0);
  upsertCircleLayer(m, "prediction-end", ends,
                    themeColor("--mark-predicted", PREDICT_COLOR),
                    ends.length > 0, () => {}, MARK_STYLE.predicted);
  upsertConeLayer(m, "prediction-cone", cones, cones.length > 0);
}

function upsertPredictionLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    m.addLayer({
      id, type: "line", source: id,
      paint: {
        "line-color": themeColor("--mark-predicted", PREDICT_COLOR),
        "line-width": 1.4,
        "line-opacity": 0.85,
        "line-dasharray": [2, 2],
      },
    });
  }
  if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
}

function upsertConeLayer(m, id, feats, on) {
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource(id);
  if (src) src.setData(fc);
  else {
    m.addSource(id, { type: "geojson", data: fc });
    // Under the AOI outline and therefore under every mark: a cone is the
    // uncertainty around a prediction, and one painted over the traffic it
    // describes would hide the vessels an operator is comparing it against.
    const under = m.getLayer("aoi-line") ? "aoi-line" : undefined;
    m.addLayer({
      id: `${id}-fill`, type: "fill", source: id,
      paint: {
        "fill-color": themeColor("--mark-predicted", PREDICT_COLOR),
        "fill-opacity": 0.06,
      },
    }, under);
    m.addLayer({
      id: `${id}-line`, type: "line", source: id,
      paint: {
        "line-color": themeColor("--mark-predicted", PREDICT_COLOR),
        "line-width": 0.8, "line-opacity": 0.35,
      },
    }, under);
  }
  for (const suff of ["-fill", "-line"]) {
    if (m.getLayer(id + suff))
      m.setLayoutProperty(id + suff, "visibility", on ? "visible" : "none");
  }
}

// **Which one is selected, on the map.** Clicking a vessel opened her record
// and left every mark on screen looking identical, so the panel described a
// hull the operator could no longer point at. On a picture holding hundreds of
// dots that is the difference between a selection and a guess.
//
// Drawn as its own layer above the marks rather than by recolouring one of
// them: colour on this map is meaning (blue is a broadcasting vessel, red is an
// alert) and a selected vessel is still a vessel. A ring adds emphasis without
// spending a hue, and it reads the same whichever layer the subject came from,
// which matters because the selection can be a vessel, a radar contact or an
// alert marker.
function renderSelection(m, id, tracks, clockSec, data) {
  let pos = null;
  const tr = id && tracks.find((t) => t.vessel_id === id);
  // Her position now if she is broadcasting; her last known fix otherwise. The
  // ring is "which one are we talking about", so it has to survive the subject
  // going quiet — that is frequently the moment the operator selected her.
  if (tr) pos = posAt(tr, clockSec) || tr.points[tr.points.length - 1]?.slice(0, 2);
  if (!pos && id) {
    const key = String(id).startsWith("contact:radar:")
      ? String(id).slice("contact:radar:".length) : null;
    const rc = key && data.radarContacts.find((c) => c.radar_track_id === key);
    if (rc && rc.lat != null) pos = [rc.lon, rc.lat];
  }
  if (!pos && id) {
    const ev = data.events.find((e) => e.vessel_id === id && e.lat != null);
    if (ev) pos = [ev.lon, ev.lat];
  }
  const feats = pos
    ? [{ type: "Feature", geometry: { type: "Point", coordinates: pos },
         properties: {} }]
    : [];
  const fc = { type: "FeatureCollection", features: feats };
  const src = m.getSource("selection");
  if (src) { src.setData(fc); return; }
  m.addSource("selection", { type: "geojson", data: fc });
  m.addLayer({
    id: "selection-halo", type: "circle", source: "selection",
    paint: {
      "circle-radius": 15,
      "circle-color": themeColor("--mark-selected", "#0b7ea8"),
      "circle-opacity": 0.16,
      "circle-stroke-width": 2,
      "circle-stroke-color": themeColor("--mark-selected", "#0b7ea8"),
      "circle-stroke-opacity": 0.95,
    },
  });
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

  // NB: the whole-corpus track polylines that used to be drawn here are gone.
  // See `renderTrails` — a vessel's line is now bounded by the session she is
  // in and by the clock, and there is no layer that draws where every hull went
  // over eight weeks all at once.

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
          + `${a.subject_name || ""}. ${at.basis}`,
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
        {!enough && ". Three or more makes an area"}.
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
          `Radar station ${s.station_id} · ${s.name}. Holds a small craft to ` +
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
          : `Suppressed: ${String(c.status || "").replace("suppressed_", "").replace(/_/g, " ")}`) +
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
        "circle-stroke-color": style.ownStroke ? color : style.strokeColor,
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
