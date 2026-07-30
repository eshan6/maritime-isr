"""Populate the graph from landed data, then measure whether it is worth looking at.

This answers one question and refuses to answer it optimistically: **is there
enough structure in the free data to justify building a graph UI?** A graph with
30,000 nodes and no path longer than one hop is a table with extra steps, and
finding that out now is much cheaper than finding it out after a frontend exists.

    python tools/graph_report.py            # populate, then report
    python tools/graph_report.py --report-only
    python tools/graph_report.py --all-gaps     # include gaps GFW did not flag

Every number printed is a count over real landed rows. Nothing here is
synthetic, and nothing here was detected by us — see the ATTRIBUTION block the
report ends with.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from maritime_isr.config import cfg  # noqa: E402
from maritime_isr.graph import GraphStore  # noqa: E402
from maritime_isr.graph import from_landed as fl  # noqa: E402
from maritime_isr.ingest.landing import read_table  # noqa: E402
from maritime_isr.ingest.sanctions_match import MATCH_TABLE  # noqa: E402

RULE = "=" * 88


def _adjacency(store) -> tuple[dict[str, set[str]], Counter]:
    """Undirected vessel-to-vessel adjacency, plus an edge-type census.

    Neighbourhood size is measured on the **vessel-to-vessel** graph only.
    Counting a shared port as adjacency would make every vessel that visited
    Mumbai a neighbour of every other, which is true and useless — the question
    is whether vessels connect to *each other*.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    census: Counter = Counter()
    for etype, src, dst in store._con.execute(
            "SELECT DISTINCT edge_type, src, dst FROM edges"):
        census[etype] += 1
        if etype == "met-with":
            adj[src].add(dst)
            adj[dst].add(src)
    return adj, census


def _hop_sizes(adj: dict[str, set[str]], node: str) -> tuple[int, int]:
    one = adj.get(node, set())
    two = set()
    for n in one:
        two |= adj.get(n, set())
    two -= one
    two.discard(node)
    return len(one), len(two)


def _dist(values: list[int]) -> str:
    if not values:
        return "no values"
    values = sorted(values)
    return (f"min {values[0]}  median {int(statistics.median(values))}  "
            f"mean {statistics.mean(values):.1f}  max {values[-1]}")


def report(store, *, at: float) -> None:
    # ---- node and edge census -------------------------------------------
    print("\n" + RULE)
    print("NODES BY TYPE")
    print(RULE)
    rows = store._con.execute(
        "SELECT node_type, COUNT(*) FROM nodes GROUP BY node_type "
        "ORDER BY COUNT(*) DESC").fetchall()
    for t, n in rows:
        print(f"  {t:<24} {n:>8,}")
    print(f"  {'TOTAL':<24} {sum(n for _, n in rows):>8,}")

    print("\n" + RULE)
    print("EDGES BY TYPE  (distinct triples; the store also keeps every re-assertion)")
    print(RULE)
    _, census = _adjacency(store)
    for t, n in census.most_common():
        print(f"  {t:<24} {n:>8,}")
    print(f"  {'TOTAL':<24} {sum(census.values()):>8,}")
    print(f"  {'rows incl. history':<24} {store.n_edges():>8,}")

    # ---- decay -----------------------------------------------------------
    print("\n" + RULE)
    print("CONFIDENCE AFTER DECAY  (evaluated now, on real observation times)")
    print(RULE)
    print("  State edges rot without re-observation; event edges hold their base")
    print("  confidence forever. 'unusable' means below 0.5 — a stated bar, not a")
    print("  law of nature.\n")
    print(f"  {'edge type':<24} {'n':>7} {'half-life':>10} {'mean conf':>10} "
          f"{'unusable':>10}")
    print("  " + "-" * 76)
    summary = fl.decay_summary(store, at=at)
    total_decayed = 0
    for etype, d in sorted(summary.items(), key=lambda kv: -kv[1]["n"]):
        hl = f"{d['half_life_days']:.0f}d" if d["half_life_days"] else "none"
        total_decayed += d["below_usable"]
        print(f"  {etype:<24} {d['n']:>7,} {hl:>10} {d['mean_confidence']:>10.3f} "
              f"{d['below_usable']:>10,}")
    print(f"\n  {total_decayed:,} edge(s) have already decayed below 0.5.")
    if summary.get("docked-at", {}).get("below_usable"):
        print("  Nearly all of these are docked-at: a 2-day half-life over an 8-week")
        print("  window. That is the model working, not a bug — 'this ship is at this")
        print("  port' stops being true within days and the confidence says so.")

    # ---- the sanctioned population ---------------------------------------
    print("\n" + RULE)
    print("CONNECTIVITY OF THE SANCTIONS-MATCHED VESSELS")
    print(RULE)
    print("  This is the number that decides whether a graph UI is worth building.")
    print("  A matched vessel with no encounter edge has nothing to show in a graph")
    print("  view; it is a row in a table.\n")

    adj, _ = _adjacency(store)
    matches = read_table(MATCH_TABLE)
    if not matches:
        print("  No matches landed. Run: maritime-isr ingest sanctions-match")
        return

    by_tier: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        by_tier[m["match_tier"]].add(fl.vessel_node_id(m["vessel_id"]))
    imo_nodes = by_tier.get("imo", set())
    all_nodes = set().union(*by_tier.values()) if by_tier else set()

    for label, nodes in (("IMO-matched (findings)", imo_nodes),
                         ("all matched (any tier)", all_nodes)):
        if not nodes:
            continue
        deg = {n: len(adj.get(n, set())) for n in nodes}
        ge1 = sum(1 for v in deg.values() if v >= 1)
        ge3 = sum(1 for v in deg.values() if v >= 3)
        print(f"  {label}: {len(nodes)} vessel(s)")
        print(f"      with >= 1 encounter edge : {ge1:>4}  ({ge1 / len(nodes):.0%})")
        print(f"      with >= 3 encounter edges: {ge3:>4}  ({ge3 / len(nodes):.0%})")
        print(f"      degree distribution      : {_dist(list(deg.values()))}")

    # ---- neighbourhood sizes ---------------------------------------------
    print("\n" + RULE)
    print("NEIGHBOURHOOD SIZE  (vessel-to-vessel, via met-with)")
    print(RULE)
    ones, twos = [], []
    for n in all_nodes:
        a, b = _hop_sizes(adj, n)
        ones.append(a)
        twos.append(b)
    print(f"  1-hop over matched vessels : {_dist(ones)}")
    print(f"  2-hop over matched vessels : {_dist(twos)}")
    if all_nodes:
        isolated = sum(1 for v in ones if v == 0)
        print(f"  {isolated} of {len(all_nodes)} matched vessels have NO encounter "
              f"neighbour at all ({isolated / len(all_nodes):.0%}).")

    # ---- the densest one, named ------------------------------------------
    print("\n" + RULE)
    print("DENSEST SANCTIONED NEIGHBOURHOOD")
    print(RULE)
    best, best_score = None, (-1, -1)
    for n in all_nodes:
        a, b = _hop_sizes(adj, n)
        if (a, b) > best_score:
            best, best_score = n, (a, b)
    if best is None or best_score[0] == 0:
        print("  None. No sanctions-matched vessel has a single encounter edge in")
        print("  the landed window. That is a real result: it says the free event")
        print("  data does not connect this population to anything, and a graph UI")
        print("  would have nothing to draw. Report it as-is.")
    else:
        node = store.node(best) or {}
        props = node.get("props", {})
        one, two = best_score
        print(f"  node   : {best}")
        print(f"  name   : {props.get('name')}")
        print(f"  imo    : {props.get('imo')}    mmsi: {props.get('mmsi')}    "
              f"flag: {props.get('flag')}")
        print(f"  1-hop  : {one} vessel(s)      2-hop: {two} vessel(s)")
        sanc = [e for e in store.edges(best, "sanctioned-under")]
        for e in sanc:
            print(f"  OFAC   : {e.props.get('ofac_name')} "
                  f"[{e.props.get('ofac_program')}] tier={e.props.get('match_tier')} "
                  f"conf={e.base_confidence}")
        print("\n  Neighbours:")
        for nb in sorted(adj.get(best, set())):
            nbp = (store.node(nb) or {}).get("props", {})
            mark = "  <-- also sanctions-matched" if nb in all_nodes else ""
            print(f"    {str(nbp.get('name'))[:30]:<32} {nb}{mark}")
        print("\n  This is the candidate demo vessel. It is a candidate because it")
        print("  is the most connected, not because it is the most suspicious.")

    print("\n" + RULE)
    print("ATTRIBUTION — carry this wording forward")
    print(RULE)
    print("  Every vessel, encounter, port visit and AIS gap above was detected and")
    print("  assessed by Global Fishing Watch. Every sanctions listing is OFAC's.")
    print("  Our contribution is the identity match between the two, and the graph")
    print("  structure built over them. We have run no SAR, detected no vessel, and")
    print("  observed nothing going dark.")
    print(RULE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true",
                    help="skip population, report the graph as it stands")
    ap.add_argument("--all-gaps", action="store_true",
                    help="include AIS gaps GFW did NOT flag as intentional")
    ap.add_argument("--db", default=None, help="graph db path override")
    args = ap.parse_args(argv)

    print(RULE)
    print("Maritime ISR — real-data graph population and connectivity report")
    print(RULE)
    print(f"data root : {cfg.data_root.resolve()}")

    store = GraphStore(args.db)
    print(f"graph db  : {store.db_path}")
    try:
        if not args.report_only:
            t0 = time.time()
            counts = fl.populate(store, only_intentional_gaps=not args.all_gaps)
            print(f"\npopulated in {time.time() - t0:.1f}s")
            print(RULE)
            print("WHAT WAS WRITTEN  (landed rows in, edges out)")
            print(RULE)
            for k, v in counts.items():
                print(f"  {k:<28} {v:>10,}")
            if counts.get("edges_total", 0) == 0:
                print("\n  Nothing landed. Run the connectors first — see README.")
                return 1
        report(store, at=time.time())
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
