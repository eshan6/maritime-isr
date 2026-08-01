// Small shared presentational pieces. Kept together so the visual language of
// "synthetic vs real", "finding vs candidate", and "risk" is defined once.
import {
  NA,
  num,
  riskBand,
  riskLabel,
  RISK_COMPONENT_COLOR,
  RISK_COMPONENT_LABEL,
} from "../lib/format.js";

export function NAtext() {
  return <span className="na">{NA}</span>;
}

// Renders a value or the italic "not available" — the graceful-null contract.
export function Value({ v, suffix = "" }) {
  if (v === null || v === undefined || v === "") return <NAtext />;
  return (
    <span>
      {v}
      {suffix}
    </span>
  );
}

export function SyntheticBadge({ on }) {
  if (!on) return null;
  return <span className="badge badge-scenario">SCENARIO</span>;
}

// Sanctions treatment keys on is_finding, never on mere presence: a name-only
// candidate must never wear the red "finding" mark (ADR-018).
export function SanctionsBadge({ sanctioned, isFinding, tier }) {
  if (!sanctioned) return null;
  if (isFinding)
    return (
      <span className="badge badge-finding" title={`match tier: ${tier || "imo"}`}>
        SANCTIONS FINDING
      </span>
    );
  return (
    <span className="badge badge-candidate" title={`match tier: ${tier || "name"}`}>
      sanctions candidate
    </span>
  );
}

export function RiskPill({ score }) {
  const band = riskBand(score);
  return (
    <span className={`risk-pill risk-${band}`}>
      <span className="risk-dot" />
      {score === null || score === undefined ? "—" : num(score, 2)}
      <span className="muted" style={{ fontWeight: 500, fontSize: 11 }}>
        {riskLabel(band)}
      </span>
    </span>
  );
}

// A source-attribution chip. Dark/gap determinations are GFW's, not ours, and
// this is where the UI says so (ADR-017/018).
export function Attribution({ source }) {
  if (!source) return null;
  return (
    <span className="badge badge-neutral" title="source of this determination">
      via {source}
    </span>
  );
}

export function ProvChip({ prov }) {
  if (!prov) return null;
  const bits = [prov.source_id, prov.pipeline_version && `@${prov.pipeline_version}`]
    .filter(Boolean)
    .join(" ");
  return (
    <span className="mono muted" style={{ fontSize: 11 }} title="provenance envelope">
      {bits}
    </span>
  );
}

export function StatTile({ label, real, synthetic }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="figures">
        <span className="real">{real.toLocaleString()}</span>
        {synthetic > 0 && <span className="syn">+{synthetic} scenario</span>}
      </div>
    </div>
  );
}

export function RiskDecomposition({ risk }) {
  if (!risk) return <div className="empty">No risk graph for this vessel.</div>;
  const comps = Object.entries(risk.components);
  const maxW = Math.max(0.001, ...comps.map(([, c]) => c.weighted));
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 26, fontWeight: 700 }}>{num(risk.risk_score, 3)}</span>
        <span className="muted">composite risk (0–1), decomposed below</span>
      </div>
      <div className="risk-decomp">
        {comps.map(([name, c]) => (
          <div className="rbar-row" key={name}>
            <span>{RISK_COMPONENT_LABEL[name] || name}</span>
            <span className="rbar-track">
              <span
                className="rbar-fill"
                style={{
                  width: `${(c.weighted / maxW) * 100}%`,
                  background: RISK_COMPONENT_COLOR[name] || "#1a5fb4",
                }}
              />
            </span>
            <span className="mono" style={{ textAlign: "right" }}>
              {num(c.weighted, 3)}
            </span>
          </div>
        ))}
      </div>
      {risk.evidence?.length > 0 && (
        <ul style={{ margin: "12px 0 0", paddingLeft: 18, color: "var(--ink-2)", fontSize: 12.5 }}>
          {risk.evidence.map((e, i) => (
            <li key={i}>
              <b>{RISK_COMPONENT_LABEL[e.kind] || e.kind}:</b> {e.detail}{" "}
              <span className="muted">(+{num(e.contribution, 3)})</span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted" style={{ fontSize: 12, marginTop: 10, marginBottom: 0 }}>
        Score is the weighted sum of the components above — never a bare number.
      </p>
    </div>
  );
}
