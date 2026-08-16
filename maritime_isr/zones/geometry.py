"""The spatial primitives for the zone layer. One of each, deliberately.

**Two rules here are load-bearing and both exist because of ADR-015.**

1. *One containment test.* Everything that asks "is this position in this
   zone" calls `contains`. When two modules implement that question slightly
   differently — one using a bounding box, one using the polygon, one
   forgetting that a res-6 covering is not the geometry — the disagreement is
   invisible in row counts and shows up as an analysis that quietly never
   fires.

2. *One cell-index rule.* A zone's H3 covering is an **index, not the
   geometry**. It is deliberately DILATED by one ring, so it over-covers; the
   exact polygon test then narrows. Getting this backwards — using a tight
   covering as if it were the geometry — loses every vessel in a zone smaller
   than a cell, which is most port limits.

Coordinates are (lon, lat) inside shapely, because that is what shapely and
GeoJSON mean by x and y, and (lat, lon) everywhere else in this project because
that is what a position report means. The conversion happens here and only
here; every public function in this module takes and returns **lat, lon** in
that order, and shapely objects are treated as an implementation detail that
happens to be lon/lat inside.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import h3
import shapely
from shapely.geometry import (LineString, MultiPolygon, Point, Polygon,
                              mapping, shape)
from shapely.ops import unary_union

from .. import h3util as tiling

__all__ = [
    "EARTH_R_M", "M_PER_NM", "INDEX_RES",
    "circle_polygon", "corridor_polygon", "polygon_from_cells",
    "cells_covering", "contains", "distance_to_m",
    "geom_to_wkt", "geom_from_wkt", "geom_to_geojson",
    "area_km2", "centroid_latlon", "bearing_deg",
]

#: WGS-84 mean radius. Same constant the scenario geodesy uses, and it is
#: repeated rather than imported because `zones/` must not depend on
#: `scenario/` — this layer ships in a checkout with no generator.
EARTH_R_M = 6_371_008.8
M_PER_NM = 1852.0

#: The resolution the zone cell index is built at. Res 6 is ~36 km² (~7 km
#: across): coarse enough that the EEZ is tens of thousands of cells rather
#: than millions, fine enough that the dilated covering of a 5 km port limit is
#: a handful of cells rather than a county.
INDEX_RES = tiling.R6


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------

def circle_polygon(lat: float, lon: float, radius_m: float,
                   steps: int = 64) -> Polygon:
    """A circle of true radius `radius_m` around a position, as a polygon.

    Built by walking true bearings rather than by buffering in degrees. A
    degree of longitude is 111 km at the equator and 101 km at 25N, so a
    degree-space buffer over this AOI would be 10% out in one axis and round —
    which for a 5 km port limit means half a kilometre of boundary error in a
    layer whose entire job is to say which side of a boundary something is on.
    """
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    ring = [_destination(lat, lon, 360.0 * i / steps, radius_m)
            for i in range(steps)]
    ring.append(ring[0])
    return Polygon([(lo, la) for la, lo in ring])


def corridor_polygon(path: Sequence[tuple[float, float]],
                     half_width_m: float, cap_steps: int = 16) -> Polygon:
    """A corridor of true half-width around a (lat, lon) polyline.

    Used for shipping lanes, where "the lane" is a centreline plus a tolerance
    rather than a surveyed boundary. Built as the union of a circle at every
    vertex and a quadrilateral along every leg, in true metres, for the same
    reason `circle_polygon` walks bearings: a degree-space buffer is anisotropic
    and a lane is long enough for that to matter.
    """
    if len(path) < 2:
        raise ValueError("a corridor needs at least two points")
    parts: list[Polygon] = [circle_polygon(la, lo, half_width_m, cap_steps)
                            for la, lo in path]
    for (la1, lo1), (la2, lo2) in zip(path, path[1:]):
        b = bearing_deg(la1, lo1, la2, lo2)
        left, right = (b - 90.0) % 360.0, (b + 90.0) % 360.0
        a1 = _destination(la1, lo1, left, half_width_m)
        a2 = _destination(la2, lo2, left, half_width_m)
        b2 = _destination(la2, lo2, right, half_width_m)
        b1 = _destination(la1, lo1, right, half_width_m)
        parts.append(Polygon([(p[1], p[0]) for p in (a1, a2, b2, b1)]))
    merged = unary_union(parts)
    if isinstance(merged, MultiPolygon):
        # Legs always overlap their end caps, so this should not happen; if a
        # caller hands in a path with a gap, take the largest piece rather than
        # returning a geometry whose kind the rest of the layer does not expect.
        merged = max(merged.geoms, key=lambda g: g.area)
    return merged


def polygon_from_cells(cells: Iterable[str]):
    """The outline of a set of H3 cells, holes and all.

    `cells_to_h3shape` does the hard part — an H3 cell set is a tiling, so its
    boundary is exactly the set of edges not shared by two members, and holes
    (a lake inside a landmass, a bay excluded from a limit) fall out correctly
    rather than having to be inferred by containment tests.
    """
    cells = list(cells)
    if not cells:
        return Polygon()
    return shape(h3.cells_to_h3shape(cells).__geo_interface__)


# --------------------------------------------------------------------------
# the cell index
# --------------------------------------------------------------------------

def cells_covering(geom, res: int = INDEX_RES) -> frozenset[str]:
    """The res-6 cells that index this geometry — over-covering on purpose.

    Three things happen here and each is needed:

    * **polyfill**, which returns the cells whose centres are inside. For a
      zone larger than a cell this is most of the answer;
    * **the boundary walk**, which adds the cell containing every vertex. A
      zone smaller than a cell contains no cell centre at all, so polyfill
      returns the empty set — and a port limit indexed by the empty set is a
      port limit no vessel is ever inside;
    * **dilation by one ring**, which covers the case where a vessel sits in a
      neighbouring cell but inside the zone's edge.

    The result is a candidate filter. `contains` is what decides.
    """
    if geom.is_empty:
        return frozenset()
    cells: set[str] = set()
    try:
        cells |= set(h3.geo_to_cells(mapping(geom), res))
    except Exception:                                            # noqa: BLE001
        # h3 refuses some degenerate shapes (zero-area, self-touching). The
        # boundary walk below still indexes them, so a bad polyfill degrades
        # the index rather than losing the zone.
        pass
    for la, lo in _vertices(geom):
        cells.add(tiling.cell(la, lo, res))
    dilated = set(cells)
    for c in cells:
        dilated |= set(tiling.neighbors(c, 1))
    return frozenset(dilated)


def _vertices(geom) -> list[tuple[float, float]]:
    """Every (lat, lon) vertex, whatever kind of geometry this is."""
    out: list[tuple[float, float]] = []
    for g in getattr(geom, "geoms", [geom]):
        if isinstance(g, Point):
            out.append((g.y, g.x))
        elif isinstance(g, LineString):
            out.extend((y, x) for x, y in g.coords)
        elif isinstance(g, Polygon):
            out.extend((y, x) for x, y in g.exterior.coords)
            for ring in g.interiors:
                out.extend((y, x) for x, y in ring.coords)
    return out


# --------------------------------------------------------------------------
# the question
# --------------------------------------------------------------------------

def contains(geom, lat: float, lon: float) -> bool:
    """Is this position inside this zone? THE containment test.

    `covers` rather than `contains` so a vessel exactly on the boundary counts
    as inside. That is a real choice and it is the right one for this domain:
    the boundaries here are approximations to a few kilometres, so treating the
    edge as excluded would be false precision, and a rule about territorial
    waters should err toward *noticing* a vessel on the line.
    """
    return bool(shapely.covers(geom, Point(lon, lat)))


def distance_to_m(geom, lat: float, lon: float) -> float:
    """Great-circle distance from a position to the nearest point of a geometry.

    Shapely works in degrees, so its own `distance` is a degree-space number
    that means different things in x and y. This finds the nearest point in
    degree space — good enough to pick *which* point, because the anisotropy is
    at most 10% over this AOI — and then measures the real distance to it.

    Returns 0.0 for a position inside an area. That is what callers want: this
    exists to answer "how far outside the lane is she", and inside is zero.
    """
    p = Point(lon, lat)
    if shapely.covers(geom, p):
        return 0.0
    near = shapely.ops.nearest_points(geom, p)[0]
    return haversine_m(lat, lon, near.y, near.x)


# --------------------------------------------------------------------------
# geodesy — spherical, and that is stated rather than hidden
# --------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(min(1.0, a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing, degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _destination(lat: float, lon: float, bearing: float,
                 dist_m: float) -> tuple[float, float]:
    d = dist_m / EARTH_R_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def area_km2(geom) -> float:
    """Area in square kilometres, corrected for the latitude of the centroid.

    A degree-space area is in square degrees and means nothing; scaling by
    `cos(lat)` at the centroid is a one-line correction that is accurate to a
    few per cent for a zone that does not span many degrees of latitude, and
    honest about being an estimate. The EEZ spans twenty degrees and its figure
    is therefore approximate — it is displayed as a size cue, not used in any
    calculation.
    """
    if geom.is_empty or geom.geom_type in ("LineString", "MultiLineString",
                                           "Point"):
        return 0.0
    lat = geom.centroid.y
    deg_km = (EARTH_R_M / 1000.0) * math.pi / 180.0
    return abs(geom.area) * deg_km * deg_km * math.cos(math.radians(lat))


def centroid_latlon(geom) -> tuple[float, float]:
    c = geom.centroid
    return float(c.y), float(c.x)


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def geom_to_wkt(geom) -> str:
    """WKT with six decimal places — about 0.1 m, well past the accuracy of
    anything in this layer, and small enough that the EEZ fits in a Parquet
    string without being absurd."""
    return shapely.to_wkt(geom, rounding_precision=6)


def geom_from_wkt(wkt: str):
    return shapely.from_wkt(wkt)


def geom_to_geojson(geom) -> dict:
    return mapping(geom)
