// Graph — an ownership-network view, seed-and-expand, never a hairball. Seeding a
// vessel opens its 2-hop neighbourhood: its operator, the parent company that
// operator rolls up to, and the sibling vessels that share that owner — the
// shell-company convergence the product exists to surface. Click any vessel or
// company to expand it a further hop; hover to isolate a node and its links.
//
// Node colour is by type, size marks hubs from leaves, a red ring marks a
// sanctioned entity, and a violet dashed ring marks scenario data. Ownership
// edges are indigo, sanctions edges red, structural edges quiet grey — so the
// shape reads before you touch a single label.
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import { api } from "../api.js";
import {
  edgeCategoryColor, edgeTypeLabel, fmtDate, isHubType, nodeTypeColor,
  nodeTypeSize, num, shortId,
} from "../lib/format.js";

export function GraphView() {
  const elRef = useRef(null);
  const cyRef = useRef(null);
  const [params, setParams] = useSearchParams();
  const [seedInput, setSeedInput] = useState(params.get("seed") || "");
  const [info, setInfo] = useState(null);
  const expandedRef = useRef(new Set());
  const [vessels, setVessels] = useState([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    api.vessels({ limit: 1000 }).then((r) => setVessels(r.items));
  }, []);

  // init cytoscape once
  useEffect(() => {
    const cy = cytoscape({
      container: elRef.current,
      minZoom: 0.15,
      maxZoom: 2.5,
      wheelSensitivity: 0.3,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n) => n.data("color"),
            width: (n) => n.data("size") * 2,
            height: (n) => n.data("size") * 2,
            "border-width": 1.5,
            "border-color": "#ffffff",
            label: (n) => (n.data("hub") ? n.data("label") : ""),
            color: "#33414f",
            "font-size": 10,
            "font-family": "Inter, system-ui, sans-serif",
            "font-weight": 600,
            "text-valign": "bottom",
            "text-margin-y": 3,
            "text-outline-color": "#f6f8fa",
            "text-outline-width": 2,
            "min-zoomed-font-size": 7,
          },
        },
        { selector: "node[sanctioned = 1]", style: { "border-color": "#b0221b", "border-width": 3 } },
        { selector: "node[synthetic = 1]", style: { "border-style": "dashed", "border-color": "#6039c4", "border-width": 2 } },
        { selector: "node[kind = 'vessel']", style: { shape: "ellipse" } },
        { selector: "node[kind = 'organization']", style: { shape: "round-rectangle" } },
        { selector: "node[kind = 'sanctions_authority']", style: { shape: "diamond" } },
        { selector: "node.seed", style: { "border-color": "#1a5fb4", "border-width": 3.5 } },
        { selector: "node:selected", style: { "border-color": "#1a5fb4", "border-width": 4 } },
        {
          selector: "node.faded",
          style: { opacity: 0.18, "text-opacity": 0 },
        },
        {
          selector: "node.hl",
          style: { label: "data(label)", "z-index": 20 },
        },
        {
          selector: "edge",
          style: {
            width: (e) => (e.data("ownership") ? 2 : 1.2),
            "line-color": (e) => e.data("color"),
            "target-arrow-color": (e) => e.data("color"),
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
            opacity: 0.7,
            label: "",
            "font-size": 9,
            color: "#5a6b7b",
            "text-rotation": "autorotate",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.9,
            "text-background-padding": 2,
          },
        },
        { selector: "edge.faded", style: { opacity: 0.06 } },
        {
          selector: "edge.hl",
          style: { label: (e) => e.data("label"), width: 2.4, opacity: 1, "z-index": 19 },
        },
      ],
    });

    const highlight = (node) => {
      const neigh = node.closedNeighborhood();
      cy.elements().addClass("faded");
      neigh.removeClass("faded").addClass("hl");
    };
    const clearHl = () => cy.elements().removeClass("faded hl");

    cy.on("mouseover", "node", (e) => highlight(e.target));
    cy.on("mouseout", "node", clearHl);
    cy.on("tap", "node", (e) => {
      const d = e.target.data();
      setInfo({ type: "node", data: d });
      if (d.kind === "vessel" || d.kind === "organization") expand(d.id, 1);
    });
    cy.on("tap", "edge", (e) => setInfo({ type: "edge", data: e.target.data() }));
    cy.on("tap", (e) => {
      if (e.target === cy) {
        setInfo(null);
        clearHl();
      }
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, []);

  // load seed when the URL param changes
  useEffect(() => {
    const seed = params.get("seed");
    if (seed && cyRef.current) {
      cyRef.current.elements().remove();
      expandedRef.current = new Set();
      expand(seed, 2, true);
    }
  }, [params]);

  async function expand(nodeId, hops, isSeed = false) {
    if (expandedRef.current.has(nodeId)) return;
    setStatus("expanding…");
    try {
      const nb = await api.neighbourhood(nodeId, hops);
      const cy = cyRef.current;
      const add = [];
      for (const n of nb.nodes) {
        if (cy.getElementById(n.id).length) continue;
        add.push({
          data: {
            id: n.id, label: n.label || shortId(n.id), kind: n.node_type,
            color: nodeTypeColor(n.node_type), size: nodeTypeSize(n.node_type),
            hub: isHubType(n.node_type) ? 1 : 0,
            synthetic: n.is_synthetic ? 1 : 0,
            sanctioned: n.props && (n.props.designated || n.node_type === "sanctions_authority") ? 1 : 0,
            props: n.props,
          },
        });
      }
      for (const e of nb.edges) {
        const eid = `${e.edge_type}|${e.source}|${e.target}|${e.t_start || ""}`;
        if (cy.getElementById(eid).length) continue;
        const ownership = e.edge_type === "owned-by" || e.edge_type === "operated-by";
        add.push({
          data: {
            id: eid, source: e.source, target: e.target,
            label: edgeTypeLabel(e.edge_type), edge_type: e.edge_type,
            color: edgeCategoryColor(e.edge_type), ownership: ownership ? 1 : 0,
            confidence: e.confidence, t_start: e.t_start, t_end: e.t_end,
            synthetic: e.is_synthetic ? 1 : 0,
          },
        });
      }
      cy.add(add);
      expandedRef.current.add(nodeId);
      runLayout(cy);
      if (isSeed) {
        const s = cy.getElementById(nodeId);
        s.addClass("seed");
        cy.animate({ fit: { eles: cy.elements(), padding: 60 }, duration: 400 });
      }
      setStatus(nb.truncated ? "traversal budget reached — partial neighbourhood shown" : "");
    } catch (e) {
      setStatus(`nothing to expand there`);
    }
  }

  function seed() {
    if (seedInput) setParams({ seed: seedInput });
  }

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={elRef} className="graph-canvas" />

      <div className="layerbox graph-help" style={{ width: 264 }}>
        <h4>Graph — ownership network</h4>
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
          Opens two hops: operator, parent company, and vessels sharing the owner.
          Click a vessel or company to expand; hover to isolate.
        </p>
        {status && <p className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{status}</p>}
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          {[["vessel", "vessel"], ["organization", "company"], ["sanctions_authority", "sanctions authority"],
            ["flag_state", "flag"], ["port", "port"], ["identity", "identity"], ["ais_gap", "AIS gap"]].map(([k, lbl]) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: k === "organization" ? 2 : "50%" }} />
              {lbl}
            </div>
          ))}
          <div className="legendline">
            <span className="layer-swatch" style={{ border: "2.5px solid #b0221b", borderRadius: "50%", background: "#fff" }} />
            sanctioned entity
          </div>
          <div className="legendline">
            <span className="layer-swatch" style={{ border: "2px dashed #6039c4", borderRadius: "50%", background: "#fff" }} />
            scenario data
          </div>
        </div>
      </div>

      {info && (
        <div className="card" style={{ position: "absolute", top: 12, right: 12, width: 300, padding: 14, zIndex: 5 }}>
          {info.type === "node" ? (
            <>
              <div className="eyebrow">{info.data.kind.replace(/_/g, " ")}</div>
              <h3 style={{ fontSize: 15, marginTop: 4 }}>{info.data.label}</h3>
              <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                {info.data.sanctioned === 1 && <span className="badge badge-finding">SANCTIONED</span>}
                {info.data.synthetic === 1 && <span className="badge badge-scenario">SCENARIO</span>}
              </div>
              <dl className="kv" style={{ marginTop: 10 }}>
                {Object.entries(info.data.props || {}).filter(([, v]) => v !== null && v !== "").slice(0, 8).map(([k, val]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k.replace(/_/g, " ")}</dt>
                    <dd className="mono" style={{ fontSize: 12 }}>{String(val)}</dd>
                  </div>
                ))}
              </dl>
              {(info.data.kind === "vessel" || info.data.kind === "organization") && (
                <p className="muted" style={{ fontSize: 11.5, marginTop: 8, marginBottom: 0 }}>
                  Click the node to expand its links.
                </p>
              )}
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

      {expandedRef.current.size === 0 && (
        <div className="empty" style={{ position: "absolute", top: "45%", left: 0, right: 0 }}>
          Pick a vessel and seed the graph to begin.
        </div>
      )}
    </div>
  );
}

function runLayout(cy) {
  cy.layout({
    name: "cose",
    animate: true,
    animationDuration: 450,
    padding: 50,
    nodeRepulsion: 14000,
    idealEdgeLength: 95,
    edgeElasticity: 120,
    gravity: 0.6,
    componentSpacing: 120,
    nodeOverlap: 12,
    numIter: 1200,
    randomize: false,
  }).run();
}
