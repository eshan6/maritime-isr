"""30-day synthetic AOI feed for Phase 2 — with TRUTH LABELS.

Extends the Phase 0 generator's realism with the things Phase 2 must be
measured against, each written to truth JSON so the eval is a comparison,
not an eyeball:

  - staggered merchant transits (vessels enter/exit the AOI — track lifecycle)
  - a RECEIVER MODEL: 4 terrestrial stations (300 km radius) + 1 satellite
    feed hearing everything but only during pass windows (~11 min every 97).
    A report only lands if something could actually have heard it — so the
    coverage model is learned from honestly-shaped silence.
  - dark vessel: 9 h transponder-off inside terrestrial coverage → INTENTIONAL_SILENCE
  - offshore vessel with only-satellite coverage → SAT_PASS_GAP filler between passes,
    plus one 6 h deliberate dark period spanning ~4 passes → INTENTIONAL_SILENCE
  - a vessel crossing a true no-coverage hole while satellite is down for a
    26 h outage → COVERAGE_GAP
  - duplicate-MMSI spoof pair (two broadcasters, both in coverage)
  - 3 true rendezvous (converge, <500 m, <2 kn, 35–50 min) and 2 engineered
    near-miss NEGATIVES (12 kn crossing at <500 m; 2 h loiter at 700 m) —
    the encounter detector's precision is measured against these
  - the usual filth: 30% second-receiver duplicates, 0.5% bad checksums,
    sentinel values

Deterministic (seed 7): same feed every run, same reproducibility contract
as the Phase 1 scene suite.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(7)
T0 = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
DAYS = 30
END = T0 + timedelta(days=DAYS)

# ---------------- receiver model ----------------
TER_STATIONS = {  # name: (lat, lon, radius_km)
    "ter:mumbai": (18.95, 72.84, 300), "ter:porbandar": (21.63, 69.60, 300),
    "ter:karachi": (24.79, 66.98, 300), "ter:kochi": (9.97, 76.24, 300),
}
SAT_PERIOD_MIN, SAT_PASS_MIN = 97, 11
SAT_OUTAGE = (timedelta(days=17, hours=3), timedelta(days=18, hours=5))  # 26 h feed outage


def sat_passes():
    out, t = [], T0
    o0, o1 = T0 + SAT_OUTAGE[0], T0 + SAT_OUTAGE[1]
    while t < END:
        t1 = t + timedelta(minutes=SAT_PASS_MIN)
        if not (t >= o0 and t1 <= o1):
            out.append((t, min(t1, END)))
        t += timedelta(minutes=SAT_PERIOD_MIN)
    return out


PASSES = sat_passes()


def in_sat_pass(t):
    for a, b in PASSES:
        if a <= t <= b:
            return True
        if a > t:
            return False
    return False


def hav_km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*6371*math.asin(math.sqrt(a))


def receivers_hearing(lat, lon, t):
    rx = [n for n, (la, lo, r) in TER_STATIONS.items() if hav_km(lat, lon, la, lo) <= r]
    if in_sat_pass(t):
        rx.append("sat:spire-syn")
    return rx


# ---------------- AIVDM type-1 encoder (same as Phase 0 tool) -------------
def u(v, n): return format(int(v) & ((1 << n) - 1), f"0{n}b")


def encode_type1(mmsi, lat, lon, sog, cog, heading, nav=0):
    bits = u(1, 6) + u(0, 2) + u(mmsi, 30) + u(nav, 4) + u(0, 8)
    bits += u(int(round(sog * 10)), 10) + u(0, 1)
    bits += u(int(round(lon * 600000)), 28) + u(int(round(lat * 600000)), 27)
    bits += u(int(round(cog * 10)), 12) + u(int(heading), 9)
    bits += u(0, 6) + u(0, 2) + u(0, 3) + u(0, 1) + u(0, 19)
    fill = (6 - len(bits) % 6) % 6
    bits += "0" * fill
    payload = "".join(chr(v + 48 if v < 40 else v + 56)
                      for v in (int(bits[i:i+6], 2) for i in range(0, len(bits), 6)))
    body = f"AIVDM,1,1,,A,{payload},{fill}"
    x = 0
    for ch in body: x ^= ord(ch)
    return f"!{body}*{x:02X}"


# ---------------- fleet script ----------------
class V:
    def __init__(self, mmsi, lat, lon, cog, sog, report_min, t_in=None, t_out=None):
        self.mmsi, self.lat, self.lon = mmsi, lat, lon
        self.cog, self.sog, self.report_min = cog, sog, report_min
        self.t_in = t_in or T0
        self.t_out = t_out or END
        self.dark = []          # [(t0,t1)]
        self.script = []        # [(t0,t1,cog,sog)] overrides
        self.wander = 0.0

    def active(self, t): return self.t_in <= t < self.t_out

    def is_dark(self, t): return any(a <= t < b for a, b in self.dark)

    def step(self, dt_min):
        self.lat += self.sog*(dt_min/60)*math.cos(math.radians(self.cog))/60
        self.lon += self.sog*(dt_min/60)*math.sin(math.radians(self.cog))/60/max(
            math.cos(math.radians(self.lat)), .2)
        if self.wander:
            self.cog = (self.cog + random.uniform(-self.wander, self.wander)) % 360


fleet, truth = [], dict(vessel_segments=[], dark_periods=[], spoof=[],
                        encounters=[], negatives=[], sat_passes=[
                            [a.isoformat(), b.isoformat()] for a, b in PASSES])

# 10 staggered merchants, Gulf→Mumbai lane, ~4.5-day transits
for i in range(10):
    t_in = T0 + timedelta(hours=i * 68)
    v = V(419100000+i, 7.5+random.uniform(0, 1.5), 60.5+random.uniform(0, 1),
          cog=48+random.uniform(-3, 3), sog=random.uniform(11, 14),
          report_min=3, t_in=t_in, t_out=min(t_in+timedelta(hours=110), END))
    fleet.append(v)
    truth["vessel_segments"].append(dict(mmsi=v.mmsi, t0=v.t_in.isoformat(),
                                         t1=v.t_out.isoformat()))
# dark vessel: merchant 419100002, transponder off 9h mid-transit while
# inside Porbandar terrestrial ring
dv = fleet[2]
d0 = dv.t_in + timedelta(hours=58); d1 = d0 + timedelta(hours=9)
dv.dark.append((d0, d1))
truth["dark_periods"].append(dict(mmsi=dv.mmsi, t0=d0.isoformat(),
                                  t1=d1.isoformat(), expected="INTENTIONAL_SILENCE"))

# 5 fishing vessels off Porbandar (in terrestrial coverage), loiter/drift
for i in range(5):
    v = V(419200000+i, 20.7+random.uniform(-.4, .4), 68.9+random.uniform(-.5, .5),
          cog=random.uniform(0, 360), sog=random.uniform(.5, 3.5), report_min=6)
    v.wander = 20
    fleet.append(v)
    truth["vessel_segments"].append(dict(mmsi=v.mmsi, t0=T0.isoformat(), t1=END.isoformat()))

# offshore long-liner: central Arabian Sea, satellite-only coverage
off = V(419400001, 14.0, 65.5, cog=90, sog=1.5, report_min=6)
off.wander = 15
fleet.append(off)
truth["vessel_segments"].append(dict(mmsi=off.mmsi, t0=T0.isoformat(), t1=END.isoformat()))
# its deliberate dark period: 6h spanning ~4 sat passes on day 9
od0 = T0 + timedelta(days=9, hours=2); od1 = od0 + timedelta(hours=6)
off.dark.append((od0, od1))
truth["dark_periods"].append(dict(mmsi=off.mmsi, t0=od0.isoformat(),
                                  t1=od1.isoformat(), expected="INTENTIONAL_SILENCE"))

# coverage-hole crosser: offshore transit during the 26h satellite outage,
# outside every terrestrial ring → honest COVERAGE_GAP
cg = V(419400002, 11.0, 63.0, cog=70, sog=10, report_min=3,
       t_in=T0+timedelta(days=16, hours=12), t_out=T0+timedelta(days=20))
fleet.append(cg)
truth["vessel_segments"].append(dict(mmsi=cg.mmsi, t0=cg.t_in.isoformat(),
                                     t1=cg.t_out.isoformat()))
truth["dark_periods"].append(dict(mmsi=cg.mmsi,
                                  t0=(T0+SAT_OUTAGE[0]).isoformat(),
                                  t1=(T0+SAT_OUTAGE[1]).isoformat(),
                                  expected="COVERAGE_GAP"))

# duplicate-MMSI spoof: both inside coverage, far apart, days 5-25
SPOOF = 419300001
sp_a = V(SPOOF, 19.5, 70.5, cog=90, sog=8, report_min=5,
         t_in=T0+timedelta(days=5), t_out=T0+timedelta(days=25))
sp_a.wander = 10
sp_b = V(SPOOF, 24.2, 66.5, cog=200, sog=7, report_min=5,
         t_in=T0+timedelta(days=5), t_out=T0+timedelta(days=25))
sp_b.wander = 10
fleet += [sp_a, sp_b]
truth["spoof"].append(dict(mmsi=SPOOF, t0=sp_a.t_in.isoformat(),
                           t1=sp_a.t_out.isoformat()))
truth["vessel_segments"] += [dict(mmsi=SPOOF, t0=sp_a.t_in.isoformat(),
                                  t1=sp_a.t_out.isoformat(), note="spoof-pair")] * 2

# --- rendezvous script: pairs of fishing-type vessels off Gujarat ---------
def scripted_rendezvous(m1, m2, meet_lat, meet_lon, t_meet, dur_min):
    """Two vessels approach from ±0.35°, sit <300 m apart at 0.7 kn for
    dur_min, then separate. Positions driven directly (no integration error)."""
    approach_h = 3.0
    rows = []
    for mm, sgn in ((m1, 1), (m2, -1)):
        v = V(mm, 0, 0, 0, 0, report_min=4,
              t_in=t_meet - timedelta(hours=approach_h + 1),
              t_out=t_meet + timedelta(minutes=dur_min) + timedelta(hours=approach_h))
        v.scripted = (meet_lat, meet_lon, t_meet, dur_min, sgn, approach_h)
        rows.append(v)
    return rows


def scripted_pos(v, t):
    meet_lat, meet_lon, t_meet, dur, sgn, ah = v.scripted
    t_end = t_meet + timedelta(minutes=dur)
    off0 = 0.35 * sgn
    if t < t_meet:                     # approach
        f = max(0.0, (t_meet - t).total_seconds()/3600/ah)
        return meet_lat + off0*f, meet_lon + off0*f*.6, 6.0*min(f*3, 1), (225 if sgn > 0 else 45)
    if t <= t_end:                     # meeting: ~250 m apart, 0.7 kn
        return meet_lat + sgn*0.0011, meet_lon, 0.7, 90.0
    f = min(1.0, (t - t_end).total_seconds()/3600/ah)   # depart
    return meet_lat - off0*f*.8, meet_lon - off0*f, 6.0*min(f*3+.2, 1), (45 if sgn > 0 else 225)


for k, (t_meet, dur) in enumerate([(T0+timedelta(days=6, hours=4), 45),
                                   (T0+timedelta(days=13, hours=20), 35),
                                   (T0+timedelta(days=22, hours=11), 50)]):
    m1, m2 = 419500000+2*k, 419500001+2*k
    la, lo = 20.0 - k*0.8, 69.5 + k*0.6
    fleet += scripted_rendezvous(m1, m2, la, lo, t_meet, dur)
    truth["encounters"].append(dict(mmsi_a=m1, mmsi_b=m2, t0=t_meet.isoformat(),
                                    t1=(t_meet+timedelta(minutes=dur)).isoformat(),
                                    lat=la, lon=lo))

# negatives: (a) 12 kn crossing within 500 m; (b) 2 h loiter at ~700 m
na, nb = V(419600001, 18.4, 70.0, cog=90, sog=12, report_min=3,
           t_in=T0+timedelta(days=8), t_out=T0+timedelta(days=8, hours=20)), \
         V(419600002, 18.4+0.002, 71.2, cog=270, sog=12, report_min=3,
           t_in=T0+timedelta(days=8), t_out=T0+timedelta(days=8, hours=20))
fleet += [na, nb]
truth["negatives"].append(dict(mmsi_a=na.mmsi, mmsi_b=nb.mmsi, kind="fast_crossing"))
for i, mm in enumerate((419600003, 419600004)):
    v = V(mm, 19.2 + i*0.0063, 70.8, cog=0, sog=0.8, report_min=5,
          t_in=T0+timedelta(days=15), t_out=T0+timedelta(days=15, hours=8))
    fleet.append(v)
truth["negatives"].append(dict(mmsi_a=419600003, mmsi_b=419600004,
                               kind="loiter_700m_apart"))
for mm in (419600001, 419600002, 419600003, 419600004):
    truth["vessel_segments"].append(dict(mmsi=mm, t0=T0.isoformat(), t1=END.isoformat(),
                                         note="negative-pair, partial window"))

# ---------------- vessel lengths + synthetic registry (Phase 3) ----------
# Separate Random instances so the AIS feed stays byte-identical.
_rl = random.Random(11)
LENGTHS = {}
for v in fleet:
    m = v.mmsi
    if m in LENGTHS and m != SPOOF:
        continue
    if 419100000 <= m < 419100010:   L = _rl.uniform(170, 280)
    elif 419200000 <= m < 419200005: L = _rl.uniform(18, 32)
    elif m == 419400001:             L = 34.0
    elif m == 419400002:             L = 55.0
    elif m == SPOOF:                 L = 120.0 if m not in LENGTHS else 95.0
    elif 419500000 <= m < 419500006: L = _rl.uniform(28, 40)
    elif m in (419600001, 419600002): L = 60.0
    else:                            L = 25.0
    # spoof pair: two physical ships under one MMSI -> store per broadcaster
    key = (m, id(v)) if m == SPOOF else m
    LENGTHS[key] = round(L, 1)
    v.length_m = LENGTHS[key]
_rr = random.Random(12)
registry = {}
for v in fleet:
    m = v.mmsi
    if m in (419200004, 419500003):
        continue                     # deliberately absent from registry
    if m == SPOOF and m in registry:
        continue                     # registry knows ONE ship under this MMSI
    registry[str(m)] = round(v.length_m * _rr.uniform(0.92, 1.08), 1)
(Path(sys.argv[1] if len(sys.argv) > 1 else "data") / "synthetic_registry.json"
 ).parent.mkdir(parents=True, exist_ok=True)

# ---------------- simulate ----------------
lines, corrupted, n_unheard = [], 0, 0
true_rows = []   # every kinematic tick, heard or not, dark or not — Phase 3 scene truth
t = T0
while t < END:
    for v in fleet:
        if not v.active(t):
            continue
        if int((t - T0).total_seconds()/60) % v.report_min != 0:
            continue
        if hasattr(v, "scripted"):
            v.lat, v.lon, v.sog, v.cog = scripted_pos(v, t)
        else:
            v.step(v.report_min)
        true_rows.append(f"{t.isoformat()},{v.mmsi},{v.lat:.6f},{v.lon:.6f},"
                         f"{v.sog:.2f},{v.cog:.1f},{v.length_m},{id(v)%100000}")
        if v.is_dark(t):
            continue                    # transponder off — ship still moved
        rx = receivers_hearing(v.lat, v.lon, t)
        if not rx:
            n_unheard += 1
            continue                    # transmitting into silence
        sog = 102.3 if random.random() < .01 else v.sog
        s = encode_type1(v.mmsi, v.lat, v.lon, round(sog, 1),
                         round(v.cog, 1) % 360, int(v.cog) % 360)
        if random.random() < .005:
            s = s[:-1] + ("0" if s[-1] != "0" else "1"); corrupted += 1
        primary = random.choice(rx)
        lines.append(f"{t.isoformat()}\t{primary}\t{s}")
        if len(rx) > 1 and random.random() < .30:
            lines.append(f"{t.isoformat()}\t{random.choice([r for r in rx if r != primary])}\t{s}")
    t += timedelta(minutes=1)

outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
outdir.mkdir(parents=True, exist_ok=True)
(outdir/"synthetic_ais_30d.nmea").write_text("\n".join(lines))
(outdir/"synthetic_truth_phase2.json").write_text(json.dumps(truth, indent=1))
(outdir/"synthetic_sat_passes.json").write_text(json.dumps(
    {"passes": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in PASSES]}))
(outdir/"synthetic_true_positions.csv").write_text(
    "ts,mmsi,lat,lon,sog,cog,length_m,body\n" + "\n".join(true_rows))
(outdir/"synthetic_registry.json").write_text(json.dumps(registry, indent=1))
print(f"wrote {len(lines)} sentences ({corrupted} corrupted, "
      f"{n_unheard} transmitted-unheard) -> {outdir/'synthetic_ais_30d.nmea'}")
print(f"true positions: {len(true_rows)} rows; registry: {len(registry)} vessels")
print(f"truth: {len(truth['vessel_segments'])} segments, "
      f"{len(truth['dark_periods'])} dark periods, {len(truth['encounters'])} rendezvous, "
      f"{len(truth['negatives'])} negative pairs, {len(PASSES)} sat passes")
