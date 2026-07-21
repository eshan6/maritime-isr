"""Phase 5 end-to-end exercise — the anomaly library turns on.

  upstream chain (Phases 2-4, in memory) → object graph
  + Phase 5 scenario injects (sensitive-zone loiter, Karachi caller,
    reflag-then-dark sequence) so all six detectors have signal
  → run the six anomaly detectors → alerts (scored, evidenced, gated)
  → simulate analyst dispositions → feedback retune with measured delta
  → composite risk ranking (explainable) → weekly anomaly summary
  → eval → ledger → dashboard snapshot

Everything SYNTHETIC. The dispositions are simulated from ground truth to
exercise the loop; on the deploy host they come from real analysts, and
that is precisely the proprietary asset this phase stands up.
"""
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr import anomaly, fusion, graph, tracks as trk
from maritime_isr.config import AOI_V1, DATA_ROOT, GRAPH_DB_NAME
from maritime_isr.connectors import ais as ais_conn, satais
from maritime_isr.eval.anomaly import evaluate_anomalies, record_to_ledger

DATA = Path(__file__).resolve().parent.parent / "data"
rng = random.Random(31)

# ---- 1. upstream chain, in memory ----------------------------------------
payload = (DATA / "synthetic_ais_30d.nmea").read_bytes()
parser = ais_conn.AivdmParser("multi", aoi=AOI_V1)
messages = []
for line in payload.decode().splitlines():
    ts_s, rx, sentence = line.split("\t")
    parser.receiver = rx
    m = parser.feed(sentence, datetime.fromisoformat(ts_s))
    if m and m["msg_type"] != 5:
        messages.append(m)
pos = ais_conn.conform(messages, source="ais:multi", source_ref="p5").to_pandas()
sched = trk.SatPassSchedule(satais.parse_pass_predictions(
    (DATA / "synthetic_sat_passes.json").read_bytes()))
eng = trk.run_track_engine(pos, source_ref="p5", sat_schedule=sched,
                           partition_day="p5", aoi=AOI_V1.name, write_outputs=False)
scenes = json.loads((DATA / "synthetic_scenes_phase3.json").read_text())
registry = {int(k): v for k, v in json.loads(
    (DATA / "synthetic_registry.json").read_text()).items()}
fus = fusion.run_fusion(scenes, eng["tracks"], eng["coverage_model"], registry,
                        gaps=eng["gaps"], spoof_events=eng["spoof_events"],
                        source_ref="p5", partition_day="p5",
                        aoi=AOI_V1.name, write_outputs=False)

# ---- 2. build the graph (Phase 4) ----------------------------------------
db = DATA_ROOT / GRAPH_DB_NAME
if db.exists():
    db.unlink()
g = graph.GraphStore(db)
graph.ensure_world(g)
for snap_file in ("synthetic_registry_v1.json", "synthetic_registry_v2.json"):
    graph.fold_registry_snapshot(g, json.loads((DATA / snap_file).read_text()),
                                 source_ref=snap_file)
graph.ingest_ownership(g, json.loads((DATA / "synthetic_ownership.json").read_text()),
                       source_ref="own", as_of=pd.Timestamp("2026-06-15", tz="UTC").timestamp())
graph.ingest_sanctions(g, json.loads(
    (DATA / "synthetic_sanctions_phase4.json").read_text()), source_ref="sanc")
graph.ingest_tracks(g, eng["tracks"], source_ref="p5")
graph.ingest_encounters(g, eng["encounters"], source_ref="p5")
graph.ingest_fusion(g, fus["associations"], fus["verdicts"], source_ref="p5")
graph.process_events(g)   # Phase 4 rules populate identity_changed events

# ---- 2b. Phase 5 scenario injects (graph-level, feed untouched) ----------
# a sensitive-zone loiter track and a Karachi port-caller, as synthetic
# BuiltTrack-shaped inputs the loiter/port detectors read via features
from maritime_isr.anomaly.library import SENSITIVE_ZONES, HIGH_RISK_PORTS
from maritime_isr.tracks.builder import build_tracks
T0 = pd.Timestamp("2026-06-15", tz="UTC")

def _mk(mmsi, pts):
    df = pd.DataFrame([dict(mmsi=mmsi, lat=la, lon=lo, sog_kn=sg, cog_deg=90.0,
                            heading_deg=90.0, nav_status=0, msg_type=1,
                            ts=T0 + pd.Timedelta(minutes=6 * i), h3_cell="",
                            receiver="ter:inj", n_receipts=1)
                       for i, (la, lo, sg) in enumerate(pts)])
    built, _ = build_tracks(df)
    return built[0]

z = SENSITIVE_ZONES[0]   # Mumbai High oil field
loiter_pts = [(z["lat"] + 0.01, z["lon"], 0.4)] * 40          # 4 h still, in-zone
karachi_pts = [(24.79, 66.98, 0.3)] * 30                      # berthed at Karachi
inj_tracks = [_mk(419800001, loiter_pts), _mk(419800002, karachi_pts)]
graph.ingest_tracks(g, inj_tracks, source_ref="p5inj")
all_tracks = eng["tracks"] + inj_tracks

# dark rendezvous inject: a real encounter where one party is AIS-silent.
# We add the encounter to the graph inputs and mark the counterpart dark by
# planting a dark detection inside its footprint (the ship-to-ship signature
# the scripted feed deliberately lacks — its 3 rendezvous are both-on).
dark_rdv_encounter = dict(
    encounter_id="DRDV1", mmsi_a=419100005, mmsi_b=419900001,
    t_start=pd.Timestamp("2026-06-21 08:00", tz="UTC"),
    t_end=pd.Timestamp("2026-06-21 08:40", tz="UTC"),
    duration_min=40.0, min_distance_m=210.0, mean_sog_kn=0.6,
    lat=13.50, lon=66.20, confidence=0.95, h3_cell="")
inj_encounters = eng["encounters"] + [dark_rdv_encounter]
inj_associations = fus["associations"] + [dict(
    detection_id="det_DRDV1_dark", status="unmatched", ts=dark_rdv_encounter["t_start"],
    scene_id="SYN3_DRDV", mmsi=None, in_ais_gap=False,
    props=dict(lat=13.501, lon=66.201))]

# identity-then-anomaly inject: a hull reflags, then goes dark 3 days later.
# Register the reflag as an identity event and a subsequent dark alert on the
# SAME hull so the correlator fires (the laundering sequence).
laundering_hull = graph.resolve_mmsi(g, 419100006,
                                     at=pd.Timestamp("2026-06-20", tz="UTC").timestamp())
g.emit("identity_changed", laundering_hull,
       pd.Timestamp("2026-06-20", tz="UTC").timestamp(),
       dict(field="flag", old="PA", new="KM", vessel=laundering_hull))
g._con.execute("UPDATE events SET processed=1 WHERE event_type='identity_changed'")
g._con.commit()
inj_verdicts = fus["verdicts"] + [dict(
    detection_id="det_LAUNDER_dark", status="dark_candidate",
    ts=pd.Timestamp("2026-06-23", tz="UTC"), scene_id="SYN3_LAUNDER",
    lat=14.0, lon=64.5, length_m=180.0, dark_score=0.72, hearable_conf=0.9)]
# link the planted dark detection to the laundering hull so the correlator
# resolves subject_vessel back to it
g.upsert_node("detection:det_LAUNDER_dark", "detection",
              dict(detection_id="det_LAUNDER_dark"))
g.add_edge("resolved-from", laundering_hull, "detection:det_LAUNDER_dark",
           t_start=pd.Timestamp("2026-06-23", tz="UTC").timestamp(),
           t_end=pd.Timestamp("2026-06-23", tz="UTC").timestamp() + 1,
           confidence=0.7, observed_at=pd.Timestamp("2026-06-23", tz="UTC").timestamp(),
           source="p5inj", source_ref="p5inj")

# ---- 3. the anomaly library ----------------------------------------------
fired = anomaly.run_anomaly_library(
    g, tracks=all_tracks, encounters=inj_encounters,
    spoof_events=eng["spoof_events"], associations=inj_associations,
    verdicts=inj_verdicts, source_ref="p5")
print("anomaly detectors fired:")
for atype, ids in fired.items():
    print(f"  {atype:>22}: {len(ids)}")
print(f"total alerts: {len(g.alerts())}")

# ---- 4. simulate dispositions → feedback loop ----------------------------
# analyst confirms true darks / true spoofs, dismisses the rest, for the
# dark_vessel detector (the one with enough alerts to retune)
truth = json.loads((DATA / "synthetic_scene_truth_phase3.json").read_text())
for a in g.alerts(anomaly_type="dark_vessel"):
    if not a["subject"].startswith("detection:"):
        continue          # graph-rule dark-gap alerts have a vessel subject;
                          # they're not scene-truth-labelable, leave them open
    det = a["subject"].replace("detection:", "")
    lab = truth.get(det, "")
    g.dispose(a["alert_id"], "confirm" if lab.startswith("ghost:") else "dismiss")
# a few extra synthetic dispositions to clear the min-count gate meaningfully
for a in g.alerts(anomaly_type="ais_spoofing"):
    g.dispose(a["alert_id"], "confirm")

retune = anomaly.propose_retune(g, "dark_vessel", apply=False)
if retune:
    print(f"\nfeedback retune [dark_vessel] on {retune.n_dispositions} dispositions:")
    print(f"  threshold {retune.old_threshold:.2f} -> {retune.new_threshold:.2f}")
    print(f"  precision {retune.precision_before:.0%} -> {retune.precision_after:.0%}"
          f"  (delta {retune.precision_delta:+.0%})")
    print(f"  recall    {retune.recall_before:.0%} -> {retune.recall_after:.0%}")
else:
    print("\nfeedback: not enough dispositions to retune yet")

# ---- 5. composite risk ranking (explainable) -----------------------------
at = pd.Timestamp("2026-07-05", tz="UTC").timestamp()
ranked = anomaly.rank_vessels(g, at=at, top=8)
print("\ntop risk (decomposable):")
for r in ranked[:6]:
    comp = r["components"]
    print(f"  {r['vessel']:<26} risk={r['risk_score']:.3f}  "
          f"[anom {comp['anomaly_history']['weighted']:.2f} "
          f"sanc {comp['sanction_proximity']['weighted']:.2f} "
          f"flag {comp['flag_opacity']['weighted']:.2f} "
          f"fp {comp['fingerprint_deviation']['weighted']:.2f}]")

# ---- 6. weekly anomaly summary (zero human effort) -----------------------
summary = dict(
    week_of="2026-06-15", generated_at=datetime.now(timezone.utc).isoformat(),
    alerts_by_type=dict(Counter(a["anomaly_type"] for a in g.alerts())),
    top_risk=[dict(vessel=r["vessel"], score=r["risk_score"]) for r in ranked[:5]],
    dispositions=anomaly.feedback_summary(g))
(DATA / "phase5_weekly_summary.json").write_text(json.dumps(summary, indent=1))
print(f"\nweekly summary -> {DATA / 'phase5_weekly_summary.json'}")

# ---- 7. eval + ledger ----------------------------------------------------
# risk ordering check: a sanctioned-owner dark vessel vs a clean vessel
high = anomaly.risk_score(g, graph.resolve_mmsi(g, 419100002, at=at), at=at)  # dark merchant, Redwater
low = anomaly.risk_score(g, graph.resolve_mmsi(g, 419200000, at=at), at=at)   # clean fisher
r = evaluate_anomalies(g, fired, retune, high, low)
print(f"\ndetectors live: {r.n_types_live}/6   "
      f"all scored+evidenced: {r.all_scored_and_evidenced}")
print(f"risk decomposes exactly: {r.risk_decomposes}   "
      f"ordering correct: {r.risk_ordering_correct}")
record_to_ledger(r)
print("ledger row appended (suite=phase5_anomaly_synthetic)")

# ---- 8. dashboard snapshot -----------------------------------------------
snap = dict(
    generated_at=datetime.now(timezone.utc).isoformat(),
    zones=SENSITIVE_ZONES,
    alerts=[dict(atype=a["anomaly_type"], subject=a["subject"],
                 mmsi=graph.current_mmsi(g, a["subject"], a["ts"])
                 if a["subject"].startswith("vessel") else None,
                 score=round(a["score"], 3) if a["score"] else None,
                 disposition=a["disposition"], ts=a["ts"],
                 props=a["props"],
                 chain=[dict(edge=c.get("edge"), src=c.get("src"),
                             dst=c.get("dst"), conf=c.get("confidence"))
                        for c in a["evidence"]])
            for a in g.alerts()],
    risk=[dict(vessel=r_["vessel"], score=r_["risk_score"],
               components=r_["components"], evidence=r_["evidence"])
          for r_ in ranked],
    feedback=anomaly.feedback_summary(g),
    retune=(dict(atype=retune.anomaly_type, old=retune.old_threshold,
                 new=retune.new_threshold,
                 p_before=retune.precision_before, p_after=retune.precision_after,
                 delta=retune.precision_delta) if retune else None),
    metrics=dict(types_live=r.n_types_live, feedback_delta=r.feedback_delta,
                 risk_decomposes=r.risk_decomposes,
                 risk_ordering_correct=r.risk_ordering_correct),
)
(DATA / "phase5_snapshot.json").write_text(json.dumps(snap))
print(f"snapshot -> {DATA / 'phase5_snapshot.json'}")
g.close()
