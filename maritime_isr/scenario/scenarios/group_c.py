"""Group C — spoofing and track manipulation.

Four failures of the assumption that an AIS position is a position. Three are
true anomalies; **C4 is a decoy and the most instructive item in the group.**

C1 is caught by geometry alone, C2 needs radar to contradict a claim, C3 is
caught by arithmetic on consecutive reports. C4 looks like twelve simultaneous
spoofers and is in fact one regional interference event — the correct output is
one attribution, not twelve alerts. A system that fires twelve times here has
found nothing and has cost the analyst an afternoon, which is the failure mode
ADR-004 exists to prevent.

**C3 is the one place the physics validator is deliberately violated.** It
declares the exemption in its truth row and the validator whitelists it by
scenario id and by rule name — not globally. Every other track in the corpus,
including the other three in this group, passes the speed-envelope check
unaided.
"""
from __future__ import annotations

from ..geography import PORTS, destination
from ..primitives.ais import emit_ais, inject_kinematic_jump
from ..primitives.track import (Leg, VoyagePlan, circle_track, generate_track,
                                point_at)
from ..truth import (DECOY, FAMILY_SPOOFING, TRUE_ANOMALY, ScenarioTruth)
from ..world import SarContact, ScenarioWorld, week
from .common import V, add_port_visit, eid, emit, hours
from ..primitives.port_call import build_port_call


# --------------------------------------------------------------------------
# C1 — phantom track
# --------------------------------------------------------------------------

def c1_phantom_track(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "phantom")
    t0 = week(2, hours=36)
    # Inside terrestrial reception. At 17.8N 67.4E — the original position —
    # nothing could hear her, so the phantom track landed zero AIS rows and
    # the scenario had no evidence at all. An implausible track nobody
    # received is not a test of anything.
    centre = (20.55, 69.45)

    # Geometrically exact circles. Real hulls cannot hold a radius this well for
    # hours — wind and current see to that — so the track is implausible on its
    # own terms without violating any speed or turn limit.
    pts = circle_track(v, centre, radius_m=5200.0, t0=t0,
                       revolutions=6.5, speed_kn=8.5)
    world.add_track(v.entity_id, pts)
    world.add_ais(v.entity_id, emit_ais(v, pts, r))

    world.truth.add(ScenarioTruth(
        scenario_id="C1", scenario_family=FAMILY_SPOOFING,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["ais_spoofing"],
        notes=("Six and a half perfect circles at a fixed 5.2 km radius and a "
               "fixed 8.5 kn. Every speed and turn rate is inside the class "
               "envelope, so no kinematic check fires — the implausibility is "
               "in the *regularity*, which is a different kind of test. A "
               "detector looking only for impossible speeds will not see it.")))


# --------------------------------------------------------------------------
# C2 — position/physics contradiction
# --------------------------------------------------------------------------

def c2_empty_berth(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "berth_ghost")
    t0 = week(6, hours=30)

    # AIS says alongside at JNPT for two days.
    pts, spec = build_port_call(
        v, "JNPT", arrive_from=PORTS["Mangalore"], t_start=t0, rng=r,
        anchorage_hours=4.0, berth_hours=44.0)
    emit(world, "berth_ghost", pts)
    add_port_visit(world, "C2", "berth_ghost", spec)

    # Radar looks at the berth mid-dwell and finds nothing there. Represented as
    # the *absence* of a contact where the AIS track claims one — so the record
    # is a scene that covered the position, with no detection at it.
    t_look = spec.t_arrive + (spec.t_depart - spec.t_arrive) / 2
    claimed = point_at(pts, t_look)
    world.add_sar(SarContact(
        detection_id=eid("C2", "sar-null", v.entity_id, t_look.isoformat()),
        # A contact 9 km away on open water: the scene was clear and imaged the
        # area, so "nothing at the berth" is an observation rather than a
        # non-observation. That distinction is the difference between "no ship
        # was there" and "nothing looked there".
        lat=claimed.lat + 0.08, lon=claimed.lon + 0.02, t=t_look,
        length_m=31.0, scene_id=f"SYN_S1_{t_look:%Y%m%d}_C",
        truth_entity_id=None))

    world.truth.add(ScenarioTruth(
        scenario_id="C2", scenario_family=FAMILY_SPOOFING,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=spec.t_arrive, t_end=spec.t_depart, expected_detection=True,
        expected_anomaly_types=["ais_spoofing"],
        notes=("AIS reports her moored at JNPT for 44 hours. A scene covering "
               "the berth mid-dwell contains no contact at the claimed "
               "position, while imaging a nearby vessel — so the scene was "
               "good. The finding is the contradiction, and it requires "
               "knowing the berth was observed, not merely that no detection "
               "exists.")))


# --------------------------------------------------------------------------
# C3 — impossible kinematics
# --------------------------------------------------------------------------

def c3_impossible_kinematics(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "kinematics")
    t0 = week(7, hours=18)

    pts = generate_track(v, VoyagePlan(
        start=(21.4, 64.0), start_time=t0,
        legs=[Leg("transit", target=PORTS["Sikka"], speed_kn=v.service_kn)]), r)
    world.add_track(v.entity_id, pts)

    reports = emit_ais(v, pts, r)
    # Three jumps, well beyond anything a loaded VLCC can do. Injected into the
    # emitted reports only — the integrated truth stays physical, because the
    # ship did not teleport; her reported position did.
    for idx in (len(reports) // 4, len(reports) // 2, 3 * len(reports) // 4):
        reports = inject_kinematic_jump(reports, r, at_index=idx, jump_kn=34.0)
    world.add_ais(v.entity_id, reports)

    world.truth.add(ScenarioTruth(
        scenario_id="C3", scenario_family=FAMILY_SPOOFING,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["ais_spoofing"],
        physics_exemption="implied_speed_envelope",
        notes=(f"Three consecutive-report jumps implying 34 kn on a loaded "
               f"{v.vessel_class} whose class maximum is {v.max_kn} kn. The "
               f"integrated truth remains physical — the vessel did not move, "
               f"the reports did — so the violation is confined to the emitted "
               f"rows. This is the only scenario exempted from the implied-"
               f"speed check, whitelisted by id and by rule name.")))


# --------------------------------------------------------------------------
# C4 — regional GPS interference (DECOY)
# --------------------------------------------------------------------------

def c4_gps_interference(world: ScenarioWorld) -> None:
    """Twelve vessels, one cause. The right answer is one attribution.

    Every vessel in the cluster is displaced by the *same* offset, because that
    is what regional interference does — it moves the solution, not the ship,
    and it moves everyone's solution the same way. Displacing each vessel
    independently would have made twelve unrelated spoofers, which is precisely
    the wrong conclusion this decoy tests for.

    The shared, identical offset is itself the evidence that separates
    interference from spoofing, and it is present in the data for anything that
    looks for it.
    """
    r = world.rng
    t0 = week(4, hours=40)
    t1 = t0 + hours(20)
    # One offset for the whole cluster.
    bearing = r.uniform(0, 360)
    offset_m = r.uniform(2600, 3400)

    affected = []
    # Dedicated hulls. An earlier version borrowed twelve vessels from other
    # scenarios, which put several of them in two places at once — the cluster
    # is a 20 h event at the northwest boundary and those vessels were working
    # ports hundreds of miles away at the time. A teleporting decoy measures
    # nothing.
    keys = [f"gps_{i:02d}" for i in range(12)]
    for key in keys:
        v = V(world, key)
        # The north-western approaches, inside terrestrial reception. The AOI
        # boundary itself (23-25N, 61-62E) is outside every receiver ring, so
        # a cluster placed there landed no AIS and C4 could not have fired or
        # not fired — it simply had no data. Interference is only a decoy if
        # the displaced positions are actually received.
        start = (r.uniform(21.3, 22.6), r.uniform(67.2, 68.6))
        pts = generate_track(v, VoyagePlan(
            start=start, start_time=t0,
            legs=[Leg("transit",
                      target=destination(*start, r.uniform(80, 130),
                                         55 * 1852.0),
                      speed_kn=v.service_kn)]), r)
        world.add_track(v.entity_id, pts)
        reports = emit_ais(v, pts, r)
        # Displace the reported positions, not the truth.
        from ..geography import destination as dest
        shifted = []
        for rep in reports:
            if t0 <= rep.t <= t1:
                la, lo = dest(rep.lat, rep.lon, bearing, offset_m)
                rep = type(rep)(rep.t, la, lo, rep.sog_kn, rep.cog_deg,
                                rep.heading_deg, rep.nav_status, rep.receiver)
            shifted.append(rep)
        world.add_ais(v.entity_id, shifted)
        affected.append(v.entity_id)

    world.truth.add(ScenarioTruth(
        scenario_id="C4", scenario_family=FAMILY_SPOOFING,
        truth_class=DECOY, entity_ids=affected,
        t_start=t0, t_end=t1, expected_detection=False,
        notes=(f"{len(affected)} vessels near the northwest boundary, all "
               f"reporting positions displaced by the same "
               f"{offset_m / 1000:.1f} km on the same bearing "
               f"{bearing:.0f} deg for 20 hours. Correct behaviour is a single "
               f"attribution to regional interference. Firing "
               f"{len(affected)} spoofing alerts is the failure this decoy "
               f"measures, and the shared identical offset is the evidence "
               f"that distinguishes the two.")))


SCENARIOS = (
    c1_phantom_track,
    c2_empty_berth,
    c3_impossible_kinematics,
    c4_gps_interference,
)
