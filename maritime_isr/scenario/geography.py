"""Geodesy and the real places the scenarios happen in.

Every coordinate in this module is a real one. Scenarios that need a port, a
transfer basin, a cable approach or an entry corridor take it from here rather
than inventing a position, because a synthetic corpus whose geography is wrong
teaches the system to associate anomalies with places that do not exist.

**AOI v1 is a hard boundary, not a preference.** `assert_in_aoi` is called by
the validator over every generated position; a scenario that wanders outside
5-25N / 60-78E fails the build. The one deliberate exception is the Makran-coast
deliberate miss, which sits at ~24N 63E — inside the box, deliberately near the
northern edge.

The great-circle helpers are spherical (R = 6,371,008.8 m, the WGS-84 mean
radius). Over the distances a scenario covers — a few hundred nautical miles at
most — the spherical/ellipsoidal difference is well under a kilometre, which is
far below the position noise the AIS emitter injects anyway. Using an ellipsoid
here would be false precision.
"""
from __future__ import annotations

from ..ports import SCENARIO_PORTS

import math

from ..config import AOI_V1

#: WGS-84 mean radius, metres.
EARTH_R_M = 6_371_008.8

M_PER_NM = 1852.0


# --------------------------------------------------------------------------
# real geography
# --------------------------------------------------------------------------

#: Ports and terminals, (lat, lon). Real coordinates for real facilities.
#: The ports the scenario places vessels at. **Sourced from the one shared
#: gazetteer** (ADR-023) rather than defined here, so the generator and the
#: feature extractor cannot disagree about where a port is — they did, and a
#: vessel could call somewhere the extractor had no name for.
#:
#: The coordinates are unchanged by the consolidation: the scenario's values
#: were the authoritative ones, including Gwadar's approach anchorage, so
#: generated tracks are byte-identical and the determinism test still holds.
PORTS: dict[str, tuple[float, float]] = dict(SCENARIO_PORTS)

#: Anchorage waiting areas, offset from the berth. A vessel waiting for a berth
#: sits here, not alongside — the distinction is what makes E5 (floating storage)
#: and the Mundra berth-congestion decoy geometrically honest.
ANCHORAGES: dict[str, tuple[float, float]] = {
    "Sikka":     (22.30, 69.65),
    "Vadinar":   (22.18, 69.55),
    "Mundra":    (22.60, 69.50),
    "Kandla":    (22.80, 70.05),
    "Karachi":   (24.65, 66.80),
    "Gwadar":    (24.78, 62.30),
    "JNPT":      (18.80, 72.75),
    "Mangalore": (12.80, 74.65),
    "Kochi":     (9.83, 76.10),
}

#: Deep-basin ship-to-ship transfer zones. The 16-19N / 62-66E block is open
#: water well outside coastal AIS reception and outside the main lane traffic —
#: which is exactly why transfers happen there.
TRANSFER_ZONES: dict[str, tuple[float, float, float, float]] = {
    # name: (lat_min, lat_max, lon_min, lon_max)
    "deep_basin_north": (17.5, 19.0, 62.0, 64.0),
    "deep_basin_mid":   (16.5, 18.0, 63.5, 65.5),
    "deep_basin_south": (16.0, 17.5, 64.5, 66.0),
}

#: Bombay High offshore oil field. Real installation cluster; E2 models repeated
#: slow passes near it.
BOMBAY_HIGH = (19.50, 71.50)

#: SEA-ME-WE submarine cable approach to Mumbai, as a coarse polyline. These are
#: approach bearings toward the Versova landing area rather than surveyed cable
#: positions — cable routes are published only as approximate corridors, and
#: pretending to metre accuracy would be the overclaim this project exists to
#: avoid. E1 loiters ALONG this line.
CABLE_APPROACH_MUMBAI: list[tuple[float, float]] = [
    (18.60, 70.40),
    (18.75, 71.10),
    (18.90, 71.85),
    (19.05, 72.45),
]

#: Declared naval exercise area (E3 intrudes into it). Coarse box in the
#: Arabian Sea west of Goa.
NAVAL_EXERCISE_AREA = (15.20, 16.80, 69.00, 71.00)  # lat_min, lat_max, lon_min, lon_max

#: The northwest entry corridor from the Gulf of Oman. Loaded tankers enter AOI
#: v1 through roughly here.
NW_ENTRY = (24.60, 60.60)

#: Productive fishing ground off Gujarat where the fleet-aggregation decoy
#: converges.
FISHING_GROUND_GUJARAT = (20.80, 68.60)

#: Where terrestrial AIS reception is actually plausible. Used by the gap
#: primitive to decide whether a silence is even *observable* as intentional,
#: and by the offshore-gap deliberate miss to sit demonstrably outside it.
#: (lat, lon, radius_km) — receiver sites, not a measured coverage model.
RECEIVER_SITES: list[tuple[float, float, float]] = [
    (18.95, 72.84, 300.0),   # Mumbai
    (21.63, 69.60, 300.0),   # Porbandar
    (22.43, 69.84, 300.0),   # Sikka
    (9.97, 76.24, 300.0),    # Kochi
    (12.92, 74.80, 300.0),   # Mangalore
]


# --------------------------------------------------------------------------
# geodesy
# --------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(min(1.0, a)))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Bearing at the start of the great circle from 1 to 2, degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def destination(lat: float, lon: float, bearing_deg: float,
                distance_m: float) -> tuple[float, float]:
    """Where you end up steering `bearing_deg` for `distance_m` on a great circle."""
    d = distance_m / EARTH_R_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def interpolate(lat1: float, lon1: float, lat2: float, lon2: float,
                f: float) -> tuple[float, float]:
    """Point a fraction `f` along the great circle from 1 to 2 (spherical slerp).

    Falls back to the endpoint for degenerate (coincident) pairs rather than
    dividing by a zero sine — two waypoints at the same position is a legitimate
    thing for a station-keeping leg to contain.
    """
    if f <= 0.0:
        return lat1, lon1
    if f >= 1.0:
        return lat2, lon2
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    d = 2 * math.asin(math.sqrt(
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d < 1e-12:
        return lat2, lon2
    a = math.sin((1 - f) * d) / math.sin(d)
    b = math.sin(f * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return (math.degrees(math.atan2(z, math.hypot(x, y))),
            math.degrees(math.atan2(y, x)))


def path_length_m(waypoints: list[tuple[float, float]]) -> float:
    return sum(haversine_m(*waypoints[i], *waypoints[i + 1])
               for i in range(len(waypoints) - 1))


def angular_diff_deg(a: float, b: float) -> float:
    """Smallest signed difference b - a, in (-180, 180]."""
    return (b - a + 540.0) % 360.0 - 180.0


# --------------------------------------------------------------------------
# AOI and coverage
# --------------------------------------------------------------------------

def in_aoi(lat: float, lon: float) -> bool:
    return AOI_V1.contains(lat, lon)


def assert_in_aoi(lat: float, lon: float, context: str = "") -> None:
    if not in_aoi(lat, lon):
        raise ValueError(
            f"position ({lat:.4f}, {lon:.4f}) is outside AOI v1 "
            f"({AOI_V1.lat_min}-{AOI_V1.lat_max}N, {AOI_V1.lon_min}-{AOI_V1.lon_max}E)"
            + (f" [{context}]" if context else ""))


def receiver_coverage(lat: float, lon: float) -> float:
    """Crude P(a terrestrial receiver would hear a transmission here), 0..1.

    Linear falloff from 1.0 at a site to 0.0 at its radius, taking the best
    site. **This is a scenario-generation convenience, not the Phase 2.2
    coverage model** — it exists so the gap primitive can place a
    "demonstrably out of coverage" silence honestly, and so the receiver-shadow
    decoy sits somewhere a real reception model would also call marginal. The
    system's own coverage model is learned from observed reception and is not
    informed by this function.
    """
    best = 0.0
    for slat, slon, radius_km in RECEIVER_SITES:
        d_km = haversine_m(lat, lon, slat, slon) / 1000.0
        if d_km < radius_km:
            best = max(best, 1.0 - d_km / radius_km)
    return best


def transfer_point(zone: str, rng) -> tuple[float, float]:
    """A position inside a named deep-basin transfer zone."""
    lat_min, lat_max, lon_min, lon_max = TRANSFER_ZONES[zone]
    return (rng.uniform(lat_min, lat_max), rng.uniform(lon_min, lon_max))


def point_on_cable(f: float) -> tuple[float, float]:
    """A point a fraction `f` along the Mumbai cable approach polyline."""
    pts = CABLE_APPROACH_MUMBAI
    total = path_length_m(pts)
    target = max(0.0, min(1.0, f)) * total
    walked = 0.0
    for i in range(len(pts) - 1):
        seg = haversine_m(*pts[i], *pts[i + 1])
        if walked + seg >= target or i == len(pts) - 2:
            return interpolate(*pts[i], *pts[i + 1],
                               (target - walked) / seg if seg else 0.0)
        walked += seg
    return pts[-1]
