// Findings — the ranked table, and the primary answer to "what did you find?"
//
// This is the screen `graph_report.py` concluded the landed data supports. On
// the real corpus the encounter graph is star-shaped (14 encounters across
// 9,184 vessels; 0 of 126 sanctions-matched hulls with an encounter neighbour),
// so a network view has nothing to draw and a ranked list does.
//
// Two rules the layout exists to enforce:
//
//   1. **Attribution is not a footnote.** A GFW gap assessment is GFW's finding
//      carried through, and a sanctions designation is OFAC/UN/EU's. Ours is the
//      identity match between them. Every row says so on its face, because the
//      sentence an operator repeats afterwards is the one on screen.
//   2. **The rank is its own explanation.** There is no blended score here. The
//      priority number is a sum of named signals, and every signal that moved a
//      row up is listed under it in plain English.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate, num, shortId } from "../lib/format.js";
import { ExportButton, NAtext } from "../components/bits.jsx";

const KIND_LABEL = {
  encounter: "encounters",
  loitering: "loitering events",
  port_visit: "port calls",
  gap: "AIS gaps",
};

export function FindingsView() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState({});
  const nav = useNavigate();

  useEffect(() => {
    api.findings({ limit: 500 }).then(setData).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="pad"><div className="empty">{err}</div></div>;
  if (!data) return <div className="pad"><div className="empty">Loading findings…</div></div>;

  // Every collection here is read defensively. A payload missing an optional
  // key — `notes` on a corpus with nothing to caveat, `basis_legend` from an
  // older build — used to throw inside render and white-screen the whole view,
  // turning a missing sentence into "the product is broken".
  const total = (data.count?.real || 0) + (data.count?.synthetic || 0);
  const notes = data.notes || [];
  const items = data.items || [];
  const basisLegend = data.basis_legend || [];

  return (
    <div className="scroll-y">
      {/* Capped, because these cards are mostly prose: the headline sentence is
          the product, and a headline set across 1,300 px is a line the eye
          loses on the way back. */}
      <div className="pad" style={{ maxWidth: 1000 }}>
        <div className="page-head">
          <div className="eyebrow">Findings</div>
          <h2>{total} vessel{total === 1 ? "" : "s"} worth an analyst's time</h2>
        </div>

        {/* The honesty block. Deliberately above the table, not below it. */}
        <div className="card card-pad" style={{ marginBottom: 14 }}>
          {notes.map((n, i) => (
            <p key={i} className="muted t-meta" style={{ margin: i ? "8px 0 0" : 0 }}>
              {n}
            </p>
          ))}
        </div>

        {items.length === 0 && (
          <div className="empty">
            Nothing in this corpus clears the bar for a finding. That is a result,
            not an error — a name-only sanctions match is a lead for the Vessels
            table, and putting leads here is how an alert queue stops being read.
          </div>
        )}

        {items.map((f) => {
          const isOpen = !!open[f.id];
          const sanctions = f.sanctions || [];
          const findings = sanctions.filter((s) => s.is_finding);
          const candidates = sanctions.filter((s) => !s.is_finding);
          return (
            <div className="card card-pad" key={f.id} style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                <span className="t-lead">{f.name || shortId(f.id)}</span>
                {f.has_dark_gap && (
                  <span className="badge badge-finding" title="Global Fishing Watch's assessment, not ours">
                    GFW: intentional AIS disabling
                  </span>
                )}
                {/* The badge must not outrun the sentence under it. When the
                    designation names the owning company rather than the hull,
                    "SANCTIONS FINDING" reads as "this ship is listed" — which
                    is precisely what the headline goes on to deny. */}
                {f.sanctions_is_finding && (
                  <span className="badge badge-finding">
                    {(f.registries || []).join(" + ")}{" "}
                    {findings.every((s) => s.listed_entity_type === "organisation")
                      ? "designated owner"
                      : "sanctions finding"}
                  </span>
                )}
                <div className="nav-spacer" />
                <span className="mono muted t-meta" title="sum of the named signals below — not a probability">
                  priority {f.priority}
                </span>
              </div>

              {/* The plain-English sentence. This is the product (CLAUDE.md §0). */}
              <p className="t-body" style={{ margin: "10px 0 12px" }}>
                {f.headline}
              </p>

              <div className="kv">
                <span className="field-label">MMSI</span>
                <span className="mono">{f.mmsi || <NAtext />}</span>
                <span className="field-label">IMO</span>
                <span className="mono">{f.imo || <NAtext />}</span>
                <span className="field-label">Flag</span>
                <span>{f.flag || <NAtext />}</span>
                <span className="field-label">Type</span>
                <span>{f.vessel_type || <NAtext />}</span>
              </div>

              <div className="muted t-meta" style={{ marginTop: 10 }}>
                {Object.entries(f.event_counts || {})
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => `${n} ${KIND_LABEL[k] || k}`)
                  .join(" · ") || "no behaviour events observed"}
                {f.ports?.length > 0 && <> · called at {f.ports.join(", ")}</>}
              </div>

              <div className="btn-group" style={{ marginTop: 10 }}>
                <button
                  className="btn btn-sm"
                  onClick={() => setOpen((o) => ({ ...o, [f.id]: !isOpen }))}
                >
                  {isOpen ? "Hide evidence" : "Why this is here"}
                </button>
                <ExportButton id={f.id} primary />
                <button className="btn btn-sm" onClick={() => nav(`/vessels/${encodeURIComponent(f.id)}`)}>
                  Open vessel
                </button>
                <button className="btn btn-sm" onClick={() => nav(`/graph?seed=${encodeURIComponent(f.id)}`)}>
                  Open in graph
                </button>
              </div>

              {isOpen && (
                <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div className="eyebrow">Why it ranks here</div>
                  <ul className="prose t-meta" style={{ margin: "8px 0 14px", paddingLeft: 18 }}>
                    {(f.basis || []).map((b) => (
                      <li key={b.signal}>
                        {b.explanation} <span className="mono muted">(+{b.weight})</span>
                      </li>
                    ))}
                  </ul>

                  {f.dark_gaps?.length > 0 && (
                    <>
                      <div className="eyebrow">AIS gaps assessed by Global Fishing Watch</div>
                      <table className="table" style={{ marginBottom: 12 }}>
                        <thead>
                          <tr>
                            <th>From</th><th>To</th><th className="num">Hours</th>
                            <th className="num">Distance</th><th className="num">From shore</th>
                          </tr>
                        </thead>
                        <tbody>
                          {f.dark_gaps.map((g, i) => (
                            <tr key={i}>
                              <td className="mono">{fmtDate(g.start_time, true) || <NAtext />}</td>
                              <td className="mono">{fmtDate(g.end_time, true) || <NAtext />}</td>
                              <td className="num mono">{num(g.duration_hours, 1) ?? "—"}</td>
                              <td className="num mono">{num(g.distance_km, 1, " km") ?? "—"}</td>
                              <td className="num mono">{num(g.distance_from_shore_km, 0, " km") ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="muted t-meta" style={{ marginTop: -6, marginBottom: 12 }}>
                        {f.dark_gaps[0].attribution}. We did not compute this and
                        have no receiver-coverage model at these positions — a
                        silence where we cannot hear is not evidence of intent.
                      </p>
                    </>
                  )}

                  {findings.length > 0 && (
                    <>
                      <div className="eyebrow">Sanctions designations</div>
                      <table className="table" style={{ marginBottom: candidates.length ? 12 : 0 }}>
                        <thead>
                          <tr>
                            <th>Registry</th><th>Listed as</th><th>Programme</th>
                            <th>Designated</th><th>Matched on</th><th className="num">Conf.</th>
                          </tr>
                        </thead>
                        <tbody>
                          {findings.map((s, i) => (
                            <tr key={i}>
                              <td>{s.registry || "OFAC"}</td>
                              <td>
                                {s.ofac_name || <NAtext />}
                                {s.listed_entity_type === "organisation" && (
                                  <span className="muted"> (an organisation, not the hull)</span>
                                )}
                              </td>
                              <td>{s.ofac_program || <NAtext />}</td>
                              <td className="mono">{fmtDate(s.sanctions_as_of) || <NAtext />}</td>
                              <td>{s.match_tier}</td>
                              <td className="num mono">{num(s.confidence, 2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}

                  {candidates.length > 0 && (
                    <>
                      <div className="eyebrow">Candidates — not findings</div>
                      <p className="muted t-meta" style={{ margin: "6px 0 0" }}>
                        {candidates.length} weaker match
                        {candidates.length === 1 ? "" : "es"} on this hull
                        ({candidates.map((c) => c.match_tier).join(", ")}). Names
                        change and collide and call signs are reassigned, so these
                        are leads to verify — they did not rank this row.
                      </p>
                    </>
                  )}

                  <p className="muted t-meta" style={{ marginTop: 14, marginBottom: 0 }}>
                    {f.attribution}.
                  </p>
                </div>
              )}
            </div>
          );
        })}

        {items.length > 0 && (
          <div className="card card-pad" style={{ marginTop: 4 }}>
            <div className="eyebrow">How rows are ordered</div>
            <ul className="prose t-meta" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {basisLegend.map((b) => (
                <li key={b.signal}>
                  <span className="mono">+{b.weight}</span> — {b.explanation}
                </li>
              ))}
            </ul>
            <p className="muted t-meta" style={{ marginTop: 12, marginBottom: 0 }}>
              Priority is the sum of the signals a vessel actually carries. It is
              an ordering, not a probability, and it is never shown without the
              signals behind it.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
