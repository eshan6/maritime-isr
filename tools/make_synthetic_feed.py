"""Generate a synthetic 72h AOI feed to exercise the pipeline end-to-end
in an environment without egress to Copernicus/AIS aggregators.

Deliberately includes the filth the parser must survive:
  - multi-receiver duplicates (~30% of reports heard twice)
  - one vessel that goes dark for 9h mid-transit (Phase 2 test case seed)
  - one MMSI broadcast simultaneously from two positions (spoof tell)
  - 0.5% corrupted sentences (bad checksums)
  - sentinel not-available values sprinkled in
Encodes to real AIVDM type-1 sentences so the actual decoder is exercised,
not a shortcut path.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)
T0 = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
HOURS = 72

def u(v, n): return format(v & ((1 << n) - 1), f"0{n}b")

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

fleet = []
# 12 merchants on the Gulf->Mumbai lane (SW-NE through the AOI)
for i in range(12):
    fleet.append(dict(mmsi=419100000+i, kind="merchant",
                      lat=8.0+random.uniform(0,3), lon=61.0+random.uniform(0,2),
                      cog=52+random.uniform(-4,4), sog=random.uniform(11,16),
                      dark=None, report_min=3))
# vessel 419100003 goes dark hours 30-39 mid-lane
fleet[3]["dark"] = (30, 39)
# 8 fishing vessels loitering off Porbandar
for i in range(8):
    fleet.append(dict(mmsi=419200000+i, kind="fishing",
                      lat=20.5+random.uniform(-0.6,0.6), lon=68.8+random.uniform(-0.8,0.8),
                      cog=random.uniform(0,360), sog=random.uniform(0.5,4),
                      dark=None, report_min=6))
# spoofed MMSI: same identity broadcasting from two positions
SPOOF = 419300001
fleet.append(dict(mmsi=SPOOF, kind="merchant", lat=12.0, lon=65.0, cog=90, sog=10, dark=None, report_min=5))
fleet.append(dict(mmsi=SPOOF, kind="ghost",    lat=22.0, lon=75.0, cog=270, sog=9, dark=None, report_min=5))

lines, corrupted = [], 0
t = T0
while t < T0 + timedelta(hours=HOURS):
    hour = (t - T0).total_seconds()/3600
    for v in fleet:
        if int((t - T0).total_seconds()/60) % v["report_min"] != 0:
            continue
        if v["dark"] and v["dark"][0] <= hour < v["dark"][1]:
            pass_dark = True
        else:
            pass_dark = False
        # advance kinematics regardless (the ship keeps moving while dark)
        dt_h = 1/60
        v["lat"] += v["sog"]*dt_h*math.cos(math.radians(v["cog"]))/60
        v["lon"] += v["sog"]*dt_h*math.sin(math.radians(v["cog"]))/60/math.cos(math.radians(v["lat"]))
        if v["kind"] == "fishing":
            v["cog"] = (v["cog"] + random.uniform(-25, 25)) % 360
            v["sog"] = max(0.3, min(5, v["sog"] + random.uniform(-0.5, 0.5)))
        if pass_dark:
            continue
        sog = 102.3 if random.random() < 0.01 else v["sog"]  # sentinel sprinkle
        s = encode_type1(v["mmsi"], v["lat"], v["lon"], round(sog,1),
                         round(v["cog"],1)%360, int(v["cog"])%360)
        if random.random() < 0.005:  # corruption
            s = s[:-1] + ("0" if s[-1] != "0" else "1"); corrupted += 1
        lines.append(f"{t.isoformat()}\t{s}")
        if random.random() < 0.30:  # second receiver hears it
            lines.append(f"{t.isoformat()}\t{s}")
    t += timedelta(minutes=1)

out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/synthetic_ais_72h.nmea")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines))
print(f"wrote {len(lines)} sentences ({corrupted} corrupted) -> {out}")

# fake Copernicus discovery response: 18 scenes over 72h (2 orbits/day-ish footprint strips)
scenes = {"value": []}
for d in range(9):
    st = T0 + timedelta(hours=8*d + 1.5)
    lon0 = 61 + (d % 3) * 5.5
    scenes["value"].append({
        "Id": f"synthetic-{d:03d}",
        "Name": f"S1A_IW_GRDH_1SDV_{st.strftime('%Y%m%dT%H%M%S')}_SYN",
        "ContentDate": {"Start": st.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
        "Footprint": f"POLYGON(({lon0} 6,{lon0+5.5} 6,{lon0+5.5} 24,{lon0} 24,{lon0} 6))",
        "Attributes": [{"Name": "orbitDirection", "Value": "DESCENDING" if d % 2 else "ASCENDING"},
                        {"Name": "relativeOrbitNumber", "Value": 34 + d % 3}],
    })
Path("data/synthetic_odata.json").write_text(json.dumps(scenes))
print(f"wrote {len(scenes['value'])} synthetic scene records -> data/synthetic_odata.json")
