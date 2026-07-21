"""Common spatial grid (H3). Decided in Phase 0 per roadmap 0.2:
all detections, tracks, and scene footprints index onto the same cells so
Phase 3 association is a hash join on cell id + time bucket, not a
geometry intersection at query time.
"""
from __future__ import annotations

import h3

from .config import H3_RESOLUTION


def cell(lat: float, lon: float, res: int = H3_RESOLUTION) -> str:
    return h3.latlng_to_cell(lat, lon, res)


def neighbors(cell_id: str, k: int = 1) -> list[str]:
    """k-ring — used by Phase 3 gating to widen a match window by
    uncertainty-cone radius without geometry math."""
    return list(h3.grid_disk(cell_id, k))


def footprint_cells(polygon_latlon: list[tuple[float, float]],
                    res: int = H3_RESOLUTION) -> list[str]:
    """Cells covering a polygon (e.g. a Sentinel-1 scene footprint), so a
    scene row in the catalog can be joined against AIS by cell id."""
    poly = h3.LatLngPoly(polygon_latlon)
    return list(h3.polygon_to_cells(poly, res))
