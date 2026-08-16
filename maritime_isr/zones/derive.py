"""The zones this project is willing to assert, and the ones it is not.

**The statutory limits are deliberately absent from this module.**

The requirement names an exclusive economic zone, a contiguous zone, a
territorial sea and the international maritime boundary line. All four are
public geometry — and none of them is *here*, because none of them is on this
machine. Marine Regions (VLIZ), which publishes the authoritative EEZ and
territorial-sea polygons, is not reachable from the environment this was built
in, and the alternatives are worse than nothing:

  * **Deriving them** from a coastline and the UNCLOS distances is arithmetic
    anyone can do, and it is wrong in ways a reader cannot see. UNCLOS measures
    from *declared straight baselines*, not from the low-water line, and India
    has declared them across the Gulf of Kachchh and the Gulf of Khambhat — so
    a coastline-derived territorial sea sits inside the real one exactly where
    the traffic is densest. It would also have no median line with Pakistan,
    Oman, the Maldives or Sri Lanka.
  * **Transcribing them from memory** is worse still: a plausible-looking
    polyline for a *disputed* boundary — and the Sir Creek terminus of the
    India–Pakistan IMBL is disputed, with the maritime boundary seaward of it
    never delimited by agreement — is the exact failure this project exists to
    engineer against. A line that looks surveyed and is not is more dangerous
    than no line.
  * **Natural Earth's "maritime boundary indicator" lines** are reachable, and
    are cartographic indicators at 1:10,000,000 rather than legal limits. They
    would look official and would not be.

So they arrive through `ingest/zones.py` from a real file, or they do not
arrive. Everything downstream is built and tested against the kinds, and the
one analysis that cannot run without the territorial sea says so by name rather
than returning an empty list and looking healthy — see
`analyses.anchoring_analysis_status`.

**What this module does build** is the operational geometry: facilities,
waiting areas, terminals, customary routes and the four circles migrated out of
the anomaly library. These are working constructs at the right scale, not
claims about statutory boundaries, and every one of them says so in its own
`method` and `note` fields. A port limit here is a circle on a gazetteer
position; the declared limit is a published administrative boundary and is not
a circle, and the row says exactly that.
"""
from __future__ import annotations

import hashlib

from ..ports import ANCHORAGES, PORTS
from .geometry import (cells_covering, circle_polygon, corridor_polygon,
                       geom_to_wkt, haversine_m)
from .model import Zone

__all__ = ["build_operational_zones", "SHIPPING_LANES", "PORT_LIMIT_KM",
           "ANCHORAGE_LIMIT_KM", "OIL_TERMINALS", "SENSITIVE_AREAS",
           "STATUTORY_KINDS"]

#: The kinds this module will not build. Named as a constant because three
#: places check it — the builder, the connector's validation, and the status
#: report that tells an operator why an analysis is idle.
STATUTORY_KINDS: frozenset[str] = frozenset(
    {"eez", "contiguous_zone", "territorial_sea", "imbl"})

#: Working radii for port areas, kilometres — scaled to the facility, not
#: declared. Kandla and Karachi are large multi-berth complexes; Sikka and
#: Vadinar are crude terminals with a tight working area.
PORT_LIMIT_KM: dict[str, float] = {
    "Mumbai": 12.0, "JNPT": 12.0, "Kandla": 15.0, "Mundra": 12.0,
    "Sikka": 10.0, "Vadinar": 10.0, "Kochi": 12.0, "Mangalore": 10.0,
    "New Mangalore": 10.0, "Mormugao": 10.0, "Karachi": 15.0, "Gwadar": 10.0,
    "Hazira": 10.0, "Dahej": 10.0, "Pipavav": 10.0,
}
_DEFAULT_PORT_LIMIT_KM = 8.0

#: However close its neighbours, a port area is at least this wide. Below about
#: 2 km the circle is smaller than the berth structure it stands for and stops
#: being useful for anything.
_MIN_PORT_LIMIT_KM = 2.0

#: Anchorages are drawn wider than port areas: a designated anchorage is a
#: holding area and vessels spread across it, which is the whole reason the
#: layer exists separately (STATE.md, 2026-08-01 — 29 of 33 loitering alerts
#: were merchants queueing at Kandla, outside the port radius).
ANCHORAGE_LIMIT_KM = 12.0

#: Offshore terminals and single point moorings. The SPM positions are the ones
#: the radar simulator already uses as fixed targets, taken from there rather
#: than retyped, so the two layers cannot disagree about where a mooring is —
#: which is the ADR-023 gazetteer failure, one layer along.
OIL_TERMINALS: dict[str, tuple[float, float, float]] = {
    # name: (lat, lon, radius_km)
    "Vadinar SPM 1":  (22.4200, 69.6400, 2.0),
    "Vadinar SPM 2":  (22.4050, 69.6100, 2.0),
    "Sikka SPM":      (22.4450, 69.7300, 2.0),
    "Bombay High":    (19.5000, 71.5000, 35.0),
    "Hazira LNG":     (21.1000, 72.6000, 4.0),
}

#: Established routes, as centreline plus half-width.
#:
#: **Customary tracks, not IMO-adopted routeing measures.** No traffic
#: separation scheme is adopted along most of this coast; what exists is where
#: ships actually go — for a coastal passage a corridor roughly parallel to the
#: coast outside the fishing grounds, for a deep-sea passage a great-circle
#: track between the Gulf of Oman and the southern capes. Every row says so, so
#: nobody quotes a deviation from these as a regulatory finding.
SHIPPING_LANES: dict[str, tuple[list[tuple[float, float]], float]] = {
    # name: ([(lat, lon), ...], half_width_km)
    "West coast coastal route": ([
        (23.00, 67.80), (22.30, 68.30), (21.40, 69.00), (20.60, 70.20),
        (19.80, 71.60), (18.60, 72.20), (17.20, 72.60), (15.60, 73.20),
        (13.80, 74.00), (11.80, 74.90), (9.60, 75.80),
    ], 25.0),
    "Gulf of Kachchh approach": ([
        (22.00, 68.60), (22.35, 69.10), (22.48, 69.62), (22.60, 70.05),
    ], 12.0),
    "Hormuz-Malacca deep-sea track": ([
        (24.60, 60.60), (22.00, 62.20), (19.00, 64.50), (16.00, 67.50),
        (13.00, 71.00), (10.00, 74.00), (7.00, 77.00),
    ], 40.0),
    "Mumbai approach": ([
        (18.20, 71.40), (18.60, 72.10), (18.88, 72.72),
    ], 10.0),
}

#: The four circles this layer replaces. Kept by name and coordinate so
#: `loitering_sensitive` behaves identically after the migration — this is a
#: change of *where the geometry lives*, not of what it is, and a behaviour
#: change smuggled in alongside a refactor is how a regression gets attributed
#: to the wrong commit.
SENSITIVE_AREAS: dict[str, tuple[float, float, float]] = {
    "Mumbai High oil field":     (19.30, 71.30, 40.0),
    "SW approaches cable":       (15.50, 68.00, 35.0),
    "Naval exercise area W":     (17.00, 69.50, 50.0),
    "Kandla pipeline corridor":  (22.90, 69.90, 25.0),
}


def _radius_for(name: str, lat: float, lon: float) -> float:
    """A port's working radius, capped so neighbouring facilities cannot overlap.

    **This cap is a repair, not a refinement.** The first version used the size
    of the facility alone, and on the Gulf of Kachchh cluster that produced
    circles that swallowed each other: Sikka and Vadinar are 11 km apart and
    both were given 10 km, so a vessel alongside at Vadinar was simultaneously
    "inside" Sikka, Vadinar and Mundra and all three anchorages. Measured
    consequence — the maiden-visit rule's "three distinct zones already visited"
    qualifier was satisfied within an hour of a vessel arriving anywhere in the
    Gulf, and the rule fired 643 times. Overlapping zones are not zones; they
    are one zone with several names, and every question asked of them is
    answered wrongly.

    Half the distance to the nearest other port is the natural boundary — the
    same construction a Voronoi diagram uses, applied one facility at a time.
    A generous 500 m margin comes off it so two adjacent circles do not touch,
    because `contains` is inclusive on the boundary and a vessel exactly between
    two ports should belong to neither rather than to both.
    """
    want = PORT_LIMIT_KM.get(name, _DEFAULT_PORT_LIMIT_KM)
    nearest = min(
        (haversine_m(lat, lon, la, lo) / 1000.0
         for other, (la, lo) in PORTS.items() if other != name),
        default=None)
    if nearest is None:
        return want
    return max(_MIN_PORT_LIMIT_KM, min(want, nearest / 2.0 - 0.5))


def _zid(kind: str, name: str) -> str:
    """A stable id from kind and name.

    Content-free by design: it must not encode a coordinate, because a zone
    whose geometry is corrected should keep its identity — otherwise every
    stored transition pointing at it becomes an orphan the day a position is
    improved by a hundred metres.
    """
    return f"zone:{kind}:" + hashlib.sha1(name.encode()).hexdigest()[:10]


def _zone(kind: str, name: str, geom, *, authority: str, method: str,
          confidence: float, note: str = "",
          facility: str | None = None) -> Zone:
    return Zone(zone_id=_zid(kind, name), kind=kind, name=name,
                wkt=geom_to_wkt(geom), authority=authority, method=method,
                confidence=confidence, cells=cells_covering(geom),
                facility=facility, note=note)


def build_operational_zones() -> list[Zone]:
    """Every zone this project is willing to assert from what it has.

    Deterministic: same code, same output, no network, no clock. That is what
    lets `zone_set_version` mean anything — a stored transition can be trusted
    to refer to the boundary it was computed against.

    Contains **no** statutory limit. See the module docstring.
    """
    zones: list[Zone] = []

    for name, (lat, lon) in sorted(PORTS.items()):
        r_km = _radius_for(name, lat, lon)
        zones.append(_zone(
            "port_limit", f"{name} port area",
            circle_polygon(lat, lon, r_km * 1000.0),
            authority="derived:maritime-isr", facility=name,
            method=(f"{r_km:.1f} km circle on the gazetteer position"
                    + (", capped at half the distance to the nearest "
                       "neighbouring port"
                       if r_km < PORT_LIMIT_KM.get(name, _DEFAULT_PORT_LIMIT_KM)
                       else "")),
            confidence=0.4,
            note=("WORKING AREA, NOT A DECLARED LIMIT. A declared port limit is "
                  "a published administrative boundary and is not a circle. "
                  "This stands in for one at the right scale; replace it via "
                  "`maritime-isr ingest zones` when the declarations are "
                  "available.")))

    for name, (lat, lon) in sorted(ANCHORAGES.items()):
        # Anchorages are capped against each OTHER but not against ports: an
        # anchorage genuinely does lie off its own port and overlapping the
        # facility it serves is correct. Two anchorages overlapping is not.
        a_km = ANCHORAGE_LIMIT_KM
        near = min((haversine_m(lat, lon, la, lo) / 1000.0
                    for o, (la, lo) in ANCHORAGES.items() if o != name),
                   default=None)
        if near is not None:
            a_km = max(_MIN_PORT_LIMIT_KM, min(a_km, near / 2.0 - 0.5))
        zones.append(_zone(
            "anchorage", f"{name} anchorage",
            circle_polygon(lat, lon, a_km * 1000.0),
            authority="charted waiting area", facility=name,
            method=(f"{a_km:.1f} km circle on the charted waiting-area "
                    f"position"),
            confidence=0.5,
            note=("Charted waiting areas, not positions read back from our own "
                  "corpus — deriving them from generated traffic would be "
                  "fitting the detector to the test set.")))

    for name, (lat, lon, r_km) in sorted(OIL_TERMINALS.items()):
        zones.append(_zone(
            "oil_terminal", name, circle_polygon(lat, lon, r_km * 1000.0),
            authority="published installation position",
            method=f"{r_km:.0f} km circle on the installation position",
            confidence=0.6))

    for name, (path, half_km) in sorted(SHIPPING_LANES.items()):
        zones.append(_zone(
            "shipping_lane", name, corridor_polygon(path, half_km * 1000.0),
            authority="derived:maritime-isr",
            method=f"{half_km:.0f} km corridor about a customary centreline",
            confidence=0.35,
            note=("CUSTOMARY ROUTE, NOT AN IMO ROUTEING MEASURE. No traffic "
                  "separation scheme is adopted along most of this coast. "
                  "Deviation from this corridor is a behavioural observation, "
                  "never a regulatory finding.")))

    for name, (lat, lon, r_km) in sorted(SENSITIVE_AREAS.items()):
        zones.append(_zone(
            "sensitive_area", name, circle_polygon(lat, lon, r_km * 1000.0),
            authority="project geofence",
            method=(f"{r_km:.0f} km circle — migrated from the four hardcoded "
                    f"circles in the anomaly library"),
            confidence=0.5))

    return zones
