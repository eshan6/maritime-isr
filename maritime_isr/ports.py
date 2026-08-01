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

__all__ = ["PORTS", "PORT_RADIUS_KM", "port_at", "SCENARIO_PORTS"]

#: How close a position must be to count as being at a port, kilometres.
#: Shared so the feature extractor and anything else asking "which port is
#: this" cannot answer differently.
PORT_RADIUS_KM = 15.0

#: name -> (lat, lon). One list.
PORTS: dict[str, tuple[float, float]] = {
    # --- Gujarat crude/product cluster ------------------------------------
    # Where most Arabian Sea tanker traffic in this AOI actually goes, and
    # where the first two were missing from the feature extractor entirely.
    "Sikka":     (22.43, 69.84),
    "Vadinar":   (22.28, 69.73),
    "Mundra":    (22.74, 69.70),
    "Kandla":    (22.99, 70.22),
    "Porbandar": (21.63, 69.60),

    # --- observed in the real corpus, GFW's own anchorage coordinates ------
    "Pipavav":   (20.9218, 71.5150),
    "Alang":     (21.7847, 72.1412),   # the shipbreaking yard; GFW: ind-ind-76
    "Hazira":    (21.1448, 72.6613),
    "Magdalla":  (21.1327, 72.7060),
    "Ghogha":    (21.6977, 72.2813),

    # --- Pakistan ---------------------------------------------------------
    "Karachi":   (24.81, 66.97),
    # The seaward approach anchorage, NOT the berth: the berth is at ~25.12N,
    # north of AOI v1's 25N edge, and a position outside the AOI is outside
    # everything this system is scoped to.
    "Gwadar":    (24.88, 62.32),

    # --- Indian west coast ------------------------------------------------
    "JNPT":      (18.95, 72.95),
    "Mumbai":    (18.92, 72.83),
    "Mangalore": (12.92, 74.80),
    "Kochi":     (9.97, 76.26),
}

#: The subset the scenario generator places vessels at. **Kept as an explicit
#: list rather than "all of PORTS"** so that adding a port here — for the
#: feature extractor's benefit, say — cannot silently change where the fleet
#: sails and break determinism. Adding a scenario port is a deliberate edit.
SCENARIO_PORT_NAMES = ("Sikka", "Vadinar", "Mundra", "Kandla", "Karachi",
                       "Gwadar", "JNPT", "Mumbai", "Mangalore", "Kochi")

SCENARIO_PORTS: dict[str, tuple[float, float]] = {
    name: PORTS[name] for name in SCENARIO_PORT_NAMES}


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
