"""Background traffic — the world the scenarios happen in.

No truth row, because nothing here is a scenario. These are twelve vessels going
about ordinary business on the real lanes: Gulf crude into Sikka and Vadinar,
product runs down the west coast, bulk out of Kandla, a reefer working Kochi.

**Background is not filler.** Three things depend on it:

  * A precision figure needs a denominator. If the only vessels in the corpus
    were scenario participants, every alert would land on someone interesting
    and precision would be flattered by construction.
  * The graph needs ordinary structure, so that a shared port or a shared flag
    is visibly uninformative. The real graph is star-shaped for exactly this
    reason and the synthetic one must be too, or traversal will look far more
    discriminating than it is.
  * Ports need queues. A vessel that is alone at an anchorage is not in a
    congested port, and the congestion decoy needs company to be plausible.

They are generated at the same fidelity as everything else — same integrator,
same emitter, same noise — because a background population that was cheap to
tell apart would make the whole corpus separable on population membership.
"""
from __future__ import annotations

from ..geography import NW_ENTRY, PORTS
from ..primitives.port_call import build_port_call
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..world import ScenarioWorld, week
from .common import V, add_loiter, add_port_visit, emit, hours

#: key -> (origin port or entry, list of ports to work, first week)
ROUTES: dict[str, tuple] = {
    "bg_1": (None, ["Sikka", "Vadinar"], 1),
    "bg_2": (None, ["Vadinar"], 2),
    "bg_3": ("Mumbai", ["Mangalore", "Kochi"], 1),
    "bg_4": ("Mundra", ["Kochi"], 3),
    "bg_5": ("Kandla", ["JNPT", "Mangalore"], 2),
    "bg_6": ("Mangalore", ["Mumbai", "JNPT"], 4),
    "bg_7": ("Mangalore", ["JNPT"], 5),
    "bg_8": ("Kochi", ["Mumbai"], 6),
    "bg_9": ("Karachi", ["Mumbai", "JNPT"], 3),
    "bg_10": (None, ["Vadinar", "Kandla"], 5),
}

#: Coastal fishing, which is what most AIS-visible small craft in this AOI are.
FISHERS = ("bg_11", "bg_12")

#: Shortest berth worth generating. Below this a call is not a call, and the
#: right move is to drop it and say so rather than to emit a two-hour berth
#: nobody would believe.
MIN_BERTH_HOURS = 4.0


def background_traffic(world: ScenarioWorld) -> None:
    r = world.rng

    for key, (origin, ports, first_week) in ROUTES.items():
        v = V(world, key)
        pos = PORTS[origin] if origin else NW_ENTRY
        t = week(first_week, hours=r.uniform(2, 40))
        for port in ports:
            # **Bound the call by what is left of the window.** The measured
            # dwell distribution reaches 336 h (the truncated tail of GFW's
            # port-visit durations), so a call started three weeks before the
            # end can run past it — which raised on seed 8 while seed 7 happened
            # to fit. A scenario corpus that only works at one seed is not
            # reproducible, and the window check would have caught it as an
            # error rather than as the scheduling problem it is.
            remaining_h = (world.t1 - t).total_seconds() / 3600.0
            if remaining_h < 30.0:
                break
            wait = min(world.profile.sample("anchorage_wait_hours", r) * 0.25,
                       max(remaining_h * 0.15, 1.0))
            dwell = min(world.profile.sample("port_call_dwell_hours", r),
                        max(remaining_h - wait - 24.0, 2.0))
            pts, spec = build_port_call(
                v, port, arrive_from=pos, t_start=t, rng=r,
                anchorage_hours=wait, berth_hours=dwell)

            # **And bound it by the passage as well, which the budget above
            # does not see.** `remaining_h` is measured from the moment she
            # *sails*, and the call does not begin until she arrives — bg_8
            # leaves Kochi for Mumbai, which is two and a half days of steaming
            # the arithmetic never subtracted. At seed 9 that put her departure
            # 11.3 hours past the end of the corpus window and the generator
            # refused the whole run. Seeds 7 and 8 fitted by luck.
            #
            # The passage length cannot be known before the call is built —
            # `_route_legs_around_land` may lengthen it around a headland — so
            # it is measured afterwards and the berth is shortened to fit. A
            # shorter call is still a call; a call that ends after the window
            # is a corpus that only generates at some seeds.
            overrun_h = (pts[-1].t - (world.t1 - hours(2))).total_seconds() / 3600.0
            if overrun_h > 0.0:
                dwell -= overrun_h
                if dwell < MIN_BERTH_HOURS:
                    world.clipped.append(
                        f"BG {key}: call at {port} dropped — the passage there "
                        f"leaves {dwell + overrun_h:.0f} h of window, under the "
                        f"{MIN_BERTH_HOURS:.0f} h a berth needs")
                    break
                pts, spec = build_port_call(
                    v, port, arrive_from=pos, t_start=t, rng=r,
                    anchorage_hours=wait, berth_hours=dwell)

            emit(world, key, pts)
            add_port_visit(world, "BG", key, spec)
            pos = (pts[-1].lat, pts[-1].lon)
            t = pts[-1].t + hours(r.uniform(3, 20))
            if t > world.t1 - hours(40):
                break

    for key in FISHERS:
        v = V(world, key)
        ground = (20.4 + r.uniform(-0.5, 0.5), 69.1 + r.uniform(-0.6, 0.6))
        t = week(1, hours=r.uniform(4, 30))
        while t < world.t1 - hours(80):
            pts = generate_track(v, VoyagePlan(
                start=PORTS["Sikka"], start_time=t,
                legs=[
                    Leg("transit", target=ground, speed_kn=v.service_kn),
                    Leg("fishing", target=ground, duration_h=r.uniform(20, 40),
                        radius_m=12000.0, speed_kn=r.uniform(2.6, 3.6)),
                    Leg("transit", target=PORTS["Sikka"], speed_kn=v.service_kn),
                ]), r)
            emit(world, key, pts)
            add_loiter(world, "BG", key, t + hours(6), t + hours(26),
                       ground[0], ground[1], mean_sog_kn=3.0)
            t = pts[-1].t + hours(r.uniform(20, 60))


SCENARIOS = (background_traffic,)
