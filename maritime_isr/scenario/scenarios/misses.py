"""Deliberate misses — build them, let them fail, make the failure explicable.

Two scenarios the system is **expected not to detect**, each sitting just beyond
a capability boundary that has a number attached.

The point is not the miss. Any system misses things. The point is that when an
operator asks "why didn't you see this?", the answer is a specific, retrievable
number rather than a shrug — *the vessel is 22 m and our size floor is 20 m with
Sentinel-1's reliable detection threshold around 25-30 m for a small wooden
hull*, or *reception at that position is 0.00 and we do not assert intentional
silence outside demonstrated coverage*.

**Explaining a considered silence is a credibility moment.** A system that
cannot distinguish "I looked and could not see" from "I did not look" is
indistinguishable from one that simply missed, and an analyst has no way to
calibrate trust in it. So both of these land the evidence of the boundary — the
size, the reception estimate — so the reasoning is renderable rather than
merely true.
"""
from __future__ import annotations

from ...config import DARK_MIN_LENGTH_M
from ..geography import PORTS, destination
from ..primitives.encounter import build_rendezvous, coherent
from ..primitives.gap import INTENTIONAL, build_gap
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..truth import (DELIBERATE_MISS, FAMILY_BOUNDARY, ScenarioTruth)
from ..world import SarContact, ScenarioWorld, week
from .common import V, add_encounter, add_gap_event, eid, emit, hours


# --------------------------------------------------------------------------
# M1 — sub-floor dhow transfer off the Makran coast
# --------------------------------------------------------------------------

def m1_sub_floor_dhow(world: ScenarioWorld) -> None:
    r = world.rng
    a, b = V(world, "dhow_sub_floor"), V(world, "dhow_partner")
    t_meet = week(6, hours=26)
    meet = (24.10, 63.05)                      # off the Makran coast, in AOI

    pts_a, pts_b, spec = build_rendezvous(
        a, b, meet_point=meet, t_meet=t_meet, duration_h=3.2,
        separation_m=14.0, rng=r,
        approach_from_a=310.0, approach_from_b=140.0, approach_nm=12.0)
    problems = coherent(spec, a, b)
    if problems:
        raise AssertionError(f"M1 geometry incoherent: {problems}")

    emit(world, "dhow_sub_floor", pts_a)
    emit(world, "dhow_partner", pts_b)
    add_encounter(world, "M1", "dhow_sub_floor", "dhow_partner", spec,
                  encounter_type="transfer")

    # A radar contact IS placed, at the true position and the true (tiny) size.
    # The cascade should suppress it on the size floor rather than never seeing
    # it — "suppressed_size" is a recorded verdict, and being able to show that
    # verdict is what makes the boundary explicable.
    t_sar = spec.t_start + (spec.t_end - spec.t_start) / 2
    world.add_sar(SarContact(
        detection_id=eid("M1", "sar", a.entity_id, t_sar.isoformat()),
        lat=spec.lat, lon=spec.lon, t=t_sar, length_m=a.length_m,
        scene_id=f"SYN_S1_{t_sar:%Y%m%d}_M", truth_entity_id=a.entity_id))

    world.truth.add(ScenarioTruth(
        scenario_id="M1", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS, entity_ids=[a.entity_id, b.entity_id],
        t_start=spec.t_start, t_end=spec.t_end, expected_detection=False,
        capability_boundary=(
            f"vessel length {a.length_m:.0f} m; dark-vessel size floor is "
            f"{DARK_MIN_LENGTH_M:.0f} m, and Sentinel-1 IW GRD at 20 m ground "
            f"resolution does not reliably detect a low-freeboard wooden hull "
            f"below roughly 25-30 m in any sea state"),
        notes=(f"A {a.length_m:.0f} m dhow transfers to a {b.length_m:.0f} m "
               f"dhow off the Makran coast for 3.2 h. The system does not see "
               f"it, and should not claim to. The radar contact is landed at "
               f"its true size so the cascade records a size-floor suppression "
               f"rather than silence — the difference between 'below our "
               f"threshold' and 'nothing was there' is the whole point, and it "
               f"is the honest statement of where free SAR stops.")))


# --------------------------------------------------------------------------
# M2 — offshore gap far outside demonstrated reception
# --------------------------------------------------------------------------

def m2_offshore_gap(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "offshore_gap")
    t0 = week(3, hours=14)
    off = (12.05, 61.10)                       # deep south-west, in AOI

    pts = generate_track(v, VoyagePlan(
        start=(14.2, 63.8), start_time=t0,
        legs=[
            Leg("transit", target=off, speed_kn=v.service_kn),
            Leg("transit", target=destination(*off, 120.0, 120 * 1852.0),
                speed_kn=v.service_kn),
            Leg("transit", target=PORTS["Kochi"], speed_kn=v.service_kn),
        ]), r)

    g0 = t0 + hours(16)
    gap = build_gap(pts, g0, g0 + hours(22), cause=INTENTIONAL)
    emit(world, "offshore_gap", pts, suppressions=[gap.suppression()])
    add_gap_event(world, "M2", "offshore_gap", gap)

    world.truth.add(ScenarioTruth(
        scenario_id="M2", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS, entity_ids=[v.entity_id],
        t_start=gap.t0, t_end=gap.t1, expected_detection=False,
        capability_boundary=(
            f"modelled terrestrial reception at the off-position "
            f"({gap.lat_off:.2f}N, {gap.lon_off:.2f}E) is "
            f"{gap.coverage_at_off:.2f}; the nearest receiver site is over "
            f"900 km away and satellite AIS is unfunded (ADR-005)"),
        notes=(f"The vessel does switch off deliberately — that is the ground "
               f"truth — and we are still right not to say so. A 22 h silence "
               f"at {gap.lat_off:.2f}N {gap.lon_off:.2f}E, where we have no "
               f"ears at all, must resolve to UNKNOWN and never to "
               f"INTENTIONAL_SILENCE. Calling it dark would be a false "
               f"positive by construction. The reception estimate is landed on "
               f"the gap row so the reasoning can be shown to an operator, "
               f"which is the credibility moment this scenario exists for.")))


SCENARIOS = (
    m1_sub_floor_dhow,
    m2_offshore_gap,
)
