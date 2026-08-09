"""One port gazetteer, shared. ADR-023.

**Three lists existed, with different contents, and the disagreement was
load-bearing.** Measured 2026-08-01:

  * `tracks.features.AOI_PORTS` — 8 ports, used to derive `port_calls` from a
    track by proximity. **No Sikka, no Vadinar** — the two Gujarat crude
    terminals that most scenario tanker traffic actually calls at. A vessel
    could run a full laden voyage into Vadinar and produce an empty
    `port_calls` list.
  * `anomaly.library.HIGH_RISK_PORTS` — 2 entries, a risk weight per port.
  * `scenario.geography.PORTS` — 11 ports, used to *place* the scenario fleet.

So the generator put ships at ports the feature extractor could not name, and
the risk rule matched against a third list again. Nothing errored; the port
call simply did not exist as far as detection was concerned.

**The scenario coordinates are authoritative where the two overlap.** They were
chosen deliberately — Gwadar's entry is the seaward approach anchorage rather
than the berth, because the berth sits north of AOI v1's 25N edge and using it
put track points outside the box. Taking the other list's coordinate would have
silently undone that reasoning, and would also have moved every generated track,
which the determinism test would then have failed for the wrong reason.

**The five ports from the real corpus are GFW's own coordinates**, read verbatim
from the anchorage records in the landed raw payloads (`topDestination` plus
`lat`/`lon`). They are not estimates. Alang is the world's largest shipbreaking
yard and appears in the corpus as `ind-ind-76`, which is why a readable name
matters (ADR-020).

This module is data, not policy. `HIGH_RISK_PORTS` stays where it is because a
risk weight is a judgement about a place, not a fact about it.
"""
from __future__ import annotations

__all__ = ["PORTS", "ANCHORAGES", "PORT_RADIUS_KM", "port_at",
           "at_waiting_area", "SCENARIO_PORTS", "SCENARIO_ANCHORAGES"]

#: How close a position must be to count as being at a port, kilometres.
#: Shared so the feature extractor and anything else asking "which port is
#: this" cannot answer differently.
PORT_RADIUS_KM = 15.0

#: name -> (lat, lon). One list.
#:
#: **The scenario ports are placed in water.** They were terminal or city
#: centroids, which put six of ten on land and drew vessels sitting in the
#: middle of Gujarat. A berth genuinely is on the coastline and a 1 km land mask
#: calls that land, so for a system whose whole output is a map the reference
#: point has to be water a ship can float in. `test_geography_is_at_sea` checks
#: every one against `global_land_mask`, so this cannot regress.
PORTS: dict[str, tuple[float, float]] = {
    # --- Gujarat crude/product cluster ------------------------------------
    # Where most Arabian Sea tanker traffic in this AOI actually goes, and
    # where the first two were missing from the feature extractor entirely.
    "Sikka":     (22.510, 69.840),
    "Vadinar":   (22.500, 69.730),
    "Mundra":    (22.709, 69.728),
    "Kandla":    (22.911, 70.235),
    "Porbandar": (21.63, 69.60),

    # --- observed in the real corpus, GFW's own anchorage coordinates ------
    "Pipavav":   (20.9218, 71.5150),
    "Alang":     (21.7847, 72.1412),   # the shipbreaking yard; GFW: ind-ind-76
    "Hazira":    (21.1448, 72.6613),
    "Magdalla":  (21.1327, 72.7060),
    "Ghogha":    (21.6977, 72.2813),

    # --- Pakistan ---------------------------------------------------------
    "Karachi":   (24.766, 66.996),
    # The seaward approach anchorage, NOT the berth: the berth is at ~25.12N,
    # north of AOI v1's 25N edge, and a position outside the AOI is outside
    # everything this system is scoped to.
    "Gwadar":    (24.880, 62.320),

    # --- Indian west coast ------------------------------------------------
    "JNPT":      (18.950, 72.950),
    "Mumbai":    (18.941, 72.890),
    "Mangalore": (12.894, 74.769),
    "Kochi":     (9.909, 76.208),
}


#: Designated anchorages — the waiting areas offshore of the berths above.
#:
#: **A second layer, not more entries in the first, and the distinction is what
#: made the difference.** A radius drawn on a terminal describes the terminal; a
#: ship waiting for that terminal is not at it, she is 15-30 km further out at
#: the anchorage, which is the entire point of an anchorage. Kandla's sits 30 km
#: from the Kandla berth coordinate, so `PORT_RADIUS_KM` at 8 km could never
#: reach it however many ports were listed — which is exactly what happened: a
#: previous session added the missing ports expecting it to suppress these
#: alerts, and nothing changed.
#:
#: Measured before this layer existed: 29 of 33 `loitering_sensitive` alerts
#: were ordinary merchants queueing at Kandla, inside the "Kandla pipeline
#: corridor" geofence — roughly 12% precision against ADR-004's 70% floor.
#:
#: Charted waiting areas, not positions read back from our own corpus. Deriving
#: them from what the generator produced would be fitting the detector to the
#: test set; on the deploy host this layer arrives from WPI with `PORTS`.
ANCHORAGES: dict[str, tuple[float, float]] = {
    "Sikka":     (22.560, 69.700),
    "Vadinar":   (22.560, 69.600),
    "Mundra":    (22.600, 69.500),
    "Kandla":    (22.800, 70.050),
    "Karachi":   (24.650, 66.800),
    "Gwadar":    (24.780, 62.300),
    "JNPT":      (18.800, 72.750),
    "Mangalore": (12.800, 74.650),
    "Kochi":     (9.830, 76.100),
}

#: The subset the scenario generator places vessels at. **Kept as an explicit
#: list rather than "all of PORTS"** so that adding a port here — for the
#: feature extractor's benefit, say — cannot silently change where the fleet
#: sails and break determinism. Adding a scenario port is a deliberate edit.
SCENARIO_PORT_NAMES = ("Sikka", "Vadinar", "Mundra", "Kandla", "Karachi",
                       "Gwadar", "JNPT", "Mumbai", "Mangalore", "Kochi")

SCENARIO_PORTS: dict[str, tuple[float, float]] = {
    name: PORTS[name] for name in SCENARIO_PORT_NAMES}

SCENARIO_ANCHORAGES: dict[str, tuple[float, float]] = {
    name: ANCHORAGES[name] for name in SCENARIO_PORT_NAMES if name in ANCHORAGES}


def port_at(lat: float, lon: float, *, radius_km: float | None = None):
    """The port within `radius_km` of this position, or None.

    Nearest wins, not first-match. The previous implementation in
    `tracks.features` broke out of its loop on the first hit, so at Mumbai and
    JNPT — 11 km apart and both inside the radius — the answer depended on
    dictionary order rather than on distance.
    """
    import math
    r = (radius_km if radius_km is not None else PORT_RADIUS_KM) * 1000.0
    best, best_d = None, r
    for name, (pla, plo) in PORTS.items():
        p1, p2 = math.radians(lat), math.radians(pla)
        dp, dl = math.radians(pla - lat), math.radians(plo - lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        d = 2 * 6_371_000.0 * math.asin(math.sqrt(a))
        if d < best_d:
            best, best_d = name, d
    return best


def at_waiting_area(lat: float, lon: float, *, port_radius_km: float,
                    anchorage_radius_km: float) -> bool:
    """Is this position somewhere a stopped vessel is simply waiting for a berth?

    The question the loitering rule actually needs to ask, and it takes **both**
    layers because they answer different halves of it: a ship alongside is at the
    port, a ship queueing is at the anchorage, and neither radius reaches the
    other. Asking only the port layer is what produced 29 alerts on merchants
    queueing at Kandla.

    The two radii are passed in rather than read from this module so the caller's
    configuration stays authoritative — `config.PORT_RADIUS_KM` and
    `config.ANCHORAGE_RADIUS_KM` are the tuned values; `PORT_RADIUS_KM` here is
    only the default for a bare `port_at` lookup.
    """
    return (_min_km(lat, lon, PORTS) < port_radius_km
            or _min_km(lat, lon, ANCHORAGES) < anchorage_radius_km)


def _min_km(lat: float, lon: float, places: dict) -> float:
    import math
    best = float("inf")
    for pla, plo in places.values():
        p1, p2 = math.radians(lat), math.radians(pla)
        dp, dl = math.radians(pla - lat), math.radians(plo - lon)
        a = (math.sin(dp / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
        best = min(best, 2 * 6371.0 * math.asin(math.sqrt(a)))
    return best
