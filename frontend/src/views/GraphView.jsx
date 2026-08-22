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
//     the hovered neighbourhood. It reveals hidden text in exactly one case —
//     the single edge under the cursor — because on the whole-network view no
//     edge carries a label at rest, and a hover that neither highlights nor
//     names the link answers nothing. Nodes still never reveal.
//   * Zoom is bounded so the graph can never be lost off-scale, with explicit
//     zoom and fit controls rather than wheel-only.
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { api } from "../api.js";

// Cytoscape's built-in `cose` compares every pair of nodes on every iteration,
// so its cost grows with the square of the node count and it blocks the main
// thread while it does. Measured on the whole-web view: **115 seconds** to lay
// out 1,409 nodes, with the iteration budget already cut to 250. That is not a
// slow render, it is a hung tab.
//
// `fcose` seeds from a spectral (eigendecomposition) draft and then relaxes it
// with quadtree-approximated repulsion, which is O(n log n) per iteration
// rather than O(n^2). Same force-directed look, same deterministic settling
// when randomize is off — see the measurement in the commit message.
cytoscape.use(fcose);
import {
  edgeCategoryColor, edgeLabel, fmtDate, humanKey, nodeTypeColor,
  nodeTypeSize, num, shortId,
} from "../lib/format.js";

//: Label size in CSS pixels, held constant across zoom levels.
const LABEL_PX = 11;
// Zoom: cytoscape's default wheelSensitivity is 1, and the earlier 0.45 made
// every wheel notch and trackpad pinch feel like wading. Back to full speed,
// with a wider range so one gesture covers useful ground — the fit control is
// what recovers the view, so a bounded-but-generous range is safe.
// 0.25 was the floor when nodes and type scaled with the canvas: below it the
// graph really did become a few unreadable pixels across. That failure mode no
// longer exists — symbols and labels hold their size on screen, so zooming out
// spreads the picture thinner without ever shrinking what you read.
//
// The floor had become the thing PREVENTING a readable view: the ownership
// network settles at a fit zoom of about 0.14, so clamping at 0.25 left it
// overflowing the viewport by ~300px on the left and ~245px on the right with
// no way to pull back. Measured, not guessed — and the label budget barely
// moves it, so this is the lever that matters.
const MIN_ZOOM = 0.08;
const MAX_ZOOM = 4;
//: How many labelled nodes a layout will measure boxes for. Label-aware
//: spacing is what stops two company names sharing a spot; measuring 1,400 of
//: them is what made it too slow to keep on. This is the count of nodes that
//: actually draw text, not the node count.
const LABEL_AWARE_MAX_LABELS = 300;

//: Floors for the draw-time clamp. A mark that scales all the way down with
//: the camera eventually vanishes, and a graph of invisible dots is not a
//: graph; these are the sizes below which a node or a line stops being a mark
//: at all. They are a FLOOR on the rendered size, never an input to a layout.
const MIN_NODE_PX = 3;
const MIN_EDGE_PX = 0.6;

//: Above this many elements the view stops doing the whole-graph interactions.
//:
//: Hover-fade adds a class to EVERY element and then restyles and redraws all
//: of them. Measured in Chromium on the 1,500-node whole-network view: 480 ms
//: to fade and 410 ms to clear, per hover — so simply moving the cursor across
//: the canvas queued near-second-long main-thread blocks back to back, which
//: is most of what made the tab feel dead after the network loaded. Below the
//: threshold the same work costs a few milliseconds and the interaction is
//: worth having.
//: The default view is now far under this — the ownership network is ~160
//: elements where the old everything-view was ~4,300 — so hover-isolation is
//: back on where it matters. The ceiling stays for the case where an operator
//: switches every context layer on, and the panel SAYS when it has engaged: an
//: interaction that silently stops existing reads as a broken feature, which is
//: exactly how it was reported.
const INTERACTIVE_MAX_ELEMENTS = 600;

//: The context families an operator can switch back on, with the node kind
//: that carries their colour and the reason each is off by default. The counts
//: are from the fixture graph and are the argument, not decoration.
const CONTEXT_LAYERS = [
  ["flag", "flag_state", "flag",
   "228 edges. One node, flag:IND, joins 156 vessels — a single star that "
   + "dominates the layout."],
  ["port", "port", "port",
   "313 edges. Ports are hubs: nine anchorages carry most of them, and every "
   + "hull touches several."],
  ["identity", "identity", "identity",
   "629 edges to 603 leaf nodes — 67% of the graph is an identity record on a "
   + "stick, and a name change is read on the vessel, not in the web."],
];

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
  // Set while the canvas is doing something that blocks the main thread, so
  // the view can say so instead of appearing to have died.
  const [busy, setBusy] = useState("");
  // Which load is the current one. Every load takes a ticket and checks it
  // before touching the canvas, so a load abandoned mid-flight — by navigating
  // away, by seeding a vessel, by React re-running the effect — cannot write
  // its results over the one that replaced it.
  //
  // NOT a boolean "busy" flag that makes a second call return early: React
  // mounts an effect, tears it down and mounts it again in development, so a
  // flag like that has the first call cancel itself and the second call refuse
  // to start. The view then sits on "loading the whole network…" forever,
  // which is the failure this guard was added to prevent.
  const runRef = useRef(0);
  // The vessel the view chose for itself, so the panel can say so rather than
  // letting an operator believe they are looking at a considered selection.
  const [autoSeed, setAutoSeed] = useState(null);
  const [autoSeedFailed, setAutoSeedFailed] = useState(false);
  // Why the whole-network view is not on screen, when the reason is a failure
  // rather than a choice. "Nothing loaded" and "you have not picked anything"
  // look identical on an empty canvas and mean opposite things.
  const [loadError, setLoadError] = useState(null);
  // The whole-web payload, kept so the panel can state what is on screen and
  // — when the graph is larger than the cap — what is not.
  const [web, setWeb] = useState(null);
  // Which context families are switched on. Held in the URL so a view an
  // operator has set up is a link they can send, and so a reload does not
  // silently drop back to the default answer to a different question.
  const context = (params.get("context") || "").split(",").filter(Boolean);

  // Writing the change into the URL is the whole implementation: the loader
  // effect already depends on `params`, so the refetch, the relayout and the
  // panel copy all follow from one `setParams`. No second code path, and the
  // back button undoes a layer the way it undoes anything else.
  function toggleContext(key) {
    const next = context.includes(key)
      ? context.filter((c) => c !== key)
      : [...context, key];
    const p = new URLSearchParams(params);
    if (next.length) p.set("context", next.join(","));
    else p.delete("context");
    // Turning a layer on is a question about the whole network, so it also
    // leaves a seeded neighbourhood — otherwise the checkbox would appear to
    // do nothing while a single hull is on screen.
    p.delete("seed");
    setParams(p);
  }

  // "Back to the whole network", and the retry it becomes when the load failed.
  //
  // Clearing `?seed=` is what normally triggers the reload, because the loader
  // effect depends on `params`. But when the URL is ALREADY parameterless —
  // which is exactly the state you are left in after a failed load — writing
  // the same empty search back changes nothing, the effect never re-runs, and
  // the button does nothing at all however many times it is pressed. That is
  // the "Back to the whole network isn't working" that was reported: not a
  // wiring mistake, a control whose only mechanism was a change that had
  // nothing to change.
  function backToWholeNetwork() {
    if ([...params.keys()].length === 0) {
      loadWholeWeb();
      return;
    }
    setParams({});
  }

  useEffect(() => {
    api.vessels({ limit: 1000 }).then((r) => setVessels(r.items)).catch(() => {});
  }, []);

  // **The canvas zooms; the symbols do not.**
  //
  // Cytoscape sizes both type and nodes in model units, so zooming scales them
  // with the picture. Labels were already pinned to the screen — which left
  // the two halves of the visual system disagreeing: zoom in and the dots
  // inflate while the text stays put, until a vessel is a saucer with a small
  // word under it. That is the "icons become too big" an operator reported.
  //
  // Pinning both means a zoom gesture changes only the SPACING. That is the
  // right behaviour for a dense network — you zoom to pull a cluster apart and
  // read it, not to magnify one circle — and it is what a map does with its
  // labels. Node size is then free to mean one thing only: importance.
  //
  // Bucketed by diameter, because there are about six distinct sizes in
  // `nodeTypeSize` and a per-element loop is what makes this expensive. Six
  // collection-level style calls hold whether the graph has 161 nodes or 1,500.
  //
  // Two things carried over from the label version, both measured and both
  // still load-bearing:
  //
  //   * **Per-element bypasses, not stylesheet rules.** The obvious
  //     `cy.style().selector(...).style(...).update()` APPENDS a rule on every
  //     call and forces a full restyle of every element. A wheel gesture emits
  //     zoom events faster than a frame, so that version grew the stylesheet
  //     without bound (16 rules -> 18 after eight zoom clicks) and made the
  //     view judder. Bypasses overwrite in place.
  //   * **At most once per animation frame**, and labels only on the elements
  //     that draw one — applying the label bypass to all 1,500 nodes and 1,900
  //     edges cost ~175 ms per zoom event when at most a hundred carry text.
  const rafRef = useRef(0);
  const bucketRef = useRef(null);
  //: Nodes worth naming in the whole-network view, best first. Held so the
  //: zoom handler can redo the de-collision pass without rebuilding it.
  const labelCandidatesRef = useRef(null);
  const sizeBuckets = useCallback((cy) => {
    if (bucketRef.current && bucketRef.current.n === cy.nodes().length) {
      return bucketRef.current.buckets;
    }
    const by = new Map();
    cy.nodes().forEach((n) => {
      const d = n.data("diameter");
      if (!by.has(d)) by.set(d, []);
      by.get(d).push(n);
    });
    const buckets = [...by.entries()].map(([d, list]) => [d, cy.collection(list)]);
    bucketRef.current = { n: cy.nodes().length, buckets };
    return buckets;
  }, []);

  // Two widths in the whole graph — ownership and everything else — so this is
  // two collection-level style calls per frame however large the web gets.
  const edgeBucketRef = useRef(null);
  const edgeWidthBuckets = useCallback((cy) => {
    if (edgeBucketRef.current && edgeBucketRef.current.n === cy.edges().length) {
      return edgeBucketRef.current.buckets;
    }
    const by = new Map();
    cy.edges().forEach((e) => {
      const w = e.data("width");
      if (!by.has(w)) by.set(w, []);
      by.get(w).push(e);
    });
    const buckets = [...by.entries()].map(([w, list]) => [w, cy.collection(list)]);
    edgeBucketRef.current = { n: cy.edges().length, buckets };
    return buckets;
  }, []);

  // **Draw-time scaling. It never runs before a layout, and it never feeds
  // back into one.**
  //
  // The previous version divided node diameters by the zoom and applied that
  // BEFORE the layout, so the layout's idea of how big a node is came from
  // where the camera happened to be. That is circular, and on a large graph
  // the loop ran away instead of converging. Measured on a 1,499-node
  // ownership forest (276 components):
  //
  //     model bounding box 559 x 287 units for 1,499 nodes
  //     fit zoom 2.65 — the fit ZOOMED IN, because the graph had collapsed
  //     every node rendered 7-8px, all of them stacked in a 614 x 316px blob
  //
  // Each round made it worse: a tighter layout raised the fit zoom, a higher
  // zoom shrank the model sizes handed to the next layout, which packed
  // tighter still. That is the diagonal smear of overlapping nodes and
  // unreadable labels that got reported, and no amount of tuning repulsion
  // fixes a feedback loop.
  //
  // So the two concerns are separated for good:
  //
  //   * **Layout space is zoom-independent.** `applyLayoutSizes` gives every
  //     node its nominal density-scaled diameter and the model font size, and
  //     the layout runs against those. Same graph, same picture, whatever the
  //     camera is doing.
  //   * **Render space is clamped, not pinned.** Node size is
  //     `clamp(nominal x zoom, floor, nominal)` — capped at nominal so zooming
  //     in never inflates a vessel into a saucer with a small word under it,
  //     floored so zooming out never shrinks it to nothing. A clamp of a
  //     monotone function cannot oscillate, and it is applied only after a
  //     layout has finished.
  //
  // Labels stay pinned to a constant screen size, which is what makes a
  // zoomed-out graph readable. They cannot feed back either, because the
  // layout measures them at the model font size, not the pinned one.
  const applyScreenScale = useCallback((cy) => {
    if (cy.destroyed()) return;
    const z = cy.zoom();
    const density = densityScale(cy.nodes().length);
    const labelled = cy.nodes(".labelled");
    const labelledEdges = cy.edges(".labelled");
    cy.batch(() => {
      if (!labelled.empty()) labelled.style("font-size", LABEL_PX / z);
      if (!labelledEdges.empty()) labelledEdges.style("font-size", (LABEL_PX / z) * 0.85);
      for (const [d, coll] of sizeBuckets(cy)) {
        // `d` is the nominal diameter in screen pixels. Model units are what
        // cytoscape wants, so divide the pixels we actually want by the zoom.
        const px = Math.min(d, Math.max(MIN_NODE_PX, d * z));
        coll.style({ width: px / z, height: px / z });
      }
      // Lines get the same treatment, so the whole visual system scales
      // together. Left in model units they were the one thing that still
      // shrank — a 2px ownership line at zoom 0.4 draws at 0.8px — and the
      // relationships are the entire point of a graph. The density factor is
      // in here too: without it a dense view shrank its dots to 41% and left
      // the arrowheads (which cytoscape sizes from the line width) at full
      // size, which is how they came to dwarf the nodes they point at.
      for (const [w, coll] of edgeWidthBuckets(cy)) {
        const nominal = w * density;
        const px = Math.min(nominal, Math.max(MIN_EDGE_PX, nominal * z));
        coll.style("width", px / z);
      }
    });
  }, [sizeBuckets, edgeWidthBuckets]);

  const syncScreenScale = useCallback((cy) => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      applyScreenScale(cy);
    });
  }, [applyScreenScale]);

  // Redo the label de-collision once the zoom has SETTLED. Debounced hard,
  // because it must not run per wheel notch: the pass itself is sub-millisecond
  // but it rewrites label data, and doing that mid-gesture makes names flicker
  // in and out while the view is still moving. On the trailing edge it reads as
  // a map filling in detail as you zoom.
  const labelTimerRef = useRef(0);
  const scheduleLabelPass = useCallback((cy) => {
    if (!labelCandidatesRef.current) return;
    clearTimeout(labelTimerRef.current);
    labelTimerRef.current = setTimeout(() => {
      if (cy.destroyed() || !labelCandidatesRef.current) return;
      applyLabelDecollision(cy, labelCandidatesRef.current);
      applyScreenScale(cy);
    }, 180);
  }, [applyScreenScale]);

  // Lay out, frame it, then scale for drawing. One pass each, in that order,
  // and never the other way round — the old two-pass fit/rescale fixed point
  // existed only to reconcile the circularity described above.
  const settleLayout = useCallback((cy, nodeCount, frame, opts) => {
    runLayout(cy, nodeCount, opts);
    frame();
    applyScreenScale(cy);
  }, [applyScreenScale]);

  useEffect(() => {
    const cy = cytoscape({
      container: elRef.current,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      wheelSensitivity: 1,
      // Render the canvas from a cached texture while a pan or zoom gesture is
      // in flight, and drop the edges for its duration. Both only affect what
      // is drawn DURING a gesture — the picture is redrawn at full fidelity
      // the moment it settles — and on the whole-network view they are the
      // difference between a gesture that tracks the cursor and one that
      // arrives a second later.
      textureOnViewport: true,
      hideEdgesOnViewport: true,
      motionBlur: false,
      // **Dragging always pans; nodes are never dragged.**
      //
      // Cytoscape makes nodes grabbable by default, and a drag that starts on
      // a node moves that node instead of the view. On a dense canvas nearly
      // every drag starts on a node, so panning simply appeared not to work —
      // reported as "the moving around feature where the cursor becomes a hand
      // does not work". It was working; there was just almost nowhere left to
      // grab.
      //
      // Nothing is lost by turning it off. Hand-placing a node in a
      // force-directed layout is undone by the next expansion anyway, and the
      // positions are meant to be deterministic (`randomize: false`) so the
      // same graph is recognisable tomorrow. A drag that quietly destroys that
      // is worse than no drag.
      autoungrabify: true,
      // Box-select on drag would re-introduce the same problem from the other
      // side: a drag on empty canvas would draw a selection rectangle rather
      // than pan. Selection here is by click, which is all the detail panel
      // needs.
      boxSelectionEnabled: false,
      style: [
        {
          selector: "node",
          style: {
            // `data(...)` mappers, not functions. A function-valued style is
            // re-invoked for every element on every restyle; a data mapper is
            // read straight off the element. With thousands of elements and a
            // restyle on every class change, that difference is measurable.
            "background-color": "data(color)",
            width: "data(diameter)",
            height: "data(diameter)",
            "border-width": 1.5,
            "border-color": "#ffffff",
            // Labelled when the view says so. The neighbourhood view labels
            // everything (it holds tens of nodes); the whole-web view holds up
            // to 1,500 and labelling all of them is an unreadable mat of text,
            // so it marks only the nodes worth naming at rest — see
            // `webLabelCandidates` + `applyLabelDecollision`. The label is
            // precomputed onto the element as
            // `shownLabel` rather than decided in a style function.
            label: "data(shownLabel)",
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
            width: "data(width)",
            "line-color": "data(color)",
            "target-arrow-color": "data(color)",
            "target-arrow-shape": "triangle",
            // Cytoscape derives the arrowhead's size from the EDGE WIDTH, and
            // edge width is pinned to the screen (`width / zoom`) while node
            // diameters are additionally shrunk by `densityScale`. On the
            // 1,500-node view that left the dots at 41% and the arrowheads at
            // 100% — pinheads under enormous triangles. `applyScreenScale` now
            // puts edges through the same density factor, and this drops the
            // head to half the line's width so a direction reads as a hint
            // rather than as the loudest mark on the canvas.
            "arrow-scale": 0.6,
            "curve-style": "bezier",
            opacity: 0.65,
            // Edge relationships are labelled at rest in the neighbourhood
            // view, so the ownership chain can be read without hovering every
            // link. The whole-network view sets `shownLabel` to "" — two
            // thousand rotated labels with backgrounds is a texture, not a
            // reading.
            label: "data(shownLabel)",
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
        // An ENDED relationship, drawn as such. It is still shown — it is a
        // real assertion with a real time scope, and hiding it would make the
        // web disagree with the dashboard's edge count — but drawing it
        // identically to a live edge would assert a stale fact as current,
        // which invariant 3 exists to prevent. Dashed and dimmed reads as
        // "was true", which is what it is.
        { selector: "edge[current = 0]", style: {
            "line-style": "dashed", opacity: 0.28, "line-dash-pattern": [5, 4] } },
        // The node the web opened on, and its immediate network.
        { selector: "node.focus", style: {
            "border-color": "#1a5fb4", "border-width": 4, "z-index": 30 } },
        { selector: ".focus-nbr", style: { "z-index": 25 } },
        { selector: "edge.focus-nbr", style: { opacity: 0.9, width: 2.2 } },
        // The hovered relationship. **Last in the stylesheet on purpose** —
        // cytoscape resolves conflicts by source order, and `edge[current = 0]`
        // above pins ended edges to opacity 0.28. Declared any earlier, hovering
        // a closed relationship would raise everything about it except its
        // visibility. Here it keeps the dash (never overridden, so "was true"
        // still reads as past) and wins on opacity and width.
        //
        // It also REVEALS its own label, which no other hover in this view
        // does. The rule elsewhere is that hovering never uncovers hidden text,
        // and that rule is right for the neighbourhood fade — it would re-run
        // label de-collision mid-gesture. One edge is different: the
        // whole-network view sets `shownLabel` to "" on all ~1,900 of them, so
        // an operator pointing at a link to ask what it is got no highlight and
        // no answer. Revealing exactly one element's `label` touches no layout.
        { selector: "edge.hovered", style: {
            width: 3.2, opacity: 1, "z-index": 22,
            label: "data(label)", "text-opacity": 1,
            color: "#1b2a38",
            "text-background-opacity": 1,
            "text-background-padding": 3,
          } },
      ],
    });

    const clearHl = () => cy.elements().removeClass("faded hl hovered");
    // Hover-fade only where it is affordable — see INTERACTIVE_MAX_ELEMENTS.
    // On a large web the neighbourhood is still raised, it is just not raised
    // by pushing everything else down.
    cy.on("mouseover", "node", (e) => {
      if (cy.elements().length > INTERACTIVE_MAX_ELEMENTS) {
        e.target.closedNeighborhood().addClass("hl");
        return;
      }
      cy.elements().addClass("faded");
      e.target.closedNeighborhood().removeClass("faded").addClass("hl");
    });
    cy.on("mouseout", "node", clearHl);

    // **Edge hover, which did not exist.** `mouseover` was bound to `node`
    // only, so pointing at a relationship produced nothing — no highlight, no
    // label, no cursor change — and the sole way to find out what a link was
    // was to click it and read the side panel. Same affordability test as the
    // node path: fade the rest where the element count allows, raise the edge
    // and both of its endpoints either way.
    cy.on("mouseover", "edge", (e) => {
      const edge = e.target;
      // Match the inline size `syncScreenScale` gives labelled edges, so a
      // revealed label sits at the same scale as the ones already on screen
      // instead of at whatever the stylesheet constant is at this zoom.
      edge.style("font-size", (LABEL_PX / (cy.zoom() || 1)) * 0.85);
      if (cy.elements().length > INTERACTIVE_MAX_ELEMENTS) {
        edge.addClass("hovered");
        edge.connectedNodes().addClass("hl");
        return;
      }
      cy.elements().addClass("faded");
      edge.connectedNodes().removeClass("faded").addClass("hl");
      edge.removeClass("faded").addClass("hovered");
    });
    cy.on("mouseout", "edge", clearHl);
    cy.on("tap", "node", (e) => {
      const d = e.target.data();
      setInfo({ type: "node", data: d });
      if (d.kind === "vessel" || d.kind === "organization") expand(d.id, 1);
    });
    cy.on("tap", "edge", (e) => setInfo({ type: "edge", data: e.target.data() }));
    cy.on("tap", (e) => {
      if (e.target === cy) { setInfo(null); clearHl(); }
    });
    cy.on("zoom", () => { syncScreenScale(cy); scheduleLabelPass(cy); });

    cyRef.current = cy;
    // The live cytoscape core, exposed for diagnosis. Not used by the app.
    // Every number in the layout notes above — bounding boxes, distinct node
    // positions, overlapping label pairs, nodes actually inside the viewport —
    // was measured through this handle in a real browser, and the collapse it
    // found was invisible from the outside. Leaving it costs nothing and makes
    // the next report answerable with measurements instead of guesses.
    if (typeof window !== "undefined") window.__cy = cy;
    // A read-only handle for verification. Every claim about this view — "the
    // labels stopped colliding", "zoom no longer inflates the nodes" — is a
    // claim about geometry, and without a handle the only way to check it is
    // to look at a screenshot and decide it seems better. That is how the
    // label-collision bug survived: it was inspected, not measured.
    //
    // Safe here and nowhere else: the API binds to 127.0.0.1 and this is a
    // single-operator laptop surface (ADR-013). It exposes no data the page is
    // not already drawing.
    if (typeof window !== "undefined") window.__cy = cy;
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
      clearTimeout(labelTimerRef.current);
      cy.destroy();
    };
  }, [syncScreenScale, scheduleLabelPass]);

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
      if (!cyRef.current) return undefined;
      cyRef.current.elements().remove();
      expandedRef.current = new Set();
      setNodeCount(0);
      setAutoSeed(null);          // an explicit pick is not an auto-seed
      setAutoSeedFailed(false);
      setWeb(null);               // no longer showing the whole web
      expand(seed, 2, true);
      // The cleanup runs on both branches now. Without it the seed branch left
      // `live` true forever, so a load abandoned by navigation could still
      // write its results into an unmounted view.
      return () => { live = false; };
    }
    loadWholeWeb(() => live);
    return () => { live = false; };
  }, [params]);

  // ---- the whole web -----------------------------------------------------
  // The default view is every relationship in the graph at once, centred on
  // one node rather than opened on it. Two things make that honest rather
  // than merely impressive:
  //
  //   * the server returns the most-connected core up to a cap, and this
  //     states the cap — the real corpus graph is an estimated ~19,000 nodes
  //     and a partial picture that looks whole is worse than no picture;
  //   * the centred node is a camera position, not a verdict. The panel says
  //     on what basis it was chosen.
  async function loadWholeWeb(stillLive = () => true) {
    const myRun = ++runRef.current;
    const current = () => runRef.current === myRun && stillLive();
    setLoadError(null);
    setStatus("loading the whole network…");
    let g;
    try {
      // Session-memoised, so a superseded call costs one map lookup rather
      // than a second trip to the server.
      g = await api.graphAll(undefined, context);
    } catch {
      if (current()) { setStatus(""); setAutoSeedFailed(true); }
      return;
    }
    const cy = cyRef.current;
    if (!current() || !cy) return;
    if (!g.nodes.length) {
      setStatus("");
      setAutoSeedFailed(true);
      return;
    }

    try {
      cy.elements().remove();
      // Everything is already on screen, so nothing needs expanding — marking
      // every node expanded stops a click firing a redundant neighbourhood call.
      expandedRef.current = new Set(g.nodes.map((n) => n.id));

      const focusId = g.focus;
      const webScale = densityScale(g.nodes.length);
      cy.add([
        ...g.nodes.map((n) => nodeElement(n, { showLabel: false, scale: webScale })),
        // Edge labels off in the web view: two thousand of them is a texture,
        // and every one is a text box the renderer measures on each redraw.
        ...g.edges.map((e) => edgeElement(e, { showLabel: false })),
      ]);

      // Get this message on screen BEFORE handing the thread to the layout,
      // which blocks it for a beat.
      //
      // Neither `setTimeout(0)` nor a double rAF is sufficient: React 18 batches
      // and schedules the re-render, and both of those can run before the commit
      // does — checked in a browser, where the message was set and never
      // appeared. `flushSync` commits it synchronously; the rAF that follows
      // then waits for the paint of that commit.
      flushSync(() => {
        setStatus("");
        setBusy(`Laying out ${g.nodes.length.toLocaleString()} nodes and `
          + `${g.edges.length.toLocaleString()} relationships…`);
      });
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      if (!current() || cy.destroyed()) return;
      // The candidate ORDER is fixed here; which of them are actually drawn is
      // decided after the camera settles, because that is when a label's
      // position in pixels — the only thing that decides whether two collide —
      // is finally known.
      labelCandidatesRef.current = webLabelCandidates(cy, focusId);

      const node = focusId ? cy.getElementById(focusId) : cy.collection();
      if (node.length) {
        node.addClass("focus");
        node.closedNeighborhood().addClass("focus-nbr");
      }
      // **Open on the whole network, always.**
      //
      // It used to open FRAMED on the focus node's neighbourhood, which made
      // sense when a fit produced an unreadable speck. It does not any more —
      // node and line sizes have a floor now, so a fitted graph stays legible
      // — and framing had a cost that was never worth it: the panel says
      // "1,499 entities" over a viewport showing 123 of them. A number on the
      // panel that the canvas contradicts is how an operator concludes the
      // view is broken, and here it was measured at 8% of what it claimed.
      //
      // The focus node keeps its ring, so it is still findable; it is a
      // labelled starting point rather than a crop.
      settleLayout(cy, g.nodes.length, () => fitView(cy), { isWeb: true });
      applyLabelDecollision(cy, labelCandidatesRef.current);
      applyScreenScale(cy);
      setNodeCount(cy.nodes().length);
      setWeb(g);
    } catch (e) {
      // **A half-built canvas is worse than an empty one.** The elements are
      // added before the layout runs, so anything that throws in between —
      // a layout bug, a malformed payload — used to leave every node sitting
      // un-positioned at the model origin while React, whose state was never
      // updated, printed "pick a vessel" over the top. What the operator saw
      // was a couple of stray dots in the top-left corner that no control
      // could clear, and no indication anything had gone wrong.
      //
      // So: clear what was half-drawn, and SAY the load failed. A view that
      // reports its own failure is debuggable; one that quietly draws two
      // dots is not.
      console.error("whole-network load failed", e);
      if (runRef.current === myRun) {
        if (cy && !cy.destroyed()) cy.elements().remove();
        setNodeCount(0);
        setWeb(null);
        setLoadError(String(e?.message || e));
      }
    } finally {
      if (runRef.current === myRun) {
        setBusy("");
        setStatus("");
      }
    }
  }

  async function expand(nodeId, hops, isSeed = false) {
    if (expandedRef.current.has(nodeId)) return;
    const myRun = ++runRef.current;
    setStatus("expanding…");
    // Whether anything reached the canvas before a throw. Everything below the
    // fetch is local work, so a failure after this point is a fault in our
    // code, not a hull with nothing attached to it — and reporting it as the
    // latter is how the same layout crash stayed invisible on this path while
    // it was leaving stray nodes on the other.
    let added = false;
    try {
      const nb = await api.neighbourhood(nodeId, hops);
      const cy = cyRef.current;
      // A superseded expansion must not add its nodes to a canvas that has
      // since been cleared for a different seed.
      if (!cy || cy.destroyed() || runRef.current !== myRun) return;
      const add = [];
      for (const n of nb.nodes) {
        if (cy.getElementById(n.id).length) continue;
        add.push(nodeElement(n, { showLabel: true }));
      }
      for (const e of nb.edges) {
        const eid = edgeId(e);
        if (cy.getElementById(eid).length) continue;
        add.push(edgeElement(e, { showLabel: true }));
      }
      cy.add(add);
      added = true;
      runLayout(cy, cy.nodes().length);
      if (isSeed) cy.getElementById(nodeId).addClass("seed");
      fitView(cy);
      syncScreenScale(cy);
      setNodeCount(cy.nodes().length);
      setStatus(nb.truncated ? "traversal budget reached — partial neighbourhood shown" : "");
    } catch (e) {
      if (runRef.current !== myRun) return;
      if (!added) {
        setStatus("nothing further to expand there");
        return;
      }
      console.error("expansion failed after adding elements", e);
      const cy = cyRef.current;
      // Same rule as the whole-network path: never leave un-positioned nodes
      // on the canvas. A seed owns the canvas, so it can be cleared outright;
      // an expansion is additive, so re-fit what is there and report the fault
      // rather than throwing away a graph the operator built up by hand.
      if (cy && !cy.destroyed()) {
        if (isSeed) {
          cy.elements().remove();
          setNodeCount(0);
        } else {
          fitView(cy);
          setNodeCount(cy.nodes().length);
        }
      }
      setStatus(`could not draw that neighbourhood — ${e?.message || e}`);
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
                disabled={!!busy}
                onClick={() => seedInput && setParams({ seed: seedInput })}>
          Seed graph
        </button>
        <p className="graph-note muted">
          Click a node to expand it; hover to isolate.
        </p>
        {/* What is on screen, stated in numbers. A web that looks complete is
            how a viewer concludes the dataset is smaller than it is, so the
            truncation is named here rather than left to be inferred. */}
        {web && (
          <div className="graph-note muted">
            <div>
              <b>{web.nodes.length.toLocaleString()}</b> entities ·{" "}
              <b>{web.edges.length.toLocaleString()}</b> ownership links
              {web.truncated && (
                <> of <b>{(web.matched_nodes ?? web.total_nodes).toLocaleString()}</b>
                  {" "}·{" "}<b>{(web.matched_edges ?? web.total_edges).toLocaleString()}</b>{" "}
                  <span title="The server returns the most-connected core up to a cap. A partial picture that looks whole is worse than no picture, so the numbers say which this is.">
                    (partial — the most-connected core)
                  </span>
                </>
              )}
            </div>
            {/* Hidden and truncated are different facts. A layer switched off
                is one checkbox away; a node past the cap is not, and reporting
                them as one number would send an operator looking for a control
                that would not help. */}
            {web.total_nodes > (web.matched_nodes ?? web.total_nodes) && (
              <div style={{ marginTop: 4 }}>
                <b>{(web.total_nodes - web.matched_nodes).toLocaleString()}</b>{" "}
                hidden — context only. Switch a layer on below.
              </div>
            )}
            {web.focus_basis && (
              <div
                style={{ marginTop: 4 }}
                title="A camera position, not a finding — the best-connected node, not the most suspicious one."
              >
                Centred on <b>{focusLabel(web)}</b> (most connected).
              </div>
            )}
            {/* An interaction that stops existing without saying so reads as a
                broken feature — which is exactly how it was reported. */}
            {web.nodes.length + web.edges.length > INTERACTIVE_MAX_ELEMENTS && (
              <div
                style={{ marginTop: 4 }}
                title={`Fading every element costs about half a second per hover above ${INTERACTIVE_MAX_ELEMENTS} of them. Switch a Context layer off to get it back.`}
              >
                Hover-isolate off above{" "}
                {INTERACTIVE_MAX_ELEMENTS.toLocaleString()} elements.
              </div>
            )}
          </div>
        )}
        {/* Say that the view chose this hull, and on what basis. An operator
            who assumes a considered selection would read "most edges" as
            "most interesting", and those are not the same claim. */}
        {autoSeed && (
          <p className="graph-note muted"
             title="Chosen automatically so the view opens on something. The best-connected hull, not the most suspicious one.">
            Opened on <b>{autoSeed.label}</b> ({autoSeed.degree} edge
            {autoSeed.degree === 1 ? "" : "s"}).
          </p>
        )}
        {!web && (
          <button
            className="btn btn-sm"
            style={{ marginTop: 8, width: "100%" }}
            disabled={!!busy}
            onClick={backToWholeNetwork}
          >
            {loadError ? "↻ Retry the whole network" : "← Back to the whole network"}
          </button>
        )}
        {loadError && (
          <p className="graph-note" style={{ color: "var(--red)" }}
             title="This is a fault, not an empty graph — nothing is being hidden deliberately.">
            Could not draw the network — {loadError}
          </p>
        )}
        {status && <p className="graph-note muted">{status}</p>}
        {/* Legend grouped by family, so the colour system explains itself. */}
        <div style={{ marginTop: 12, borderTop: "1px solid var(--border)", paddingTop: 9 }}>
          <div className="legend-group">Entities</div>
          {[["vessel", "vessel"], ["organization", "company"]].map(([k, lbl]) => (
            <div className="legendline" key={k}>
              <span className="layer-swatch" style={{ background: nodeTypeColor(k), borderRadius: k === "organization" ? 2 : "50%" }} />
              {lbl}
            </div>
          ))}
          {/* Context is a CONTROL, not a key. These three families are 88% of
              the edges and are what turned the view into a hairball, so they
              are off by default — but "these forty hulls share a flag" is a
              real question, and switching one back on is how you ask it.
              Each toggle refetches: it is a different graph, not a filter over
              the one on screen, so the layout is entitled to change. */}
          <div className="legend-group">Context — off by default</div>
          {CONTEXT_LAYERS.map(([key, nodeKind, lbl, why]) => (
            <label className="legendline" key={key}
                   title={why}
                   style={{ cursor: "pointer", userSelect: "none" }}>
              <input
                type="checkbox"
                checked={context.includes(key)}
                disabled={!!busy}
                onChange={() => toggleContext(key)}
                style={{ marginRight: 6 }}
              />
              <span className="layer-swatch" style={{ background: nodeTypeColor(nodeKind), borderRadius: "50%" }} />
              {lbl}
            </label>
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

      {/* The canvas is about to hold the main thread. Saying so, over the top
          of everything, is the difference between "working" and "crashed". */}
      {busy && (
        <div className="graph-busy">
          <div className="box">
            {busy}
            <div className="muted t-meta" style={{ marginTop: 6 }}>
              The page will not respond until this finishes.
            </div>
          </div>
        </div>
      )}

      {/* An empty canvas needs to say WHICH kind of empty it is. "No edges in
          the graph at all" and "you have not picked a vessel yet" look
          identical on screen and mean completely different things — the first
          is a fact about the corpus (GFW ownership is ~1.3% populated here),
          the second is a prompt. */}
      {nodeCount === 0 && !status && !busy && (
        <div className="empty" style={{ position: "absolute", top: "45%", left: 0, right: 0 }}>
          {/* Three kinds of empty, and they mean different things: the draw
              failed, the corpus has no ownership edges, or you have not asked
              for anything yet. Collapsing the first into the third is how a
              fault spent a release looking like a prompt. */}
          {loadError
            ? "The whole network could not be drawn. Use the retry in the panel."
            : autoSeedFailed
            ? "No ownership edges in the graph yet. Populate the graph, or pick "
              + "a vessel to check one hull directly."
            : "Pick a vessel and seed the graph to begin."}
        </div>
      )}
    </div>
  );
}

// ---- element shapes -------------------------------------------------------
// Both views build the same element, so a field can never exist in one and be
// missing in the other. Everything the stylesheet reads is precomputed here:
// no style function ever runs per element per restyle.

// How much to shrink every node, given how many share the canvas.
//
// `nodeTypeSize` is one table of diameters used by both views, and it is tuned
// for the neighbourhood — tens of nodes, each worth looking at. The whole-web
// view puts up to 1,500 in the same rectangle at the same size, and 1,500
// 30-unit discs cannot be laid out without touching whatever the layout does:
// the ink alone exceeds the canvas. Reported by an operator as "everything is
// overlapping and the object icons are too big", and visible as a clump of
// merged circles under stacked labels at the centre of the network.
//
// Area is what has to stay bounded, so the scale goes as the square root of
// the crowding. The floor keeps a vessel a dot rather than a pinprick: below
// roughly 6px on screen a node stops being clickable, and clicking a hull to
// open its own network is the whole interaction.
export function densityScale(n) {
  if (n <= FCOSE_ABOVE) return 1;
  return Math.max(0.4, Math.sqrt(FCOSE_ABOVE / n));
}

// Re-stamp diameters when the graph's size changes — an expansion can push a
// small graph past the threshold, and without this the graph ends up drawn at
// two scales at once. A data update, not a style function: the stylesheet
// reads `data(diameter)` and nothing here may reintroduce a per-element style
// callback (see the note above `nodeElement`).
function applyDensityScale(cy) {
  const scale = densityScale(cy.nodes().length);
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      const want = n.data("size") * 2 * scale;
      if (n.data("diameter") !== want) n.data("diameter", want);
    });
  });
  return scale;
}

//: Put the graph into LAYOUT space: nominal sizes, model font, no zoom
//: anywhere. This is what makes a layout reproducible — the same graph gets
//: the same picture whatever the camera was doing when it ran, and the
//: run-away collapse documented on `applyScreenScale` cannot happen because
//: nothing here reads `cy.zoom()`.
function applyLayoutSizes(cy) {
  const scale = applyDensityScale(cy);
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      const d = n.data("diameter");
      n.style({ width: d, height: d, "font-size": LABEL_PX });
    });
    cy.edges().forEach((e) => {
      e.style({ width: e.data("width") * scale, "font-size": LABEL_PX * 0.85 });
    });
  });
  return scale;
}

function nodeElement(n, { showLabel, scale = 1 }) {
  const label = n.label || shortId(n.id);
  const designated = !!(n.props && n.props.designated)
    || n.node_type === "sanctions_authority";
  const size = nodeTypeSize(n.node_type);
  // **Size carries the hierarchy.** With the context families off, every
  // remaining node is a vessel or a company and the type table gives them all
  // but the same diameter — 161 identical discs in identical little stars,
  // which is a picture with nothing to look at and no way in. Weighting by
  // connections puts the companies that control fleets, and the hulls that sit
  // in several structures, where the eye goes first.
  //
  // Logarithmic and bounded: degree runs from 1 to 25 here, and scaling
  // linearly would make one node twelve times another and swamp the layout.
  const deg = Number(n.degree) || 0;
  const weight = Math.min(2.2, Math.max(0.85, 0.85 + 0.30 * Math.log2(1 + deg)));
  return {
    data: {
      id: n.id,
      label,
      shownLabel: showLabel ? label : "",
      kind: n.node_type,
      color: nodeTypeColor(n.node_type),
      size,
      diameter: size * 2 * scale * weight,
      sanctioned: designated ? 1 : 0,
      degree: n.degree,
      props: n.props,
    },
    classes: showLabel ? "labelled" : "",
  };
}

const edgeId = (e) => `${e.edge_type}|${e.source}|${e.target}|${e.t_start || ""}`;

function edgeElement(e, { showLabel }) {
  const ownership = e.edge_type === "owned-by" || e.edge_type === "operated-by";
  const label = edgeLabel(e);
  return {
    data: {
      id: edgeId(e),
      source: e.source,
      target: e.target,
      label,
      shownLabel: showLabel ? label : "",
      edge_type: e.edge_type,
      // Carried onto the element so the detail card can re-derive the same
      // label from `e.target.data()` — the card reads element data, not the
      // API row, and without this it would fall back to "identified as" on the
      // very edges the canvas has just labelled "MMSI".
      identity_kind: e.identity_kind || null,
      color: edgeCategoryColor(e.edge_type),
      ownership: ownership ? 1 : 0,
      width: ownership ? 2 : 1.2,
      confidence: e.confidence,
      t_start: e.t_start,
      t_end: e.t_end,
      // `is_current` is only sent by the whole-graph endpoint; a neighbourhood
      // edge carries its end date instead, so derive it rather than defaulting
      // every neighbourhood edge to "ended" and dashing the entire view.
      current: (e.is_current !== undefined ? e.is_current : !e.t_end) ? 1 : 0,
    },
    classes: showLabel ? "labelled" : "",
  };
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
          <h3>{isNode ? d.label : edgeLabel(d)}</h3>
        </div>
        <button className="iconbtn" onClick={onClose}>×</button>
      </div>
      <div className="graph-detail-badges">
        {d.sanctioned === 1 && isNode && d.kind !== "sanctions_authority" &&
          <span className="badge badge-finding">Sanctioned</span>}
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
        <p className="muted t-meta" style={{ margin: "10px 0 0" }}>
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

//: Breathing room inside the available rectangle, in screen pixels.
const FIT_PAD = 36;

// Fit into the canvas the operator can actually SEE.
//
// `cy.fit()` fits the whole container, which is the whole window — the control
// panel floats on top of it. The old compensation was to fit the full width and
// then shove the picture right by half the panel: content stopped hiding under
// the panel and started running off the right edge instead, because a pan moves
// a picture without resizing it. Both symptoms were visible at once, a company
// clipped by the panel on the left and a fleet cut off on the right.
//
// Fitting to the rectangle between the panels gets both: the scale is chosen
// for the space that exists, and the centring puts the middle of the graph in
// the middle of that space.
function fitView(cy) {
  const eles = cy.elements();
  if (eles.length === 0) return;
  const bb = eles.boundingBox({ includeLabels: true, includeNodes: true });
  if (!bb.w || !bb.h) return;

  const availW = Math.max(120, cy.width() - PANEL_L - PANEL_R - FIT_PAD * 2);
  const availH = Math.max(120, cy.height() - FIT_PAD * 2);
  // Never leave the view at an unusable scale — a single stray gesture used to
  // be able to leave the whole graph a few pixels across.
  const z = clamp(Math.min(availW / bb.w, availH / bb.h));
  cy.zoom(z);
  cy.pan({
    x: PANEL_L + FIT_PAD + availW / 2 - (bb.x1 + bb.w / 2) * z,
    y: FIT_PAD + availH / 2 - (bb.y1 + bb.h / 2) * z,
  });
}

// Which nodes carry a label at rest in the whole-web view.
//
// The neighbourhood view labels everything, because "the graph should be
// readable at rest" and it holds tens of nodes. The web holds up to 1,500, and
// `syncScreenScale` pins label size to the SCREEN — so zooming out does not
// shrink text away, it stacks it. Labelling all of them produces a solid mat.
//
// So: name the things worth naming without the cursor — the focus and its
// immediate network, every organisation and sanctions authority (few, and the
// reason an ownership graph exists), anything designated, and the best-connected
// hubs. Everything else keeps its label in `data` and reveals it in the detail
// card on click.
function focusLabel(web) {
  const n = (web.nodes || []).find((x) => x.id === web.focus);
  return (n && n.label) || shortId(web.focus || "");
}


//: How far apart two labels must be ON SCREEN before both are drawn, and how
//: many nodes are even considered. The cap keeps the greedy pass below a
//: millisecond; the spacing is roughly a short name plus its leading.
const LABEL_MIN_SEPARATION_PX = 78;
const LABEL_CANDIDATE_CAP = 400;

//: Nodes worth naming, best first. Priority order: what is designated, then
//: the companies (few, and the reason an ownership graph exists), then the
//: focus and its immediate network, then the best-connected hubs.
function webLabelCandidates(cy, focusId) {
  const byDegree = (a, b) => (b.data("degree") || 0) - (a.data("degree") || 0);
  const out = [];
  const seen = new Set();
  const push = (eles) => eles.forEach((n) => {
    if (out.length >= LABEL_CANDIDATE_CAP || seen.has(n.id())) return;
    seen.add(n.id());
    out.push(n);
  });
  push(cy.nodes().filter((n) =>
    n.data("sanctioned") === 1 || n.data("kind") === "sanctions_authority")
    .sort(byDegree));
  push(cy.nodes().filter((n) => n.data("kind") === "organization").sort(byDegree));
  if (focusId) {
    const f = cy.getElementById(focusId);
    if (f.length) push(f.closedNeighborhood().nodes().sort(byDegree));
  }
  push(cy.nodes().sort(byDegree));
  return out;
}

// **Labels that get out of each other's way.**
//
// A flat budget is not enough. Labels are pinned to a constant SCREEN size, so
// they do not shrink when the view is fitted — and the density that matters is
// how close two of them land in PIXELS, which depends on the zoom and on where
// the layout put things. A cap of 55 still stacked every company name on top
// of the next around the inner ring of a concentric core, because all 25 of
// them were inside 300px of arc.
//
// So: walk the candidates best-first and accept one only if it is clear of
// every label already accepted. Everything keeps its name in `data` — this
// decides what is drawn AT REST, and the detail card shows the rest on click.
//
// Recomputed when the zoom settles, so zooming in reveals more names the way a
// map does, rather than leaving the picture permanently as sparse as its most
// zoomed-out moment.
function applyLabelDecollision(cy, candidates) {
  const minGap = LABEL_MIN_SEPARATION_PX;
  const accepted = [];
  const keep = new Set();
  for (const n of candidates) {
    const p = n.renderedPosition();
    let clear = true;
    for (const q of accepted) {
      if (Math.abs(p.x - q.x) < minGap && Math.abs(p.y - q.y) < minGap * 0.55) {
        clear = false;
        break;
      }
    }
    if (!clear) continue;
    accepted.push(p);
    keep.add(n.id());
  }
  cy.batch(() => {
    cy.nodes().forEach((n) => {
      const want = keep.has(n.id()) ? n.data("label") : "";
      if (n.data("shownLabel") !== want) n.data("shownLabel", want);
      if (want) n.addClass("labelled");
      else n.removeClass("labelled");
    });
  });
  return keep.size;
}

//: Above this many nodes the view switches from `cose` to `fcose`. Not a taste
//: threshold — `cose` is O(n^2) per iteration and was measured at 115s on 1,409
//: nodes, where `fcose` does the same graph in well under a second. Below it,
//: `cose` is kept because the neighbourhood view's look is tuned to it and
//: there is nothing to gain from changing what already works.
const FCOSE_ABOVE = 250;

//: Up to this many nodes the whole-network view can afford fcose's SPECTRAL
//: seeding, which lays a mostly-disconnected ownership forest out far better
//: than draft mode does (measured on the ownership network: 15 overlapping
//: label pairs against draft's 274). Above it, spectral seeding is the
//: superlinear cost that froze the tab for 14 seconds — see `withSeededRandom`.
const SPECTRAL_MAX_NODES = 400;

//: A deterministic pseudo-random source, installed over `Math.random` for the
//: duration of one layout run.
//:
//: fcose has two ways to place its starting positions. `randomize: false` seeds
//: from a SPECTRAL draft (an eigendecomposition of the graph Laplacian), which
//: is deterministic but was measured in Chromium at **14.4 seconds of blocked
//: main thread** on the 1,500-node whole-network view — the freeze that read as
//: the tab crashing. Cutting the iteration budget did nothing, because the
//: iterations were never the cost: 800 -> 250 iterations moved it 15.7s -> 14.4s.
//: `quality: "draft"` skips the spectral step entirely and runs the same graph
//: in **0.65 seconds**, but it requires `randomize: true` and therefore
//: `Math.random` — which would reshuffle the web on every visit, and a picture
//: that cannot be re-found is a picture that cannot be learned.
//:
//: Seeding the randomness fixes that: same graph, same numbers, same layout,
//: every time. Verified by laying the same 1,500-node graph out twice and
//: comparing every node position — identical to the last decimal.
function withSeededRandom(fn) {
  const real = Math.random;
  let s = 0x2f6e2b1 >>> 0;
  Math.random = () => {
    // xorshift32 — small, fast, and stable across browsers, which
    // `Math.random` itself is not.
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5;  s >>>= 0;
    return s / 4294967296;
  };
  try {
    return fn();
  } finally {
    Math.random = real;
  }
}

// `isWeb` says whether this is the whole-network view rather than a seeded
// neighbourhood. It is a PARAMETER and not a closure over component state:
// this function lives at module scope, and the earlier version read a `web`
// state variable that does not exist here. Every call with more than
// FCOSE_ABOVE nodes threw `ReferenceError: web is not defined` — see the note
// on `quality` below for what that broke.
//: A component bigger than this earns a force layout of its own. Below it the
//: component is a star — a company and the hulls it operates — and a force
//: simulation is both the wrong tool and, in fcose's draft mode, a broken one.
const BIG_COMPONENT = 60;

//: Spacing between a hub and its leaves, and between packed components, in
//: model units.
const STAR_GAP = 46;
const COMPONENT_PAD = 60;

// **The whole-network layout. It is a forest, so lay it out as one.**
//
// What this graph IS, measured on a corpus-shaped 1,499-node view: **276
// connected components**, nearly all of them one company and the three to five
// hulls it operates, plus a couple of larger cores around the sanctions
// authority. That is a forest of stars, and a global force simulation is the
// wrong instrument for it.
//
// It is also, specifically, a BROKEN instrument. fcose in `quality: "draft"`
// — the fast path — collapses every small component onto a single point.
// Measured headless on that graph, with real `Math.random`, not the seeded one:
//
//     fcose draft   box   250 x  442    23 distinct positions out of 961
//     this layout   box  3724 x 2898  1499 distinct positions out of 1499
//                                      median node spacing 46 units
//
// Twenty-three positions for nine hundred nodes is every star crushed to a dot
// and the dots packed into a band — which is exactly what was on screen: a
// diagonal smear with labels stacked on labels and most of the graph invisible.
// Tuning repulsion, gravity, `nodeSeparation` and `packComponents` moved the
// bounding box and changed nothing about the collapse, because the collapse is
// not a spacing problem.
//
// So each component gets the cheapest treatment that suits its shape, and the
// components are then packed:
//
//   * **a star** (the common case) — highest-degree node in the middle, the
//     rest on a ring around it. Deterministic, instant, and it is a truthful
//     picture of "this company operates these hulls";
//   * **a real network** (above BIG_COMPONENT) — fcose with SPECTRAL seeding,
//     which is affordable here precisely because it is scoped to one
//     component. The cost that made spectral unusable is superlinear in the
//     node count it is handed, and a component is a fraction of the graph;
//   * **packed** into rows about as wide as the whole is tall, biggest first,
//     so the result is roughly square rather than a ribbon.
function layoutForest(cy) {
  const comps = cy.elements().components();
  const boxes = [];

  for (const comp of comps) {
    const nodes = comp.nodes();
    if (nodes.length === 1) {
      nodes[0].position({ x: 0, y: 0 });
    } else if (nodes.length > BIG_COMPONENT) {
      // **Rings by connectedness, not a force simulation.**
      //
      // The large components here are hub-and-spoke too, just deeper: a
      // sanctions authority, the companies it designated, and the hulls those
      // companies operate. Measured on exactly that shape (126 nodes, 18-unit
      // node diameters), nearest-neighbour spacing and aspect ratio:
      //
      //     fcose draft      median-nn  0   — collapsed, 76 distinct of 126
      //     fcose spectral   median-nn  8   — overlapping; nodes are 18 wide
      //     cose             median-nn 67   — good, but 546ms and O(n^2)
      //     concentric       median-nn 48   — clean rings, 20ms, deterministic
      //
      // Concentric imposes an organising principle rather than discovering
      // one, and that is worth saying: the rings mean degree and nothing else.
      // For an ownership graph that is a fair reading — an authority is more
      // connected than a company, a company than a hull — and it is what the
      // panel already says it centres on. The seeded neighbourhood view keeps
      // a real force layout, which is where structure is actually read.
      comp.layout({
        name: "concentric",
        animate: false, fit: false, padding: 0,
        minNodeSpacing: STAR_GAP,
        concentric: (n) => n.degree(),
        levelWidth: () => 1,
      }).run();
    } else {
      const sorted = nodes.sort((a, b) => b.degree() - a.degree());
      const hub = sorted[0];
      const leaves = sorted.slice(1);
      const r = Math.max(STAR_GAP, (STAR_GAP * leaves.length) / (2 * Math.PI));
      hub.position({ x: 0, y: 0 });
      leaves.forEach((n, i) => {
        const a = (2 * Math.PI * i) / leaves.length;
        n.position({ x: r * Math.cos(a), y: r * Math.sin(a) });
      });
    }
    const bb = comp.boundingBox();
    boxes.push({ comp, w: bb.w, h: bb.h, x1: bb.x1, y1: bb.y1 });
  }

  // Shelf-pack, biggest first. The row width is derived from the total area so
  // the packed result is roughly square: a fixed row count would give a 40:1
  // ribbon on one graph and a column on another.
  boxes.sort((a, b) => b.w * b.h - a.w * a.h);
  const area = boxes.reduce((t, b) => t + (b.w + COMPONENT_PAD) * (b.h + COMPONENT_PAD), 0);
  const rowWidth = Math.sqrt(area) * 1.3;
  let x = 0, y = 0, rowH = 0;
  cy.batch(() => {
    for (const b of boxes) {
      if (x > 0 && x + b.w > rowWidth) { x = 0; y += rowH + COMPONENT_PAD; rowH = 0; }
      const dx = x - b.x1, dy = y - b.y1;
      b.comp.nodes().forEach((n) => {
        const p = n.position();
        n.position({ x: p.x + dx, y: p.y + dy });
      });
      x += b.w + COMPONENT_PAD;
      rowH = Math.max(rowH, b.h);
    }
  });
}

function runLayout(cy, nodeCount = 0, { isWeb = false } = {}) {
  // Sizes before positions, in LAYOUT space: the layout reads node sizes when
  // it separates them, and it must read a number that does not depend on where
  // the camera is. See the note on `applyScreenScale` for what happened when
  // it did.
  applyLayoutSizes(cy);

  if (isWeb) {
    layoutForest(cy);
    return;
  }

  // The seeded neighbourhood is ONE connected component of a few dozen nodes,
  // and `cose`'s organic look is tuned for it. Nothing here is worth changing.
  //
  // Un-animated on purpose: an animating force simulation keeps nudging nodes
  // for seconds after a click and reads as a glitch. `randomize: false` keeps
  // it deterministic, so re-finding a cluster you saw yesterday is possible.
  withSeededRandom(() => cy.layout({
    name: "cose",
    animate: false,
    padding: 50,
    // Lay out around the LABELS, not the dots. Every node is labelled at rest
    // in this view, so a layout that only avoids circle overlap still stacks
    // text on text.
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
  }).run());
}
