// The vessel entity view — the heart of the demo. Rendered as a slide-over on
// the map and as a full page under /vessels/:id. Identity + a legible identity
// TIMELINE, sanctions with explicit tier/confidence, port calls / encounters /
// gaps with GFW attribution, and a DECOMPOSED risk score. Everything degrades
// gracefully when a field is null.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import {
  fmtDate,
  fmtDateTime,
  num,
  edgeTypeLabel,
} from "../lib/format.js";
import {
  Attribution,
  NAtext,
  ProvChip,
  RiskDecomposition,
  SanctionsBadge,
  SyntheticBadge,
  Value,
} from "./bits.jsx";

export function VesselPanel({ vesselId, onOpenGraph }) {
  const [v, setV] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let live = true;
    setV(null);
    setErr(null);
    api
      .vessel(vesselId)
      .then((d) => live && setV(d))
      .catch((e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, [vesselId]);

  if (err) return <div className="empty">Could not load vessel: {err}</div>;
  if (!v) return <div className="empty">Loading…</div>;

  const c = v.current;
  return (
    <div>
      <div className="section">
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <h2>{c.name || <span className="na">unnamed vessel</span>}</h2>
          <SyntheticBadge on={v.is_synthetic} />
          <SanctionsBadge
            sanctioned={v.sanctions?.length > 0}
            isFinding={v.sanctions?.some((s) => s.is_finding)}
            tier={v.sanctions?.[0]?.match_tier}
          />
        </div>
        <dl className="kv" style={{ marginTop: 12 }}>
          <dt>MMSI</dt>
          <dd className="mono"><Value v={c.mmsi} /></dd>
          <dt>IMO</dt>
          <dd className="mono"><Value v={c.imo} /></dd>
          <dt>Flag</dt>
          <dd><Value v={c.flag} /></dd>
          <dt>Type</dt>
          <dd><Value v={c.vessel_class} /></dd>
          <dt>Call sign</dt>
          <dd className="mono"><Value v={c.call_sign} /></dd>
          <dt>Dimensions</dt>
          <dd>
            {c.length_m || c.width_m ? (
              <span>
                {c.length_m ? `${num(c.length_m, 0)} m` : "?"} ×{" "}
                {c.width_m ? `${num(c.width_m, 0)} m` : "?"}
              </span>
            ) : (
              <NAtext />
            )}
          </dd>
          <dt>Tonnage</dt>
          <dd><Value v={c.tonnage_gt && num(c.tonnage_gt, 0)} suffix=" GT" /></dd>
        </dl>
        <div style={{ marginTop: 10 }}>
          <ProvChip prov={v.prov} />
        </div>
      </div>

      {/* risk */}
      <div className="section">
        <div className="eyebrow">Risk, decomposed</div>
        <RiskDecomposition risk={v.risk} />
      </div>

      {/* sanctions */}
      {v.sanctions?.length > 0 && (
        <div className="section">
          <div className="eyebrow">Sanctions</div>
          {v.sanctions.map((s, i) => (
            <div className="card" style={{ padding: 12, marginBottom: 8 }} key={i}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <SanctionsBadge sanctioned isFinding={s.is_finding} tier={s.match_tier} />
                <span className="mono muted" style={{ fontSize: 12 }}>
                  tier {s.match_tier} · confidence {num(s.confidence, 2)}
                </span>
              </div>
              <dl className="kv" style={{ marginTop: 8 }}>
                <dt>OFAC name</dt>
                <dd><Value v={s.ofac_name} /></dd>
                <dt>Programme</dt>
                <dd><Value v={s.ofac_program} /></dd>
                <dt>Listed owner</dt>
                <dd><Value v={s.ofac_owner} /></dd>
                <dt>As of</dt>
                <dd>{fmtDate(s.sanctions_as_of) || <NAtext />}</dd>
              </dl>
              {!s.is_finding && (
                <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
                  Name-only match — a candidate, not a confirmed finding. Names collide;
                  only an IMO or call-sign+name match is treated as a finding.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* identity history timeline */}
      <div className="section">
        <div className="eyebrow">Identity history</div>
        {v.identity_history?.length <= 1 ? (
          <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
            One identity record on file — no recorded rename or reflag. (Real GFW
            identity history averages ~1 record per vessel.)
          </p>
        ) : (
          <div className="timeline" style={{ marginTop: 8 }}>
            {v.identity_history.map((iv, i) => (
              <div className="tl-item" key={i}>
                <div className="tl-rail">
                  <span className={`tl-dot ${iv.valid_to ? "past" : ""}`} />
                  <span className="tl-line" />
                </div>
                <div className="tl-body">
                  <div style={{ fontWeight: 600 }}>
                    {iv.name || <span className="na">unnamed</span>}
                    {iv.flag ? ` · ${iv.flag}` : ""}
                  </div>
                  <div className="mono muted" style={{ fontSize: 12 }}>
                    MMSI {iv.mmsi || "—"} · IMO {iv.imo || "—"}
                  </div>
                  <div className="tl-when">
                    {fmtDate(iv.valid_from) || "?"} →{" "}
                    {iv.valid_to ? fmtDate(iv.valid_to) : "current"}
                    {iv.superseded ? " · superseded" : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <EventList title="Port calls" events={v.port_calls} kind="port" />
      <EventList title="Encounters" events={v.encounters} kind="encounter" />
      <EventList title="AIS gaps" events={v.gaps} kind="gap" />

      <div className="section">
        <button className="btn" onClick={() => onOpenGraph?.(vesselId)}>
          Open graph neighbourhood →
        </button>{" "}
        <Link className="btn" to={`/vessels/${encodeURIComponent(vesselId)}`}>
          Full entity page
        </Link>
      </div>
    </div>
  );
}

function EventList({ title, events, kind }) {
  if (!events || events.length === 0) {
    return (
      <div className="section">
        <div className="eyebrow">{title}</div>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>None on record.</p>
      </div>
    );
  }
  return (
    <div className="section">
      <div className="eyebrow">
        {title} <span className="muted">({events.length})</span>
      </div>
      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
        {events.slice(0, 20).map((e, i) => (
          <div className="card" style={{ padding: "9px 12px" }} key={i}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
              <span style={{ fontWeight: 600 }}>
                {kind === "port"
                  ? e.place || <span className="na">unnamed anchorage</span>
                  : kind === "encounter"
                  ? `with ${e.counterpart_name || "an unnamed vessel"}`
                  : e.classification}
              </span>
              <Attribution source={e.attribution} />
            </div>
            <div className="mono muted" style={{ fontSize: 12, marginTop: 3 }}>
              {fmtDateTime(e.start_time) || "?"}
              {e.duration_hours ? ` · ${num(e.duration_hours, 1)} h` : ""}
              {e.distance_from_shore_km != null
                ? ` · ${num(e.distance_from_shore_km, 1)} km offshore`
                : ""}
            </div>
            {kind === "gap" && (
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                {e.classification} — this is GFW's assessment, not our detection.
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
