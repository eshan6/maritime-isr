"""Phase 6 product-surface snapshot builder.

Runs the whole platform (Phases 2-5) and serializes ONE operational-picture
JSON that the product surface renders: tracks, latest SAR contacts, dark
candidates, alert queue with evidence chains, per-vessel entity pages
(identity history, fingerprint, risk decomposition, graph neighborhood),
replay frames, and one-click report payloads.

This is the backend the laptop-rotation demo runs against — no rehearsed
data, the same synthetic pipeline every other phase used.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pandas as pd

from .. import anomaly, fusion, graph, tracks as trk
from ..config import AOI_V1, DATA_ROOT, GRAPH_DB_NAME
from ..connectors import ais as ais_conn, satais


def _build_upstream(data_dir):
    payload = (data_dir / "synthetic_ais_30d.nmea").read_bytes()
    parser = ais_conn.AivdmParser("multi", aoi=AOI_V1)
    messages = []
    for line in payload.decode().splitlines():
        ts_s, rx, sentence = line.split("\t")
        parser.receiver = rx
        m = parser.feed(sentence, datetime.fromisoformat(ts_s))
        if m and m["msg_type"] != 5:
            messages.append(m)
    pos = ais_conn.conform(messages, source="ais:multi", source_ref="p6").to_pandas()
    sched = trk.SatPassSchedule(satais.parse_pass_predictions(
        (data_dir / "synthetic_sat_passes.json").read_bytes()))
    eng = trk.run_track_engine(pos, source_ref="p6", sat_schedule=sched,
                              partition_day="p6", aoi=AOI_V1.name,
                              write_outputs=False)
    scenes = json.loads((data_dir / "synthetic_scenes_phase3.json").read_text())
    registry = {int(k): v for k, v in json.loads(
        (data_dir / "synthetic_registry.json").read_text()).items()}
    fus = fusion.run_fusion(scenes, eng["tracks"], eng["coverage_model"],
                            registry, gaps=eng["gaps"],
                            spoof_events=eng["spoof_events"], source_ref="p6",
                            partition_day="p6", aoi=AOI_V1.name,
                            write_outputs=False)
    return eng, fus, scenes


def _build_graph(data_dir, eng, fus):
    db = DATA_ROOT / GRAPH_DB_NAME
    if db.exists():
        db.unlink()
    g = graph.GraphStore(db)
    graph.ensure_world(g)
    for f in ("synthetic_registry_v1.json", "synthetic_registry_v2.json"):
        graph.fold_registry_snapshot(g, json.loads((data_dir / f).read_text()),
                                     source_ref=f)
    graph.ingest_ownership(g, json.loads(
        (data_dir / "synthetic_ownership.json").read_text()), source_ref="own",
        as_of=pd.Timestamp("2026-06-15", tz="UTC").timestamp())
    graph.ingest_sanctions(g, json.loads(
        (data_dir / "synthetic_sanctions_phase4.json").read_text()),
        source_ref="sanc")
    graph.ingest_tracks(g, eng["tracks"], source_ref="p6")
    graph.ingest_encounters(g, eng["encounters"], source_ref="p6")
    graph.ingest_fusion(g, fus["associations"], fus["verdicts"], source_ref="p6")
    graph.process_events(g)
    return g


def build_snapshot(data_dir) -> dict:
    eng, fus, scenes = _build_upstream(data_dir)
    g = _build_graph(data_dir, eng, fus)

    # anomaly library over the live graph
    fired = anomaly.run_anomaly_library(
        g, tracks=eng["tracks"], encounters=eng["encounters"],
        spoof_events=eng["spoof_events"], associations=fus["associations"],
        verdicts=fus["verdicts"], source_ref="p6")

    at = pd.Timestamp("2026-07-15", tz="UTC").timestamp()

    # ---- tracks (decimated for the map) ----
    tracks_out = []
    for t in eng["tracks"]:
        pts = t.points[t.points.quality != "outlier"]
        tracks_out.append(dict(
            track_id=t.track_id, mmsi=t.mmsi,
            t_start=t.points["ts"].min().isoformat(),
            t_end=t.points["ts"].max().isoformat(),
            pts=[[round(r.lat, 4), round(r.lon, 4),
                  int(pd.Timestamp(r.ts).timestamp())]
                 for r in pts.iloc[::5].itertuples()]))

    # ---- latest SAR contacts + dark candidates ----
    dark = [dict(detection_id=v["detection_id"], scene_id=v["scene_id"],
                 lat=round(v["lat"], 4), lon=round(v["lon"], 4),
                 length_m=round(v["length_m"], 1),
                 score=round(v["dark_score"], 3),
                 ts=pd.Timestamp(v["ts"]).isoformat(),
                 status=v["status"])
            for v in fus["verdicts"] if v["status"] == "dark_candidate"]
    matched = [dict(lat=round(next(d["lat"] for s in scenes
                                   for d in s["detections"]
                                   if d["detection_id"] == a["detection_id"]), 4),
                    lon=round(next(d["lon"] for s in scenes
                                   for d in s["detections"]
                                   if d["detection_id"] == a["detection_id"]), 4),
                    mmsi=a["mmsi"], status=a["status"],
                    ts=pd.Timestamp(a["ts"]).isoformat())
               for a in fus["associations"]
               if a["status"] in ("matched", "ambiguous")]

    # ---- alert queue (graph rules + anomaly library) ----
    alerts_out = []
    for a in g.alerts():
        subj = a["subject"]
        mmsi = (graph.current_mmsi(g, subj, a["ts"])
                if subj.startswith("vessel") else None)
        alerts_out.append(dict(
            alert_id=a["alert_id"], type=a["anomaly_type"] or a["rule"],
            rule=a["rule"], subject=subj, mmsi=mmsi,
            score=round(a["score"], 3) if a["score"] else round(a["confidence"], 3),
            confidence=round(a["confidence"], 3),
            disposition=a["disposition"],
            ts=datetime.fromtimestamp(a["ts"], tz=timezone.utc).isoformat(),
            props=a["props"],
            evidence=[dict(edge=c.get("edge"), src=c.get("src"),
                           dst=c.get("dst"),
                           confidence=c.get("confidence"),
                           source=c.get("source")) for c in a["evidence"]]))

    # ---- entity pages: vessels that carry a track, alert, or risk ----
    subjects = {a["subject"] for a in g.alerts() if a["subject"].startswith("vessel")}
    for t in eng["tracks"]:
        subjects.add(graph.resolve_mmsi(g, t.mmsi,
                                        at=t.points["ts"].min().timestamp()))
    entities = {}
    for vid in subjects:
        node = g.node(vid)
        if node is None:
            continue
        rs = anomaly.risk_score(g, vid, at=at)
        ids = []
        for e in g.edges(vid, "identified-as", history=True):
            ids.append(dict(kind=e.props.get("kind"), value=e.props.get("value"),
                            t_start=e.t_start, t_end=e.t_end,
                            closed=e.t_end is not None))
        neighborhood = []
        for direction in ("out", "in"):
            for e in g.edges(vid, direction=direction):
                neighborhood.append(dict(
                    edge=e.edge_type, src=e.src, dst=e.dst,
                    confidence=round(g.edge_confidence(e, at=at), 3),
                    closed=e.t_end is not None))
        vtracks = [e.dst for e in g.edges(vid, "resolved-from")
                   if e.dst.startswith("track:")]
        valerts = [a["alert_id"] for a in g.alerts() if a["subject"] == vid]
        entities[vid] = dict(
            vessel_id=vid, props=node["props"],
            mmsi=graph.current_mmsi(g, vid, at) or node["props"].get("mmsi"),
            risk=rs, identity_history=ids,
            neighborhood=neighborhood[:40],
            n_tracks=len(vtracks), alerts=valerts)

    # ---- replay frames: 24 hourly-ish snapshots of vessel positions ----
    t0 = pd.Timestamp("2026-06-15", tz="UTC").timestamp()
    t1 = pd.Timestamp("2026-07-15", tz="UTC").timestamp()
    n_frames = 60
    frame_times = [t0 + (t1 - t0) * i / (n_frames - 1) for i in range(n_frames)]
    frames = []
    for ft in frame_times:
        positions = []
        for t in eng["tracks"]:
            te = t.points["ts"].map(lambda x: x.timestamp())
            if te.min() <= ft <= te.max():
                st = t.state_at(ft)
                la, lo = st.latlon
                if 5 <= la <= 25 and 60 <= lo <= 78:
                    positions.append([round(la, 3), round(lo, 3), t.mmsi])
        frames.append(dict(t=int(ft), pos=positions))

    # ---- risk board ----
    risk_board = [dict(vessel=r["vessel"],
                       mmsi=g.node(r["vessel"])["props"].get("mmsi")
                       if g.node(r["vessel"]) else None,
                       score=r["risk_score"],
                       components=r["components"])
                  for r in anomaly.rank_vessels(g, at=at, top=25)]

    snap = dict(
        generated_at=datetime.now(timezone.utc).isoformat(),
        as_of=datetime.fromtimestamp(at, tz=timezone.utc).isoformat(),
        aoi=dict(lat_min=AOI_V1.lat_min, lat_max=AOI_V1.lat_max,
                 lon_min=AOI_V1.lon_min, lon_max=AOI_V1.lon_max),
        pipeline_version="0.7.0",
        stats=dict(
            tracks=len(tracks_out), dark_candidates=len(dark),
            alerts=len(alerts_out),
            open_alerts=sum(1 for a in alerts_out if a["disposition"] == "open"),
            vessels=g.n_nodes("vessel"), edges=g.n_edges(),
            anomaly_types_live=sum(1 for v in fired.values() if v)),
        tracks=tracks_out, dark=dark, matched=matched,
        alerts=alerts_out, entities=entities, frames=frames,
        risk_board=risk_board,
        alert_type_counts=dict(Counter(a["type"] for a in alerts_out)),
        receivers=[dict(name=n, lat=la, lon=lo, radius_km=rk) for n, (la, lo, rk) in
                   {"Mumbai": (18.95, 72.84, 300), "Porbandar": (21.63, 69.60, 300),
                    "Karachi": (24.79, 66.98, 300), "Kochi": (9.97, 76.24, 300)}.items()],
    )
    g.close()
    return snap
