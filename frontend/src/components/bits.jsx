// Small shared presentational pieces. Kept together so the visual language of
// "finding vs candidate" and "risk" is defined once.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { NA, num, riskBand, riskLabel, RISK_COMPONENT_LABEL,
         FAMILY_ORDER, familyColor, familyLabel, fmtDateTime,
         provenanceLine } from "../lib/format.js";

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

// ---------------------------------------------------------------------------
// Find her on the map
// ---------------------------------------------------------------------------

// Every vessel anywhere in the product carries this. The operator's next
// question after "who is she and why is she flagged" is almost always "where is
// she", and until now the only answer was to leave the screen, open the map and
// hunt — through a picture holding thousands of tracks.
//
// **It goes to her last known position when there is no live one, and says
// which it did.** In the deployed product the AIS feed is live and most hulls
// have a current fix; the ones worth looking at are frequently the ones that do
// not, because going dark is the finding. A button that silently showed a
// six-hour-old position as though it were current would be lying at exactly the
// moment the operator most needs to know — so the map states the age of the fix
// on arrival rather than the button pretending it is fresh.
export function FindOnMap({ id, name, compact = false }) {
  const nav = useNavigate();
  if (!id) return null;
  return (
    <button
      className="btn-map"
      title={`Show ${name || "this vessel"} on the map — her live position, or her last known one if she is not currently reporting`}
      onClick={(e) => {
        e.stopPropagation();   // rows are clickable; this is not a row click
        nav(`/?vessel=${encodeURIComponent(id)}`);
      }}
    >
      <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M8 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.2 4 8.3 4.2 8.5a.4.4 0 0 0 .6 0c.2-.2 4.2-5.3 4.2-8.5 0-2.5-2-4.5-4.5-4.5z"
              fill="none" stroke="currentColor" strokeWidth="1.4" />
        <circle cx="8" cy="6" r="1.7" fill="currentColor" />
      </svg>
      {compact ? "Map" : "Find on map"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// The score makeup bar, and the legend that makes it mean something
// ---------------------------------------------------------------------------

// One vessel's score, divided between the factors that produced it.
//
// Segments are laid out in a fixed family order rather than by size, so two
// rows can be compared by eye: the same colour is always in the same place. The
// track behind them is the full 0–1 scale, so a bar that fills a third of the
// row is a score of about 0.33 — the length means something on its own, which a
// bar normalised to the top-scoring row would not.
export function MakeupBar({ factors, score }) {
  const fs = [...(factors || [])].sort(
    (a, b) => FAMILY_ORDER.indexOf(a.family) - FAMILY_ORDER.indexOf(b.family));
  return (
    <span className="mkbar" role="img"
          aria-label={`score ${(score || 0).toFixed(2)} of 1, made up of `
            + fs.map((f) => `${f.kind.replace(/_/g, " ")} ${f.points.toFixed(2)}`).join(", ")}>
      {fs.map((f) => (
        <i key={f.factor_id}
           style={{ width: `${100 * (f.points || 0)}%`, background: familyColor(f.family) }}
           title={`${familyLabel(f.family)} — ${f.kind.replace(/_/g, " ")}: `
                  + `${f.points.toFixed(3)} of ${(score || 0).toFixed(3)}`} />
      ))}
      <i className="mkbar-rest" />
    </span>
  );
}

// Shown once above a list, never per row.
export function FamilyLegend({ families }) {
  const shown = FAMILY_ORDER.filter((f) => !families || families.has(f));
  if (shown.length === 0) return null;
  return (
    <div>
      <div className="muted t-meta">
        The bar is the score out of 1, split into the factors that built it.
        Colour is the kind of evidence a factor came from; length is the points
        it contributed. A short bar is a low score, not a small chart.
      </div>
      <div className="legend">
        {shown.map((f) => (
          <span className="legend-item" key={f}>
            <span className="legend-swatch" style={{ background: familyColor(f) }} />
            {familyLabel(f)}
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Evidence
// ---------------------------------------------------------------------------

// One evidence item is a block, not a line.
//
// The fact goes first, at reading weight. Under it, when it happened, and who
// says so — and, where this system derived the claim rather than receiving it,
// what it did to get there. That last line is the one that used to read
// `source graph / events`, which named a table inside this repository: an
// operator cannot audit a folder, and a product whose whole proposition is
// traceable trust cannot cite one.
export function EvidenceList({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="evi">
      {items.map((e, i) => {
        const { origin, derivation } = provenanceLine(e.provenance);
        return (
          <li key={i}>
            <div className="evi-fact">{e.label}</div>
            {e.occurred_at && (
              <div className="evi-when">{fmtDateTime(e.occurred_at)}</div>
            )}
            <div className="evi-src">
              Source: <span className="who">{origin}</span>
              {e.confidence != null && (
                <span className="muted"> · confidence {num(e.confidence, 2)}</span>
              )}
            </div>
            {derivation && <div className="evi-derived">{derivation}</div>}
          </li>
        );
      })}
    </ul>
  );
}
