// Coastal radar — the watchkeeper's view of the dark-contact queue (ADR-028).
//
// Until this existed the entire radar path was CLI-only: the correlation ran,
// landed its verdicts, and the only way to read them was a terminal. A sensor
// nobody can see is not a sensor in the product.
//
// Two things this view refuses to do:
//
//   * **Show only the survivors.** The suppressed verdicts are one click away
//     and carry the reason. "Why is this NOT flagged" has to be answerable from
//     the product; a filter cascade whose rejections are invisible is a black
//     box the operator has to take on faith, and the whole thesis is that they
//     should not have to.
//   * **Imply a sensor that is not there.** No coastal radar feed has ever
//     been connected to this system. One non-dismissible line at the top of the
//     view says so; it is not repeated on every row, because a disclosure
//     stamped forty times stops being read after the second.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtDateTime, num } from "../lib/format.js";

//: Human wording for every verdict the cascade can return. A raw enum in the
//: status column would make the suppressions unreadable, which defeats the
//: point of showing them.
const VERDICT = {
  dark_candidate: {
    label: "Dark contact",
    tone: "finding",
    why: "held on radar, nothing on AIS explained it, and it survived every suppression below",
  },
  suppressed_static: {
    label: "Suppressed · fixed object",
    tone: "muted",
    why: "repeat unexplained looks in the same place across many days — a mooring, terminal or platform, not a vessel",
  },
  suppressed_transient: {
    label: "Suppressed · too brief",
    tone: "muted",
    why: "too few looks over too short a span to be a track rather than clutter",
  },
  suppressed_not_isolated: {
    label: "Suppressed · not isolated",
    tone: "muted",
    why: "unspent broadcasters sat in the same neighbourhood — the contact is most likely one of them, mis-associated",
  },
  suppressed_coverage: {
    label: "Suppressed · outside AIS reception",
    tone: "muted",
    why: "no demonstrated receiver coverage here, so silence is not evidence of anything (CLAUDE.md §6)",
  },
};

//: Human wording for the per-track correlation outcome carried onto the row.
const CORRELATION = {
  correlated: "matched an AIS track throughout",
  correlated_then_dark: "was identified on AIS, then stopped",
  correlated_gap_explained: "lost its AIS match, but the vessel was still being heard",
  ambiguous: "no single AIS track explained enough of it",
  dark: "no AIS track ever explained it",
  transient: "too short to judge",
};

export function RadarView() {
  const [rows, setRows] = useState(null);
  const [note, setNote] = useState(null);
  const [showSuppressed, setShowSuppressed] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    setRows(null);
    api
      .radarContacts(showSuppressed ? { status: "all", limit: 2000 } : {})
      .then((r) => {
        if (!live) return;
        setRows(r.items || []);
        setNote(r.note || null);
      })
      .catch((e) => live && setError(String(e.message || e)));
    return () => {
      live = false;
    };
  }, [showSuppressed]);

  const candidates = (rows || []).filter((r) => r.status === "dark_candidate");
  const suppressed = (rows || []).filter((r) => r.status !== "dark_candidate");

  return (
    <div className="scroll-y">
      <div className="toolbar">
        <div>
          <div className="eyebrow">Coastal radar — dark contacts</div>
          <div className="muted t-meta">
            {candidates.length} contact{candidates.length === 1 ? "" : "s"} survived
            the cascade
            {showSuppressed ? ` · ${suppressed.length} suppressed` : ""}
          </div>
        </div>
        <div className="nav-spacer" />
        <label className="layer-toggle" style={{ margin: 0 }}>
          <input
            type="checkbox"
            checked={showSuppressed}
            onChange={(e) => setShowSuppressed(e.target.checked)}
          />
          Show suppressed verdicts
        </label>
      </div>

      <div className="pad" style={{ maxWidth: 900 }}>
        <ProvenanceNote />

        {error && <div className="notebar">Could not load radar contacts — {error}</div>}
        {!rows && !error && <div className="empty">Loading…</div>}
        {note && <div className="notebar">{note}</div>}

        {rows && !note && candidates.length === 0 && (
          <div className="notebar">
            No radar contact survived the cascade in this corpus. That is a result,
            not an empty page — turn on the suppressed verdicts to see what was
            rejected and why.
          </div>
        )}

        {candidates.map((c) => (
          <ContactCard key={c.candidate_id} c={c} />
        ))}

        {showSuppressed && suppressed.length > 0 && (
          <>
            <h3 style={{ marginTop: 26 }}>Suppressed</h3>
            <div className="muted t-meta" style={{ margin: "6px 0 10px" }}>
              Radar held something and AIS did not explain it, but the cascade
              found a better explanation than a dark vessel. These are the false
              positives the precision-first policy is paying for (ADR-004).
            </div>
            {suppressed.map((c) => (
              <ContactCard key={c.candidate_id} c={c} compact />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// The disclosure the brief requires to be visible in the INTERFACE, not only
// in the database: no Coastal Surveillance Network feed has ever been connected
// to this system, so nothing below is a measurement of a real sensor. Stated
// once, at the top, in the operator's line of sight — a banner repeated on
// every row is a banner nobody reads by the third one.
function ProvenanceNote() {
  return (
    <div
      className="notebar"
      style={{
        borderLeft: "3px solid var(--amber)",
        marginBottom: 14,
      }}
    >
      <b>No live coastal radar feed is connected to this system.</b> The picture
      below is modelled from the same vessel positions as the AIS view, so a
      radar contact and an AIS track are two views of one modelled ship — which
      is what makes the correlation measurable at all, and what means every
      figure here describes the model rather than a sensor. Treat it as a
      demonstration of the method, not as a measurement of traffic.
    </div>
  );
}

function ContactCard({ c, compact = false }) {
  const v = VERDICT[c.status] || { label: c.status, tone: "muted", why: "" };
  return (
    <div
      className="card"
      style={{
        marginBottom: 10,
        padding: "11px 13px",
        opacity: compact ? 0.82 : 1,
        borderLeft: `3px solid ${v.tone === "finding" ? "#b0221b" : "#98a4ae"}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span
          className={`badge ${v.tone === "finding" ? "badge-finding" : "badge-candidate"}`}
        >
          {v.label}
        </span>
        <span className="mono t-med t-meta">{c.radar_track_id}</span>
        <span className="muted mono t-meta">{fmtDateTime(c.ts)}</span>
        <span className="muted mono t-meta">
          {num(c.lat, 3)}, {num(c.lon, 3)}
        </span>
      </div>

      <div className="t-meta prose" style={{ marginTop: 8 }}>
        {v.why}.
      </div>

      <div className="t-meta prose" style={{ marginTop: 7 }}>
        <Fact k="Seen by" v={c.station_ids} />
        <Fact k="Length from radar cross-section" v={c.length_m ? `≈ ${num(c.length_m, 0)} m` : null} />
        <Fact k="Unexplained for" v={c.dark_minutes ? `${num(c.dark_minutes, 0)} min` : null} />
        <Fact
          k="AIS reception here"
          v={
            c.hearable_conf != null
              ? `${num(100 * c.hearable_conf, 0)}% — ${
                  c.hearable_conf >= 0.5
                    ? "we would have heard a transponder"
                    : "weak, so silence proves little"
                }`
              : null
          }
        />
        <Fact k="Correlation" v={CORRELATION[c.correlation_status] || c.correlation_status} />
        <Fact k="Score" v={c.dark_score != null ? num(c.dark_score, 2) : null} />
      </div>

      {/* The headline sentence of the whole build, and it is only ever printed
          when the evidence actually supports it: this contact WAS matched to a
          broadcasting hull, and then that hull stopped being heard while radar
          kept holding her. A contact that never transmitted has no such pair
          and gets no such sentence. */}
      {c.went_dark_at && (
        <div
          style={{
            marginTop: 9,
            padding: "8px 10px",
            background: "rgba(176,34,27,0.07)",
            border: "1px solid rgba(176,34,27,0.25)",
            borderRadius: 5,
          }}
          className="t-meta prose"
        >
          <b>Transponder went quiet here.</b> Last explained by{" "}
          <span className="mono">MMSI {c.mmsi}</span> at{" "}
          {fmtDateTime(c.went_dark_at)}, position {num(c.went_dark_lat, 4)},{" "}
          {num(c.went_dark_lon, 4)} — radar held her continuously across the
          transition.
        </div>
      )}
    </div>
  );
}

function Fact({ k, v }) {
  if (v === null || v === undefined || v === "") return null;
  return (
    <div>
      <span className="muted">{k}:</span> {v}
    </div>
  );
}

