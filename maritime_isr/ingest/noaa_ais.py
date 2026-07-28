"""Unit 0.4 — NOAA historical AIS -> same canonical schema.

PARKED: structurally cannot serve AOI_V1. Marine Cadastre publishes AIS from
the US Coast Guard receiver network, subsetted to the US Exclusive Economic
Zone. Our AOI is the Arabian Sea (5-25N, 60-78E), where coverage is zero — not
sparse, zero. This connector is only useful if the AOI moves to US waters.
Kept intact because the mapping to the canonical schema is correct and would be
reusable. See DATA_SOURCES.md.

NOAA/Marine Cadastre publishes daily/zonal AIS CSVs. We fetch a month, filter
to the AOI, map to canonical PositionReports, and land them in the same hourly
Parquet store the live feed writes to (identical schema — a historical row and a
live row are indistinguishable downstream, which is the point).
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timezone

import requests

from ..config import cfg
from ..schemas import PositionReport, Provenance
from ..writer import write_position_reports

SOURCE_ID = "noaa-ais"
# Marine Cadastre AIS archive (public). Pattern by year; zonal files vary by year.
BASE = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"


def _map_row(r: dict) -> dict | None:
    try:
        lat, lon = float(r["LAT"]), float(r["LON"])
        if not cfg.aoi.contains(lat, lon):
            return None
        ts = datetime.fromisoformat(r["BaseDateTime"]).replace(tzinfo=timezone.utc)
        pr = PositionReport(
            mmsi=int(r["MMSI"]),
            imo=int(r["IMO"]) if r.get("IMO", "").isdigit() else None,
            lat=lat, lon=lon,
            sog=float(r["SOG"]) if r.get("SOG") else None,
            cog=float(r["COG"]) if r.get("COG") else None,
            heading=float(r["Heading"]) if r.get("Heading") else None,
            timestamp=ts,
            receiver_source=SOURCE_ID,
            prov=Provenance(source_id=SOURCE_ID, source_ref=r["MMSI"], acquired_at=ts),
        )
        d = pr.model_dump()
        prov = pr.prov.stamp()
        d.pop("prov")
        d.update(prov)
        return d
    except Exception:  # noqa: BLE001
        return None


def run(month: str) -> int:
    """month = 'YYYY-MM'. Downloads matching NOAA zips, filters to AOI, lands them."""
    year = month.split("-")[0]
    url = f"{BASE}/{year}/AIS_{month.replace('-', '_')}.zip"
    print(f"[noaa] fetching {url}")
    resp = requests.get(url, timeout=600)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    total = 0
    for name in zf.namelist():
        if not name.lower().endswith(".csv"):
            continue
        rows = []
        with zf.open(name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, "utf-8"))
            for r in reader:
                m = _map_row(r)
                if m:
                    rows.append(m)
        if rows:
            w = write_position_reports(rows, store="ais")
            total += sum(w.values())
            print(f"[noaa] {name}: landed {sum(w.values())} AOI rows")
    print(f"[noaa] done: {total} rows for {month}")
    return 0
