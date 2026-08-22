// The MDA assistant — the ranked Vessel of Interest list and one subject's page.
//
// This is the frame Area 1 of the Section-3 brief exists to build, and the test
// it has to pass is stated there: a stranger opens the picture, sees a ranked
// list, picks the top subject, reads why it is flagged in language they
// understand, sees what the system suggests doing about it, asks a follow-up
// question, and gets a grounded answer.
//
// Four rules the layout enforces, each of them a thing that would otherwise be
// quietly lost between the API and the screen:
//
//   1. **The score decomposes on its face.** Every row shows the points each
//      factor contributed, and they add to the total. A composite an operator
//      cannot take apart is worthless to them, and a bar chart is not a
//      decomposition — the numbers are printed.
//   2. **Absence is shown, not omitted.** Three of the six evidence families
//      are unbuilt areas of the brief. A page that lists only what it found
//      reads as complete, so the empty families are on screen with the area
//      that would fill them.
//   3. **Capability is never implied.** Each recommendation says who performs
//      it and what the system can actually do towards it. Most say "not built".
//   4. **Synthetic is visible in the interface, not merely in the database.**
//      Per the ADR-019 standing caution, repeated in the Section-3 brief.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ExportButton } from "../components/bits.jsx";

const FAMILY_TONE = {
  motion: "var(--blue)",
  identity: "var(--amber)",
  network: "var(--red)",
  paperwork: "var(--ink-3)",
  imagery: "var(--ink-3)",
  radio: "var(--ink-3)",
};

function pct(x) {
  return `${Math.round(100 * (x || 0))}%`;
}

function SynBadge({ on }) {
  if (!on) return null;
  return <span className="badge badge-candidate" title="Generated scenario data. Every figure is measured on the synthetic corpus and says nothing about any real vessel.">SCENARIO</span>;
}

// ---------------------------------------------------------------------------
// the ranked list
// ---------------------------------------------------------------------------

function ScoreBar({ factors, score }) {
  // Widths are the factors' allocated shares, which sum to 1 by construction —
  // so the bar is the decomposition rather than a decoration beside it.
  return (
    <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden",
                  background: "var(--surface-2)", width: 190 }}
         title={factors.map((f) => `${f.kind}: ${f.points.toFixed(3)}`).join("\n")}>
      {factors.map((f) => (
        <div key={f.factor_id}
             style={{ width: `${100 * (f.share || 0) * score}%`,
                      background: FAMILY_TONE[f.family] || "var(--ink-3)" }} />
      ))}
    </div>
  );
}

function Row({ item, selected, onSelect }) {
  return (
    <tr className={selected ? "selected" : ""} onClick={() => onSelect(item.subject_id)}
        style={{ cursor: "pointer" }}>
      <td className="num">{item.rank}</td>
      <td className="num" style={{ fontWeight: 600 }}>{item.score.toFixed(3)}</td>
      <td><ScoreBar factors={item.factors} score={item.score} /></td>
      <td>
        <div style={{ fontWeight: 600 }}>{item.display_name}</div>
        <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>
          {item.subject_kind === "vessel"
            ? [item.identifiers.mmsi && `MMSI ${item.identifiers.mmsi}`,
               item.identifiers.imo && `IMO ${item.identifiers.imo}`,
               item.identifiers.flag].filter(Boolean).join(" · ") || item.subject_id
            : "no broadcast identity"}
        </div>
      </td>
      <td>
        {item.factors.map((f) => (
          <span key={f.factor_id} className="badge badge-neutral"
                style={{ marginRight: 4, borderLeft: `3px solid ${FAMILY_TONE[f.family]}` }}
                title={`${f.kind} — contributed ${f.points.toFixed(3)} of ${item.score.toFixed(3)}`}>
            {f.kind.replace(/_/g, " ")} {f.points.toFixed(2)}
          </span>
        ))}
      </td>
      <td><SynBadge on={item.is_synthetic} /></td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// one subject
// ---------------------------------------------------------------------------

function AskBox({ subjectId, suggestions }) {
  const [q, setQ] = useState("");
  const [answers, setAnswers] = useState([]);
  const [busy, setBusy] = useState(false);

  async function send(text) {
    const question = (text ?? q).trim();
    if (!question || busy) return;
    setBusy(true);
    try {
      const a = await api.voiAsk(subjectId, question);
      setAnswers((prev) => [a, ...prev]);
      setQ("");
    } catch (e) {
      // A failed request is not an answer, and must not be rendered as one.
      setAnswers((prev) => [{ question, outcome: "error", text: String(e),
                              basis: [], suggestions: [] }, ...prev]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card card-pad" style={{ marginTop: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Ask about this subject</div>
      <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 8 }}>
        Answers are retrieved from what the system holds, never generated. Where
        it holds nothing it says so, and it distinguishes that from "no".
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && send()}
               placeholder="e.g. why is she flagged?"
               style={{ flex: 1, padding: "6px 9px", background: "var(--surface-2)",
                        border: "1px solid var(--border)", borderRadius: 6,
                        color: "inherit", font: "inherit" }} />
        <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => send()}>
          {busy ? "…" : "Ask"}
        </button>
      </div>
      <div className="btn-group" style={{ marginTop: 8 }}>
        {(suggestions || []).slice(0, 6).map((s) => (
          <button key={s} className="btn btn-sm" onClick={() => send(s)}>{s}</button>
        ))}
      </div>
      {answers.map((a, i) => (
        <div key={i} style={{ marginTop: 10, paddingTop: 10,
                              borderTop: "1px solid var(--border)" }}>
          <div style={{ fontWeight: 600 }}>{a.question}</div>
          <span className={`badge ${a.outcome === "answered" ? "badge-neutral"
                            : a.outcome === "no_data" ? "badge-candidate"
                            : "badge-finding"}`}>
            {a.outcome.replace("_", " ")}
          </span>
          <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0",
                        font: "inherit" }}>{a.text}</pre>
          {a.basis?.length > 0 && (
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginTop: 4 }}>
              read: {a.basis.join(", ")}
            </div>
          )}
          {a.suggestions?.length > 0 && (
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginTop: 4 }}>
              I can answer: {a.suggestions.join(" · ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Detail({ id }) {
  const [v, setV] = useState(null);
  const [err, setErr] = useState(null);
  const [openEvidence, setOpenEvidence] = useState({});

  useEffect(() => {
    setV(null); setErr(null);
    api.voiDetail(id).then(setV).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="card card-pad"><div className="empty">{err}</div></div>;
  if (!v) return <div className="card card-pad"><div className="empty">Loading…</div></div>;

  return (
    <div>
      <div className="card card-pad">
        <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
          <div style={{ fontSize: "1.3rem", fontWeight: 700 }}>{v.display_name}</div>
          <div style={{ fontSize: "1.3rem", fontWeight: 700 }}>{v.score.toFixed(3)}</div>
          <SynBadge on={v.is_synthetic} />
          <div className="nav-spacer" style={{ flex: 1 }} />
          {v.subject_kind === "vessel" && (
            <ExportButton id={v.subject_id} label="Export report" />
          )}
        </div>
        <p style={{ marginBottom: 0 }}>{v.account}</p>
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Why she is on the list</div>
        {v.account_lines.map((line, i) => (
          <div key={i} style={{ marginBottom: 8, paddingLeft: 10,
                                borderLeft: `3px solid ${FAMILY_TONE[v.factors[i]?.family] || "var(--border)"}` }}>
            <div>{line}</div>
            <div className="muted" style={{ fontSize: "var(--fs-meta)", marginTop: 2 }}>
              {v.factors[i]?.kind.replace(/_/g, " ")} · contributed{" "}
              {v.factors[i]?.points.toFixed(3)} ({pct(v.factors[i]?.share)} of the score)
              {" · "}
              <button className="btn-link"
                      onClick={() => setOpenEvidence((o) => ({ ...o, [i]: !o[i] }))}>
                {openEvidence[i] ? "hide" : "show"} {v.factors[i]?.n_evidence} evidence item(s)
              </button>
            </div>
            {openEvidence[i] && (
              <ul style={{ margin: "6px 0 0", fontSize: "var(--fs-meta)" }}>
                {v.factors[i].evidence.map((e, j) => (
                  <li key={j} className="muted-2">
                    {e.label}
                    {e.occurred_at && ` [${e.occurred_at.slice(0, 19)}]`}
                    {" — source "}{e.provenance?.source_id || "unattributed"}
                    {e.provenance?.source_ref ? ` / ${e.provenance.source_ref}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>The sum</div>
        <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 8 }}>
          {v.arithmetic.formula}
        </div>
        <table className="table">
          <thead><tr>
            <th className="no-sort">factor</th>
            <th className="no-sort num">weight</th>
            <th className="no-sort num">confidence</th>
            <th className="no-sort num">alone</th>
            <th className="no-sort num">points</th>
            <th className="no-sort num">share</th>
          </tr></thead>
          <tbody>
            {v.arithmetic.rows.map((r) => (
              <tr key={r.factor_id}>
                <td>{r.kind.replace(/_/g, " ")}</td>
                <td className="num">{r.weight.toFixed(2)}</td>
                <td className="num">{r.confidence.toFixed(2)}</td>
                <td className="num">{r.standalone.toFixed(2)}</td>
                <td className="num">{r.points.toFixed(3)}</td>
                <td className="num">{r.share_pct.toFixed(0)}%</td>
              </tr>
            ))}
            <tr>
              <td style={{ fontWeight: 600 }}>total</td>
              <td colSpan={3} />
              <td className="num" style={{ fontWeight: 600 }}>
                {v.arithmetic.sum_of_points.toFixed(3)}
              </td>
              <td className="num">
                {v.arithmetic.reconciles ? "reconciles" : "DOES NOT RECONCILE"}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>
          What to do next — the system proposes, you decide
        </div>
        {v.recommendations.map((r) => (
          <div key={r.action} style={{ marginTop: 8, opacity: r.feasible ? 1 : 0.6 }}>
            <div>
              <strong>{r.headline}</strong>{" "}
              <span className="badge badge-neutral">{r.performed_by}</span>{" "}
              {!r.feasible && <span className="badge badge-finding">not available</span>}
            </div>
            <div className="muted-2" style={{ fontSize: "var(--fs-meta)" }}>{r.rationale}</div>
            {r.feasibility && (
              <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>{r.feasibility}</div>
            )}
            <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>
              system capability: {r.system_capability}
            </div>
          </div>
        ))}
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>What is not known</div>
        <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>
          Absent evidence families. This is a build state, not a finding that
          these were clean.
        </div>
        <ul style={{ margin: 0, fontSize: "var(--fs-meta)" }}>
          {v.not_known.map((n, i) => <li key={i} className="muted-2">{n}</li>)}
        </ul>
      </div>

      <AskBox subjectId={v.subject_id} suggestions={v.answerable_questions} />
    </div>
  );
}

// ---------------------------------------------------------------------------

export function AssistantView() {
  const [data, setData] = useState(null);
  const [work, setWork] = useState(null);
  const [err, setErr] = useState(null);
  const [sel, setSel] = useState(null);
  const [showSuppressed, setShowSuppressed] = useState(false);

  useEffect(() => {
    api.voi({ limit: 200 }).then((d) => {
      setData(d);
      if (d.items?.length) setSel(d.items[0].subject_id);
    }).catch((e) => setErr(String(e)));
    // The workload figure is a separate request on purpose: it re-runs the whole
    // assembly to count what was suppressed, and the list must not wait on it.
    api.voiWorkload().then(setWork).catch(() => setWork(null));
  }, []);

  if (err) return <div className="pad"><div className="empty">{err}</div></div>;
  if (!data) return <div className="pad"><div className="empty">Assembling the list…</div></div>;

  const items = data.items || [];
  const health = data.queue_health || {};

  return (
    <div className="pad" style={{ display: "grid", gridTemplateColumns: "minmax(0,1.15fr) minmax(0,1fr)", gap: 14 }}>
      <div style={{ minWidth: 0 }}>
        <div className="card card-pad">
          <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>
            Vessels of Interest
          </div>
          <div className="muted-2">
            {data.count.real} from real data, {data.count.synthetic} from the
            scenario corpus. {data.n_suppressed} further subject(s) carried a
            signal and were held below the {data.min_score} attention bar.
          </div>
          {work && (
            <div style={{ marginTop: 6 }}>
              <strong>{work.statement}</strong>
              <div className="muted" style={{ fontSize: "var(--fs-meta)" }}>{work.caveat}</div>
            </div>
          )}
          {(health.notes || []).map((n, i) => (
            <div key={i} className="badge badge-candidate"
                 style={{ display: "block", marginTop: 6, whiteSpace: "normal" }}>
              queue health: {n}
            </div>
          ))}
        </div>

        <div className="card" style={{ marginTop: 12, overflowX: "auto" }}>
          <table className="table">
            <thead><tr>
              <th className="no-sort num">#</th>
              <th className="no-sort num">score</th>
              <th className="no-sort">makeup</th>
              <th className="no-sort">subject</th>
              <th className="no-sort">factors, with the points each contributed</th>
              <th className="no-sort" />
            </tr></thead>
            <tbody>
              {items.map((it) => (
                <Row key={it.subject_id} item={it} selected={sel === it.subject_id}
                     onSelect={setSel} />
              ))}
            </tbody>
          </table>
          {items.length === 0 && (
            <div className="empty">
              Nothing reached the list. An empty queue is a result — but check
              the detectors fired at all before reading it as one.
            </div>
          )}
        </div>

        <div className="card card-pad" style={{ marginTop: 12 }}>
          <div style={{ fontWeight: 600 }}>Evidence families in this picture</div>
          <div className="muted" style={{ fontSize: "var(--fs-meta)", marginBottom: 6 }}>
            The empty ones are the point: they are unbuilt areas, not clean findings.
          </div>
          {(data.coverage || []).map((f) => (
            <div key={f.family} style={{ marginTop: 4 }}>
              <span className="badge" style={{
                color: f.present ? "var(--blue)" : "var(--ink-3)",
                background: "var(--surface-2)",
                borderLeft: `3px solid ${FAMILY_TONE[f.family]}` }}>
                {f.present ? "present" : "absent"}
              </span>{" "}
              <strong>{f.label}</strong>{" "}
              <span className="muted-2" style={{ fontSize: "var(--fs-meta)" }}>
                — {f.blurb} ({f.areas.join(", ")})
              </span>
            </div>
          ))}
        </div>

        <div className="card card-pad" style={{ marginTop: 12 }}>
          {(data.notes || []).map((n, i) => (
            <div key={i} className="muted-2" style={{ fontSize: "var(--fs-meta)", marginBottom: 4 }}>{n}</div>
          ))}
          {data.n_suppressed > 0 && (
            <>
              <button className="btn btn-sm" onClick={() => setShowSuppressed((s) => !s)}>
                {showSuppressed ? "hide" : "show"} the {data.n_suppressed} suppressed subject(s)
              </button>
              {showSuppressed && (
                <ul style={{ fontSize: "var(--fs-meta)", marginTop: 6 }}>
                  {data.suppressed.map((s) => (
                    <li key={s.subject_id} className="muted-2">
                      <code>{s.subject_id}</code> — {s.explanation}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>

      <div style={{ minWidth: 0 }}>
        {sel ? <Detail id={sel} />
             : <div className="card card-pad"><div className="empty">Pick a subject.</div></div>}
      </div>
    </div>
  );
}
