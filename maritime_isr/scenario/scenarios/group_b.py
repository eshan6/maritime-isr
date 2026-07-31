"""Group B — identity manipulation.

Six ways a hull stops being the hull we thought it was. What unites them is that
**the physical vessel never changes** — the dimensions, the speed envelope, the
behavioural habits all persist — while the identifiers move. So the detection
question is always the same: can we re-attach a returning hull to its history
when the labels have been replaced?

The scenarios are ordered by how much of the identity is discarded. B1 keeps the
IMO, which makes re-attachment easy and is the control. B2 discards it too,
leaving nothing but physics and behaviour. B4 wears a dead hull's number. B5
runs two hulls under one number simultaneously, which is the case where the
right answer is not "pick one" but "record both and say so".

**New identifiers come from the reserved bands.** A phoenix that reappeared
under a real MMSI would be a false accusation against whoever holds it, so the
replacement identifiers are minted from the same 999/1xxxxxx space as the
originals and are checked by the collision guard alongside them — the guard
reads the identity ledger, not just current identities, precisely so a discarded
number cannot slip past.
"""
from __future__ import annotations

from ..geography import NW_ENTRY, PORTS, transfer_point
from ..identifiers import mint_imo, mint_mmsi
from ..primitives.gap import INTENTIONAL, build_gap
from ..primitives.port_call import build_port_call, transit_between
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..primitives.vessel import vessel_name
from ..truth import FAMILY_IDENTITY, TRUE_ANOMALY, ScenarioTruth
from ..world import ScenarioWorld, week
from .common import V, add_gap_event, add_port_visit, emit, hours


# --------------------------------------------------------------------------
# B1 — the phoenix (the narrative spine's week 4-5)
# --------------------------------------------------------------------------

def b1_phoenix(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "spine")
    t_dark = week(4, hours=20)
    t_back = t_dark + hours(24 * 9)          # nine days silent

    # She keeps moving while dark: out to the basin, loiters, comes back east.
    zone = transfer_point("deep_basin_mid", r)
    pts = generate_track(v, VoyagePlan(
        start=(world.track_of(v.entity_id)[-1].lat,
               world.track_of(v.entity_id)[-1].lon)
        if world.track_of(v.entity_id) else NW_ENTRY,
        start_time=t_dark - hours(6),
        legs=[
            Leg("transit", target=zone, speed_kn=v.service_kn),
            Leg("station", duration_h=100.0, radius_m=4000.0),
            Leg("transit", target=PORTS["Vadinar"], speed_kn=v.service_kn),
        ]), r)

    gap = build_gap(pts, t_dark, t_back, cause=INTENTIONAL)
    emit(world, "spine", pts, suppressions=[gap.suppression()])
    add_gap_event(world, "B1", "spine", gap)

    old_name, old_mmsi, old_flag = v.name, v.mmsi, v.flag
    new_mmsi = mint_mmsi(world.take_serial())
    new_name = vessel_name(r)
    # The flag change here completes the B3 cascade — the same event, counted by
    # both scenarios, because that is what actually happened.
    world.identity.change(v, t_back, {
        "name": new_name, "mmsi": new_mmsi, "flag": "CMR",
    }, reason="B1 phoenix reappearance")

    world.truth.add(ScenarioTruth(
        scenario_id="B1", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t_dark, t_end=t_back, expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly", "dark_vessel"],
        notes=(f"Dark for 9 days, returns as {new_name} / MMSI {new_mmsi} / "
               f"flag CMR, having been {old_name} / {old_mmsi} / {old_flag}. "
               f"IMO {v.imo}, dimensions ({v.length_m} x {v.beam_m} m) and "
               f"speed envelope are unchanged, so re-attachment is available "
               f"through the hull number alone. This is the easy case and the "
               f"control for B2.")))


# --------------------------------------------------------------------------
# B2 — full identity break
# --------------------------------------------------------------------------

def b2_full_identity_break(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "identity_break")
    t_dark = week(3, hours=60)
    t_back = t_dark + hours(24 * 7)

    pts = generate_track(v, VoyagePlan(
        start=NW_ENTRY, start_time=t_dark - hours(30),
        legs=[
            Leg("transit", target=transfer_point("deep_basin_south", r),
                speed_kn=v.service_kn),
            Leg("station", duration_h=70.0, radius_m=5000.0),
            Leg("transit", target=PORTS["Kandla"], speed_kn=v.service_kn),
        ]), r)
    gap = build_gap(pts, t_dark, t_back, cause=INTENTIONAL)
    emit(world, "identity_break", pts, suppressions=[gap.suppression()])
    add_gap_event(world, "B2", "identity_break", gap)

    old = dict(name=v.name, mmsi=v.mmsi, imo=v.imo, flag=v.flag)
    # A fraudulent registration using a scrapped hull's number. The replacement
    # IMO is checksum-valid, because a fraudster's forged number would be —
    # an invalid one would be rejected at the first port and defeat the point.
    world.identity.change(v, t_back, {
        "name": vessel_name(r),
        "mmsi": mint_mmsi(world.take_serial()),
        "imo": mint_imo(world.take_serial()),
        "flag": "TZA",
    }, reason="B2 fraudulent re-registration")

    world.truth.add(ScenarioTruth(
        scenario_id="B2", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t_dark, t_end=t_back, expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly"],
        notes=(f"Every identifier is replaced, IMO included ({old['imo']} -> "
               f"{v.imo}), using a scrapped hull's number. Nothing in the "
               f"identity space connects the two records. Detection must rest "
               f"entirely on the physical fingerprint ({v.length_m} x "
               f"{v.beam_m} m, {v.service_kn} kn service) and the behavioural "
               f"profile. If this one is missed, that is the honest limit of "
               f"identity-only re-attachment and should be reported as such.")))


# --------------------------------------------------------------------------
# B3 — flag cascade
# --------------------------------------------------------------------------

def b3_flag_cascade(world: ScenarioWorld) -> None:
    """Panama -> Comoros -> Gambia -> Cameroon. The rate is the anomaly.

    Each individual reflagging is legal, ordinary and boring. Four of them in
    eight weeks is not, and the only way to see that is to have dated intervals
    and count transitions — which is why this scenario is worthless without
    time-scoped edges and is a good test that they work.

    The final hop is B1's reappearance, so this cascade and that phoenix share
    an event rather than each inventing their own. Double-counting it would
    inflate both.
    """
    v = V(world, "spine")
    steps = [
        (week(1, hours=30), "COM", "reflag after a compliance inspection"),
        (week(2, hours=54), "GMB", "reflag after a port-state detention"),
    ]
    for t, flag, why in steps:
        world.identity.change(v, t, {"flag": flag}, reason=f"B3 — {why}")

    world.truth.add(ScenarioTruth(
        scenario_id="B3", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=week(1, hours=30), t_end=week(5, hours=24),
        expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly"],
        notes=("PAN -> COM -> GMB -> CMR across the window, each change "
               "following a compliance event. No single reflagging is "
               "irregular; four in eight weeks is. The last hop is shared with "
               "B1's reappearance rather than duplicated. Requires counting "
               "transitions over dated intervals, so it fails silently on any "
               "store that keeps only current state.")))


# --------------------------------------------------------------------------
# B4 — zombie IMO
# --------------------------------------------------------------------------

def b4_zombie_imo(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "zombie")
    t0 = week(2, hours=8)

    pts, spec = build_port_call(
        v, "Kandla", arrive_from=PORTS["Karachi"], t_start=t0, rng=r,
        anchorage_hours=7.0, berth_hours=22.0)
    emit(world, "zombie", pts)
    add_port_visit(world, "B4", "zombie", spec)

    # Recorded as a property of the hull's registry record, not as a verdict.
    # The demolition date is a registry fact; concluding fraud from it is the
    # analyst's step, and the evidence for it is present either way.
    v.notes += " | registry: hull recorded demolished 2019-11"

    world.truth.add(ScenarioTruth(
        scenario_id="B4", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=spec.t_depart, expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly"],
        notes=(f"Broadcasts IMO {v.imo} — checksum-valid, and belonging to a "
               f"hull recorded demolished in November 2019. The number passes "
               f"every arithmetic check we apply, which is the point: "
               f"validating a check digit proves the number is well-formed, "
               f"never that the ship is alive. Detection needs a registry "
               f"lifecycle fact, not a checksum.")))


# --------------------------------------------------------------------------
# B5 — MMSI cloning
# --------------------------------------------------------------------------

def b5_mmsi_clone(world: ScenarioWorld) -> None:
    r = world.rng
    real, ghost = V(world, "clone_real"), V(world, "clone_ghost")
    t0 = week(6, hours=12)

    # The ghost adopts the real hull's MMSI. Both transmit simultaneously from
    # roughly 400 nm apart, which no single vessel could reconcile.
    world.identity.change(ghost, t0, {"mmsi": real.mmsi},
                          reason="B5 — MMSI cloned from the real hull")

    real_pts = generate_track(real, VoyagePlan(
        start=PORTS["Mangalore"], start_time=t0,
        legs=[Leg("transit", target=PORTS["Mumbai"], speed_kn=real.service_kn)]), r)
    # The ghost must be somewhere we can actually *hear* her. An earlier
    # version put her at 20.4N 63.2E — deep basin, zero modelled reception —
    # so she landed no AIS rows at all and the duplicate-MMSI collision could
    # never be observed. The scenario was unobservable by construction, which
    # is worse than a miss: it would have been reported as a detector failure.
    # She now works the Gujarat coast, ~500 nm from the real hull and inside
    # coverage, so both broadcasts land and the contradiction is visible.
    ghost_pts = generate_track(ghost, VoyagePlan(
        start=(21.0, 69.0), start_time=t0,
        legs=[Leg("transit", target=(22.2, 69.9), speed_kn=ghost.service_kn)]), r)

    emit(world, "clone_real", real_pts)
    emit(world, "clone_ghost", ghost_pts)

    from ..geography import haversine_m
    sep_nm = haversine_m(real_pts[0].lat, real_pts[0].lon,
                         ghost_pts[0].lat, ghost_pts[0].lon) / 1852.0

    world.truth.add(ScenarioTruth(
        scenario_id="B5", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY,
        entity_ids=[real.entity_id, ghost.entity_id],
        t_start=t0, t_end=real_pts[-1].t, expected_detection=True,
        expected_anomaly_types=["ais_spoofing"],
        notes=(f"Two hulls broadcast MMSI {real.mmsi} simultaneously, "
               f"{sep_nm:.0f} nm apart. The correct behaviour is to record both "
               f"hypotheses as a duplicate-MMSI spoof tell and keep both "
               f"tracks — never to discard one as a data error, which would "
               f"delete the signal. The track builder's multi-hypothesis path "
               f"is what this exercises.")))


# --------------------------------------------------------------------------
# B6 — voyage-specific reflagging
# --------------------------------------------------------------------------

def b6_voyage_flag(world: ScenarioWorld) -> None:
    r = world.rng
    v = V(world, "voyage_flag")
    original = v.flag
    t_out = week(5, hours=6)
    t_voyage = t_out + hours(24 * 4)
    t_back = t_voyage + hours(24 * 11)

    world.identity.change(v, t_out, {"flag": "PLW"},
                          reason="B6 — reflag before a sensitive voyage")

    pts, spec = build_port_call(
        v, "Gwadar", arrive_from=NW_ENTRY, t_start=t_voyage, rng=r,
        anchorage_hours=5.0, berth_hours=30.0)
    emit(world, "voyage_flag", pts)
    add_port_visit(world, "B6", "voyage_flag", spec)

    world.identity.change(v, t_back, {"flag": original},
                          reason="B6 — reflag back afterwards")

    world.truth.add(ScenarioTruth(
        scenario_id="B6", scenario_family=FAMILY_IDENTITY,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t_out, t_end=t_back, expected_detection=True,
        expected_anomaly_types=["identity_then_anomaly"],
        notes=(f"Reflags {original} -> PLW four days before a sensitive voyage "
               f"and back eleven days after. A store holding only the current "
               f"flag sees no change at all — she ends the window flagged "
               f"exactly as she started. Only dated intervals make the "
               f"round trip visible, which makes this the sharpest test of "
               f"valid_from/valid_to in the catalogue.")))


SCENARIOS = (
    b3_flag_cascade,       # runs first: weeks 1-2, before the phoenix
    b1_phoenix,
    b2_full_identity_break,
    b4_zombie_imo,
    b5_mmsi_clone,
    b6_voyage_flag,
)
