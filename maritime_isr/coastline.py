"""How far is this position from land? — a real answer, not a proxy.

Area 3 of the IDEX Challenge 82 brief lists the inputs a vessel-type classifier
should read from motion: *"Speed distribution, turn behaviour, path sinuosity,
dwell patterns, operating depth and distance from shore, diurnal rhythm."*

The classifier had six of the seven. **Distance from shore was approximated by
distance to the nearest gazetteer port**, which is a different quantity and a
worse one: the gazetteer holds 34 ports along 2,000 km of coast, so a hull
working the Konkan coast five miles off a beach with no port on it scored as
"120 km from shore". A fishing vessel and a transiting merchant differ sharply
in how close inshore they work, and the feature that was supposed to carry that
was measuring the port network instead.

**Operating depth is still absent and is not fixed here.** It needs bathymetry
this project does not hold, and there is no honest way to approximate seabed
depth from a coastline — the shelf off Gujarat and the shelf off Kerala are
different shapes. It is recorded as a gap rather than filled with a proxy that
would look like the real thing (STATE.md).

**How this works.** The global 1 km land mask is sampled once on a grid over the
AOI, cells that are land *and* adjacent to water are kept as the coastline, and
the result is a KD-tree over the unit sphere. A query is one nearest-neighbour
lookup. The grid is coarse enough to build in about a second and fine enough
that the answer is good to a few kilometres, which is the resolution the
feature is used at — nothing here needs to know whether a ship is 4.1 or 4.4 km
off a beach.

The same land mask the SAR detector and the corpus validator use
(`global_land_mask`), so the generator, the validator and this feature cannot
disagree about where the sea is.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

__all__ = ["distance_to_shore_km", "coastline_points", "GRID_DEG", "R_EARTH_KM"]

R_EARTH_KM = 6371.0

#: Sampling step for the coastline extraction, degrees. 0.05° is about 5.5 km of
#: latitude — one land-mask cell at this latitude — so a finer grid would sample
#: the same mask repeatedly without learning anything new about it.
GRID_DEG = 0.05

#: The box the coastline is extracted over. Generous around the AOI so a track
#: that strays does not fall off the edge of the model and silently get the
#: distance to the nearest *sampled* point instead of the nearest land.
_LAT_MIN, _LAT_MAX = 4.0, 28.0
_LON_MIN, _LON_MAX = 60.0, 80.0

_TREE = None
_POINTS: Optional[np.ndarray] = None


def _unit_xyz(lat_deg, lon_deg):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    return np.stack([np.cos(lat) * np.cos(lon),
                     np.cos(lat) * np.sin(lon),
                     np.sin(lat)], axis=-1)


def coastline_points() -> np.ndarray:
    """(N, 2) array of lat/lon on the coast, built once and cached.

    A cell counts as coast when it is land and at least one of its four
    neighbours is water. Interior land is dropped: it is never the nearest land
    to a point at sea, and keeping it would multiply the tree by twenty for no
    change in any answer.
    """
    global _POINTS
    if _POINTS is not None:
        return _POINTS
    from global_land_mask import globe

    lats = np.arange(_LAT_MIN, _LAT_MAX + GRID_DEG, GRID_DEG)
    lons = np.arange(_LON_MIN, _LON_MAX + GRID_DEG, GRID_DEG)
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    land = globe.is_land(grid_lat, grid_lon)

    # Water on any side => this land cell is on the coast.
    edge = np.zeros_like(land)
    edge[:-1, :] |= land[:-1, :] & ~land[1:, :]
    edge[1:, :] |= land[1:, :] & ~land[:-1, :]
    edge[:, :-1] |= land[:, :-1] & ~land[:, 1:]
    edge[:, 1:] |= land[:, 1:] & ~land[:, :-1]

    _POINTS = np.stack([grid_lat[edge], grid_lon[edge]], axis=-1)
    return _POINTS


def _tree():
    global _TREE
    if _TREE is None:
        from scipy.spatial import cKDTree
        _TREE = cKDTree(_unit_xyz(*coastline_points().T))
    return _TREE


def distance_to_shore_km(lat, lon):
    """Great-circle distance to the nearest coast, in kilometres.

    Accepts a scalar or arrays. A position *on* land returns a small number
    rather than zero or a negative — the model has no inside, and a berth is on
    the coastline by definition, so callers that care about "is she at sea" ask
    the land mask directly rather than reading a sign off this.
    """
    pts = _unit_xyz(np.asarray(lat, dtype=float), np.asarray(lon, dtype=float))
    chord, _ = _tree().query(pts.reshape(-1, 3))
    # Chord length on the unit sphere back to arc length, then to kilometres.
    arc = 2.0 * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
    km = arc * R_EARTH_KM
    if np.ndim(lat) == 0:
        return float(km[0])
    return km.reshape(np.shape(lat))


def _haversine_km(lat1, lon1, lat2, lon2):        # pragma: no cover - reference
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * math.asin(math.sqrt(a))
