"""D1 — Global Fishing Watch SAR connector.

**Read this before using it: the API does not give per-detection SAR data.**

We assumed GFW's API would return individual radar detections — one row per
blip, with position, timestamp, length estimate and whether it matched a vessel
broadcasting AIS. Reconnaissance on 2026-07-28 established that it does not.
There are two distinct products and only one is automatable:

1. **Gridded SAR presence** (`4wings/report`, dataset
   `public-global-sar-presence:latest`) — an *aggregate*. Each row is a grid
   cell and a **count** of detections inside it, at ~0.1 degree (LOW) or ~0.01
   degree (HIGH) resolution. There is no `length_m` and no AIS-match flag.
2. **Per-detection SAR** — has `length_m`, `presence_score`, `matching_score`,
   `fishing_score` and matched/unmatched status, but is available **only** as a
   CSV export from GFW's browser Data Download Portal. It is not an API
   endpoint; the Bulk Download API covers fixed infrastructure only.

**Why we do not quietly treat grid cells as detections.** A cell saying "7
detections somewhere in this 1 km square" is not seven contacts with positions.
Feeding cell centres to the Phase 3 association engine as if they were radar
contacts would invent contacts that were never observed at those coordinates,
and association would then match real AIS tracks to them — manufacturing exactly
the phantom dark vessels CLAUDE.md §6 forbids. So:

  * `run_gridded()` lands cells into `gfw_sar_presence_grid`, a table clearly
    marked as aggregate, and **never** into the `detections` store.
  * `import_portal_csv()` lands true per-detection rows into
    `gfw_sar_detections` in canonical Detection shape.

**Current status: the upstream data is offline.** GFW announced on 2026-07-03
that the SAR vessel detections and fixed infrastructure datasets are down across
the map, the APIs and the download portal, pending migration to Sentinel-1C/1D,
with a gap of at least one month. Both functions here degrade to "landed
nothing, here is why" rather than failing. See DATA_SOURCES.md.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..config import AOI_V1
from . import gfw_client as gc
from .checks import report_landed
from .landing import land_raw, land_raw_json, land_table, stamp_envelope, stamp_h3

SOURCE_ID = "gfw-sar"
GRID_TABLE = "gfw_sar_presence_grid"
DETECTION_TABLE = "gfw_sar_detections"

SAR_DATASET = "public-global-sar-presence:latest"


def _parse_ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip().replace("Z", "+00:00")
    for parse in (
        lambda x: datetime.fromisoformat(x),
        lambda x: datetime.strptime(x, "%Y-%m-%d"),
        lambda x: datetime.strptime(x, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            dt = parse(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "", "NA", "null") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 1. gridded presence — aggregate, via the API
# --------------------------------------------------------------------------

def fetch_gridded(start: date, end: date, resolution: str = "HIGH") -> list[dict]:
    """Fetch gridded SAR presence over the AOI. Aggregate counts, not contacts."""
    body = {
        "geojson": gc.aoi_geojson(AOI_V1),
        "datasets": [SAR_DATASET],
    }
    params = {
        "spatial-resolution": resolution,     # LOW ~0.1deg, HIGH ~0.01deg
        "temporal-resolution": "DAILY",
        "spatial-aggregation": "false",
        "datasets[0]": SAR_DATASET,
        "date-range": f"{start.isoformat()},{end.isoformat()}",
        "format": "JSON",
    }
    resp = gc.request("POST", "4wings/report", params=params, json_body=body)
    if resp.status_code >= 400:
        raise gc.GFWUnavailable(
            f"4wings/report returned HTTP {resp.status_code}. As of 2026-07-03 the SAR "
            f"datasets are offline pending the Sentinel-1C/1D migration. "
            f"Response: {resp.text[:300]}"
        )
    payload = resp.json()
    land_raw_json(SOURCE_ID, f"sar_grid_{start:%Y%m%d}_{end:%Y%m%d}.json", payload)

    entries = payload.get("entries") or []
    # entries is usually a list of per-dataset lists
    flat: list[dict] = []
    for e in entries:
        if isinstance(e, list):
            flat.extend(x for x in e if isinstance(x, dict))
        elif isinstance(e, dict):
            # {"public-global-sar-presence": [...]} shape
            nested = False
            for v in e.values():
                if isinstance(v, list):
                    flat.extend(x for x in v if isinstance(x, dict))
                    nested = True
            if not nested:
                flat.append(e)
    return flat


def map_grid_cell(cell: dict) -> dict | None:
    lat, lon = _f(cell.get("lat")), _f(cell.get("lon"))
    ts = _parse_ts(cell.get("date"))
    if lat is None or lon is None or ts is None:
        return None
    if not AOI_V1.contains(lat, lon):
        return None

    row = {
        # NOTE: cell_lat/cell_lon, not lat/lon — naming that refuses to be
        # mistaken for a contact position downstream.
        "cell_lat": lat,
        "cell_lon": lon,
        "lat": lat,   # kept only so the shared H3 stamper can index the cell
        "lon": lon,
        "observed_date": ts,
        "detection_count": cell.get("detections"),
        "hours": _f(cell.get("hours")),
        "flag": cell.get("flag"),
        "vessel_type": cell.get("vesselType"),
        "is_aggregate": True,   # explicit: this row is a COUNT, not a contact
    }
    stamp_h3(row)
    stamp_envelope(
        row, source_id=SOURCE_ID,
        source_ref=f"grid:{lat:.4f},{lon:.4f}:{ts:%Y-%m-%d}",
        acquired_at=ts, confidence=None,
    )
    return row


def run_gridded(weeks: int = 8, resolution: str = "HIGH") -> int:
    """Land gridded SAR presence for the AOI. Aggregate only."""
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    start = end - timedelta(weeks=weeks)
    print(f"[gfw-sar] gridded presence {start} .. {end}  res={resolution}  AOI={AOI_V1.name}")
    print("[gfw-sar] NOTE: these are per-cell COUNTS, not individual contacts. "
          "They cannot be fed to the Phase 3 association engine.")

    try:
        raw = fetch_gridded(start, end, resolution)
    except (gc.GFWUnavailable, gc.GFWAuthError) as e:
        print(f"[gfw-sar] gridded presence unavailable — {e}")
        return 0

    rows = [r for r in (map_grid_cell(c) for c in raw) if r is not None]
    if not rows:
        print("[gfw-sar] no grid cells returned for this window. Expected while the "
              "SAR datasets are offline (see DATA_SOURCES.md).")
        return 0

    written = land_table(rows, table=GRID_TABLE,
                         key_fields=("cell_lat", "cell_lon", "observed_date"),
                         day_field="observed_date")
    report_landed("gfw-sar", GRID_TABLE, written, len(rows), noun="grid cell")
    return 0


# --------------------------------------------------------------------------
# 2. per-detection — from the manually downloaded portal CSV
# --------------------------------------------------------------------------

# The portal's column names have varied between releases; accept the known
# aliases rather than breaking on a rename.
_COL_ALIASES = {
    "lat": ("lat", "latitude", "detect_lat"),
    "lon": ("lon", "longitude", "detect_lon"),
    "timestamp": ("timestamp", "detect_timestamp", "date", "detection_time", "acquired_at"),
    "length_m": ("length_m", "length", "vessel_length_m"),
    "detection_id": ("detect_id", "detection_id", "id"),
    "scene_id": ("scene_id", "sar_scene_id", "granule_id"),
    "matched": ("matched", "is_matched", "ais_matched", "matched_to_ais"),
    "mmsi": ("mmsi", "ssvid", "matched_mmsi"),
    "presence_score": ("presence_score",),
    "matching_score": ("matching_score", "match_score"),
    "fishing_score": ("fishing_score",),
}


def _pick(row: dict, field: str):
    for alias in _COL_ALIASES[field]:
        if alias in row and row[alias] not in ("", None):
            return row[alias]
        # tolerate case differences
        for k in row:
            if k.lower() == alias and row[k] not in ("", None):
                return row[k]
    return None


def _truthy(v) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "matched"):
        return True
    if s in ("false", "0", "no", "n", "unmatched"):
        return False
    return None


def map_portal_row(r: dict, idx: int) -> dict | None:
    lat, lon = _f(_pick(r, "lat")), _f(_pick(r, "lon"))
    ts = _parse_ts(_pick(r, "timestamp"))
    if lat is None or lon is None or ts is None:
        return None
    if not AOI_V1.contains(lat, lon):
        return None

    det_id = _pick(r, "detection_id") or f"gfwsar-{ts:%Y%m%dT%H%M%S}-{lat:.5f}-{lon:.5f}"
    matched = _truthy(_pick(r, "matched"))
    presence = _f(_pick(r, "presence_score"))

    row = {
        "detection_id": str(det_id),
        "scene_id": _pick(r, "scene_id") or "gfw-portal",
        "method": "gfw",
        "lat": lat,
        "lon": lon,
        "length_m": _f(_pick(r, "length_m")),
        "acquired_at_detection": ts,
        # The dark-vessel-relevant bit: did this radar blip line up with a ship
        # that was broadcasting AIS? Unmatched is the interesting case.
        "matched_to_ais": matched,
        "matched_mmsi": _pick(r, "mmsi"),
        "presence_score": presence,
        "matching_score": _f(_pick(r, "matching_score")),
        "fishing_score": _f(_pick(r, "fishing_score")),
        "is_aggregate": False,
    }
    stamp_h3(row)
    stamp_envelope(
        row, source_id=SOURCE_ID, source_ref=f"portal:{det_id}",
        acquired_at=ts, confidence=presence,
    )
    return row


def import_portal_csv(path: str | Path) -> int:
    """Land a per-detection SAR CSV exported from GFW's Data Download Portal.

    This is the human-in-the-loop half of the SAR story: the file is downloaded
    by hand from the portal, then handed to this function, which lands it with
    the same provenance, H3 and idempotency guarantees as an API pull.

        maritime-isr ingest gfw-sar-csv --path C:\\Users\\you\\Downloads\\sar.csv
    """
    p = Path(path)
    if not p.exists():
        print(f"[gfw-sar] file not found: {p}")
        return 1

    payload = p.read_bytes()
    land_raw(SOURCE_ID, p.name, payload)

    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows, skipped = [], 0
    for i, r in enumerate(reader):
        mapped = map_portal_row(r, i)
        if mapped is None:
            skipped += 1
        else:
            rows.append(mapped)

    if not rows:
        print(f"[gfw-sar] {p.name}: no usable in-AOI rows "
              f"({skipped} skipped). Check the export covers 5-25N / 60-78E.")
        return 1

    written = land_table(rows, table=DETECTION_TABLE,
                         key_fields=("detection_id",), day_field="acquired_at_detection")
    matched = sum(1 for r in rows if r["matched_to_ais"] is True)
    unmatched = sum(1 for r in rows if r["matched_to_ais"] is False)
    report_landed("gfw-sar", DETECTION_TABLE, written, len(rows), noun="detection")
    print(f"[gfw-sar]   {skipped} skipped/out-of-AOI")
    print(f"[gfw-sar]   matched to AIS: {matched}   unmatched: {unmatched}   "
          f"unknown: {len(rows) - matched - unmatched}")
    print("[gfw-sar]   'unmatched' is the dark-vessel-relevant subset, but it is NOT "
          "by itself a dark vessel — see CLAUDE.md §6 on coverage.")
    return 0


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

def run(weeks: int = 8, csv_path: str | None = None, resolution: str = "HIGH") -> int:
    if csv_path:
        return import_portal_csv(csv_path)
    return run_gridded(weeks=weeks, resolution=resolution)
