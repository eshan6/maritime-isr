// The answer to "draw a box anywhere — who was in it?" (ADR-030).
//
// Two things this panel refuses to blur:
//
//   * **What kind of geometry you are looking at.** A published boundary, a
//     working circle this project drew at the right scale, and a box the
//     operator drew thirty seconds ago all live in one layer, and the panel
//     shows the authority and the caveat on every one. A 10 km circle labelled
//     "Mumbai port area" must never read as a declared port limit.
//   * **Where the answer came from.** `landed` means the pipeline computed
//     these transitions; `computed` means they were worked out just now from
//     raw positions and cover AIS only; `computed-empty` means we looked and
//     found nobody. "Nobody was here" and "we have not looked" are different
//     sentences and the panel says which one it is saying.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtDateTime, num } from "../lib/format.js";

const KIND_LABEL = {
  eez: "Exclusive economic zone",
  contiguous_zone: "Contiguous zone",
  territorial_sea: "Territorial sea",
  imbl: "Maritime boundary line",
  port_limit: "Port area",
  anchorage: "Anchorage",
  oil_terminal: "Terminal / SPM",
  shipping_lane: "Shipping lane",
  sensitive_area: "Sensitive area",
  geofence: "Drawn area",
};

//: Compass point from a bearing — "entering from where and leaving to where"
//: is the requirement, and 229° is not an answer a watchkeeper reads at a
//: glance. The degrees stay in the tooltip.
const POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
function compass(deg) {
  if (deg === null || deg === undefined) return null;
  return POINTS[Math.round((deg % 360) / 22.5) % 16];
}

export function ZonePanel({ zone, onDelete }) {
  const [res, setRes] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setRes(null);
    setError(null);
    if (!zone) return;
    api
      .zoneVessels(zone.zone_id, { limit: 400 })
      .then((r) => live && setRes(r))
      .catch((e) => live && setError(String(e.message || e)));
    return () => { live = false; };
  }, [zone?.zone_id]);

  if (!zone) return null;
  const drawn = zone.authority === "operator";

  return (
    <div className="prose">
      <div className="eyebrow">{KIND_LABEL[zone.kind] || zone.kind}</div>
      <h3 style={{ margin: "3px 0 10px" }}>{zone.name}</h3>

      {/* The provenance claim, always, never behind a click. */}
      <div className="notebar" style={{ marginBottom: 12 }}>
        <div>
          <span className="muted">Authority:</span> {zone.authority}
          {"  ·  "}
          <span className="muted">confidence</span> {num(zone.confidence, 2)}
        </div>
        <div>
          <span className="muted">How it was made:</span> {zone.method}
        </div>
        {zone.note && (
          <div style={{ marginTop: 5, color: "var(--amber)" }}>
            {zone.note}
          </div>
        )}
      </div>

      {error && <div className="notebar">Could not load. {error}</div>}
      {!res && !error && <div className="empty">Working…</div>}

      {res && (
        <>
          <div className="t-med" style={{ marginBottom: 8 }}>
            {res.n_vessels} vessel{res.n_vessels === 1 ? "" : "s"},{" "}
            {res.items.length} visit{res.items.length === 1 ? "" : "s"}
          </div>
          <BasisLine basis={res.basis} note={res.note} />

          {res.items.length === 0 && res.basis === "computed-empty" && (
            <div className="notebar">
              We looked and found nobody in this area. That is a result, not an
              empty page.
            </div>
          )}

          <div style={{ maxHeight: 340, overflowY: "auto", marginTop: 8 }}>
            {res.items.map((v, i) => (
              <VisitRow key={i} v={v} />
            ))}
          </div>
        </>
      )}

      {drawn && (
        <button
          className="btn btn-sm"
          style={{ marginTop: 12 }}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.deleteGeofence(zone.zone_id);
              onDelete?.(zone.zone_id);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Deleting…" : "Delete this area"}
        </button>
      )}
    </div>
  );
}

function BasisLine({ basis, note }) {
  const tone =
    basis === "landed" ? "var(--green)"
      : basis === "none" ? "var(--red)" : "var(--amber)";
  const label = {
    landed: "from the computed transition table",
    computed: "computed on demand, just now",
    "computed-empty": "computed on demand, just now",
    none: "not yet computed",
  }[basis] || basis;
  return (
    <div className="t-meta" style={{ color: tone, marginBottom: 8 }}>
      ● {label}
      {note && (
        <div className="muted" style={{ marginTop: 3 }}>
          {note}
        </div>
      )}
    </div>
  );
}

function VisitRow({ v }) {
  const inFrom = compass(v.entry_bearing_deg);
  const outTo = compass(v.exit_bearing_deg);
  return (
    <div
      className="t-meta"
      style={{ borderTop: "1px solid var(--border)", padding: "7px 0" }}
    >
      <div>
        <span className="mono t-med">
          {v.mmsi ? `MMSI ${v.mmsi}` : v.track_key}
        </span>{" "}
        <span className="muted">({v.track_source})</span>
      </div>
      <div className="muted">
        {fmtDateTime(v.t_enter)} → {v.t_exit ? fmtDateTime(v.t_exit) : "still inside"}
        {v.dwell_min != null && ` · ${fmtDwell(v.dwell_min)}`}
      </div>
      <div>
        {/* Censoring is stated, not silently dropped: an entry we did not see
            is not an entry from anywhere. */}
        {v.entry_censored ? (
          <span className="muted">already inside when the track began</span>
        ) : (
          inFrom && (
            <span title={`${v.entry_bearing_deg}°`}>in from the {opposite(inFrom)}</span>
          )
        )}
        {!v.exit_censored && outTo && (
          <span title={`${v.exit_bearing_deg}°`}>
            {" · "}out to the {outTo}
          </span>
        )}
        {v.exit_censored && !v.entry_censored && (
          <span className="muted"> · still inside at the last fix</span>
        )}
      </div>
    </div>
  );
}

//: A vessel travelling ON 045 came FROM the south-west. Getting this backwards
//: would reverse every entry direction in the panel, which is the one thing
//: "entering from where" must not do.
function opposite(point) {
  const i = POINTS.indexOf(point);
  return i < 0 ? point : POINTS[(i + 8) % 16];
}

function fmtDwell(min) {
  if (min < 90) return `${Math.round(min)} min`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}
