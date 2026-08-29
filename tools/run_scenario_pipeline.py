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
from datetime import datetime, timedelta, timezone
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


def _clear_synthetic_table(table: str) -> int:
    """Drop the synthetic rows of one derived table, keeping any real ones.

    Same shape as `scenario.run.clear`: partitions are rewritten without the
    synthetic rows rather than deleted, so a partition holding both kinds keeps
    what it should.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    from maritime_isr.ingest.landing import table_day_partitions

    removed = 0
    for path in table_day_partitions(table):
        try:
            tbl = pq.read_table(path)
        except Exception:                                     # noqa: BLE001
            continue
        rows = tbl.to_pylist()
        keep = [r for r in rows if not r.get("is_synthetic")]
        if len(keep) == len(rows):
            continue
        removed += len(rows) - len(keep)
        if keep:
            pq.write_table(pa.Table.from_pylist(keep, schema=tbl.schema), path)
        else:
            path.unlink(missing_ok=True)
    return removed


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


#: Identity records that describe what the vessel *said about herself*. GFW
#: publishes these as `self_reported`; everything else in the table is somebody
#: else attesting to the same hull.
_BROADCAST_KINDS = ("self_reported",)


def _current_identities(reader) -> list[dict]:
    """One current row per hull, with the registry's attestation attached.

    **The previous version of this query destroyed the evidence the identity
    check needs.** It took the single most recent row per `vessel_id`, which
    collapses a hull's broadcast identity and her registry entry into whichever
    happened to sort first — so `check_registry_consistency` was handed one
    attestation, had nothing to compare it against, and returned "cannot check"
    for all 230 hulls. The real GFW connector has landed both record kinds from
    the beginning, with a comment saying exactly why: *"disagreement with the
    registry is a signal in its own right, so we keep both."* The query threw
    the second one away.

    So: the current row per (hull, record kind), then the broadcast row as the
    subject with the registry row attached as `registry`. A hull with only one
    kind still yields a row — she is simply not checkable for consistency, and
    that stays an honest "cannot check" rather than becoming an error.
    """
    rows = reader.rows("""
        SELECT * FROM (
          SELECT *, row_number() OVER (
            PARTITION BY vessel_id, record_kind
            ORDER BY (valid_to IS NULL) DESC, valid_from DESC NULLS LAST
          ) AS _rn FROM gfw_vessel_identity) WHERE _rn = 1
    """)

    by_vessel: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_vessel.setdefault(r.get("vessel_id"), {})[r.get("record_kind")] = r

    out: list[dict] = []
    for vid, kinds in by_vessel.items():
        if not vid:
            continue
        broadcast = next((kinds[k] for k in _BROADCAST_KINDS if k in kinds),
                         None)
        attestations = {k: v for k, v in kinds.items()
                        if k not in _BROADCAST_KINDS}
        if broadcast is None:
            # Nothing self-reported: the registry record is all we hold, and a
            # registry cannot contradict itself. Pass it through as the subject
            # so the arithmetic checks still run on whatever identifiers it
            # carries, with no comparison attached.
            broadcast = next(iter(kinds.values()))
            attestations = {}
        row = dict(broadcast)
        if attestations:
            att = next(iter(attestations.values()))
            row["registry"] = dict(
                name=att.get("ship_name"), call_sign=att.get("call_sign"),
                vessel_class=att.get("vessel_class"))
            row["registry_record_kind"] = att.get("record_kind")
        out.append(row)
    return out


def _read_pans_inbox() -> list[dict]:
    """Read the arrival-notification inbox and land what came out (ADR-036).

    **This is the connector run, inside the pipeline, over documents on disk.**
    Not a shortcut past it: the extractor opens the same PDFs, spreadsheets and
    faxes an operator would receive, and lands whatever it manages to read. A
    stage that took the generator's own field values would prove the rules work
    and nothing at all about the half of Area 4 that is the hard half.
    """
    from maritime_isr.config import DATA_ROOT
    from maritime_isr.ingest.pans.land import (FIELD_NAMES, TABLE, land_inbox,
                                               read_inbox)
    from maritime_isr.ingest.pans.readers import reader_availability
    from maritime_isr.ingest.pans.resolve import merge_identity_sources
    from maritime_isr.scenario.pans import PANS_DIRNAME

    inbox = DATA_ROOT / PANS_DIRNAME
    if not inbox.exists():
        print("  no pans_inbox/ — Area 4 has nothing to read")
        return []

    avail = reader_availability()
    missing = {k: v for k, v in avail.items() if v != "ok"}
    print(f"  readers        : {len(avail) - len(missing)}/{len(avail)} "
          f"available" + (f" — MISSING {sorted(missing)}" if missing else ""))

    # The registry the resolver sees is everything the system holds about a
    # hull, not one table's opinion of her: `gfw_vessel_identity` first, then
    # the static identity she broadcast herself in AIS message 5. See
    # `resolve.merge_identity_sources` for why reading only the first turns a
    # gap in our coverage into an accusation about somebody's paperwork.
    registry = merge_identity_sources(
        list(read_table("gfw_vessel_identity")),
        [dict(vessel_id=r.get("vessel_id"), imo=r.get("imo"))
         for r in read_table("ais_voyage")],
    )
    rows = read_inbox(inbox, registry, is_synthetic=True)
    land_inbox(inbox, registry, is_synthetic=True)

    by_fmt = Counter(r["document_format"] for r in rows)
    resolved = sum(1 for r in rows if r.get("vessel_id"))
    unread = sum(1 for r in rows if r.get("unread_reason"))
    fields = sum(r.get("fields_read") or 0 for r in rows)
    print(f"  notifications  : {len(rows):,} document(s) {dict(by_fmt)}")
    print(f"                   {resolved:,} resolved to a hull, "
          f"{len(rows) - resolved - unread:,} unmatched, {unread:,} unreadable")
    print(f"                   {fields:,} field(s) extracted "
          f"({fields / max(len(rows), 1):.1f} per document of "
          f"{len(FIELD_NAMES)})")
    by_how = Counter(r.get("resolved_by") for r in rows if r.get("vessel_id"))
    if by_how:
        print(f"                   resolved by {dict(by_how)}")
    print(f"  landed into {TABLE}")
    return rows


def _port_call_rows() -> list[dict]:
    """The arrivals the corpus recorded, for the paperwork rules to check."""
    out = []
    for r in read_table("gfw_port_visits"):
        out.append(dict(vessel_id=r.get("vessel_id"),
                        start_time=r.get("start_time"),
                        port_name=r.get("port_name"),
                        lat=r.get("lat"), lon=r.get("lon")))
    return out


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
            identities = _current_identities(reader)
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


def _voyage_declarations() -> list[dict]:
    """AIS message 5 rows — what each hull said about the voyage she was on.

    Absent is a legitimate answer and not an error: a corpus generated before
    ADR-035, or a real feed whose connector has not run, has no declarations and
    the voyage rule simply stays quiet. A vessel that never declared a
    destination has not contradicted one.
    """
    from maritime_isr.api.reader import open_reader
    with open_reader() as reader:
        if not reader.has("ais_voyage"):
            print("  declarations   : no ais_voyage table — voyage checks quiet")
            return []
        rows = reader.rows("SELECT * FROM ais_voyage")
    hulls = len({r.get("vessel_id") for r in rows})
    named = sum(1 for r in rows if r.get("destination"))
    with_eta = sum(1 for r in rows if r.get("eta") is not None)
    print(f"  declarations   : {len(rows):,} row(s) over {hulls:,} hull(s) — "
          f"{named:,} name a destination, {with_eta:,} state an ETA")
    return rows


def _area3_interactions(tracks):
    """Vessel-to-vessel interactions over the whole picture (ADR-033).

    Degrades to an empty list rather than raising: a picture too dense for the
    pair search is a real condition and the rest of Phase 5 should still run.
    The guard's message names the count, so the reason is not lost.
    """
    from maritime_isr.tracks.interactions import detect_interactions, summarise

    try:
        itx = detect_interactions(list(tracks))
    except RuntimeError as exc:
        print(f"  interactions   : SKIPPED — {exc}")
        return []
    s = summarise(itx)
    print(f"  interactions   : {s['total']} ({s['by_kind'] or 'none'}), "
          f"{s['cross_sensor']} cross-sensor")
    return itx


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
    declarations = _voyage_declarations()
    notifications = _read_pans_inbox()
    port_calls = _port_call_rows()

    # ---- Area 3 inputs (ADR-033) -----------------------------------------
    # The pair search is the expensive half of interaction detection, so it is
    # run once here and handed to the detector rather than run inside it.
    interactions = _area3_interactions(tracks_out["tracks"])

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
        declarations=declarations,
        notifications=notifications,
        port_calls=port_calls,
        baselines=baselines,
        interactions=interactions)
    print(f"  ran in {time.time() - t0:.0f}s")
    for atype, ids in sorted(fired.items()):
        print(f"    {atype:<26}{len(ids):>6} alert(s)")
    return fired


#: How long one cueing slot is, and how far the campaign reaches.
#:
#: **Derived from transit geometry, not from compute budget.** A slot is a
#: decision interval, so the question it has to answer is: how many times does
#: the scheduler get to consider a vessel while she is close enough to prove
#: something about her?
#:
#: The band inside which a mismatch is provable reaches about 8 km in this
#: coast's visibility. A hull passing a station at 5.5 km and 11.5 knots crosses
#: an 11.7 km chord of that band, which takes 33 minutes. At half-hour slots
#: that is **1.1 decisions** — and the figure barely moves with how close she
#: passes (a 3 km pass gives 1.1, a 6 km pass gives 1.0), because a closer track
#: lengthens the chord and the speed is unchanged. So the old value gave every
#: transiting vessel in the corpus exactly one look, taken wherever the slot
#: boundary happened to fall rather than near her closest approach. O1 was
#: caught at 8.6 km on the way in, at image quality 0.29 against a 0.35 floor,
#: and the campaign never returned to her.
#:
#: Ten minutes gives 3.3 decisions across the same crossing, so at least one
#: falls near the closest point. The cost is real and was checked rather than
#: assumed: three times the camera-slots. It is affordable because the cameras
#: were idle in 98.4% of slots at the old cadence — a decision interval is not
#: a rationing device when there is nothing to ration.
#:
#: The old comment named the defect and read it as a virtue: "a merchant covers
#: six kilometres in it, which is most of the range band inside which a mismatch
#: is provable". Covering most of the band between decisions is the failure, not
#: the design point.
EO_SLOT_SECONDS = 600.0

#: How often the loop closes — how soon what an image concluded can change what
#: the scheduler does next.
#:
#: **Set by how long an opportunity lasts, not by the calendar.** The
#: corroboration rule needs a second agreeing look, and the mechanism for
#: getting one is that a contradicted hull is re-ranked. That only works if the
#: verdict reaches the scheduler while she is still in view. At weekly stages it
#: never did: a coastal transit is inside the provable band for about half an
#: hour, so the first contradicting image landed in a stage that had already
#: been planned, and the loop closed six days after she had gone.
#:
#: **One slot, because in a deployment the loop's latency is the classifier's
#: latency, and that is seconds.** Half an hour was still a batch, and it was
#: measured costing the area its two authored findings. O1 passes Porbandar
#: observable for 78 minutes, at an image quality good enough to settle her
#: identity for about 55 of them. Her first look at 08:15 contradicted what she
#: broadcasts — but `verdict_state` only advanced at the stage boundary, so for
#: the rest of that stage the scheduler still believed her unverified, reset her
#: staleness clock on the look it had just taken, and dropped her to 0.165
#: against a 0.30 floor. Porbandar's camera then sat idle through six slots in
#: which she was in clear view. By the time the verdict arrived her window had
#: closed, and the corroborating second look the rule requires was never taken.
#: The camera saw a 270 m tanker broadcasting that she was a trawler, and the
#: system reported nothing.
#:
#: A stage is a planning convenience; a batch interval is a claim about how long
#: it takes to read an image. Making them the same number meant the second claim
#: was never examined. There is no assignment work lost: each call ranked only
#: its own slots either way, so what multiplies is per-call setup, and the
#: lookahead that urgency depends on no longer stops at the plan's edge.
EO_STAGE_SLOTS = 1


def run_eo_loop(store, all_tracks, alerts_before: int) -> dict:
    """Area 5 (ADR-037) — cue, capture, classify, and bind to the track.

    **This runs after the anomaly library, and the ordering is the design.**
    Cueing is driven by suspicion, and suspicion is what that pass produces. A
    camera network that decided where to look before anything had been flagged
    would be a raster scan, which is what the requirement is asking to replace.

    Nothing here is scenario-aware except the `CaptureSource`, which is the
    camera. The scheduler, the classifier, the library and the mismatch rule are
    the same objects a deployment would run.
    """
    import numpy as np

    from maritime_isr.eo.camera import default_camera_network
    from maritime_isr.eo.capture import (TABLE as EO_TABLE, land_captures,
                                         publish_captures, run_captures)
    from maritime_isr.eo.classify import (PrototypeClassifier,
                                          ReferenceLibrary,
                                          SilhouetteClassifier)
    from maritime_isr.eo.cue import (MAX_LOOKS_PER_VERDICT, CueCandidate,
                                     plan_cueing)
    from maritime_isr.ingest.landing import SYNTHETIC_SOURCE_ID
    from maritime_isr.scenario.eo import (TABLE as APPEARANCE_TABLE,
                                          SimulatedCameraSource)
    from maritime_isr.tracks.kalman import epoch_s

    appearance = read_table(APPEARANCE_TABLE)
    if not appearance:
        print("  no scenario_eo_appearance — the camera simulator has no world "
              "model, so no capture can be taken and none is invented")
        return {}
    cameras = default_camera_network()
    print(f"  cameras        : {len(cameras)} (all simulated; there is no "
          f"camera in this system)")

    # ---- the picture, resampled onto the slot grid ------------------------
    #
    # Every track in the corpus is a candidate, not only the flagged ones. That
    # is the requirement's framing — "a picture containing far more tracks than
    # there are cameras" — and a scheduler handed only the suspicious ones would
    # never have to choose.
    suspicion: dict[str, tuple[float, str]] = {}
    for a in store.alerts():
        if a.get("disposition") == "dismiss":
            continue
        score = float(a.get("score") or a.get("confidence") or 0.0)
        cur = suspicion.get(a["subject"])
        if cur is None or score > cur[0]:
            suspicion[a["subject"]] = (score, str(a.get("anomaly_type")
                                                  or a.get("rule") or "alert"))

    from maritime_isr.graph.identity import contact_node_id
    from maritime_isr.schemas.keys import vessel_node_id

    # **MMSI to hull, through the identity table rather than through the graph
    # walk.** `resolve_mmsi` follows `identified-as` edges and mints a
    # provisional `vessel:mmsi:<n>` when it finds none — which is what it did
    # for every hull in this corpus, so the captures bound to nodes that carry
    # no declared type and the mismatch rule had nothing to check. The identity
    # table is the side that *publishes* the canonical key (ADR-022), and it is
    # the same table every other stage in this script joins on.
    hull_of_mmsi: dict[int, str] = {}
    for r in read_table("gfw_vessel_identity"):
        if r.get("record_kind") not in (None, "", "self_reported"):
            continue
        m, vid = r.get("mmsi"), r.get("vessel_id")
        if m in (None, "") or not vid:
            continue
        try:
            hull_of_mmsi[int(float(m))] = vessel_node_id(str(vid))
        except (TypeError, ValueError):
            continue

    # **A radar track the correlation stage matched to a hull is not an
    # unidentified contact, and treating it as one broke the whole area.**
    #
    # Measured on the first run: all 487 captures landed on `contact:` subjects
    # and not one on a hull, so the mismatch rule — which needs a *declared*
    # identity to contradict — fired zero times over a corpus containing two
    # authored lies. The cause was not the rule and not the geometry. It was
    # that every radar track entered the candidate set as anonymous, so its
    # information gain scored 1.00 against 0.55 for a named hull; the cameras
    # are co-located with the radars, the radars hold about fourteen hundred
    # coastal tracks, and the AIS hulls therefore lost every slot they were
    # ever offered. The scheduler was working exactly as designed on an input
    # that lied to it.
    #
    # The correlation stage (ADR-028) already decides this question, with
    # evidence, and lands the answer. Reading it here is what makes "an image
    # of a named hull is worth less than an image of a target nobody can name"
    # a true statement rather than a systematic bias toward radar.
    #
    # Only `correlated` and `correlated_then_dark` count. `ambiguous` means the
    # correlation could not choose, and `dark` means it chose "nobody" — both
    # are exactly the cases where the identity is not established, and folding
    # them in would be claiming an identification the cascade declined to make.
    identified_track: dict[str, int] = {}
    for r in read_table("radar_correlation"):
        if r.get("status") not in ("correlated", "correlated_then_dark"):
            continue
        rid, mmsi = r.get("radar_track_id"), r.get("mmsi")
        if rid and mmsi not in (None, ""):
            try:
                identified_track[str(rid)] = int(float(mmsi))
            except (TypeError, ValueError):
                continue
    print(f"  correlation    : {len(identified_track):,} radar track(s) the "
          f"cascade matched to a hull — cued as that hull, not as a contact")

    fixes: list[tuple[float, CueCandidate]] = []
    n_named_radar = 0
    for tr in all_tracks:
        pts = tr.points[tr.points.quality != "outlier"] \
            if hasattr(tr.points, "quality") else tr.points
        if len(pts) == 0:
            continue
        has_id = bool(getattr(tr, "has_identity", False))
        mmsi = tr.mmsi if has_id else identified_track.get(str(tr.track_key))
        hull = hull_of_mmsi.get(int(mmsi)) if mmsi is not None else None
        if hull is not None:
            # The canonical subject, so suspicion, captures and the declared
            # identity all land on one node. An AIS track and the radar track of
            # the same hull collapse onto it, which is also what stops the
            # network photographing one ship twice in a slot.
            subject = hull
            has_id = True
            n_named_radar += int(not getattr(tr, "has_identity", False))
        else:
            # Either no identity at all, or an MMSI no identity row claims —
            # which is a gap in our coverage and not a hull, so it stays a
            # contact rather than becoming a stub (ADR-022).
            has_id = False
            subject = contact_node_id(str(tr.track_key), source=tr.source.name)
        length = None
        if "length_est_m" in pts.columns:
            vals = [v for v in pts["length_est_m"].tolist() if v]
            length = float(np.median(vals)) if vals else None
        ts = epoch_s(pts["ts"])
        susp, why = suspicion.get(subject, (0.0, ""))
        for i in range(0, len(pts), 3):     # one candidate per ~3 fixes
            fixes.append((float(ts[i]), CueCandidate(
                subject_id=subject, track_id=tr.track_id,
                lat=float(pts["lat"].iloc[i]), lon=float(pts["lon"].iloc[i]),
                sog_kn=float(pts["sog_kn"].iloc[i]
                             if "sog_kn" in pts.columns else 0.0),
                cog_deg=float(pts["cog_deg"].iloc[i]
                              if "cog_deg" in pts.columns else 0.0),
                length_m=length, suspicion=susp, suspicion_reason=why,
                identity_known=has_id, track_source=tr.source.name,
                is_synthetic=True)))
    if not fixes:
        print("  no tracks to cue over")
        return {}

    # **How long she is belongs to the hull, not to the fix that reported her.**
    #
    # An AIS track carries no `length_est_m` — the length comes from the radar
    # measurement — and the camera model needs a length to work out pixels on
    # target, so it refuses a candidate that has none. Both of O1's tracks are
    # collapsed onto one subject above precisely because they are one ship, and
    # then the length was left attached to whichever track the slot happened to
    # pick. In half her slots the feed picked the AIS fix, handed the scheduler
    # a hull of unknown size, and the camera declined to look at a 270 m tanker
    # sitting 5.5 km off Porbandar in clear daylight. She was a candidate in ten
    # consecutive slots, above the floor in all ten with a camera free, and was
    # imaged in the two where the nearest fix happened to be the radar one.
    #
    # Measured, not declared: the radar's estimate is what this system has
    # observed about her size, and reading it off her AIS static message would
    # be taking the word of a hull we are in the middle of accusing of lying
    # about herself.
    lengths: dict[str, list[float]] = {}
    for _t, c in fixes:
        if c.length_m:
            lengths.setdefault(c.subject_id, []).append(float(c.length_m))
    merged = {s: float(np.median(v)) for s, v in lengths.items()}
    n_filled = 0
    for _t, c in fixes:
        if not c.length_m and c.subject_id in merged:
            c.length_m = merged[c.subject_id]
            n_filled += 1
    if n_filled:
        print(f"                   {n_filled:,} candidate position(s) took "
              f"their length from another track of the same hull "
              f"({len(merged):,} target(s) with a measured length)")
    fixes.sort(key=lambda f: f[0])
    subjects = {c.subject_id for _t, c in fixes}
    named = sum(1 for s in subjects if s.startswith("vessel:"))
    print(f"  picture        : {len(subjects):,} distinct target(s) "
          f"({named:,} named, {len(subjects) - named:,} unidentified), "
          f"{len(fixes):,} candidate position(s)")
    print(f"                   {n_named_radar:,} radar track(s) entered as "
          f"their correlated hull rather than as a contact")

    # Station positions once, for the per-slot "which fix is the best look"
    # choice below. Cameras sit on the radar stations, so nearest-station range
    # is the same quantity the camera model starts from.
    _stations = [(c.lat, c.lon) for c in cameras]

    def _nearest_station_km(lat: float, lon: float) -> float:
        import math
        best = float("inf")
        for slat, slon in _stations:
            p1, p2 = math.radians(lat), math.radians(slat)
            dp = math.radians(slat - lat)
            dl = math.radians(slon - lon)
            a = (math.sin(dp / 2) ** 2
                 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
            d = 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))
            if d < best:
                best = d
        return best

    slot_index: dict[int, list[CueCandidate]] = {}
    t_start = fixes[0][0]
    for t, c in fixes:
        slot_index.setdefault(int((t - t_start) // EO_SLOT_SECONDS),
                              []).append(c)

    def feed_for(base: float):
        def feed(when):
            k = int((when.timestamp() - t_start) // EO_SLOT_SECONDS)
            # One candidate per subject per slot — several fixes of one hull in
            # ten minutes are one target, not three — but **the best of them,
            # not the first**.
            #
            # Candidates are time-sorted, so taking the first handed the
            # scheduler each vessel's *earliest* position in the window. For any
            # hull closing on a station that is systematically her furthest, and
            # for a hull departing it is her nearest: the scheduler was
            # consistently pessimistic about exactly the vessels about to become
            # imageable. O1 was evaluated twice at a frozen 8.588 km, at an
            # image quality of 0.29 against a 0.35 classify floor, while her
            # track reached 5.46 km inside the same window — her closer fixes
            # were discarded here before the assignment ever saw them.
            #
            # Nearest-station distance is the proxy, not full image quality: the
            # scheduler computes quality per camera and would have to be run to
            # know it, while range dominates it and is one haversine away. The
            # window is short enough that the best-ranged fix is the best look
            # in all but contrived geometry.
            #
            # A candidate the camera model cannot evaluate loses to one it can,
            # whatever the range: a fix with no length yields no pixels-on-
            # target and is refused as unobservable, so preferring it because it
            # is 300 m nearer trades a look for nothing. The merge above means
            # this rarely binds now; it stays because "nearest" is a proxy for
            # "best look" and a candidate that cannot be looked at is not one.
            best: dict[str, tuple[tuple[int, float], CueCandidate]] = {}
            for c in slot_index.get(k, ()):
                rank = (0 if c.length_m else 1,
                        _nearest_station_km(c.lat, c.lon))
                prev = best.get(c.subject_id)
                if prev is None or rank < prev[0]:
                    best[c.subject_id] = (rank, c)
            return [c for _r, c in best.values()]
        return feed

    # ---- the campaign, one slot at a time so the loop closes ---------------
    source = SimulatedCameraSource(appearance)
    classifier = PrototypeClassifier()
    library = ReferenceLibrary()
    imaged_at: dict[str, float] = {}
    states: dict[str, str] = {}
    #: Looks that actually yielded a type, per subject, across every stage. The
    #: scheduler bounds how many of these one verdict is worth; that bound is
    #: only a bound if the count survives the stage boundary, and at three slots
    #: a stage a per-call counter never reaches three.
    classified: dict[str, int] = {}
    all_caps = []
    plans = []

    t_end = fixes[-1][0]
    stage_seconds = EO_STAGE_SLOTS * EO_SLOT_SECONDS
    n_stages = max(1, int((t_end - t_start) // stage_seconds) + 1)
    t0 = datetime.fromtimestamp(t_start, tz=timezone.utc)
    for stage in range(n_stages):
        start = t0 + timedelta(seconds=stage_seconds * stage)
        plan = plan_cueing(feed_for(t_start), cameras, t0=start,
                           slots=int(stage_seconds / EO_SLOT_SECONDS),
                           slot_seconds=EO_SLOT_SECONDS,
                           imaged_at=imaged_at, verdict_state=states,
                           classified_looks=classified)
        plans.append(plan)
        caps = run_captures(plan, source=source, classifier=classifier,
                            library=library, is_synthetic=True)
        all_caps.extend(caps)
        # Close the loop: what this stage's images concluded changes what the
        # next stage thinks is worth a camera.
        #
        # **Only a capture that yielded a type counts as having imaged her.**
        # `cue.plan_cueing` withholds the staleness clock from an unclassifiable
        # look within a stage; setting it here unconditionally handed the next
        # stage the opposite belief and undid that across every stage boundary.
        # A hull photographed at 0.29 quality has not been looked at in any
        # sense the scheduler should act on — the image proved she was there and
        # nothing else.
        #
        # The look count is carried the same way and for the same reason, but
        # counted on what the image *returned* rather than on what the
        # scheduler expected of it. The plan carries its own optimistic count
        # out (`plan.classified_looks`); seeding the next stage from that would
        # charge a hull for a look that came back too dim to read.
        #
        # **And an unsettled contradiction does not reset the clock here
        # either.** `plan_cueing` withholds it inside a stage precisely so a
        # hull whose image disagreed with her declaration stays stale enough to
        # clear `PRIORITY_FLOOR` and be looked at again; resetting it at the
        # boundary handed the next stage the opposite belief and reinstated the
        # deadlock the withholding exists to break — an unsuspicious hull,
        # freshly imaged and contradicted, scores 0.2550 against a 0.30 floor.
        # Every authored liar got **exactly one classifiable look** and the rule
        # then waited forever for a corroborating second one, so the whole area
        # reported zero. The two rules must state the same thing at both scales
        # or the boundary quietly undoes the fix, which is the same shape of
        # defect as the look counter resetting between calls.
        _update_states(store, caps, states)
        for c in caps:
            if not c.imaged_type:
                continue
            classified[c.subject_id] = classified.get(c.subject_id, 0) + 1
            unsettled = (states.get(c.subject_id) == "contradicted"
                         and classified[c.subject_id] < MAX_LOOKS_PER_VERDICT)
            if not unsettled:
                imaged_at[c.subject_id] = c.taken_at.timestamp()

    n_task = sum(len(p.taskings) for p in plans)
    n_defer = sum(len(p.deferrals) for p in plans)
    slots = sum(p.camera_slots for p in plans)
    counters = Counter()
    for p in plans:
        counters.update(p.counters)
    n_opp = counters.get("opportunistic_looks", 0)
    print(f"  cueing         : {n_task:,} tasking(s) over {slots:,} "
          f"camera-slot(s) ({n_task / max(slots, 1):.1%} utilisation), "
          f"{n_defer:,} deferral(s) recorded")
    # Split, because one utilisation figure covering both would be a claim the
    # plan does not support: a look taken only because the head was free is not
    # a look the priority model would have paid for, and reading them together
    # would let a fill inflate the number that is supposed to measure demand.
    if n_opp:
        print(f"                   {n_task - n_opp:,} earned their slot; "
              f"{n_opp:,} filled a camera nothing else could use "
              f"({(n_task - n_opp) / max(slots, 1):.1%} / "
              f"{n_opp / max(slots, 1):.1%})")
    for k in ("candidates_seen", "below_priority_floor", "opportunistic_looks",
              "no_camera_in_reach", "outranked", "slew_too_far",
              "idle_camera_slots"):
        print(f"    {k:<24}{counters[k]:>10,}")
    if source.misses:
        print(f"    {'no world model':<24}{source.misses:>10,}  "
              f"(the simulator could not say what was at that bearing; no "
              f"capture was invented)")

    present = sum(1 for c in all_caps if c.target_present)
    empty = len(all_caps) - present
    claims = sum(1 for c in all_caps if c.verdict and c.verdict.is_claim)
    ident = sum(1 for c in all_caps if c.verdict and c.verdict.identity_subject)
    print(f"  captures       : {len(all_caps):,} — {present:,} with a target "
          f"in frame, {empty:,} empty (a resolved radar track, not an alert)")
    print(f"                   {claims:,} type claim(s), {ident:,} "
          f"re-recognised from the library of {len(library):,} entries")

    # **This campaign replaces the last one; it does not add to it.**
    # `eo_capture` is a derived output regenerated wholesale on every pipeline
    # run, so leaving the previous run's rows in place breaks the invariant that
    # a derived layer is reproducible from raw plus a git SHA (CLAUDE.md §4.2) —
    # and worse, the corroboration rule reads the table whole and would count
    # one run's look and the next run's look at the same hull as two independent
    # looks. Clearing here rather than in `scenario clear` because this is the
    # stage that produces them: a table is cleared by whoever regenerates it.
    _clear_synthetic_table(EO_TABLE)
    written = land_captures(all_caps,
                            source_id=f"{SYNTHETIC_SOURCE_ID}:eo-camera",
                            is_synthetic=True)
    for table, n in sorted(written.items()):
        print(f"  landed {n:,} row(s) into {table}")
    pub = publish_captures(store, all_caps)
    print(f"  graph          : {pub['nodes']:,} eo_capture node(s), "
          f"{pub['depicts']:,} depicts, {pub['captured-by']:,} captured-by")

    # ---- swap-ability, demonstrated on the same captures ------------------
    #
    # The brief asks for this to be shown rather than asserted. Same images,
    # same loop, a second model: the vocabulary is coarser and the claim rate
    # differs, and not one line of the cueing, tagging or rule code changed.
    thin = SilhouetteClassifier()
    thin_claims = 0
    for cap in all_caps:
        if cap.observed is None:
            continue
        v = thin.classify(cap.observed, quality=cap.image_quality,
                          band=cap.band, library=ReferenceLibrary())
        thin_claims += int(v.is_claim)
    print(f"  classifier swap: {classifier.name} made {claims:,} type claim(s) "
          f"on these captures; {thin.name} makes {thin_claims:,} on the same "
          f"images, through the same interface, with no other change")

    print(plans[max(range(len(plans)), key=lambda i: len(plans[i].taskings))]
          .format())
    return dict(captures=all_caps, plans=plans, table=EO_TABLE)


def _update_states(store, caps, states: dict) -> None:
    """Fold this stage's verdicts into what the scheduler believes.

    `confirmed` means an image agreed with what she broadcasts and there is
    little left to learn; `contradicted` means the opposite and she should be
    looked at again, which is how the corroborating second look gets taken.
    """
    from maritime_isr.anomaly.imagery import check_declared_type
    from maritime_isr.api.reader import open_reader

    from maritime_isr.schemas.keys import vessel_node_id

    # Keyed by the canonical node id, because that is what a capture is bound
    # to. The identity table names `vessel:eo_false_class` and the graph node is
    # `vessel:gfw:eo_false_class`; indexing on one and looking up with the other
    # is the join defect that made the whole area silent.
    declared: dict[str, str] = {}
    with open_reader() as reader:
        if reader.has("gfw_vessel_identity"):
            for r in reader.rows(
                    "SELECT vessel_id, vessel_class FROM gfw_vessel_identity "
                    "WHERE record_kind = 'self_reported'"):
                if r.get("vessel_id") and r.get("vessel_class"):
                    declared[vessel_node_id(str(r["vessel_id"]))] = \
                        str(r["vessel_class"])
    for c in caps:
        if not c.verdict or not c.verdict.is_claim:
            continue
        f = check_declared_type(declared_class=declared.get(c.subject_id),
                                verdict=c.verdict, quality=c.image_quality,
                                band=c.band)
        if f.outcome == "contradiction":
            states[c.subject_id] = "contradicted"
        elif f.outcome == "ok" and states.get(c.subject_id) != "contradicted":
            states[c.subject_id] = "confirmed"


def run_imagery_mismatch(store, captures) -> list[str]:
    """The payoff: the camera against the transponder."""
    from maritime_isr.anomaly.library import detect_imagery_mismatch

    identities, _baselines = _area2_inputs()
    rows = [c.as_row() for c in captures]
    fired = detect_imagery_mismatch(store, rows, identities,
                                    source_ref="scenario-combined")
    print(f"    {'imagery_type_mismatch':<26}{len(fired):>6} alert(s)")
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

    _hdr("7c. Area 5 — the electro-optical loop (ADR-037)")
    # After the anomaly library on purpose: cueing is driven by suspicion, and
    # suspicion is what that pass produces. A camera network that decided where
    # to look before anything had been flagged would be a raster scan, which is
    # what the requirement is asking to replace.
    eo = run_eo_loop(store, zone_tracks, alerts_before=len(store.alerts()))

    _hdr("7d. Area 5 — the camera against the transponder")
    if eo.get("captures"):
        run_imagery_mismatch(store, eo["captures"])
    else:
        print("  no captures, so nothing to compare against a declaration")

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
