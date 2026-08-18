// The vessel entity view — the heart of the demo. Rendered as a slide-over on
// the map and as a full page under /vessels/:id. Identity + a legible identity
// TIMELINE, sanctions with explicit tier/confidence, port calls / encounters /
// gaps with GFW attribution, and a DECOMPOSED risk score. Everything degrades
// gracefully when a field is null.
//
// Type here follows the shared scale in theme.css and nothing else: no inline
// font sizes, no italics, one family. Hierarchy is the section rule, the
// eyebrow, and the weight of a value against its label.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate, fmtDateTime, num } from "../lib/format.js";
import {
  Attribution,
  ExportButton,
  NAtext,
  ProvChip,
  RiskDecomposition,
  SanctionsBadge,
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
        <div className="entity-head">
          <h2>{c.name || <span className="na">Unnamed vessel</span>}</h2>
          <SanctionsBadge
            sanctioned={v.sanctions?.length > 0}
            isFinding={v.sanctions?.some((s) => s.is_finding)}
            tier={v.sanctions?.[0]?.match_tier}
          />
        </div>
        <dl className="kv" style={{ marginTop: 14 }}>
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
              <span className="mono">
                {c.length_m ? `${num(c.length_m, 0)} m` : "?"} ×{" "}
                {c.width_m ? `${num(c.width_m, 0)} m` : "?"}
              </span>
            ) : (
              <NAtext />
            )}
          </dd>
          <dt>Tonnage</dt>
          <dd className="mono">
            <Value v={c.tonnage_gt && num(c.tonnage_gt, 0)} suffix=" GT" />
          </dd>
        </dl>
        <div style={{ marginTop: 12 }}>
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
          <div className="eyebrow">
            Sanctions <span className="count">({v.sanctions.length})</span>
          </div>
          {v.sanctions.map((s, i) => (
            <div className="card card-pad" style={{ marginBottom: 8 }} key={i}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <SanctionsBadge sanctioned isFinding={s.is_finding} tier={s.match_tier} />
                <span className="mono muted t-meta">
                  tier {s.match_tier} · confidence {num(s.confidence, 2)}
                </span>
              </div>
              <dl className="kv" style={{ marginTop: 10 }}>
                <dt>OFAC name</dt>
                <dd><Value v={s.ofac_name} /></dd>
                <dt>Programme</dt>
                <dd><Value v={s.ofac_program} /></dd>
                <dt>Listed owner</dt>
                <dd><Value v={s.ofac_owner} /></dd>
                <dt>As of</dt>
                <dd className="mono">{fmtDate(s.sanctions_as_of) || <NAtext />}</dd>
              </dl>
              {!s.is_finding && (
                <p className="muted t-meta" style={{ margin: "8px 0 0" }}>
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
          <p className="section-note">
            One identity record on file — no recorded rename or reflag. (Real GFW
            identity history averages about one record per vessel.)
          </p>
        ) : (
          <div className="timeline">
            {v.identity_history.map((iv, i) => (
              <div className="tl-item" key={i}>
                <div className="tl-rail">
                  <span className={`tl-dot ${iv.valid_to ? "past" : ""}`} />
                  <span className="tl-line" />
                </div>
                <div className="tl-body">
                  <div className="t-med">
                    {iv.name || <span className="na">Unnamed</span>}
                    {iv.flag ? ` · ${iv.flag}` : ""}
                  </div>
                  <div className="mono muted t-meta">
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
        {/* The export sits on the vessel itself, not only on the findings row:
            an analyst establishing whether a hull is worth flagging needs to be
            able to hand over what is known about it, and the report is
            available for any vessel rather than only for flagged ones. */}
        <div className="btn-group">
          <ExportButton id={vesselId} primary label="Export incident report" />
          <button className="btn btn-sm" onClick={() => onOpenGraph?.(vesselId)}>
            Open graph neighbourhood →
          </button>
          <Link className="btn btn-sm" to={`/vessels/${encodeURIComponent(vesselId)}`}>
            Full entity page
          </Link>
        </div>
      </div>
    </div>
  );
}

function EventList({ title, events, kind }) {
  if (!events || events.length === 0) {
    return (
      <div className="section">
        <div className="eyebrow">{title}</div>
        <p className="section-note">None on record.</p>
      </div>
    );
  }
  return (
    <div className="section">
      <div className="eyebrow">
        {title} <span className="count">({events.length})</span>
      </div>
      <div>
        {events.slice(0, 20).map((e, i) => (
          <div className="event-row" key={i}>
            <div className="event-title">
              <span>
                {kind === "port"
                  ? e.place || <span className="na">Unnamed anchorage</span>
                  : kind === "encounter"
                  ? `with ${e.counterpart_name || "an unnamed vessel"}`
                  : e.classification}
              </span>
              <Attribution source={e.attribution} />
            </div>
            <div className="event-meta mono">
              {fmtDateTime(e.start_time) || "?"}
              {e.duration_hours ? ` · ${num(e.duration_hours, 1)} h` : ""}
              {e.distance_from_shore_km != null
                ? ` · ${num(e.distance_from_shore_km, 1)} km offshore`
                : ""}
            </div>
            {kind === "gap" && (
              <div className="event-meta">
                {e.classification} — this is GFW's assessment, not our detection.
              </div>
            )}
          </div>
        ))}
        {events.length > 20 && (
          <p className="section-note" style={{ marginTop: 8 }}>
            Showing the first 20 of {events.length}.
          </p>
        )}
      </div>
    </div>
  );
}
