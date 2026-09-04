"""Ordinary movement for the wider fleet — one motion pattern per archetype.

No truth rows: nothing in this module is a scenario. These four hundred hulls
exist to be the sea that the scenarios happen in, and their whole job is to be
numerous, varied and individually uninteresting.

**Why each archetype gets its own generator instead of a shared rotation.**
`commercial_traffic.py` moves every hull the same way — pick a three-port
rotation, wait, berth, sail — and that is exactly right for what it is: bulk
background that must not be distinguishable from anything. But it means a
`fishing` hull and a `VLCC` hull in that fleet describe the same motion, and
`tracks/vessel_type.py` is trained on this corpus with the declared class as its
label. A trawler that steams a straight line at thirteen knots is a mislabelled
example: it does not merely fail to help the classifier, it teaches it that
`fishing` looks like a merchant and moves the confusion matrix the product
quotes.

So the pattern is chosen by archetype and the archetype declares the kinematic
envelope it must land inside (`fleet.ARCHETYPES`). `validate._check_archetypes`
fails the build when a hull falls outside her own band, which makes "the label
matches the motion" a checked property rather than an intention.

**Everything here draws from the fleet's derived RNG**, never `world.rng` — see
the `fleet` module docstring for the measurement that made that non-negotiable.

**Everything is bounded by the corpus window.** A call scheduled near the end is
dropped rather than allowed to overrun, for the reason `background.py` gives: a
corpus that only validates at a lucky seed is not reproducible.
"""
from __future__ import annotations

import hashlib

from ..fleet import ARCHETYPES, fleet_rng
from ..geography import (BOMBAY_HIGH, PORTS, haversine_m, destination,
                         initial_bearing_deg)
from ..primitives.ais import emit_ais
from ..primitives.port_call import anchorage_of, build_port_call
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..searoute import nearest_water, seaward_point
from ..world import ScenarioWorld, week
from .common import V, add_loiter, add_port_visit, hours

#: Below this many hours left in the window, do not start another leg.
MIN_REMAINING_H = 26.0

#: Liner rotations, deep-sea and coastal. Real ports, in orders a service would
#: actually work.
#:
#: **Every leg has to be long enough for the class to reach service speed.** The
#: rotation originally included Mundra-Kandla, thirty nautical miles between two
#: adjacent Gujarat ports, and every hull working it came out with a
#: ninetieth-percentile speed of six to nine knots against a class that is
#: defined by running at nineteen. She never got out of the approach. A liner
#: doing a thirty-mile shuttle is not a liner, and a corpus that contains six of
#: them is teaching the type classifier that `container` means "slow".
BOX_ROUTES: tuple[tuple[str, ...], ...] = (
    ("Mundra", "JNPT"), ("JNPT", "Mundra"), ("Mundra", "Mangalore"),
    ("JNPT", "Kochi"), ("Kochi", "JNPT"), ("Mangalore", "Mundra"),
)

#: Coastal feeder legs — shorter hops between the smaller berths.
#: Mumbai-JNPT was dropped for the reason above: six miles across one harbour is
#: a shift, not a voyage, and the hulls working it never exceeded a knot.
FEEDER_ROUTES: tuple[tuple[str, ...], ...] = (
    ("Mumbai", "Mangalore"), ("Mangalore", "Kochi"), ("Kochi", "Mumbai"),
    ("JNPT", "Mangalore"), ("Mangalore", "Mumbai"), ("Kochi", "Mangalore"),
)

#: Product-tanker terminals. The Gulf of Kutch is where the refineries are.
PRODUCT_ROUTES: tuple[tuple[str, ...], ...] = (
    ("Vadinar", "Mumbai"), ("Sikka", "Mangalore"), ("Vadinar", "Kochi"),
    ("Sikka", "JNPT"), ("Mumbai", "Vadinar"), ("Kochi", "Sikka"),
)

#: Where crude discharges.
CRUDE_TERMINALS: tuple[str, ...] = ("Vadinar", "Sikka")

#: Where dry bulk queues.
BULK_TERMINALS: tuple[str, ...] = ("Kandla", "Mundra")

#: Reefer runs — fish and produce north to the box ports.
REEFER_ROUTES: tuple[tuple[str, ...], ...] = (
    ("Kochi", "Mumbai"), ("Mangalore", "JNPT"), ("Kochi", "JNPT"),
)

#: Ferry runs. Short, fixed, and repeated — the point of the archetype.
#: Every pair is a genuinely short leg on this coast; a "ferry" doing 300 nm
#: would be a small cargo ship with a different name. But "short" has a floor as
#: well as a ceiling: Sikka-Vadinar is seven miles, and a hull that spends the
#: whole crossing accelerating and decelerating peaked at under ten knots, which
#: puts a `ferry` inside the dhow's speed band. Six to thirty miles is the
#: window where the leg is unmistakably a shuttle *and* she gets up to speed on
#: it.
FERRY_RUNS: tuple[tuple[str, str], ...] = (
    ("Mumbai", "JNPT"), ("Mundra", "Sikka"), ("Kandla", "Mundra"),
)

#: Harbours the tugs work out of.
TUG_PORTS: tuple[str, ...] = ("Mumbai", "JNPT", "Kandla", "Mundra", "Kochi",
                              "Mangalore")

#: Where the trawlers work. Real productive grounds off Gujarat and the Konkan,
#: given as (lat, lon) centres; each boat takes a patch a few miles across
#: inside one of them.
FISHING_GROUNDS: tuple[tuple[float, float], ...] = (
    (20.80, 68.60), (21.40, 68.90), (20.10, 69.40), (19.30, 71.20),
    (17.60, 72.40), (15.20, 73.10), (13.40, 74.00), (11.20, 75.30),
)

#: Home landings the trawlers sail from.
FISHING_HOMES: tuple[str, ...] = ("Sikka", "Mumbai", "Mangalore", "Kochi")


def home_for_ground(ground: tuple[float, float]) -> str:
    """The landing a boat working `ground` actually sails from: the nearest one.

    **This is not a cosmetic pairing.** The first version indexed homes and
    grounds by two independent moduli, so a boat homed at Kochi was sent to work
    a ground off Saurashtra — a 600-nautical-mile passage each way in a 27-metre
    trawler. It broke the corpus twice over. The transit swamped the fishing, so
    a hull labelled `fishing` came out with a *median* speed of 11.6 knots and a
    turn rate below a bulker's, which is a merchant's motion wearing a trawler's
    label — precisely the mislabelled training example `fleet.ARCHETYPES` exists
    to prevent, in the one class the type classifier can genuinely separate. And
    the passage itself ran down the Malabar coast and clipped the shore near
    Kannur, which is where two of the afloat violations came from.

    Fishing boats work the grounds off their own landing. Pairing by distance
    makes the motion match the label because it makes the voyage the real one.
    """
    return min(FISHING_HOMES,
               key=lambda name: haversine_m(*PORTS[name], *ground))

#: Where the dhows trade. Small inshore hops, twelve to forty miles.
#: Mumbai-JNPT was dropped for the same reason it left the feeder rotation: a
#: forty-five-minute hop followed by half a day at anchor left the hull's
#: ninetieth-percentile speed at one knot, which is an unattended mooring
#: rather than a working dhow.
DHOW_LEGS: tuple[tuple[str, str], ...] = (
    ("Sikka", "Mundra"), ("Mundra", "Kandla"), ("Kandla", "Sikka"),
    ("Vadinar", "Kandla"),
)


# ==========================================================================
# helpers
# ==========================================================================

#: How far off the terminal a harbour mooring sits, in nautical miles, tried in
#: order until one is clear of the land mask with room to swing.
_MOORING_TRIES_NM = (1.0, 1.6, 2.4, 3.4, 5.0, 7.0)

#: Radius the mooring must have clear water inside, in metres. A `moored` leg
#: still drifts a few hundred metres on the tide and a `station` leg is given
#: 350-700 m, so a point that is water only at its exact centre is not a berth,
#: it is a coin flip repeated four thousand times.
_MOORING_CLEARANCE_M = 900.0

_MOORINGS: dict[str, tuple[float, float]] = {}


def harbour_mooring(port: str) -> tuple[float, float]:
    """A mooring just off `port`, with room to swing, clear of the land mask.

    **Why harbour craft cannot lie at the berth coordinate.** `build_port_call`
    moors a merchant at `PORTS[port]` and that is right: a berth is against the
    quay, the 1 km land mask cannot resolve a dock, and the handful of alongside
    points that sample as land are what `validate.AFLOAT_MAX_RATE` exists to
    tolerate. A merchant spends two days alongside out of a three-week voyage,
    so those points are a rounding error in her track.

    A tug, a ferry and a dhow are the opposite shape. Their whole existence is
    inside one harbour, alongside is *most* of the track, and mooring them on
    the quay coordinate put a third of three hulls' positions on land — a real
    failure of the afloat check, not a tolerance question. Widening the
    tolerance to admit them would have retired the check that once found 51.3%
    of the corpus steaming across Gujarat.

    So harbour craft lie at a mooring a mile or two off the terminal, which is
    also what they actually do between jobs. The point is found by walking
    seaward — toward the port's own anchorage where there is one, due west into
    the Arabian Sea where the anchorage *is* the port — and taking the first
    position with `_MOORING_CLEARANCE_M` of water all round it. Deterministic
    and cached, so a hull's berth does not depend on the RNG or on call order.
    """
    if port in _MOORINGS:
        return _MOORINGS[port]
    berth = PORTS[port]
    anch = anchorage_of(port)
    # Most of these berths already have a mile of water round them at the
    # mask's resolution and need no correction — only JNPT, up the Mumbai
    # harbour channel, does. Leaving the others exactly where they are keeps the
    # harbour craft on the terminal they belong to.
    out = berth
    if not _has_sea_room(berth):
        # Toward the anchorage when there is a distinct one; otherwise due west,
        # which on this coast is open sea from every port in the corpus.
        brg = (initial_bearing_deg(*berth, *anch)
               if haversine_m(*berth, *anch) > 1000.0 else 270.0)
        out = nearest_water(anch)
        for nm in _MOORING_TRIES_NM:
            p = seaward_point(berth, brg, nm * 1852.0)
            if _has_sea_room(p):
                out = p
                break
    _MOORINGS[port] = out
    return out


def _has_sea_room(p: tuple[float, float]) -> bool:
    """True when `p` and everything within `_MOORING_CLEARANCE_M` is water."""
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return True
    ring = [p] + [destination(p[0], p[1], b, _MOORING_CLEARANCE_M)
                  for b in range(0, 360, 30)]
    lat = np.array([q[0] for q in ring], dtype=float)
    lon = np.array([q[1] for q in ring], dtype=float)
    return not bool(globe.is_land(lat, lon).any())


#: Cross-track lane offsets, metres. Hulls working the same rotation get the
#: same deterministic route out of `searoute`, so without this they trace the
#: *identical* path and pass each other at nothing. Measured before the fix:
#: 25,811 encounters among 453 hulls, 76.6 per hull, with a tenth percentile
#: closest approach of **21 metres**. Twenty-one metres between two merchant
#: ships is not an encounter, it is a collision, and an encounter table built
#: from it can only teach a rendezvous rule to fire on ordinary traffic.
_LANE_MIN_M = 300.0
_LANE_MAX_M = 2400.0

#: The lane is handed to `build_port_call`, which moves the ROUTE waypoints and
#: lets the integrator produce the motion. Displacing the integrated track
#: instead was tried first and is unfixable — see `port_call._laned` for the
#: mechanism and the 75-knot container ships that ended it.


def lane_offset_m(key: str) -> float:
    """This hull's signed lane, in metres. Stable across regeneration.

    Derived from the key by hash rather than drawn from the fleet RNG: it is a
    property of the hull, not an event in her voyage, and a hash keeps it
    identical no matter what order the archetypes are built in. `hash()` itself
    is unusable here — PYTHONHASHSEED randomises it per process, so the corpus
    would not reproduce.
    """
    digest = hashlib.blake2b(key.encode(), digest_size=8).digest()
    u = int.from_bytes(digest, "big") / float(1 << 64)
    side = 1.0 if u >= 0.5 else -1.0
    frac = (u * 2.0) % 1.0
    return side * (_LANE_MIN_M + frac * (_LANE_MAX_M - _LANE_MIN_M))


def emit_fleet(world: ScenarioWorld, key: str, points, rng, *,
               suppressions=None) -> None:
    """`common.emit`, but on the fleet's own random stream.

    Identical in every other respect: the same integrator, the same emitter, the
    same noise model. That sameness is load-bearing — a background population
    generated more cheaply than the scenario hulls would be separable on
    craftsmanship, and every precision figure measured against this corpus would
    then be measuring the generator (`test_decoys_are_not_trivially_separable`).
    The only thing that differs is which stream the draws come from.
    """
    v = V(world, key)
    world.add_track(v.entity_id, points)
    if not v.ais_expected:
        return
    world.add_ais(v.entity_id, emit_ais(v, points, rng,
                                        suppressions=suppressions))


def _room(world: ScenarioWorld, t) -> float:
    return (world.t1 - t).total_seconds() / 3600.0


def _call(world: ScenarioWorld, key: str, port: str, *, pos, t, rng,
          wait_h: float, berth_h: float, scenario_id: str = "FL",
          declare: bool = True, skip_berth: bool = False):
    """One port call, landed, or None when the window has no room for it.

    Returns (position, time) to continue from. The overrun guard is the one
    `background.py` arrived at the hard way: the budget is measured from when
    she *sails*, and the passage there is not known until the call is built, so
    the berth is shortened afterwards to fit and the call is dropped outright
    when shortening it would leave a berth nobody would believe.
    """
    if _room(world, t) < MIN_REMAINING_H:
        return None
    lane = lane_offset_m(key)
    pts, spec = build_port_call(V(world, key), port, arrive_from=pos,
                               t_start=t, rng=rng, anchorage_hours=wait_h,
                               berth_hours=berth_h, lane_offset_m=lane,
                               skip_berth=skip_berth)
    overrun_h = (pts[-1].t - (world.t1 - hours(2))).total_seconds() / 3600.0
    if overrun_h > 0.0:
        berth_h -= overrun_h
        if berth_h < 4.0:
            world.clipped.append(
                f"fleet {key}: call at {port} dropped — no room in the window")
            return None
        pts, spec = build_port_call(V(world, key), port, arrive_from=pos,
                                   t_start=t, rng=rng, anchorage_hours=wait_h,
                                   berth_hours=berth_h, lane_offset_m=lane,
                                   skip_berth=skip_berth)
    emit_fleet(world, key, pts, rng)
    add_port_visit(world, scenario_id, key, spec, declare=declare)
    return (pts[-1].lat, pts[-1].lon), pts[-1].t


# ==========================================================================
# one generator per archetype
# ==========================================================================

def _liner(world, key, rng, i, routes, *, wait=(0.4, 2.5), berth=(10.0, 22.0),
           calls=2):
    """Scheduled service: sail, berth briefly, sail again. Fast and repetitive."""
    route = routes[i % len(routes)]
    pos = PORTS[route[0]]
    t = week(1, hours=(i % 34) * 10 + rng.uniform(0, 9))
    for n in range(calls):
        port = route[(n + 1) % len(route)]
        got = _call(world, key, port, pos=pos, t=t, rng=rng,
                    wait_h=rng.uniform(*wait), berth_h=rng.uniform(*berth))
        if got is None:
            return
        pos, t = got
        t = t + hours(rng.uniform(6, 30))


def _crude_entry(world, key, rng, i, terminals):
    """In from the northwest corridor, discharge for days, and back out.

    One very long straight leg is the whole kinematic signature of the class,
    and it is why the crude archetypes carry the tightest turn-rate band in
    `fleet.ARCHETYPES`.
    """
    from ..geography import NW_ENTRY
    port = terminals[i % len(terminals)]
    t = week(1, hours=(i % 28) * 12 + rng.uniform(0, 10))
    got = _call(world, key, port, pos=NW_ENTRY, t=t, rng=rng,
                wait_h=rng.uniform(4.0, 26.0), berth_h=rng.uniform(40.0, 90.0))
    if got is None:
        return
    pos, t = got
    if _room(world, t) < 40.0:
        return
    # Outbound, part-way back up the corridor. She does not have to get there.
    out = nearest_water(seaward_point(pos, 285.0, 150.0 * 1852.0))
    pts = generate_track(V(world, key), VoyagePlan(
        start=pos, start_time=t + hours(rng.uniform(2, 8)),
        legs=[Leg("transit", target=out,
                  speed_kn=V(world, key).service_kn)]), rng)
    if pts:
        emit_fleet(world, key, pts, rng)


def _bulk_waiter(world, key, rng, i, terminals):
    """The anchorage queue. Most of her track is spent stopped, on purpose.

    A bulker's median speed sits far below her passage speed because she spends
    a day or more swinging at anchor outside Kandla or Mundra waiting for a
    berth — which is also why the loitering rule needs a port-proximity term
    rather than a bare speed threshold, and why this archetype is the honest
    denominator for that rule.
    """
    port = terminals[i % len(terminals)]
    origin = PORTS["Mumbai"] if i % 2 else PORTS["Mangalore"]
    t = week(1, hours=(i % 40) * 10 + rng.uniform(0, 12))
    wait = rng.uniform(20.0, 58.0)
    got = _call(world, key, port, pos=origin, t=t, rng=rng,
                wait_h=wait, berth_h=rng.uniform(24.0, 60.0))
    if got is None:
        return
    anch = anchorage_of(port)
    add_loiter(world, "FL", key, t + hours(_passage_guess(world, key, origin,
                                                          anch)),
               t + hours(_passage_guess(world, key, origin, anch) + wait),
               anch[0], anch[1], mean_sog_kn=rng.uniform(0.2, 1.0))


def _passage_guess(world, key, a, b) -> float:
    """Hours from `a` to `b`, along the route she will actually be given.

    **Not the great-circle distance.** `generate_track` expands a transit into
    waypoints round the coast, and on this coast that is not a rounding error:
    Sikka lies inside the Gulf of Kutch, so a boat working a ground 145 miles
    south has to come out of the gulf and round the Saurashtra peninsula, and
    her real passage is nearly twice the straight line. Nine trawlers ended up
    with a merchant's median speed because the working spell had been sized
    against the straight line — the ratio in `_WORK_TO_PASSAGE` was right and it
    was being applied to the wrong number.
    """
    from ..searoute import sea_route

    v = V(world, key)
    legs = [a, *sea_route(a, b), b]
    nm = sum(haversine_m(*p, *q) for p, q in zip(legs, legs[1:])) / 1852.0
    return nm / max(v.service_kn * 0.9, 1.0)


#: Hours a trawler must work the ground for every hour of the passage out.
#:
#: **Set against the measured split, not against the arithmetic.** The obvious
#: value is a little over 2, since fishing then exceeds steaming — and at 2.6
#: the measured working fraction came out at 45%, not the 57% the arithmetic
#: promised, so half the boats still had a merchant's median speed. The gap is
#: the routing: a transit leg is expanded into waypoints round the coast and
#: costs more than the great-circle guess, and the guess is what this ratio is
#: applied to. 3.2 puts the measured fraction above 60% with margin, which is
#: the number that matters. See `_trawler`.
_WORK_TO_PASSAGE = 3.2

#: The widest circle a boat wanders while working, plus a margin. A ground has
#: to have this much clear water round it or the wander crosses the beach.
_GROUND_CLEARANCE_M = 16_000.0


def _fishing_ground(rng, centre: tuple[float, float]) -> tuple[float, float]:
    """A patch near `centre` with room for a boat to work it without grounding.

    `nearest_water` guarantees the centre is *at* sea and nothing more. A boat
    trawling a 15 km radius round a point 3 km off the Malabar coast spends
    forty per cent of her track ashore, which is what the afloat validator found
    on `fl_trawler_07`. So the offset is drawn as before, and then pushed
    seaward until the whole working circle is clear — westward, because on this
    coast that is where the open sea is. Falling back to the snapped point keeps
    the old behaviour when no clear patch is found rather than inventing a
    position; the validator then fails on it, which is the intended way to be
    told.
    """
    p = nearest_water((centre[0] + rng.uniform(-0.35, 0.35),
                       centre[1] + rng.uniform(-0.45, 0.45)))
    for km in (0.0, 8.0, 16.0, 25.0, 35.0, 50.0):
        q = p if km == 0.0 else destination(p[0], p[1], 270.0, km * 1000.0)
        if _clear_circle(q, _GROUND_CLEARANCE_M):
            return q
    return p


def _clear_circle(p: tuple[float, float], radius_m: float) -> bool:
    try:
        import numpy as np
        from global_land_mask import globe
    except ImportError:                                          # pragma: no cover
        return True
    ring = [p] + [destination(p[0], p[1], b, r)
                  for r in (radius_m * 0.6, radius_m)
                  for b in range(0, 360, 20)]
    lat = np.array([q[0] for q in ring], dtype=float)
    lon = np.array([q[1] for q in ring], dtype=float)
    return not bool(globe.is_land(lat, lon).any())


def _trawler(world, key, rng, i):
    """Out, work the ground for a day or two, home. Twice if the window allows.

    The working legs are what the archetype *is*: 2.5-4 knots with a course
    change every twenty to sixty minutes inside a radius of a few miles. Every
    other class in this corpus makes long straight legs; this is the one motion
    a type classifier can genuinely separate from motion alone, and it is only
    separable because it is generated as the thing it claims to be.

    **So the working spell is sized against the passage, not drawn independently
    of it.** A fixed 18-40 hours of fishing is a trawler's motion only if the
    steaming either side of it is shorter; the first version drew the two
    without reference to each other and produced hulls whose *median* speed was
    eleven knots — a boat that spent most of her track in transit and a few
    hours fishing, labelled `fishing`, in the one class the classifier can
    actually separate. `_WORK_TO_PASSAGE` is what makes the label true: at 2.6
    times the one-way passage, fishing is 57% of the track by construction, so
    the median sits in the working band however far offshore the ground is. A
    trip that will not fit that ratio inside the window does not sail at all,
    which is the right answer — a boat that cannot work the ground stays in.
    """
    ground = FISHING_GROUNDS[i % len(FISHING_GROUNDS)]
    ground = _fishing_ground(rng, ground)
    home = PORTS[home_for_ground(ground)]
    v = V(world, key)
    t = week(1, hours=(i % 46) * 9 + rng.uniform(0, 10))
    out_h = _passage_guess(world, key, home, ground)
    for _ in range(2):
        work_h = min(max(rng.uniform(30.0, 60.0), _WORK_TO_PASSAGE * out_h),
                     _room(world, t) - 2.0 * out_h - 8.0)
        if work_h < _WORK_TO_PASSAGE * out_h or work_h < 20.0:
            return
        legs = [Leg("transit", target=ground, speed_kn=v.service_kn),
                Leg("fishing", target=ground, duration_h=work_h,
                    radius_m=rng.uniform(7000.0, 15000.0),
                    speed_kn=rng.uniform(2.5, 3.9)),
                Leg("transit", target=home, speed_kn=v.service_kn)]
        pts = generate_track(v, VoyagePlan(start=home, start_time=t,
                                           legs=legs), rng)
        if not pts:
            return
        emit_fleet(world, key, pts, rng)
        # The working spell, landed as a loitering event the way GFW lands
        # theirs. It is not an accusation: a trawler loitering on a fishing
        # ground is a trawler fishing, and a corpus in which loitering is always
        # suspicious would make the loitering detector worthless.
        t_work0 = t + hours(_passage_guess(world, key, home, ground))
        add_loiter(world, "FL", key, t_work0, t_work0 + hours(work_h * 0.9),
                   ground[0], ground[1], mean_sog_kn=rng.uniform(2.4, 3.6))
        t = pts[-1].t + hours(rng.uniform(24, 70))


def _tug(world, key, rng, i):
    """Harbour work: several short jobs out of one port, and nothing else.

    Tiny spread, low straightness, a turn rate several times any merchant's —
    and she never leaves her harbour, which is the feature that separates her
    from a small craft doing the same speeds out at sea.

    **She lies at a mooring, not at the quay** (`harbour_mooring`), and the
    stand-down between jobs is one to four hours rather than three to nine. Both
    are corrections from measurement. At nine hours idle her track was 85%
    stationary, which made her ninetieth-percentile speed 0.6 knots: a hull
    labelled `tug` that a type classifier can only see as a moored barge, which
    is the mislabelling this whole archetype scheme exists to stop. A working
    harbour tug does several jobs a day.
    """
    port = TUG_PORTS[i % len(TUG_PORTS)]
    base = harbour_mooring(port)
    v = V(world, key)
    t = week(1, hours=(i % 30) * 11 + rng.uniform(0, 8))
    for _ in range(8):
        if _room(world, t) < 12.0:
            return
        # The job: out to a ship waiting a few miles off, work her in, back.
        job = nearest_water(seaward_point(
            base, rng.uniform(200.0, 300.0), rng.uniform(3.0, 11.0) * 1852.0))
        legs = [
            Leg("transit", target=job, speed_kn=rng.uniform(8.5, 11.0)),
            Leg("station", duration_h=rng.uniform(0.8, 2.4), radius_m=350.0),
            Leg("transit", target=base, speed_kn=rng.uniform(5.0, 8.0)),
            Leg("moored", duration_h=rng.uniform(1.0, 4.0)),
        ]
        pts = generate_track(v, VoyagePlan(start=base, start_time=t,
                                           legs=legs), rng)
        if not pts:
            return
        emit_fleet(world, key, pts, rng)
        t = pts[-1].t + hours(rng.uniform(2, 14))


def _osv(world, key, rng, i):
    """Run out to the field, hold station beside a platform, run back.

    Twelve to thirty hours stationary in open water is a shape nothing else in
    the corpus makes: a merchant stopped that long offshore is a transfer
    candidate, and this archetype is the population that makes that rule
    measurable instead of merely plausible.
    """
    port = "Mumbai" if i % 2 else "JNPT"
    base = PORTS[port]
    v = V(world, key)
    t = week(1, hours=(i % 26) * 13 + rng.uniform(0, 9))
    field = nearest_water((BOMBAY_HIGH[0] + rng.uniform(-0.35, 0.35),
                           BOMBAY_HIGH[1] + rng.uniform(-0.45, 0.45)))
    for _ in range(2):
        if _room(world, t) < 50.0:
            break
        stand_h = min(rng.uniform(12.0, 30.0), max(_room(world, t) - 30.0, 4.0))
        legs = [Leg("transit", target=field, speed_kn=rng.uniform(10.0, 12.5)),
                Leg("station", duration_h=stand_h, radius_m=500.0),
                Leg("transit", target=base, speed_kn=rng.uniform(10.0, 12.5))]
        pts = generate_track(v, VoyagePlan(start=base, start_time=t,
                                           legs=legs), rng)
        if not pts:
            break
        emit_fleet(world, key, pts, rng)
        t0 = t + hours(_passage_guess(world, key, base, field))
        add_loiter(world, "FL", key, t0, t0 + hours(stand_h * 0.9),
                   field[0], field[1], mean_sog_kn=rng.uniform(0.2, 0.9))
        t = pts[-1].t + hours(rng.uniform(20, 60))
    # One landed call, so an offshore support vessel is visible in the port
    # picture as well as the offshore one.
    _call(world, key, port, pos=base, t=t, rng=rng,
          wait_h=rng.uniform(0.3, 2.0), berth_h=rng.uniform(8.0, 22.0))


def _ferry(world, key, rng, i):
    """The same short leg, over and over, with a turnaround at each end.

    Repetition is the signature. Speed alone would put her with the reefers;
    what separates her is that her whole track fits in a box twenty miles
    across and she crosses it a dozen times.

    **The turnarounds are minutes, not hours, and there are twenty crossings.**
    The first version gave her ten crossings with one to three hours alongside
    and up to five more between, which left her stationary for three-quarters of
    her own track: median speed zero, ninetieth percentile six knots. That is
    not a ferry, it is a small ship that occasionally moves, and it is the exact
    failure mode `fleet.ARCHETYPES` was written to catch — the label said
    `ferry` and the motion said moored barge. A harbour ferry turns round in
    twenty minutes and goes again, which is what makes the repetition visible at
    all.
    """
    # Pick a run whose crossing is actually water. A ferry builds her own legs
    # rather than going through `build_port_call`, so the leg guard there never
    # sees her — and the Mumbai/JNPT pair steams straight across the harbour
    # headland. `sea_route` does not rescue it either: on a 20 km leg its
    # sampling is ~244 m and the headland is about that wide, so it reports the
    # crossing clear. Checked at 40 m instead, and a run that is not water is
    # skipped rather than sailed: losing one harbour route costs the corpus far
    # less than a ferry driving over Nhava Sheva twenty times.
    from ..primitives.port_call import _leg_clear
    runs = FERRY_RUNS[i % len(FERRY_RUNS):] + FERRY_RUNS[:i % len(FERRY_RUNS)]
    for a_name, b_name in runs:
        a, b = harbour_mooring(a_name), harbour_mooring(b_name)
        if _leg_clear(a, b):
            break
    else:
        world.clipped.append(
            f"fleet {key}: no ferry run with a clear crossing — hull skipped")
        return
    v = V(world, key)
    t = week(1, hours=(i % 24) * 12 + rng.uniform(0, 6))
    pos, target = a, b
    for n in range(20):
        if _room(world, t) < 8.0:
            break
        legs = [Leg("transit", target=target, speed_kn=rng.uniform(14.0, 17.0)),
                Leg("moored", duration_h=rng.uniform(0.25, 0.6))]
        pts = generate_track(v, VoyagePlan(start=pos, start_time=t,
                                           legs=legs), rng)
        if not pts:
            break
        emit_fleet(world, key, pts, rng)
        pos, target = target, pos
        t = pts[-1].t + hours(rng.uniform(0.1, 0.5))
    # Two of her calls land as port visits. Not all of them: a domestic ferry
    # shuttling twice a day does not generate a pre-arrival notification per
    # crossing, and landing twenty visits per hull would have buried the
    # merchant port picture under harbour traffic.
    #
    # The berth is short for the same reason the turnarounds are: a ten-hour
    # dwell at the end swamps twenty crossings and puts the median back at zero.
    for port in (a_name, b_name):
        # `skip_berth`: she waits off the terminal instead of mooring on the
        # quay coordinate. This is `harbour_mooring`'s own argument applied
        # where it was being ignored — a harbour ferry's whole track is inside
        # one port, so the handful of alongside points the 1 km land mask reads
        # as shore are a fifth of HER track rather than a rounding error on a
        # merchant's three-week voyage. That is what put fl_ferry_00 18% ashore.
        got = _call(world, key, port, pos=pos, t=t + hours(rng.uniform(1, 4)),
                    rng=rng, wait_h=rng.uniform(0.2, 1.0),
                    berth_h=rng.uniform(2.0, 5.0), skip_berth=True)
        if got is None:
            return
        pos, t = got


def _dhow(world, key, rng, i):
    """Inshore hops of a few hours with spells at anchor between them.

    Six hops rather than four, and the anchor spells are three to ten hours
    rather than six to twenty. Twenty hours at anchor after a two-hour hop left
    nine tenths of her track stationary, so her ninetieth-percentile speed came
    out at one knot — a `dhow` that a type classifier can only read as an
    unattended mooring. She trades for a living; the hops are the point of her.
    """
    a_name, b_name = DHOW_LEGS[i % len(DHOW_LEGS)]
    a, b = harbour_mooring(a_name), harbour_mooring(b_name)
    v = V(world, key)
    t = week(1, hours=(i % 28) * 12 + rng.uniform(0, 10))
    pos, target = a, b
    for _ in range(6):
        if _room(world, t) < 20.0:
            return
        legs = [Leg("transit", target=target, speed_kn=rng.uniform(6.0, 8.0)),
                Leg("station", duration_h=rng.uniform(3.0, 10.0),
                    radius_m=400.0)]
        pts = generate_track(v, VoyagePlan(start=pos, start_time=t,
                                           legs=legs), rng)
        if not pts:
            return
        emit_fleet(world, key, pts, rng)
        pos, target = target, pos
        t = pts[-1].t + hours(rng.uniform(2, 9))


# ==========================================================================
# the entry point
# ==========================================================================

#: archetype key -> the generator that moves it.
GENERATORS = {
    "box": lambda w, k, r, i: _liner(w, k, r, i, BOX_ROUTES),
    "feeder": lambda w, k, r, i: _liner(w, k, r, i, FEEDER_ROUTES,
                                        wait=(1.0, 8.0), berth=(16.0, 44.0)),
    "product": lambda w, k, r, i: _liner(w, k, r, i, PRODUCT_ROUTES,
                                         wait=(2.0, 14.0), berth=(20.0, 52.0)),
    "aframax": lambda w, k, r, i: _crude_entry(w, k, r, i, CRUDE_TERMINALS),
    "vlcc": lambda w, k, r, i: _crude_entry(w, k, r, i, CRUDE_TERMINALS),
    "bulker": lambda w, k, r, i: _bulk_waiter(w, k, r, i, BULK_TERMINALS),
    "reefer": lambda w, k, r, i: _liner(w, k, r, i, REEFER_ROUTES,
                                        wait=(0.3, 3.0), berth=(8.0, 20.0)),
    "trawler": _trawler,
    "tug": _tug,
    "osv": _osv,
    "ferry": _ferry,
    "dhow": _dhow,
}


def fleet_traffic(world: ScenarioWorld) -> None:
    """Move every hull in the wider fleet, each in her own archetype's manner.

    One RNG per archetype, derived from the fleet stream. That is not
    decoration: it means adding, removing or resizing one archetype cannot
    change the motion of any other, so a corpus can grow a trade without every
    trawler in it being re-rolled — the same additivity argument the module
    docstring makes, one level down.
    """
    for n, a in enumerate(ARCHETYPES):
        rng = fleet_rng(world, salt=11 + n)
        gen = GENERATORS[a.key]
        for i in range(a.count):
            gen(world, a.hull_key(i), rng, i)


SCENARIOS = (fleet_traffic,)
