"""THE H3 helper. One module, every resolution, always computed from coordinates.

H3 carves the earth into hexagons. A "resolution" is how fine: res 4 cells are
~1,770 km² across, res 9 are ~170 m. Stamping every located record with its cell
turns Phase 3's central question — *which ships could be at this radar contact?*
— from a geometry problem into a hash join.

**Why this module exists in this shape (ADR-015).** Until 2026-07-29 there were
**two** helpers: `tiling.py` defaulting to res 6, and this file using res 7 and 9,
with res 4 and 8 hard-coded inside individual modules. Ingest stamped res-7/9
cells while the fusion core joined on res-6 cells — and because different
resolutions produce entirely different cell ids, **those joins returned nothing
at all.** It had not yet caused damage only because nothing consumed the ingest
tables yet. `tiling.py` is deleted; this is the only helper.

**The rule that matters most: never derive a coarser cell from a finer one.**

H3's hierarchy is not geometrically nested — a res-7 cell is not fully contained
in its res-6 parent — so `cell_to_parent` and direct computation disagree for
points near cell boundaries. Measured on 200,000 random points inside AOI v1:

    parent(res-7 cell) != direct res-6 cell : 14,398  (7.199%)
    parent(res-9 cell) != direct res-7 cell : 12,341  (6.170%)

Roughly **1 record in 14** would be filed under the wrong coarse cell —
position-dependent, intermittent, invisible in row counts, and far harder to
diagnose than a uniform mismatch. So this module deliberately offers **no
parent-derivation function**, and a test asserts no module calls
`cell_to_parent`. If you need a coarse cell, compute it from lat/lon.

**Which resolution for what** (all five are in active use):

| Res | Cell size   | Used for |
|-----|-------------|----------|
| 4   | ~1,770 km²  | coverage model — reception is a regional property |
| 6   | ~36 km²     | fusion gating, track/detection indexing |
| 7   | ~5 km²      | **the canonical join key** (ADR-003), encounter bucketing |
| 8   | ~0.7 km²    | static-object clustering (rigs, buoys, wrecks) |
| 9   | ~0.1 km²    | fine matching (ADR-003) |

Targets h3 v4 (`latlng_to_cell`, `grid_disk`) with a v3 fallback shim.
"""
from __future__ import annotations

import h3

# Named resolutions. Nothing outside this module should hard-code an integer.
R4 = 4   # coverage model — regional
R6 = 6   # fusion gating, track/detection indexing
R7 = 7   # canonical join key (ADR-003)
R8 = 8   # static-object clustering
R9 = 9   # fine matching (ADR-003)

#: Every resolution the project uses. Stamped onto located records at ingest so
#: any consumer can join at the resolution it needs without recomputation — and
#: so nobody is ever tempted to derive one from another.
RESOLUTIONS: tuple[int, ...] = (R4, R6, R7, R8, R9)

#: The old `tiling.py` default. Preserved so fusion behaviour is unchanged by
#: the unification itself — this refactor fixes the structure, it does not
#: re-tune the fusion core.
DEFAULT_RES = R6

if not hasattr(h3, "latlng_to_cell"):  # pragma: no cover - v3 fallback
    h3.latlng_to_cell = h3.geo_to_h3
    h3.cell_to_latlng = h3.h3_to_geo
    h3.grid_disk = h3.k_ring
    h3.cell_to_boundary = h3.h3_to_geo_boundary


# --------------------------------------------------------------------------
# the one way to get a cell
# --------------------------------------------------------------------------

def cell(lat: float, lon: float, res: int = DEFAULT_RES) -> str:
    """The cell containing this position, at `res`. Computed, never derived."""
    return h3.latlng_to_cell(lat, lon, res)


def cells_at(lat: float, lon: float, *resolutions: int) -> tuple[str, ...]:
    """Cells at several resolutions, each computed independently from lat/lon."""
    return tuple(cell(lat, lon, r) for r in (resolutions or RESOLUTIONS))


def index_all(lat: float, lon: float) -> dict[str, str]:
    """`{'h3_r4': ..., 'h3_r6': ..., ...}` for every project resolution."""
    return {f"h3_r{r}": cell(lat, lon, r) for r in RESOLUTIONS}


def cell_r7(lat: float, lon: float) -> str:
    return cell(lat, lon, R7)


def cell_r9(lat: float, lon: float) -> str:
    return cell(lat, lon, R9)


def index_both(lat: float, lon: float) -> tuple[str, str]:
    """(h3_r7, h3_r9) — the ADR-003 join pair. Both computed from lat/lon."""
    return cell(lat, lon, R7), cell(lat, lon, R9)


# --------------------------------------------------------------------------
# neighbourhoods and coverage
# --------------------------------------------------------------------------

def disk(cell_id: str, k: int = 1) -> list[str]:
    """All cells within k rings of `cell_id`, inclusive. For gating windows."""
    return list(h3.grid_disk(cell_id, k))


#: Phase 2/3 call this `neighbors`; same function, so call sites read naturally
#: in both places without a second implementation existing.
neighbors = disk


def cell_center(cell_id: str) -> tuple[float, float]:
    return h3.cell_to_latlng(cell_id)


def footprint_cells(polygon_latlon: list[tuple[float, float]],
                    res: int = DEFAULT_RES) -> list[str]:
    """Cells covering a polygon — e.g. a Sentinel-1 scene footprint.

    Lets a scene row be joined against AIS or detections by cell id, which is
    how "was this patch of ocean actually observed?" gets answered cheaply.
    """
    poly = h3.LatLngPoly(polygon_latlon)
    return list(h3.polygon_to_cells(poly, res))


def cells_covering_bbox(lat_min, lat_max, lon_min, lon_max, res: int = R7) -> set[str]:
    """Every H3 cell whose centroid falls in the bbox (grid-sampled)."""
    step = 0.05 if res <= 6 else (0.02 if res == 7 else 0.005)
    cells: set[str] = set()
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            cells.add(cell(lat, lon, res))
            lon += step
        lat += step
    return cells


# --------------------------------------------------------------------------
# record helpers
# --------------------------------------------------------------------------

def enrich_position(report_dict: dict) -> dict:
    """Stamp every project resolution onto a position-report-like dict."""
    report_dict.update(index_all(report_dict["lat"], report_dict["lon"]))
    return report_dict


def dedup_key(mmsi: int, timestamp_iso: str, lat: float, lon: float) -> str:
    """Deterministic dedup key: MMSI + timestamp + rounded position (~11 m)."""
    return f"{mmsi}|{timestamp_iso}|{lat:.4f}|{lon:.4f}"
