"""Group W — what the wider fleet does wrong, and what only looks wrong.

The archetypes in `fleet_traffic.py` opened four new trades in the corpus:
container liners, harbour tugs, offshore supply vessels, ferries, and a great
many more trawlers and dhows. Each of those trades has its own way of going
wrong, and none of them was represented — the existing catalogue is built almost
entirely on tankers and cargo ships, because those are the hulls a sanctions
story is about.

**The base rate, and the argument for it.** Ten of the 421 hulls this fleet adds
carry a staged anomaly: **2.4%**. That number was chosen, not arrived at, and
ADR-004 is the reason.

Precision before recall is a stated product policy: of every ten alerts, seven
must survive human review. A corpus in which a third of the fleet is doing
something wrong makes that target trivially achievable and completely
meaningless — a detector that fired on every hull with an odd-looking hour would
post a respectable precision figure, and it would collapse the first time it met
an ocean. The measurement only means something when the *denominator* is
overwhelmingly boring, which is the same argument `background.py` and
`commercial.py` already make and the reason this module is small.

2.4% is still an order of magnitude above reality — the share of vessels in this
AOI doing something alert-worthy in any eight weeks is well under a tenth of a
percent — and that is a deliberate compromise, stated rather than hidden: below
about ten positives a recall figure is an anecdote. What the addition does do is
move the *corpus-wide* figure in the right direction: it takes the fraction of
hulls carrying a true anomaly from 21.3% to 9.5%, so every precision number
measured after this change is measured against a harder picture than every
number measured before it. Nothing here makes the demo look better. That is the
point.

**Every anomaly is paired with a decoy that shares its surface.** Seventeen of
the new hulls are decoys, so the group runs at 1.7 innocent look-alikes per
guilty hull. A group of positives measures recall and says nothing about
precision, and precision is the binding constraint. The pairings:

  W1 unlicensed pair trawling      <-> WD1 a licensed pair, identical pattern
  W2 catch transferred at sea      <-> WD2 the same transfer, alongside a berth
  W3 a tug that goes dark on a tow <-> WD3 a tug with a failing transponder
  W4 a ferry that leaves her run   <-> WD4 a ferry held off a busy terminal
  W5 an OSV meeting a dhow at sea  <-> WD5 an OSV on contracted platform standby
  W6 dark inside good reception    <-> WD6 silent inside the coverage hole
  W7 two dhows meeting at night    <-> WD7 three dhows fishing inshore together
                                   <-> WD8 two liners on an identical schedule
                                   <-> WD9 three bulkers in the lawful queue

**Everything draws from the fleet's derived RNG**, never `world.rng`, so this
module can grow without re-rolling a single hull that existed before it.
"""
from __future__ import annotations

from ..fleet import fleet_rng
from ..geography import (BOMBAY_HIGH, PORTS, haversine_m,
                         initial_bearing_deg, receiver_coverage)
from ..primitives.encounter import build_rendezvous, coherent
from ..primitives.gap import (EQUIPMENT_FAILURE, INTENTIONAL, OUT_OF_COVERAGE,
                              build_gap, plausible_placement)
from ..primitives.port_call import anchorage_of, build_port_call
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..searoute import nearest_water, seaward_point
from ..truth import (DECOY, FAMILY_BEHAVIOURAL, FAMILY_DARK_TRANSFER,
                     FAMILY_DECOY, TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import (V, add_encounter, add_gap_event, add_loiter,
                     add_port_visit, hours, schedule_arrival)
from .fleet_traffic import emit_fleet, harbour_mooring

# --------------------------------------------------------------------------
# places these scenarios use
# --------------------------------------------------------------------------

#: A ground close inshore off Saurashtra that the trawl pair has no licence for.
#: Inside the territorial sea and inside good terrestrial reception, which is
#: what makes the finding assertable at all — an unlicensed boat 200 nm out
#: would be a coverage question, not a fishing one (CLAUDE.md §6).
CLOSED_GROUND = (21.30, 69.05)

#: An ordinary, permitted ground for the decoy pair, far enough away that the
#: two scenarios cannot be confused for one and near enough that a rule keyed on
#: "is fishing" fires on both.
LICENSED_GROUND = (20.25, 68.55)

#: Open water off the Konkan where the OSV meets the dhow — nowhere near an
#: installation, which is the whole of W5's argument.
OPEN_KONKAN = (17.20, 71.30)

#: The Mumbai-Mangalore reception hole. Both stations are 300 km sets and this
#: sits beyond both, so a silence here is unhearable rather than intentional.
#: The value is asserted here and *checked* by `wd6`, which refuses to build if
#: the coverage model ever says otherwise.
COVERAGE_HOLE = (15.60, 72.90)

#: Where two dhows meet after dark, twenty-odd miles off the Gujarat coast.
DHOW_MEET = (21.70, 68.60)

#: The inshore patch WD7's three dhows work, in the Gulf of Kutch.
#:
#: Chosen by searching the gulf for a position with five kilometres of open
#: water all round it, because the scenario needs one: three boats offset from
#: each other wander a 3.5 km radius for fourteen hours, so a centre that is
#: merely *at* sea puts a third of their positions ashore. The first value here
#: was (22.05, 69.30), which is dry land in Saurashtra — `nearest_water` moved
#: the centre to the coast and the boats then fished across the beach, which is
#: what the afloat validator found. The gulf is narrow and full of islands and
#: there are only a few such patches; this is the nearest one to Sikka, which
#: is also what makes it the right one, since the boats are homed there.
INSHORE_DHOW_GROUND = (22.55, 69.75)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _rendezvous(world, rng, a_key, b_key, *, meet, planned, duration_h,
                separation_m, approach_a=250.0, approach_b=70.0,
                approach_nm=40.0, underway=False):
    """A meeting between two fleet hulls, scheduled around both calendars.

    `build_rendezvous` decides for itself where each hull opens her approach and
    when, from the meet time. That is the right shape for a scenario placing two
    idle vessels, and the wrong shape for two that have already been somewhere
    this window: the opening position is fixed and the opening *time* is
    derived, so a hull who was 300 nm away an hour earlier is asked to teleport
    into it, and `world.add_track` refuses. So the opening positions are
    computed here first, both calendars are consulted, and the meet time is
    pushed out until each hull could actually have sailed there.
    """
    va, vb = V(world, a_key), V(world, b_key)
    start_a = seaward_point(meet, approach_a, approach_nm * 1852.0)
    start_b = seaward_point(meet, approach_b, approach_nm * 1852.0)
    lead_a = approach_nm / max(va.service_kn, 1.0) * 1.35
    lead_b = approach_nm / max(vb.service_kn, 1.0) * 1.35
    open_at = max(
        schedule_arrival(world, a_key, start_a, planned - hours(lead_a)),
        schedule_arrival(world, b_key, start_b, planned - hours(lead_b)))
    t_meet = open_at + hours(max(lead_a, lead_b))

    pts_a, pts_b, spec = build_rendezvous(
        va, vb, meet_point=meet, t_meet=t_meet, duration_h=duration_h,
        separation_m=separation_m, rng=rng,
        approach_from_a=approach_a, approach_from_b=approach_b,
        approach_nm=approach_nm, depart_nm=25.0, underway=underway)
    problems = coherent(spec, va, vb)
    if problems:
        raise AssertionError(f"group W rendezvous incoherent: {problems}")
    return pts_a, pts_b, spec


def _fishing_trip(world, key, rng, *, home, ground, t_start, work_h,
                  radius_m=9000.0, speed=(2.5, 3.8)):
    """Out, work, home — the same primitive shape `fleet_traffic` uses.

    Deliberately the same call. If the IUU pair were generated by a different
    code path from the ordinary trawlers, a detector could separate them on the
    generator rather than on the behaviour, and the precision figure would
    measure this file (`test_decoys_are_not_trivially_separable`).
    """
    v = V(world, key)
    pts = generate_track(v, VoyagePlan(
        start=home, start_time=t_start,
        legs=[Leg("transit", target=ground, speed_kn=v.service_kn),
              Leg("fishing", target=ground, duration_h=work_h,
                  radius_m=radius_m, speed_kn=rng.uniform(*speed)),
              Leg("transit", target=home, speed_kn=v.service_kn)]), rng)
    emit_fleet(world, key, pts, rng)
    return pts


def _work_window(world, key, t_start, home, ground, work_h):
    """(start, end) of the working spell, allowing for the passage out."""
    v = V(world, key)
    out_h = haversine_m(*home, *ground) / 1852.0 / max(v.service_kn * 0.9, 1.0)
    return t_start + hours(out_h), t_start + hours(out_h + work_h * 0.9)


# ==========================================================================
# W1 / WD1 — pair trawling, once where it is not allowed and once where it is
# ==========================================================================

def w1_unlicensed_pair_trawl(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=101)
    home = PORTS["Sikka"]
    ground = nearest_water(CLOSED_GROUND)
    t0 = week(2, hours=9)
    work_h = 30.0

    for k, off in (("w_iuu_a", (0.006, 0.004)), ("w_iuu_b", (-0.006, -0.004))):
        g = (ground[0] + off[0], ground[1] + off[1])
        _fishing_trip(world, k, rng, home=home, ground=g, t_start=t0,
                      work_h=work_h, radius_m=6500.0)
        a, b = _work_window(world, k, t0, home, g, work_h)
        add_loiter(world, "W1", k, a, b, g[0], g[1],
                   mean_sog_kn=rng.uniform(2.5, 3.4))

    a, b = _work_window(world, "w_iuu_a", t0, home, ground, work_h)
    world.truth.add(ScenarioTruth(
        scenario_id="W1", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[V(world, "w_iuu_a").entity_id,
                    V(world, "w_iuu_b").entity_id],
        t_start=a, t_end=b, expected_detection=True,
        expected_anomaly_types=["loitering", "vessel_interaction"],
        notes=(f"Two trawlers work in company for {work_h:.0f} h on a ground "
               f"inside the territorial sea at {ground[0]:.2f}N "
               f"{ground[1]:.2f}E, roughly 700 m apart throughout. The pair "
               f"holding station on each other is what distinguishes this from "
               f"a single boat fishing; the position is what makes the claim "
               f"assertable, because reception here is good and the silence "
               f"argument (CLAUDE.md §6) does not apply.")))


def wd1_licensed_pair_trawl(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=102)
    home = PORTS["Sikka"]
    ground = nearest_water(LICENSED_GROUND)
    t0 = week(3, hours=15)
    work_h = 32.0
    keys = ("wd_licensed_a", "wd_licensed_b", "wd_licensed_c")

    for n, k in enumerate(keys):
        g = (ground[0] + 0.008 * (n - 1), ground[1] + 0.006 * (n - 1))
        _fishing_trip(world, k, rng, home=home, ground=g, t_start=t0,
                      work_h=work_h, radius_m=6500.0)
        a, b = _work_window(world, k, t0, home, g, work_h)
        add_loiter(world, "W D1", k, a, b, g[0], g[1],
                   mean_sog_kn=rng.uniform(2.5, 3.4))

    a, b = _work_window(world, keys[0], t0, home, ground, work_h)
    world.truth.add(ScenarioTruth(
        scenario_id="WD1", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[V(world, k).entity_id for k in keys],
        t_start=a, t_end=b, expected_detection=False,
        notes=("Three licensed trawlers work one ground in company for 32 h, "
               "built by the same call, at the same speeds and separations, as "
               "W1. Nothing in the motion tells them apart — the only "
               "difference is which patch of water they are on, and a system "
               "that flags this is flagging the fishing industry.")))


# ==========================================================================
# W2 / WD2 — the catch changes hands
# ==========================================================================

def w2_catch_transferred_at_sea(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=103)
    meet = nearest_water((20.10, 68.20))
    pts_r, pts_t, spec = _rendezvous(
        world, rng, "w_catch_carrier", "w_iuu_b", meet=meet,
        planned=week(4, hours=20), duration_h=rng.uniform(4.0, 7.0),
        separation_m=world.profile.sample("encounter_separation_m", rng),
        approach_a=200.0, approach_b=35.0, approach_nm=35.0)
    emit_fleet(world, "w_catch_carrier", pts_r, rng)
    emit_fleet(world, "w_iuu_b", pts_t, rng)
    add_encounter(world, "W2", "w_catch_carrier", "w_iuu_b", spec,
                  encounter_type="transfer")

    world.truth.add(ScenarioTruth(
        scenario_id="W2", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[V(world, "w_catch_carrier").entity_id,
                    V(world, "w_iuu_b").entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=True,
        expected_anomaly_types=["encounter", "vessel_interaction"],
        notes=(f"A reefer lies alongside one of the W1 trawlers for "
               f"{spec.duration_h:.1f} h in open water at "
               f"{spec.mean_separation_m:.0f} m, then carries the catch north. "
               f"The link back to W1 is the same hull, which is what makes this "
               f"a chain rather than two unrelated meetings — and the graph is "
               f"where that becomes visible.")))


def wd2_transhipment_alongside(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=104)
    meet = anchorage_of("Kochi")
    pts_r, pts_t, spec = _rendezvous(
        world, rng, "wd_port_reefer", "wd_port_trawler", meet=meet,
        planned=week(4, hours=8), duration_h=rng.uniform(5.0, 9.0),
        separation_m=world.profile.sample("encounter_separation_m", rng),
        approach_a=250.0, approach_b=185.0, approach_nm=25.0)
    emit_fleet(world, "wd_port_reefer", pts_r, rng)
    emit_fleet(world, "wd_port_trawler", pts_t, rng)
    add_encounter(world, "WD2", "wd_port_reefer", "wd_port_trawler", spec,
                  encounter_type="transfer")

    world.truth.add(ScenarioTruth(
        scenario_id="WD2", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[V(world, "wd_port_reefer").entity_id,
                    V(world, "wd_port_trawler").entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=False,
        notes=("The same transfer as W2, from the same primitive, at the "
               "designated Kochi anchorage instead of open water. Landing fish "
               "into a reefer is the ordinary business of that anchorage; the "
               "discriminating fact is distance from port, and nothing else.")))


# ==========================================================================
# W3 / WD3 — a tug that goes quiet, and a tug whose set is failing
# ==========================================================================

def w3_tug_goes_dark_on_a_tow(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=105)
    v = V(world, "w_dark_tug")
    base = harbour_mooring("Mundra")
    away = nearest_water(seaward_point(base, 235.0, 34.0 * 1852.0))
    t0 = week(5, hours=4)
    pts = generate_track(v, VoyagePlan(
        start=base, start_time=t0,
        legs=[Leg("transit", target=away, speed_kn=6.5),
              Leg("station", duration_h=3.0, radius_m=600.0),
              Leg("transit", target=base, speed_kn=6.5)]), rng)
    g0 = pts[0].t + hours(3.0)
    g1 = g0 + hours(7.5)
    gap = build_gap(pts, g0, g1, cause=INTENTIONAL)
    bad = plausible_placement(gap, expect_intentional_verdict=True)
    if bad:
        raise AssertionError(f"W3 gap is not assertable as intentional: {bad}")
    emit_fleet(world, "w_dark_tug", pts, rng, suppressions=[gap.suppression()])
    add_gap_event(world, "W3", "w_dark_tug", gap)

    world.truth.add(ScenarioTruth(
        scenario_id="W3", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=gap.t0, t_end=gap.t1, expected_detection=True,
        expected_anomaly_types=["ais_gap", "dark_vessel"],
        notes=(f"A harbour tug on a tow out of Mundra stops transmitting for "
               f"{gap.duration_h:.1f} h at "
               f"{gap.coverage_at_off:.2f} modelled reception — well inside "
               f"cover — and comes back on 6 nm from where she went off. A tug "
               f"is not a hull anyone watches, which is the point of using "
               f"one.")))


def wd3_tug_transponder_failure(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=106)
    v = V(world, "wd_faulty_tug")
    base = harbour_mooring("JNPT")
    t0 = week(5, hours=11)
    away = nearest_water(seaward_point(base, 250.0, 9.0 * 1852.0))
    pts = generate_track(v, VoyagePlan(
        start=base, start_time=t0,
        legs=[Leg("transit", target=away, speed_kn=9.0),
              Leg("station", duration_h=2.0, radius_m=400.0),
              Leg("transit", target=base, speed_kn=8.0),
              Leg("moored", duration_h=14.0)]), rng)
    outages = []
    t = t0 + hours(0.8)
    while t < pts[-1].t - hours(2.0):
        dur = hours(rng.uniform(0.3, 1.4))
        outages.append(build_gap(pts, t, t + dur, cause=EQUIPMENT_FAILURE))
        t = t + dur + hours(rng.uniform(0.6, 2.2))
    emit_fleet(world, "wd_faulty_tug", pts, rng,
               suppressions=[g.suppression() for g in outages])
    for g in outages:
        add_gap_event(world, "WD3", "wd_faulty_tug", g)

    world.truth.add(ScenarioTruth(
        scenario_id="WD3", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=outages[0].t0, t_end=outages[-1].t1,
        expected_detection=False,
        notes=(f"The same tug archetype in the same waters with "
               f"{len(outages)} short dropouts instead of one long one, and a "
               f"maintenance lay-up at the end. A failing set drops out "
               f"repeatedly and comes back; a switch goes off once. Only the "
               f"pattern separates them.")))


# ==========================================================================
# W4 / WD4 — a ferry that leaves her run, and one that is simply waiting
# ==========================================================================

def w4_ferry_leaves_her_run(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=107)
    v = V(world, "w_stray_ferry")
    a, b = harbour_mooring("Mumbai"), harbour_mooring("JNPT")
    t = week(6, hours=6)
    pos = a
    for _ in range(3):
        pts = generate_track(v, VoyagePlan(
            start=pos, start_time=t,
            legs=[Leg("transit", target=b if pos is a else a, speed_kn=15.5),
                  Leg("moored", duration_h=1.5)]), rng)
        emit_fleet(world, "w_stray_ferry", pts, rng)
        pos = b if pos is a else a
        t = pts[-1].t + hours(1.0)

    offshore = nearest_water(seaward_point(a, 250.0, 32.0 * 1852.0))
    stray = generate_track(v, VoyagePlan(
        start=pos, start_time=t + hours(2.0),
        legs=[Leg("transit", target=offshore, speed_kn=15.0),
              Leg("station", duration_h=4.5, radius_m=700.0),
              Leg("transit", target=pos, speed_kn=15.0)]), rng)
    emit_fleet(world, "w_stray_ferry", stray, rng)
    t_hold = stray[0].t + hours(
        haversine_m(*pos, *offshore) / 1852.0 / max(v.service_kn * 0.9, 1.0))
    add_loiter(world, "W4", "w_stray_ferry", t_hold, t_hold + hours(4.0),
               offshore[0], offshore[1], mean_sog_kn=rng.uniform(0.3, 1.0))

    world.truth.add(ScenarioTruth(
        scenario_id="W4", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t_hold, t_end=t_hold + hours(4.0), expected_detection=True,
        expected_anomaly_types=["loitering", "route_deviation"],
        notes=("A passenger ferry that has crossed the same 13-mile harbour "
               "leg three times leaves it, runs 32 nm offshore and stops for "
               "four and a half hours. The finding is not the stop — plenty of "
               "hulls stop — it is that this hull has never been there and her "
               "own three previous crossings are the baseline that says so.")))


def wd4_ferry_held_off_the_berth(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=108)
    v = V(world, "wd_held_ferry")
    a, b = harbour_mooring("Mumbai"), harbour_mooring("JNPT")
    t = week(6, hours=3)
    hold = nearest_water(seaward_point(b, 210.0, 3.5 * 1852.0))
    pts = generate_track(v, VoyagePlan(
        start=a, start_time=t,
        legs=[Leg("transit", target=hold, speed_kn=15.0),
              Leg("station", duration_h=3.4, radius_m=600.0),
              Leg("transit", target=b, speed_kn=8.0),
              Leg("moored", duration_h=6.0),
              Leg("transit", target=a, speed_kn=15.0)]), rng)
    emit_fleet(world, "wd_held_ferry", pts, rng)
    t_hold = pts[0].t + hours(
        haversine_m(*a, *hold) / 1852.0 / max(v.service_kn * 0.9, 1.0))
    add_loiter(world, "WD4", "wd_held_ferry", t_hold, t_hold + hours(3.0),
               hold[0], hold[1], mean_sog_kn=rng.uniform(0.3, 1.0))

    world.truth.add(ScenarioTruth(
        scenario_id="WD4", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t_hold, t_end=t_hold + hours(3.0),
        expected_detection=False,
        notes=("The same class, the same run and a stop of nearly the same "
               "length as W4 — three and a half miles off her own terminal, "
               "waiting for the berth to clear. Duration does not separate "
               "these two. Distance from the port she is booked into does.")))


# ==========================================================================
# W5 / WD5 — an offshore supply vessel where she has no business being
# ==========================================================================

def w5_osv_meets_a_dhow(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=109)
    meet = nearest_water(OPEN_KONKAN)
    pts_o, pts_d, spec = _rendezvous(
        world, rng, "w_osv_rogue", "w_dhow_runner", meet=meet,
        planned=week(5, hours=30), duration_h=rng.uniform(3.0, 5.5),
        separation_m=world.profile.sample("encounter_separation_m", rng),
        approach_a=30.0, approach_b=210.0, approach_nm=30.0)
    emit_fleet(world, "w_osv_rogue", pts_o, rng)
    emit_fleet(world, "w_dhow_runner", pts_d, rng)
    add_encounter(world, "W5", "w_osv_rogue", "w_dhow_runner", spec,
                  encounter_type="transfer")

    km = haversine_m(*meet, *BOMBAY_HIGH) / 1000.0
    world.truth.add(ScenarioTruth(
        scenario_id="W5", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[V(world, "w_osv_rogue").entity_id,
                    V(world, "w_dhow_runner").entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=True,
        expected_anomaly_types=["encounter", "vessel_interaction"],
        notes=(f"A platform supply vessel lies alongside a 22 m dhow for "
               f"{spec.duration_h:.1f} h in open water {km:.0f} km from the "
               f"nearest installation she could plausibly be serving. An OSV "
               f"stopped offshore is her normal day; an OSV stopped offshore "
               f"next to a dhow, nowhere near a platform, is not — and telling "
               f"those apart needs the platform positions, which is why WD5 "
               f"exists.")))


def wd5_osv_platform_standby(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=110)
    v = V(world, "wd_standby_osv")
    base = PORTS["Mumbai"]
    field = nearest_water((BOMBAY_HIGH[0] + 0.08, BOMBAY_HIGH[1] - 0.10))
    t = week(5, hours=12)
    stand_h = 26.0
    pts = generate_track(v, VoyagePlan(
        start=base, start_time=t,
        legs=[Leg("transit", target=field, speed_kn=11.5),
              Leg("station", duration_h=stand_h, radius_m=450.0),
              Leg("transit", target=base, speed_kn=11.5)]), rng)
    emit_fleet(world, "wd_standby_osv", pts, rng)
    t0 = t + hours(haversine_m(*base, *field) / 1852.0
                   / max(v.service_kn * 0.9, 1.0))
    add_loiter(world, "WD5", "wd_standby_osv", t0, t0 + hours(stand_h * 0.9),
               field[0], field[1], mean_sog_kn=rng.uniform(0.2, 0.9))

    world.truth.add(ScenarioTruth(
        scenario_id="WD5", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=t0 + hours(stand_h * 0.9),
        expected_detection=False,
        notes=(f"Twenty-six hours stopped in open water — five times longer "
               f"than W5's meeting — 10 km from the Bombay High cluster she is "
               f"contracted to. A loitering rule keyed on duration flags this "
               f"and misses W5, which is exactly the wrong way round.")))


# ==========================================================================
# W6 / WD6 — silence you can assert, and silence you cannot
# ==========================================================================

def w6_dark_inside_reception(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=111)
    v = V(world, "w_coast_dark")
    t0 = week(4, hours=5)
    pts = generate_track(v, VoyagePlan(
        start=PORTS["Mumbai"], start_time=t0,
        legs=[Leg("transit", target=PORTS["Sikka"],
                  speed_kn=v.service_kn)]), rng)
    g0 = pts[0].t + hours(6.0)
    g1 = g0 + hours(9.0)
    gap = build_gap(pts, g0, g1, cause=INTENTIONAL)
    bad = plausible_placement(gap, expect_intentional_verdict=True)
    if bad:
        raise AssertionError(f"W6 gap is not assertable as intentional: {bad}")
    emit_fleet(world, "w_coast_dark", pts, rng,
               suppressions=[gap.suppression()])
    add_gap_event(world, "W6", "w_coast_dark", gap)

    world.truth.add(ScenarioTruth(
        scenario_id="W6", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=gap.t0, t_end=gap.t1, expected_detection=True,
        expected_anomaly_types=["ais_gap", "dark_vessel"],
        notes=(f"A product tanker on a coastal passage stops transmitting for "
               f"{gap.duration_h:.1f} h at {gap.coverage_at_off:.2f} modelled "
               f"reception and reappears "
               f"{haversine_m(gap.lat_off, gap.lon_off, gap.lat_on, gap.lon_on) / 1852.0:.0f} "
               f"nm along her track. Inside demonstrated cover, so the silence "
               f"is a fact about the vessel and not about the receiver.")))


def wd6_silent_in_the_coverage_hole(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=112)
    cov = receiver_coverage(*COVERAGE_HOLE)
    if cov > 0.05:
        raise AssertionError(
            f"WD6 assumes {COVERAGE_HOLE} is outside terrestrial reception, "
            f"but the coverage model now gives {cov:.3f} there. The decoy's "
            f"entire content is that the silence is unhearable; move the point "
            f"or drop the scenario rather than letting it quietly become a "
            f"true positive.")
    v = V(world, "wd_hole_tanker")
    t0 = week(4, hours=14)
    hole = nearest_water(COVERAGE_HOLE)
    pts = generate_track(v, VoyagePlan(
        start=PORTS["Mangalore"], start_time=t0,
        legs=[Leg("transit", target=hole, speed_kn=v.service_kn),
              Leg("transit", target=PORTS["Mumbai"],
                  speed_kn=v.service_kn)]), rng)
    # She is not suppressed at all: nothing is switched off. The silence is the
    # emitter's own coverage model deciding nobody could hear her, which is
    # what makes this a decoy rather than a staged one.
    emit_fleet(world, "wd_hole_tanker", pts, rng)
    mid = pts[len(pts) // 2]
    gap = build_gap(pts, mid.t - hours(3.0), mid.t + hours(3.0),
                    cause=OUT_OF_COVERAGE)
    add_gap_event(world, "WD6", "wd_hole_tanker", gap)

    world.truth.add(ScenarioTruth(
        scenario_id="WD6", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=gap.t0, t_end=gap.t1,
        expected_detection=False,
        notes=(f"The same class of hull as W6, silent for the same sort of "
               f"span, at {gap.coverage_at_off:.3f} modelled reception in the "
               f"Mumbai-Mangalore hole. She never switched anything off. This "
               f"must resolve to `unknown` and never to `dark`: calling an "
               f"out-of-coverage gap intentional is a false positive by "
               f"construction (CLAUDE.md §6).")))


# ==========================================================================
# W7 / WD7 — small craft meeting at night, and small craft simply working
# ==========================================================================

def w7_dhows_meet_after_dark(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=113)
    meet = nearest_water(DHOW_MEET)
    pts_a, pts_b, spec = _rendezvous(
        world, rng, "w_dhow_meet_a", "w_dhow_meet_b", meet=meet,
        planned=week(6, hours=21), duration_h=rng.uniform(2.5, 4.0),
        separation_m=world.profile.sample("encounter_separation_m", rng) * 0.6,
        approach_a=20.0, approach_b=200.0, approach_nm=20.0)
    emit_fleet(world, "w_dhow_meet_a", pts_a, rng)
    emit_fleet(world, "w_dhow_meet_b", pts_b, rng)
    add_encounter(world, "W7", "w_dhow_meet_a", "w_dhow_meet_b", spec,
                  encounter_type="transfer")

    world.truth.add(ScenarioTruth(
        scenario_id="W7", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[V(world, "w_dhow_meet_a").entity_id,
                    V(world, "w_dhow_meet_b").entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=True,
        expected_anomaly_types=["encounter", "vessel_interaction"],
        notes=(f"Two dhows lie alongside for {spec.duration_h:.1f} h "
               f"{haversine_m(*meet, *PORTS['Sikka']) / 1852.0:.0f} nm off "
               f"Saurashtra in the small hours. Both are above the SAR size "
               f"floor only marginally, so this is a case AIS can see and "
               f"imagery may not — the opposite of M1, and worth having both.")))


def wd7_dhows_fishing_inshore(world: ScenarioWorld) -> None:
    rng = fleet_rng(world, salt=114)
    keys = ("wd_inshore_dhow_a", "wd_inshore_dhow_b", "wd_inshore_dhow_c")
    home = PORTS["Sikka"]
    ground = nearest_water(INSHORE_DHOW_GROUND)
    t0 = week(6, hours=5)
    for n, k in enumerate(keys):
        g = (ground[0] + 0.010 * (n - 1), ground[1] + 0.008 * (n - 1))
        _fishing_trip(world, k, rng, home=home, ground=g, t_start=t0,
                      work_h=14.0, radius_m=3500.0, speed=(2.0, 3.2))
        a, b = _work_window(world, k, t0, home, g, 14.0)
        add_loiter(world, "WD7", k, a, b, g[0], g[1],
                   mean_sog_kn=rng.uniform(2.0, 3.0))

    a, b = _work_window(world, keys[0], t0, home, ground, 14.0)
    world.truth.add(ScenarioTruth(
        scenario_id="WD7", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[V(world, k).entity_id for k in keys],
        t_start=a, t_end=b, expected_detection=False,
        notes=("Three dhows work one inshore patch within a mile of each "
               "other for fourteen hours. Small craft in company at low speed "
               "is what the Gulf of Kutch looks like every day of the year, "
               "and W7's finding has to survive being told apart from it.")))


# ==========================================================================
# WD8 / WD9 — two more shapes a naive rule mistakes for coordination
# ==========================================================================

def wd8_liners_on_the_same_schedule(world: ScenarioWorld) -> None:
    """Two container ships that call at the same ports, on the same days.

    A pattern-of-life rule that treats "these two hulls keep turning up
    together" as association will fire on every liner service in the world.
    """
    rng = fleet_rng(world, salt=115)
    keys = ("wd_liner_a", "wd_liner_b")
    t0 = week(2, hours=6)
    first = last = None
    for n, k in enumerate(keys):
        pos = PORTS["Mundra"]
        t = t0 + hours(n * 1.5)
        for port in ("JNPT", "Mundra"):
            v = V(world, k)
            pts, spec = build_port_call(
                v, port, arrive_from=pos, t_start=t, rng=rng,
                anchorage_hours=rng.uniform(0.4, 1.8),
                berth_hours=rng.uniform(12.0, 18.0))
            emit_fleet(world, k, pts, rng)
            add_port_visit(world, "WD8", k, spec)
            pos = (pts[-1].lat, pts[-1].lon)
            t = pts[-1].t + hours(rng.uniform(8, 16))
            first = spec.t_arrive if first is None else first
            last = spec.t_depart

    world.truth.add(ScenarioTruth(
        scenario_id="WD8", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[V(world, k).entity_id for k in keys],
        t_start=first, t_end=last, expected_detection=False,
        notes=("Two container ships on the same liner rotation call at the "
               "same two berths within 36 hours of each other, twice. That is "
               "what a scheduled service is. Co-occurrence at a port is the "
               "single most abundant coincidence in the corpus and must not "
               "become an association edge on its own.")))


def wd9_bulkers_in_the_queue(world: ScenarioWorld) -> None:
    """Three bulkers waiting at the Kandla designated anchorage at once.

    Sitting still for two days beside two strangers is the anchorage queue, and
    a rendezvous rule keyed on proximity and low speed cannot be allowed to
    call it a meeting.
    """
    rng = fleet_rng(world, salt=116)
    keys = ("wd_queue_a", "wd_queue_b", "wd_queue_c")
    anch = anchorage_of("Kandla")
    t0 = week(3, hours=2)
    first = last = None
    for n, k in enumerate(keys):
        v = V(world, k)
        wait = 40.0 + n * 6.0
        pts, spec = build_port_call(
            v, "Kandla", arrive_from=PORTS["Mumbai"],
            t_start=t0 + hours(n * 3.0), rng=rng,
            anchorage_hours=wait, berth_hours=rng.uniform(20.0, 34.0))
        emit_fleet(world, k, pts, rng)
        add_port_visit(world, "WD9", k, spec)
        a = spec.t_arrive
        b = a + hours(wait * 0.9)
        add_loiter(world, "WD9", k, a, b, anch[0], anch[1],
                   mean_sog_kn=rng.uniform(0.2, 0.9))
        first = a if first is None else first
        last = b

    world.truth.add(ScenarioTruth(
        scenario_id="WD9", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[V(world, k).entity_id for k in keys],
        t_start=first, t_end=last, expected_detection=False,
        notes=("Three bulkers sit in the Kandla designated anchorage for forty "
               "hours or more, overlapping, within a couple of miles of one "
               "another. Every element a transfer detector keys on is present "
               "except the one that matters: they are inside a designated "
               "waiting area and none of them ever closes on another.")))


SCENARIOS = (
    w1_unlicensed_pair_trawl,
    wd1_licensed_pair_trawl,
    w2_catch_transferred_at_sea,
    wd2_transhipment_alongside,
    w3_tug_goes_dark_on_a_tow,
    wd3_tug_transponder_failure,
    w4_ferry_leaves_her_run,
    wd4_ferry_held_off_the_berth,
    w5_osv_meets_a_dhow,
    wd5_osv_platform_standby,
    w6_dark_inside_reception,
    wd6_silent_in_the_coverage_hole,
    w7_dhows_meet_after_dark,
    wd7_dhows_fishing_inshore,
    wd8_liners_on_the_same_schedule,
    wd9_bulkers_in_the_queue,
)
