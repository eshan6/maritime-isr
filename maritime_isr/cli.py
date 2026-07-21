"""maritime_isr CLI — Phase 0 + Phase 1 orchestration.

  python -m maritime_isr.cli backfill-scenes --days 90     # discover+download S1 over AOI
  python -m maritime_isr.cli ingest-ais <nmea-or-json file> --receiver <id>
  python -m maritime_isr.cli snapshot-sanctions <csv> --as-of YYYY-MM-DD
  python -m maritime_isr.cli process-scenes <dir> --model <pkl>   # sigma0 -> published detections
  python -m maritime_isr.cli eval-report                   # eval ledger (release gate memory)
  python -m maritime_isr.cli status                        # coverage + drop-rate report

Cron on the deploy host runs `backfill-scenes --days 4` and the AIS inlet
continuously; that satisfies 'one command backfills 90 days and keeps it
current automatically'.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import AOI_V1, CONFORMED_ROOT, PIPELINE_VERSION
from .connectors import ais as ais_conn, registries, sentinel1
from .storage import catalog as cat, conformed, raw


def cmd_backfill_scenes(args):
    t1 = datetime.now(timezone.utc)
    t0 = t1 - timedelta(days=args.days)
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    n = sentinel1.discover(AOI_V1, fmt(t0), fmt(t1), token=args.token)
    print(f"discovered {n} scenes over {AOI_V1.name}, {args.days}d window")
    if args.download:
        d = sentinel1.download_pending(token=args.token, limit=args.limit)
        print(f"downloaded {d} scenes to raw store")


def cmd_ingest_ais(args):
    path = Path(args.file)
    payload = path.read_bytes()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rpath, sha = raw.land(f"ais_{args.receiver}", path.name, payload, day=day)

    messages, statics = [], []
    if args.format == "nmea":
        parser = ais_conn.AivdmParser(args.receiver, aoi=AOI_V1 if args.aoi_filter else None)
        for line in payload.decode(errors="replace").splitlines():
            if not line.strip():
                continue
            ts_part, _, sentence = line.partition("\t")  # our capture format: ts<TAB>sentence
            ts = datetime.fromisoformat(ts_part) if sentence else datetime.now(timezone.utc)
            msg = parser.feed(sentence or ts_part, ts)
            if msg:
                (statics if msg["msg_type"] == 5 else messages).append(msg)
        stats = parser.stats
        print(f"parsed {stats.parsed}/{stats.total}  drop_rate={stats.drop_rate:.3%}  "
              f"(checksum={stats.dropped_checksum} malformed={stats.dropped_malformed} "
              f"unsupported={stats.dropped_unsupported_type} out_of_aoi={stats.dropped_out_of_aoi})")
        if stats.drop_rate >= 0.01:
            print("WARNING: drop rate >= 1% — Phase 0 exit criterion violated", file=sys.stderr)
    else:
        for rec in json.loads(payload):
            m = ais_conn.normalize_json_report(rec, args.receiver)
            if m:
                m.setdefault("msg_type", 1)
                messages.append(m)
        print(f"normalized {len(messages)} json reports")

    tbl = ais_conn.conform(messages, source=f"ais_terrestrial:{args.receiver}", source_ref=sha[:12])
    out = conformed.write(tbl, "ais_position", source=f"ais_terrestrial:{args.receiver}",
                          aoi=AOI_V1.name, partition_day=day)
    print(f"conformed {tbl.num_rows} position rows "
          f"({tbl.schema.metadata[b'n_deduped'].decode()} deduped) -> {out}")
    if statics:
        print(f"captured {len(statics)} static/voyage (type 5) messages for registry fold-in")


def cmd_process_scenes(args):
    """Phase 1 automatic path: every calibrated scene in <dir> ->
    landmask -> CFAR -> discriminator -> conformed DETECTION parquet ->
    catalog PUBLISHED. Cron this after calibration on the deploy host;
    that closes the 'no human touch' exit criterion."""
    from .detect import pipeline as det_pipeline
    from .detect.classifier import Discriminator
    from .detect.scene import load_npz
    disc = Discriminator.load(args.model) if args.model else None
    if disc is None:
        print("WARNING: no --model — running CFAR-only (v1) fallback", file=sys.stderr)
    n = 0
    for f in sorted(Path(args.dir).glob("*.npz")):
        scene = load_npz(f)
        rep = det_pipeline.process_scene(scene, disc)
        print(f"{rep['scene_id']}: {rep['n_candidates']} candidates -> "
              f"{rep['n_published']} published in {rep['latency_s']:.1f}s")
        n += 1
    print(f"processed {n} scenes")


def cmd_eval_report(args):
    from .eval import harness
    runs = harness.latest_runs(args.n)
    if not runs:
        print("eval ledger empty — run tools/run_phase1_synthetic.py")
        return
    print(f"{'suite':<16}{'version':<9}{'P':>7}{'R':>7}{'F1':>7}"
          f"{'lenMAE':>8}{'FP/1e3km2':>11}  ran_at")
    for r in runs:
        print(f"{r['suite']:<16}{r['pipeline_version']:<9}"
              f"{r['precision']:>7.3f}{r['recall']:>7.3f}{r['f1']:>7.3f}"
              f"{r['length_mae_m']:>8.1f}{r['fp_per_1000km2']:>11.2f}  {r['ran_at']}")


def cmd_snapshot_sanctions(args):
    res = registries.snapshot_registry(
        args.registry, Path(args.file).read_bytes(),
        registries.parse_ofac_sdn_csv, as_of=date.fromisoformat(args.as_of))
    day = args.as_of
    out = conformed.write(res["table"], "sanctions", source=args.registry,
                          aoi=None, partition_day=day)
    print(f"{args.registry} snapshot as-of {args.as_of}: "
          f"+{res['n_added']} -{res['n_removed']} ~{res['n_changed']} -> {out}")


def cmd_build_tracks(args):
    """Phase 2: conformed ais_position → tracks, labeled gaps, encounters,
    spoof events, published back to the conformed layer + catalog."""
    import glob as _glob

    import pandas as pd

    from . import tracks as trk
    from .connectors import satais

    paths = sorted(_glob.glob(str(CONFORMED_ROOT / "ais_position" / "*" / "*.parquet")))
    if not paths:
        print("no conformed ais_position data — run ingest-ais first", file=sys.stderr)
        sys.exit(1)
    pos = pd.concat([pd.read_parquet(x) for x in paths]).drop_duplicates(
        subset=["mmsi", "ts", "lat", "lon"])
    if args.since:
        pos = pos[pos.ts >= pd.Timestamp(args.since, tz="UTC")]
    print(f"loaded {len(pos)} positions from {len(paths)} artifacts")

    sched = None
    if args.sat_passes:
        sched = trk.SatPassSchedule(
            satais.parse_pass_predictions(Path(args.sat_passes).read_bytes()))
        print(f"sat pass schedule: {len(sched.windows)} windows")

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = trk.run_track_engine(pos, source_ref="cli", sat_schedule=sched,
                               partition_day=day, aoi=AOI_V1.name)
    from collections import Counter
    kinds = Counter(g["gap_type"] for g in out["gaps"])
    print(f"tracks={len(out['tracks'])} gaps={len(out['gaps'])} {dict(kinds)}")
    print(f"encounters={len(out['encounters'])} "
          f"spoof_events={len(out['spoof_events'])}")


def cmd_dark_vessels(args):
    """THE Phase 3 demo query: 'dark vessels off Porbandar from last
    Tuesday' — against the conformed store, reproducible."""
    import glob as _glob
    import math

    import pandas as pd

    from .tracks.features import AOI_PORTS

    if args.near in AOI_PORTS:
        nlat, nlon = AOI_PORTS[args.near]
    else:
        try:
            nlat, nlon = (float(x) for x in args.near.split(","))
        except ValueError:
            print(f"unknown port {args.near!r}; known: {', '.join(AOI_PORTS)}",
                  file=sys.stderr)
            sys.exit(1)

    paths = sorted(_glob.glob(str(CONFORMED_ROOT / "dark_candidate" / "*" / "*.parquet")))
    if not paths:
        print("no dark_candidate data — run the fusion pipeline first", file=sys.stderr)
        sys.exit(1)
    df = pd.concat([pd.read_parquet(x) for x in paths]).drop_duplicates("candidate_id")

    def hav_km(la, lo):
        p1, p2 = math.radians(nlat), math.radians(la)
        dp, dl = math.radians(la - nlat), math.radians(lo - nlon)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return 2 * 6371 * math.asin(math.sqrt(a))

    df["dist_km"] = [hav_km(la, lo) for la, lo in zip(df.lat, df.lon)]
    q = df[df.dist_km <= args.radius_km]
    if args.date:
        day = pd.Timestamp(args.date, tz="UTC")
        q = q[(q.ts >= day) & (q.ts < day + pd.Timedelta("1D"))]
    if not args.all:
        q = q[q.status == "dark_candidate"]

    if q.empty:
        print(f"no dark-vessel candidates within {args.radius_km:.0f} km of "
              f"{args.near}" + (f" on {args.date}" if args.date else ""))
        return
    q = q.sort_values("dark_score", ascending=False)
    print(f"{len(q)} result(s) within {args.radius_km:.0f} km of {args.near}"
          + (f" on {args.date}" if args.date else "") + ":\n")
    for r in q.itertuples():
        print(f"  {r.ts:%Y-%m-%d %H:%M}Z  ({r.lat:7.3f},{r.lon:8.3f})  "
              f"{r.dist_km:5.0f} km  len≈{r.length_m:5.1f} m  "
              f"score={r.dark_score:.2f}  hearable={r.hearable_conf:.2f}  "
              f"{r.status}  scene={r.scene_id}")


def cmd_graph_query(args):
    """Vessel neighborhood dump: identity history, ownership, sanctions
    proximity, evidence edges — the entity page in text form."""
    from .config import DATA_ROOT, GRAPH_DB_NAME
    from .graph import GraphStore, current_mmsi, resolve_mmsi

    g = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    vid = resolve_mmsi(g, args.mmsi)
    node = g.node(vid)
    print(f"{vid}  props: {json.dumps(node['props'], default=str)[:300]}")
    for direction, label in (("out", "->"), ("in", "<-")):
        for e in sorted(g.edges(vid, direction=direction),
                        key=lambda e: e.edge_type):
            other = e.dst if direction == "out" else e.src
            scope = "" if e.t_end is None else " [CLOSED]"
            print(f"  {e.edge_type:>18} {label} {other:<40} "
                  f"conf={g.edge_confidence(e):.2f}{scope} src={e.source}")
    g.close()


def cmd_alerts(args):
    """Alert queue with full evidence chains."""
    from .config import DATA_ROOT, GRAPH_DB_NAME
    from .graph import GraphStore, current_mmsi

    g = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    for a in g.alerts():
        m = current_mmsi(g, a["subject"], a["ts"])
        print(f"[{a['rule']}] mmsi {m}  conf={a['confidence']:.2f}  "
              f"({a['disposition']})")
        for c in a["evidence"]:
            print(f"    {c['src']} -[{c['edge']} {c['confidence']:.2f}]-> "
                  f"{c['dst']}   ({c['source']})")
    g.close()


def cmd_anomalies(args):
    """Anomaly alert queue, optionally filtered by type, with dispositions."""
    from .config import DATA_ROOT, GRAPH_DB_NAME
    from .graph import GraphStore, current_mmsi
    g = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    for a in g.alerts(anomaly_type=args.type):
        subj = a["subject"]
        m = current_mmsi(g, subj, a["ts"]) if subj.startswith("vessel") else None
        print(f"[{a['anomaly_type']}] {subj}"
              + (f" (mmsi {m})" if m else "")
              + f"  score={a['score']:.2f}  {a['disposition']}")
    g.close()


def cmd_risk(args):
    """Ranked vessel risk with the decomposition (explainable by design)."""
    import time
    from .config import DATA_ROOT, GRAPH_DB_NAME
    from .graph import GraphStore
    from .anomaly import rank_vessels, risk_score
    g = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    if args.mmsi:
        from .graph import resolve_mmsi
        rs = risk_score(g, resolve_mmsi(g, args.mmsi))
        print(f"{rs['vessel']}  risk={rs['risk_score']:.3f}")
        for k, c in rs["components"].items():
            print(f"  {k:>22}  {c['value']:.2f} × {c['weight']:.2f} = {c['weighted']:.3f}")
        for e in rs["evidence"]:
            print(f"    - {e['kind']}: {e['detail']} ({e['contribution']})")
    else:
        for r in rank_vessels(g, top=args.top):
            print(f"  {r['risk_score']:.3f}  {r['vessel']}")
    g.close()


def cmd_feedback(args):
    """Disposition tally + a proposed retune for one detector."""
    from .config import DATA_ROOT, GRAPH_DB_NAME
    from .graph import GraphStore
    from .anomaly import feedback_summary, propose_retune
    g = GraphStore(DATA_ROOT / GRAPH_DB_NAME)
    for atype, s_ in feedback_summary(g).items():
        rp = f"{s_['realized_precision']:.0%}" if s_['realized_precision'] is not None else "-"
        print(f"  {atype:>22}: {s_['n']:3d} disp "
              f"(✓{s_['confirm']} ✗{s_['dismiss']} ~{s_['watch']})  "
              f"realized_prec={rp}  thr={s_['threshold']:.2f}")
    if args.retune:
        r = propose_retune(g, args.retune)
        if r:
            print(f"retune [{args.retune}]: thr {r.old_threshold:.2f}->{r.new_threshold:.2f}, "
                  f"precision {r.precision_before:.0%}->{r.precision_after:.0%} "
                  f"({r.precision_delta:+.0%})")
        else:
            print(f"retune [{args.retune}]: not enough dispositions yet")
    g.close()


def cmd_status(args):
    with cat.connect() as con:
        print(f"pipeline {PIPELINE_VERSION} | AOI {AOI_V1.name}")
        print("\nscene catalog:")
        for r in cat.coverage_summary(con):
            print(f"  {r['status']:<12} {r['n']:>5}  {r['t0']} .. {r['t1']}")
        print("\nconformed artifacts:")
        for r in con.execute(
                """SELECT source, COUNT(*) n, MIN(t_start) t0, MAX(t_end) t1
                   FROM artifacts WHERE kind='conformed' GROUP BY source"""):
            print(f"  {r['source']:<32} {r['n']:>4} files  {r['t0']} .. {r['t1']}")
        print("\nregistry snapshots:")
        for r in con.execute(
                "SELECT registry, as_of, n_records, n_added, n_removed FROM registry_snapshots ORDER BY as_of"):
            print(f"  {r['registry']:<12} as-of {r['as_of']}  n={r['n_records']} "
                  f"(+{r['n_added']}/-{r['n_removed']})")


def main():
    ap = argparse.ArgumentParser(prog="maritime_isr")
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("backfill-scenes")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--download", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--token", default=None)
    p.set_defaults(fn=cmd_backfill_scenes)

    p = sub.add_parser("ingest-ais")
    p.add_argument("file")
    p.add_argument("--receiver", required=True)
    p.add_argument("--format", choices=["nmea", "json"], default="nmea")
    p.add_argument("--aoi-filter", action="store_true", default=True)
    p.set_defaults(fn=cmd_ingest_ais)

    p = sub.add_parser("snapshot-sanctions")
    p.add_argument("file")
    p.add_argument("--registry", default="ofac_sdn")
    p.add_argument("--as-of", required=True)
    p.set_defaults(fn=cmd_snapshot_sanctions)

    p = sub.add_parser("process-scenes")
    p.add_argument("dir")
    p.add_argument("--model", default=None)
    p.set_defaults(fn=cmd_process_scenes)

    p = sub.add_parser("build-tracks")
    p.add_argument("--since", default=None, help="ISO date lower bound")
    p.add_argument("--sat-passes", default=None,
                   help="pass-prediction JSON for the satellite feed")
    p.set_defaults(fn=cmd_build_tracks)

    p = sub.add_parser("dark-vessels")
    p.add_argument("--near", required=True,
                   help="port name (e.g. Porbandar) or 'lat,lon'")
    p.add_argument("--date", default=None, help="UTC day, YYYY-MM-DD")
    p.add_argument("--radius-km", type=float, default=200.0)
    p.add_argument("--all", action="store_true",
                   help="include suppressed verdicts (why-not-dark view)")
    p.set_defaults(fn=cmd_dark_vessels)

    p = sub.add_parser("graph-query")
    p.add_argument("--mmsi", type=int, required=True)
    p.set_defaults(fn=cmd_graph_query)

    p = sub.add_parser("alerts")
    p.set_defaults(fn=cmd_alerts)

    p = sub.add_parser("anomalies")
    p.add_argument("--type", default=None)
    p.set_defaults(fn=cmd_anomalies)

    p = sub.add_parser("risk")
    p.add_argument("--mmsi", type=int, default=None)
    p.add_argument("--top", type=int, default=15)
    p.set_defaults(fn=cmd_risk)

    p = sub.add_parser("feedback")
    p.add_argument("--retune", default=None, help="anomaly type to propose a retune for")
    p.set_defaults(fn=cmd_feedback)

    p = sub.add_parser("eval-report")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(fn=cmd_eval_report)

    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
