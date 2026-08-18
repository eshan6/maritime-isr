// Small shared presentational pieces. Kept together so the visual language of
// "finding vs candidate" and "risk" is defined once.
import { useState } from "react";
import { api } from "../api.js";
import { NA, num, riskBand, riskLabel, RISK_COMPONENT_LABEL } from "../lib/format.js";

export function NAtext() {
  return <span className="na">{NA}</span>;
}

// Renders a value or a grey "not available" — the graceful-null contract.
// Upright, never italic: the absence is carried by colour, which stays legible
// at 12px where a slant does not.
export function Value({ v, suffix = "" }) {
  if (v === null || v === undefined || v === "") return <NAtext />;
  return (
    <span>
      {v}
      {suffix}
    </span>
  );
}

// Sanctions treatment keys on is_finding, never on mere presence: a name-only
// candidate must never wear the red "finding" mark (ADR-018).
export function SanctionsBadge({ sanctioned, isFinding, tier }) {
  if (!sanctioned) return null;
  if (isFinding)
    return (
      <span className="badge badge-finding" title={`match tier: ${tier || "imo"}`}>
        Sanctions finding
      </span>
    );
  return (
    <span className="badge badge-candidate" title={`match tier: ${tier || "name"}`}>
      Sanctions candidate
    </span>
  );
}

// The one-click incident report (CLAUDE.md §0 — the last named piece of the M6
// demo). Reports its own state rather than failing silently: a button that
// appears to do nothing is worse than one that says it could not.
export function ExportButton({ id, primary = false, label = "Export report" }) {
  const [state, setState] = useState("idle");
  return (
    <button
      className={`btn btn-sm ${primary ? "btn-primary" : ""}`}
      disabled={state === "working"}
      title="Download a self-contained incident report — opens in any browser, prints to PDF"
      onClick={async () => {
        setState("working");
        try {
          await api.downloadReport(id);
          setState("done");
          setTimeout(() => setState("idle"), 2500);
        } catch (e) {
          setState("failed");
          setTimeout(() => setState("idle"), 4000);
        }
      }}
    >
      {{ idle: label, working: "Building…", done: "Downloaded ✓",
         failed: "Export failed" }[state]}
    </button>
  );
}

export function RiskPill({ score }) {
  const band = riskBand(score);
  return (
    <span className={`risk-pill risk-${band}`}>
      <span className="risk-dot" />
      {score === null || score === undefined ? "—" : num(score, 2)}
      <span className="risk-band">{riskLabel(band)}</span>
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
    <span className="mono muted t-micro" title="provenance envelope">
      {bits}
    </span>
  );
}

export function StatTile({ label, real, synthetic }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="figures">
        <span className="real">{(real + synthetic).toLocaleString()}</span>
      </div>
    </div>
  );
}

// The decomposed risk score.
//
// Two bugs lived here and both showed on screen as "the bars do not work":
//
//   1. `.rbar-fill` was an inline <span>, and an inline box ignores `height`.
//      Every bar rendered as an empty track regardless of the numbers beside
//      it. Fixed in the stylesheet (both track and fill are block-level).
//   2. The width was `weighted / max(weighted)`, which normalises the largest
//      component to a full bar no matter how small it is. A vessel scoring
//      0.037 on one component and zero on the rest drew one FULL bar and three
//      empty ones — a picture of "maximum flag risk" for a hull barely above
//      zero. Each bar now shows the component's own value on its own 0–1
//      scale, which is the thing the weight is applied to, and the number
//      beside it is the weighted contribution to the composite.
export function RiskDecomposition({ risk }) {
  if (!risk) return <div className="empty">No risk graph for this vessel.</div>;
  const comps = Object.entries(risk.components || {});
  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 14 }}>
        <span className="t-hero">{num(risk.risk_score, 3)}</span>
        <span className="muted t-meta">composite risk (0–1), decomposed below</span>
      </div>

      <div className="risk-decomp">
        {comps.map(([name, c]) => {
          // `value` is the component on its own 0–1 scale; `weighted` is what
          // it contributed to the composite. Older payloads carried only the
          // weighted figure, so fall back to it rather than drawing nothing.
          const weight = Number(c.weight) || 0;
          const value = c.value != null
            ? Number(c.value)
            : weight > 0 ? Number(c.weighted) / weight : 0;
          const pct = Math.max(0, Math.min(1, value || 0)) * 100;
          return (
            <div className={`rbar-row ${pct === 0 ? "is-zero" : ""}`} key={name}>
              <span className="rbar-label">
                {RISK_COMPONENT_LABEL[name] || name}
              </span>
              <span
                className="rbar-track"
                title={`${RISK_COMPONENT_LABEL[name] || name}: ${num(value, 2)} of 1`
                  + (weight ? ` × weight ${num(weight, 2)}` : "")}
              >
                {/* A non-zero component keeps a visible sliver: a 1% bar that
                    renders as nothing is indistinguishable from a zero, and
                    those mean different things. */}
                <span className="rbar-fill" style={{ width: `${pct === 0 ? 0 : Math.max(2, pct)}%` }} />
              </span>
              <span className="rbar-value mono">{num(c.weighted, 3)}</span>
            </div>
          );
        })}
      </div>

      {risk.evidence?.length > 0 && (
        <ul className="prose t-meta muted-2" style={{ margin: "14px 0 0", paddingLeft: 18 }}>
          {risk.evidence.map((e, i) => (
            <li key={i}>
              <span className="t-med">{RISK_COMPONENT_LABEL[e.kind] || e.kind}:</span>{" "}
              {e.detail} <span className="muted">(+{num(e.contribution, 3)})</span>
            </li>
          ))}
        </ul>
      )}

      <p className="muted t-meta" style={{ marginTop: 12, marginBottom: 0 }}>
        The bar is the component on its own 0–1 scale; the figure beside it is
        what that component contributed after its weight. The score is their
        sum — never a bare number.
      </p>
    </div>
  );
}
