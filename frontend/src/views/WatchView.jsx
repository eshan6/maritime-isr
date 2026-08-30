// Watch — the one screen an officer works, read two ways.
//
// This replaces three separate tabs (Assistant, Findings, Alerts) that a user
// reasonably described as "seemingly doing the same things". They were not
// quite the same, but the overlap was most of each: the assistant already
// ranked every subject and carried the evidence, findings re-listed a subset of
// the same hulls under a stricter real-data-only rule, and alerts held the same
// detections again — but was the only place any of it could be acted on. Three
// screens, two of them mostly a view of the third, and the one irreplaceable
// capability buried in the last.
//
// So: one screen, two lenses over the same facts.
//
//   **By Vessel** answers "who should I look at". One row per hull, ranked, with
//   every alert about her gathered underneath — because an officer investigates
//   a ship, not a detection, and four alerts on one hull are one investigation.
//
//   **By Event** answers "what has happened". A chronological queue, newest
//   first, one card per detection with the disposition buttons on it — because
//   working a watch means going down the list and clearing it, and a ranked list
//   of hulls cannot be worked that way.
//
// Both act. Confirm / Watch / Dismiss are on the alert wherever the alert is
// shown, so the officer never has to change screens to record a decision.
//
// **No real-vs-synthetic boundary**, by explicit instruction — there is no
// filter, no separate section and no gate. Individual rows still carry the
// SCENARIO tag, which is not a boundary but a label, and it stays: quoting a
// generated figure as though it were an observation is the one error this
// project treats as unrecoverable (CLAUDE.md §4.6, ADR-019).
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { anomalyLabel, edgeTypeLabel, familyColor, familyLabel, fmtDate,
         fmtDateTime, num, ANOMALY_META } from "../lib/format.js";
import { EvidenceList, ExportButton, FamilyLegend, FindOnMap,
         MakeupBar } from "../components/bits.jsx";

function pct(x) {
  return `${Math.round(100 * (x || 0))}%`;
}

function SynBadge({ on }) {
  if (!on) return null;
  return (
    <span className="badge badge-candidate"
          title="Generated scenario data. Every figure on this row is measured on the synthetic corpus and says nothing about any real vessel.">
      SCENARIO
    </span>
  );
}

// A factor as a chip: family colour on the edge, the factor named in words, and
// the points it contributed. The colour repeats what the text already says —
// three of the six family hues sit under 3:1 contrast on this surface, so
// colour alone could never have carried the meaning.
function FactorChip({ f, total }) {
  return (
    <span className="chip"
          style={{ borderLeftColor: familyColor(f.family) }}
          title={`${familyLabel(f.family)} — contributed ${f.points.toFixed(3)} of ${total.toFixed(3)}`}>
      <b>{f.kind.replace(/_/g, " ")}</b>
      <span className="pts">{f.points.toFixed(2)}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// the disposition control — the thing only the old Alerts tab could do
// ---------------------------------------------------------------------------

function Dispose({ alert, onDone }) {
  const [busy, setBusy] = useState(null);
  const settled = alert.disposition && alert.disposition !== "open";
  async function go(label) {
    setBusy(label);
    try {
      await api.dispose(alert.id, label);
      onDone?.();
    } finally {
      setBusy(null);
    }
  }
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8,
                  flexWrap: "wrap" }}>
      <button className="btn btn-sm btn-primary" disabled={busy === "confirm"}
              onClick={() => go("confirm")}>Confirm</button>
      <button className="btn btn-sm" disabled={busy === "watch"}
              onClick={() => go("watch")}>Watch</button>
      <button className="btn btn-sm" disabled={busy === "dismiss"}
              onClick={() => go("dismiss")}>Dismiss</button>
      {settled && (
        <span className="badge badge-neutral">recorded: {alert.disposition}</span>
      )}
    </div>
  );
}

// One detection, with its evidence chain and its buttons. Used unchanged in
// both lenses, so an alert looks and behaves the same wherever it is met.
function AlertCard({ a, onDone, showSubject = true }) {
  const tone = ANOMALY_META[a.anomaly_type]?.tone || "neutral";
  const dot = { finding: "var(--red)", candidate: "var(--amber)",
                neutral: "var(--ink-2)" }[tone];
  return (
    <div className="card card-pad evq-card">
      <div className="evq-head">
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: dot,
                       flex: "0 0 auto" }} />
        <strong>{anomalyLabel(a.anomaly_type)}</strong>
        {a.ts && <span className="evq-time">{fmtDateTime(a.ts)}</span>}
        <span className="muted t-meta">
          confidence {num(a.confidence, 2)}
          {a.score != null ? ` · score ${num(a.score, 2)}` : ""}
        </span>
        <SynBadge on={a.is_synthetic} />
        <div className="nav-spacer" style={{ flex: 1 }} />
        {showSubject && <FindOnMap id={a.subject} name={a.subject_name} compact />}
      </div>

      {showSubject && (
        <div style={{ marginTop: 4, fontWeight: 600 }}>
          {a.subject_name || a.subject}
        </div>
      )}

      {a.evidence?.length > 0 && (
        <ul className="evi">
          {a.evidence.map((h, i) => (
            <li key={i}>
              <div className="evi-fact">
                {h.detail || edgeTypeLabel(h.edge)}
              </div>
              {h.t_start && <div className="evi-when">{fmtDateTime(h.t_start)}</div>}
              <div className="evi-src">
                Source: <span className="who">{h.origin || h.source || "not attributed"}</span>
                {h.confidence != null && (
                  <span className="muted"> · confidence {num(h.confidence, 2)}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <Dispose alert={a} onDone={onDone} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BY VESSEL
// ---------------------------------------------------------------------------

function VesselRow({ item, alerts, selected, onSelect }) {
  return (
    <tr className={selected ? "selected" : ""}
        onClick={() => onSelect(item.subject_id)} style={{ cursor: "pointer" }}>
      <td className="num">{item.rank}</td>
      <td className="num" style={{ fontWeight: 600 }}>{item.score.toFixed(3)}</td>
      <td>
        <MakeupBar factors={item.factors} score={item.score} />
      </td>
      <td>
        {/* The name gets its own line and the tags go under it. Inline, a long
            name pushed the SCENARIO badge onto a second row and the badge
            pushed the identifiers onto a third, so a single subject occupied
            four lines of a table meant to be scanned. */}
        <div style={{ fontWeight: 600 }}>{item.display_name}</div>
        <div className="muted t-meta">
          {item.subject_kind === "vessel"
            ? [item.identifiers.mmsi && `MMSI ${item.identifiers.mmsi}`,
               item.identifiers.imo && `IMO ${item.identifiers.imo}`,
               item.identifiers.flag].filter(Boolean).join(" · ") || item.subject_id
            : "no broadcast identity"}
        </div>
        {(item.is_synthetic || alerts > 0) && (
          <div style={{ marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <SynBadge on={item.is_synthetic} />
            {alerts > 0 && (
              <span className="badge badge-neutral"
                    title="open detections on this hull">
                {alerts} alert{alerts === 1 ? "" : "s"}
              </span>
            )}
          </div>
        )}
      </td>
      <td>
        {item.factors.map((f) => (
          <FactorChip key={f.factor_id} f={f} total={item.score} />
        ))}
      </td>
      <td onClick={(e) => e.stopPropagation()}>
        <FindOnMap id={item.subject_id} name={item.display_name} compact />
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// one subject's page — the "why", the arithmetic, what to do, and her alerts
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
      <div className="muted t-meta" style={{ marginBottom: 8 }}>
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
        <button className="btn btn-primary btn-sm" disabled={busy}
                onClick={() => send()}>{busy ? "…" : "Ask"}</button>
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
          <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0", font: "inherit" }}>
            {a.text}
          </pre>
          {a.basis?.length > 0 && (
            <div className="muted t-meta" style={{ marginTop: 4 }}>
              read: {a.basis.join(", ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Subject({ id, alerts, onDisposed }) {
  const [v, setV] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState({});

  useEffect(() => {
    setV(null); setErr(null); setOpen({});
    api.voiDetail(id).then(setV).catch((e) => setErr(String(e)));
  }, [id]);

  if (err) return <div className="card card-pad"><div className="empty">{err}</div></div>;
  if (!v) return <div className="card card-pad"><div className="empty">Loading…</div></div>;

  const mine = alerts.filter((a) => a.subject === id);

  return (
    <div>
      <div className="card card-pad">
        <div style={{ display: "flex", alignItems: "baseline", gap: 10,
                      flexWrap: "wrap" }}>
          <div style={{ fontSize: "1.3rem", fontWeight: 700 }}>{v.display_name}</div>
          <div style={{ fontSize: "1.3rem", fontWeight: 700 }}>{v.score.toFixed(3)}</div>
          <SynBadge on={v.is_synthetic} />
          <div className="nav-spacer" style={{ flex: 1 }} />
          <FindOnMap id={v.subject_id} name={v.display_name} />
          {v.subject_kind === "vessel" && (
            <ExportButton id={v.subject_id} label="Export report" />
          )}
        </div>
        <p style={{ marginBottom: 0 }}>{v.account}</p>
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>Why she is on the list</div>
        {v.account_lines.map((line, i) => {
          const f = v.factors[i];
          return (
            <div key={i} style={{ marginBottom: 14, paddingLeft: 12,
                                  borderLeft: `3px solid ${familyColor(f?.family)}` }}>
              <div>{line}</div>
              <div className="muted t-meta" style={{ marginTop: 4 }}>
                {familyLabel(f?.family)} · {f?.kind.replace(/_/g, " ")} ·
                {" "}contributed {f?.points.toFixed(3)} ({pct(f?.share)} of the score)
                {f?.n_evidence > 0 && (
                  <>
                    {" · "}
                    <button className="btn-link"
                            onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}>
                      {open[i] ? "hide" : "show"} {f.n_evidence} evidence item
                      {f.n_evidence === 1 ? "" : "s"}
                    </button>
                  </>
                )}
              </div>
              {open[i] && <EvidenceList items={f.evidence} />}
            </div>
          );
        })}
      </div>

      {/* Her detections, actionable in place. This is what the separate Alerts
          tab existed for; gathering them under the hull is what makes them an
          investigation rather than a list. */}
      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 2 }}>
          Detections on this hull{mine.length > 0 ? ` (${mine.length})` : ""}
        </div>
        <div className="muted t-meta" style={{ marginBottom: 8 }}>
          Each one is recorded separately, so confirming a rendezvous does not
          confirm the identity contradiction beside it.
        </div>
        {mine.length === 0 && (
          <div className="muted t-meta">
            No open detections. She is on the list on the factors above, which
            are standing conditions rather than events — a designation or a flag
            history does not arrive as an alert.
          </div>
        )}
        {mine.map((a) => (
          <AlertCard key={a.id} a={a} onDone={onDisposed} showSubject={false} />
        ))}
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>The sum</div>
        <div className="muted t-meta" style={{ marginBottom: 8 }}>
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
          <div key={r.action} style={{ marginTop: 10, opacity: r.feasible ? 1 : 0.6 }}>
            <div>
              <strong>{r.headline}</strong>{" "}
              <span className="badge badge-neutral">{r.performed_by}</span>{" "}
              {!r.feasible && <span className="badge badge-finding">not available</span>}
            </div>
            <div className="muted-2 t-meta">{r.rationale}</div>
            {r.feasibility && <div className="muted t-meta">{r.feasibility}</div>}
            <div className="muted t-meta">system capability: {r.system_capability}</div>
          </div>
        ))}
      </div>

      <div className="card card-pad" style={{ marginTop: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>What is not known</div>
        <div className="muted t-meta" style={{ marginBottom: 6 }}>
          Absent evidence families. This is a build state, not a finding that
          these were clean.
        </div>
        <ul className="evi">
          {v.not_known.map((n, i) => (
            <li key={i}><div className="evi-fact muted-2">{n}</div></li>
          ))}
        </ul>
      </div>

      <AskBox subjectId={v.subject_id} suggestions={v.answerable_questions} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// BY EVENT — the chronological queue an officer works down
// ---------------------------------------------------------------------------

function EventQueue({ alerts, onDisposed }) {
  // Newest first, and grouped by day with a sticky heading. A flat list of
  // ninety cards gives no sense of when the watch got busy; the day break does,
  // and it costs one row.
  const days = useMemo(() => {
    const sorted = [...alerts].sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
    const out = [];
    let cur = null;
    for (const a of sorted) {
      const key = a.ts ? fmtDate(a.ts) : "undated";
      if (!cur || cur.key !== key) { cur = { key, items: [] }; out.push(cur); }
      cur.items.push(a);
    }
    return out;
  }, [alerts]);

  if (alerts.length === 0) {
    return (
      <div className="notebar">
        Nothing in the queue. A near-empty queue is the intended state — this
        system is tuned so that seven of every ten alerts survive review, which
        means far fewer of them (ADR-004). An empty queue is only a problem if
        the detectors did not run.
      </div>
    );
  }

  return (
    <div>
      {days.map((d) => (
        <div key={d.key}>
          <div className="evq-day">{d.key}</div>
          {d.items.map((a) => (
            <AlertCard key={a.id} a={a} onDone={onDisposed} />
          ))}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

export function WatchView() {
  const [params, setParams] = useSearchParams();
  const lens = params.get("by") === "event" ? "event" : "vessel";
  const [data, setData] = useState(null);
  const [work, setWork] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [err, setErr] = useState(null);
  const [sel, setSel] = useState(null);
  const [showSuppressed, setShowSuppressed] = useState(false);

  function loadAlerts() {
    api.alerts({}).then((r) => setAlerts(r.items || [])).catch(() => setAlerts([]));
  }

  useEffect(() => {
    api.voi({ limit: 200 }).then((d) => {
      setData(d);
      if (d.items?.length) setSel((s) => s || d.items[0].subject_id);
    }).catch((e) => setErr(String(e)));
    // The workload figure re-runs the whole assembly to count what was
    // suppressed, so the list must not wait on it.
    api.voiWorkload().then(setWork).catch(() => setWork(null));
    loadAlerts();
  }, []);

  // How many open detections each hull carries, so the ranked row can say so.
  const byHull = useMemo(() => {
    const m = new Map();
    for (const a of alerts) m.set(a.subject, (m.get(a.subject) || 0) + 1);
    return m;
  }, [alerts]);

  const families = useMemo(() => {
    const s = new Set();
    for (const it of data?.items || []) for (const f of it.factors) s.add(f.family);
    return s;
  }, [data]);

  if (err) return <div className="page"><div className="empty">{err}</div></div>;
  if (!data) return <div className="page"><div className="empty">Assembling the picture…</div></div>;

  const items = data.items || [];
  const health = data.queue_health || {};

  const switcher = (
    <div className="segmented" role="tablist" aria-label="how to read the watch">
      <button role="tab" aria-selected={lens === "vessel"}
              className={lens === "vessel" ? "on" : ""}
              onClick={() => setParams({}, { replace: true })}>
        By vessel <span className="count">{items.length}</span>
      </button>
      <button role="tab" aria-selected={lens === "event"}
              className={lens === "event" ? "on" : ""}
              onClick={() => setParams({ by: "event" }, { replace: true })}>
        By event <span className="count">{alerts.length}</span>
      </button>
    </div>
  );

  if (lens === "event") {
    return (
      <div className="page page-narrow">
        <div className="card card-pad" style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12,
                        flexWrap: "wrap" }}>
            {switcher}
            <div className="nav-spacer" style={{ flex: 1 }} />
          </div>
          <div className="muted-2" style={{ marginTop: 8 }}>
            Every detection, newest first. Work down the list: Confirm records
            that it was real, Watch keeps it open, Dismiss closes it. Your
            decision is stored against the alert and is what the precision
            figure is measured from.
          </div>
        </div>
        <EventQueue alerts={alerts} onDisposed={loadAlerts} />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="watch-split">
        <div>
          <div className="card card-pad">
            <div style={{ display: "flex", alignItems: "center", gap: 12,
                          flexWrap: "wrap", marginBottom: 10 }}>
              {switcher}
            </div>
            <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>
              Vessels of Interest
            </div>
            <div className="muted-2">
              {items.length} subject(s) on the list. {data.n_suppressed} further
              subject(s) carried a signal and were held below the{" "}
              {data.min_score} attention bar.
            </div>
            {work && (
              <div style={{ marginTop: 6 }}>
                <strong>{work.statement}</strong>
                <div className="muted t-meta">{work.caveat}</div>
              </div>
            )}
            {(health.notes || []).map((n, i) => (
              <div key={i} className="badge badge-candidate"
                   style={{ display: "block", marginTop: 6, whiteSpace: "normal" }}>
                queue health: {n}
              </div>
            ))}
            <div style={{ marginTop: 10 }}>
              <FamilyLegend families={families} />
            </div>
          </div>

          <div className="card" style={{ marginTop: 12, overflowX: "auto" }}>
            <table className="table">
              <colgroup>
                <col style={{ width: 34 }} />
                <col style={{ width: 62 }} />
                <col style={{ width: 132 }} />
                <col style={{ width: "44%" }} />
                <col style={{ width: "26%" }} />
                <col style={{ width: 74 }} />
              </colgroup>
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
                  <VesselRow key={it.subject_id} item={it}
                             alerts={byHull.get(it.subject_id) || 0}
                             selected={sel === it.subject_id} onSelect={setSel} />
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
            <div className="muted t-meta" style={{ marginBottom: 6 }}>
              The empty ones are the point: they are unbuilt areas, not clean
              findings.
            </div>
            {(data.coverage || []).map((f) => (
              <div key={f.family} style={{ marginTop: 6 }}>
                <span className="badge" style={{
                  color: f.present ? "var(--ink)" : "var(--ink-3)",
                  background: "var(--surface-2)",
                  borderLeft: `3px solid ${familyColor(f.family)}` }}>
                  {f.present ? "present" : "absent"}
                </span>{" "}
                <strong>{f.label}</strong>{" "}
                <span className="muted-2 t-meta">
                  — {f.blurb} ({f.areas.join(", ")})
                </span>
              </div>
            ))}
          </div>

          <div className="card card-pad" style={{ marginTop: 12 }}>
            {(data.notes || []).map((n, i) => (
              <div key={i} className="muted-2 t-meta" style={{ marginBottom: 4 }}>{n}</div>
            ))}
            {data.n_suppressed > 0 && (
              <>
                <button className="btn btn-sm" onClick={() => setShowSuppressed((s) => !s)}>
                  {showSuppressed ? "hide" : "show"} the {data.n_suppressed} suppressed subject(s)
                </button>
                {showSuppressed && (
                  <ul className="evi">
                    {data.suppressed.map((s) => (
                      <li key={s.subject_id}>
                        <div className="evi-fact">{s.subject_id}</div>
                        <div className="evi-src muted-2">{s.explanation}</div>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>

        <div>
          {sel
            ? <Subject id={sel} alerts={alerts} onDisposed={loadAlerts} />
            : <div className="card card-pad"><div className="empty">Pick a subject.</div></div>}
        </div>
      </div>
    </div>
  );
}
