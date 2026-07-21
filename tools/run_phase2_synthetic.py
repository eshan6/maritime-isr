"""Phase 2 end-to-end exercise. Same shape as Phase 1's runner:

  feed (real AIVDM decode, per-line receivers) → conformed ais_position
  → track engine (build, smooth, coverage model, gap classify, encounters,
    features, spoof events) → conformed outputs + catalog
  → eval vs injected truth → ledger → dashboard snapshot JSON

Everything below runs against the SYNTHETIC 30-day feed. No real AIS has
flowed through this system; the deploy host runs this identical path on the
live terrestrial + Spire feeds.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maritime_isr import tracks as trk
from maritime_isr.config import AOI_V1
from maritime_isr.connectors import ais as ais_conn
from maritime_isr.connectors import satais
from maritime_isr.eval.tracks import evaluate_tracks, record_to_ledger
from maritime_isr.storage import raw

DATA = Path(__file__).resolve().parent.parent / "data"

# ---- 1. ingest through the REAL decoder, per-line receiver routing ------
feed = DATA / "synthetic_ais_30d.nmea"
payload = feed.read_bytes()
rpath, sha = raw.land("ais_synthetic_phase2", feed.name, payload,
                      day=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
parser = ais_conn.AivdmParser("multi", aoi=AOI_V1)
messages = []
for line in payload.decode().splitlines():
    ts_s, rx, sentence = line.split("\t")
    parser.receiver = rx
    msg = parser.feed(sentence, datetime.fromisoformat(ts_s))
    if msg and msg["msg_type"] != 5:
        messages.append(msg)
st = parser.stats
print(f"decoded {st.parsed}/{st.total}  drop_rate={st.drop_rate:.3%}")
assert st.drop_rate < 0.01, "Phase 0 exit criterion violated on Phase 2 feed"

tbl = ais_conn.conform(messages, source="ais_synthetic:multi", source_ref=sha[:12])
pos = tbl.to_pandas()
print(f"conformed {len(pos)} positions "
      f"({tbl.schema.metadata[b'n_deduped'].decode()} deduped), "
      f"{pos['receiver'].str.contains('sat').mean():.1%} heard by satellite")

# ---- 2. satellite pass schedule via the sat-AIS connector code path -----
sched = trk.SatPassSchedule(
    satais.parse_pass_predictions((DATA / "synthetic_sat_passes.json").read_bytes()))
print(f"sat pass schedule: {len(sched.windows)} windows")

# ---- 3. the track engine, end to end -------------------------------------
day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out = trk.run_track_engine(pos, source_ref=sha[:12], sat_schedule=sched,
                           partition_day=day, aoi=AOI_V1.name)
tracks, gaps, encs, spoofs = (out["tracks"], out["gaps"],
                              out["encounters"], out["spoof_events"])
print(f"\ntracks: {len(tracks)}   gaps: {len(gaps)}   "
      f"encounters: {len(encs)}   spoof events: "
      f"{len([s for s in spoofs if s['event_type']=='DUPLICATE_MMSI'])} duplicate-MMSI / "
      f"{len([s for s in spoofs if s['event_type']=='IMPOSSIBLE_KINEMATICS'])} kinematic")
from collections import Counter
print("gap types:", dict(Counter(g["gap_type"] for g in gaps)))

# ---- 4. eval vs truth, ledger ---------------------------------------------
truth = json.loads((DATA / "synthetic_truth_phase2.json").read_text())
r = evaluate_tracks(tracks, gaps, encs, truth)
print(f"\nfragmentation: {r.n_fragmented}/{r.n_segments} segments "
      f"= {r.fragmentation_rate:.1%}  (exit: <10%)")
print(f"gap labels: {r.gap_label_accuracy:.0%} correct  {r.gap_confusion}")
print(f"encounters: P={r.encounter_precision:.2f} R={r.encounter_recall:.2f} "
      f"F1={r.encounter_f1:.2f}  (exit: precision >70%)")
record_to_ledger(r)
print("ledger row appended (suite=phase2_tracks_synthetic)")

# ---- 5. dashboard snapshot ------------------------------------------------
snap = dict(
    generated_at=datetime.now(timezone.utc).isoformat(),
    aoi=dict(lat_min=AOI_V1.lat_min, lat_max=AOI_V1.lat_max,
             lon_min=AOI_V1.lon_min, lon_max=AOI_V1.lon_max),
    receivers=[dict(name=n, lat=la, lon=lo, radius_km=rk) for n, (la, lo, rk) in
               {"ter:mumbai": (18.95, 72.84, 300), "ter:porbandar": (21.63, 69.60, 300),
                "ter:karachi": (24.79, 66.98, 300), "ter:kochi": (9.97, 76.24, 300)}.items()],
    tracks=[dict(track_id=t.track_id, mmsi=t.mmsi, hypothesis=t.hypothesis,
                 pts=[[round(r_.lat, 4), round(r_.lon, 4)] for r_ in
                      t.points[t.points.quality != "outlier"].iloc[::6].itertuples()])
            for t in tracks],
    gaps=[dict(mmsi=g["mmsi"], type=g["gap_type"], conf=round(g["confidence"], 2),
               dur_min=round(g["duration_min"]),
               a=[round(g["lat_start"], 4), round(g["lon_start"], 4)],
               b=[round(g["lat_end"], 4), round(g["lon_end"], 4)])
          for g in gaps],
    encounters=[dict(mmsi_a=e["mmsi_a"], mmsi_b=e["mmsi_b"],
                     lat=round(e["lat"], 4), lon=round(e["lon"], 4),
                     dur_min=round(e["duration_min"]),
                     dmin=round(e["min_distance_m"])) for e in encs],
    spoofs=[dict(mmsi=s["mmsi"], sep_km=round(s["max_separation_km"]))
            for s in spoofs if s["event_type"] == "DUPLICATE_MMSI"][:5],
    metrics=dict(fragmentation=r.fragmentation_rate,
                 gap_accuracy=r.gap_label_accuracy,
                 enc_precision=r.encounter_precision,
                 enc_recall=r.encounter_recall,
                 n_tracks=len(tracks), n_gaps=len(gaps)),
)
(DATA / "phase2_snapshot.json").write_text(json.dumps(snap))
print(f"snapshot -> {DATA/'phase2_snapshot.json'} "
      f"({(DATA/'phase2_snapshot.json').stat().st_size//1024} KB)")
