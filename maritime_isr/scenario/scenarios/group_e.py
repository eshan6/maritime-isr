"""Group E — behavioural and geographic scenarios.

Seven patterns where *where* and *how long* carry the signal rather than any
identity fact. Most of the group is genuinely anomalous; **E5 is deliberately
ambiguous and is recorded as a decoy**, because six tankers sitting off Gujarat
for a month is simultaneously ordinary commercial floating storage and a
documented evasion pattern. A corpus that resolved that ambiguity in either
direction would be teaching the system something the world does not support.

E4 closes the narrative spine in week 8.
"""
from __future__ import annotations

from ..geography import (BOMBAY_HIGH, CABLE_APPROACH_MUMBAI, FISHING_GROUND_GUJARAT,
                         NAVAL_EXERCISE_AREA, PORTS, destination, haversine_m,
                         point_on_cable)
from ..primitives.encounter import build_rendezvous, coherent
from ..primitives.port_call import (build_anchorage_stay, build_port_call,
                                    sequence_ports)
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..truth import (DECOY, FAMILY_BEHAVIOURAL, TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import (V, add_encounter, add_loiter, add_port_visit, emit, hours,
                     schedule_arrival)


# --------------------------------------------------------------------------
# E1 — cable corridor loitering
# --------------------------------------------------------------------------

def e1_cable_loitering(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "cable_loiter")
    t0 = week(7, hours=8)

    # Station-keeping at three points along the approach, which is what a
    # survey pattern looks like and what drift does not: a drifting hull moves
    # downwind in a straight line, it does not hold three separate stations.
    stations = [point_on_cable(f) for f in (0.30, 0.55, 0.78)]
    legs = []
    for s in stations:
        legs += [Leg("transit", target=s, speed_kn=v.service_kn),
                 Leg("station", duration_h=7.5, radius_m=600.0)]
    legs.append(Leg("transit", target=PORTS["Mumbai"], speed_kn=v.service_kn))

    pts = generate_track(v, VoyagePlan(
        start=destination(*stations[0], 250.0, 40 * 1852.0),
        start_time=t0, legs=legs), r)
    emit(world, "cable_loiter", pts)

    t = t0 + hours(6)
    for s in stations:
        add_loiter(world, "E1", "cable_loiter", t, t + hours(7.5),
                   s[0], s[1], mean_sog_kn=0.5)
        t = t + hours(13)

    world.truth.add(ScenarioTruth(
        scenario_id="E1", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["loitering_sensitive"],
        notes=("Survey-capable vessel holds three separate stations of 7.5 h "
               "each along the SEA-ME-WE approach to Mumbai, with no declared "
               "survey on file. The pattern is inconsistent with drift: a "
               "drifting hull tracks downwind, it does not return to station "
               "three times. Compare the declared-survey decoy, which is the "
               "same pattern with paperwork.")))


# --------------------------------------------------------------------------
# E2 — offshore infrastructure proximity
# --------------------------------------------------------------------------

def e2_bombay_high_passes(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "rig_prowler")
    t0 = week(5, hours=14)

    legs, t = [], t0
    for i in range(4):
        near = destination(*BOMBAY_HIGH, 40.0 + i * 85.0, r.uniform(4200, 7000))
        legs += [Leg("transit", target=near, speed_kn=3.2),
                 Leg("station", duration_h=1.6, radius_m=900.0)]
    legs.append(Leg("transit", target=PORTS["JNPT"], speed_kn=v.service_kn))

    pts = generate_track(v, VoyagePlan(
        start=destination(*BOMBAY_HIGH, 200.0, 55 * 1852.0),
        start_time=t0, legs=legs), r)
    emit(world, "rig_prowler", pts)

    for i in range(4):
        ts = t0 + hours(9 + i * 7)
        add_loiter(world, "E2", "rig_prowler", ts, ts + hours(1.6),
                   BOMBAY_HIGH[0] + 0.02 * i, BOMBAY_HIGH[1] - 0.02 * i,
                   mean_sog_kn=1.1)

    world.truth.add(ScenarioTruth(
        scenario_id="E2", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["loitering_sensitive"],
        notes=("Four slow passes within 4-7 km of the Bombay High field over "
               "28 hours, at 3 kn, well outside the coastal lanes and with no "
               "field business. Any one pass is unremarkable; four with "
               "station-keeping between them is a survey.")))


# --------------------------------------------------------------------------
# E3 — naval exercise area intrusion
# --------------------------------------------------------------------------

def e3_exercise_area_intrusion(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "naval_intruder")
    t0 = week(6, hours=44)
    lat_min, lat_max, lon_min, lon_max = NAVAL_EXERCISE_AREA
    centre = ((lat_min + lat_max) / 2, (lon_min + lon_max) / 2)

    pts = generate_track(v, VoyagePlan(
        start=(13.6, 72.4), start_time=t0,
        legs=[
            # A deviation, not a transit: she leaves the direct line to enter
            # the box, crosses it slowly, and rejoins.
            Leg("transit", target=(lat_min + 0.2, lon_max - 0.2),
                speed_kn=v.service_kn),
            Leg("transit", target=centre, speed_kn=5.0),
            Leg("station", duration_h=3.0, radius_m=1200.0),
            Leg("transit", target=(lat_max - 0.2, lon_min + 0.3), speed_kn=6.0),
            Leg("transit", target=PORTS["Mumbai"], speed_kn=v.service_kn),
        ]), r)
    emit(world, "naval_intruder", pts)
    add_loiter(world, "E3", "naval_intruder", t0 + hours(20),
               t0 + hours(23), centre[0], centre[1], mean_sog_kn=0.9)

    world.truth.add(ScenarioTruth(
        scenario_id="E3", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["loitering_sensitive"],
        notes=("Commercial hull deviates from the coastal route into a "
               "declared exercise area, slows to 5 kn, holds station for 3 h "
               "in the centre and rejoins. The deviation is the signal — a "
               "vessel transiting the box on its direct line would be "
               "unremarkable, and a detector that only geofences would not "
               "tell the two apart.")))


# --------------------------------------------------------------------------
# E4 — port-call laundering (closes the narrative spine)
# --------------------------------------------------------------------------

def e4_port_call_laundering(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "spine")
    # Starts late in week 7 so the four-call sequence *completes* in week 8.
    # The corpus window now ends at the real corpus maximum (2026-07-25 22:00,
    # five days earlier than the old T1), which leaves week 8 too short to
    # contain a ~5-day laundering sequence from its own start.
    t0 = week(7, hours=48)
    track = world.track_of(v.entity_id)
    start = (track[-1].lat, track[-1].lon) if track else PORTS["Karachi"]

    # High-risk terminal, then two brief clean intermediate calls, then the
    # destination. The two middle calls are deliberately short — long enough to
    # appear in a port-call history, too short to work cargo.
    #
    # The sequence stays inside the Sindh/Gujarat cluster because the whole
    # laundering pattern has to fit in the eight-week window with time to
    # spare: a route with 700 nm legs between the intermediate calls would
    # overrun the corpus, and a scenario that only works if the window is
    # extended is a scenario that does not work.
    pts, specs = sequence_ports(
        v, ["Karachi", "Mundra", "Sikka", "Vadinar"], t0, r,
        start_from=start,
        dwell_hours=[26.0, 5.0, 4.5, 30.0],
        wait_hours=[4.0, 2.0, 2.0, 8.0])
    emit(world, "spine", pts)
    for s in specs:
        add_port_visit(world, "E4", "spine", s)

    world.truth.add(ScenarioTruth(
        scenario_id="E4", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=specs[-1].t_depart, expected_detection=True,
        expected_anomaly_types=["port_risk_propagation"],
        notes=("Karachi (26 h alongside) -> Mundra (5 h) -> Sikka (4.5 h) -> "
               "Vadinar (30 h). The two intermediate calls are far too short "
               "to work cargo and exist to put clean ports between the "
               "high-risk terminal and the destination. The signal is the "
               "sequence and the dwell asymmetry, not any single call. Closes "
               "the narrative spine that began with the week-1 reflagging.")))


# --------------------------------------------------------------------------
# E5 — floating storage (DECOY — deliberately ambiguous)
# --------------------------------------------------------------------------

def e5_floating_storage(world: ScenarioWorld) -> None:
    r = world.rng
    keys = [f"storage_{i}" for i in range(1, 7)]
    t0 = week(1, hours=12)
    entities = []

    for i, key in enumerate(keys):
        v = V(world, key)
        pts, spec = build_anchorage_stay(
            v, "Vadinar", arrive_from=(21.2, 66.8 - i * 0.3),
            t_start=t0 + hours(i * 9), rng=r,
            hours=r.uniform(30 * 24, 42 * 24), radius_m=1400.0)
        emit(world, key, pts)
        add_port_visit(world, "E5", key, spec)
        add_loiter(world, "E5", key, spec.t_arrive + hours(6),
                   min(spec.t_depart, spec.t_arrive + hours(30 * 24)),
                   21.9 + i * 0.03, 69.4 + i * 0.04, mean_sog_kn=0.4)
        entities.append(v.entity_id)

    world.truth.add(ScenarioTruth(
        scenario_id="E5", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=DECOY, entity_ids=entities,
        t_start=t0, t_end=min(week(8, hours=100), world.t1),
        expected_detection=False,
        notes=("Six tankers at anchor off Gujarat for 30+ days. This is "
               "commercially normal — floating storage against a contango "
               "market — and it is also a documented evasion pattern, and the "
               "AIS data cannot distinguish them. Recorded as a decoy because "
               "on this evidence the honest output is 'watch', not 'alert'. If "
               "the system fires here it will fire on every storage play in "
               "the Gulf, which is a lot of ordinary tonnage.")))


# --------------------------------------------------------------------------
# E6 — IUU transshipment
# --------------------------------------------------------------------------

def e6_iuu_transshipment(world: ScenarioWorld) -> None:
    r = world.rng
    fisher, reefer = V(world, "iuu_fisher"), V(world, "iuu_reefer")
    t0 = week(5, hours=20)
    ground = (13.4, 71.8)                      # inside the Indian EEZ

    fish = generate_track(fisher, VoyagePlan(
        start=(12.9, 73.2), start_time=t0,
        legs=[
            Leg("transit", target=ground, speed_kn=fisher.service_kn),
            Leg("fishing", target=ground, duration_h=34.0, radius_m=9000.0,
                speed_kn=3.1),
        ]), r)
    emit(world, "iuu_fisher", fish)
    add_loiter(world, "E6", "iuu_fisher", t0 + hours(10), t0 + hours(44),
               ground[0], ground[1], mean_sog_kn=3.1)

    t_meet = t0 + hours(50)
    pts_f, pts_r, spec = build_rendezvous(
        fisher, reefer, meet_point=(13.1, 71.2), t_meet=t_meet,
        duration_h=4.5, separation_m=45.0, rng=r,
        approach_from_a=60.0, approach_from_b=250.0, approach_nm=22.0)
    problems = coherent(spec, fisher, reefer)
    if problems:
        raise AssertionError(f"E6 geometry incoherent: {problems}")

    emit(world, "iuu_fisher", pts_f)
    add_encounter(world, "E6", "iuu_fisher", "iuu_reefer", spec,
                  encounter_type="transshipment")

    last = pts_r[-1]
    call, spec_pc = build_port_call(
        reefer, "Kochi", arrive_from=(last.lat, last.lon),
        t_start=last.t + hours(2), rng=r, anchorage_hours=5.0, berth_hours=19.0)
    emit(world, "iuu_reefer", pts_r + call)
    add_port_visit(world, "E6", "iuu_reefer", spec_pc)

    world.truth.add(ScenarioTruth(
        scenario_id="E6", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[fisher.entity_id, reefer.entity_id],
        t_start=t0, t_end=spec_pc.t_depart, expected_detection=True,
        expected_anomaly_types=["dark_rendezvous", "port_risk_propagation"],
        notes=("Unauthorised fishing inside the Indian EEZ for 34 h, then a "
               "4.5 h transshipment to a reefer which carries the catch to "
               "Kochi. The reefer is the graph link: it never fished and never "
               "entered the EEZ, and connecting it to the offence requires the "
               "encounter edge plus the subsequent port call.")))


# --------------------------------------------------------------------------
# E7 — return to the same open-water position
# --------------------------------------------------------------------------

def e7_return_to_position(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "receiver_alpha")
    spot = (18.30, 65.10)
    planned = [week(1, hours=40), week(3, hours=8), week(5, hours=52),
               week(7, hours=30)]
    entities = [v.entity_id]

    # She is also A1's receiving vessel, so some of these planned slots collide
    # with the transfer and the Sikka call that follows it. Rather than hard-
    # coding times that happen to miss today's catalogue, each visit is pushed
    # to whenever she is actually free. The scenario's claim is about the
    # *repetition* and the identical position, not about specific dates, so
    # deferring a visit costs the finding nothing — and it keeps E7 correct when
    # another scenario later borrows her.
    # Computed inside the loop, not up front: each visit occupies her calendar,
    # so the next one has to be scheduled against the state after the previous
    # was committed.
    visits = []
    for i, planned_t in enumerate(planned):
        approach_from = destination(*spot, 200.0 + i * 30, 38 * 1852.0)
        # Not just "when is she free" but "when could she have got here" — she
        # may have been left 300 nm away by another scenario, and starting her
        # at a fixed position without allowing passage time is a teleport.
        t = schedule_arrival(world, "receiver_alpha", approach_from, planned_t)
        visits.append(t)
        pts = generate_track(v, VoyagePlan(
            start=approach_from,
            start_time=t,
            legs=[
                Leg("transit", target=spot, speed_kn=v.service_kn),
                Leg("station", duration_h=3.4, radius_m=700.0),
                Leg("transit",
                    target=destination(*spot, 20.0 + i * 40, 38 * 1852.0),
                    speed_kn=v.service_kn),
            ]), r)
        emit(world, "receiver_alpha", pts)
        add_loiter(world, "E7", "receiver_alpha", t + hours(4),
                   t + hours(7.4), spot[0], spot[1], mean_sog_kn=0.6)

    world.truth.add(ScenarioTruth(
        scenario_id="E7", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=entities,
        t_start=visits[0], t_end=visits[-1] + hours(12),
        expected_detection=True,
        expected_anomaly_types=["loitering_sensitive"],
        notes=(f"Returns to {spot[0]:.2f}N {spot[1]:.2f}E on four occasions "
               f"across seven weeks, holding station 3-4 h each time, "
               f"approaching from a different bearing on every visit. There is "
               f"nothing at that position — no port, no field, no anchorage — "
               f"which is what makes the repetition the finding. A single "
               f"visit is noise; four is a pattern, and detecting it requires "
               f"the graph to remember weeks back.")))


SCENARIOS = (
    e5_floating_storage,       # weeks 1-8, accumulates from the start
    e7_return_to_position,
    e1_cable_loitering,
    e2_bombay_high_passes,
    e3_exercise_area_intrusion,
    e6_iuu_transshipment,
    e4_port_call_laundering,   # week 8, closes the spine
)
