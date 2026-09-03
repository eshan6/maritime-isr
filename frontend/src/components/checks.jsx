// The three-valued surfaces: rule checks, motion, area baselines, captures.
//
// Everything in this file exists to keep one distinction alive all the way to
// the glass: **`contradiction`, `ok` and `not_checkable` are three answers.**
// The rule modules in `anomaly/` have returned all three since ADR-032, only
// the first ever became an alert, and until now the other two never left the
// process. A screen that shows contradictions alone tells an officer a corpus
// nobody could check is a corpus that passed.
//
// So the three states differ by SHAPE and not only by hue — filled, outlined,
// dashed — because colour alone cannot carry a three-way distinction on a
// surface where several of the family hues already sit under 3:1 contrast.
//
// Nothing here hardcodes a colour. Every value comes off the custom properties
// in theme.css, so both themes are one definition.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtDateTime, num } from "../lib/format.js";

export const OUTCOMES = ["contradiction", "ok", "not_checkable"];

const OUTCOME_META = {
  contradiction: { glyph: "✕", label: "contradiction" },
  ok: { glyph: "✓", label: "checked, no conflict" },
  not_checkable: { glyph: "?", label: "not checkable" },
  error: { glyph: "!", label: "could not load" },
};

//: Why each state means what it means. On the pill, so the distinction is
//: available at the point somebody is deciding what to do about it.
const OUTCOME_TITLE = {
  contradiction: "The rule found a conflict between what she declares and what "
    + "the record holds.",
  ok: "The rule ran and found no conflict. This is a result, not an absence.",
  not_checkable: "The rule could not run: the record does not hold what it "
    + "needs. This is NOT a clean result and must never be read as one.",
  error: "The request failed. Nothing is being asserted either way.",
};

export function Outcome({ value }) {
  const m = OUTCOME_META[value] || OUTCOME_META.error;
  return (
    <span className={`outcome outcome-${value}`} title={OUTCOME_TITLE[value]}>
      <span className="glyph" aria-hidden="true">{m.glyph}</span>
      {m.label}
    </span>
  );
}

// The three-way tally as a bar. The shape of the split is the finding: a check
// that is not checkable on nine tenths of a corpus has told you almost nothing,
// and three numbers in a row hide that where a bar cannot.
export function TriBar({ counts, unit = "check" }) {
  const c = counts || {};
  const total = OUTCOMES.reduce((s, k) => s + (c[k] || 0), 0);
  if (!total) {
    return (
      <div className="muted t-meta">
        Nothing was checked, so there is no split to show.
      </div>
    );
  }
  return (
    <div>
      <div className="tri" role="img"
           aria-label={OUTCOMES.map((k) => `${c[k] || 0} ${k}`).join(", ")}>
        {OUTCOMES.map((k) => (
          (c[k] || 0) > 0 && (
            <i key={k} className={`i-${k}`}
               style={{ width: `${(100 * (c[k] || 0)) / total}%` }}
               title={`${c[k]} ${k.replace("_", " ")}`} />
          )
        ))}
      </div>
      <div className="tri-key">
        {OUTCOMES.map((k) => (
          <span key={k}>
            <b>{(c[k] || 0).toLocaleString()}</b>{" "}
            {k.replace(/_/g, " ")}
          </span>
        ))}
        <span className="muted">
          {total.toLocaleString()} {unit}{total === 1 ? "" : "s"}
        </span>
      </div>
    </div>
  );
}

// Who says so, and what this system then did to what they said (ADR-038).
// Two lines because they answer two questions: `origin` is the outside body an
// operator could go and check; `derivation` is ours and must never be carried
// under their name.
export function SourceLine({ origin, derivation }) {
  if (!origin && !derivation) return null;
  return (
    <div className="attrib">
      {origin && (
        <div>Source: <span className="who">{origin}</span></div>
      )}
      {derivation && <div className="attrib-derived">{derivation}</div>}
    </div>
  );
}

function CheckRow({ f }) {
  const cap = f.capture;
  return (
    <div className={`chk chk-${f.outcome}`}>
      <div className="chk-head">
        <Outcome value={f.outcome} />
        <span className="chk-name">{f.check.replace(/_/g, " ")}</span>
        {f.confidence > 0 && (
          <span className="muted t-meta">confidence {num(f.confidence, 2)}</span>
        )}
      </div>
      <div className="chk-statement">{f.statement}</div>
      {/* The passage the value was read off the document. Area 4's own bar for
          evidence: a field an analyst cannot put a finger on is not usable. */}
      {f.passage && (
        <div className="chk-detail">
          Read from: “{f.passage}”
          {f.locator ? ` — ${f.locator}` : ""}
        </div>
      )}
      {f.document && (
        <div className="muted t-meta" style={{ marginTop: 4 }}>
          {f.document.name}
          {f.document.format ? ` · ${f.document.format}` : ""}
          {f.document.filed_at ? ` · filed ${fmtDateTime(f.document.filed_at)}` : ""}
        </div>
      )}
      {f.declared && (
        <div className="muted t-meta" style={{ marginTop: 4 }}>
          declared {f.declared.destination || "no destination"}
          {f.declared.eta ? `, ETA ${fmtDateTime(f.declared.eta)}` : ""}
          {f.declared.declared_at
            ? ` · heard ${fmtDateTime(f.declared.declared_at)}` : ""}
        </div>
      )}
      {cap && <CaptureCard c={cap} compact />}
    </div>
  );
}

// One group of checks — one rule module, one question, one attribution.
function CheckGroup({ g }) {
  const [open, setOpen] = useState(false);
  const shown = open ? g.findings : g.findings.slice(0, 3);
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 9,
                    flexWrap: "wrap" }}>
        <strong>{g.label}</strong>
        <span className="muted t-meta">{g.area} · {g.adr}</span>
      </div>
      <div className="muted-2 t-meta" style={{ margin: "2px 0 7px" }}>
        {g.question}
      </div>
      {g.findings.length > 0 && <TriBar counts={g.counts} />}
      {g.note && (
        <div className="chk chk-not_checkable" style={{ marginTop: 8 }}>
          <div className="chk-head"><Outcome value="not_checkable" /></div>
          <div className="chk-statement">{g.note}</div>
        </div>
      )}
      {shown.map((f, i) => <CheckRow key={i} f={f} />)}
      {g.findings.length > 3 && (
        <button className="btn-link" style={{ marginTop: 8 }}
                onClick={() => setOpen((o) => !o)}>
          {open ? "show fewer" : `show all ${g.findings.length} checks`}
        </button>
      )}
      <SourceLine origin={g.origin} derivation={g.derivation} />
      <div className="muted t-meta" style={{ marginTop: 6 }}>
        Boundary: {g.boundary}
      </div>
    </div>
  );
}

// The whole panel for one subject.
export function ChecksPanel({ subjectId }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let live = true;
    setD(null); setErr(null);
    api.vesselChecks(subjectId)
      .then((r) => live && setD(r))
      .catch((e) => live && setErr(String(e.message || e)));
    return () => { live = false; };
  }, [subjectId]);

  if (err) {
    return (
      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>What was checked</div>
        <div className="chk chk-not_checkable">
          <div className="chk-head"><Outcome value="error" /></div>
          <div className="chk-statement">
            The checks could not be loaded. {err} Nothing is being asserted
            about this subject either way.
          </div>
        </div>
      </div>
    );
  }
  if (!d) {
    return (
      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div className="empty">Loading checks…</div>
      </div>
    );
  }

  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>What was checked</div>
      <div className="muted t-meta" style={{ marginBottom: 8 }}>
        Every rule that reads her declared identity, her declared voyage, her
        paperwork and her photographs. "Not checkable" means the record does not
        hold what the rule needs. It is not a pass.
      </div>
      <TriBar counts={d.totals} />
      <div style={{ marginTop: 14 }}>
        {d.groups.map((g) => <CheckGroup key={g.key} g={g} />)}
      </div>
      {d.track_basis && (
        <div className="muted t-meta">
          Track basis: {d.track_basis}.
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// motion: what she is doing, whether that is normal here, where she is going
// ---------------------------------------------------------------------------

function ActivityLine({ a, lead }) {
  if (!a) return null;
  return (
    <div className={`chk chk-${a.unclassified ? "not_checkable" : "ok"}`}>
      <div className="chk-head">
        {a.unclassified
          ? <span className="outcome outcome-not_checkable"
                  title="Nothing in the rule set describes this motion. A confident wrong activity costs more than an admitted gap.">
              <span className="glyph" aria-hidden="true">?</span>unclassified
            </span>
          : <strong>{a.activity.replace(/_/g, " ")}</strong>}
        <span className="muted t-meta">confidence {num(a.confidence, 2)}</span>
        {a.duration_hours != null && (
          <span className="muted t-meta">{num(a.duration_hours, 1)} h</span>
        )}
        {lead && <span className="muted t-meta">{lead}</span>}
      </div>
      {a.description && !a.unclassified && (
        <div className="muted-2 t-meta" style={{ marginTop: 3 }}>
          {a.description}
        </div>
      )}
      <div className="chk-statement">{a.reason}</div>
    </div>
  );
}

//: The three states of a per-area baseline, and what each is allowed to imply.
//: `no_opinion` is the one that matters: on the last measured corpus 212 of 770
//: cells were usable, so most of the sea has no local normal at all, and
//: drawing those as "ordinary" would report every unmonitored patch of water as
//: clean.
const BASELINE_STATE = {
  unusual: { outcome: "contradiction", label: "unusual here" },
  ordinary: { outcome: "ok", label: "ordinary here" },
  no_opinion: { outcome: "not_checkable", label: "no opinion here" },
  no_layer: { outcome: "not_checkable", label: "no baselines landed" },
};

function BaselinePanel({ b }) {
  if (!b) return null;
  const s = BASELINE_STATE[b.state] || BASELINE_STATE.no_opinion;
  const cov = b.coverage || {};
  return (
    <div className={`chk chk-${s.outcome}`} style={{ marginTop: 10 }}>
      <div className="chk-head">
        <Outcome value={s.outcome} />
        <span className="chk-name">local baseline · {s.label}</span>
      </div>
      <div className="chk-statement">{b.statement}</div>
      {cov.cells > 0 && (
        <div className="chk-detail">
          {cov.usable.toLocaleString()} of {cov.cells.toLocaleString()} cells
          carry enough observation to have a normal at all
          ({Math.round(100 * (cov.fraction_usable || 0))}%, at{" "}
          {cov.min_observations} observations minimum). The rest fall back to a
          global threshold.
        </div>
      )}
    </div>
  );
}

function ProjectionPanel({ p }) {
  if (!p) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        Where dead reckoning puts her
      </div>
      <div className="muted t-meta">{p.basis}</div>
      <div style={{ marginTop: 8, overflowX: "auto" }}>
        <table className="table">
          <thead><tr>
            <th className="no-sort num">lead</th>
            <th className="no-sort num">cone radius</th>
            <th className="no-sort num">confidence</th>
            <th className="no-sort">position</th>
          </tr></thead>
          <tbody>
            {p.steps.map((s) => (
              <tr key={s.lead_hours}>
                <td className="num">{s.lead_hours} h</td>
                <td className="num">{num(s.radius_km, 1)} km</td>
                <td className="num">{num(s.confidence, 2)}</td>
                <td className="mono t-meta">
                  {num(s.lat, 3)}, {num(s.lon, 3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* On the screen, not in a tooltip. A line reaching ahead of a ship is
          read as knowledge unless something says otherwise. */}
      <div className="notebar" style={{ marginTop: 8 }}>{p.caveat}</div>
      <SourceLine origin={p.origin} derivation={p.derivation} />
    </div>
  );
}

export function MotionPanel({ subjectId }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [allEpisodes, setAllEpisodes] = useState(false);

  useEffect(() => {
    let live = true;
    setD(null); setErr(null); setAllEpisodes(false);
    api.vesselMotion(subjectId)
      .then((r) => live && setD(r))
      .catch((e) => live && setErr(String(e.message || e)));
    return () => { live = false; };
  }, [subjectId]);

  if (err || !d) {
    return (
      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>
          What she is doing
        </div>
        <div className="empty">{err ? `Could not load motion. ${err}` : "Loading…"}</div>
      </div>
    );
  }
  if (!d.available) {
    return (
      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>
          What she is doing
        </div>
        <div className="chk chk-not_checkable">
          <div className="chk-head"><Outcome value="not_checkable" /></div>
          <div className="chk-statement">{d.note}</div>
        </div>
      </div>
    );
  }

  const episodes = allEpisodes ? d.episodes : d.episodes.slice(0, 4);
  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>What she is doing</div>
      <div className="muted t-meta" style={{ marginBottom: 8 }}>
        Read from motion alone. Nothing here consults a sensor name, which is
        what lets the same rules answer for a radar contact with no transponder.
      </div>

      <ActivityLine a={d.activity} lead="dominant" />
      <div className="muted t-meta" style={{ marginTop: 6 }}>
        {d.vocabulary_note}
      </div>

      {d.episodes.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            Episodes ({d.n_episodes})
          </div>
          {episodes.map((a, i) => (
            <ActivityLine key={i} a={a}
                          lead={a.t_start_iso ? fmtDateTime(a.t_start_iso) : null} />
          ))}
          {d.episodes.length > 4 && (
            <button className="btn-link" style={{ marginTop: 8 }}
                    onClick={() => setAllEpisodes((o) => !o)}>
              {allEpisodes ? "show fewer" : `show all ${d.episodes.length}`}
            </button>
          )}
        </div>
      )}

      <BaselinePanel b={d.baseline} />
      <ProjectionPanel p={d.projection} />

      <SourceLine origin={d.origin} derivation={d.derivation} />
      <div className="muted t-meta" style={{ marginTop: 6 }}>
        {d.n_points} fixes over {d.span_hours} h. Track basis: {d.track_basis}.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// captures — there is no camera, and the frame says so
// ---------------------------------------------------------------------------

// A capture with the disclosure ON the image, not under it. The frame is
// hatched because a plain rectangle where a photograph belongs gets read as a
// photograph that failed to load.
export function CaptureCard({ c, compact = false }) {
  if (!c) return null;
  return (
    <div className="capture" style={compact ? { marginTop: 8 } : undefined}>
      <div className="capture-frame">
        <span className="capture-stamp">
          {c.capture_mode === "live" ? "live capture" : "simulated · no image"}
        </span>
      </div>
      <div className="capture-body">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap",
                      alignItems: "baseline" }}>
          <strong>{c.station || "unknown station"}</strong>
          {c.taken_at && (
            <span className="muted t-meta">{fmtDateTime(c.taken_at)}</span>
          )}
          <span className="muted t-meta">
            {num(c.range_km, 1)} km on {num(c.bearing_deg, 0)}°
          </span>
          <span className="muted t-meta">
            {c.band} band · quality {num(c.image_quality, 2)}
          </span>
        </div>
        {c.statement && (
          <div style={{ marginTop: 5, lineHeight: 1.5 }}>{c.statement}</div>
        )}
        {!c.target_present && (
          <div className="muted-2 t-meta" style={{ marginTop: 4 }}>
            The frame was empty. That is recorded and counted and is
            deliberately never raised as an alert.
          </div>
        )}
        {c.imaged_families?.length > 0 && (
          <div className="muted t-meta" style={{ marginTop: 4 }}>
            Rules out everything outside: {c.imaged_families.join(", ")}
          </div>
        )}
        {c.not_classifiable && (
          <div className="muted t-meta" style={{ marginTop: 4 }}>
            No type claimed: {c.not_classifiable}
          </div>
        )}
        {/* The valuable half of the loop: why this camera was pointed here and
            not somewhere else. Landed on every row and never shown until now. */}
        {c.cue_sentence && (
          <div className="capture-why">
            Why the camera looked here: {c.cue_sentence}
            {c.cue_priority != null && ` (priority ${num(c.cue_priority, 2)})`}
          </div>
        )}
        {c.model_provenance && (
          <div className="muted t-meta" style={{ marginTop: 4 }}>
            Classifier: {c.model_name}. {c.model_provenance}
          </div>
        )}
      </div>
    </div>
  );
}

export function CapturesPanel({ subjectId }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let live = true;
    setD(null); setErr(null);
    api.eoCaptures({ subject: subjectId })
      .then((r) => live && setD(r))
      .catch((e) => live && setErr(String(e.message || e)));
    return () => { live = false; };
  }, [subjectId]);

  if (err || !d) return null;
  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 2 }}>
        Camera looks{d.items.length ? ` (${d.items.length})` : ""}
      </div>
      <div className="notebar" style={{ margin: "6px 0 10px" }}>
        {d.disclosure}
      </div>
      {d.note && <div className="muted t-meta">{d.note}</div>}
      {d.items.length === 0 && !d.note && (
        <div className="muted t-meta">
          No camera was ever pointed at her.
        </div>
      )}
      {d.items.map((c) => <CaptureCard key={c.capture_id} c={c} />)}
    </div>
  );
}
