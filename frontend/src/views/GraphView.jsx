// Graph — an ownership-network view, seed-and-expand, never a hairball. Seeding a
// vessel opens its 2-hop neighbourhood: its operator, the parent company that
// operator rolls up to, and the sibling vessels that share that owner.
//
// Presentation rules, held deliberately:
//   * The layout runs ONCE per expansion, un-animated, then fits. A physics
//     simulation that keeps animating reads as a glitch, not as motion.
//   * Labels are held at a constant SCREEN size by dividing the model font size
//     by the zoom level, so type does not balloon as you zoom in. One size, one
//     weight, one colour — the hierarchy comes from node size, not type size.
//   * Colour follows semantic FAMILIES, not one hue per type: investigated
//     entities (vessel, company, person) share a blue-to-navy family, context
//     (flag, port, identity) recedes to warm grey, and red means risk and only
//     risk. The only ring on a node means "sanctioned".
//   * Node and edge labels are on by DEFAULT. Hovering fades everything outside
//     the hovered neighbourhood; it never reveals text that was hidden.
//   * Zoom is bounded so the graph can never be lost off-scale, with explicit
//     zoom and fit controls rather than wheel-only.
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import { api } from "../api.js";
import {
  edgeCategoryColor, edgeTypeLabel, fmtDate, humanKey, nodeTypeColor,
  nodeTypeSize, num, shortId,
} from "../lib/format.js";

//: Label size in CSS pixels, held constant across zoom levels.
const LABEL_PX = 11;
// Zoom: cytoscape's default wheelSensitivity is 1, and the earlier 0.45 made
// every wheel notch and trackpad pinch feel like wading. Back to full speed,
// with a wider range so one gesture covers useful ground — the fit control is
// what recovers the view, so a bounded-but-generous range is safe.
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;

export function GraphView() {
  const elRef = useRef(null);
  const cyRef = useRef(null);
  const [params, setParams] = useSearchParams();
  const [seedInput, setSeedInput] = useState(params.get("seed") || "");
  const [info, setInfo] = useState(null);
  const expandedRef = useRef(new Set());
  const [nodeCount, setNodeCount] = useState(0);
  const [vessels, setVessels] = useState([]);
  const [status, setStatus] = useState("");
  // The vessel the view chose for itself, so the panel can say so rather than
  // letting an operator believe they are looking at a considered selection.
  const [autoSeed, setAutoSeed] = useState(null);
  const [autoSeedFailed, setAutoSeedFailed] = useState(false);

  useEffect(() => {
    api.vessels({ limit: 1000 }).then((r) => setVessels(r.items));
  }, []);

  // Hold label type at a constant on-screen size. Cytoscape font sizes are in
  // model units, so without this the labels grow with the zoom and the view
  // reads as though it uses a dozen different type sizes.
  //
  // **Applied as per-element bypasses, and at most once per animation frame.**
  // The obvious implementation — `cy.style().selector(...).style(...).update()`
  // — APPENDS a rule to the stylesheet on every call and then forces a full
  // restyle and redraw of every element. A wheel gesture emits zoom events far
  // faster than a frame, so that version rebuilt the stylesheet dozens of times
  // a second and grew it without bound (measured: 16 rules -> 18 after eight
  // zoom clicks). That is what made the view judder during any interaction.
  // Bypass styles overwrite in place and never accumulate.
  const rafRef = useRef(0);
  const syncLabelScale = useCallback((cy) => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      if (cy.destroyed()) return;
      const px = LABEL_PX / cy.zoom();
      cy.batch(() => {
        cy.nodes().style("font-size", px);
        cy.edges().style("font-size", px * 0.85);
      });
    });
  }, []);

  useEffect(() => {
    const cy = cytoscape({
      container: elRef.current,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      wheelSensitivity: 1,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n) => n.data("color"),
            width: (n) => n.data("size") * 2,
            height: (n) => n.data("size") * 2,
            "border-width": 1.5,
            "border-color": "#ffffff",
            // Every node is labelled by default — the graph should be readable
            // at rest, not only under the cursor. Hovering fades everything
            // outside the hovered neighbourhood rather than revealing new text.
            label: "data(label)",
            color: "#1f2a36",
            "font-size": LABEL_PX,
            "font-family": "Inter, system-ui, sans-serif",
            "font-weight": 500,
            "text-valign": "bottom",
            "text-margin-y": 4,
            // Wrap rather than truncate: a company name is the thing an analyst
            // is reading the graph FOR, and "Leadenhall Bulk Carri…" answers
            // nothing. Two short lines beat one clipped one.
            "text-wrap": "wrap",
            "text-max-width": 128,
            "text-outline-color": "#ffffff",
            "text-outline-width": 2.5,
            "min-zoomed-font-size": 6,
          },
        },
        // No scenario marking on the canvas: the nav pill already declares that
        // the picture contains scenario data, and the detail panel carries a
        // SCENARIO badge per entity, so the traceability ADR-019 asks for is
        // kept without a second ring competing with the sanctions one.
        { selector: "node[sanctioned = 1]", style: { "border-color": "#b0221b", "border-width": 3 } },
        { selector: "node[kind = 'organization']", style: { shape: "round-rectangle" } },
        { selector: "node[kind = 'sanctions_authority']", style: { shape: "diamond" } },
        { selector: "node.seed", style: { "border-color": "#1a5fb4", "border-width": 3.5 } },
        { selector: "node:selected", style: { "border-color": "#1a5fb4", "border-width": 4 } },
        { selector: "node.faded", style: { opacity: 0.12, "text-opacity": 0 } },
        { selector: "node.hl", style: { "z-index": 20 } },
        {
          selector: "edge",
          style: {
            width: (e) => (e.data("ownership") ? 2 : 1.2),
            "line-color": (e) => e.data("color"),
            "target-arrow-color": (e) => e.data("color"),
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.7,
            "curve-style": "bezier",
            opacity: 0.65,
            // Edge relationships are labelled at rest too, so the ownership
            // chain can be read without hovering every link.
            label: "data(label)",
            "font-size": LABEL_PX * 0.85,
            "font-family": "Inter, system-ui, sans-serif",
            color: "#5a6b7b",
            "text-rotation": "autorotate",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.92,
            "text-background-padding": 2,
          },
        },
        { selector: "edge.faded", style: { opacity: 0.05, "text-opacity": 0 } },
        { selector: "edge.hl", style: { width: 2.4, opacity: 1, "z-index": 19 } },
      ],
    });

    const clearHl = () => cy.elements().removeClass("faded hl");
    cy.on("mouseover", "node", (e) => {
      cy.elements().addClass("faded");
      e.target.closedNeighborhood().removeClass("faded").addClass("hl");
    });
    cy.on("mouseout", "node", clearHl);
    cy.on("tap", "node", (e) => {
      const d = e.target.data();
      setInfo({ type: "node", data: d });
      if (d.kind === "vessel" || d.kind === "organization") expand(d.id, 1);
    });
    cy.on("tap", "edge", (e) => setInfo({ type: "edge", data: e.target.data() }));
    cy.on("tap", (e) => {
      if (e.target === cy) { setInfo(null); clearHl(); }
    });
    cy.on("zoom", () => syncLabelScale(cy));

    cyRef.current = cy;
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      cy.destroy();
    };
  }, [syncLabelScale]);

  // ---- seed the view ----
  // Arriving at /graph with no ?seed= used to leave an empty canvas and a
  // dropdown of ~9,000 vessels. That reads as "the graph is broken", and
  // picking at random mostly confirms it: GFW registry ownership covers about
  // 1.3% of hulls in this AOI, so most choices produce one circle and nothing
  // else. Opening on the most-connected vessel shows the part of the graph
  // that has structure. It changes no stored fact — a vessel missing from the
  // seed list is less CONNECTED, not less suspicious, and the panel says so.
  useEffect(() => {
    let live = true;
    const seed = params.get("seed");
    if (seed) {
      if (!cyRef.current) return;
      cyRef.current.elements().remove();
      expandedRef.current = new Set();
      setNodeCount(0);
      setAutoSeed(null);          // an explicit pick is not an auto-seed
      setAutoSeedFailed(false);
      expand(seed, 2, true);
      return;
    }
    setStatus("finding a vessel with a network…");
    api.graphSeeds(12).then((r) => {
      if (!live || !cyRef.current) return;
      const best = (r.items || [])[0];
      if (!best) {
        setStatus("");
        setAutoSeedFailed(true);
        return;
      }
      setSeedInput(best.id);
      setAutoSeed(best);
      expand(best.id, 2, true);
    }).catch(() => {
      if (live) { setStatus(""); setAutoSeedFailed(true); }
    });
    return () => { live = false; };
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
        const designated = !!(n.props && n.props.designated) ||
          n.node_type === "sanctions_authority";
        add.push({
          data: {
            id: n.id, label: n.label || shortId(n.id), kind: n.node_type,
            color: nodeTypeColor(n.node_type), size: nodeTypeSize(n.node_type),
            synthetic: n.is_synthetic ? 1 : 0,
            sanctioned: designated ? 1 : 0,
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
      if (isSeed) cy.getElementById(nodeId).addClass("seed");
      fitView(cy);
      syncLabelScale(cy);
      setNodeCount(cy.nodes().length);
      setStatus(nb.truncated ? "traversal budget reached — partial neighbourhood shown" : "");
    } catch {
      setStatus("nothing further to expand there");
    }
  }

  const zoomBy = (factor) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: clamp(cy.zoom() * factor), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={elRef} className="graph-canvas" />

      <div className="graph-zoom">
        <button className="iconbtn" title="Zoom in" onClick={() => zoomBy(1.35)}>+</button>
        <button className="iconbtn" title="Zoom out" onClick={() => zoomBy(1 / 1.35)}>−</button>
        <button className="iconbtn" title="Fit to view" onClick={() => cyRef.current && fitView(cyRef.current)}>⤢</button>
      </div>

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
            </option>
          ))}
        </select>
        <button className="btn btn-sm btn-primary" style={{ marginTop: 8, width: "100%" }}
                onClick={() => seedInput && setParams({ seed: seedInput })}>
          Seed graph
        </button>
        <p className="muted" style={{ fontSize: 11.5, marginTop: 8, marginBottom: 0 }}>
          Opens two hops: operator, parent company, and vessels sharing the owner.
          Click a vessel or company to expand; hover to isolate.
        </p>
        {/* Say that the view chose this hull, and on what basis. An operator
            who assumes a considered selection would read "most edges" as
            "most interesting", and those are not the same claim. */}
        {autoSeed && (
          <p className="muted" style={{ fontSize: 11.5, marginTop: 6, marginBottom: 0 }}>
            Opened on <b>{autoSeed.label}</b> — the most connected vessel in the
            graph ({autoSeed.degree} edge{autoSeed.degree === 1 ? "" : "s"}),
            chosen automatically so the view opens on something. It is the
            best-connected hull, not the most suspicious one.
          </p>
        )}
        {status && <p className="muted" style={{ fontSize: 11.5, marginTop: 6 }}>{status}</p>}
        {/* Legend grouped by family, so the colour system explains itself. */}
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          <div className="legend-group">Entities</div>
          {[["vessel", "vessel"], ["organization", "company"]].map(([k, lbl]) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: k === "organization" ? 2 : "50%" }} />
              {lbl}
            </div>
          ))}
          <div className="legend-group">Context</div>
          {[["flag_state", "flag"], ["port", "port"], ["identity", "identity"]].map(([k, lbl]) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: "50%" }} />
              {lbl}
            </div>
          ))}
          <div className="legend-group">Risk</div>
          {[["sanctions_authority", "sanctions authority"], ["ais_gap", "AIS gap"]].map(([k, lbl]) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: k === "sanctions_authority" ? 2 : "50%" }} />
              {lbl}
            </div>
          ))}
          <div className="legendline">
            <span className="layer-swatch" style={{ border: "2.5px solid #b0221b", borderRadius: "50%", background: "#fff" }} />
            sanctioned entity
          </div>
        </div>
      </div>

      {info && <DetailCard info={info} onClose={() => setInfo(null)} />}

      {/* An empty canvas needs to say WHICH kind of empty it is. "No edges in
          the graph at all" and "you have not picked a vessel yet" look
          identical on screen and mean completely different things — the first
          is a fact about the corpus (GFW ownership is ~1.3% populated here),
          the second is a prompt. */}
      {nodeCount === 0 && !status && (
        <div className="empty" style={{ position: "absolute", top: "45%", left: 0, right: 0 }}>
          {autoSeedFailed
            ? "No ownership edges in the graph yet. Run "
              + "tools/run_scenario_pipeline.py to populate it, or pick a "
              + "vessel to check one hull directly."
            : "Pick a vessel and seed the graph to begin."}
        </div>
      )}
    </div>
  );
}

function DetailCard({ info, onClose }) {
  const isNode = info.type === "node";
  const d = info.data;
  const rows = isNode
    ? Object.entries(d.props || {}).filter(([, v]) => v !== null && v !== "" && v !== undefined)
    : [["confidence", num(d.confidence, 3)],
       ["from", fmtDate(d.t_start) || "—"],
       ["to", d.t_end ? fmtDate(d.t_end) : "current"]];
  return (
    <div className="graph-detail card">
      <div className="graph-detail-head">
        <div>
          <div className="eyebrow">{isNode ? d.kind.replace(/_/g, " ") : "relationship"}</div>
          <h3>{isNode ? d.label : edgeTypeLabel(d.edge_type)}</h3>
        </div>
        <button className="iconbtn" onClick={onClose}>×</button>
      </div>
      <div className="graph-detail-badges">
        {d.sanctioned === 1 && isNode && d.kind !== "sanctions_authority" &&
          <span className="badge badge-finding">SANCTIONED</span>}
      </div>
      <dl className="kv kv-detail">
        {rows.slice(0, 10).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{humanKey(k)}</dt>
            <dd>{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
          </div>
        ))}
      </dl>
      {isNode && (d.kind === "vessel" || d.kind === "organization") && (
        <p className="muted" style={{ fontSize: 11.5, margin: "8px 0 0" }}>
          Click the node to expand its links.
        </p>
      )}
    </div>
  );
}

const clamp = (z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

//: The controls panel and the detail card float over the canvas, so a plain fit
//: centres the graph underneath them. Reserve that gutter.
const PANEL_L = 290;
const PANEL_R = 60;

function fitView(cy) {
  if (cy.elements().length === 0) return;
  cy.fit(cy.elements(), 60);
  // Never leave the view at an unusable scale — a single stray gesture used to
  // be able to leave the whole graph a few pixels across.
  if (cy.zoom() < MIN_ZOOM) {
    cy.zoom({ level: MIN_ZOOM, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  }
  cy.panBy({ x: (PANEL_L - PANEL_R) / 2, y: 0 });
}

function runLayout(cy) {
  // Un-animated on purpose: an animating force simulation keeps nudging nodes
  // for seconds after a click and reads as a glitch. This settles instantly and
  // deterministically (randomize: false), so the same graph always looks the same.
  cy.layout({
    name: "cose",
    animate: false,
    padding: 50,
    // Lay out around the LABELS, not the dots. Every node is labelled at rest,
    // so a layout that only avoids circle overlap still stacks text on text.
    nodeDimensionsIncludeLabels: true,
    nodeRepulsion: 26000,
    idealEdgeLength: 150,
    edgeElasticity: 100,
    gravity: 0.35,
    componentSpacing: 170,
    nodeOverlap: 28,
    numIter: 1500,
    randomize: false,
    fit: false,
  }).run();
}
