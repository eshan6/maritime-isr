// Alerts — a short, high-signal list, NOT a busy queue. A near-empty queue is by
// design; the depth lives in the evidence chains and the entity pages. Each alert renders its evidence as a readable sequence of
// hops (edge type, confidence, time window) and carries disposition buttons
// wired to persist — the analyst feedback loop.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { anomalyLabel, edgeTypeLabel, fmtDateTime, num, ANOMALY_META } from "../lib/format.js";

export function AlertsView() {
  const [alerts, setAlerts] = useState(null);
  const [count, setCount] = useState({ real: 0, synthetic: 0 });
  const [busy, setBusy] = useState(null);
  const nav = useNavigate();

  function load() {
    api.alerts({}).then((r) => {
      setAlerts(r.items);
      setCount(r.count);
    });
  }
  useEffect(load, []);

  async function dispose(id, label) {
    setBusy(id + label);
    try {
      await api.dispose(id, label);
      load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="scroll-y">
      <div className="toolbar">
        <div>
          <div className="eyebrow">Alert queue</div>
          <div className="muted" style={{ fontSize: 12.5 }}>
            {count.real + count.synthetic} open. Deliberately short — high precision over volume.
          </div>
        </div>
        <div className="nav-spacer" />
      </div>

      <div className="pad" style={{ maxWidth: 860 }}>
        {!alerts && <div className="empty">Loading…</div>}
        {alerts && alerts.length === 0 && (
          <div className="notebar">
            No alerts. A near-empty queue is by design — the value is in the entity
            pages, not the count.
          </div>
        )}
        {alerts?.map((a) => (
          <AlertCard key={a.id} a={a} onDispose={dispose} busy={busy} nav={nav} />
        ))}
      </div>
    </div>
  );
}

function AlertCard({ a, onDispose, busy, nav }) {
  const tone = ANOMALY_META[a.anomaly_type]?.tone || "neutral";
  const toneColor = { finding: "var(--red)", candidate: "var(--amber)", neutral: "var(--ink-2)" }[tone];
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: toneColor }} />
        <h3 style={{ fontSize: 15 }}>{anomalyLabel(a.anomaly_type)}</h3>
        <span className="muted mono" style={{ fontSize: 12 }}>
          conf {num(a.confidence, 2)}
          {a.score != null ? ` · score ${num(a.score, 2)}` : ""}
        </span>
        <div className="nav-spacer" />
        <span
          className={`badge ${a.disposition === "open" ? "badge-neutral" : "badge-candidate"}`}
        >
          {a.disposition}
        </span>
      </div>

      <div style={{ marginTop: 4 }}>
        <button
          className="navlink"
          style={{ padding: 0, background: "none", border: "none", color: "var(--blue)" }}
          onClick={() => nav(`/vessels/${encodeURIComponent(a.subject)}`)}
        >
          {a.subject_name || a.subject}
        </button>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="eyebrow" style={{ marginBottom: 8 }}>Evidence chain</div>
        <div className="chain">
          {a.evidence.map((h, i) => (
            <div className="hop" key={i}>
              <div className="rail">
                <span className="node" />
                <span className="line" />
              </div>
              <div className="body">
                <div className="edge-label">{edgeTypeLabel(h.edge)}</div>
                <div className="edge-meta">
                  {h.confidence != null ? `confidence ${num(h.confidence, 2)}` : ""}
                  {h.source ? ` · via ${h.source}` : ""}
                  {h.t_start ? ` · ${fmtDateTime(h.t_start)}` : ""}
                </div>
                {h.detail && <div className="edge-detail">{h.detail}</div>}
                {!h.detail && h.dst && (
                  <div className="edge-detail mono muted">{h.src} → {h.dst}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
        <button
          className="btn btn-sm btn-primary"
          disabled={busy === a.id + "confirm"}
          onClick={() => onDispose(a.id, "confirm")}
        >
          Confirm
        </button>
        <button className="btn btn-sm" disabled={busy === a.id + "watch"} onClick={() => onDispose(a.id, "watch")}>
          Watch
        </button>
        <button className="btn btn-sm" disabled={busy === a.id + "dismiss"} onClick={() => onDispose(a.id, "dismiss")}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
