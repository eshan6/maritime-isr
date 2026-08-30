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

// Dates are DD/MM/YYYY across the whole product, and the whole product means
// every surface: tables, evidence lists, alert cards, exported reports.
//
// Two reasons it is one function and not a call to `toLocaleDateString`. The
// operator's browser locale is not the operator's convention — a laptop shipped
// with en-US renders 03/08 as the third of August to the machine and the eighth
// of March to the watchkeeper reading it, and neither of them is told. And a
// format that changes with the viewer makes two people looking at the same
// incident report disagree about when it happened. Fixed format, stated once.
//
// Times stay UTC and keep the Z. Maritime work is done in UTC and a local-time
// stamp on a position report is a different claim from the one the sensor made.
export function fmtDate(iso, withTime = false) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const date = `${dd}/${mm}/${d.getUTCFullYear()}`;
  if (!withTime) return date;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${date} ${hh}:${mi}Z`;
}

export function fmtDateTime(iso) {
  return fmtDate(iso, true);
}

// ---------------------------------------------------------------------------
// Evidence families — the one categorical palette in the product
// ---------------------------------------------------------------------------
//
// Six families, a fixed hue each, assigned in this order and never cycled or
// re-assigned by rank. Colour follows the family, so a filter that changes which
// families are on screen never repaints the survivors — the blue block means
// "motion" on every row of every screen or it means nothing.
//
// The previous mapping reused the interface's own semantic colours: `--amber`
// (#9a6300, a brown) for identity and `--red` (#b0221b) for network. Two
// problems, and the visible one was the smaller. Brown beside red is a poor
// pairing, but red is also the product's *status* colour — it means risk, on
// badges, on risk pills, on the graph canvas. Spending it as "category three"
// meant a red block on a score bar and a red badge beside it were the same red
// saying two unrelated things. Status colours stay reserved.
//
// This set is validated rather than chosen by eye: worst adjacent pair is
// ΔE 9.1 under protanopia and 19.6 under normal vision (OKLab ×100, against
// floors of 8 and 15). Three of the six sit under 3:1 contrast on the light
// surface, which obliges a written label everywhere the colour appears — and
// every use here has one, on the chip, in the legend, and in the breakdown
// table. The colour reinforces the label; it never carries identity alone.
export const FAMILY_COLOR = {
  motion: "#2a78d6",     // blue
  identity: "#eb6834",   // orange
  network: "#1baf7a",    // aqua
  paperwork: "#eda100",  // yellow
  imagery: "#e87ba4",    // magenta
  radio: "#008300",      // green
};

export const FAMILY_LABEL = {
  motion: "How she moves",
  identity: "Who she says she is",
  network: "Who she is connected to",
  paperwork: "What she filed",
  imagery: "What she looks like",
  radio: "What she transmits",
};

//: Fixed render order, so segments in a stacked bar are laid out the same way
//: on every row and two rows can be compared by eye.
export const FAMILY_ORDER = ["motion", "identity", "network", "paperwork",
  "imagery", "radio"];

export function familyColor(f) {
  return FAMILY_COLOR[f] || "#8996a3";
}

export function familyLabel(f) {
  return FAMILY_LABEL[f] || (f || "other").replace(/_/g, " ");
}

// A provenance envelope as one readable line. `origin` and `derivation` are
// added server-side by `assistant/attribution.py`; older payloads that carry
// only the raw ids fall back to those rather than rendering nothing, but the
// raw id is humanised on the way out so a source never reads as a file path.
export function provenanceLine(prov) {
  if (!prov) return { origin: "not attributed", derivation: null };
  const origin = prov.origin
    || (prov.source_id ? String(prov.source_id).replace(/[_-]/g, " ")
                       : "not attributed");
  return { origin, derivation: prov.derivation || null };
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

// What each KIND of identity edge actually asserts. `identified-as` is the one
// edge type in the ontology whose name carries no information: every vessel has
// several, and on the canvas they were all drawn "identified as" — the one
// thing they have in common. An MMSI, an IMO and a painted name are three
// claims of very different strength (a hull number is welded on; a name is
// paint), and the label is where that distinction is free to make.
//
// Keys are `schemas.keys.IDENTITY_KINDS`, which is a closed vocabulary; the
// server only sends a kind that is in it, and an unknown one falls back to the
// generic relationship label rather than being rendered raw.
const IDENTITY_KIND_LABEL = {
  mmsi: "MMSI",
  imo: "IMO number",
  call_sign: "call sign",
  name: "ship name",
  flag: "flag",
};

export function edgeTypeLabel(t, identityKind) {
  if (t === "identified-as" && IDENTITY_KIND_LABEL[identityKind]) {
    return IDENTITY_KIND_LABEL[identityKind];
  }
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

//: The label for one edge as the API returns it — the shape every caller with a
//: whole edge object should use, so the identity-kind argument cannot be
//: forgotten at one call site and passed at another.
export function edgeLabel(edge) {
  if (!edge) return "";
  return edgeTypeLabel(edge.edge_type, edge.identity_kind);
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
