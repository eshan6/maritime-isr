"""D1 smoke test — drive the real connector code with FIXTURE input.

**Everything this lands is SYNTHETIC.** It is not real vessel data, not a real
GFW pull, and no number it produces may ever be quoted as a measurement
(CLAUDE.md §4.6). Its only job is to prove the plumbing runs end to end:

    fixture rows -> the real mapping functions -> the real landing layer
                 -> Parquet on disk -> DuckDB query -> the report script

so that when a live pull happens the only new variable is the API response.

It lands into a **separate data root** (`data/_smoke/`) so it can never be
confused with, or contaminate, real landed data.

Run:

    python tools/d1_smoke.py          # land fixtures, then print the report
    python tools/d1_smoke.py --clean  # delete the smoke data root

Expected: every GFW table shows a non-zero row count, `AOI: all inside`, and
re-running produces identical counts (because landing is idempotent).
"""
from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SMOKE_ROOT = REPO / "data" / "_smoke"

UTC = timezone.utc
# Deterministic: same fixtures every run, so idempotency is actually testable.
RNG = random.Random(20260728)

AOI_LAT = (5.0, 25.0)
AOI_LON = (60.0, 78.0)

FLAGS = ["IND", "IRN", "PAN", "ARE", "OMN", "LKA", "MLT"]
NAMES = ["SEA HARRIER", "OCEAN PEARL", "GULF CARRIER", "NIGHT RUNNER",
         "SILVER TIDE", "ARABIAN STAR", "MONSOON QUEEN", "DEEP HORIZON"]


def _pos() -> tuple[float, float]:
    return (round(RNG.uniform(*AOI_LAT), 4), round(RNG.uniform(*AOI_LON), 4))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_events(kind: str, n: int, base: datetime) -> list[dict]:
    """Build GFW-shaped event payloads — the same shape map_event() parses."""
    out = []
    for i in range(n):
        lat, lon = _pos()
        start = base + timedelta(hours=RNG.randint(0, 8 * 7 * 24))
        dur = RNG.uniform(0.5, 30.0)
        vid = f"v-{RNG.randint(1000, 9999)}"
        ev = {
            "id": f"{kind}-{i:04d}",
            "type": {"encounters": "ENCOUNTER", "loitering": "LOITERING",
                     "port_visits": "PORT_VISIT", "gaps": "GAP"}[kind],
            "start": _iso(start),
            "end": _iso(start + timedelta(hours=dur)),
            "position": {"lat": lat, "lon": lon},
            "confidence": RNG.choice(["2", "3", "4"]),
            "vessel": {
                "id": vid,
                "ssvid": str(RNG.randint(200000000, 599999999)),
                "name": RNG.choice(NAMES),
                "flag": RNG.choice(FLAGS),
                "type": RNG.choice(["FISHING", "CARGO", "CARRIER", "BUNKER"]),
            },
        }
        if kind == "encounters":
            ev["vessels"] = [ev["vessel"], {
                "id": f"v-{RNG.randint(1000, 9999)}",
                "ssvid": str(RNG.randint(200000000, 599999999)),
                "name": RNG.choice(NAMES),
                "flag": RNG.choice(FLAGS),
            }]
            ev["encounter"] = {"type": "FISHING-CARRIER"}
        if kind == "gaps":
            ev["gap"] = {"distanceKm": round(RNG.uniform(20, 600), 1),
                         "impliedSpeedKnots": round(RNG.uniform(2, 18), 1)}
        out.append(ev)
    return out


def make_vessel_payload() -> dict:
    """A vessel identity payload with a rename and a change of owner."""
    n1, n2 = RNG.sample(NAMES, 2)
    f1, f2 = RNG.sample(FLAGS, 2)
    m1, m2 = str(RNG.randint(2 * 10**8, 5 * 10**8)), str(RNG.randint(2 * 10**8, 5 * 10**8))
    return {
        "registryInfo": [
            {"ssvid": m1, "imo": str(RNG.randint(9000000, 9999999)), "shipname": n1,
             "callsign": "AAA111", "flag": f1, "lengthM": round(RNG.uniform(30, 250), 1),
             "tonnageGt": RNG.randint(500, 40000), "sourceCode": ["IMO"],
             "transmissionDateFrom": "2019-01-01T00:00:00Z",
             "transmissionDateTo": "2023-06-30T00:00:00Z"},
            {"ssvid": m2, "imo": str(RNG.randint(9000000, 9999999)), "shipname": n2,
             "callsign": "BBB222", "flag": f2, "lengthM": round(RNG.uniform(30, 250), 1),
             "sourceCode": ["IMO"],
             "transmissionDateFrom": "2023-07-01T00:00:00Z",
             "transmissionDateTo": "2026-07-01T00:00:00Z"},
        ],
        "registryOwners": [
            {"name": "BLUEWATER HOLDINGS", "flag": "ARE", "ssvid": m1,
             "sourceCode": ["REG"], "dateFrom": "2019-01-01T00:00:00Z",
             "dateTo": "2023-06-30T00:00:00Z"},
            {"name": "MERIDIAN SHIPPING", "flag": "PAN", "ssvid": m2,
             "sourceCode": ["REG"], "dateFrom": "2023-07-01T00:00:00Z", "dateTo": None},
        ],
        "selfReportedInfo": [
            {"ssvid": m1, "shipname": n1, "flag": f1,
             "firstTransmissionDate": "2019-02-01T00:00:00Z",
             "lastTransmissionDate": "2023-06-01T00:00:00Z"},
        ],
    }


def make_sar_csv(n: int, base: datetime) -> str:
    lines = ["lat,lon,timestamp,length_m,matched,presence_score"]
    for _ in range(n):
        lat, lon = _pos()
        ts = base + timedelta(hours=RNG.randint(0, 8 * 7 * 24))
        lines.append(
            f"{lat},{lon},{_iso(ts)},{round(RNG.uniform(15, 300), 1)},"
            f"{RNG.choice(['true', 'false'])},{round(RNG.uniform(0.5, 1.0), 2)}"
        )
    return "\n".join(lines) + "\n"


def land_everything() -> None:
    # Point the config at the smoke root BEFORE the connectors resolve paths.
    from maritime_isr import config as cfg_mod
    from maritime_isr.ingest import landing

    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    cfg_mod.cfg.data_root = SMOKE_ROOT
    landing.cfg.data_root = SMOKE_ROOT

    from maritime_isr.ingest import gfw, gfw_events, gfw_vessels
    from maritime_isr.ingest.landing import land_table

    # Pinned, NOT now()-8weeks. A moving base would make every run generate
    # genuinely different detections, and the re-run would look like a broken
    # idempotency guarantee when it is really just different input.
    base = datetime(2026, 6, 2, tzinfo=UTC)

    print("Landing FIXTURE data (synthetic — not real vessels) ...\n")

    # --- events -----------------------------------------------------------
    counts = {"encounters": 120, "loitering": 200, "port_visits": 160, "gaps": 90}
    for kind, n in counts.items():
        raw = make_events(kind, n, base)
        rows = [r for r in (gfw_events.map_event(e, kind) for e in raw) if r]
        spec = gfw_events.EVENT_SPECS[kind]
        land_table(rows, table=spec["table"], key_fields=("event_id",), day_field="start_time")
        print(f"  {spec['table']:<24} {len(rows):>5} rows")

    # --- vessel identity, harvested from the events just landed -----------
    ids = gfw_vessels.vessel_ids_from_events()
    ident, owners, current = [], [], []
    for vid in ids:
        payload = make_vessel_payload()
        ident.extend(gfw_vessels.map_identity_rows(vid, payload))
        owners.extend(gfw_vessels.map_owner_rows(vid, payload))
        cur = gfw_vessels.map_current_row(vid, payload)
        if cur:
            current.append(cur)

    land_table(ident, table=gfw_vessels.IDENTITY_TABLE,
               key_fields=("vessel_id", "record_kind", "mmsi", "ship_name", "valid_from"),
               day_field="valid_from")
    land_table(owners, table=gfw_vessels.OWNERS_TABLE,
               key_fields=("vessel_id", "owner_name", "valid_from"), day_field="valid_from")
    land_table(current, table=gfw_vessels.CURRENT_TABLE,
               key_fields=("vessel_id",), day_field="last_seen")
    print(f"  {gfw_vessels.IDENTITY_TABLE:<24} {len(ident):>5} rows  ({len(ids)} vessels)")
    print(f"  {gfw_vessels.OWNERS_TABLE:<24} {len(owners):>5} rows")
    print(f"  {gfw_vessels.CURRENT_TABLE:<24} {len(current):>5} rows")

    # --- SAR per-detection, through the real CSV importer -----------------
    csv_path = SMOKE_ROOT / "fixture_sar.csv"
    csv_path.write_text(make_sar_csv(300, base), encoding="utf-8")
    gfw.import_portal_csv(csv_path)

    # --- SAR gridded presence --------------------------------------------
    cells = []
    for _ in range(250):
        lat, lon = _pos()
        day = (base + timedelta(days=RNG.randint(0, 55))).strftime("%Y-%m-%d")
        c = gfw.map_grid_cell({"lat": lat, "lon": lon, "date": day,
                               "detections": RNG.randint(1, 12)})
        if c:
            cells.append(c)
    land_table(cells, table=gfw.GRID_TABLE,
               key_fields=("cell_lat", "cell_lon", "observed_date"),
               day_field="observed_date")
    print(f"  {gfw.GRID_TABLE:<24} {len(cells):>5} rows")

    # --- registries, through the real parsers ----------------------------
    import duckdb

    from maritime_isr.ingest import registries as reg

    con = duckdb.connect(str(SMOKE_ROOT / "misr.duckdb"))
    reg._ensure_snapshot_meta(con)
    # Also pinned. Registry snapshots are deliberately append-only — a re-run
    # with a NEW as_of would correctly add another version, which is the
    # designed behaviour but would muddy the idempotency demonstration.
    as_of_old = datetime(2026, 6, 28, tzinfo=UTC)
    as_of_new = datetime(2026, 7, 28, tzinfo=UTC)

    ofac = "\n".join(
        f'{9000 + i},"{RNG.choice(NAMES)} {i}","vessel","IRAN","-0-","CS{i:04d}",'
        f'"Tanker","1,900","2,100","{RNG.choice(FLAGS)}","OWNER {i}","-0-"'
        for i in range(400)
    ) + "\n"
    # Two snapshots so the diff-on-refresh path is exercised, not just asserted.
    reg._fetch = lambda *a, **k: ofac.encode()          # type: ignore[assignment]
    reg.refresh_ofac(con, as_of_old)
    ofac2 = ofac.replace('9399,"', '9999,"')
    reg._fetch = lambda *a, **k: ofac2.encode()         # type: ignore[assignment]
    reg.refresh_ofac(con, as_of_new)

    wpi = "World Port Index Number,Main Port Name,Country Code,Latitude,Longitude,Harbor Size,Harbor Type\n"
    wpi += "48220,MUMBAI,IN,18.9200,72.8300,Large,Coastal Natural\n"
    wpi += "48180,KANDLA,IN,23.0167,70.2167,Medium,River Natural\n"
    wpi += "48300,COCHIN,IN,9.9667,76.2667,Medium,Coastal Natural\n"
    wpi += "49100,KARACHI,PK,24.8500,66.9700,Large,Coastal Natural\n"
    wpi += "53000,ROTTERDAM,NL,51.9500,4.1400,Large,Coastal Natural\n"
    reg._fetch = lambda *a, **k: wpi.encode()           # type: ignore[assignment]
    reg.refresh_wpi(con, as_of_new)
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="delete the smoke data root and exit")
    args = ap.parse_args()

    if args.clean:
        if SMOKE_ROOT.exists():
            shutil.rmtree(SMOKE_ROOT)
            print(f"removed {SMOKE_ROOT}")
        else:
            print("nothing to clean")
        return 0

    print("=" * 96)
    print("D1 SMOKE TEST — FIXTURE DATA ONLY, NOT REAL VESSELS")
    print("=" * 96)
    land_everything()

    print("\n" + "=" * 96)
    print("Now the report, reading what was just landed:")
    print("=" * 96)
    env_report = subprocess.run(
        [sys.executable, str(REPO / "tools" / "d1_report.py")],
        env={**__import__("os").environ, "MISR_DATA_ROOT": str(SMOKE_ROOT)},
        cwd=str(REPO),
    )
    print("\nReminder: every row above is synthetic fixture data. It proves the "
          "pipes run,\nnot that anything was observed at sea.")
    print(f"Clean up with:  python tools/d1_smoke.py --clean")
    return env_report.returncode


if __name__ == "__main__":
    raise SystemExit(main())
