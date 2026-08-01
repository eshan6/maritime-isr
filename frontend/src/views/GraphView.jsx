// Graph — seed-and-expand, never a hairball. Opens on one vessel and expands one
// hop per click on a vessel node. Every edge is labelled with its type; tapping
// an edge shows its confidence and time window in the side panel. Node colour is
// by type; synthetic nodes get a violet dashed ring. Renders correctly on a
// star-shaped real neighbourhood (a hull touching only flags/ports) and a
// connected synthetic one alike.
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import { api } from "../api.js";
import { edgeTypeLabel, fmtDate, num, nodeTypeColor, shortId } from "../lib/format.js";

export function GraphView() {
  const elRef = useRef(null);
  const cyRef = useRef(null);
  const [params, setParams] = useSearchParams();
  const [seedInput, setSeedInput] = useState(params.get("seed") || "");
  const [info, setInfo] = useState(null);
  const [expanded, setExpanded] = useState(new Set());
  const [vessels, setVessels] = useState([]);
  const [status, setStatus] = useState("");

  // vessel picker options
  useEffect(() => {
    api.vessels({ limit: 1000 }).then((r) => setVessels(r.items));
  }, []);

  // init cytoscape once
  useEffect(() => {
    const cy = cytoscape({
      container: elRef.current,
      minZoom: 0.2,
      maxZoom: 3,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n) => n.data("color"),
            label: "data(label)",
            color: "#16212e",
            "font-size": 11,
            "font-family": "Inter, system-ui, sans-serif",
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: (n) => (n.data("kind") === "vessel" ? 26 : 16),
            height: (n) => (n.data("kind") === "vessel" ? 26 : 16),
            "border-width": 2,
            "border-color": "#fff",
          },
        },
        {
          selector: "node[synthetic = 1]",
          style: { "border-color": "#6039c4", "border-style": "dashed", "border-width": 2.5 },
        },
        {
          selector: "node[kind = 'vessel']",
          style: { "border-color": "#124a8f" },
        },
        { selector: "node:selected", style: { "border-color": "#1a5fb4", "border-width": 4 } },
        {
          selector: "edge",
          style: {
            width: 1.4,
            "line-color": "#c2ccd6",
            "target-arrow-color": "#c2ccd6",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.8,
            "curve-style": "bezier",
            label: "data(label)",
            "font-size": 9,
            color: "#8996a3",
            "text-rotation": "autorotate",
            "text-background-color": "#fff",
            "text-background-opacity": 0.85,
            "text-background-padding": 1,
          },
        },
        { selector: "edge[synthetic = 1]", style: { "line-style": "dashed" } },
        { selector: "edge:selected", style: { "line-color": "#1a5fb4", "target-arrow-color": "#1a5fb4", width: 2.5 } },
      ],
    });
    cy.on("tap", "node", (e) => {
      const d = e.target.data();
      setInfo({ type: "node", data: d });
      if (d.kind === "vessel") expand(d.id);
    });
    cy.on("tap", "edge", (e) => setInfo({ type: "edge", data: e.target.data() }));
    cy.on("tap", (e) => {
      if (e.target === cy) setInfo(null);
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  // load seed when it changes
  useEffect(() => {
    const seed = params.get("seed");
    if (seed && cyRef.current) {
      cyRef.current.elements().remove();
      setExpanded(new Set());
      expand(seed, true);
    }
  }, [params]);

  async function expand(vesselId, isSeed = false) {
    if (expanded.has(vesselId)) return;
    setStatus("expanding…");
    try {
      const nb = await api.neighbourhood(vesselId, 1);
      const cy = cyRef.current;
      const add = [];
      for (const n of nb.nodes) {
        if (cy.getElementById(n.id).length) continue;
        add.push({
          data: {
            id: n.id, label: n.label || shortId(n.id), kind: n.node_type,
            color: nodeTypeColor(n.node_type), synthetic: n.is_synthetic ? 1 : 0,
            props: n.props,
          },
        });
      }
      for (const e of nb.edges) {
        const eid = `${e.edge_type}|${e.source}|${e.target}|${e.t_start || ""}`;
        if (cy.getElementById(eid).length) continue;
        add.push({
          data: {
            id: eid, source: e.source, target: e.target,
            label: edgeTypeLabel(e.edge_type), edge_type: e.edge_type,
            confidence: e.confidence, t_start: e.t_start, t_end: e.t_end,
            synthetic: e.is_synthetic ? 1 : 0,
          },
        });
      }
      cy.add(add);
      setExpanded((s) => new Set(s).add(vesselId));
      cy.layout({ name: "cose", animate: true, animationDuration: 350, padding: 40, nodeRepulsion: 9000 }).run();
      if (isSeed) cy.getElementById(vesselId).select();
      setStatus(nb.truncated ? "traversal budget reached — showing a partial neighbourhood" : "");
    } catch (e) {
      setStatus(`could not expand: ${String(e).slice(0, 80)}`);
    }
  }

  function seed() {
    if (seedInput) setParams({ seed: seedInput });
  }

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={elRef} className="graph-canvas" />

      <div className="layerbox graph-help" style={{ width: 260 }}>
        <h4>Graph — seed & expand</h4>
        <select
          className="select"
          style={{ width: "100%" }}
          value={seedInput}
          onChange={(e) => setSeedInput(e.target.value)}
        >
          <option value="">Choose a vessel…</option>
          {vessels.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name || shortId(v.id)}
              {v.is_synthetic ? " (scenario)" : ""}
            </option>
          ))}
        </select>
        <button className="btn btn-sm btn-primary" style={{ marginTop: 8, width: "100%" }} onClick={seed}>
          Seed graph
        </button>
        <p className="muted" style={{ fontSize: 11.5, marginTop: 8, marginBottom: 0 }}>
          Click a vessel node (blue-ringed) to expand one hop. Other nodes are leaves.
        </p>
        {status && <p className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{status}</p>}
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          {["vessel", "flag_state", "port", "identity", "sanctions_authority", "ais_gap"].map((k) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: "50%" }} />
              {k.replace(/_/g, " ")}
            </div>
          ))}
          <div className="legendline">
            <span className="layer-swatch" style={{ border: "2px dashed #6039c4", borderRadius: "50%", background: "#fff" }} />
            scenario node
          </div>
        </div>
      </div>

      {info && (
        <div className="card" style={{ position: "absolute", top: 12, right: 12, width: 300, padding: 14, zIndex: 5 }}>
          {info.type === "node" ? (
            <>
              <div className="eyebrow">{info.data.kind.replace(/_/g, " ")}</div>
              <h3 style={{ fontSize: 15, marginTop: 4 }}>{info.data.label}</h3>
              {info.data.synthetic === 1 && <span className="badge badge-scenario">SCENARIO</span>}
              <dl className="kv" style={{ marginTop: 10 }}>
                {Object.entries(info.data.props || {}).slice(0, 8).map(([k, val]) => (
                  <>
                    <dt key={k + "k"}>{k}</dt>
                    <dd key={k + "v"} className="mono" style={{ fontSize: 12 }}>{String(val)}</dd>
                  </>
                ))}
              </dl>
            </>
          ) : (
            <>
              <div className="eyebrow">Edge</div>
              <h3 style={{ fontSize: 15, marginTop: 4 }}>{edgeTypeLabel(info.data.edge_type)}</h3>
              {info.data.synthetic === 1 && <span className="badge badge-scenario">SCENARIO</span>}
              <dl className="kv" style={{ marginTop: 10 }}>
                <dt>Confidence</dt>
                <dd>{num(info.data.confidence, 3)}</dd>
                <dt>From</dt>
                <dd>{fmtDate(info.data.t_start) || "—"}</dd>
                <dt>To</dt>
                <dd>{info.data.t_end ? fmtDate(info.data.t_end) : "current"}</dd>
              </dl>
            </>
          )}
        </div>
      )}

      {expanded.size === 0 && (
        <div className="empty" style={{ position: "absolute", top: "45%", left: 0, right: 0 }}>
          Pick a vessel and seed the graph to begin.
        </div>
      )}
    </div>
  );
}
