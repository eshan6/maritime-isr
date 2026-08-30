// Vessels — a sortable, filterable table. Columns: name, MMSI, flag, type, risk,
// sanctions status, last seen. Clicking a row opens the entity page.
// Sparse-friendly: risk and length degrade to "—" / "not available".
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { fmtDate, riskBand } from "../lib/format.js";
import { FindOnMap, RiskPill, SanctionsBadge } from "../components/bits.jsx";

const COLS = [
  { key: "name", label: "Name" },
  { key: "mmsi", label: "MMSI", mono: true },
  { key: "flag", label: "Flag" },
  { key: "vessel_type", label: "Type" },
  { key: "risk_score", label: "Risk", num: true },
  { key: "sanctioned", label: "Sanctions" },
  { key: "last_seen", label: "Last seen" },
  // Not sortable and not a field — an action. Every vessel surface in the
  // product carries it, so "where is she" is always one click from her name.
  { key: "_map", label: "" },
];

export function VesselsView() {
  const [rows, setRows] = useState(null);
  const [count, setCount] = useState({ real: 0, synthetic: 0 });
  const [sort, setSort] = useState({ key: "risk_score", dir: -1 });
  const [q, setQ] = useState("");
  const [flag, setFlag] = useState("");
  const [sanctioned, setSanctioned] = useState(false);
  const nav = useNavigate();

  useEffect(() => {
    const params = { limit: 1000 };
    if (q) params.q = q;
    if (flag) params.flag = flag;
    if (sanctioned) params.sanctioned = true;
    api.vessels(params).then((r) => {
      setRows(r.items);
      setCount(r.count);
    });
  }, [q, flag, sanctioned]);

  const flags = useMemo(() => {
    const s = new Set((rows || []).map((r) => r.flag).filter(Boolean));
    return [...s].sort();
  }, [rows]);

  const sorted = useMemo(() => {
    if (!rows) return [];
    const arr = [...rows];
    arr.sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      if (x < y) return -sort.dir;
      if (x > y) return sort.dir;
      return 0;
    });
    return arr;
  }, [rows, sort]);

  function toggleSort(key) {
    setSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: key === "name" ? 1 : -1 }));
  }

  return (
    <div className="scroll-y">
      <div className="toolbar">
        <input
          className="input"
          placeholder="Search name / MMSI / IMO / flag"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 260 }}
        />
        <select className="select" value={flag} onChange={(e) => setFlag(e.target.value)}>
          <option value="">All flags</option>
          {flags.map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
        <label className="layer-toggle">
          <input type="checkbox" checked={sanctioned} onChange={(e) => setSanctioned(e.target.checked)} />
          Sanctioned only
        </label>
        <div className="nav-spacer" />
        <span className="muted t-meta mono">
          {(count.real + count.synthetic).toLocaleString()} vessels
        </span>
      </div>

      <div className="pad">
        <div className="card" style={{ overflow: "hidden" }}>
          <table className="table">
            <thead>
              <tr>
                {COLS.map((c) => (
                  <th key={c.key} className={c.num ? "num" : ""} onClick={() => toggleSort(c.key)}>
                    {c.label}
                    {sort.key === c.key && <span className="sort-caret">{sort.dir < 0 ? "▼" : "▲"}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((v) => (
                <tr
                  key={v.id}
                  onClick={() => nav(`/vessels/${encodeURIComponent(v.id)}`)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="t-med">
                    {v.name || <span className="na">Unnamed</span>}
                  </td>
                  <td className="mono">{v.mmsi || "-"}</td>
                  <td>{v.flag || "-"}</td>
                  <td>{v.vessel_type || <span className="na">-</span>}</td>
                  <td className="num">
                    <RiskPill score={v.risk_score} />
                  </td>
                  <td>
                    {v.sanctioned ? (
                      <SanctionsBadge sanctioned isFinding={v.sanctions_is_finding} />
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                  <td className="mono muted">{fmtDate(v.last_seen) || "-"}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <FindOnMap id={v.id} name={v.name} compact />
                  </td>
                </tr>
              ))}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={COLS.length}>
                    <div className="empty">No vessels match these filters.</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
