"""Group R — the coastal picture, where a shore radar can actually see.

**Why this group had to exist rather than reusing group A.** Every dark
scenario in this corpus happens in the deep transfer basins, 350-450 km
offshore. That is the right place for a ship-to-ship transfer and it is out of
reach of every shore station on the coast: a 35 m tower holds a laden tanker to
about 48 km and nothing at all beyond it. So the existing dark scenarios produce
exactly zero radar contacts, and no amount of tuning changes that. It is a
finding about the sensor rather than about the corpus, and it is recorded in
ADR-028 instead of being papered over by moving the network out to sea.

What a coastal radar *is* good for is the coastal belt — which is also the
threat the Coast Guard's requirement is written about: small and medium hulls
working close inshore, with or without a transponder. So this group puts five
situations inside the belt, and the sixth deliberately outside it.

  R1  a hull that never transmits at all, working the Saurashtra coast
  R2  a transponder switched off mid-passage, inside cover — the headline
  R3  an inshore transfer with one silent party
  R4  a 12 m skiff at 25 km: under the radar's reach, a stated boundary
  R5  a dark passage through the Konkan coverage hole: nothing sees it
  R6  the single point moorings — permanent contacts that must be suppressed

R1-R3 are true anomalies. R4 and R5 are deliberate misses with numbers
attached, which is the more useful kind: an operator who is told *why* the
system is quiet trusts it more than one who is told nothing.

R6 is a decoy against a layer that has never been exercised. The self-building
static-object layer in `fusion/dark.py` needs a contact to recur in one place
across several looks over at least a week, and the SAR corpus contains six
contacts in total — so the layer has shipped and never run. A single point
mooring reports every quarter of an hour for eight weeks, which is exactly what
it was built for.
"""
from __future__ import annotations

from ..geography import destination
from ..primitives.ais import Suppression
from ..primitives.encounter import build_rendezvous, coherent
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..radar_network import FIXED_TARGETS, STATIONS_BY_ID
from ..truth import (DECOY, DELIBERATE_MISS, FAMILY_BOUNDARY,
                     FAMILY_DARK_TRANSFER, FAMILY_DECOY, TRUE_ANOMALY,
                     ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V, emit, hours, schedule_arrival


def _offshore_of(station_id: str, bearing_deg: float, km: float
                 ) -> tuple[float, float]:
    """A position `km` seaward of a named station on a given bearing.

    Scenario positions are written against the station network rather than as
    bare coordinates, so a station that moves takes its scenarios with it and a
    scenario cannot silently drift out of the cover it was written to sit in.
    """
    st = STATIONS_BY_ID[station_id]
    return destination(st.lat, st.lon, bearing_deg, km * 1000.0)


# --------------------------------------------------------------------------
# R1 — the hull that never transmits
# --------------------------------------------------------------------------

def r1_coastal_dark_runner(world: ScenarioWorld) -> None:
    """A 127 m coaster working Porbandar to Diu with the transponder never on.

    The plainest possible case, and the one the whole build exists to make
    possible: nothing on AIS, a solid radar track for hours, inside demonstrated
    reception. `ais_expected=False` on the hull means `emit` records her motion
    and lands not one position row — she is genuinely absent from AIS, rather
    than present-and-suppressed.
    """
    r = world.rng
    v = V(world, "coast_runner")
    t0 = week(2, hours=6)

    # Hugging the coast at 18-22 km off: inside Porbandar's and Veraval's cover
    # for most of the passage, through the thinner patch between them.
    legs = [
        Leg("transit", target=_offshore_of("SYN-POR", 200.0, 20.0), speed_kn=11.0),
        Leg("transit", target=_offshore_of("SYN-VER", 190.0, 18.0), speed_kn=11.5),
        Leg("station", duration_h=3.5, radius_m=1200.0),
        Leg("transit", target=_offshore_of("SYN-DIU", 195.0, 16.0), speed_kn=11.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-POR", 250.0, 26.0), start_time=t0,
        legs=legs), r)
    emit(world, "coast_runner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="R1", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["dark_vessel"],
        notes=("A 127 m hull working the Saurashtra coast with no AIS at all, "
               "inside the cover of three stations and inside demonstrated "
               "terrestrial reception. If this does not produce a dark contact "
               "nothing will.")))


# --------------------------------------------------------------------------
# R2 — the transponder goes quiet, and radar keeps watching
# --------------------------------------------------------------------------

def r2_transponder_quits_under_cover(world: ScenarioWorld) -> None:
    """AIS on, then off, mid-passage, while a station holds the track.

    **This is the scenario the build is named for.** The correlation stage should
    match her radar track to her AIS track for the first leg, lose the match at
    the moment she stops transmitting, and carry on holding the contact — so the
    product can say where the transponder went quiet and what she did next,
    rather than merely that something unexplained exists.

    The point of switching off *inside* cover rather than outside it is that the
    system can tell the difference. A vessel that goes quiet where nothing was
    listening has told us nothing.
    """
    r = world.rng
    v = V(world, "quiet_quitter")
    t0 = schedule_arrival(world, "quiet_quitter",
                          _offshore_of("SYN-DAH", 250.0, 30.0),
                          week(4, hours=5))

    legs = [
        # Southbound past Dahanu, then Mumbai, then on toward Ratnagiri.
        Leg("transit", target=_offshore_of("SYN-DAH", 240.0, 24.0), speed_kn=12.5),
        Leg("transit", target=_offshore_of("SYN-MUM", 255.0, 22.0), speed_kn=12.0),
        Leg("station", duration_h=2.0, radius_m=1500.0),
        Leg("transit", target=_offshore_of("SYN-MUM", 200.0, 30.0), speed_kn=10.5),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-DAH", 260.0, 34.0), start_time=t0,
        legs=legs), r)

    # She goes quiet about two thirds of the way through and stays quiet. The
    # window is open-ended to the end of the passage: a shutdown, not a dropout.
    #
    # **The fraction is load-bearing and the first value was wrong.** At one
    # third she was still north of Dahanu and outside Mumbai's cover when she
    # switched off, so by the time a station picked her up she had already been
    # silent for an hour — her whole radar track was the dark period, there was
    # nothing to correlate, and the scenario written to demonstrate the
    # transition could not produce one. She has to be *held on radar while still
    # transmitting* for the "and then she stopped" to exist at all, which is
    # exactly the condition an operator would need in reality.
    t_quiet = pts[int(len(pts) * 0.65)].t
    emit(world, "quiet_quitter", pts,
         suppressions=[Suppression(t_quiet, pts[-1].t + hours(1),
                                   cause="intentional")])

    world.truth.add(ScenarioTruth(
        scenario_id="R2", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=t_quiet, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["dark_vessel"],
        notes=("A 170 m product tanker transmitting normally past Dahanu, then "
               "silent from roughly a third of the way through the passage, "
               "while Mumbai's station holds the track throughout. The "
               "correlation should show the match breaking at a specific place "
               "and time — that transition is the product, not the contact.")))


# --------------------------------------------------------------------------
# R3 — an inshore transfer, one party silent
# --------------------------------------------------------------------------

def r3_inshore_transfer_one_silent(world: ScenarioWorld) -> None:
    """A dark coaster and a transmitting fishing boat, alongside, 9 km out.

    Both are on radar. Only one is on AIS. Two things should follow: the dark
    party becomes a contact, and the *meeting itself* is visible from radar
    tracks alone — which is the encounter detector running over radar-sourced
    tracks, the claim the architecture makes and had never been tested.
    """
    r = world.rng
    dark, light = "coast_dark_party", "coast_light_party"
    vd, vl = V(world, dark), V(world, light)

    # **Off Dwarka, not off Vengurla, and the move is a finding.** The first
    # placement was on the Konkan coast, which the radar network covers densely
    # and the scenario's five AIS receiver sites do not reach at all. The
    # transmitting party landed no AIS rows, so the corpus contained a
    # "transfer with one silent party" in which both parties were silent — and
    # the contrast the scenario exists to create had quietly evaporated. Dwarka
    # sits 93 km from the Sikka receiver, so both sensors genuinely reach it.
    meet = _offshore_of("SYN-DWA", 250.0, 10.0)
    t_meet = schedule_arrival(world, dark, meet, week(6, hours=20))

    pts_d, pts_l, spec = build_rendezvous(
        vd, vl, meet_point=meet, t_meet=t_meet, duration_h=2.6,
        separation_m=world.profile.sample("encounter_separation_m", r),
        rng=r, approach_from_a=280.0, approach_from_b=200.0,
        approach_nm=14.0, depart_to_a=300.0, depart_to_b=170.0,
        depart_nm=12.0)
    problems = coherent(spec, vd, vl)
    if problems:
        raise AssertionError(f"R3 geometry incoherent: {problems}")

    # She has a transponder and it is off for the whole run — a suppression
    # spanning the voyage rather than `ais_expected=False`, because those are
    # different facts about a hull. R1's coaster has no transponder fitted; this
    # one has one and chose not to use it today, and the corpus should be able
    # to contain both.
    emit(world, dark, pts_d,
         suppressions=[Suppression(pts_d[0].t - hours(1),
                                   pts_d[-1].t + hours(1),
                                   cause="intentional")])
    emit(world, light, pts_l)

    world.truth.add(ScenarioTruth(
        scenario_id="R3", scenario_family=FAMILY_DARK_TRANSFER,
        truth_class=TRUE_ANOMALY,
        entity_ids=[vd.entity_id, vl.entity_id],
        t_start=spec.t_start, t_end=spec.t_end,
        expected_detection=True,
        expected_anomaly_types=["dark_vessel", "dark_rendezvous"],
        notes=("A transfer 10 km off Dwarka between a hull whose transponder is "
               "off and a fishing boat with a working one. Radar holds both, so "
               "the meeting is derivable from radar tracks alone — which is the "
               "encounter detector running on a sensor it was not written for.")))


# --------------------------------------------------------------------------
# R4 — under the radar's reach: a stated boundary
# --------------------------------------------------------------------------

def r4_subfloor_skiff(world: ScenarioWorld) -> None:
    """A 12 m hull working 25 km offshore. The network cannot see her.

    This is a real dark vessel that produces nothing, and the value is that the
    reason is a number rather than a shrug: a 12 m target returns about 15 dBsm,
    which against a 35 m tower puts the half-detection range at roughly 9 km. At
    25 km she is four times too far. Saying that out loud is a credibility
    moment; going quiet without explanation looks identical to having missed
    her.
    """
    r = world.rng
    v = V(world, "subfloor_skiff")
    t0 = week(3, hours=4)

    ground = _offshore_of("SYN-RAT", 260.0, 25.0)
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-RAT", 250.0, 27.0), start_time=t0,
        legs=[Leg("fishing", target=ground, duration_h=14.0, radius_m=6000.0),
              Leg("transit", target=_offshore_of("SYN-RAT", 240.0, 24.0),
                  speed_kn=8.0)]), r)
    emit(world, "subfloor_skiff", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="R4", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("Genuinely dark and genuinely invisible to this sensor."),
        capability_boundary=(
            "A 12 m hull returns roughly 15 dBsm. Against a 35 m coastal tower "
            "that puts 50% detection at about 9 km; she works at 25 km. The "
            "network cannot see her, and no threshold change alters that — it "
            "needs a different sensor.")))


# --------------------------------------------------------------------------
# R5 — the coverage hole
# --------------------------------------------------------------------------

def r5_konkan_coverage_hole(world: ScenarioWorld) -> None:
    """A dark tanker through the gap between Mumbai and Ratnagiri.

    The two stations are 215 km apart along a coast that has one station between
    them, and roughly 60 nm offshore of Dabhol there is simply nothing watching.
    A 183 m hull running dark through it produces no contact at all.

    **This is the most important of the two boundaries**, because it is the one
    an operator will meet weekly and the one that a coverage map fixes. A dark
    vessel we cannot see is not a detector failure; it is an argument for a
    station, and the system should be able to make that argument with a
    position attached.
    """
    r = world.rng
    v = V(world, "hole_runner")
    t0 = schedule_arrival(world, "hole_runner",
                          _offshore_of("SYN-MUM", 200.0, 95.0),
                          week(5, hours=11))

    # Well outside both stations' cover for the whole passage.
    legs = [
        Leg("transit", target=_offshore_of("SYN-RAT", 280.0, 105.0), speed_kn=13.0),
        Leg("station", duration_h=5.0, radius_m=2500.0),
        Leg("transit", target=_offshore_of("SYN-VEN", 265.0, 110.0), speed_kn=13.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-MUM", 205.0, 100.0), start_time=t0,
        legs=legs), r)
    emit(world, "hole_runner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="R5", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("A dark vessel the network is not looking at."),
        capability_boundary=(
            "Mumbai and Ratnagiri are 215 km apart with one station between "
            "them. About 100 km offshore of that stretch nothing has cover: a "
            "183 m hull at 13 knots crosses it dark and produces zero plots. "
            "The gap is geometry, not tuning — it is an argument for a station, "
            "and the system should be able to say where.")))


# --------------------------------------------------------------------------
# R6 — the moorings that are always there
# --------------------------------------------------------------------------

def r6_fixed_installations(world: ScenarioWorld) -> None:
    """The single point moorings and the light float. Never vessels, always there.

    No track is generated here: `scenario/radar.py` reports the fixed targets
    directly from `radar_network.FIXED_TARGETS`, because they are part of the
    picture rather than part of the cast. What this function contributes is the
    truth row — an assertion that a correctly built system stays silent about
    them — and the entity ids the measurement matches against.

    They are the hardest ordinary false positive in a radar picture: permanently
    present, never on AIS, and the right size to clear a vessel size floor. The
    self-building static layer is supposed to absorb them, and until now has had
    nothing to absorb.
    """
    world.truth.add(ScenarioTruth(
        scenario_id="R6", scenario_family=FAMILY_DECOY,
        truth_class=DECOY,
        entity_ids=[f.target_id for f in FIXED_TARGETS],
        t_start=world.t0, t_end=world.t1,
        expected_detection=False,
        notes=("Two Vadinar single point moorings, the Sikka SPM and the "
               "Prongs light float. Radar reports all four every quarter of an "
               "hour for eight weeks and no AIS will ever explain any of them. "
               "The static-object layer must accumulate them and suppress them; "
               "if it does not, the top of the dark-contact queue is four "
               "mooring buoys and the queue is worthless.")))


SCENARIOS = (
    r1_coastal_dark_runner,
    r2_transponder_quits_under_cover,
    r3_inshore_transfer_one_silent,
    r4_subfloor_skiff,
    r5_konkan_coverage_hole,
    r6_fixed_installations,
)
