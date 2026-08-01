"""Run the EXISTING pipeline over the combined corpus and measure the result.

    python tools/run_scenario_pipeline.py

**Nothing in this script is a scenario-aware code path.** It calls the same
track engine, the same graph populator, the same anomaly library and the same
risk scorer that run on real data, over whatever is in the landed tables. That
is the point of ADR-019: if scenario rows took a special route, a green run here
would only prove the special route works.

The only scenario-aware step is the last one, `measure`, which reads
`scenario_truth` **after** the pipeline has finished and compares alerts against
what each scenario actually was. No detector sees it.

*Success* looks like a detection table with an outcome per scenario and a
precision/recall figure. *Failure* looks like `no landed ais_position data` —
run `maritime-isr scenario generate` first.

Every count is printed split real versus synthetic. There is no combined total
anywhere in the output, deliberately.
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd                                             # noqa: E402

from maritime_isr.config import DATA_ROOT, GRAPH_DB_NAME        # noqa: E402
from maritime_isr.graph import GraphStore                       # noqa: E402
from maritime_isr.graph import from_landed                      # noqa: E402
from maritime_isr.ingest.landing import (read_table,            # noqa: E402
                                         split_real_synthetic)
from maritime_isr.scenario.measure import (format_measurement,  # noqa: E402
                                           measure)


def _hdr(title: str) -> None:
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def load_positions() -> pd.DataFrame:
    rows = read_table("ais_position")
    if not rows:
        print("no landed ais_position data — run "
              "`python -m maritime_isr.cli scenario generate` first",
              file=sys.stderr)
        raise SystemExit(1)
    real, syn = split_real_synthetic(rows)
    print(f"  ais_position: {len(real):,} real + {len(syn):,} synthetic row(s)")
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col, default in (("sog_kn", 0.0), ("cog_deg", 0.0), ("receiver", "")):
        if col not in df.columns:
            df[col] = default
    return df.sort_values("ts").reset_index(drop=True)


def run_tracks(df: pd.DataFrame) -> dict:
    """Phase 2 over the combined corpus — the real track engine, unmodified."""
    from maritime_isr import tracks as trk
    t0 = time.time()
    out = trk.run_track_engine(
        df, source_ref="scenario-combined",
        partition_day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        aoi="arabian_sea_v1", write_outputs=False)
    kinds = Counter(g["gap_type"] for g in out["gaps"])
    print(f"  tracks       : {len(out['tracks']):,}")
    print(f"  gaps         : {len(out['gaps']):,}  {dict(kinds)}")
    print(f"  encounters   : {len(out['encounters']):,}")
    print(f"  spoof events : {len(out['spoof_events']):,}")
    print(f"  ({time.time() - t0:.0f}s)")
    return out


def run_fusion_stage(df: pd.DataFrame, tracks_out: dict) -> dict:
    """Phase 3 over the combined corpus — the real fusion core, unmodified.

    **This stage was not being run at all.** `run_anomalies` passed
    `associations=[]` and `verdicts=[]` as literals, so `detect_dark_vessel`
    iterated an empty list and `detect_dark_rendezvous` short-circuited on every
    one of 5,880 encounters. Six scenarios expected one of those two rules; none
    could fire, for reasons that had nothing to do with thresholds. The rules
    were being reported as "silent" when they had never been asked a question.

    Scenes come from `scenario_detections`, grouped by `scene_id` — the shape
    `associate_scene` expects. The registry is MMSI to length, read from landed
    identity, which is what lets association apply a length gate rather than a
    proximity gate alone.

    Nothing is written: `write_outputs=False`, because this is a measurement run
    over a corpus that already exists and publishing fusion outputs would put
    derived rows in the conformed layer that the next run would then read back.
    """
    from maritime_isr import fusion
    from maritime_isr.tracks.coverage import CoverageModel

    dets = read_table("scenario_detections")
    if not dets:
        print("  no SAR contacts landed — dark_vessel and dark_rendezvous have "
              "nothing to work from, and their silence says nothing about them")
        return dict(associations=[], verdicts=[], statics=[])

    by_scene: dict[str, list[dict]] = {}
    for d in dets:
        by_scene.setdefault(str(d.get("scene_id") or "unknown"), []).append(d)
    scenes = [
        dict(scene_id=sid,
             ts=pd.Timestamp(rows[0]["ts"]),
             detections=[dict(detection_id=r["detection_id"], lat=r["lat"],
                              lon=r["lon"], length_m=r.get("length_m"),
                              score=r.get("score", 0.9)) for r in rows])
        for sid, rows in sorted(by_scene.items())]

    # MMSI -> length. Association uses it to reject a contact whose radar
    # length cannot belong to the candidate hull, which is the difference
    # between a match and a coincidence of position.
    registry: dict[int, float] = {}
    for r in read_table("gfw_vessel_identity"):
        mmsi, length = r.get("mmsi"), r.get("length_m")
        if mmsi in (None, "") or length in (None, ""):
            continue
        try:
            registry[int(float(mmsi))] = float(length)
        except (TypeError, ValueError):
            continue

    model = CoverageModel(df["ts"].min().timestamp()).fit(df)
    t0 = time.time()
    out = fusion.run_fusion(
        scenes, tracks_out["tracks"], model, registry,
        source_ref="scenario-combined",
        partition_day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        aoi="arabian_sea_v1", gaps=tracks_out["gaps"],
        spoof_events=tracks_out["spoof_events"], write_outputs=False)

    assoc = out.get("associations", [])
    verdicts = out.get("verdicts", [])
    statics = out.get("statics", [])
    matched = sum(1 for a in assoc if a["status"] != "unmatched")
    print(f"  scenes           : {len(scenes):,} "
          f"({sum(len(s['detections']) for s in scenes):,} contact(s))")
    print(f"  registry entries : {len(registry):,} MMSI -> length")
    print(f"  associations     : {len(assoc):,}  "
          f"({matched:,} matched, {len(assoc) - matched:,} unmatched)")
    print(f"  static objects   : {len(statics):,}")
    print(f"  dark verdicts    : {len(verdicts):,}  "
          f"{dict(Counter(v['status'] for v in verdicts))}")
    print(f"  ({time.time() - t0:.0f}s)")
    return dict(associations=assoc, verdicts=verdicts, statics=statics)


def populate_graph() -> tuple[GraphStore, dict]:
    """Phase 4 over the combined corpus — `from_landed.populate`, unmodified."""
    store = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    t0 = time.time()
    counts = from_landed.populate(store, only_intentional_gaps=False)
    print(f"  populated in {time.time() - t0:.0f}s")
    for k, v in sorted(counts.items()):
        print(f"    {k:<28}{v:>10,}")
    return store, counts


def report_graph_split(store: GraphStore) -> dict:
    split = store.counts_by_synthetic()
    print(f"  {'':<24}{'real':>12}{'synthetic':>12}")
    for k in ("nodes", "edges", "edges_current", "alerts"):
        d = split[k]
        print(f"  {k:<24}{d['real']:>12,}{d['synthetic']:>12,}")

    print()
    print("  node types (synthetic):")
    for t, n in sorted(store.n_nodes_by_type(is_synthetic=True).items()):
        print(f"    {t:<24}{n:>10,}")
    print("  edge types (synthetic):")
    for t, n in sorted(store.n_edges_by_type(is_synthetic=True).items()):
        print(f"    {t:<24}{n:>10,}")
    real_edges = store.n_edges_by_type(is_synthetic=False)
    if real_edges:
        print("  edge types (real):")
        for t, n in sorted(real_edges.items()):
            print(f"    {t:<24}{n:>10,}")
    return split


def connectivity(store: GraphStore) -> None:
    """The number the whole exercise exists to move: 0 of 98 on real data.

    On the real corpus, none of the 98 OFAC-matched vessels had a single
    encounter edge, so there was no network to traverse and STATE.md concluded
    a graph UI had nothing to draw. This recomputes the same measurement over
    the combined corpus and reports it split, so the real finding is not
    quietly improved by scenario data sitting in the same table.
    """
    matched = {"real": set(), "synthetic": set()}
    for r in read_table("sanctioned_vessel_matches"):
        vid = r.get("vessel_id")
        if not vid:
            continue
        bucket = "synthetic" if r.get("is_synthetic") else "real"
        if r.get("is_finding"):
            matched[bucket].add(from_landed.vessel_node_id(vid)
                                if not str(vid).startswith("vessel:") else vid)

    for bucket in ("real", "synthetic"):
        vessels = matched[bucket]
        if not vessels:
            print(f"  {bucket:<10}: no sanctions-matched vessels")
            continue
        with_enc = 0
        with_3 = 0
        for v in vessels:
            n = len([e for e in store.edges(v, "met-with", direction="out")]
                    + [e for e in store.edges(v, "met-with", direction="in")])
            if n >= 1:
                with_enc += 1
            if n >= 3:
                with_3 += 1
        pct = 100.0 * with_enc / len(vessels)
        print(f"  {bucket:<10}: {len(vessels)} matched vessel(s), "
              f"{with_enc} with >=1 encounter edge ({pct:.0f}%), "
              f"{with_3} with >=3")


def run_anomalies(store: GraphStore, tracks_out: dict,
                  fusion_out: dict) -> dict:
    """Phase 5 over the combined corpus — the real anomaly library.

    `associations` and `verdicts` now come from the fusion stage rather than
    being passed as empty literals. Two of the six detectors read nothing else.
    """
    from maritime_isr.anomaly.library import run_anomaly_library
    t0 = time.time()
    fired = run_anomaly_library(
        store,
        tracks=tracks_out["tracks"],
        encounters=tracks_out["encounters"],
        spoof_events=tracks_out["spoof_events"],
        associations=fusion_out["associations"],
        verdicts=fusion_out["verdicts"],
        source_ref="scenario-combined")
    print(f"  ran in {time.time() - t0:.0f}s")
    for atype, ids in sorted(fired.items()):
        print(f"    {atype:<26}{len(ids):>6} alert(s)")
    return fired


def main() -> int:
    _hdr("1. landed corpus")
    df = load_positions()

    _hdr("2. Phase 2 — track engine over the combined corpus")
    tracks_out = run_tracks(df)

    _hdr("3. Phase 3 — fusion core over the combined corpus")
    fusion_out = run_fusion_stage(df, tracks_out)

    _hdr("4. Phase 4 — graph populated from the landed tables")
    store, _ = populate_graph()

    _hdr("5. graph, split real vs synthetic")
    report_graph_split(store)

    _hdr("6. connectivity of the sanctions-matched population")
    print("  Real data alone gave 0 of 98 with any encounter edge (2026-07-30).")
    connectivity(store)

    _hdr("7. Phase 5 — anomaly library")
    run_anomalies(store, tracks_out, fusion_out)

    _hdr("8. decay over the combined graph")
    dec = from_landed.decay_summary(store)
    print(f"  {'edge type':<24}{'n':>8}{'below usable':>14}{'mean conf':>12}")
    for etype, d in sorted(dec.items()):
        print(f"  {etype:<24}{d['n']:>8,}{d['below_usable']:>14,}"
              f"{d['mean_confidence']:>12.3f}")

    _hdr("9. risk scoring")
    from maritime_isr.anomaly import rank_vessels
    ranked = rank_vessels(store, top=12)
    for r in ranked:
        node = store.node(r["vessel"]) or {}
        props = node.get("props", {})
        tag = "SYN" if node and props.get("gfw_vessel_id", "").startswith(
            "vessel:") else "   "
        print(f"  {r['risk_score']:.3f}  {tag}  {r['vessel']}")
    if not ranked:
        print("  (no vessels scored)")

    print(format_measurement(measure(store), store=store))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
