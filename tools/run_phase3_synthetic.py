"""Phase 3 end-to-end exercise — the nightly run, on the synthetic world:

  AIS feed → tracks + coverage model (the Phase 2 engine, unchanged)
  scenes → association → static layer → dark cascade
  → eval vs scene truth → ledger → dashboard snapshot

Everything below runs against SYNTHETIC data. The deploy host runs this
identical path on real Sentinel-1 detections and live AIS.
"""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr import fusion, tracks as trk
from maritime_isr.config import AOI_V1
from maritime_isr.connectors import ais as ais_conn, satais
from maritime_isr.eval.fusion import evaluate_fusion, record_to_ledger
from maritime_isr.storage import raw
from maritime_isr.tracks.coverage import CoverageModel

DATA = Path(__file__).resolve().parent.parent / "data"

# ---- 1. AIS side: the Phase 2 engine, unchanged --------------------------
payload = (DATA / "synthetic_ais_30d.nmea").read_bytes()
rpath, sha = raw.land("ais_synthetic_phase3", "synthetic_ais_30d.nmea", payload,
                      day=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
parser = ais_conn.AivdmParser("multi", aoi=AOI_V1)
messages = []
for line in payload.decode().splitlines():
    ts_s, rx, sentence = line.split("\t")
    parser.receiver = rx
    msg = parser.feed(sentence, datetime.fromisoformat(ts_s))
    if msg and msg["msg_type"] != 5:
        messages.append(msg)
pos = ais_conn.conform(messages, source="ais_synthetic:multi",
                       source_ref=sha[:12]).to_pandas()
sched = trk.SatPassSchedule(satais.parse_pass_predictions(
    (DATA / "synthetic_sat_passes.json").read_bytes()))
eng = trk.run_track_engine(pos, source_ref=sha[:12], sat_schedule=sched,
                           partition_day=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                           aoi=AOI_V1.name, write_outputs=False)
tracks = eng["tracks"]
model: CoverageModel = eng["coverage_model"]
print(f"AIS side: {len(pos)} positions -> {len(tracks)} tracks")

# ---- 2. SAR side: scenes + registry ---------------------------------------
scenes = json.loads((DATA / "synthetic_scenes_phase3.json").read_text())
registry = {int(k): v for k, v in
            json.loads((DATA / "synthetic_registry.json").read_text()).items()}
n_det = sum(len(s["detections"]) for s in scenes)
print(f"SAR side: {len(scenes)} scenes, {n_det} detections; "
      f"registry knows {len(registry)} vessels")

# ---- 3. the fusion core ----------------------------------------------------
day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out = fusion.run_fusion(scenes, tracks, model, registry, gaps=eng["gaps"],
                        spoof_events=eng["spoof_events"],
                        source_ref=sha[:12], partition_day=day, aoi=AOI_V1.name)
assoc, statics, verdicts = out["associations"], out["statics"], out["verdicts"]
print(f"\nassociations: {dict(Counter(a['status'] for a in assoc))}")
print(f"gap-confirmed (SAR sees a ship its AIS says is silent): "
      f"{sum(1 for a in assoc if a.get('in_ais_gap'))}"
      f" of which INTENTIONAL: "
      f"{sum(1 for a in assoc if a.get('gap_type') == 'INTENTIONAL_SILENCE')}")
print(f"static objects: {len(statics)}")
print(f"dark verdicts: {dict(Counter(v['status'] for v in verdicts))}")

# ---- 4. eval vs truth, ledger ----------------------------------------------
scene_truth = json.loads((DATA / "synthetic_scene_truth_phase3.json").read_text())
feed_truth = json.loads((DATA / "synthetic_truth_phase2.json").read_text())
r = evaluate_fusion(assoc, verdicts, scene_truth, feed_truth,
                    ghost_lengths={"ghost:smuggler": 45.0, "ghost:dhow": 14.0})
print(f"\nassociation accuracy (non-ambiguous vessel contacts): "
      f"{r.assoc_accuracy:.1%} on {r.n_assoc_scored}  (target >=85%)")
print(f"dark-vessel precision: {r.dark_precision:.1%} "
      f"({r.n_dark_flagged} flagged)  (exit: >=70%)")
print(f"dark-vessel recall:    {r.dark_recall:.1%} "
      f"of {r.n_dark_truth} ghost detections above size floor "
      f"({r.n_below_floor_ghosts} below floor — capability boundary)")
print(f"gap confirmation: {r.gap_confirm_rate:.0%} of {r.n_gap_window_dets} "
      f"dark-window vessel detections matched w/ in_ais_gap flag")
print(f"rigs suppressed by static layer: {r.rig_suppressed_frac:.0%}   "
      f"clutter alerts leaked: {r.clutter_alert_count}")
record_to_ledger(r)
print("ledger row appended (suite=phase3_fusion_synthetic)")

# ---- 5. dashboard snapshot --------------------------------------------------
truth_status = {}
for v in verdicts:
    lab = scene_truth.get(v["detection_id"], "?")
    truth_status[v["detection_id"]] = lab
snap = dict(
    generated_at=datetime.now(timezone.utc).isoformat(),
    aoi=dict(lat_min=AOI_V1.lat_min, lat_max=AOI_V1.lat_max,
             lon_min=AOI_V1.lon_min, lon_max=AOI_V1.lon_max),
    tracks=[dict(mmsi=t.mmsi,
                 pts=[[round(r_.lat, 4), round(r_.lon, 4)] for r_ in
                      t.points[t.points.quality != "outlier"].iloc[::8].itertuples()])
            for t in tracks],
    matched=[dict(lat=round(a_det["lat"], 4), lon=round(a_det["lon"], 4),
                  mmsi=a["mmsi"], conf=a["confidence"], st=a["status"])
             for a in assoc if a["status"] in ("matched", "ambiguous")
             for a_det in [next(d for s in scenes for d in s["detections"]
                                if d["detection_id"] == a["detection_id"])]],
    verdicts=[dict(lat=round(v["lat"], 4), lon=round(v["lon"], 4),
                   st=v["status"], score=v["dark_score"],
                   len=v["length_m"], truth=truth_status[v["detection_id"]],
                   scene=v["scene_id"]) for v in verdicts],
    statics=[dict(lat=round(s["lat"], 4), lon=round(s["lon"], 4),
                  n=s["n_scenes"]) for s in statics],
    metrics=dict(assoc_accuracy=r.assoc_accuracy,
                 dark_precision=r.dark_precision, dark_recall=r.dark_recall,
                 n_flagged=r.n_dark_flagged, n_statics=len(statics),
                 n_ambiguous=r.n_ambiguous),
)
(DATA / "phase3_snapshot.json").write_text(json.dumps(snap))
print(f"snapshot -> {DATA / 'phase3_snapshot.json'}")
