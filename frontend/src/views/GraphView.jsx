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
  edgeCategoryColor, edgeTypeLabel, fmtDate, humanKey, nodeTypeColor,
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
//: How far in the whole-network view is allowed to open. Framing the focus
//: node's neighbourhood is right; letting that fit run to MAX_ZOOM when the
//: focus has three neighbours is not — the operator lands on a handful of
//: giant circles with no network around them.
const OPENING_MAX_ZOOM = 1.1;
//: How many labelled nodes a layout will measure boxes for. Label-aware
//: spacing is what stops two company names sharing a spot; measuring 1,400 of
//: them is what made it too slow to keep on. This is the count of nodes that
//: actually draw text, not the node count.
const LABEL_AWARE_MAX_LABELS = 300;

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

  // Immediate, unthrottled. The layout needs the true sizes applied BEFORE it
  // runs (see `settleLayout`), and a rAF-deferred version would hand it the
  // previous pass's numbers.
  const applyScreenScale = useCallback((cy) => {
    if (cy.destroyed()) return;
    const z = cy.zoom();
    const px = LABEL_PX / z;
    const nodes = cy.nodes(".labelled");
    const labelledEdges = cy.edges(".labelled");
    cy.batch(() => {
      if (!nodes.empty()) nodes.style("font-size", px);
      if (!labelledEdges.empty()) labelledEdges.style("font-size", px * 0.85);
      for (const [d, coll] of sizeBuckets(cy)) {
        const s = d / z;
        coll.style({ width: s, height: s });
      }
      // Lines belong to the same visual system as the dots and the type. Left
      // in model units they were the one thing that still shrank: the network
      // settles around zoom 0.4, where a 2px ownership line draws at 0.8px and
      // a 1.2px context line at half a pixel. The relationships — the entire
      // point of a graph — faded to grey haze while the nodes stayed crisp,
      // which is most of why the uncluttered version read as empty rather than
      // clear.
      for (const [w, coll] of edgeWidthBuckets(cy)) coll.style("width", w / z);
    });
  }, [sizeBuckets, edgeWidthBuckets]);

  const syncScreenScale = useCallback((cy) => {
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      applyScreenScale(cy);
    });
  }, [applyScreenScale]);

  // **Lay out, frame, then lay out again knowing how big the type ended up.**
  //
  // Pinning labels to the screen and fitting a graph to the viewport pull
  // against each other. The layout spaces nodes in MODEL units using the
  // stylesheet font (11). The fit then zooms out to show everything — measured
  // at 0.30 on the ownership network — and the pin scales the type back up to
  // stay 11px on screen, which in model units is 11/0.30 = 36.7. Every label is
  // suddenly three times the size the layout allowed for, so they collide:
  // 40 overlapping label pairs among 40 labelled nodes.
  //
  // Neither half is wrong. Constant-size type is what makes a zoomed-out graph
  // readable, and fitting is what makes it whole. What was wrong is that the
  // layout was told one thing and the renderer did another.
  //
  // So: run it, frame it, apply the type size that framing implies, and run it
  // again against the real numbers. Measured on the ownership network the
  // overlap count goes 40 -> 1 -> 0 and the zoom settles at 0.25, so a second
  // pass is enough and a third changes nothing. It is a fixed point rather
  // than a fudge: bigger labels spread the graph, spreading lowers the fit
  // zoom, a lower zoom means bigger labels — and the loop converges because
  // each round adds less than the last.
  // The camera needs the same treatment as the layout, and for the same
  // reason. `fitView` picks a zoom from the graph's bounding box, but that box
  // includes labels whose size depends on the zoom it is about to replace — so
  // one fit lands wrong, and the picture ends up overflowing every edge at a
  // scale chosen for different type. Alternating fit and rescale converges on
  // the same fixed point the layout does; three rounds is comfortably past it.
  const settleCamera = useCallback((cy, frame) => {
    for (let i = 0; i < 3; i++) {
      frame();
      applyScreenScale(cy);
    }
  }, [applyScreenScale]);

  const settleLayout = useCallback((cy, nodeCount, frame) => {
    for (let pass = 0; pass < 2; pass++) {
      runLayout(cy, nodeCount);
      settleCamera(cy, frame);
    }
  }, [applyScreenScale, settleCamera]);

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
            // `markWebLabels`. The label is precomputed onto the element as
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
            "arrow-scale": 0.7,
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
      ],
    });

    const clearHl = () => cy.elements().removeClass("faded hl");
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
    cy.on("tap", "node", (e) => {
      const d = e.target.data();
      setInfo({ type: "node", data: d });
      if (d.kind === "vessel" || d.kind === "organization") expand(d.id, 1);
    });
    cy.on("tap", "edge", (e) => setInfo({ type: "edge", data: e.target.data() }));
    cy.on("tap", (e) => {
      if (e.target === cy) { setInfo(null); clearHl(); }
    });
    cy.on("zoom", () => syncScreenScale(cy));

    cyRef.current = cy;
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
      cy.destroy();
    };
  }, [syncScreenScale]);

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
      // Labels FIRST, then layout. `cose` lays out around label boxes
      // (`nodeDimensionsIncludeLabels`), and it was being handed a graph whose
      // labels had not been assigned yet — so it spaced bare circles and the
      // text landed on top of itself the moment the labels appeared. Every
      // "Harrow Maritime Services" sitting across "MORNING BREEZE" came from
      // these two lines being the wrong way round.
      markWebLabels(cy, focusId);

      const node = focusId ? cy.getElementById(focusId) : cy.collection();
      if (node.length) {
        node.addClass("focus");
        node.closedNeighborhood().addClass("focus-nbr");
      }
      // **Open on the whole network when the whole network is legible.**
      //
      // Framing the focus neighbourhood was the right call when the default
      // view was everything: fitting 1,500 nodes showed a grey mass, so the
      // camera started somewhere readable and left the rest to be found by
      // zooming out. But it also meant the view opened mid-graph with edges
      // running off every side, which reads as a picture that has been cut off.
      //
      // The ownership network is ~325 elements and, with symbols pinned to the
      // screen, fits whole and legible. Showing all of it is the honest opening
      // — the panel's claim is "here is the ownership network", and the camera
      // should agree with the sentence.
      if (!node.length) {
        settleLayout(cy, g.nodes.length, () => fitView(cy));
      } else {
        // **Open framed on a cluster, not fitted to everything.**
        //
        // Fitting the whole network was tried and is worse to look at. The
        // ownership graph is a forest of about thirty small stars, and the
        // whole of it at once is a field of identical dots too small to carry
        // a name — tidy, and saying nothing. Framed, the same data reads the
        // way it used to: substantial nodes, legible companies, edges you can
        // follow. The fit control and a scroll wheel get you the overview when
        // you want it, which is the right way round — an overview is something
        // you ask for, a working view is what you land in.
        //
        // Un-animated because `settleLayout` runs the framing twice; animating
        // a camera move that is about to be recomputed reads as a stutter, and
        // there is nothing on screen yet to animate from.
        //
        // Capped, because a fit is a fit to whatever is there: a focus with
        // three neighbours fills the viewport with three enormous circles and
        // no surrounding network, which reads as "the graph contains four
        // things".
        const nbr = node.closedNeighborhood();
        settleLayout(cy, g.nodes.length, () => {
          cy.fit(nbr, 90);
          if (cy.zoom() > OPENING_MAX_ZOOM) {
            cy.zoom({ level: OPENING_MAX_ZOOM,
                      renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
            cy.center(nbr);
          }
        });
      }
      setNodeCount(cy.nodes().length);
      setWeb(g);
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
      runLayout(cy, cy.nodes().length);
      if (isSeed) cy.getElementById(nodeId).addClass("seed");
      fitView(cy);
      syncScreenScale(cy);
      setNodeCount(cy.nodes().length);
      setStatus(nb.truncated ? "traversal budget reached — partial neighbourhood shown" : "");
    } catch {
      if (runRef.current === myRun) setStatus("nothing further to expand there");
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
          Opens two hops: operator, parent company, and vessels sharing the owner.
          Click a vessel or company to expand; hover to isolate.
        </p>
        {/* What is on screen, stated in numbers. A web that looks complete is
            how a viewer concludes the dataset is smaller than it is, so the
            truncation is named here rather than left to be inferred. */}
        {web && (
          <div className="graph-note muted">
            <div>
              Showing <b>{web.nodes.length.toLocaleString()}</b> entities and{" "}
              <b>{web.edges.length.toLocaleString()}</b> ownership relationships
              {web.truncated ? (
                <>
                  {" "}of <b>{(web.matched_nodes ?? web.total_nodes).toLocaleString()}</b>{" "}
                  and <b>{(web.matched_edges ?? web.total_edges).toLocaleString()}</b>{" "}
                  that match — this is a partial picture, the most-connected
                  core. Seed a vessel above to see any hull's own network in
                  full.
                </>
              ) : (
                <> — every one that matches.</>
              )}
            </div>
            {/* Hidden and truncated are different facts. A layer switched off
                is one checkbox away; a node past the cap is not, and reporting
                them as one number would tell an operator to go looking for a
                control that would not help. */}
            {web.total_nodes > (web.matched_nodes ?? web.total_nodes) && (
              <div style={{ marginTop: 5 }}>
                <b>{(web.total_nodes - web.matched_nodes).toLocaleString()}</b>{" "}
                {web.total_nodes - web.matched_nodes === 1
                  ? "more node carries" : "more nodes carry"}{" "}
                only flag, port or identity links. They are hidden, not
                missing — switch a Context layer on below to bring them back.
              </div>
            )}
            {web.focus_basis && (
              <div style={{ marginTop: 5 }}>
                Centred on <b>{focusLabel(web)}</b> — {web.focus_basis}. That is
                where the camera starts, not a finding: it is the
                best-connected node, not the most suspicious one.
              </div>
            )}
            <div style={{ marginTop: 5 }}>
              Dashed links are relationships that have ended; solid ones are
              current.
            </div>
            {/* An interaction that stops existing without saying so reads as a
                broken feature — which is exactly how it was reported. */}
            {web.nodes.length + web.edges.length > INTERACTIVE_MAX_ELEMENTS && (
              <div style={{ marginTop: 5 }}>
                Hover-to-isolate is off above{" "}
                {INTERACTIVE_MAX_ELEMENTS.toLocaleString()} elements — fading
                this many costs about half a second per hover. Switch a Context
                layer off to get it back.
              </div>
            )}
          </div>
        )}
        {/* Say that the view chose this hull, and on what basis. An operator
            who assumes a considered selection would read "most edges" as
            "most interesting", and those are not the same claim. */}
        {autoSeed && (
          <p className="graph-note muted">
            Opened on <b>{autoSeed.label}</b> — the most connected vessel in the
            graph ({autoSeed.degree} edge{autoSeed.degree === 1 ? "" : "s"}),
            chosen automatically so the view opens on something. It is the
            best-connected hull, not the most suspicious one.
          </p>
        )}
        {!web && (
          <button
            className="btn btn-sm"
            style={{ marginTop: 8, width: "100%" }}
            disabled={!!busy}
            onClick={() => { setParams({}); }}
          >
            ← Back to the whole network
          </button>
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
          {autoSeedFailed
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
  const label = edgeTypeLabel(e.edge_type);
  return {
    data: {
      id: edgeId(e),
      source: e.source,
      target: e.target,
      label,
      shownLabel: showLabel ? label : "",
      edge_type: e.edge_type,
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
          <h3>{isNode ? d.label : edgeTypeLabel(d.edge_type)}</h3>
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

// How many hubs to name at rest, given how many nodes share the canvas.
//
// A fixed 40 was tuned on a smaller web. Labels are pinned to a constant SCREEN
// size (`syncScreenScale`), so zooming out to see the whole network does not
// shrink them — it stacks them, and 40 hub names plus every organisation lands
// as a mat of text over the dense core. Fewer names on a bigger graph is the
// only lever, since the graph itself cannot get less dense.
function hubLabelCount(n) {
  return Math.max(12, Math.min(40, Math.round(600 / Math.sqrt(Math.max(1, n)))));
}

function markWebLabels(cy, focusId, hubCount = null) {
  if (hubCount == null) hubCount = hubLabelCount(cy.nodes().length);
  cy.batch(() => {
    const show = (eles) => eles.forEach((n) => {
      n.data("shownLabel", n.data("label"));
      n.addClass("labelled");
    });
    show(cy.nodes().filter((n) =>
      n.data("kind") === "organization" ||
      n.data("kind") === "sanctions_authority" ||
      n.data("sanctioned") === 1));
    show(cy.nodes().sort((a, b) => (b.data("degree") || 0) - (a.data("degree") || 0))
      .slice(0, hubCount));
    if (focusId) {
      const f = cy.getElementById(focusId);
      if (f.length) show(f.closedNeighborhood().nodes());
    }
  });
}

//: Above this many nodes the view switches from `cose` to `fcose`. Not a taste
//: threshold — `cose` is O(n^2) per iteration and was measured at 115s on 1,409
//: nodes, where `fcose` does the same graph in well under a second. Below it,
//: `cose` is kept because the neighbourhood view's look is tuned to it and
//: there is nothing to gain from changing what already works.
const FCOSE_ABOVE = 250;

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

function runLayout(cy, nodeCount = 0) {
  // Un-animated on purpose: an animating force simulation keeps nudging nodes
  // for seconds after a click and reads as a glitch. Both layouts settle
  // deterministically, so the same graph always looks the same — a web that
  // reshuffled on every visit would make it impossible to learn, and
  // re-finding a cluster you saw yesterday is most of the value.
  //
  // **The web always uses fcose, whatever its size.** The ownership network is
  // structurally a forest — a company and the hulls it owns, with few links
  // between companies — and `cose` lays disconnected components out in tidy
  // rows. On 161 nodes that produced a grid of near-identical stars: honest
  // about the data, and unreadable as a picture, because a regular grid has no
  // shape to remember and nothing to draw the eye. fcose packs components
  // organically, which is what made the old view look like a network.
  // fcose was tried for the web and reverted. It packs disconnected components
  // instead of laying them in rows, which sounded right for an ownership
  // forest — but `packComponents` crams them into one diagonal band with the
  // nodes touching, measured at 64 overlapping label pairs against cose's 9.
  // Rows are not elegant; a blob is not readable.
  const big = nodeCount > FCOSE_ABOVE;
  // Diameters before positions: the layout reads node sizes when it separates
  // them, so shrinking after it ran would leave the spacing of the larger dots.
  const scale = applyDensityScale(cy);
  const opts = big ? {
    name: "fcose",
    animate: false,
    // Draft quality with a SEEDED random source — see `withSeededRandom` for
    // why this is both the fast path and still a deterministic one.
    //
    // The ownership web is the exception: at ~160 nodes the spectral seeding
    // costs nothing and is worth a lot. Measured on it, draft settled to 274
    // overlapping label pairs where `default` gives 15 — draft starts from
    // scattered positions and, on a graph that is mostly small disconnected
    // stars, never fully recovers. The size threshold is what keeps this from
    // being a promise about the 29,000-node real graph.
    quality: web && nodeCount <= 400 ? "default" : "draft",
    randomize: !(web && nodeCount <= 400),
    padding: 50,
    // Measure label boxes when there are few enough to be worth measuring.
    // Only a minority of nodes carry a label here (see `markWebLabels`), and
    // measuring 1,400 of them costs more than it buys when most are empty —
    // but with a couple of hundred it is what stops company names landing on
    // top of each other. `markWebLabels` now runs BEFORE the layout, so this
    // count is the real one rather than zero.
    nodeDimensionsIncludeLabels:
      cy.nodes(".labelled").length <= LABEL_AWARE_MAX_LABELS,
    // Left at the values tuned against the real corpus graph. Draft mode
    // starts from scattered positions rather than a spectral draft, so it
    // settles a little looser than the spectral path did — but retuning the
    // forces is a judgement to make against real data, not against a
    // stand-in, so they stay put.
    nodeRepulsion: 9000,
    idealEdgeLength: 70,
    edgeElasticity: 0.35,
    gravity: 0.3,
    gravityRange: 3.2,
    // Draft mode starts from scattered positions rather than a spectral draft,
    // so it wants the iterations the spectral seeding used to save. They are
    // cheap: the whole run is well under a second either way.
    numIter: 1200,
    fit: false,
  } : {
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
  };
  withSeededRandom(() => cy.layout(opts).run());
}
