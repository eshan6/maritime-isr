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
from typing import Iterator

from ..config import GRAPH_DB_NAME, TRAVERSAL_MAX_NODES, cfg
from ..graph import GraphStore

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

def _node_label(props: dict, node_id: str) -> str:
    for key in ("name", "code", "port_id", "mmsi", "imo"):
        if props.get(key):
            return str(props[key])
    # fall back to the last id segment, e.g. vessel:gfw:spine -> spine
    return node_id.rsplit(":", 1)[-1]


def neighbourhood(vessel_id: str, hops: int = 1) -> dict | None:
    """Seed-and-expand BFS from a vessel, cycle-protected and budget-bounded.

    Returns None if the seed is not in the graph. `hops` is clamped to 1-2: the
    view opens on one hop and expands a hop at a time, and going deeper than two
    turns even the synthetic neighbourhoods into a hairball.
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
                    }
                if other not in visited:
                    visited.add(other)
                    frontier.append((other, depth + 1))

    return {
        "seed": vessel_id,
        "hops": hops,
        "truncated": truncated,
        "budget": budget,
        "nodes": list(seen_nodes.values()),
        "edges": list(seen_edges.values()),
    }


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def _evidence_hops(evidence: list) -> list[dict]:
    hops = []
    for h in evidence or []:
        if not isinstance(h, dict):
            hops.append({"detail": str(h), "props": {}})
            continue
        props = h.get("props", {}) if isinstance(h.get("props"), dict) else {}
        hops.append({
            "edge": h.get("edge") or h.get("edge_type"),
            "src": h.get("src"),
            "dst": h.get("dst"),
            "confidence": h.get("confidence"),
            "t_start": _iso(h["t_start"]) if isinstance(h.get("t_start"), (int, float)) else h.get("t_start"),
            "t_end": _iso(h["t_end"]) if isinstance(h.get("t_end"), (int, float)) else h.get("t_end"),
            "source": h.get("source"),
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
