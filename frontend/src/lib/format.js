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

export const RISK_COMPONENT_COLOR = {
  anomaly_history: "#1a5fb4",
  sanction_proximity: "#b0221b",
  flag_opacity: "#9a6300",
  fingerprint_deviation: "#1f7a4d",
};

export const RISK_COMPONENT_LABEL = {
  anomaly_history: "Anomaly history",
  sanction_proximity: "Sanction proximity",
  flag_opacity: "Flag opacity",
  fingerprint_deviation: "Fingerprint deviation",
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

export function nodeTypeColor(t) {
  return (
    {
      vessel: "#1a5fb4",
      identity: "#7a8794",
      flag_state: "#1f7a4d",
      port: "#9a6300",
      organization: "#6039c4",
      sanctions_authority: "#b0221b",
      ais_gap: "#b0221b",
      zone: "#9a6300",
    }[t] || "#8996a3"
  );
}

// short id -> the last, human-ish segment (vessel:gfw:spine -> spine)
export function shortId(id) {
  if (!id) return "";
  const seg = String(id).split(":").pop();
  return seg;
}
