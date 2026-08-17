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
           "at_waiting_area", "SCENARIO_PORTS", "SCENARIO_ANCHORAGES",
           "GAZETTEER_V1_NAMES", "gazetteer_recall"]

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

    # --- the west-coast gap, closed 2026-08-16 (ADR-030) ------------------
    #
    # **Twenty-five facilities the gazetteer did not know.** Every one of them
    # is a place a vessel in this AOI can and does call at, and a stop at any of
    # them produced no port call, no `docked-at` edge and no port-risk signal —
    # the vessel simply appeared to stop in empty water. That is a recall
    # problem in the literal sense: the evidence existed and nothing could name
    # it. The before/after figure is measured rather than asserted; see
    # `gazetteer_recall()` below and STATE.md.
    #
    # Positions are the facility, moved seaward to the nearest water in the
    # 1 km land mask where the facility position itself is on land — the same
    # rule the original entries follow, and for the same reason: a berth
    # genuinely is on the coastline, and a system whose output is a map needs a
    # reference point a ship can float at. The largest correction is Bedi at
    # 4.5 km, which sits well inside the Gulf of Kachchh; every other one is
    # under 2.5 km.
    #
    # These are PORT POSITIONS ONLY. No anchorage is added for them, because
    # `ANCHORAGES` holds charted waiting areas and this project does not have
    # the charts — inventing twenty waiting areas to make the layer look
    # complete is precisely the fabrication the rest of this file avoids.
    "Jakhau":        (23.2062, 68.5974),
    "Navlakhi":      (22.9587, 70.4388),
    "Bedi":          (22.5505, 70.0500),
    "Okha":          (22.4671, 69.0663),
    "Dwarka":        (22.2361, 68.9576),
    "Mangrol":       (21.1062, 70.0976),
    "Veraval":       (20.8971, 70.3663),
    "Diu":           (20.7167, 70.9925),
    "Dahej":         (21.7090, 72.5300),
    "Dahanu":        (19.9800, 72.7200),
    "Murud":         (18.3200, 72.9500),
    "Dabhol":        (17.5777, 73.1661),
    "Ratnagiri":     (16.9831, 73.2860),
    "Vengurla":      (15.8571, 73.6164),
    "Redi":          (15.7500, 73.6300),
    "Mormugao":      (15.4000, 73.7800),
    "Karwar":        (14.8071, 74.1164),
    "Honnavar":      (14.2710, 74.4239),
    "Malpe":         (13.3500, 74.6900),
    "New Mangalore": (12.9156, 74.7992),
    "Kannur":        (11.8583, 75.3669),
    "Beypore":       (11.1656, 75.7992),
    "Alappuzha":     (9.4900, 76.3100),
    "Kollam":        (8.9300, 76.5300),
    "Vizhinjam":     (8.3742, 76.9970),
}

#: The gazetteer as it stood before ADR-030, recorded so the recall effect of
#: closing the gap is a **measurement rather than a claim**.
#:
#: Kept as a name list in the source rather than recovered from git history,
#: because a before/after number that depends on which commit you happen to
#: have checked out is not reproducible, and this one is quoted.
GAZETTEER_V1_NAMES: frozenset[str] = frozenset({
    "Sikka", "Vadinar", "Mundra", "Kandla", "Porbandar", "Pipavav", "Alang",
    "Hazira", "Magdalla", "Ghogha", "Karachi", "Gwadar", "JNPT", "Mumbai",
    "Mangalore", "Kochi",
})


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


def port_at(lat: float, lon: float, *, radius_km: float | None = None,
            only: frozenset[str] | set[str] | None = None):
    """The port within `radius_km` of this position, or None.

    Nearest wins, not first-match. The previous implementation in
    `tracks.features` broke out of its loop on the first hit, so at Mumbai and
    JNPT — 11 km apart and both inside the radius — the answer depended on
    dictionary order rather than on distance.

    `only` restricts the search to a named subset. It exists for exactly one
    caller — `gazetteer_recall`, which needs to ask the same question of the
    old list and the new one — and it is a parameter rather than a second
    function because measuring the effect of a change with a *different code
    path* than the one being changed measures the wrong thing.
    """
    import math
    r = (radius_km if radius_km is not None else PORT_RADIUS_KM) * 1000.0
    best, best_d = None, r
    items = (PORTS.items() if only is None
             else ((n, c) for n, c in PORTS.items() if n in only))
    for name, (pla, plo) in items:
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


# --------------------------------------------------------------------------
# the before/after measurement
# --------------------------------------------------------------------------

def gazetteer_recall(positions, *, radius_km: float | None = None) -> dict:
    """How many of these positions the gazetteer can name, before and after.

    **The recall figure ADR-030 quotes, computed rather than asserted.** Feed
    it the positions where vessels actually stopped — the loiter episodes the
    feature extractor already derives — and it reports how many the old
    sixteen-port list could name against how many the current list can. The
    difference is the gap that was closed, measured on the same data by the
    same code path.

    A position no list can name is not necessarily a miss: a vessel may
    genuinely have stopped in open water, and that is the whole subject of the
    dark-vessel work. So the third number, `named_by_neither`, is reported
    rather than treated as a failure — it is the denominator's honest residue,
    not a bug.
    """
    positions = list(positions)
    before = after = 0
    gained: dict[str, int] = {}
    for lat, lon in positions:
        b = port_at(lat, lon, radius_km=radius_km, only=GAZETTEER_V1_NAMES)
        a = port_at(lat, lon, radius_km=radius_km)
        if b:
            before += 1
        if a:
            after += 1
        if a and not b:
            gained[a] = gained.get(a, 0) + 1
    n = len(positions)
    return {
        "positions": n,
        "named_before": before,
        "named_after": after,
        "gained": after - before,
        "named_by_neither": n - after,
        "recall_before": (before / n) if n else None,
        "recall_after": (after / n) if n else None,
        "by_new_port": dict(sorted(gained.items(), key=lambda kv: -kv[1])),
        "n_ports_before": len(GAZETTEER_V1_NAMES),
        "n_ports_after": len(PORTS),
    }
