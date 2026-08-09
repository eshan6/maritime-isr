"""Ordinary movement for the commercial fleet — the volume the picture needs.

No truth rows: nothing here is a scenario. These are merchant vessels working
the real ports of the AOI, and their whole purpose is to be numerous and
uninteresting, so that finding the one hull that matters is an achievement
rather than an arithmetic certainty.

**Routes are coastal by design, and that is a cost decision as much as a
realism one.** A Gulf-to-Kutch laden transit is a thousand miles of open water
and emits AIS the whole way; a Mumbai-JNPT-Mangalore rotation produces the same
number of port visits for a fraction of the positions. The named cast already
supplies the long-haul transits the scenarios need, so the fleet buys density
where density is cheap: calls, dwells, anchorage waits and coastal loitering.

**Everything is bounded by the corpus window.** A call scheduled near the end is
truncated rather than allowed to overrun, because a corpus that only validates
on a lucky seed is not reproducible — the same failure the named background
traffic hit on seed 8.
"""
from __future__ import annotations

from ..commercial import fleet_key, fleet_size
from ..geography import PORTS
from ..primitives.port_call import build_port_call
from ..world import ScenarioWorld, week
from .common import V, add_loiter, add_port_visit, emit, hours

#: Coastal rotations the fleet works. Each is a plausible sequence of calls for
#: a vessel in this trade; a hull picks one and runs it.
#:
#: **Karachi and Gwadar are deliberately absent.** Both sit within about a
#: kilometre of the AOI's northern edge (25.0N), and a call there puts the
#: vessel at an anchorage a few hundred metres over the line — measured, 16
#: track points at 25.000-25.004N. The AOI bound is the contract every located
#: record is checked against, so the fleet works the ports that are comfortably
#: inside it rather than having the boundary quietly widened for traffic that
#: exists only to add volume. Both ports remain in the corpus through the named
#: cast and the gazetteer.
ROTATIONS: tuple[tuple[str, ...], ...] = (
    ("Sikka", "Vadinar", "Mundra"),
    ("Kandla", "Mundra", "JNPT"),
    ("Mumbai", "JNPT", "Mangalore"),
    ("Mangalore", "Kochi", "Mumbai"),
    ("JNPT", "Mumbai", "Kandla"),
    ("Vadinar", "Sikka", "Kandla"),
    ("Kochi", "Mangalore", "JNPT"),
    ("Mundra", "Kandla", "Sikka"),
    ("Mumbai", "Kochi"),
    ("Kandla", "Mangalore"),
    ("Mumbai", "Mangalore"),
    ("Sikka", "Mundra", "Mumbai"),
)

#: Below this many hours left in the window, do not start another call.
_MIN_REMAINING_H = 30.0


def commercial_traffic(world: ScenarioWorld) -> None:
    n = fleet_size()
    if n == 0:
        return
    r = world.rng

    for i in range(n):
        key = fleet_key(i)
        v = V(world, key)
        rotation = ROTATIONS[i % len(ROTATIONS)]
        # Stagger departures across the window so arrivals are not synchronised;
        # a fleet that all sails on the same morning makes every port look
        # congested at once and every anchorage empty in between.
        t = week(1, hours=(i % 40) * 9 + r.uniform(0, 8))
        pos = PORTS[rotation[0]]

        for port in rotation:
            remaining_h = (world.t1 - t).total_seconds() / 3600.0
            if remaining_h < _MIN_REMAINING_H:
                break
            wait = min(world.profile.sample("anchorage_wait_hours", r) * 0.3,
                       max(remaining_h * 0.12, 1.0))
            dwell = min(world.profile.sample("port_call_dwell_hours", r) * 0.5,
                        max(remaining_h - wait - 20.0, 3.0))
            pts, spec = build_port_call(
                v, port, arrive_from=pos, t_start=t, rng=r,
                anchorage_hours=wait, berth_hours=dwell)
            emit(world, key, pts)
            add_port_visit(world, "COM", key, spec)

            # A share of the calls include a spell at anchor before the berth —
            # ordinary queueing, and the reason a loitering detector needs a
            # port-proximity rule rather than a bare speed threshold.
            if wait >= 4.0 and i % 3 == 0:
                add_loiter(world, "COM", key, t, t + hours(wait),
                           spec.lat, spec.lon,
                           mean_sog_kn=r.uniform(0.2, 1.1))

            pos = (pts[-1].lat, pts[-1].lon)
            t = pts[-1].t + hours(r.uniform(4, 26))
            if t > world.t1 - hours(_MIN_REMAINING_H):
                break


SCENARIOS = (commercial_traffic,)
