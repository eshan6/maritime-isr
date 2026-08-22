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


def cmd_zones(args):
    """Build and land the operational zone layer, and report what is missing.

    `build` is idempotent — the zone ids are derived from kind and name, not
    from geometry, so re-running after a coordinate is corrected updates the
    row in place and every stored transition still points at the same zone.
    """
    from .zones import (STATUTORY_KINDS, ZoneIndex, build_operational_zones,
                        land_zones, load_zones)
    from .zones.analyses import anchoring_analysis_status

    if args.action == "build":
        from .zones.store import clear_standing_zones
        # Build FIRST, then clear exactly the ids we are about to write.
        # Clearing by "everything not drawn by the operator" would delete an
        # imported territorial sea — a published boundary this project cannot
        # regenerate. See `clear_standing_zones`.
        zones = build_operational_zones()
        dropped = clear_standing_zones({z.zone_id for z in zones})
        if dropped:
            print(f"  replaced {dropped:,} row(s) of the previous standing set "
                  f"(imported boundaries and drawn areas untouched)")
        written = land_zones(zones)
        for table, days in sorted(written.items()):
            print(f"  landed {sum(days.values()):,} row(s) into {table}")

    zones = load_zones()
    if not zones:
        print("no landed zone layer — run `maritime-isr zones build`",
              file=sys.stderr)
        return 1
    from collections import Counter
    counts = Counter(z.kind for z in zones)
    print()
    print(f"maritime zone layer — {len(zones)} zone(s)")
    for kind, n in sorted(counts.items()):
        print(f"  {kind:<18}{n:>5}")

    missing = sorted(STATUTORY_KINDS - set(counts))
    if missing:
        print()
        print("NOT PRESENT, and not derived on purpose:")
        for kind in missing:
            print(f"  {kind}")
        print("  These are statutory limits. This project will not compute or "
              "transcribe them\n  (zones/derive.py explains why). Load a real "
              "file:\n"
              "    maritime-isr ingest zones --path <file.geojson> --kind "
              "territorial_sea")
    ok, why = anchoring_analysis_status(ZoneIndex(zones))
    if not ok:
        print()
        print(f"  anchored_outside_limits: {why}")
    return 0


def cmd_radar(args):
    """Coastal radar: correlate the picture against AIS, and report.

    `maritime-isr radar correlate` runs the whole radar path over whatever is
    landed and prints the correlation and dark-contact summary. It writes
    nothing: this is a measurement over a corpus that already exists, and
    publishing fusion outputs would put derived rows in the conformed layer
    that the next run would read back.
    """
    import pandas as pd

    from .fusion.radar_ais import correlate_radar, format_correlation
    from .ingest.landing import read_table, split_real_synthetic
    from .ingest.radar import TABLE as RADAR_TABLE
    from .schemas.sources import AIS, RADAR
    from .tracks import build_tracks
    from .tracks.coverage import CoverageModel, classify_gaps

    rad = read_table(RADAR_TABLE)
    if not rad:
        print(f"no landed {RADAR_TABLE} data — generate the scenario corpus "
              f"(`maritime-isr scenario generate`) or land a station feed "
              f"(`maritime-isr ingest radar --path <file>`)", file=sys.stderr)
        return 1
    pos = read_table("ais_position")
    if not pos:
        print("no landed ais_position data — radar cannot be correlated "
              "against nothing, and every contact would be 'dark' by "
              "construction", file=sys.stderr)
        return 1

    real_r, syn_r = split_real_synthetic(rad)
    real_a, syn_a = split_real_synthetic(pos)
    print(f"radar_track_report : {len(real_r):,} real + {len(syn_r):,} synthetic")
    print(f"ais_position       : {len(real_a):,} real + {len(syn_a):,} synthetic")

    dfa = pd.DataFrame(pos)
    dfa["ts"] = pd.to_datetime(dfa["ts"], utc=True)
    for col, default in (("sog_kn", 0.0), ("cog_deg", 0.0), ("receiver", "")):
        if col not in dfa.columns:
            dfa[col] = default
    dfa = dfa.sort_values("ts").reset_index(drop=True)
    dfr = pd.DataFrame(rad)
    dfr["ts"] = pd.to_datetime(dfr["ts"], utc=True)
    dfr = dfr.sort_values("ts").reset_index(drop=True)

    ais_tracks, spoofs = build_tracks(dfa, source=AIS)
    radar_tracks, _ = build_tracks(dfr, source=RADAR)
    model = CoverageModel(dfa["ts"].min().timestamp()).fit(dfa)
    spoof_win: dict[int, list] = {}
    for s_ in spoofs:
        if s_["event_type"] == "DUPLICATE_MMSI":
            spoof_win.setdefault(s_["mmsi"], []).append(
                (s_["t_start"].timestamp(), s_["t_end"].timestamp()))
    gaps = []
    for tr in ais_tracks:
        gaps.extend(classify_gaps(tr, model, spoof_win.get(tr.mmsi)))

    registry: dict[int, float] = {}
    for r in read_table("gfw_vessel_identity"):
        m, L = r.get("mmsi"), r.get("length_m")
        if m in (None, "") or L in (None, ""):
            continue
        try:
            registry[int(float(m))] = float(L)
        except (TypeError, ValueError):
            continue

    out = correlate_radar(radar_tracks, ais_tracks, model, registry,
                          spoof_events=spoofs, ais_gaps=gaps)
    print()
    print(format_correlation(out))

    if getattr(args, "write", False):
        from .fusion.radar_ais import land_correlation
        from .ingest.landing import SYNTHETIC_SOURCE_ID
        syn = bool(syn_r) and not real_r
        written = land_correlation(
            out,
            source_id=SYNTHETIC_SOURCE_ID if syn else "coastal_radar",
            is_synthetic=syn)
        print()
        for table, n in sorted(written.items()):
            print(f"  landed {n:,} row(s) into {table}")

    darks = [v for v in out.verdicts if v["status"] == "dark_candidate"]
    if darks:
        print()
        print(f"{len(darks)} dark contact(s):")
        for v in sorted(darks, key=lambda v: -v["dark_score"]):
            c = next((c for c in out.correlations
                      if c["correlation_id"] == v.get("correlation_id")), {})
            print(f"  {v['ts']:%Y-%m-%d %H:%M}Z  ({v['lat']:7.3f},{v['lon']:8.3f})  "
                  f"len≈{v['length_m'] or 0:5.1f} m  score={v['dark_score']:.2f}  "
                  f"{c.get('dark_minutes', 0):.0f} min unexplained  "
                  f"[{c.get('station_ids', '?')}]")
            if c.get("went_dark_at") is not None:
                print(f"      last explained by MMSI {c.get('mmsi')} at "
                      f"{c['went_dark_at']:%Y-%m-%d %H:%M}Z "
                      f"({c['went_dark_lat']}, {c['went_dark_lon']})")
    print()
    print("SYNTHETIC unless the split above says otherwise. No Coastal "
          "Surveillance Network feed has ever been seen by this system.")
    return 0


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


def cmd_graph_populate(args):
    """Fill the graph from landed real data and report its connectivity.

    Thin wrapper over tools/graph_report.py so the documented CLI shape works;
    the tool stays runnable on its own for ad-hoc use.
    """
    import runpy
    import sys as _sys
    from pathlib import Path as _Path

    argv = []
    if args.report_only:
        argv.append("--report-only")
    if args.all_gaps:
        argv.append("--all-gaps")
    script = _Path(__file__).resolve().parent.parent / "tools" / "graph_report.py"
    old = _sys.argv
    _sys.argv = [str(script), *argv]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as e:
        return e.code or 0
    finally:
        _sys.argv = old
    return 0


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


def cmd_baselines(args):
    """Derive or show the per-area baseline layer.

    The whole point of this layer is that it is *inspectable* — the requirement
    asks for a maintained artifact rather than a constant, and an artifact
    nobody can print is indistinguishable from a constant.
    """
    import pandas as pd

    from . import baselines as bl
    from .api.reader import open_reader
    from .config import CLI as _CLI

    if args.action == "derive":
        with open_reader() as reader:
            if not reader.has("ais_position"):
                raise SystemExit(
                    f"no landed AIS positions to derive from — run "
                    f"`{_CLI} scenario generate` or land a real corpus first")
            pos = pd.DataFrame(reader.rows(
                "SELECT vessel_id, mmsi, lat, lon, sog_kn, cog_deg, ts, "
                "is_synthetic FROM ais_position"))
        derived = bl.derive_baselines(pos)
        n = bl.land_baselines(derived)
        cov = bl.BaselineIndex(derived).coverage()
        print(f"derived from {len(pos):,} position(s)")
        print(f"  landed        : {n:,} cell(s) at H3 res {cov['res']}")
        print(f"  usable        : {cov['usable']:,} "
              f"({cov['fraction_usable']:.0%})")
        print(f"  insufficient  : {cov['insufficient']:,} "
              f"(under {cov['min_observations']} observations)")
        print(f"\n  {_CLI} baselines show   to read them")
        return

    rows = bl.load_baselines()
    if not rows:
        raise SystemExit(f"no landed baselines — run `{_CLI} baselines derive`")
    index = bl.BaselineIndex(rows)
    cov = index.coverage()
    print(f"per-area baselines — {cov['usable']:,} usable of {cov['cells']:,} "
          f"cell(s) at H3 res {cov['res']}\n")
    usable = sorted(index.usable(), key=lambda b: -b.n_observations)
    print(f"{'cell':<17}{'lat':>8}{'lon':>8}{'obs':>9}{'ships':>7}"
          f"{'p50':>7}{'p95':>7}{'p99':>7}{'ships/day':>11}")
    for b in usable[:args.top]:
        m = b.metrics.get("sog_kn", {})
        print(f"{b.h3_cell:<17}{b.lat:>8.2f}{b.lon:>8.2f}"
              f"{b.n_observations:>9,}{b.n_vessels:>7}"
              f"{m.get('p50', 0):>7.1f}{m.get('p95', 0):>7.1f}"
              f"{m.get('p99', 0):>7.1f}"
              f"{(b.vessels_per_day if b.vessels_per_day is not None else 0):>11.1f}")
    print(f"\n  Speeds are knots. A cell under {cov['min_observations']} "
          f"observations reports no distribution at all — a percentile over a "
          f"handful of\n  points is noise wearing an authoritative face, and "
          f"`is_unusual` answers 'cannot say' there rather than 'normal'.")


def cmd_voi(args):
    """The MDA assistant from a terminal — the same code the API serves.

    Deliberately the same functions rather than a second assembly path: a CLI
    that computed the list its own way would eventually disagree with the
    screen, and the disagreement would surface in front of whoever was being
    shown the demo.
    """
    from . import assistant
    from .config import CLI as _CLI

    if args.action == "workload":
        w = assistant.workload()
        print(f"corpus : {w['corpus']}")
        for k, v in w["inputs"].items():
            print(f"  in   {k:<28}{v:>10,}")
        for k, v in w["outputs"].items():
            print(f"  out  {k:<28}{v:>10,}")
        print()
        print(f"  {w['statement']}")
        print(f"\n  {w['caveat']}")
        return

    if args.action == "list":
        res = assistant.build_list(limit=args.top)
        c = res["count"]
        print(f"ranked Vessels of Interest — {c['real']} real, "
              f"{c['synthetic']} scenario, {res['n_suppressed']} suppressed "
              f"below {res['min_score']:.2f}\n")
        for it in res["items"]:
            syn = " [SYN]" if it["is_synthetic"] else ""
            print(f"{it['rank']:>3}. {it['score']:.3f}  "
                  f"{it['display_name'][:44]:<46}{syn}")
            for f in it["factors"]:
                print(f"        {f['points']:.3f}  {f['kind']:<24}"
                      f"conf {f['confidence']:.2f}  ({f['family']})")
        print()
        for n in res["notes"]:
            print(f"  * {n}")
        for n in res["queue_health"]["notes"]:
            print(f"  ! {n}")
        print(f"\n  {_CLI} voi show <subject-id>   for the full account")
        return

    if not args.subject:
        raise SystemExit(f"`{_CLI} voi {args.action}` needs a subject id — "
                         f"run `{_CLI} voi list` to see them")

    if args.action == "show":
        v = assistant.build_one(args.subject)
        if v is None:
            raise SystemExit(f"no subject {args.subject!r} on the list")
        print(f"{v['display_name']}   score {v['score']:.3f}"
              + ("   [SCENARIO DATA]" if v["is_synthetic"] else ""))
        print("-" * 72)
        print(v["account"])
        print("\nwhy:")
        for line in v["account_lines"]:
            print(f"  - {line}")
        print("\nthe sum:")
        for r in v["arithmetic"]["rows"]:
            print(f"  {r['points']:.3f}  {r['kind']:<24}"
                  f"weight {r['weight']:.2f} x conf {r['confidence']:.2f} "
                  f"= {r['standalone']:.2f} standalone  "
                  f"({r['share_pct']:.0f}% of the score)")
        print(f"  {v['arithmetic']['sum_of_points']:.3f}  "
              f"TOTAL (score {v['arithmetic']['score']:.3f}, "
              f"reconciles: {v['arithmetic']['reconciles']})")
        print("\nwhat to do next — the system proposes, you decide:")
        for r in v["recommendations"]:
            mark = "" if r["feasible"] else "  [NOT AVAILABLE]"
            print(f"  - {r['headline']}{mark}")
            print(f"      {r['rationale']}")
            if r["feasibility"]:
                print(f"      {r['feasibility']}")
            print(f"      system can do this: {r['system_capability']}")
        if args.evidence:
            print("\nevidence:")
            for f in v["factors"]:
                print(f"  {f['kind']}:")
                for e in f["evidence"]:
                    print(f"    - {e['label']}")
                    print(f"        source {e['provenance'].get('source_id')} "
                          f"ref {e['provenance'].get('source_ref')}")
        print("\nnot known about this subject:")
        for n in v["not_known"]:
            print(f"  - {n}")
        return

    if args.action == "ask":
        if not args.question:
            raise SystemExit(f"`{_CLI} voi ask <subject> --question '...'`")
        a = assistant.ask(args.subject, args.question)
        if a is None:
            raise SystemExit(f"no subject {args.subject!r} on the list")
        print(f"Q: {a['question']}")
        print(f"   [{a['outcome']}]"
              + (f" intent={a['intent']}" if a["intent"] else ""))
        print()
        for line in a["text"].splitlines():
            print(f"   {line}")
        if a["basis"]:
            print(f"\n   read: {', '.join(a['basis'])}")
        if a["suggestions"]:
            print("\n   what I can answer about this subject:")
            for s in a["suggestions"]:
                print(f"     - {s}")


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


def cmd_scenario(args):
    """Generate, clear or report the synthetic scenario corpus (ADR-019).

    Scenario data lands in the SAME tables as real data, flagged is_synthetic,
    so it exercises the identical code path. Every count this prints is split
    real versus synthetic — a blended total is never printed, because any
    number that could be quoted externally has to be splittable.
    """
    from .scenario import (clear, format_generation, format_status, generate,
                           status)
    if args.action == "generate":
        res = generate(seed=args.seed)
        print(format_generation(res))
        if not res.validation.ok:
            print(f"\nVALIDATION FAILED: {len(res.validation.violations)} "
                  f"violation(s). The corpus was landed but must not be "
                  f"trusted until these are fixed.", file=sys.stderr)
            return 1
        return 0
    if args.action == "clear":
        removed = clear()
        if not removed:
            print("no synthetic rows found")
            return 0
        for table, n in sorted(removed.items()):
            print(f"  removed {n:>8,} synthetic row(s) from {table}")
        print(f"total {sum(removed.values()):,} row(s) removed")
        return 0
    if args.action == "status":
        print(format_status(status()))
        return 0
    raise SystemExit(f"unknown scenario action {args.action!r}")


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




# =====================================================================
# Live-data path commands (execution-spec units 0.0-0.5). These need
# real credentials (see `maritime-isr config`) and run on the deploy
# host; they are inert without env vars, by design.
# =====================================================================

def cmd_live_config(args):
    from .config import _main
    return _main()


def cmd_live_doctor(args):
    """Default: check THIS laptop can run download-only mode.

    The SNAP/pyroSAR checks are parked (no deploy host) and now live behind
    `--snap`, so a laptop run is not drowned in failures about a toolchain it
    is not supposed to have.
    """
    from .infra.laptop_doctor import run
    return run(snap=getattr(args, "snap", False))


def cmd_live_preprocess(args):
    from .process.s1_preprocess import run
    return run(limit=args.limit)


def cmd_live_validate(args):
    from .process.validate_sigma0 import run
    return run(limit=args.limit)


def cmd_overpass(args):
    """Which AIS gaps a Sentinel-1 pass could have imaged.

    Its own verb rather than an `ingest` subcommand because it fetches nothing
    — it reads two tables already landed and produces a third.
    """
    from .overpass import V_MAX_DEFAULT_KN, run
    return run(v_max_kn=args.v_max if args.v_max is not None else V_MAX_DEFAULT_KN,
               flagged_only=not args.all_gaps)


def cmd_live_ingest(args):
    if args.source == "s1":
        from .ingest.copernicus import run
        return run(days=args.days, catalog_only=args.catalog_only)
    if args.source == "ais":
        from .ingest.aisstream import run
        return run(max_hours=args.hours)
    if args.source == "gfw":
        from .ingest.gfw import run
        return run(weeks=args.weeks, resolution=args.resolution)
    if args.source == "gfw-sar-csv":
        from .ingest.gfw import import_portal_csv
        return import_portal_csv(args.path)
    if args.source == "gfw-events":
        from .ingest.gfw_events import run
        return run(kind=args.kind, weeks=args.weeks)
    if args.source == "gfw-vessels":
        from .ingest.gfw_vessels import run
        return run(limit=args.limit)
    if args.source == "sanctions-match":
        from .ingest.sanctions_match import REGISTRY_ORDER, run
        regs = (tuple(r.strip().upper() for r in args.registries.split(","))
                if args.registries else REGISTRY_ORDER)
        return run(registries=regs)
    if args.source == "registries":
        from .ingest.registries import run
        return run(only=args.only, path=args.path)
    if args.source == "noaa":
        from .ingest.noaa_ais import run
        return run(month=args.month)
    if args.source == "radar":
        from .ingest.radar import run
        return run(args.path, station_id=args.station)
    if args.source == "zones":
        from .ingest.zones import run
        return run(args.path, kind=args.kind, authority=args.authority,
                   clip_to_aoi=not args.no_clip)
    raise SystemExit(f"unknown ingest source {args.source!r}")

def build_parser() -> argparse.ArgumentParser:
    """Every verb this CLI accepts, with no dispatch attached.

    Split out of `main` so the parser can be asked what it accepts without
    running anything. Several operator-facing messages tell someone to run a
    specific command — the Radar view's "run `maritime-isr radar correlate
    --write`" is the one that matters most, because it is the only thing
    between an empty panel and the conclusion that the product is broken. A
    test parses those hints against this, so a renamed verb fails here instead
    of sending an operator to a command that no longer exists.
    """
    ap = argparse.ArgumentParser(prog="maritime_isr")
    sub = ap.add_subparsers(required=True)

    # ---- live-data path (execution-spec) ----
    p = sub.add_parser("config", help="print resolved live config + env check")
    p.set_defaults(fn=cmd_live_config)

    p = sub.add_parser("doctor", help="check this machine can run download-only mode")
    p.add_argument("--snap", action="store_true",
                   help="instead run the PARKED SNAP/pyroSAR checks (deploy host only)")
    p.set_defaults(fn=cmd_live_doctor)

    p = sub.add_parser("preprocess", help="0.2: SNAP chain raw->calibrated sigma0 COG")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(fn=cmd_live_preprocess)

    p = sub.add_parser("validate", help="0.2 exit test: sigma0 dB-range sanity")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(fn=cmd_live_validate)

    p = sub.add_parser(
        "overpass",
        help="which AIS gaps a Sentinel-1 pass could have imaged (no pixels needed)")
    p.add_argument("--v-max", type=float, default=None,
                   help="assumed top vessel speed in knots (default 20, "
                        "deliberately generous — a higher value makes a "
                        "'confirmed' containment harder to claim, not easier)")
    p.add_argument("--all-gaps", action="store_true",
                   help="assess every landed AIS gap, not only those GFW "
                        "flagged as intentional disabling")
    p.set_defaults(fn=cmd_overpass)

    p = sub.add_parser("ingest", help="run a live source connector")
    ing = p.add_subparsers(dest="source", required=True)
    ps1 = ing.add_parser("s1"); ps1.add_argument("--days", type=int, default=90); ps1.add_argument("--catalog-only", action="store_true")
    pais = ing.add_parser("ais"); pais.add_argument("--hours", type=float, default=None)
    pgfw = ing.add_parser("gfw", help="GFW gridded SAR presence (AGGREGATE counts, not contacts)")
    pgfw.add_argument("--weeks", type=int, default=8)
    pgfw.add_argument("--resolution", choices=["LOW", "HIGH"], default="HIGH")

    pcsv = ing.add_parser("gfw-sar-csv", help="land a per-detection SAR CSV from the GFW portal")
    pcsv.add_argument("--path", required=True, help="path to the downloaded CSV")

    pev = ing.add_parser("gfw-events", help="GFW encounters/loitering/port visits/AIS gaps")
    pev.add_argument("--kind", choices=["encounters", "loitering", "port_visits", "gaps"],
                     default=None, help="default: all four")
    pev.add_argument("--weeks", type=int, default=8)

    pves = ing.add_parser("gfw-vessels", help="identity for vessels seen in the event tables")
    pves.add_argument("--limit", type=int, default=None)

    psm = ing.add_parser(
        "sanctions-match",
        help="match identified vessels against OFAC/UN/EU sanctioned hulls (ADR-016a)")
    psm.add_argument("--registries", default=None,
                     help="comma list of OFAC,UN,EU — default is all three. "
                          "Use --registries OFAC to reproduce a pre-UN/EU run.")

    preg = ing.add_parser("registries", help="OFAC SDN, UN, EU sanctions + WPI ports")
    preg.add_argument("--only", choices=["ofac", "un", "eu", "wpi"], default=None,
                      help="refresh just one source; default is all four")
    preg.add_argument("--path", default=None,
                      help="import a hand-downloaded file instead of fetching "
                           "(WPI only, for when NGA is down)")
    pnoaa = ing.add_parser("noaa"); pnoaa.add_argument("--month", required=True)

    prad = ing.add_parser(
        "radar",
        help="land a coastal-radar track feed (CSV or newline JSON). "
             "UNTESTED against any real system — no such feed is available to "
             "this project; see ingest/radar.py")
    prad.add_argument("--path", required=True)
    prad.add_argument("--station", default=None,
                      help="station id, when the feed omits it")

    pzon = ing.add_parser(
        "zones",
        help="land maritime boundary geometry from a GeoJSON file. THE ONLY "
             "way an EEZ, contiguous zone, territorial sea or IMBL enters "
             "this system — see zones/derive.py for why they are not derived")
    pzon.add_argument("--path", required=True, help="a .geojson file")
    pzon.add_argument("--kind", default=None,
                      help="force a kind for features that do not declare one "
                           "(eez, contiguous_zone, territorial_sea, imbl, ...)")
    pzon.add_argument("--authority", default=None,
                      help="who published it; defaults to the filename")
    pzon.add_argument("--no-clip", action="store_true",
                      help="keep geometry outside AOI v1 (default clips)")
    p.set_defaults(fn=cmd_live_ingest)


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

    p = sub.add_parser(
        "radar",
        help="coastal radar: correlate the picture against AIS and report "
             "the dark contacts (ADR-028)")
    p.add_argument("action", choices=["correlate"], nargs="?",
                   default="correlate")
    p.add_argument("--write", action="store_true",
                   help="land the correlation and the contacts, so the API and "
                        "the map can show them without re-running it")
    p.set_defaults(fn=cmd_radar)

    p = sub.add_parser(
        "zones",
        help="build/inspect the maritime zone layer (ADR-030)")
    p.add_argument("action", choices=["build", "status"], nargs="?",
                   default="status")
    p.set_defaults(fn=cmd_zones)

    p = sub.add_parser("graph-query")
    p.add_argument("--mmsi", type=int, required=True)
    p.set_defaults(fn=cmd_graph_query)

    p = sub.add_parser("graph-populate",
                       help="build the graph from landed real data, then report it")
    p.add_argument("--report-only", action="store_true",
                   help="report the graph as it stands, write nothing")
    p.add_argument("--all-gaps", action="store_true",
                   help="include AIS gaps GFW did NOT flag as intentional")
    p.set_defaults(fn=cmd_graph_populate)

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

    p = sub.add_parser("scenario",
                       help="synthetic scenario corpus: generate / clear / status")
    p.add_argument("action", choices=["generate", "clear", "status"])
    p.add_argument("--seed", type=int, default=7,
                   help="generation seed; the same seed reproduces the corpus")
    p.set_defaults(fn=cmd_scenario)

    p = sub.add_parser(
        "voi", help="the MDA assistant: ranked Vessels of Interest (ADR-031)")
    p.add_argument("action", choices=["list", "show", "ask", "workload"],
                   help="list the queue, open one subject, ask a question "
                        "about one, or print the workload reduction")
    p.add_argument("subject", nargs="?", default=None,
                   help="subject id, for `show` and `ask`")
    p.add_argument("--question", default=None, help="for `ask`")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--evidence", action="store_true",
                   help="with `show`: print every evidence item, not a count")
    p.set_defaults(fn=cmd_voi)

    p = sub.add_parser(
        "baselines",
        help="per-area behavioural baselines: what normal looks like where "
             "(ADR-032)")
    p.add_argument("action", choices=["derive", "show"],
                   help="derive from landed positions and land the result, "
                        "or show the landed snapshot")
    p.add_argument("--top", type=int, default=20)
    p.set_defaults(fn=cmd_baselines)

    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)

    return ap


def main():
    args = build_parser().parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
