"""Coastal routing — passages that go around the land instead of through it.

Every transit in the generator used to be a great-circle line between two
points. Over open ocean that is correct; along a coastline it is nonsense, and
it showed: **51.3% of landed AIS positions fell on land**, with tracks drawn
straight across the Saurashtra peninsula and vessels parked in the middle of
Gujarat. A map is the product's entire output, so this was not a cosmetic
problem — it was the picture being wrong.

The fix is the one real coastal traffic uses: a **corridor of offshore
waypoints** running the length of the coast, and a passage that joins the
corridor near its origin, follows it, and leaves it near its destination.

**Why a fixed corridor rather than a search.** A general sea-route search
(A* over a land raster) is the thorough answer and is far more machinery than
this needs: the AOI has one coastline, traffic runs along it, and thirteen
waypoints describe it. Every waypoint and every leg between consecutive
waypoints is verified clear of land by `test_searoute_is_clear_of_land`, so the
corridor cannot silently rot, and the honest limitation is recorded here: the
corridor models **this** coastline and nothing else, so a passage whose blocking
land is not the Makran-to-Malabar coast has no waypoints to route through and
will still run direct. Inside the AOI there is no such coast; outside it, this
module is not the right tool.

The corridor is ordered north to south. Ordering matters: a passage takes the
contiguous slice of waypoints between its endpoints, in the direction of travel,
so the shape of the route follows the shape of the coast.
"""
from __future__ import annotations

import math

#: Offshore waypoints, north to south, each in open water with a clear leg to
#: its neighbours. Placed far enough out that a vessel following them reads as
#: coastal traffic rather than as something threading a harbour.
CORRIDOR: tuple[tuple[str, float, float], ...] = (
    ("gwadar_off",    24.70, 62.40),
    ("makran_off",    24.30, 64.50),
    ("karachi_off",   24.20, 66.60),
    ("indus_off",     23.10, 67.60),
    ("kutch_link",    22.60, 69.40),   # serves all four Gulf of Kutch terminals
    ("kutch_mouth",   22.55, 69.30),
    ("okha",          22.40, 68.60),
    ("dwarka",        22.10, 68.60),
    ("porbandar",     21.30, 68.90),
    ("diu",           20.30, 70.30),
    ("khambhat_off",  20.20, 71.60),
    ("mumbai_off",    18.90, 72.40),
    ("ratnagiri",     16.90, 72.80),
    ("goa_off",       15.30, 73.40),
    ("karwar",        14.20, 73.90),
    ("mangalore_off", 12.85, 74.55),
    ("kochi_off",      9.85, 75.95),
)

#: Fairways. A few berths have no clear line to the corridor — Mumbai and JNPT
#: sit behind Salsette, Karachi behind its harbour approaches — so a vessel has
#: to leave by a specific channel before it can join coastal traffic. This is
#: what a chart calls a pilot boarding ground, and each one is verified to have
#: a clear line both to its berth and to the corridor.
APPROACHES: dict[tuple[float, float], tuple[float, float]] = {
    (18.950, 72.950): (18.878, 72.877),   # JNPT
    (18.941, 72.890): (18.883, 72.907),   # Mumbai
    (24.766, 66.996): (24.700, 66.900),   # Karachi
}

#: A position within this of a keyed berth uses that berth's fairway.
APPROACH_SNAP_KM = 12.0


def _hav_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _nearest_index(p: tuple[float, float]) -> tuple[int, float]:
    best_i, best_d = 0, float("inf")
    for i, (_, la, lo) in enumerate(CORRIDOR):
        d = _hav_km(p, (la, lo))
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


#: Spacing between land-mask samples along a leg. The mask resolves about 1 km,
#: so sampling must be **below half** that or a single cell can fall between two
#: samples: at 0.5 km a 600 km Mumbai-to-Okha leg clipped the Gujarat coast and
#: was reported clear, and only a test that sampled finer than the code caught
#: it. 0.25 km is Nyquist with room to spare, and the cost is a vectorised
#: numpy lookup nobody can measure.
SAMPLE_KM = 0.25


def crosses_land(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Does the straight line from `a` to `b` touch land?

    The decision the whole module turns on. Sampling is **proportional to the
    leg's length**, not a fixed count: a fixed 160 samples is dense on a 20 km
    hop and 6 km apart on a 1,000 km ocean crossing, which is how a
    Gwadar-to-JNPT leg stepped straight over the Saurashtra coast and was
    reported clear.

    Without the land mask available the answer is "assume it does", so routing
    still happens — degrading to direct lines is what produced the original
    defect, and a missing optional dependency is not a reason to reintroduce it.
    """
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return True
    n = max(64, min(20000, int(_hav_km(a, b) / SAMPLE_KM) + 2))
    lat = np.linspace(a[0], b[0], n)
    lon = np.linspace(a[1], b[1], n)
    return bool(globe.is_land(lat, lon).any())


#: How far `nearest_water` will look before giving up and returning the point
#: unchanged. Larger than any inland excursion a bearing-and-distance
#: calculation can plausibly produce inside the AOI.
WATER_SEARCH_MAX_KM = 60.0
WATER_SEARCH_STEP_KM = 1.0


def nearest_water(p: tuple[float, float],
                  reachable_from: tuple[float, float] | None = None
                  ) -> tuple[float, float]:
    """The closest sea position to `p`, or `p` itself if it is already at sea.

    Routing can only ever solve half the problem. A passage between two sea
    positions can be made to go around a peninsula, but a passage *to a point in
    the middle of Kutch* has no solution — and the generator was producing those
    without noticing: a rendezvous approach start is computed as
    "18 nm from the meet point on bearing 200", a departure target as "55 nm out
    on the reciprocal", and neither calculation knows what is under the answer.
    Three scenarios (DX1, C4, B5) plus a fifth of the commercial fleet began
    their voyages inland and steamed to the coast, which is where the residual
    on-land positions were coming from after routing was in place.

    So positions are snapped here, at the one point every leg passes through,
    rather than at each of the ~40 scenario call sites — the same reasoning as
    the shared H3 helper: one implementation, applied everywhere, cannot drift.

    The search is a widening ring rather than a gradient walk: gradient descent
    on a binary mask has nothing to descend, and a ring search is exact to its
    step size. Deterministic, so the corpus stays reproducible from seed + SHA.

    `reachable_from` is where the vessel will be coming from, and supplying it
    matters more than it looks. Nearest-by-distance is not nearest-by-sea: DX1's
    departure target landed in Kachchh, and the closest water was 55 km away at
    the *head* of the Gulf of Kutch — a pocket the vessel can only enter by
    rounding a headland the corridor does not model, so the snap turned a bad
    destination into a bad passage. Given an origin, a candidate is preferred
    when the line from that origin to it is clear, which keeps the correction on
    the same body of water the vessel is already in.
    """
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return p
    lat, lon = p
    if not bool(globe.is_land(lat, lon)):
        return p
    coslat = max(math.cos(math.radians(lat)), 0.1)
    th = np.radians(np.arange(0, 360, 6.0))
    r = WATER_SEARCH_STEP_KM
    fallback: tuple[float, float] | None = None
    while r <= WATER_SEARCH_MAX_KM:
        cand_lat = lat + (r / 111.0) * np.cos(th)
        cand_lon = lon + (r / (111.0 * coslat)) * np.sin(th)
        free = ~globe.is_land(cand_lat, cand_lon)
        if free.any():
            ring = [(float(cand_lat[i]), float(cand_lon[i]))
                    for i in range(len(th)) if free[i]]
            if fallback is None:
                fallback = ring[0]
            if reachable_from is None:
                return ring[0]
            for q in ring:
                if not crosses_land(reachable_from, q):
                    return q
        r += WATER_SEARCH_STEP_KM
    if fallback is not None:
        # Water exists nearby but none of it is on the origin's side of the
        # coast. Returning the nearest anyway is better than returning a point
        # on land, and the routing step still gets its chance.
        return fallback
    # Nothing within 60 km. Returning `p` unchanged is the honest outcome: the
    # caller asked for somewhere deeply inland and moving it an arbitrary
    # distance would invent a position rather than correct one. The `afloat`
    # validator will fail on it, which is the intended way to find out.
    return p


#: Granularity of the walk in `seaward_point`.
SEAWARD_STEP_KM = 2.0


def seaward_point(origin: tuple[float, float], bearing_deg: float,
                  max_m: float) -> tuple[float, float]:
    """As far along `bearing_deg` from `origin` as a vessel can actually steam.

    For "leave on roughly this heading and keep going", which is how a departure
    is specified when the scenario cares about the heading rather than the
    destination. Dead-reckoning the full distance and hoping is what put DX1's
    bunker barge 150 km into Kachchh; walking out and stopping at the coast
    gives the same heading, a shorter leg, and a target that is by construction
    at sea with a clear line from the origin.

    Returns `origin` if even the first step is blocked, which the caller should
    read as "there is no sea on that heading" — the leg is then a no-op rather
    than a crossing.
    """
    try:
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return origin
    from .geography import destination

    best = origin
    d = SEAWARD_STEP_KM * 1000.0
    while d <= max_m:
        c = destination(*origin, bearing_deg, d)
        if bool(globe.is_land(c[0], c[1])) or crosses_land(origin, c):
            break
        best = c
        d += SEAWARD_STEP_KM * 1000.0
    return best


def sea_route(a: tuple[float, float], b: tuple[float, float]
              ) -> list[tuple[float, float]]:
    """Waypoints to pass through going from `a` to `b`. May be empty.

    Empty means "go direct", and that is now decided by **asking the land mask**
    rather than by geometry. The first version routed only when the endpoints
    sat nearest different corridor waypoints, which silently sent every hop
    inside the Gulf of Kutch — Mundra to Kandla, Sikka to Vadinar — straight
    over the Kachchh shore, because both ends are nearest the same waypoint.

    The endpoints themselves are never included; the caller already has them.

    The corridor gets the passage to the right stretch of coast; `_repair` then
    checks **every leg of the assembled path** and fixes any that still crosses.
    That second step is not belt-and-braces, it is where the enclosed water gets
    handled: the corridor describes the open coast, so a berth deep inside the
    Gulf of Kutch joins it on a line that runs over the Kachchh shore, and the
    corridor has no vocabulary for that. Repairing legs locally is what lets one
    seventeen-point corridor cover gulfs and bays it does not enumerate.
    """
    if not crosses_land(a, b):
        return []

    lead = _approach_for(a)
    tail = _approach_for(b)

    # Deliberately no "too far from the corridor, go direct" escape here. There
    # was one, keyed on JOIN_RADIUS_KM, and it was unsound: it ran *after* the
    # land check above had already established that the direct line is blocked,
    # so its effect was to send exactly the long approaches — a hull inbound
    # from the mid-Arabian Sea to the Gulf of Kutch, 900 km from any waypoint —
    # straight over the Saurashtra peninsula. Whether joining the corridor is a
    # detour is not the question once the direct line is known to cross land;
    # the only question is which waypoints get the vessel there by sea.
    ia, _ = _nearest_index(a)
    ib, _ = _nearest_index(b)
    if ia == ib:
        # Same stretch of coast, but the direct line is blocked — a headland or
        # a gulf shore between the two. Go out to the shared waypoint and back
        # in, which is what a vessel actually does leaving one berth for another
        # in the same gulf.
        mid = [(CORRIDOR[ia][1], CORRIDOR[ia][2])]
        return _repair(a, _dedupe([*lead, *mid, *reversed(tail)]), b)
    step = 1 if ib > ia else -1
    idxs = list(range(ia, ib + step, step))
    # Drop a waypoint that sits behind the origin or beyond the destination, so
    # a passage starting mid-corridor does not sail backwards to join it.
    pts = [(CORRIDOR[i][1], CORRIDOR[i][2]) for i in idxs]
    if pts and _hav_km(a, pts[0]) < 12.0:
        pts = pts[1:]
    if pts and _hav_km(b, pts[-1]) < 12.0:
        pts = pts[:-1]
    return _repair(a, _dedupe([*lead, *pts, *reversed(tail)]), b)


#: How far off a blocked leg's midpoint `_repair` will look for a way round, and
#: how finely. 140 km covers the width of the Gulf of Kutch and the Saurashtra
#: headlands; 8 km steps are coarse enough to stay cheap and fine enough that a
#: usable channel is not stepped over.
DETOUR_MAX_KM = 140.0
DETOUR_STEP_KM = 8.0
#: Times a blocked leg may be split before the search gives up. Three levels
#: turn one leg into at most eight, which is the most detail a coastal passage
#: in this AOI has ever needed.
REPAIR_MAX_DEPTH = 3


def _repair(a: tuple[float, float], mid: list[tuple[float, float]],
            b: tuple[float, float]) -> list[tuple[float, float]]:
    """Make every leg of `a -> mid... -> b` clear of land, inserting as needed.

    The corridor is a description of the open coast and cannot describe every
    inlet. Rather than enumerate them — a gazetteer that rots the moment a
    scenario picks a berth nobody listed — a blocked leg is repaired in place by
    looking for a single point that clears both halves, and failing that by
    splitting and recursing. This is a bounded local search, not a planner: it
    finds a way round a headland, and it is honest about not being A* over a
    raster, which is what a general answer would need.
    """
    out: list[tuple[float, float]] = []
    path = [a, *mid, b]
    for p, q in zip(path, path[1:]):
        out.extend(_detour(p, q, REPAIR_MAX_DEPTH))
        if q is not b:
            out.append(q)
    return _dedupe(out)


def _detour(a: tuple[float, float], b: tuple[float, float],
            depth: int) -> list[tuple[float, float]]:
    """Points to insert strictly between `a` and `b` so neither half crosses."""
    if not crosses_land(a, b):
        return []
    if depth <= 0:
        # Out of budget. Returning nothing leaves the leg crossing, which the
        # `afloat` validator will report against the vessel — the failure is
        # visible rather than papered over with an invented waypoint.
        return []
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return []

    mlat, mlon = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    coslat = max(math.cos(math.radians(mlat)), 0.1)
    # Search perpendicular to the leg first — a detour round a headland is a
    # sideways displacement, and trying the two sides in order of distance keeps
    # the result the shorter way round rather than whichever the grid hit first.
    brg = math.atan2(b[1] - a[1], b[0] - a[0])
    perp = brg + math.pi / 2.0
    r = DETOUR_STEP_KM
    while r <= DETOUR_MAX_KM:
        for sign in (1.0, -1.0):
            dlat = sign * (r / 111.0) * math.cos(perp)
            dlon = sign * (r / (111.0 * coslat)) * math.sin(perp)
            c = (mlat + dlat, mlon + dlon)
            if bool(globe.is_land(c[0], c[1])):
                continue
            if not crosses_land(a, c) and not crosses_land(c, b):
                return [c]
        r += DETOUR_STEP_KM

    # No single point clears both halves. Split at the best sea point we can
    # find near the midpoint and solve the two halves independently.
    c = nearest_water((mlat, mlon), reachable_from=a)
    if bool(globe.is_land(c[0], c[1])) or c in (a, b):
        return []
    return [*_detour(a, c, depth - 1), c, *_detour(c, b, depth - 1)]


def _approach_for(p: tuple[float, float]) -> list[tuple[float, float]]:
    for berth, fairway in APPROACHES.items():
        if _hav_km(p, berth) < APPROACH_SNAP_KM:
            return [fairway]
    return []


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for q in pts:
        if not out or _hav_km(out[-1], q) > 1.0:
            out.append(q)
    return out
