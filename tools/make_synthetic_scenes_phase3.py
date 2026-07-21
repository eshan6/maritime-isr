"""Synthetic SAR scene suite for Phase 3 — detections derived from the
Phase 2 world's TRUE positions (data/synthetic_true_positions.csv), so the
association eval has exact truth.

Physics honesty carried over from Phase 1: detection probability falls with
vessel length (the 15-25 m floor is physics, not engineering), positions
carry ~25 m noise, SAR length estimates carry ~18% noise.

Scene schedule: one strip per day rotating across three longitude bands,
plus three TARGETED scenes timed inside the truth dark windows (dark
merchant, offshore long-liner, outage crosser) — the dark-vessel eval needs
scenes that actually catch the darks.

Injected beyond the AIS world:
  - 2 ghost vessels (never transmitted, exist only here): a 45 m smuggler
    transit and a 14 m dhow (below the size floor — must NOT alert)
  - 3 fixed oil rigs detected in every covering scene (static-layer test)
  - Poisson sea clutter, mostly small, occasionally 25-35 m (hard FPs —
    the precision gate earns its keep on these)

Deterministic (seed 21). Truth per detection: mmsi | ghost:<id> | rig:<id>
| clutter.
"""
import csv
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

rng = random.Random(21)
T0 = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
DAYS = 30

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data")

# ---------------- load true positions ----------------
rows = []
with open(DATA / "synthetic_true_positions.csv") as f:
    for r in csv.DictReader(f):
        rows.append(dict(ts=datetime.fromisoformat(r["ts"]), mmsi=int(r["mmsi"]),
                         lat=float(r["lat"]), lon=float(r["lon"]),
                         sog=float(r["sog"]), length=float(r["length_m"]),
                         body=r["body"]))
by_body: dict = {}
for r in rows:
    by_body.setdefault((r["mmsi"], r["body"]), []).append(r)
for v in by_body.values():
    v.sort(key=lambda r: r["ts"])


def pos_at(recs, t):
    """Nearest-tick position (cadence <= 6 min, fine for scene sampling)."""
    lo, hi = 0, len(recs) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if recs[mid]["ts"] < t:
            lo = mid + 1
        else:
            hi = mid
    r = recs[lo]
    return r if abs((r["ts"] - t).total_seconds()) <= 360 else None


# ---------------- ghosts (never in AIS) ----------------
GHOSTS = {
    "ghost:smuggler": dict(lat0=9.5, lon0=63.0, cog=38.0, sog=9.0, length=45.0,
                           t_in=T0 + timedelta(days=3), t_out=T0 + timedelta(days=27)),
    "ghost:dhow": dict(lat0=16.5, lon0=67.0, cog=120.0, sog=5.0, length=14.0,
                       t_in=T0 + timedelta(days=2), t_out=T0 + timedelta(days=28)),
}


def ghost_pos(g, t):
    if not (g["t_in"] <= t < g["t_out"]):
        return None
    h = (t - g["t_in"]).total_seconds() / 3600
    lat = g["lat0"] + g["sog"] * h * math.cos(math.radians(g["cog"])) / 60
    lon = g["lon0"] + g["sog"] * h * math.sin(math.radians(g["cog"])) / 60 / \
        max(math.cos(math.radians(lat)), .2)
    # keep them wandering inside the AOI
    lat = 5.5 + (lat - 5.5) % 18.0
    lon = 60.5 + (lon - 60.5) % 16.5
    return lat, lon


RIGS = {"rig:heera": (19.05, 71.30), "rig:panna": (19.55, 71.05),
        "rig:d1": (18.60, 70.75)}

# ---------------- scene schedule ----------------
STRIPS = [(60.0, 66.5), (66.0, 72.5), (71.5, 78.0)]
scenes = []
for d in range(DAYS):
    t = T0 + timedelta(days=d, hours=(5 if d % 2 else 17), minutes=(d * 13) % 60)
    scenes.append((f"SYN3_{d:03d}", t, STRIPS[d % 3]))
# targeted scenes inside the truth dark windows
scenes += [
    ("SYN3_DARK_MERCH", T0 + timedelta(hours=2 * 68 + 62), STRIPS[1]),   # 419100002 dark
    ("SYN3_DARK_LINER", T0 + timedelta(days=9, hours=4), STRIPS[1]),     # 419400001 dark
    ("SYN3_OUTAGE", T0 + timedelta(days=17, hours=12), STRIPS[0]),       # crosser, sat down
    # appended LAST so earlier scenes' rng draws stay identical:
    ("SYN3_DARK_LINER_B", T0 + timedelta(days=9, hours=2, minutes=30), STRIPS[1]),
    ("SYN3_DARK_LINER_C", T0 + timedelta(days=9, hours=6, minutes=30), STRIPS[1]),
]


def p_detect(length):
    if length >= 100: return 0.97
    if length >= 40:  return 0.85
    if length >= 25:  return 0.65
    if length >= 15:  return 0.35
    return 0.12


out_scenes, truth = [], {}
n_det = 0
for scene_id, t_s, (lon0, lon1) in scenes:
    dets = []

    def emit(lat, lon, length, label, score=None):
        global n_det
        if not (5.0 <= lat <= 25.0 and lon0 <= lon <= lon1):
            return
        n_det += 1
        did = f"det_{scene_id}_{n_det:05d}"
        dets.append(dict(
            detection_id=did,
            lat=lat + rng.gauss(0, 25 / 111_320),
            lon=lon + rng.gauss(0, 25 / 111_320),
            length_m=round(max(8.0, length * rng.gauss(1.0, 0.18)), 1),
            score=round(score if score is not None else rng.uniform(0.75, 0.99), 3)))
        truth[did] = label

    for (mmsi, body), recs in by_body.items():
        r = pos_at(recs, t_s)
        if r and rng.random() < p_detect(r["length"]):
            emit(r["lat"], r["lon"], r["length"], str(mmsi))
    for gid, g in GHOSTS.items():
        p = ghost_pos(g, t_s)
        if p and rng.random() < p_detect(g["length"]):
            emit(p[0], p[1], g["length"], gid)
    for rid, (la, lo) in RIGS.items():
        if lon0 <= lo <= lon1:
            emit(la, lo, 60.0, rid, score=rng.uniform(0.85, 0.99))
    for _ in range(rng.choice([2, 3, 3, 4, 5])):
        clat, clon = rng.uniform(5.5, 24.5), rng.uniform(lon0 + .3, lon1 - .3)
        hard = rng.random() < 0.10
        emit(clat, clon, rng.uniform(25, 35) if hard else rng.uniform(5, 16),
             "clutter", score=rng.uniform(0.35, 0.6) if hard else rng.uniform(0.2, 0.45))

    out_scenes.append(dict(scene_id=scene_id, ts=t_s.isoformat(),
                           lon_min=lon0, lon_max=lon1, detections=dets))

(DATA / "synthetic_scenes_phase3.json").write_text(json.dumps(out_scenes))
(DATA / "synthetic_scene_truth_phase3.json").write_text(json.dumps(truth, indent=0))
kinds = {}
for v in truth.values():
    k = v.split(":")[0] if ":" in v else ("vessel" if v.isdigit() else v)
    kinds[k] = kinds.get(k, 0) + 1
print(f"{len(out_scenes)} scenes, {n_det} detections: {kinds}")
