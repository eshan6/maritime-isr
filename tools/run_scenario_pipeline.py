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

from maritime_isr.config import GRAPH_DB_NAME, cfg             # noqa: E402
from maritime_isr.graph import GraphStore                       # noqa: E402
from maritime_isr.graph import from_landed                      # noqa: E402
from maritime_isr.ingest.landing import (SYNTHETIC_SOURCE_ID,     # noqa: E402
                                         read_table,
                                         split_real_synthetic)
from maritime_isr.scenario.measure import (format_measurement,  # noqa: E402
                                           measure)
from maritime_isr.scenario.measure_radar import (  # noqa: E402
    format_radar_measurement, measure_radar)


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


def run_radar_stage(tracks_out: dict) -> dict:
    """Coastal radar: the same track engine, the same fusion core (ADR-028).

    Nothing here is a radar-specific pipeline. `build_tracks` is called with the
    RADAR source descriptor instead of the AIS one; `correlate_radar` slices the
    picture into epochs and hands each to `associate_scene`, unmodified; the
    survivors go through `dark_cascade`, unmodified, with the sensor's own
    parameters. If any of that had needed a fork, the connector claim in
    CLAUDE.md §4.5 would be false and this stage would be the proof.

    The coverage model handed to the cascade is the one fitted on **AIS**. That
    is not an oversight: the question the cascade asks is "would we have heard a
    transmitter here", and a model fitted on radar answers "does our radar reach
    here" — same shape, different question, and confusing them would let the
    radar's own coverage vouch for the AIS network's.
    """
    from maritime_isr.fusion.radar_ais import correlate_radar, format_correlation
    from maritime_isr.schemas.sources import RADAR
    from maritime_isr.tracks import build_tracks

    from maritime_isr.tracks.features import detect_encounters

    rows = read_table("radar_track_report")
    if not rows:
        print("  no landed radar_track_report data — the radar picture has not "
              "been generated, and the dark-contact path has nothing to run on")
        return dict(correlations=[], verdicts=[], statics=[], associations=[],
                    radar_tracks=[], landed={}, encounters=[])
    real, syn = split_real_synthetic(rows)
    print(f"  radar_track_report: {len(real):,} real + {len(syn):,} synthetic plot(s)")

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)

    t0 = time.time()
    radar_tracks, radar_spoofs = build_tracks(df, source=RADAR)
    print(f"  radar tracks : {len(radar_tracks):,} "
          f"({df['radar_track_id'].nunique():,} station track number(s))")
    if radar_spoofs:                       # must be empty — see ADR-028
        print(f"  WARNING: {len(radar_spoofs)} identity spoof event(s) from a "
              f"sensor that observes no identity — this is a bug")

    registry: dict[int, float] = {}
    for r in read_table("gfw_vessel_identity"):
        mmsi, length = r.get("mmsi"), r.get("length_m")
        if mmsi in (None, "") or length in (None, ""):
            continue
        try:
            registry[int(float(mmsi))] = float(length)
        except (TypeError, ValueError):
            continue

    out = correlate_radar(radar_tracks, tracks_out["tracks"],
                          tracks_out["coverage_model"], registry,
                          spoof_events=tracks_out["spoof_events"],
                          ais_gaps=tracks_out["gaps"])
    print(f"  ({time.time() - t0:.0f}s)")
    print(format_correlation(out))

    # **Land what this stage just computed.**
    #
    # It used to print the correlation and drop it. Everything else in the
    # pipeline lands its output, so the omission was invisible — the stage
    # reported real numbers and looked complete — while the Radar view, which
    # reads `radar_dark_contact` off disk, kept saying "0 contacts survived the
    # cascade" after a full successful run. The only way to fill it was
    # `radar correlate --write`, a separate command that recomputes for minutes
    # exactly what is already in memory here.
    #
    # An operator followed the documented sequence, got an empty Radar tab, and
    # reasonably concluded the feature was broken. A stage that does the work
    # and discards it is not a smaller version of doing the work.
    from maritime_isr.fusion.radar_ais import land_correlation

    is_syn = bool(syn) and not real
    landed = land_correlation(
        out,
        source_id=SYNTHETIC_SOURCE_ID if is_syn else "coastal_radar",
        is_synthetic=is_syn)
    for table, n in sorted(landed.items()):
        print(f"  landed {n:,} row(s) into {table}")

    return dict(correlations=out.correlations, verdicts=out.verdicts,
                statics=out.statics, associations=out.associations,
                radar_tracks=radar_tracks, landed=landed,
                encounters=detect_encounters(radar_tracks))


def run_radar_behaviour(store, radar_tracks: list) -> dict:
    """Every behavioural detector, over radar-sourced tracks.

    This is the architectural claim under test, and it is the reason the stage
    exists separately from the alert run: the anomaly library was written
    against AIS tracks and had never seen a track without an identity. What it
    must NOT do is crash, invent a hull, or silently produce nothing.

    Alerts land on `contact:radar:<station>:<n>` nodes rather than on vessels,
    because that is what is known — see `graph.identity.track_subject_id`.
    """
    from collections import Counter as _C

    from maritime_isr.anomaly.library import (detect_port_risk,
                                              detect_sensitive_loitering)
    from maritime_isr.tracks.features import detect_encounters, extract_features

    if not radar_tracks:
        print("  (no radar tracks)")
        return {}
    feats = [extract_features(tr) for tr in radar_tracks]
    n_loiter = sum(f["n_loiter_episodes"] for f in feats)
    n_calls = sum(len(f["port_calls"]) for f in feats)
    encounters = detect_encounters(radar_tracks)
    print(f"  extract_features   : {len(feats):,} track(s), "
          f"{n_loiter:,} loiter episode(s), {n_calls:,} port call(s)")
    print(f"  detect_encounters  : {len(encounters):,} radar-only encounter(s)")

    loiter = detect_sensitive_loitering(store, radar_tracks,
                                        source_ref="radar-combined")
    risk = detect_port_risk(store, radar_tracks, source_ref="radar-combined")
    print(f"  loitering_sensitive: {len(loiter):,} alert(s)")
    print(f"  port_risk_propagat.: {len(risk):,} alert(s)")
    subjects = _C(a["subject"].split(":")[0] for a in store.alerts()
                  if a["subject"].startswith("contact:"))
    print(f"  alert subjects that are contacts (not hulls): {dict(subjects)}")
    return dict(features=feats, encounters=encounters,
                loitering=loiter, port_risk=risk)


def run_zone_stage(all_tracks: list) -> dict:
    """Phase 2.5 — the maritime zone layer (ADR-030).

    Transitions are computed over **every** track, AIS and radar alike: a zone
    crossing is a fact about a position history, and a radar contact nobody can
    name crossing into territorial waters is exactly as interesting as a named
    hull doing it. The transitions land like any other event.
    """
    from maritime_isr.ports import gazetteer_recall
    from maritime_isr.zones import (STATUTORY_KINDS, ZoneIndex,
                                    anchoring_analysis_status, load_zones)
    from maritime_isr.zones.transitions import (land_transitions,
                                                transitions_for_tracks)

    zones = load_zones()
    if not zones:
        print("  no landed zone layer — run `maritime-isr zones build`")
        return {}
    index = ZoneIndex(zones)
    from collections import Counter as _C
    print(f"  zone layer         : {len(zones)} zone(s) "
          f"{dict(sorted(_C(z.kind for z in zones).items()))}")

    missing = sorted(STATUTORY_KINDS - {z.kind for z in zones})
    if missing:
        print(f"  NOT LOADED         : {', '.join(missing)} — statutory limits, "
              f"not derived on purpose (zones/derive.py)")

    t0 = time.time()
    trans = transitions_for_tracks(all_tracks, index)
    print(f"  transitions        : {len(trans):,} in {time.time() - t0:.0f}s "
          f"over {len(all_tracks):,} track(s)")
    by_kind = _C(r["zone_kind"] for r in trans)
    for kind, n in sorted(by_kind.items()):
        print(f"    {kind:<20}{n:>8,}")
    censored = sum(1 for r in trans if r["entry_censored"])
    print(f"    {'entry censored':<20}{censored:>8,}  (already inside when the "
          f"track began — the entry position is where we picked them up)")

    written = land_transitions(trans, source_id=SYNTHETIC_SOURCE_ID,
                               is_synthetic=True)
    for table, n in sorted(written.items()):
        print(f"  landed {n:,} row(s) into {table}")

    # --- the gazetteer gap, measured on this corpus rather than asserted ----
    #
    # **The unit is a stopped vessel-hour, not a loiter episode.** The first
    # version of this measurement used `loiter_episodes`, which are the strict
    # sustained-drift definition and number nine in the whole corpus — a
    # denominator that small makes any before/after figure noise. What the
    # gazetteer is actually for is naming the place a vessel has stopped, so
    # the population is every hour in which a vessel was essentially stationary.
    STOP_SOG_KN = 1.0
    stops = []
    for tr in all_tracks:
        pts = tr.points
        if hasattr(pts, "quality"):
            pts = pts[pts.quality != "outlier"]
        if "sog_kn" not in pts.columns or len(pts) == 0:
            continue
        slow = pts[pts["sog_kn"] <= STOP_SOG_KN]
        if len(slow) == 0:
            continue
        # One sample per vessel-hour, so a ship sitting still for two days does
        # not dominate the denominator with a thousand identical fixes.
        hourly = pd.to_datetime(slow["ts"], utc=True).dt.floor("h")
        seen = set()
        for h, la, lo in zip(hourly, slow["lat"], slow["lon"]):
            if h in seen:
                continue
            seen.add(h)
            stops.append((float(la), float(lo)))
    rec = gazetteer_recall(stops)
    print()
    print(f"  port gazetteer, before and after ADR-030 "
          f"({rec['n_ports_before']} -> {rec['n_ports_after']} ports):")
    print(f"    stopped vessel-hours           {rec['positions']:>8,}")
    print(f"    nameable BEFORE                {rec['named_before']:>8,}  "
          f"({(rec['recall_before'] or 0) * 100:.1f}%)")
    print(f"    nameable AFTER                 {rec['named_after']:>8,}  "
          f"({(rec['recall_after'] or 0) * 100:.1f}%)")
    print(f"    newly nameable                 {rec['gained']:>8,}")
    print(f"    nameable by neither            {rec['named_by_neither']:>8,}  "
          f"(open water — not a miss)")
    if rec["by_new_port"]:
        top = list(rec["by_new_port"].items())[:6]
        print("    biggest gains: "
              + ", ".join(f"{k} x{v}" for k, v in top))
    # **This measurement has a ceiling it cannot see past, and saying so is the
    # whole point of printing it.** The corpus is GENERATED FROM the gazetteer:
    # the scenario places vessels at ports the gazetteer knows, so a port that
    # was missing had no synthetic traffic to name and closing the gap can only
    # ever move this number by the handful of stops that happened to land near a
    # newly-added facility by coincidence. The gain on REAL traffic — where
    # ships call at Mormugao and Ratnagiri regardless of what our list contains —
    # is not observable here and must not be inferred from this figure.
    print("    NOTE: this corpus is generated FROM the gazetteer, so it cannot")
    print("          show what closing the gap is worth. Vessels are placed at")
    print("          ports the old list already knew; a port it lacked had no")
    print("          traffic to name. The directly countable fact is that 25")
    print("          real west-coast facilities were absent and now are not.")
    print("          A valid before/after needs REAL port-visit positions")
    print("          (`maritime-isr ingest gfw`), which are not landed here.")

    ok, why = anchoring_analysis_status(index)
    if not ok:
        print()
        print(f"  anchored_outside_limits: {why}")
    return dict(index=index, zones=zones, transitions=trans,
                gazetteer=rec, anchoring_ok=ok, anchoring_why=why)


def run_zone_analyses(store, all_tracks: list, zone_out: dict) -> dict:
    """The four analyses the zone layer unlocks (ADR-030).

    Area visit is a query and emits no alerts by design; the other three are
    precision-gated detectors like every other rule in the library.
    """
    from maritime_isr.zones.analyses import (
        detect_anchored_outside_port_limits, detect_area_visits,
        detect_lane_deviation, detect_maiden_visit)
    if not zone_out:
        print("  (no zone layer)")
        return {}
    index, trans = zone_out["index"], zone_out["transitions"]

    visits = detect_area_visits(trans)
    print(f"  area_visit         : {len(visits):,} presence row(s) "
          f"(a query, not an alert)")

    maiden = detect_maiden_visit(store, trans, index=index,
                                 source_ref="zones-combined")
    print(f"  maiden_zone_visit  : {len(maiden):,} alert(s)")

    lane = detect_lane_deviation(store, all_tracks, index,
                                 source_ref="zones-combined")
    print(f"  lane_deviation     : {len(lane):,} alert(s)")

    anch = detect_anchored_outside_port_limits(store, all_tracks, index,
                                               source_ref="zones-combined")
    if zone_out.get("anchoring_ok"):
        print(f"  anchored_outside_l.: {len(anch):,} alert(s)")
    else:
        # `anchoring_why` already opens with "IDLE — "; prefixing another one
        # printed "IDLE — IDLE — no territorial_sea zone is loaded".
        print(f"  anchored_outside_l.: {zone_out['anchoring_why']}")
    return dict(area_visits=visits, maiden=maiden, lane=lane, anchored=anch)


def populate_graph() -> tuple[GraphStore, dict]:
    """Phase 4 over the combined corpus — `from_landed.populate`, unmodified."""
    # cfg.data_root, not the hardcoded DATA_ROOT constant — so with
    # MISR_DATA_ROOT set the graph lands in the same directory the conformed
    # tables and the API read from. Writing to DATA_ROOT left the API's graph
    # empty (0 alerts, neighbourhood 404) whenever the two diverged.
    store = GraphStore(cfg.data_root / GRAPH_DB_NAME)
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


def _area2_inputs():
    """Current identities and the per-area baseline index (ADR-032).

    Both degrade to "absent" rather than raising: a corpus with no identity
    table or too few positions to fit a baseline still runs the original six
    detectors, and the two Area 2 ones simply stay quiet. A pipeline stage that
    could not run without an optional input would make the optional input
    mandatory by accident.
    """
    import pandas as pd

    from maritime_isr import baselines as bl
    from maritime_isr.api.reader import open_reader

    identities: list[dict] = []
    index = None
    with open_reader() as reader:
        if reader.has("gfw_vessel_identity"):
            identities = reader.rows("""
                SELECT * FROM (
                  SELECT *, row_number() OVER (
                    PARTITION BY vessel_id
                    ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST
                  ) AS _rn FROM gfw_vessel_identity) WHERE _rn = 1
            """)
        if reader.has("ais_position"):
            pos = pd.DataFrame(reader.rows(
                "SELECT vessel_id, mmsi, lat, lon, sog_kn, cog_deg, ts, "
                "is_synthetic FROM ais_position"))
            derived = bl.derive_baselines(pos)
            if derived:
                n = bl.land_baselines(derived)
                index = bl.BaselineIndex(derived)
                cov = index.coverage()
                print(f"  area baselines : {n:,} cell(s) landed, "
                      f"{cov['usable']:,} usable "
                      f"({cov['fraction_usable']:.0%} of cells at res "
                      f"{cov['res']}, floor {cov['min_observations']} obs)")
    print(f"  identities     : {len(identities):,} current row(s)")
    return identities, index


def run_anomalies(store: GraphStore, tracks_out: dict,
                  fusion_out: dict) -> dict:
    """Phase 5 over the combined corpus — the real anomaly library.

    `associations` and `verdicts` now come from the fusion stage rather than
    being passed as empty literals. Two of the six detectors read nothing else.
    """
    from maritime_isr.anomaly.library import run_anomaly_library
    # **The graph accumulates and the measurement at the end of this script
    # reads the whole store, not this run's output.** That is correct for the
    # graph — edge history is the moat and cannot be backfilled (CLAUDE.md §6) —
    # and it is a trap for measuring a rule change: re-running after tightening
    # `dark_rendezvous` reported 81 new alerts against a store still holding 667
    # from the previous run's looser version, and the final table scored both.
    # Alert ids are deterministic, so a re-run with the SAME code is idempotent
    # and this number is 0; a non-zero count after a rule change means the
    # figures below are a union of two rule sets. Say so rather than let a
    # reader assume otherwise.
    prior = len(store.alerts())
    if prior:
        print(f"  NOTE: {prior:,} alert(s) were already in the graph before this "
              f"stage.\n        If the detectors changed since they were written, "
              f"the measurement\n        at the end scores BOTH rule sets. Delete "
              f"data/graph.sqlite and re-run\n        for a clean figure.")
    # ---- Area 2 inputs (ADR-032) -----------------------------------------
    # Derived here rather than inside the library so the detectors stay pure
    # functions of what they are handed, and so the baseline artifact is landed
    # and inspectable rather than living for the duration of one call.
    identities, baselines = _area2_inputs()

    t0 = time.time()
    fired = run_anomaly_library(
        store,
        tracks=tracks_out["tracks"],
        encounters=tracks_out["encounters"],
        spoof_events=tracks_out["spoof_events"],
        associations=fusion_out["associations"],
        verdicts=fusion_out["verdicts"],
        source_ref="scenario-combined",
        identities=identities,
        baselines=baselines)
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

    _hdr("3b. coastal radar — the same core, a second sensor (ADR-028)")
    radar_out = run_radar_stage(tracks_out)

    _hdr("3c. Phase 2.5 — the maritime zone layer (ADR-030)")
    zone_tracks = [*tracks_out["tracks"], *radar_out.get("radar_tracks", [])]
    zone_out = run_zone_stage(zone_tracks)

    _hdr("4. Phase 4 — graph populated from the landed tables")
    store, _ = populate_graph()

    _hdr("4b. every behavioural detector, over radar-sourced tracks")
    run_radar_behaviour(store, radar_out["radar_tracks"])

    _hdr("5. graph, split real vs synthetic")
    report_graph_split(store)

    _hdr("6. connectivity of the sanctions-matched population")
    print("  Real data alone gave 0 of 98 with any encounter edge (2026-07-30).")
    connectivity(store)

    _hdr("7. Phase 5 — anomaly library")
    # Radar's dark verdicts join the SAR ones. `detect_dark_vessels` reads a
    # list of verdict rows and does not ask which sensor produced them, which
    # is the connector claim holding at the last stage as well as the first.
    # **Every input the library takes now carries both sensors.** Verdicts,
    # associations, encounters and tracks are concatenations, and not one of the
    # six detectors is told which sensor a row came from — which is the
    # connector claim holding at the last stage as well as the first. Radar
    # encounters in particular are what let `dark_rendezvous` fire on a meeting
    # nobody can name; without them it saw an empty list and its silence read as
    # "radar found no rendezvous" rather than "radar was never asked".
    fusion_out = dict(
        fusion_out,
        verdicts=[*fusion_out["verdicts"], *radar_out["verdicts"]],
        associations=[*fusion_out["associations"], *radar_out["associations"]])
    tracks_out = dict(
        tracks_out,
        tracks=[*tracks_out["tracks"], *radar_out["radar_tracks"]],
        encounters=[*tracks_out["encounters"], *radar_out["encounters"]])
    run_anomalies(store, tracks_out, fusion_out)

    _hdr("7b. Phase 5 — the four analyses the zone layer unlocks (ADR-030)")
    run_zone_analyses(store, zone_tracks, zone_out)

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

    # The dark-contact measurement is separate from the scenario one and both
    # are needed: one asks whether the product raised the right alerts about the
    # right vessels, the other whether the sensor fusion found the unexplained
    # targets. A system can pass either while failing the other.
    if radar_out["verdicts"] or radar_out["correlations"]:
        print()
        print(format_radar_measurement(
            measure_radar(radar_out["verdicts"], radar_out["correlations"])))

    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
