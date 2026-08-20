// Presentation helpers. The one rule that runs through all of them: a null
// field degrades to an explicit "not available", never a blank or a zero — most
// vessels lack a length, many lack an IMO.

export const NA = "not available";

export function orNA(v, suffix = "") {
  if (v === null || v === undefined || v === "") return null; // caller renders <NA/>
  return suffix ? `${v}${suffix}` : String(v);
}

export function num(v, digits = 1, suffix = "") {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  const n = Number(v);
  const s = Number.isInteger(n) ? String(n) : n.toFixed(digits);
  return suffix ? `${s}${suffix}` : s;
}

export function fmtDate(iso, withTime = false) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const date = d.toISOString().slice(0, 10);
  if (!withTime) return date;
  return `${date} ${d.toISOString().slice(11, 16)}Z`;
}

export function fmtDateTime(iso) {
  return fmtDate(iso, true);
}

// Risk banding — thresholds chosen for a high-precision, sparse corpus.
// The scores are small here (few alerts), so the bands are deliberately low;
// the point is a legible three-step hierarchy, not a calibrated probability.
export function riskBand(score) {
  if (score === null || score === undefined) return "none";
  if (score >= 0.3) return "high";
  if (score >= 0.12) return "elevated";
  if (score > 0) return "low";
  return "none";
}

export function riskLabel(band) {
  return { high: "High", elevated: "Elevated", low: "Low", none: "—" }[band];
}

// One label table for both the component rows and the evidence list under
// them. The evidence entries use shorter kind names than the components they
// belong to (`anomaly`, `fingerprint_dev`), and an unmapped kind used to print
// the raw enum straight into an analyst-facing sentence.
export const RISK_COMPONENT_LABEL = {
  anomaly_history: "Anomaly history",
  anomaly: "Anomaly history",
  sanction_proximity: "Sanction proximity",
  flag_opacity: "Flag opacity",
  fingerprint_deviation: "Fingerprint deviation",
  fingerprint_dev: "Fingerprint deviation",
};

// Anomaly type -> a plain-English label + severity hue class.
export const ANOMALY_META = {
  dark_vessel: { label: "Dark vessel", tone: "finding" },
  dark_rendezvous: { label: "Dark rendezvous", tone: "candidate" },
  ais_spoofing: { label: "AIS spoofing", tone: "candidate" },
  loitering_sensitive: { label: "Loitering in sensitive zone", tone: "candidate" },
  identity_then_anomaly: { label: "Identity change then anomaly", tone: "finding" },
  port_risk_propagation: { label: "Port-risk propagation", tone: "neutral" },
};

export function anomalyLabel(t) {
  return ANOMALY_META[t]?.label || t || "Anomaly";
}

export function edgeTypeLabel(t) {
  const m = {
    "flagged-to": "flagged to",
    "docked-at": "docked at",
    "identified-as": "identified as",
    "met-with": "met with",
    "reported-gap": "reported AIS gap",
    "sanctioned-under": "sanctioned under",
    "owned-by": "owned by",
    "loiter-in-zone": "loitered in zone",
    "duplicate_mmsi": "duplicate MMSI",
  };
  return m[t] || (t || "").replace(/[-_]/g, " ");
}

// Node colour by SEMANTIC FAMILY, not by "one hue per type".
//
//   Investigated entities — vessel, company, person — share a blue-to-navy
//   family at different depths, so an ownership chain reads as one connected
//   thing rather than a row of unrelated colours.
//   Context you read past — flag, port, identity — drops to warm grey and
//   recedes, because it is shared by hundreds of unrelated hulls and carries
//   no signal on its own.
//   Risk — sanctions authority, AIS gap — is the only red on the canvas, so a
//   red mark always means the same thing.
export function nodeTypeColor(t) {
  return (
    {
      // entity family (blue → navy, deepening with corporate distance)
      vessel: "#1a5fb4",
      organization: "#123a6e", // navy — one step deeper than a hull
      person: "#2c4f80",
      // context family (warm grey, deliberately quiet)
      flag_state: "#9aa3ad",
      port: "#8b9099",
      identity: "#b6bcc4",
      zone: "#8b9099",
      // risk family (the only reds)
      sanctions_authority: "#b0221b",
      ais_gap: "#c2554d",
    }[t] || "#a8b0b8"
  );
}

// Node display radius by type — hubs (vessels, orgs) read larger than leaves.
// Node RADIUS in screen pixels (the renderer doubles it to a diameter and
// pins that to the screen, so these numbers are what you actually see).
//
// Roughly 40% smaller than they were. Symbols and labels are both pinned to
// the screen now, so the two are directly comparable — and a 30px dot beside
// an 11px label reads as a button with a caption, not as a labelled node. At
// 18px the dot and its name carry about the same visual weight, which is what
// makes a dense network look organised rather than clunky.
//
// The RATIOS are unchanged: a company is still bigger than a vessel, context
// still recedes. Size means importance and nothing else.
export function nodeTypeSize(t) {
  return { vessel: 9, organization: 10, person: 9, sanctions_authority: 8,
    flag_state: 5.5, port: 5.5, ais_gap: 5.5, identity: 3.5 }[t] || 5;
}

// Edge colour follows the same families: ownership is the entity-blue that
// binds hulls to companies, sanctions is red, structural links are grey and
// recede. Ownership is the only saturated line on a clean graph, so the chain
// is what the eye follows.
const OWNERSHIP_EDGES = new Set(["owned-by", "operated-by"]);
const SANCTION_EDGES = new Set(["sanctioned-under"]);
export function edgeCategoryColor(t) {
  if (OWNERSHIP_EDGES.has(t)) return "#1a5fb4"; // ownership — entity blue
  if (SANCTION_EDGES.has(t)) return "#b0221b"; // sanctions — red
  if (t === "met-with") return "#c2554d"; // vessel-to-vessel encounter
  return "#cdd4dc"; // structural (flag, port, identity) — quiet grey
}

// A props key rendered as a reader-facing label: snake_case -> "Sentence case",
// with the identifier acronyms kept upper. Detail panels read as prose this way
// rather than as a dump of database column names.
const KEY_ACRONYMS = { imo: "IMO", mmsi: "MMSI", gfw: "GFW", ofac: "OFAC",
  sdn: "SDN", id: "ID", ais: "AIS", n: "No." };
export function humanKey(k) {
  const words = String(k).split(/[_\s]+/).filter(Boolean);
  return words
    .map((w, i) => {
      const a = KEY_ACRONYMS[w.toLowerCase()];
      if (a) return a;
      return i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w;
    })
    .join(" ");
}

// short id -> the last, human-ish segment (vessel:gfw:spine -> spine)
export function shortId(id) {
  if (!id) return "";
  const seg = String(id).split(":").pop();
  return seg;
}
