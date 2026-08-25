"""Decoys — every one of these looks wrong and is right.

**If the system flags any of these, that is the finding.** These are not filler
traffic; they are the measurement. Precision is the fraction of alerts that
survive review, and the only way to measure it is to build things that *should*
draw an alert from a naive detector and must not draw one from a good one.

**Built at exactly the fidelity of the true positives.** The bunkering below is
constructed by the same `build_rendezvous` call, from the same measured
separation distribution, with the same emitter and the same noise, as the
illicit transfer in A1. That is enforced rather than intended: a test compares
the statistical properties of decoy and true-positive tracks — report intervals,
position noise, speed distributions — and fails if they are trivially separable.
Without it, a detector could learn "sloppier generation means innocent" and post
a precision figure that measured nothing but this file's craftsmanship.

The last one, `clean_neighbour`, is the most important item in the corpus.
"""
from __future__ import annotations

from ..searoute import nearest_water
from ..geography import (ANCHORAGES, PORTS, destination, point_on_cable,
                         FISHING_GROUND_GUJARAT)
from ..primitives.encounter import build_rendezvous, coherent, near_miss
from ..primitives.gap import (EQUIPMENT_FAILURE, OUT_OF_COVERAGE,
                              RECEIVER_SHADOW, build_gap, plausible_placement)
from ..primitives.port_call import build_port_call
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..truth import DECOY, FAMILY_DECOY, ScenarioTruth
from ..world import ScenarioWorld, week
from .common import (V, add_encounter, add_gap_event, add_loiter,
                     add_port_visit, coverage_at, emit, hours, schedule_after,
                     schedule_arrival)


# --------------------------------------------------------------------------
# DX1 — legitimate bunkering, geometrically identical to an STS transfer
# --------------------------------------------------------------------------

def dx1_legitimate_bunkering(world: ScenarioWorld) -> None:
    r = world.rng
    barge, client = V(world, "bunker_barge"), V(world, "bunker_client")
    t_meet = week(3, hours=52)
    anchorage = ANCHORAGES["Mundra"]

    pts_a, pts_b, spec = build_rendezvous(
        barge, client, meet_point=anchorage, t_meet=t_meet,
        duration_h=world.profile.sample("encounter_duration_hours", r),
        separation_m=world.profile.sample("encounter_separation_m", r),
        rng=r, approach_from_a=30.0, approach_from_b=200.0, approach_nm=18.0)
    problems = coherent(spec, barge, client)
    if problems:
        raise AssertionError(f"bunkering geometry incoherent: {problems}")

    emit(world, "bunker_barge", pts_a)
    emit(world, "bunker_client", pts_b)
    add_encounter(world, "DX1", "bunker_barge", "bunker_client", spec,
                  encounter_type="bunkering")

    world.truth.add(ScenarioTruth(
        scenario_id="DX1", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[barge.entity_id, client.entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=False,
        notes=(f"Bunker barge supplies a bulker at the Mundra designated "
               f"anchorage: {spec.duration_h:.1f} h alongside at "
               f"{spec.mean_separation_m:.0f} m mean separation. Built by the "
               f"same primitive call, from the same measured distributions, as "
               f"the A1 transfer — the geometry is identical and the only "
               f"differences are location (a designated anchorage, 0 km from "
               f"port) and the fact that both parties transmit throughout.")))


# --------------------------------------------------------------------------
# DX2 — genuine equipment failure
# --------------------------------------------------------------------------

def dx2_equipment_failure(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "faulty_txp")
    t0 = week(2, hours=20)

    pts = generate_track(v, VoyagePlan(
        start=PORTS["Mangalore"], start_time=t0,
        legs=[Leg("transit", target=PORTS["JNPT"], speed_kn=v.service_kn)]), r)

    # Intermittent, not clean: a failing unit drops out repeatedly for varying
    # spells and comes back, which is a different shape from a single decisive
    # switch-off. Both are absences; only the pattern differs.
    outages = []
    t = t0 + hours(5)
    while t < pts[-1].t - hours(3):
        dur = hours(r.uniform(0.4, 2.6))
        outages.append(build_gap(pts, t, t + dur, cause=EQUIPMENT_FAILURE))
        t = t + dur + hours(r.uniform(1.2, 4.0))

    emit(world, "faulty_txp", pts,
         suppressions=[g.suppression() for g in outages])
    for g in outages:
        add_gap_event(world, "DX2", "faulty_txp", g)

    # The repair: a maintenance call, after which the reporting is clean.
    last = pts[-1]
    call, spec = build_port_call(
        v, "JNPT", arrive_from=(last.lat, last.lon), t_start=last.t + hours(1),
        rng=r, anchorage_hours=3.0, berth_hours=34.0)
    emit(world, "faulty_txp", call)
    ev = add_port_visit(world, "DX2", "faulty_txp", spec)
    ev.props["call_purpose"] = "maintenance"

    onward = generate_track(v, VoyagePlan(
        start=(call[-1].lat, call[-1].lon), start_time=call[-1].t,
        # Continues from the port call, so it inherits her heading and speed.
        # Omitting these snapped her course to 000 and her speed to zero inside
        # one 60 s step — a 2.69 deg/s turn on a hull limited to 0.7.
        initial_course_deg=call[-1].cog_deg, initial_sog_kn=call[-1].sog_kn,
        legs=[Leg("transit", target=PORTS["Kandla"], speed_kn=v.service_kn)]), r)
    emit(world, "faulty_txp", onward)

    world.truth.add(ScenarioTruth(
        scenario_id="DX2", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t0, t_end=onward[-1].t, expected_detection=False,
        notes=(f"{len(outages)} irregular dropouts of 0.4-2.6 h over a single "
               f"passage, inside good coverage, followed by a 34 h maintenance "
               f"call after which reporting is continuous. The repair-consistent "
               f"pattern — many short ragged gaps, then a yard visit, then "
               f"silence stops — is what separates this from a deliberate "
               f"shutdown, and it is only visible across the whole voyage.")))


# --------------------------------------------------------------------------
# DX3 — receiver shadow (must resolve to unknown)
# --------------------------------------------------------------------------

def dx3_receiver_shadow(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "shadow_gap")
    t0 = week(4, hours=30)
    # Deliberately at the fringe of the modelled reception rings.
    fringe = (19.6, 66.9)

    pts = generate_track(v, VoyagePlan(
        start=(21.0, 68.4), start_time=t0,
        legs=[
            Leg("transit", target=fringe, speed_kn=v.service_kn),
            Leg("transit", target=PORTS["Mangalore"], speed_kn=v.service_kn),
        ]), r)

    g = build_gap(pts, t0 + hours(9), t0 + hours(20), cause=RECEIVER_SHADOW)
    # Emission is thinned by the coverage model itself; the gap is a consequence
    # of where she is, not an instruction.
    emit(world, "shadow_gap", pts, suppressions=[g.suppression()])
    add_gap_event(world, "DX3", "shadow_gap", g)

    world.truth.add(ScenarioTruth(
        scenario_id="DX3", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=g.t0, t_end=g.t1, expected_detection=False,
        notes=(f"An 11 h silence at {g.lat_off:.2f}N {g.lon_off:.2f}E, where "
               f"modelled reception is {g.coverage_at_off:.2f}. The honest "
               f"verdict is UNKNOWN — we cannot hear there, so we cannot say "
               f"the ship stopped transmitting. Asserting INTENTIONAL_SILENCE "
               f"here is a false positive by construction and is exactly what "
               f"CLAUDE.md's offshore rule forbids.")))


# --------------------------------------------------------------------------
# DX4 — berth congestion off Mundra
# --------------------------------------------------------------------------

def dx4_berth_congestion(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "congested")
    t0 = week(2, hours=6)
    anchorage = ANCHORAGES["Mundra"]

    legs = [Leg("transit", target=anchorage, speed_kn=v.service_kn)]
    # Slow circling for 40 h: the vessel is not holding one station, she is
    # working a holding pattern, which is what congestion actually looks like.
    for i in range(5):
        legs += [Leg("transit",
                     target=destination(*anchorage, i * 72.0, 5200.0),
                     speed_kn=4.2),
                 Leg("station", duration_h=3.0, radius_m=1500.0)]
    legs.append(Leg("transit", target=PORTS["Mundra"], speed_kn=5.0))
    legs.append(Leg("moored", duration_h=28.0))

    pts = generate_track(v, VoyagePlan(start=(21.4, 68.0), start_time=t0,
                                       legs=legs), r)
    emit(world, "congested", pts)
    add_loiter(world, "DX4", "congested", t0 + hours(8), t0 + hours(48),
               anchorage[0], anchorage[1], mean_sog_kn=2.4)

    world.truth.add(ScenarioTruth(
        scenario_id="DX4", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=False,
        notes=("40 h of slow circling in the Mundra anchorage, then a berth "
               "and 28 h alongside. The berth call is the disambiguator: a "
               "vessel that waits and then works cargo was queuing. "
               "Distance-from-port is ~0 throughout, which is the field that "
               "separates an anchorage queue from an open-water loiter and is "
               "carried on every real event row.")))


# --------------------------------------------------------------------------
# DX5 — routine ownership sale and reflagging
# --------------------------------------------------------------------------

def dx5_clean_sale(world: ScenarioWorld) -> None:
    from ..cast import ORG_CLEAN_A, ORG_CLEAN_B
    r = world.rng
    v = V(world, "clean_sale")
    t_sale = week(4, hours=12)
    old_flag = v.flag

    world.identity.change(v, t_sale, {"flag": "SGP"},
                          reason="DX5 — reflag on sale, fully documented")
    world.corporate.link("owned-by", v.entity_id, ORG_CLEAN_B,
                         t_sale, None, confidence=0.9,
                         notes="DX5 — documented sale")

    for i, (port, frm) in enumerate((("Kandla", "Karachi"),
                                     ("Kochi", "Kandla"),
                                     ("Mundra", "Kochi"))):
        pts, spec = build_port_call(
            v, port, arrive_from=PORTS[frm], t_start=week(3, hours=10 + i * 190),
            rng=r, anchorage_hours=6.0, berth_hours=27.0)
        emit(world, "clean_sale", pts)
        add_port_visit(world, "DX5", "clean_sale", spec)

    world.truth.add(ScenarioTruth(
        scenario_id="DX5", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t_sale, t_end=week(8), expected_detection=False,
        notes=(f"One reflagging, {old_flag} -> SGP, on a documented sale to a "
               f"clean operator, with no change in trading pattern before or "
               f"after — the same three-port rotation at the same speeds. "
               f"Compare B3, where four reflaggings in eight weeks *is* the "
               f"anomaly. The rate is what matters, and a detector that fires "
               f"on any reflagging fires on ordinary S&P activity.")))


# --------------------------------------------------------------------------
# DX6 — declared survey on the cable route
# --------------------------------------------------------------------------

def dx6_declared_survey(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "survey_declared")
    t0 = week(6, hours=16)
    stations = [point_on_cable(f) for f in (0.22, 0.48, 0.70)]

    legs = []
    for s in stations:
        legs += [Leg("transit", target=s, speed_kn=v.service_kn),
                 Leg("station", duration_h=8.0, radius_m=600.0)]
    legs.append(Leg("transit", target=PORTS["Mumbai"], speed_kn=v.service_kn))
    pts = generate_track(v, VoyagePlan(
        start=destination(*stations[0], 240.0, 35 * 1852.0),
        start_time=t0, legs=legs), r)
    emit(world, "survey_declared", pts)

    t = t0 + hours(5)
    for s in stations:
        ev = add_loiter(world, "DX6", "survey_declared", t, t + hours(8),
                        s[0], s[1], mean_sog_kn=0.5)
        # The authorisation is a landed fact about the vessel's business, in the
        # same place a real system would carry a notice-to-mariners reference.
        ev.props["declared_activity"] = "cable survey"
        ev.props["authorisation_ref"] = "SCENARIO-NTM-2026-0143"
        t = t + hours(14)

    world.truth.add(ScenarioTruth(
        scenario_id="DX6", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=False,
        notes=("Kinematically indistinguishable from E1 — three stations of "
               "8 h each along the same cable approach. The only difference is "
               "a declared activity and an authorisation reference on file. "
               "This pair is the cleanest statement of the group's thesis: "
               "behaviour alone cannot resolve intent, and the system must "
               "consult the paperwork before it accuses.")))


# --------------------------------------------------------------------------
# DX7 — naval vessel with AIS off
# --------------------------------------------------------------------------

def dx7_naval_dark(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "navy_dark")
    t0 = week(5, hours=8)
    lat_min, lat_max, lon_min, lon_max = (15.2, 16.8, 69.0, 71.0)

    pts = generate_track(v, VoyagePlan(
        start=(14.4, 72.6), start_time=t0,
        legs=[
            Leg("transit", target=((lat_min + lat_max) / 2,
                                   (lon_min + lon_max) / 2),
                speed_kn=18.0),
            Leg("station", duration_h=26.0, radius_m=9000.0),
            Leg("transit", target=PORTS["Mumbai"], speed_kn=20.0),
        ]), r)
    # `ais_expected=False` on this hull, so `emit` records the motion and emits
    # nothing. She is genuinely invisible, and correctly so.
    emit(world, "navy_dark", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="DX7", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=False,
        notes=("A naval vessel operating with AIS off inside its own declared "
               "exercise area. This is entirely normal and must NEVER be "
               "flagged dark. She appears in the corpus only as integrated "
               "truth and any radar contact — there are no AIS rows at all — "
               "so a system that equates 'radar contact with no AIS' with "
               "'dark vessel' will flag a friendly warship, which is the "
               "single most expensive false positive available.")))


# --------------------------------------------------------------------------
# DX8 — name collision
# --------------------------------------------------------------------------

def dx8_name_collision(world: ScenarioWorld) -> None:
    r = world.rng
    a, b = V(world, "saga_one"), V(world, "saga_two")

    for i, (key, port, frm) in enumerate((("saga_one", "Vadinar", "Karachi"),
                                          ("saga_two", "Kochi", "Mangalore"))):
        pts, spec = build_port_call(
            V(world, key), port, arrive_from=PORTS[frm],
            t_start=week(3, hours=30 + i * 60), rng=r,
            anchorage_hours=5.0, berth_hours=23.0)
        emit(world, key, pts)
        add_port_visit(world, "DX8", key, spec)

    world.truth.add(ScenarioTruth(
        scenario_id="DX8", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[a.entity_id, b.entity_id],
        t_start=week(3), t_end=week(4), expected_detection=False,
        notes=(f"Two unrelated vessels both named SAGA — IMO {a.imo} "
               f"({a.vessel_class}) and IMO {b.imo} ({b.vessel_class}) — "
               f"trading in different waters. The real corpus already proves "
               f"name collisions are common, which is why ADR-018 makes a "
               f"name-only sanctions match a candidate and never a finding. "
               f"Merging these into one entity would corrupt both histories.")))


# --------------------------------------------------------------------------
# DX9 — weather diversion
# --------------------------------------------------------------------------

def dx9_weather_diversion(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "monsoon_diverter")
    t0 = week(6, hours=6)

    # A wide dogleg south of the direct line, then a rejoin — the shape a
    # vessel makes running from a monsoon low, and superficially the shape of
    # an evasive detour.
    pts = generate_track(v, VoyagePlan(
        start=PORTS["Karachi"], start_time=t0,
        legs=[
            Leg("transit", target=(20.1, 67.0), speed_kn=v.service_kn),
            Leg("transit", target=(16.8, 68.9), speed_kn=v.service_kn * 0.8),
            Leg("transit", target=(15.9, 71.6), speed_kn=v.service_kn * 0.75),
            Leg("transit", target=PORTS["Mumbai"], speed_kn=v.service_kn),
        ]), r)
    emit(world, "monsoon_diverter", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="DX9", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=False,
        notes=("A 300 nm dogleg south of the direct Karachi-Mumbai line during "
               "the southwest monsoon, with speed reduced to 75-80% of service "
               "on the exposed legs. Route deviation plus slowing reads as "
               "evasive; it is seakeeping. Without a weather layer the system "
               "has no way to tell, so the honest posture is not to alert on "
               "deviation alone — and that limitation should be stated rather "
               "than papered over.")))


# --------------------------------------------------------------------------
# DX10 — fishing fleet aggregation
# --------------------------------------------------------------------------

def dx10_fishing_aggregation(world: ScenarioWorld) -> None:
    from ..cast import fleet_keys
    r = world.rng
    t0 = week(4, hours=2)
    entities = []

    for i, key in enumerate(fleet_keys()):
        v = V(world, key)
        # **A random bearing off the ground can land on Saurashtra, and did.**
        # Forty hulls are scattered 40-95 nm from the fishing ground on a
        # uniform bearing; a third of that circle is peninsula. `generate_track`
        # routes transit *legs* around land but cannot route a vessel that
        # begins on it, so the passage started ashore and the afloat validator
        # refused the corpus — at whichever seed happened to draw that bearing.
        # It went unseen until an unrelated change shifted the RNG stream, which
        # is how every seed-dependent placement bug in this generator has
        # surfaced. `nearest_water` is the shared correction and it is the same
        # one the route builder uses.
        # **Draw order is preserved exactly**, which is why the start time is
        # taken here rather than inline in the plan below. Moving a single
        # `r.uniform` past another re-rolls every parameter of all forty hulls,
        # and the measured cost of doing it by accident was fishing recall
        # falling from 86% to 73% — a corpus resample that reads exactly like a
        # classifier regression. A fix for hulls placed on land must move the
        # hulls that were on land and nothing else.
        start = nearest_water(
            destination(*FISHING_GROUND_GUJARAT, r.uniform(0, 360),
                        r.uniform(40, 95) * 1852.0),
            reachable_from=FISHING_GROUND_GUJARAT)
        depart = t0 + hours(r.uniform(0, 26))
        work = nearest_water(
            destination(*FISHING_GROUND_GUJARAT, r.uniform(0, 360),
                        r.uniform(1500, 14000)),
            reachable_from=FISHING_GROUND_GUJARAT)
        pts = generate_track(v, VoyagePlan(
            start=start, start_time=depart,
            legs=[
                Leg("transit", target=work, speed_kn=v.service_kn),
                Leg("fishing", target=FISHING_GROUND_GUJARAT,
                    duration_h=r.uniform(26, 52), radius_m=16000.0,
                    speed_kn=r.uniform(2.4, 3.8)),
                Leg("transit", target=PORTS["Mundra"], speed_kn=v.service_kn),
            ]), r)
        emit(world, key, pts)
        entities.append(v.entity_id)

    world.truth.add(ScenarioTruth(
        scenario_id="DX10", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=entities,
        t_start=t0, t_end=week(5, hours=40), expected_detection=False,
        notes=(f"{len(entities)} fishing vessels converge on a productive "
               f"ground off Gujarat over 26 hours, work it at 2.4-3.8 kn for "
               f"one to two days, and disperse to Mundra. Superficially a mass "
               f"rendezvous: dozens of hulls, slow speeds, close proximity, no "
               f"port. It is a fishing fleet. The fleet is sized to the "
               f"phenomenon rather than to the cast budget — a handful of "
               f"vessels would not resemble the thing being tested.")))


# --------------------------------------------------------------------------
# DX11 — clean vessel, dirty neighbour (the most important decoy)
# --------------------------------------------------------------------------

def dx11_clean_neighbour(world: ScenarioWorld) -> None:
    """Proximity is not association.

    A clean vessel is assigned an anchorage berth beside a designated one. They
    never meet, never trade, share no owner, no manager and no agent. The only
    thing connecting them is that a port authority put them near each other.

    **This is the false positive that would destroy analyst trust fastest**,
    because it is the easiest one for a graph to make: co-location is cheap to
    compute, superficially compelling, and wrong. An analyst who is told a
    perfectly ordinary vessel is suspicious *because of where it was parked*
    learns within one alert that the system does not understand ports.
    """
    r = world.rng
    clean, dirty = V(world, "clean_neighbour"), V(world, "brazen")
    anchorage = ANCHORAGES["Karachi"]
    t0 = week(4, hours=10)

    # She waits at the same anchorage, over the same hours, ~600 m away — close
    # enough to share an H3 cell at several resolutions, which is exactly the
    # join that would produce the false link.
    berth = destination(*anchorage, 55.0, 600.0)
    pts = generate_track(clean, VoyagePlan(
        start=PORTS["Mumbai"], start_time=t0 - hours(30),
        legs=[
            Leg("transit", target=berth, speed_kn=clean.service_kn),
            Leg("station", duration_h=34.0, radius_m=500.0),
            Leg("transit", target=PORTS["Kandla"], speed_kn=clean.service_kn),
        ]), r)
    emit(world, "clean_neighbour", pts)
    add_loiter(world, "DX11", "clean_neighbour", t0, t0 + hours(34),
               berth[0], berth[1], mean_sog_kn=0.5)

    world.truth.add(ScenarioTruth(
        scenario_id="DX11", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[clean.entity_id, dirty.entity_id],
        t_start=t0, t_end=t0 + hours(34), expected_detection=False,
        notes=(f"A clean vessel waits ~600 m from a designated hull at the "
               f"Karachi anchorage, over the same hours, purely by berth "
               f"assignment. No encounter, no shared owner, manager or agent, "
               f"no cargo relationship. They share H3 cells at several "
               f"resolutions, so a co-location join finds them instantly — and "
               f"is wrong. Proximity is not association. This is the single "
               f"most damaging false positive available to this system and the "
               f"most important row in the decoy set.")))


# --------------------------------------------------------------------------
# DX12 — fast crossing inside the encounter radius
# --------------------------------------------------------------------------

def dx12_fast_crossing(world: ScenarioWorld) -> None:
    r = world.rng
    a, b = V(world, "agent_share_1"), V(world, "agent_share_2")
    # Both hulls are also D4's shared-agent pair, so the crossing waits until
    # each has finished her port call. What DX12 asserts is that two vessels
    # pass close at speed without meeting; the date is immaterial.
    # Both need to be free *and* to have had time to sail to their start
    # points, which sit 30 nm out from the crossing on two different bearings.
    cross_point = (19.2, 67.6)
    t_cross = schedule_after(world, ["agent_share_1", "agent_share_2"],
                             week(7, hours=44))
    for k, brg in (("agent_share_1", 270.0), ("agent_share_2", 180.0)):
        t_cross = max(t_cross, schedule_arrival(
            world, k, destination(*cross_point, brg, 30 * 1852.0), t_cross)
            + hours(30.0 / 12.5))
    pa, pb = near_miss(a, b, cross_point=cross_point, t_cross=t_cross, rng=r,
                       speed_kn=12.5, closest_m=380.0)
    emit(world, "agent_share_1", pa)
    emit(world, "agent_share_2", pb)

    world.truth.add(ScenarioTruth(
        scenario_id="DX12", scenario_family=FAMILY_DECOY,
        truth_class=DECOY, entity_ids=[a.entity_id, b.entity_id],
        t_start=pa[0].t, t_end=pa[-1].t, expected_detection=False,
        notes=("Two vessels pass 380 m apart at 12.5 kn without either "
               "slowing. Inside the 500 m encounter radius and therefore "
               "inside any proximity gate, but a crossing rather than a "
               "meeting. The discriminator is the speed and the closing rate, "
               "not the distance — a detector using distance alone fires on "
               "every busy shipping lane in the AOI.")))


SCENARIOS = (
    dx1_legitimate_bunkering,
    dx2_equipment_failure,
    dx3_receiver_shadow,
    dx4_berth_congestion,
    dx5_clean_sale,
    dx6_declared_survey,
    dx7_naval_dark,
    dx8_name_collision,
    dx9_weather_diversion,
    dx10_fishing_aggregation,
    dx11_clean_neighbour,
    dx12_fast_crossing,
)
