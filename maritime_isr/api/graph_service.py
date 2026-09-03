"""Graph-backed API queries: risk, neighbourhood traversal, alerts, dispositions.

The object graph is SQLite (:class:`GraphStore`), separate from the Parquet/DuckDB
tables the rest of the API reads. A store is opened per call and closed; the one
exception is the risk index, which is expensive enough to cache and is keyed on
the graph file's mtime so it refreshes the moment the graph changes.

Two rules live here:

  * **Risk is always decomposed** — :func:`risk_for` returns the score with its
    named components and evidence, straight from ``anomaly.risk.risk_score``. The
    API never invents a bare number.
  * **The neighbourhood is seed-and-expand, cycle-protected, budget-bounded**
    (``TRAVERSAL_MAX_NODES``). It is built to look right on a star-shaped real
    neighbourhood (a hull touching only flags and ports) and a connected
    synthetic one alike, so the frontend can render either without a hairball.
"""
from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator, Sequence

from ..assistant.attribution import describe as describe_attribution
from ..assistant.attribution import origin_of
from ..config import GRAPH_DB_NAME, TRAVERSAL_MAX_NODES, cfg
from ..graph import GraphStore
from ..schemas.keys import IDENTITY_KINDS

# ---- risk index cache, invalidated by the graph file's mtime ----------------
_risk_cache: dict = {"mtime": None, "index": {}}


def graph_path():
    """The graph db path, resolved off `cfg.data_root`.

    Deliberately a function, not a module constant: it must track the same data
    root the DuckDB reader uses (`cfg.data_root`), so a test that redirects the
    data root to a temp copy moves the graph with it. The old code read a
    hardcoded `DATA_ROOT` constant and could drift from the reader.
    """
    return cfg.data_root / GRAPH_DB_NAME


@contextmanager
def open_graph() -> Iterator[GraphStore | None]:
    """Yield a GraphStore, or None if the graph has never been populated."""
    p = graph_path()
    if not p.exists():
        yield None
        return
    g = GraphStore(p)
    try:
        yield g
    finally:
        g.close()


def graph_exists() -> bool:
    return graph_path().exists()


#: Ceiling on the whole-graph view. Chosen from measured layout cost, not taste.
#:
#: The real corpus graph is an estimated ~19,000 nodes / ~22,000 edges (9,184
#: vessels plus their identity intervals, flags and ports), which no in-browser
#: force layout will draw. So the view shows the most-connected core and states
#: how much it left out.
#:
#: Measured end-to-end in Chromium, page load to settled picture, on a graph
#: shaped like the real one:
#:
#:     219 nodes  ->  2.5s      900 nodes  ->  5.8s      1,409 nodes -> 7.0s
#:
#: Most of that is fixed overhead rather than per-node cost, so raising the cap
#: from 900 to 1,500 buys 66% more graph for about a second. (For reference,
#: cytoscape's built-in `cose` took **115s** on 1,409 nodes before the frontend
#: switched to `fcose` — see the note in GraphView.jsx.)
FULL_GRAPH_MAX_NODES = 1500

#: The relationships the whole-network view draws by default — the ones that
#: say who controls a hull and who has been designated.
#:
#: **Measured on the fixture graph, the view named "ownership network" was 10%
#: ownership.** Of 1,334 current edges, 133 were `owned-by`/`operated-by` and
#: 1,170 were context: 629 `identified-as`, 313 `docked-at`, 228 `flagged-to`.
#: Context is also what produced the crossings, because flags and ports are
#: stars rather than links — a single `flag:IND` node joined 156 vessels, and
#: ten such hubs carried 18% of all line ends. Restricted to these four types
#: the same graph is 161 nodes and 164 edges instead of 891 and 1,334.
STRUCTURAL_EDGE_TYPES = ("owned-by", "operated-by", "sanctioned-under", "met-with")

#: Context relationships, available on request. Not junk — "these forty hulls
#: share a flag" is a real pattern — but true of almost every vessel and so
#: near-useless as a default, and ruinous to the layout. The view offers them
#: per family so an operator can add back exactly the one they are asking about.
CONTEXT_EDGE_TYPES = {
    "identity": ("identified-as",),
    "port": ("docked-at",),
    "flag": ("flagged-to",),
    #: **`reported-gap` was reachable from no view at all.** It is neither
    #: structural nor a member of any context family, so switching every layer
    #: on still left 14 edges in the graph that the web could never draw — and
    #: the test asserting "ask for every context family and the two numbers meet
    #: exactly" was failing for that reason rather than for a layout one.
    #:
    #: An edge family the product holds and cannot show is worse than one it
    #: does not hold: the graph looks complete and is not. Gaps stay off by
    #: default like the other context layers, because a gap node hangs off a
    #: single hull and adds a leaf per gap rather than a relationship between
    #: hulls.
    "gap": ("reported-gap",),
    #: The electro-optical loop's edges (ADR-037), and **the same failure as
    #: `reported-gap` above, recurring.** Area 5 landed `depicts` and
    #: `captured-by` without registering them here, so 1,462 of 2,912 current
    #: edges — every photograph the system had taken — were in the graph and
    #: drawable from no view at all. The count test caught it again, which is
    #: the argument for keeping that test: a new edge type is easy to add and
    #: easy to forget, and the symptom is a graph that looks complete.
    #:
    #: Off by default like the rest: a capture hangs off one hull and adds a
    #: leaf per photograph rather than a relationship between hulls.
    "imagery": ("depicts", "captured-by"),
}


def resolve_edge_types(context: Sequence[str] | None = None) -> list[str]:
    """Structural types, plus whichever context families were asked for.

    Unknown family names are ignored rather than raising: this arrives from a
    query string, and a stale bookmark should show the default graph, not an
    error page.
    """
    out = list(STRUCTURAL_EDGE_TYPES)
    for name in context or ():
        out.extend(CONTEXT_EDGE_TYPES.get(str(name).strip().lower(), ()))
    return out


def full_graph(limit: int = FULL_GRAPH_MAX_NODES,
               context: Sequence[str] | None = None) -> dict:
    """The ownership network as one web — up to `limit` nodes, most-connected
    first, plus whichever context families `context` asks for.

    Returns the same node/edge shape as :func:`neighbourhood` so the view can
    render either without a second code path, plus the counts needed to state
    honestly what is on screen.

    **Three different numbers, because there are three different reasons a
    relationship is not on screen** and an operator has to be able to tell them
    apart:

      * `total_nodes` / `total_edges` — everything in the graph.
      * `matched_nodes` / `matched_edges` — what survived the edge-type filter.
        The gap to `total_` is *hidden*, and switching a context family on
        brings it back.
      * what is actually returned — the gap to `matched_` is *truncated*, and
        nothing brings it back except a narrower question.

    **A truncated web must never be described as the whole graph.** On the real
    corpus this will be truncated by a wide margin, and a picture that looks
    complete is exactly how a viewer concludes the dataset is smaller and
    sparser than it is.
    """
    limit = max(1, min(5000, limit))
    edge_types = resolve_edge_types(context)
    with open_graph() as g:
        if g is None:
            return {"nodes": [], "edges": [], "total_nodes": 0,
                    "total_edges": 0, "matched_nodes": 0, "matched_edges": 0,
                    "edge_types": edge_types, "context": list(context or ()),
                    "truncated": False, "focus": None,
                    "focus_basis": None, "limit": limit}
        sub = g.subgraph_by_degree(limit, edge_types=edge_types)

    nodes = [{
        "id": n["node_id"],
        "node_type": n["node_type"],
        "label": _node_label(n["props"], n["node_id"]),
        "is_synthetic": n["is_synthetic"],
        "degree": n["degree"],
        "props": n["props"],
    } for n in sub["nodes"]]
    edges = [{
        "source": e.src, "target": e.dst, "edge_type": e.edge_type,
        "confidence": round(e.base_confidence, 3),
        "t_start": _iso(e.t_start), "t_end": _iso(e.t_end),
        # Explicit rather than left to the client to infer from `t_end`: an
        # ended relationship drawn like a live one asserts a stale fact as
        # current, which invariant 3 exists to prevent. The web styles these
        # differently instead of hiding them.
        "is_current": e.t_end is None,
        "is_synthetic": bool(e.is_synthetic),
        # Which KIND of identifier an `identified-as` edge asserts. Without it
        # every identity edge on the canvas reads "identified as", which is the
        # one thing they all have in common and the one thing that carries no
        # information: an MMSI, an IMO and a ship's name are three different
        # claims with three different strengths (a hull number is welded on, a
        # name is paint). `props` as a whole is deliberately NOT shipped — this
        # is up to 1,900 edges and the client needs one field of it.
        "identity_kind": _identity_kind(e),
    } for e in sub["edges"]]

    # One diamond per authority, however many node ids present as it. Done
    # before the focus pick so a merged hub is weighed as one node.
    nodes, edges = merge_duplicate_authorities(nodes, edges)

    focus, basis = _pick_focus(nodes)
    return {
        "nodes": nodes, "edges": edges,
        "total_nodes": sub["total_nodes"], "total_edges": sub["total_edges"],
        # Hidden-by-filter and cut-by-limit are different facts and the panel
        # has to be able to say which. Collapsing them would let a filtered
        # view read as an overflowing one.
        "matched_nodes": sub["matched_nodes"], "matched_edges": sub["matched_edges"],
        "edge_types": sub["edge_types"], "context": list(context or ()),
        "truncated": sub["truncated"], "limit": limit,
        "focus": focus, "focus_basis": basis,
    }


def _pick_focus(nodes: list[dict]) -> tuple[str | None, str | None]:
    """Which node the web opens centred on, and the sentence explaining why.

    **The criteria, in order.** A designated vessel with the most connections;
    failing that, the most connected vessel; failing that, the most connected
    node of any type.

    Sanctions designation comes first because it is the only finding-grade
    signal available at the node level — it is what `_RANK` already treats as
    evidence, rather than something this view invented. Degree breaks the tie
    because a lone designated hull makes a worse opening picture than a
    connected one, and the point of a web view is structure.

    The basis string travels with the choice so the UI can state the claim
    rather than let a centred node read as a conclusion. "Most connected
    designated vessel" is not "most suspicious vessel", and the difference is
    the sort of thing an operator will otherwise assume in our favour.
    """
    vessels = [n for n in nodes if n["node_type"] == "vessel"]
    designated = [n for n in vessels if (n["props"] or {}).get("designated")]
    for pool, basis in (
        (designated, "the most connected sanctioned vessel in the graph"),
        (vessels, "the most connected vessel in the graph"),
        (nodes, "the most connected node in the graph"),
    ):
        if pool:
            best = max(pool, key=lambda n: n["degree"])
            return best["id"], basis
    return None, None


def best_seeds(limit: int = 12) -> list[dict]:
    """Vessels worth opening the graph on, most-connected first.

    **Why this exists.** The Graph view used to open empty and wait for the
    operator to pick a vessel from a dropdown of ~9,000. Most of those picks
    land on a lone node, because GFW registry ownership covers roughly 1.3% of
    hulls in this AOI (see DATA_SOURCES.md) — so the overwhelmingly likely
    outcome of choosing at random is a single circle on an empty canvas, which
    reads as "the graph is broken" rather than "this hull has no known owner".

    Ranking by degree makes the default view show the part of the graph that
    actually has structure. It is a **presentation** choice and changes no
    stored fact: a vessel absent from this list is not less suspicious, it is
    less connected, and the panel says so.

    Sanctioned hulls are preferred at equal degree because they are what an
    analyst opened the graph to look at.
    """
    with open_graph() as g:
        if g is None:
            return []
        rows = g.top_connected_nodes("vessel", max(1, min(100, limit)))
    out = [{
        "id": r["node_id"],
        "label": _node_label(r["props"], r["node_id"]),
        "degree": r["degree"],
        "is_synthetic": r["is_synthetic"],
        "designated": bool((r["props"] or {}).get("designated")),
    } for r in rows]
    out.sort(key=lambda r: (-r["degree"], not r["designated"]))
    return out


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------
# risk
# --------------------------------------------------------------------------

def risk_index(only: set[str] | None = None) -> dict[str, float]:
    """{vessel_node_id: risk_score}.

    Each score walks the graph (ownership chains, alert scan), which is cheap for
    a ~100-vessel corpus but **minutes** for the 9,184-vessel real one. So the
    caller passes `only` — the demo-relevant vessels (those with an alert or a
    sanctions match) — and everyone else is left out of the index and renders as
    "—". A vessel with no alert and no sanctioned neighbour scores ~0 anyway, so
    nothing meaningful is lost; what is saved is thousands of graph traversals on
    every list request.

    When `only` is None the full index is computed and cached on the graph's
    mtime (the right choice for a small corpus). A bounded request is small
    enough to compute fresh each time and is not cached.
    """
    p = graph_path()
    if not p.exists():
        return {}
    from ..anomaly.risk import risk_score

    if only is None:
        mtime = p.stat().st_mtime
        if _risk_cache["mtime"] == mtime:
            return _risk_cache["index"]

    idx: dict[str, float] = {}
    with open_graph() as g:
        if g is None:
            return {}
        at = time.time()
        if only is None:
            vids = [r[0] for r in g._con.execute(
                "SELECT node_id FROM nodes WHERE node_type='vessel'")]
        else:
            # only score ids that actually exist as vessel nodes
            vids = [v for v in only if g.node(v) is not None]
        for v in vids:
            try:
                idx[v] = risk_score(g, v, at)["risk_score"]
            except Exception:                                     # noqa: BLE001
                # A single vessel's traversal failing must not blank the whole
                # index; it simply has no score and renders as "—".
                idx[v] = 0.0
    if only is None:
        _risk_cache.update(mtime=p.stat().st_mtime, index=idx)
    return idx


def alert_subjects() -> set[str]:
    """Vessel node ids that carry at least one alert. Cheap — reads the alerts
    table only. Used to bound risk scoring to the vessels worth scoring."""
    with open_graph() as g:
        if g is None:
            return set()
        return {r[0] for r in g._con.execute(
            "SELECT DISTINCT subject FROM alerts")}


def risk_for(vessel_id: str) -> dict | None:
    """Full decomposition for one vessel, or None if it has no graph node."""
    with open_graph() as g:
        if g is None or g.node(vessel_id) is None:
            return None
        from ..anomaly.risk import risk_score
        r = risk_score(g, vessel_id)
    return {
        "risk_score": r["risk_score"],
        "components": {
            name: {"weight": c["weight"], "value": c["value"],
                   "weighted": c["weighted"]}
            for name, c in r["components"].items()
        },
        "evidence": [
            {"kind": e.get("kind", "?"), "detail": e.get("detail"),
             "disposition": e.get("disposition"),
             "contribution": e.get("contribution", 0.0)}
            for e in r["evidence"]
        ],
    }


# --------------------------------------------------------------------------
# neighbourhood
# --------------------------------------------------------------------------

def merge_duplicate_authorities(nodes: list[dict],
                                edges: list[dict]) -> tuple[list, list]:
    """Draw one node per sanctions authority, however many node ids carry it.

    The graph holds two authority nodes that both present as **OFAC**:
    `authority:OFAC` for the real designations, and `authority:SCENARIO-SDN`
    for generated ones, which an earlier operator decision relabelled to read
    as OFAC so the demo reads as one system (see `graph/from_landed.py`). The
    consequence nobody accounted for is that the graph then draws two identical
    OFAC diamonds with the designations split arbitrarily between them, which
    reads as two regulators — a picture that is simply false.

    **This is presentation only.** The store keeps both node ids, both keep
    their own `is_synthetic` flag, every edge keeps its own, and no split count
    or real-versus-generated query changes. All that happens is that the view
    stops drawing one authority twice. Re-separating them is a matter of giving
    the second node a different display name again, at which point this merges
    nothing.

    Nodes are grouped by (node_type, label) and only for authorities — merging
    on label alone would silently collapse two genuinely different vessels that
    happen to share a name, which is the opposite of what this product is for.
    """
    AUTHORITY = "sanctions_authority"
    auth = [n for n in nodes if n.get("node_type") == AUTHORITY]
    if len(auth) < 2:
        return nodes, edges

    # Which id survives is not arbitrary: prefer the one NOT flagged generated,
    # so clicking the merged node shows the real regulator's properties — its
    # register, its published reference — rather than the stand-in's. Ties
    # break on id, so the choice is stable from one request to the next.
    canon: dict[str, str] = {}
    winner: dict[tuple, dict] = {}
    for n in sorted(auth, key=lambda x: (bool(x.get("is_synthetic")), x["id"])):
        key = (n["node_type"], n.get("label"))
        keep = winner.setdefault(key, n)
        canon[n["id"]] = keep["id"]
        if keep is n:
            continue
        # Fold the loser in: carry its degree across so the focus pick sees one
        # hub rather than two half-hubs, and mark the result generated if ANY
        # part of it was — never the other way round.
        if keep.get("degree") is not None and n.get("degree") is not None:
            keep["degree"] = keep["degree"] + n["degree"]
        keep["is_synthetic"] = bool(keep.get("is_synthetic")) or \
            bool(n.get("is_synthetic"))

    if all(k == v for k, v in canon.items()):
        return nodes, edges

    survivors = {n["id"] for n in winner.values()}
    kept = [n for n in nodes
            if n.get("node_type") != AUTHORITY or n["id"] in survivors]

    out_edges: list[dict] = []
    seen: set[tuple] = set()
    for e in edges:
        src = canon.get(e["source"], e["source"])
        dst = canon.get(e["target"], e["target"])
        # Two designations that pointed at the two authority ids become one
        # edge between the same pair; keep the first and drop the duplicate.
        key = (e["edge_type"], src, dst, e.get("t_start"))
        if key in seen:
            continue
        seen.add(key)
        out_edges.append({**e, "source": src, "target": dst})
    return kept, out_edges


def _identity_kind(edge) -> str | None:
    """The identifier kind an `identified-as` edge asserts, or None.

    The populator stamps `props["kind"]` on every identity edge it writes
    (`graph/from_landed._add_key_identities` and `add_identities`), drawn from
    the closed `schemas.keys.IDENTITY_KINDS` vocabulary. Reading it back is
    what lets the graph say "MMSI" or "IMO" where it used to say "identified
    as" for all of them.

    Gated on the closed vocabulary rather than passed through raw: an unknown
    spelling reaching the canvas as an edge label would be a silent way for the
    populator and the UI to drift apart, which is the drift ADR-022 exists to
    prevent. An unrecognised kind falls back to the generic label instead.
    """
    if getattr(edge, "edge_type", None) != "identified-as":
        return None
    kind = (getattr(edge, "props", None) or {}).get("kind")
    return kind if kind in IDENTITY_KINDS else None


def _node_label(props: dict, node_id: str) -> str:
    for key in ("name", "code", "port_id", "mmsi", "imo"):
        if props.get(key):
            return str(props[key])
    # fall back to the last id segment, e.g. vessel:gfw:spine -> spine
    return node_id.rsplit(":", 1)[-1]


#: Node types worth expanding THROUGH. A flag or a port is shared by hundreds of
#: unrelated vessels, so pulling in everything that touches it drowns the signal
#: (STATE.md: real vessel-to-vessel paths run through shared flags and carry no
#: meaning). Vessels and companies are the hubs whose links are worth following;
#: flags, ports, identities, gaps and authorities are shown as leaves — the
#: seed's connection to them is context, their OTHER vessels are noise.
_EXPANDABLE_TYPES = {"vessel", "organization", "person"}


def neighbourhood(vessel_id: str, hops: int = 1) -> dict | None:
    """Seed-and-expand BFS from a vessel, cycle-protected and budget-bounded.

    Returns None if the seed is not in the graph. `hops` is clamped to 1-2: the
    view opens on one hop and expands a hop at a time, and going deeper than two
    turns even the synthetic neighbourhoods into a hairball.

    The traversal only continues THROUGH vessels and companies (see
    `_EXPANDABLE_TYPES`); flags, ports, identities and gaps are terminal, so the
    view shows a vessel's ownership cluster rather than every hull that happens
    to share its flag.
    """
    hops = max(1, min(2, hops))
    with open_graph() as g:
        if g is None or g.node(vessel_id) is None:
            return None

        budget = TRAVERSAL_MAX_NODES
        seen_nodes: dict[str, dict] = {}
        seen_edges: dict[tuple, dict] = {}
        truncated = False

        def add_node(nid: str) -> bool:
            if nid in seen_nodes:
                return True
            if len(seen_nodes) >= budget:
                return False
            n = g.node(nid) or {"node_type": "unknown", "props": {},
                                "is_synthetic": False}
            seen_nodes[nid] = {
                "id": nid,
                "node_type": n["node_type"],
                "label": _node_label(n["props"], nid),
                "is_synthetic": bool(n.get("is_synthetic")),
                "props": n["props"],
            }
            return True

        add_node(vessel_id)
        frontier = deque([(vessel_id, 0)])
        visited = {vessel_id}
        while frontier:
            node, depth = frontier.popleft()
            if depth >= hops:
                continue
            # both directions — a vessel points OUT to its flag/port/identity,
            # but sanctions/ownership can point IN to it.
            edges = (g.edges(node, direction="out")
                     + g.edges(node, direction="in"))
            for e in edges:
                other = e.dst if e.src == node else e.src
                if len(seen_nodes) >= budget and other not in seen_nodes:
                    truncated = True
                    continue
                if not add_node(e.src) or not add_node(e.dst):
                    truncated = True
                    continue
                key = (e.edge_type, e.src, e.dst, e.t_start)
                if key not in seen_edges:
                    seen_edges[key] = {
                        "source": e.src, "target": e.dst,
                        "edge_type": e.edge_type,
                        "confidence": round(e.base_confidence, 3),
                        "t_start": _iso(e.t_start), "t_end": _iso(e.t_end),
                        "is_synthetic": bool(e.is_synthetic),
                        "identity_kind": _identity_kind(e),
                    }
                # Only keep expanding through hubs (vessels, companies). A leaf
                # type is added and shown, but we do not pull in its other
                # vessels — that is the flag/port fan-out that made the graph
                # unreadable.
                if other not in visited:
                    visited.add(other)
                    onode = seen_nodes.get(other)
                    if onode and onode["node_type"] in _EXPANDABLE_TYPES:
                        frontier.append((other, depth + 1))

    nodes, edges = merge_duplicate_authorities(
        list(seen_nodes.values()), list(seen_edges.values()))
    return {
        "seed": vessel_id,
        "hops": hops,
        "truncated": truncated,
        "budget": budget,
        "nodes": nodes,
        "edges": edges,
    }


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def _attr(hop: dict, props: dict) -> dict:
    """`origin` and `derivation` for one evidence hop.

    `source_ref` is where the derived cases are recognised — `events` and
    `ownership_chains` are internal storage, not places anyone outside could
    look — so it is read off the hop or its props before falling back to the
    source id alone.
    """
    return describe_attribution({
        "source_id": hop.get("source"),
        "source_ref": hop.get("source_ref") or props.get("source_ref"),
    })


def _evidence_hops(evidence: list) -> list[dict]:
    hops = []
    for h in evidence or []:
        if not isinstance(h, dict):
            hops.append({"detail": str(h), "props": {}})
            continue
        props = h.get("props", {}) if isinstance(h.get("props"), dict) else {}
        attr = _attr(h, props)
        hops.append({
            "edge": h.get("edge") or h.get("edge_type"),
            "src": h.get("src"),
            "dst": h.get("dst"),
            "confidence": h.get("confidence"),
            "t_start": _iso(h["t_start"]) if isinstance(h.get("t_start"), (int, float)) else h.get("t_start"),
            "t_end": _iso(h["t_end"]) if isinstance(h.get("t_end"), (int, float)) else h.get("t_end"),
            "source": h.get("source"),
            # The same attribution the assistant's evidence carries, for the
            # same reason. `source` is a machine id — `identity_rules`,
            # `pans_resolver` — and an alert card printing it raw asks an
            # operator to trust a module name. `origin` is the answer to "and
            # who says that", which is the question they are actually asking
            # when they read an accusation.
            "origin": attr["origin"],
            # And what this system then did to those facts. `origin` alone
            # attributes a derived claim to the feed it was read from, which is
            # the source asserting something it never said — the second half of
            # the split ADR-038 draws.
            "derivation": attr["derivation"],
            "detail": h.get("detail") or props.get("detail"),
            "props": props,
        })
    return hops


def list_alerts(is_synthetic: bool | None = None,
                disposition: str | None = None) -> list[dict]:
    with open_graph() as g:
        if g is None:
            return []
        rows = g.alerts(is_synthetic=is_synthetic, disposition=disposition)
        out = []
        for a in rows:
            subj = a["subject"]
            node = g.node(subj)
            name = (node["props"].get("name") if node else None)
            out.append({
                "id": a["alert_id"],
                "rule": a["rule"],
                "anomaly_type": a["anomaly_type"],
                "subject": subj,
                "subject_name": name,
                "ts": _iso(a["ts"]),
                "confidence": a["confidence"],
                "score": a["score"],
                "disposition": a["disposition"],
                "evidence": _evidence_hops(a["evidence"]),
                "is_synthetic": bool(a["is_synthetic"]),
            })
        # highest signal first: confidence desc, but open before disposed so the
        # queue leads with what still needs a human.
        out.sort(key=lambda a: ((a["disposition"] != "open"),
                                -(a["confidence"] or 0)))
        return out


def get_alert(alert_id: str) -> dict | None:
    for a in list_alerts():
        if a["id"] == alert_id:
            return a
    return None


def dispose_alert(alert_id: str, label: str) -> bool:
    """Persist an analyst verdict. Returns False if the alert does not exist."""
    with open_graph() as g:
        if g is None:
            return False
        try:
            g.dispose(alert_id, label)
        except ValueError:
            return False
    # a disposition changes risk (confirmed alerts count full), so drop the cache
    _risk_cache["mtime"] = None
    return True
