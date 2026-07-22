"""H3 helpers. Resolution 7 (~5 km) for joins, resolution 9 for fine matching.

Decided in unit 0.0, used everywhere. Every spatial record is stamped with both
indices at ingest so Phase 3 association is a cheap H3 equality/neighbor join
instead of a geometry cross-product. Targets h3 v4 with a v3 fallback shim.
"""
from __future__ import annotations

import h3

R7 = 7
R9 = 9

if not hasattr(h3, "latlng_to_cell"):  # pragma: no cover - v3 fallback
    h3.latlng_to_cell = h3.geo_to_h3
    h3.cell_to_latlng = h3.h3_to_geo
    h3.grid_disk = h3.k_ring
    h3.cell_to_boundary = h3.h3_to_geo_boundary


def cell_r7(lat: float, lon: float) -> str:
    return h3.latlng_to_cell(lat, lon, R7)


def cell_r9(lat: float, lon: float) -> str:
    return h3.latlng_to_cell(lat, lon, R9)


def index_both(lat: float, lon: float) -> tuple[str, str]:
    """Return (h3_r7, h3_r9) for a position. Use at ingest time."""
    return cell_r7(lat, lon), cell_r9(lat, lon)


def disk(cell: str, k: int = 1) -> list[str]:
    """All cells within k rings of `cell` (inclusive). For gating neighborhoods."""
    return list(h3.grid_disk(cell, k))


def cell_center(cell: str) -> tuple[float, float]:
    return h3.cell_to_latlng(cell)


def cells_covering_bbox(lat_min, lat_max, lon_min, lon_max, res: int = R7) -> set[str]:
    """Every H3 cell whose centroid falls in the bbox (grid-sampled)."""
    step = 0.05 if res <= 6 else (0.02 if res == 7 else 0.005)
    cells: set[str] = set()
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            cells.add(h3.latlng_to_cell(lat, lon, res))
            lon += step
        lat += step
    return cells


def enrich_position(report_dict: dict) -> dict:
    """Stamp h3_r7/h3_r9 onto a position-report-like dict."""
    r7, r9 = index_both(report_dict["lat"], report_dict["lon"])
    report_dict["h3_r7"] = r7
    report_dict["h3_r9"] = r9
    return report_dict


def dedup_key(mmsi: int, timestamp_iso: str, lat: float, lon: float) -> str:
    """Deterministic dedup key: MMSI + timestamp + rounded position (~11 m)."""
    return f"{mmsi}|{timestamp_iso}|{lat:.4f}|{lon:.4f}"
