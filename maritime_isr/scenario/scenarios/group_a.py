"""Group A — dark vessel and transfer scenarios.

Five ways a cargo moves without the movement being declared, arranged so that
each one attacks a different assumption:

  A1  the canonical case, and the one the demo tells
  A2  a chain, where no single hop is worth an alert but the sequence is
  A3  a track that is a lie, contradicted by radar
  A4  degradation rather than silence — much harder, much more common
  A5  no darkness at all, to break the reflex that dark == bad

**A1 lands no encounter event, and that is the finding.** GFW derives encounters
from AIS; when both parties are silent there is no AIS to derive one from. A
generator that landed a tidy encounter row for a transfer both sides went dark
for would be inventing the very evidence whose absence is the problem. What A1
lands instead is what a real system would actually have: two gaps that bracket
the same patch of ocean at the same time, radar contacts inside that window, and
a receiving vessel that turns up at Sikka afterwards. Assembling those into a
conclusion is the work — and it is the work the corpus exists to test.
"""
from __future__ import annotations

from datetime import timedelta

from ..geography import (NW_ENTRY, PORTS, destination, haversine_m,
                         initial_bearing_deg, transfer_point)
from ..primitives.encounter import build_rendezvous, coherent
from ..primitives.gap import INTENTIONAL, build_gap, degrade_ramp
from ..primitives.port_call import build_port_call, transit_between
from ..primitives.track import Leg, VoyagePlan, generate_track, point_at
from ..truth import (FAMILY_DARK_TRANSFER, TRUE_ANOMALY, ScenarioTruth)
from ..world import SarContact, ScenarioWorld, week
from .common import (V, add_gap_event, add_loiter, add_port_visit, eid, emit,
                     hours)


def _sar(world: ScenarioWorld, scenario_id: str, key: str, t, *,
         scene_suffix: str = "A") -> SarContact | None:
    """Place a synthetic radar contact on a vessel's true position at time t.

    Taken from the *integrated truth*, not from AIS — which is the entire point.
    During a dark window there is no AIS to take it from, and the contact's
    disagreement with the (absent, or lying) AIS track is the evidence.
    """
    v = V(world, key)
    p = point_at(world.track_of(v.entity_id), t)
    if p is None:
        return None
    return world.add_sar(SarContact(
        detection_id=eid(scenario_id, "sar", v.entity_id, t.isoformat()),
        lat=p.lat, lon=p.lon, t=t, length_m=v.length_m,
        scene_id=f"SYN_S1_{t:%Y%m%d}_{scene_suffix}",
        truth_entity_id=v.entity_id))


# --------------------------------------------------------------------------
# A1 — canonical dark ship-to-ship transfer
# --------------------------------------------------------------------------

def a1_canonical_dark_sts(world: ScenarioWorld) -> None:
    r = world.rng
    donor, receiver = "spine", "receiver_alpha"
    vd, vr = V(world, donor), V(world, receiver)

    t_meet = week(3, hours=38)
    dark_h = r.uniform(9.0, 14.0)

    meet = transfer_point("deep_basin_north", r)

    # Her entry through the northwest corridor **is** the rendezvous approach,
    # rather than a separate leg bolted on in front of it. Generating both
    # produced two overlapping segments for the same hull — she was transiting
    # from the corridor and approaching the meet point simultaneously — which
    # the occupancy check in `world.add_track` now refuses outright.
    entry_bearing = initial_bearing_deg(*meet, *NW_ENTRY)
    entry_nm = haversine_m(*meet, *NW_ENTRY) / 1852.0

    pts_d, pts_r, spec = build_rendezvous(
        vd, vr, meet_point=meet, t_meet=t_meet, duration_h=dark_h,
        separation_m=world.profile.sample("encounter_separation_m", r),
        rng=r, approach_from_a=entry_bearing, approach_from_b=110.0,
        approach_nm=entry_nm, depart_to_a=300.0, depart_to_b=55.0,
        depart_nm=40.0)

    problems = coherent(spec, vd, vr)
    if problems:
        raise AssertionError(f"A1 geometry incoherent: {problems}")

    # Both go dark across the transfer, from an hour before to an hour after.
    g0 = spec.t_start - hours(1.0)
    g1 = spec.t_end + hours(1.0)
    gap_d = build_gap(pts_d, g0, g1, cause=INTENTIONAL)
    gap_r = build_gap(pts_r, g0, g1, cause=INTENTIONAL)

    emit(world, donor, pts_d, suppressions=[gap_d.suppression()])
    emit(world, receiver, pts_r, suppressions=[gap_r.suppression()])

    add_gap_event(world, "A1", donor, gap_d)
    add_gap_event(world, "A1", receiver, gap_r)

    # Radar sees two hulls alongside in the middle of the dark window. This is
    # the evidence that exists when AIS does not.
    t_sar = spec.t_start + (spec.t_end - spec.t_start) / 2
    _sar(world, "A1", donor, t_sar)
    _sar(world, "A1", receiver, t_sar)

    # The receiver proceeds to Sikka and declares a different cargo origin.
    last_r = pts_r[-1]
    call, call_spec = build_port_call(
        vr, "Sikka", arrive_from=(last_r.lat, last_r.lon),
        t_start=last_r.t + hours(2), rng=r,
        anchorage_hours=world.profile.sample("anchorage_wait_hours", r) * 0.3,
        berth_hours=world.profile.sample("port_call_dwell_hours", r) * 0.6)
    emit(world, receiver, call)
    ev = add_port_visit(world, "A1", receiver, call_spec)
    # The declaration is a claim made by the vessel, landed as a property of the
    # call rather than as a verdict. Contradicting it against where she actually
    # was is the analyst's job, and the data to do it is present.
    ev.props["declared_cargo_origin"] = "Fujairah"

    world.truth.add(ScenarioTruth(
        scenario_id="A1", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[vd.entity_id, vr.entity_id],
        t_start=pts_d[0].t, t_end=call_spec.t_depart,
        expected_detection=True,
        expected_anomaly_types=["dark_vessel", "dark_rendezvous"],
        notes=(f"Loaded {vd.vessel_class} enters via the NW corridor, both "
               f"parties dark for {dark_h:.1f} h over a transfer at "
               f"{spec.lat:.2f}N {spec.lon:.2f}E, min separation "
               f"{spec.min_separation_m:.0f} m. No encounter event lands "
               f"because no AIS was transmitting to derive one from — the "
               f"evidence is two co-located gaps plus radar. Receiver declares "
               f"a Fujairah origin at Sikka.")))


# --------------------------------------------------------------------------
# A2 — daisy chain, three hops over eleven days
# --------------------------------------------------------------------------

def a2_daisy_chain(world: ScenarioWorld) -> None:
    r = world.rng
    hops = [
        ("chain_a", "chain_b", "deep_basin_north", week(5, hours=30)),
        ("chain_b", "chain_c", "deep_basin_mid", week(6, hours=12)),
        ("chain_c", "chain_d", "deep_basin_south", week(6, hours=132)),
    ]
    entities, t_first, t_last = [], None, None

    for i, (a_key, b_key, zone, t_meet) in enumerate(hops, start=1):
        va, vb = V(world, a_key), V(world, b_key)
        meet = transfer_point(zone, r)
        # Each hop is short and unremarkable on its own — deliberately at the
        # low end of the measured encounter-duration distribution, so no single
        # transfer clears a threshold. The chain is the finding.
        dur = max(1.5, world.profile.sample("encounter_duration_hours", r) * 0.45)

        pts_a, pts_b, spec = build_rendezvous(
            va, vb, meet_point=meet, t_meet=t_meet, duration_h=dur,
            separation_m=world.profile.sample("encounter_separation_m", r),
            rng=r, approach_from_a=290.0 + i * 12, approach_from_b=95.0 - i * 9,
            approach_nm=35.0)
        if coherent(spec, va, vb):
            raise AssertionError(f"A2 hop {i} geometry incoherent")

        emit(world, a_key, pts_a)
        emit(world, b_key, pts_b)
        entities += [va.entity_id, vb.entity_id]
        t_first = spec.t_start if t_first is None else min(t_first, spec.t_start)
        t_last = spec.t_end if t_last is None else max(t_last, spec.t_end)

        # These hops are NOT dark — both parties keep transmitting, which is why
        # each one looks like ordinary lightering and why the encounter event
        # exists at all. Making them dark would collapse A2 into A1.
        from .common import add_encounter
        add_encounter(world, "A2", a_key, b_key, spec,
                      encounter_type="transfer")

    # The last link delivers, which is what turns a chain into a route.
    vd = V(world, "chain_d")
    last = world.track_of(vd.entity_id)[-1]
    call, spec_pc = build_port_call(
        vd, "Mundra", arrive_from=(last.lat, last.lon),
        t_start=last.t + hours(3), rng=r, anchorage_hours=9.0, berth_hours=26.0)
    emit(world, "chain_d", call)
    add_port_visit(world, "A2", "chain_d", spec_pc)

    world.truth.add(ScenarioTruth(
        scenario_id="A2", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=sorted(set(entities)),
        t_start=t_first, t_end=spec_pc.t_depart, expected_detection=True,
        expected_anomaly_types=["dark_rendezvous"],
        notes=("Cargo moves across three hops in eleven days through three "
               "different basin locations, ending at Mundra. Every hop is "
               "short, transmitting and individually marginal; the finding is "
               "the connected sequence, which is only reachable by multi-hop "
               "traversal. This scenario is the justification for traversal "
               "depth > 1.")))


# --------------------------------------------------------------------------
# A3 — spoof and swap
# --------------------------------------------------------------------------

def a3_spoof_and_swap(world: ScenarioWorld) -> None:
    r = world.rng
    vs, vp = V(world, "spoofer"), V(world, "spoof_partner")
    t0 = week(7, hours=6)

    # Where she really is: a transfer in the deep basin.
    meet = transfer_point("deep_basin_mid", r)
    dur = world.profile.sample("encounter_duration_hours", r)
    pts_s, pts_p, spec = build_rendezvous(
        vs, vp, meet_point=meet, t_meet=t0 + hours(20), duration_h=dur,
        separation_m=world.profile.sample("encounter_separation_m", r),
        rng=r, approach_from_a=300.0, approach_from_b=120.0, approach_nm=40.0)
    problems = coherent(spec, vs, vp)
    if problems:
        raise AssertionError(f"A3 geometry incoherent: {problems}")

    # Truth: she was in the basin. Recorded so the SAR contact is placed on the
    # real hull rather than on the story.
    world.add_track(vs.entity_id, pts_s)
    emit(world, "spoof_partner", pts_p)

    # The lie: a plausible southbound track toward Kochi, generated with the
    # same integrator so it is kinematically flawless. A spoof that failed a
    # physics check would be a different scenario (that is C1); this one is
    # only detectable against radar.
    start_lie = (pts_s[0].lat, pts_s[0].lon)
    lie = generate_track(vs, VoyagePlan(
        start=start_lie, start_time=pts_s[0].t,
        legs=[Leg("transit", target=PORTS["Kochi"], speed_kn=vs.service_kn)]), r)
    # Emit the lie under her identity; her true motion stays in truth only.
    # The lie is NOT added to `tracks` — truth holds where she really was, and
    # the broadcast track exists only as emitted rows. That asymmetry is the
    # scenario: any check run against truth sees the basin, any check run
    # against AIS sees the run to Kochi, and the contradiction is the finding.
    from ..primitives.ais import emit_ais
    world.add_ais(vs.entity_id, emit_ais(vs, lie, r))

    # Radar sees her where she actually is, ~200 nm from the broadcast track.
    t_sar = spec.t_start + (spec.t_end - spec.t_start) / 2
    contact = _sar(world, "A3", "spoofer", t_sar, scene_suffix="B")
    _sar(world, "A3", "spoof_partner", t_sar, scene_suffix="B")

    claimed = point_at(lie, t_sar)
    from ..geography import haversine_m
    sep_nm = (haversine_m(contact.lat, contact.lon, claimed.lat, claimed.lon)
              / 1852.0) if (contact and claimed) else 0.0

    world.truth.add(ScenarioTruth(
        scenario_id="A3", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=[vs.entity_id, vp.entity_id],
        t_start=t0, t_end=spec.t_end, expected_detection=True,
        expected_anomaly_types=["ais_spoofing", "dark_rendezvous"],
        notes=(f"Broadcasts a kinematically flawless track toward Kochi while "
               f"physically transferring {sep_nm:.0f} nm away in the basin. "
               f"The AIS track alone is unimpeachable; detection requires the "
               f"radar contact to contradict it. Her counterpart transmits "
               f"normally throughout, so the encounter is half-visible.")))


# --------------------------------------------------------------------------
# A4 — partial darkness
# --------------------------------------------------------------------------

def a4_partial_darkness(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "partial_dark")
    t0 = week(4, hours=10)
    t_deg0 = t0 + hours(26)
    t_deg1 = t_deg0 + hours(16)

    meet_zone = transfer_point("deep_basin_north", r)
    legs = [
        Leg("transit", target=meet_zone, speed_kn=v.service_kn),
        Leg("drift", duration_h=12.0, speed_kn=0.7),
        Leg("transit", target=PORTS["Vadinar"], speed_kn=v.service_kn),
    ]
    pts = generate_track(v, VoyagePlan(
        start=NW_ENTRY, start_time=t0, legs=legs), r)

    # Cadence slides from normal to hours apart and back — the emitted rows show
    # a vessel reporting badly, which is what poor reception looks like too.
    ramp = degrade_ramp(t_deg0, t_deg1, steps=6, start_factor=1.0,
                        end_factor=120.0, cause=INTENTIONAL)
    emit(world, "partial_dark", pts, suppressions=ramp)

    add_loiter(world, "A4", "partial_dark", t_deg0 + hours(4),
               t_deg0 + hours(14), meet_zone[0], meet_zone[1], mean_sog_kn=0.7)

    world.truth.add(ScenarioTruth(
        scenario_id="A4", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t_deg0, t_end=t_deg1, expected_detection=True,
        expected_anomaly_types=["dark_vessel", "loitering_sensitive"],
        notes=("Reporting interval degrades from roughly 3 minutes to roughly "
               "6 hours across the transfer window, then recovers. Deliberately "
               "indistinguishable from poor reception on the emitted data "
               "alone — separating the two requires knowing the reception was "
               "fine before and after, in the same water.")))


# --------------------------------------------------------------------------
# A5 — the brazen operator
# --------------------------------------------------------------------------

def a5_brazen_operator(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "brazen")
    t0 = week(4, hours=4)

    call_pts, spec = build_port_call(
        v, "Karachi", arrive_from=NW_ENTRY, t_start=t0, rng=r,
        anchorage_hours=world.profile.sample("anchorage_wait_hours", r) * 0.4,
        berth_hours=world.profile.sample("port_call_dwell_hours", r))
    emit(world, "brazen", call_pts)
    add_port_visit(world, "A5", "brazen", spec)

    world.truth.add(ScenarioTruth(
        scenario_id="A5", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=spec.t_depart, expected_detection=True,
        expected_anomaly_types=["port_risk_propagation"],
        notes=("A designated hull that never goes dark, transmits continuously "
               "and calls openly at a non-enforcing port. Exists to break the "
               "reflex that darkness defines the problem: this vessel is the "
               "highest-risk hull in the corpus and its AIS behaviour is "
               "perfect. A system that ranks by darkness misses her entirely.")))


SCENARIOS = (
    a1_canonical_dark_sts,
    a2_daisy_chain,
    a3_spoof_and_swap,
    a4_partial_darkness,
    a5_brazen_operator,
)
