// Method: what this system checks, what it measured, and where it declines.
//
// Not a sixth queue. `Watch` remains the one screen an officer works, read by
// vessel or by event (ADR-038), and nothing here duplicates it: there is no
// ranked list, no alert, no disposition control. This answers a different
// question, asked once rather than every shift — *what is this thing actually
// able to tell me, and where does it stop?*
//
// Everything on it was already true in Python and was reachable only from a
// terminal. Four things in particular, and each is a place where the honest
// half is the valuable half:
//
//   * The three-way split of every rule check across the corpus. "Not
//     checkable" is usually the biggest of the three and it never left the
//     process, so a reader of contradictions alone was reading silence as a
//     clean bill of health.
//   * The vessel-type vocabulary, read off a measured confusion matrix rather
//     than declared. A laden bulker and a laden product tanker at 13 knots on
//     a great-circle course are doing the same thing; the system says
//     `merchant` and names what it merged. That refusal is the product.
//   * The interaction detector's measured silence on this corpus, reported as
//     a fact about the corpus rather than hidden.
//   * The camera loop, which has no camera.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { CaptureCard, Outcome, SourceLine, TriBar } from "../components/checks.jsx";
import { num } from "../lib/format.js";

function Card({ title, sub, children }) {
  return (
    <div className="card card-pad" style={{ marginBottom: 12 }}>
      <div style={{ fontWeight: 600 }}>{title}</div>
      {sub && (
        <div className="muted t-meta" style={{ margin: "3px 0 10px" }}>{sub}</div>
      )}
      {children}
    </div>
  );
}

function useEndpoint(fn, deps = []) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true;
    setD(null); setErr(null);
    fn().then((r) => live && setD(r))
        .catch((e) => live && setErr(String(e.message || e)));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return [d, err];
}

// ---------------------------------------------------------------------------

function ChecksCoverage() {
  const [d, err] = useEndpoint(() => api.checksCoverage());
  if (err) return <Card title="What can be checked"><div className="empty">{err}</div></Card>;
  if (!d) return <Card title="What can be checked"><div className="empty">Loading…</div></Card>;
  return (
    <Card
      title="What can be checked, across the whole record"
      sub={d.note}
    >
      {d.groups.map((g) => (
        <div key={g.key} style={{ marginBottom: 18 }}>
          <div style={{ display: "flex", gap: 9, alignItems: "baseline",
                        flexWrap: "wrap" }}>
            <strong>{g.label}</strong>
            <span className="muted t-meta">{g.area} · {g.adr} · {g.module}</span>
          </div>
          <div className="muted-2 t-meta" style={{ margin: "2px 0 7px" }}>
            {g.question}
          </div>
          {g.note ? (
            <div className="chk chk-not_checkable">
              <div className="chk-head"><Outcome value="not_checkable" /></div>
              <div className="chk-statement">{g.note}</div>
            </div>
          ) : (
            <>
              <TriBar counts={g.counts} unit={g.unit || "check"} />
              <div className="muted t-meta" style={{ marginTop: 5 }}>
                {(g.scanned || 0).toLocaleString()} of{" "}
                {(g.total || 0).toLocaleString()} {g.unit || "row"}
                {g.total === 1 ? "" : "s"} swept
                {g.checks_swept
                  ? `, over ${g.checks_swept.map((c) => c.replace(/_/g, " ")).join(", ")}`
                  : ""}
                .
              </div>
              {Object.keys(g.by_check || {}).length > 1 && (
                <div style={{ marginTop: 8 }}>
                  {Object.entries(g.by_check).map(([check, counts]) => (
                    <div key={check} style={{ marginTop: 8 }}>
                      <div className="chk-name">{check.replace(/_/g, " ")}</div>
                      <TriBar counts={counts} unit="hull" />
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
          <SourceLine origin={g.origin} derivation={g.derivation} />
        </div>
      ))}

      <div className="notebar">
        <b>Not swept here, and it is not a gap in the capability.</b>{" "}
        {d.per_subject_only.map((p) => p.check.replace(/_/g, " ")).join(", ")}{" "}
        need one hull&apos;s whole track to answer, which is a pipeline job
        rather than a request. They run on a subject&apos;s own page.
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function ConfusionMatrix({ cm, labels }) {
  if (!cm || !labels?.length) return null;
  return (
    <div className="matrix-wrap">
      <table className="matrix">
        <thead>
          <tr>
            <th className="rowhead">true \ predicted</th>
            {labels.map((l) => <th key={l}>{l}</th>)}
          </tr>
        </thead>
        <tbody>
          {labels.map((t) => (
            <tr key={t}>
              <th className="rowhead">{t}</th>
              {labels.map((p) => {
                const n = (cm[t] || {})[p] || 0;
                const cls = n === 0 ? "zero" : t === p ? "diag" : "off";
                return <td key={p} className={cls}>{n}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VesselTypeCard() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.vesselTypeModel({})
      .then((r) => live && setD(r))
      .catch((e) => live && setErr(String(e.message || e)));
    return () => { live = false; };
  }, []);

  async function measure() {
    setBusy(true);
    try {
      setD(await api.vesselTypeModel({ compute: true }));
      setErr(null);
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  if (err) {
    return <Card title="Vessel type from motion alone"><div className="empty">{err}</div></Card>;
  }
  if (!d) {
    return <Card title="Vessel type from motion alone"><div className="empty">Loading…</div></Card>;
  }
  const m = d.measured;
  return (
    <Card
      title="Vessel type from motion alone"
      sub={`${d.area} · ${d.adr} · ${d.module}`}
    >
      <p className="prose" style={{ marginTop: 0 }}>{d.vocabulary_rule}</p>

      {d.status === "measured" && m ? (
        <>
          <div style={{ display: "flex", gap: 22, flexWrap: "wrap",
                        margin: "12px 0" }}>
            <div>
              <div className="muted t-micro">FINE ACCURACY</div>
              <div className="t-hero">
                {Math.round(100 * (m.fine_accuracy || 0))}%
              </div>
            </div>
            <div>
              <div className="muted t-micro">COARSE ACCURACY</div>
              <div className="t-hero">
                {Math.round(100 * (m.coarse_accuracy || 0))}%
              </div>
            </div>
            <div>
              <div className="muted t-micro">HULLS</div>
              <div className="t-hero">{d.n_hulls}</div>
            </div>
          </div>

          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            What it publishes
          </div>
          <div className="muted t-meta" style={{ marginBottom: 6 }}>
            The vocabulary an operator actually sees. Every label here is one
            the confusion matrix supports.
          </div>
          <div>
            {(m.vocabulary || []).map((v) => (
              <span key={v} className="chip"><b>{v.replace(/_/g, " ")}</b></span>
            ))}
          </div>

          <div style={{ fontWeight: 600, margin: "14px 0 4px" }}>
            What it cannot separate, and says so
          </div>
          <div className="muted t-meta" style={{ marginBottom: 6 }}>
            These are merged and reported under one coarse label. The system
            reports the merge rather than picking one of them, and that refusal
            is the point: motion genuinely does not distinguish them.
          </div>
          {(m.cannot_separate || []).length === 0 ? (
            <div className="muted t-meta">
              Nothing was merged in this run: no pair was confused past the{" "}
              {Math.round(100 * d.merge_threshold)}% bar.
            </div>
          ) : (
            (m.cannot_separate || []).map((g, i) => (
              <span key={i} className="merged">
                <b>{g.join(" · ")}</b>
              </span>
            ))
          )}

          <div style={{ fontWeight: 600, margin: "14px 0 4px" }}>
            The confusion matrix it was read off
          </div>
          <div className="muted t-meta" style={{ marginBottom: 6 }}>
            Rows are the true class, columns what the model said, on held-out
            hulls. Off-diagonal weight is where the merges come from.
          </div>
          <ConfusionMatrix cm={m.confusion} labels={m.labels} />

          <div className="notebar" style={{ marginTop: 12 }}>{m.caveat}</div>
          <div className="muted t-meta" style={{ marginTop: 6 }}>{d.note}</div>
        </>
      ) : (
        <>
          <div className="chk chk-not_checkable" style={{ marginTop: 10 }}>
            <div className="chk-head">
              <Outcome value="not_checkable" />
              <span className="chk-name">status: {d.status}</span>
            </div>
            <div className="chk-statement">{d.note}</div>
          </div>
          <button className="btn btn-primary btn-sm" style={{ marginTop: 10 }}
                  disabled={busy} onClick={measure}>
            {busy ? "Measuring…" : "Measure on the landed corpus"}
          </button>
          <div className="muted t-meta" style={{ marginTop: 6 }}>
            This fits a model here and now over the landed AIS fleet, split by
            hull. It takes tens of seconds and the result is then held for the
            life of the server.
          </div>
        </>
      )}

      <div className="muted t-meta" style={{ marginTop: 12 }}>
        {d.split_rule}
      </div>
      <div className="muted t-meta" style={{ marginTop: 6 }}>
        {d.sensor_blind}
      </div>
      <SourceLine origin={d.origin} derivation={d.derivation} />
    </Card>
  );
}

// ---------------------------------------------------------------------------

function InteractionsCard() {
  const [d, err] = useEndpoint(() => api.interactionCapability());
  if (err || !d) {
    return (
      <Card title="One hull against another">
        <div className="empty">{err || "Loading…"}</div>
      </Card>
    );
  }
  return (
    <Card title="One hull against another"
          sub={`${d.area} · ${d.adr} · ${d.module}`}>
      <div className="muted t-meta" style={{ marginBottom: 8 }}>
        Relative motion between two tracks, not proximity. How the separation
        behaves, whether the courses agree, whether one holds a bearing astern.
      </div>
      {d.behaviours.map((b) => (
        <div key={b.kind} className="chk chk-ok">
          <div className="chk-head">
            <strong>{b.kind.replace(/_/g, " ")}</strong>
          </div>
          <div className="chk-statement">{b.what}</div>
        </div>
      ))}
      <div className="muted t-meta" style={{ marginTop: 10 }}>
        Gates: at least {d.gates.min_minutes} minutes sustained, within{" "}
        {num(d.gates.max_separation_m / 1852, 1)} nm, courses agreeing to{" "}
        {d.gates.same_course_deg}°, alongside under {d.gates.alongside_m} m,
        making way above {d.gates.underway_min_kn} kn.
      </div>
      <div className="notebar" style={{ marginTop: 10 }}>{d.measured_note}</div>
      <div className="muted t-meta" style={{ marginTop: 8 }}>{d.boundary}</div>
      <div className="muted t-meta" style={{ marginTop: 8 }}>
        {d.n_alerts} interaction alert{d.n_alerts === 1 ? "" : "s"} currently on
        the watch queue.
      </div>
      <SourceLine origin={d.origin} derivation={d.derivation} />
    </Card>
  );
}

// ---------------------------------------------------------------------------

function BaselinesCard() {
  const [d, err] = useEndpoint(() => api.baselines({ usable_only: false,
                                                     limit: 500 }));
  if (err || !d) {
    return (
      <Card title="What normal looks like, per area">
        <div className="empty">{err || "Loading…"}</div>
      </Card>
    );
  }
  const cov = d.coverage || {};
  const usable = cov.usable || 0;
  const cells = cov.cells || 0;
  return (
    <Card
      title="What normal looks like, per area"
      sub="ADR-032 · baselines.py · derived per H3 cell at resolution 5, about 8 km across"
    >
      {d.note && <div className="notebar">{d.note}</div>}
      {cells > 0 && (
        <>
          {/* The coverage split, in the same three-state language as the rule
              checks, because it is the same distinction: a cell with too few
              observations has no opinion, and that is not "normal here". */}
          <div className="tri" role="img"
               aria-label={`${usable} cells with a baseline, ${cells - usable} without`}>
            <i className="i-ok" style={{ width: `${(100 * usable) / cells}%` }} />
            <i className="i-not_checkable"
               style={{ width: `${(100 * (cells - usable)) / cells}%` }} />
          </div>
          <div className="tri-key">
            <span><b>{usable.toLocaleString()}</b> cells have a local normal</span>
            <span>
              <b>{(cells - usable).toLocaleString()}</b> have no opinion
            </span>
            <span className="muted">
              {cells.toLocaleString()} cells, {cov.min_observations} observations
              minimum
            </span>
          </div>
          <p className="prose" style={{ marginTop: 10 }}>
            The second number is the one that matters. Where a cell has no
            baseline, a rule falls back to its global threshold and the subject
            page says so rather than reporting the water as ordinary. Rendering
            those cells as normal would report every unmonitored patch of ocean
            as clean.
          </p>
          <div className="muted t-meta">
            Busiest cells: {(d.items || []).slice(0, 5).map((b) =>
              `${b.h3_cell} (${b.n_observations.toLocaleString()} obs, ${b.n_vessels} vessels)`
            ).join(" · ") || "none"}
          </div>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------

function EoCard() {
  const [d, err] = useEndpoint(() => api.eoSummary());
  if (err || !d) {
    return (
      <Card title="The camera loop">
        <div className="empty">{err || "Loading…"}</div>
      </Card>
    );
  }
  const t = d.totals || {};
  return (
    <Card title="The camera loop" sub="Area 5 · ADR-037 · eo/">
      {/* The disclosure is the first thing, not a footnote, and it is repeated
          on every capture frame below. */}
      <div className="notebar" style={{ borderLeft: "3px solid var(--amber)" }}>
        {d.disclosure}
      </div>

      {!d.available ? (
        <div className="chk chk-not_checkable" style={{ marginTop: 10 }}>
          <div className="chk-head"><Outcome value="not_checkable" /></div>
          <div className="chk-statement">{d.note}</div>
        </div>
      ) : (
        <>
          <div className="statstrip" style={{ padding: "12px 0" }}>
            <div className="stat">
              <div className="label">captures</div>
              <div className="figures">
                <span className="real">{(t.captures || 0).toLocaleString()}</span>
              </div>
            </div>
            <div className="stat">
              <div className="label">subjects imaged</div>
              <div className="figures">
                <span className="real">{(t.subjects || 0).toLocaleString()}</span>
              </div>
            </div>
            <div className="stat">
              <div className="label">type claimed</div>
              <div className="figures">
                <span className="real">{(t.type_claimed || 0).toLocaleString()}</span>
              </div>
            </div>
            <div className="stat">
              <div className="label">empty frames</div>
              <div className="figures">
                <span className="real">{(t.empty_frames || 0).toLocaleString()}</span>
              </div>
            </div>
          </div>

          <div className="muted t-meta">
            Mean image quality {num(t.mean_quality, 2)} at a mean range of{" "}
            {num(t.mean_range_km, 1)} km.
          </div>

          <div style={{ fontWeight: 600, margin: "14px 0 4px" }}>
            Where the looks came from
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead><tr>
                <th className="no-sort">station</th>
                <th className="no-sort num">captures</th>
                <th className="no-sort num">mean quality</th>
                <th className="no-sort num">mean range</th>
              </tr></thead>
              <tbody>
                {d.stations.map((s) => (
                  <tr key={s.station}>
                    <td>{s.station}</td>
                    <td className="num">{s.captures.toLocaleString()}</td>
                    <td className="num">{num(s.mean_quality, 2)}</td>
                    <td className="num">{num(s.mean_range_km, 1)} km</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="notebar" style={{ marginTop: 12 }}>
            {d.empty_frame_note}
          </div>

          <div style={{ fontWeight: 600, margin: "14px 0 4px" }}>
            Why the camera looked where it did
          </div>
          <div className="muted t-meta" style={{ marginBottom: 8 }}>
            {d.cue_note}
          </div>
          {d.recent.map((c) => <CaptureCard key={c.capture_id} c={c} />)}
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------

export function MethodView() {
  return (
    <div className="page page-narrow">
      <div className="page-head">
        <div className="eyebrow">Method</div>
        <h2 style={{ margin: "3px 0 0" }}>
          What is checked, what was measured, where it stops
        </h2>
        <p className="muted-2 prose" style={{ maxWidth: 680 }}>
          Read once, not every shift. Nothing here is a queue and nothing here
          can be actioned. It is the answer to "what is this able to tell me",
          including the parts where the honest answer is that it cannot tell
          you anything. Every figure on this page is measured on the corpus
          currently landed, which is generated, and says so.
        </p>
      </div>

      <ChecksCoverage />
      <VesselTypeCard />
      <InteractionsCard />
      <BaselinesCard />
      <EoCard />
    </div>
  );
}
