"""Group F — the factors that had no scenario.

Areas 2 and 3 of the IDEX Challenge 82 brief added three classes of factor to
the Vessel of Interest object: a **contradicted identity**, a **notable
activity**, and a **relationship between two hulls**. All three were built,
wired into the anomaly library, given a gate and a narration, and all three
fired **zero times** on this corpus.

Each zero had a cause, and none of the causes was the detector:

  * the generator minted every IMO checksum-valid and every MMSI inside a
    reserved block, so the arithmetic identity checks had nothing to catch;
  * it wrote one identity attestation per hull, so the consistency check had
    nothing to compare against and could only answer "cannot check";
  * no hull in the corpus ran a survey pattern or manoeuvred erratically once
    the thresholds were re-derived from measured populations;
  * and every rendezvous in the corpus is a *dark* rendezvous — the
    counterparties are silent by design, which is what makes them findings, and
    which means no pair of AIS-visible hulls ever holds a relationship long
    enough to be one.

The brief's own test is unambiguous: *"After each area lands, the ranked Vessel
of Interest list should visibly gain a new class of factor. If adding an area
does not change what appears on that list, the area was built in isolation."*

**The fix belongs here and not in the thresholds.** A rule that only fires once
it has been loosened has been fitted to the absence of evidence, and the
loosening is invisible afterwards — it looks like a working detector. Writing
the situation the rule was built for leaves the rule exactly as it was and makes
the measurement mean something.

**Writing the positives found four defects, none of them in a threshold** — a
reversal counted fix-to-fix that no real ship can execute between two fixes,
half of every cross-cell pair discarded before it was tested, a resampler that
deleted every stopped vessel, and a pipeline query that threw away the second
identity attestation the check needs. All four are recorded in ADR-034. Each
made a rule quieter and none of them said so, which is the shape of every defect
this project has found so far.

**And it moved two numbers that were never as solid as they looked.** The
interaction persistence floor was re-derived on one corpus draw and falsified by
the next; separation, not duration, turned out to be what separates a formation
from traffic. And dark-contact recall — reported at 86% before this group
existed — reads 43% on this draw and 62% on the next seed, with precision at
100% throughout. Adding sixteen hulls to the cast shifts the generator's RNG
stream, so every scenario's noise is a fresh sample; a single-variable A/B
confirmed none of the detector changes here is responsible. A recall figure with
a denominator of seven episodes was never a capability measurement, and is now
described as what it is.

  F1   an IMO that fails its own check digit — pure arithmetic, no judgement
  F2   a call sign the registry does not hold for her
  F3   a name that is not the registered one
  F4   DECOY: the registry spells her name "M.V. X." — the same hull
  F5   DECOY: registry says bulker, she says general cargo — two records
       honestly disagreeing about one hull, which is not a lie
  F6   a genuine lawnmower survey pattern
  F7   turning far more than passage requires, going nowhere
  F8   DECOY: a there-and-back coastal rotation — long legs, reciprocal turns,
       and emphatically not a survey. This is the exact false positive that
       claimed 151 of 209 tracks before the rule was fixed; it stays in the
       corpus so it cannot come back unnoticed.
  F9   two hulls moving in company for eleven hours
  F10  one hull sitting dead astern of another for nine
  F11  a ship-to-ship transfer with **both** parties transmitting
  F12  DECOY: two hulls sharing a coastal lane — same course, wandering gap.
       Traffic, not a formation.

F1-F5 are authored in `cast.py`, because an identity is minted with the hull
rather than acted out over time; this module carries their truth rows so the
whole group is measurable from one place.

Every hull here is new and unshared, so placement is unconstrained and the
existing temporal choreography is untouched.
"""
from __future__ import annotations

from datetime import timedelta

from ..cast import REGISTRY_DISAGREEMENTS
from ..geography import destination, haversine_m, initial_bearing_deg
from ..primitives.track import Leg, TrackPoint, VoyagePlan, generate_track
from ..validate import RULE_IDENTIFIERS
from ..truth import (DECOY, FAMILY_BEHAVIOURAL, FAMILY_DECOY, FAMILY_IDENTITY,
                     TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V, declare_voyage, emit

#: Open water off **Karnataka**, clear of every port and anchorage in the
#: gazetteer and inside modelled terrestrial AIS reception.
#:
#: Two conditions, and both were got wrong once.
#:
#: **In coverage.** The first draft put these hulls in the deep basins where the
#: existing transfer scenarios live, 400 km out, and the corpus validator
#: refused the whole generation — *"no participating entity landed any AIS
#: report ... the scenario has no observable evidence at all and would be scored
#: as a detector miss when the corpus never contained anything to find"*. Quite
#: right. A relationship between two vessels nobody can hear is a different
#: scenario, and this project already has it (group R, and ADR-005 on why
#: offshore silence is `unknown` rather than dark).
#:
#: **Out of another scenario's water.** The second draft moved them inshore and
#: put sixteen new AIS-transmitting hulls along the Saurashtra coast — which is
#: exactly where R1's `coast_runner` works with no transponder at all.
#: Dark-contact recall fell from 86% to 71% overnight, and the cause was not any
#: detector: the dark cascade suppresses a radar contact that has a plausible
#: AIS neighbour, so denser legitimate traffic makes a dark vessel genuinely
#: harder to find. That is a true property of the detector and an interesting
#: one, but it is not a finding when it arrives as a side effect of where a
#: dozen unrelated ships were parked. **A change written to test three factor
#: classes must not move an unrelated headline number**, or neither number can
#: be read afterwards. So group F works the Karnataka coast, 700 km from the
#: Saurashtra radar scenarios, and the traffic-density effect is left to be
#: measured deliberately rather than stumbled into.
_SURVEY_BOX = (13.55, 74.10)
_ERRATIC_BOX = (13.22, 73.98)
_COMPANY_LEGS = ((14.35, 73.70), (13.75, 74.10), (13.10, 74.55))
_SHADOW_LEGS = ((14.20, 73.95), (13.60, 74.30), (13.02, 74.68))
_TRANSFER_APPROACH = (14.10, 73.70)
_TRANSFER_HOLD = (13.45, 74.28)
_LANE_LEGS = ((14.25, 74.00), (13.60, 74.40), (13.05, 74.72))
_ROTATION_ENDS = ((14.30, 73.85), (13.05, 74.60))


# ==========================================================================
# F1-F5 — identity. Authored in cast.py; recorded here.
# ==========================================================================

def f1_to_f5_identity_attestations(world: ScenarioWorld) -> None:
    """Truth rows for the five identity hulls, and a track to hang them on.

    Each of these hulls needs *some* motion or she is a vessel that exists only
    as a row, which is not a vessel — the graph populator would never publish a
    node for her and the alert would land on a subject nothing else references.
    So each makes an ordinary coastal passage: unremarkable by construction,
    because the finding is in her paperwork and must not be confounded by her
    behaviour.
    """
    r = world.rng
    t0 = week(2, hours=3)
    starts = {
        "bad_imo_hull": ((14.40, 73.60), (13.00, 74.70)),
        "wrong_call_sign": ((13.00, 74.10), (14.45, 73.95)),
        "registry_renamed": ((14.30, 73.65), (13.10, 74.50)),
        "punctuation_twin": ((13.05, 74.55), (14.35, 73.80)),
        "class_quibble": ((14.15, 73.75), (13.02, 74.62)),
    }
    for i, (key, (start, end)) in enumerate(starts.items()):
        v = V(world, key)
        mid = ((start[0] + end[0]) / 2.0 + 0.35,
               (start[1] + end[1]) / 2.0 - 0.35)
        pts = generate_track(v, VoyagePlan(
            start=start, start_time=t0 + timedelta(hours=7 * i),
            legs=[Leg("transit", target=mid, speed_kn=11.5),
                  Leg("transit", target=end, speed_kn=11.5)]), r)
        emit(world, key, pts)

    _identity_truth(world, "F1", "bad_imo_hull", TRUE_ANOMALY,
                    ["identity_contradiction"],
                    "Broadcasts her own IMO with one digit wrong. The check "
                    "digit is arithmetic: it fails or it does not, and this "
                    "one fails. The registry entry carries the correct "
                    "number, so she also contradicts her own record.",
                    # The corpus validator otherwise refuses a synthetic IMO
                    # that fails its own checksum, and is right to: everywhere
                    # else that is a generator bug. Declared here by scenario
                    # id and rule name, the same narrow gate C3 uses for its
                    # impossible speed.
                    physics_exemption=RULE_IDENTIFIERS)
    _identity_truth(world, "F2", "wrong_call_sign", TRUE_ANOMALY,
                    ["identity_contradiction"],
                    "The registry holds "
                    f"{REGISTRY_DISAGREEMENTS['wrong_call_sign']['call_sign']}"
                    " for this hull and the air says something else. A call "
                    "sign is issued with the flag and changes only when the "
                    "flag does, so a disagreement is a strong signal.")
    _identity_truth(world, "F3", "registry_renamed", TRUE_ANOMALY,
                    ["identity_contradiction"],
                    "Broadcasts a name the registry does not hold. Weak on its "
                    "own — registries lag a sale by months — which is why it "
                    "is scored as a lead rather than a finding.")
    _identity_truth(world, "F4", "punctuation_twin", DECOY, [],
                    "The registry spells her name with a prefix and a full "
                    "stop. Identical hull, identical name, different "
                    "punctuation. A check that fires here fires on a third of "
                    "the fleet.")
    _identity_truth(world, "F5", "class_quibble", DECOY, [],
                    "Registry says bulker, she broadcasts general cargo. Two "
                    "records honestly disagreeing about one hull — the "
                    "weakest of the three fields for exactly this reason.")


def _identity_truth(world: ScenarioWorld, sid: str, key: str, cls: str,
                    types: list[str], notes: str, *,
                    physics_exemption: str = "") -> None:
    v = V(world, key)
    track = world.track_of(v.entity_id)
    world.truth.add(ScenarioTruth(
        scenario_id=sid,
        scenario_family=(FAMILY_IDENTITY if cls == TRUE_ANOMALY
                         else FAMILY_DECOY),
        physics_exemption=physics_exemption,
        truth_class=cls, entity_ids=[v.entity_id],
        t_start=track[0].t, t_end=track[-1].t,
        expected_detection=(cls == TRUE_ANOMALY),
        expected_anomaly_types=types, notes=notes))


# ==========================================================================
# F6 — a genuine survey pattern
# ==========================================================================

def f6_lawnmower_survey(world: ScenarioWorld) -> None:
    """Ten parallel legs, each reversed on the last, covering a box.

    This is what the survey rule was written for and what the corpus did not
    contain. The signature is not "has some reciprocal turns" — every
    there-and-back rotation has those, which is how the first version of the
    rule claimed 151 of 209 tracks. It is *made of* reciprocal turns and ends
    up roughly where it started: a lawnmower covers an area rather than
    crossing one.

    Ten legs of 4 nm at 7 knots is 34 minutes a leg, comfortably past the
    twelve-minute floor, with the whole pattern inside six hours — so the
    legs-per-hour gate is met by a wide margin and the straightness is about
    an eighth, because 40 nm of steaming nets 2.5 nm of displacement.
    """
    r = world.rng
    v = V(world, "survey_runner")
    t0 = week(4, hours=6)
    lat0, lon0 = _SURVEY_BOX

    legs = []
    for i in range(10):
        # Alternate east and west; step 0.3 nm north between passes.
        row_lat = lat0 + i * (0.3 / 60.0)
        east = i % 2 == 0
        anchor_lon = lon0 if east else lon0 + 0.075
        legs.append(Leg("transit",
                        target=destination(row_lat, anchor_lon,
                                           90.0 if east else 270.0,
                                           4.0 * 1852.0),
                        speed_kn=7.0))

    pts = generate_track(v, VoyagePlan(
        start=(lat0, lon0), start_time=t0, legs=legs), r)
    emit(world, "survey_runner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="F6", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["notable_activity"],
        notes=("Ten parallel legs joined by reciprocal turns, covering a box "
               "rather than crossing it. The activity rule's survey_pattern "
               "branch had no positive case in the corpus before this.")))


# ==========================================================================
# F7 — manoeuvring erratically
# ==========================================================================

def f7_erratic_manoeuvring(world: ScenarioWorld) -> None:
    """Fourteen hours at nine knots, turning constantly, arriving nowhere.

    Deliberately *not* a fishing signature: she is well above trawling speed, so
    the fishing branch cannot claim her. And deliberately not a survey either —
    the first draft alternated a fixed pair of course changes and produced a
    lawnmower by accident, which the survey rule correctly claimed. Real erratic
    manoeuvring has no period: the alterations here vary in size and in
    direction, so consecutive legs are not reciprocal and no pattern emerges
    from them.

    Fourteen hours because the classifier's confidence saturates at twelve, and
    the notable-activity gate sits just above what a shorter look can support.
    That is the gate working: a two-hour glimpse of a vessel milling about is
    not worth an operator's attention, and this one is.
    """
    r = world.rng
    v = V(world, "erratic_runner")
    t0 = week(4, hours=15)
    centre = _ERRATIC_BOX

    # A deterministic bearing walk with no period, herded back toward the box
    # whenever she strays. Written out rather than drawn from `world.rng` so
    # the shape does not move when an unrelated scenario is added upstream.
    legs = []
    brg, lat, lon = 40.0, centre[0], centre[1]
    steps = (73.0, -128.0, 61.0, 96.0, -154.0, 88.0, -67.0, 141.0,
             -83.0, 112.0, 59.0, -137.0, 104.0, -71.0, 126.0, -95.0)
    for i in range(64):
        brg = (brg + steps[i % len(steps)] + (i % 5) * 7.0) % 360.0
        # Herd home: past six miles out, the next leg points back.
        if haversine_m(lat, lon, *centre) > 11_000.0:
            brg = initial_bearing_deg(lat, lon, *centre)
        run_m = (1.15 + 0.25 * (i % 3)) * 1852.0
        lat, lon = destination(lat, lon, brg, run_m)
        legs.append(Leg("transit", target=(lat, lon), speed_kn=9.0))

    pts = generate_track(v, VoyagePlan(
        start=centre, start_time=t0, legs=legs), r)
    emit(world, "erratic_runner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="F7", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["notable_activity"],
        notes=("Sixty-four alterations at nine knots inside six miles of "
               "water, with no period to them. Above trawling speed so the "
               "fishing branch cannot claim her, and aperiodic so the survey "
               "branch cannot either.")))


# ==========================================================================
# F8 — DECOY: the rotation that is not a survey
# ==========================================================================

def f8_rotation_not_a_survey(world: ScenarioWorld) -> None:
    """Six long legs and five reciprocal turns — and a liner on her rotation.

    **This decoy is the memory of a fixed bug.** The first survey rule asked
    for four long legs and three near-reciprocal turns and got 151 of 209
    tracks, because a there-and-back coastal rotation supplies both in
    abundance. Keeping the shape in the corpus means the regression is caught
    by a measurement rather than by somebody remembering.

    She goes somewhere: 300 nm down the coast and back, which is why her
    straightness is nothing like a lawnmower's even though her turn history
    looks similar.
    """
    r = world.rng
    v = V(world, "rotation_liner")
    t0 = week(5, hours=2)

    north, south = _ROTATION_ENDS
    legs = []
    for i in range(6):
        legs.append(Leg("transit", target=(south if i % 2 == 0 else north),
                        speed_kn=12.0))
    pts = generate_track(v, VoyagePlan(
        start=north, start_time=t0, legs=legs), r)
    emit(world, "rotation_liner", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="F8", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False, expected_anomaly_types=[],
        notes=("Long legs and reciprocal turns, and a coastal rotation. The "
               "false positive the first survey rule made 151 times.")))


# ==========================================================================
# F9-F12 — relationships between two hulls
# ==========================================================================

def _offset_track(points: list[TrackPoint], *, bearing_off_course: float,
                  metres: float) -> list[TrackPoint]:
    """A second hull holding station on the first, at a fixed relative bearing.

    A formation is a rigid translation of one track onto another: same course,
    same speed, constant gap. Deriving the consort from the leader rather than
    steering her to a rendezvous is what makes the separation *stable* — a
    second integrated track wanders by a few hundred metres, which is exactly
    the coefficient of variation the interaction rule uses to tell a formation
    from two ships that happen to share a lane.
    """
    out: list[TrackPoint] = []
    for p in points:
        lat, lon = destination(p.lat, p.lon,
                               (p.cog_deg + bearing_off_course) % 360.0, metres)
        out.append(TrackPoint(p.t, lat, lon, p.sog_kn, p.cog_deg, p.nav_status))
    return out


def _delayed_track(points: list[TrackPoint], *, minutes: float
                   ) -> list[TrackPoint]:
    """A second hull steering the leader's exact track, `minutes` behind.

    Which is what following actually is. The separation is speed times delay,
    the bearing from leader to follower is dead astern by construction, and
    neither is asserted by the generator — both fall out of the geometry, so
    the detector is measuring a relationship rather than a label.
    """
    shift = timedelta(minutes=minutes)
    return [TrackPoint(p.t + shift, p.lat, p.lon, p.sog_kn, p.cog_deg,
                       p.nav_status)
            for p in points]


def f9_moving_in_company(world: ScenarioWorld) -> None:
    """Eleven hours on one course, 1.2 km apart, gap never moving."""
    r = world.rng
    lead = V(world, "company_leader")
    t0 = week(3, hours=5)

    start, *waypoints = _COMPANY_LEGS
    legs = [Leg("transit", target=w, speed_kn=11.0) for w in waypoints]
    a = generate_track(lead, VoyagePlan(
        start=start, start_time=t0, legs=legs), r)
    b = _offset_track(a, bearing_off_course=115.0, metres=1200.0)
    emit(world, "company_leader", a)
    emit(world, "company_escort", b)

    world.truth.add(ScenarioTruth(
        scenario_id="F9", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[lead.entity_id, V(world, "company_escort").entity_id],
        t_start=t0, t_end=a[-1].t, expected_detection=True,
        expected_anomaly_types=["vessel_interaction"],
        notes=("Two hulls on one course holding 1.2 km for eleven hours in "
               "open water. Both transmitting, so this is the AIS-visible "
               "formation the corpus lacked.")))


def f10_shadowing(world: ScenarioWorld) -> None:
    """Nine hours dead astern at two and a half kilometres."""
    r = world.rng
    tgt = V(world, "shadow_target")
    t0 = week(6, hours=8)

    start, *waypoints = _SHADOW_LEGS
    legs = [Leg("transit", target=w, speed_kn=10.5) for w in waypoints]
    a = generate_track(tgt, VoyagePlan(
        start=start, start_time=t0, legs=legs), r)
    # 14 minutes at 10.5 kn is about 4.5 km — inside the five-mile ceiling,
    # far outside "alongside", and unmistakably astern.
    b = _delayed_track(a, minutes=14.0)
    emit(world, "shadow_target", a)
    emit(world, "shadow_follower", b)

    world.truth.add(ScenarioTruth(
        scenario_id="F10", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[tgt.entity_id, V(world, "shadow_follower").entity_id],
        t_start=t0, t_end=a[-1].t, expected_detection=True,
        expected_anomaly_types=["vessel_interaction"],
        notes=("One hull steering the other's track fourteen minutes behind. "
               "The astern bearing is not asserted — it falls out of "
               "following, which is the point.")))


def f11_transfer_both_transmitting(world: ScenarioWorld) -> None:
    """Alongside at 300 m, both stopped, nine hours, in open water.

    **The case this corpus could not supply.** Every transfer already written
    has a dark counterparty — that silence is what makes those scenarios
    findings — so the transfer branch of the interaction rule had no positive
    to be measured against and was, honestly, untested. A transfer between two
    transmitting hulls is not thereby innocent: it is the one an operator can
    actually see, and seeing it is the capability.

    **Each hull steams her own approach, from a different direction.** The first
    draft derived the second track as a rigid offset of the first, which put the
    pair 300 m apart for the whole five-hour run-in as well as the hold — and
    the interaction rule then read the *median* speed over that run as nine
    knots and called the pair a formation. It was right to: a pair that is
    close and moving is not a transfer. Converging from opposite quarters means
    the sustained proximity begins when they stop, which is the behaviour being
    claimed.
    """
    r = world.rng
    a_v, b_v = V(world, "transfer_open_a"), V(world, "transfer_open_b")
    t0 = week(6, hours=20)

    hold_b = destination(_TRANSFER_HOLD[0], _TRANSFER_HOLD[1], 90.0, 300.0)
    a = generate_track(a_v, VoyagePlan(
        start=_TRANSFER_APPROACH, start_time=t0,
        legs=[Leg("transit", target=_TRANSFER_HOLD, speed_kn=10.0),
              Leg("station", duration_h=9.0, radius_m=110.0)]), r)
    b = generate_track(b_v, VoyagePlan(
        start=destination(_TRANSFER_HOLD[0], _TRANSFER_HOLD[1], 200.0, 55_000.0),
        start_time=t0 + timedelta(hours=2.0),
        legs=[Leg("transit", target=hold_b, speed_kn=9.0),
              Leg("station", duration_h=9.0, radius_m=110.0)]), r)
    emit(world, "transfer_open_a", a)
    emit(world, "transfer_open_b", b)

    world.truth.add(ScenarioTruth(
        scenario_id="F11", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY,
        entity_ids=[a_v.entity_id, b_v.entity_id],
        t_start=t0, t_end=a[-1].t, expected_detection=True,
        expected_anomaly_types=["vessel_interaction"],
        notes=("300 m apart, both under 2 knots, nine hours, clear of every "
               "berth and anchorage. The transfer branch had no positive case "
               "in the corpus before this — both parties to every other "
               "transfer here are dark by design.")))


def f12_lane_traffic_decoy(world: ScenarioWorld) -> None:
    """Two hulls in the coastal lane, same course, gap wandering.

    The reason the interaction rule has a separation-stability test at all.
    Ships on a route are near each other for hours; a formation *holds* its gap.
    Here the second hull runs the same lane a knot and a half faster and then a
    knot slower, so she closes from four kilometres to under two and opens back
    out again — a slow overtake, which is the commonest thing on this coast and
    must never read as a relationship.

    **She has to stay inside the interaction rule's reach to be a decoy at
    all.** The first draft put the pair sixteen kilometres apart, where the
    candidate filter drops them before any test runs — a pair that was never
    considered proves nothing about the tests that would have considered it.
    """
    r = world.rng
    a_v, b_v = V(world, "lane_mate_a"), V(world, "lane_mate_b")
    t0 = week(2, hours=17)

    lane_start, *lane_waypoints = _LANE_LEGS
    a = generate_track(a_v, VoyagePlan(
        start=lane_start, start_time=t0,
        legs=[Leg("transit", target=w, speed_kn=12.0)
              for w in lane_waypoints]), r)

    # Her own passage down the same lane, two miles off the beam and making her
    # own speed. Integrated separately on purpose: two hulls steering the same
    # route is the null hypothesis, and it has to be generated as two hulls.
    start_b = destination(lane_start[0], lane_start[1], 250.0, 3700.0)
    legs_b = [Leg("transit", target=destination(w[0], w[1], 250.0, 3700.0),
                  speed_kn=speed)
              for w, speed in zip(lane_waypoints, (13.6, 10.9))]
    b = generate_track(b_v, VoyagePlan(
        start=start_b, start_time=t0 + timedelta(minutes=6), legs=legs_b), r)
    emit(world, "lane_mate_a", a)
    emit(world, "lane_mate_b", b)

    world.truth.add(ScenarioTruth(
        scenario_id="F12", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[a_v.entity_id, b_v.entity_id],
        t_start=t0, t_end=a[-1].t, expected_detection=False,
        expected_anomaly_types=[],
        notes=("Same lane, same course, different speeds — a slow overtake "
               "inside the interaction rule's reach, with the gap swinging by "
               "kilometres. Two ships on a route, which is most of the "
               "traffic on this coast.")))


# ==========================================================================
# F13-F15 — the voyage she declares against the voyage she makes (ADR-035)
# ==========================================================================

def f13_declares_one_port_and_steams_for_another(world: ScenarioWorld) -> None:
    """Declares Kandla, then runs south-west for two days and never turns.

    The brief calls this "one of the strongest and simplest suspicion factors
    available", and until AIS message 5 was landed the system had no column to
    read it from.

    **The test is "was she ever heading there", not "did she arrive".** A vessel
    diverted mid-passage is ordinary and happens to F15 in this same group; a
    vessel that declared a port and never once pointed at it is making a
    statement that her own track contradicts from the first hour.
    """
    from ...ports import PORTS
    r = world.rng
    v = V(world, "false_destination")
    t0 = week(3, hours=11)

    # She starts off Karnataka. Kandla is 1,100 km north-west. She goes south.
    start = (14.30, 73.75)
    legs = [Leg("transit", target=(13.40, 73.30), speed_kn=11.5),
            Leg("transit", target=(12.30, 73.60), speed_kn=11.5),
            Leg("transit", target=(11.20, 74.20), speed_kn=11.0)]
    pts = generate_track(v, VoyagePlan(
        start=start, start_time=t0, legs=legs), r)
    emit(world, "false_destination", pts)

    # An ETA that is *achievable* for the distance, so the arithmetic check
    # stays silent and only the behavioural one fires. Two findings on one hull
    # would prove nothing about which rule found her.
    declare_voyage(world, "false_destination", destination="Kandla",
                   eta=pts[-1].t + timedelta(hours=60),
                   t_start=t0, t_end=pts[-1].t)

    world.truth.add(ScenarioTruth(
        scenario_id="F13", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["voyage_contradiction"],
        notes=("Declares Kandla, 1,100 km north-west, and steams south-west "
               "for the whole passage. Her declared ETA is achievable, so only "
               "the behavioural check should fire — the arithmetic one has "
               "nothing to catch.")))
    assert PORTS["Kandla"][0] > start[0], "Kandla must be north of her start"


def f14_declares_an_arrival_no_hull_could_make(world: ScenarioWorld) -> None:
    """Off Karnataka, declaring Kandla in nine hours. That needs 130 knots.

    Pure arithmetic, like the IMO check digit: distance over time against a
    hull-speed ceiling, with no judgement in it except a routing margin taken
    in the direction that avoids accusing an honest vessel.

    A mistyped ETA is a common and innocent thing, which is exactly why the
    finding is scored as a lead and not a verdict — but a vessel whose paperwork
    cannot be true is worth a watchkeeper's minute.
    """
    r = world.rng
    v = V(world, "impossible_eta")
    t0 = week(5, hours=6)

    legs = [Leg("transit", target=(14.05, 73.55), speed_kn=12.0),
            Leg("transit", target=(13.35, 74.05), speed_kn=12.0)]
    pts = generate_track(v, VoyagePlan(
        start=(14.60, 73.35), start_time=t0, legs=legs), r)
    emit(world, "impossible_eta", pts)

    # She *is* heading roughly the right way for a coastal passage, so the
    # behavioural check has no quarrel with her. The arithmetic one does.
    declare_voyage(world, "impossible_eta", destination="Kandla",
                   eta=t0 + timedelta(hours=9),
                   t_start=t0, t_end=pts[-1].t)

    world.truth.add(ScenarioTruth(
        scenario_id="F14", scenario_family=FAMILY_BEHAVIOURAL,
        truth_class=TRUE_ANOMALY, entity_ids=[v.entity_id],
        t_start=t0, t_end=pts[-1].t, expected_detection=True,
        expected_anomaly_types=["voyage_contradiction"],
        notes=("Declares Kandla in nine hours from 1,100 km away. No hull in "
               "this traffic makes 130 knots; the declaration cannot be true "
               "whatever her intentions are.")))


def f15_diverted_honestly(world: ScenarioWorld) -> None:
    """DECOY: declares Mangalore, is sent to Mormugao halfway, says so.

    Orders change. A charterer re-fixes a cargo, a berth falls through, a
    weather routing service sends her round. The vessel updates her declaration
    and carries on, and **none of that is deceit** — which is why the rule tests
    whether she was *ever* heading to the port she named rather than whether she
    ended up there.

    This decoy is the reason that distinction exists in the rule at all. Without
    it "declared destination does not match arrival port" looks like a perfectly
    good detector, and it would fire on a large fraction of honest commercial
    traffic.
    """
    r = world.rng
    v = V(world, "diverted_honestly")
    t0 = week(6, hours=4)

    # South towards Mangalore for the first half — genuinely heading there.
    first = generate_track(v, VoyagePlan(
        start=(14.60, 73.40), start_time=t0,
        legs=[Leg("transit", target=(13.60, 74.10), speed_kn=11.0),
              Leg("transit", target=(13.10, 74.55), speed_kn=11.0)]), r)
    emit(world, "diverted_honestly", first)
    declare_voyage(world, "diverted_honestly", destination="Mangalore",
                   eta=first[-1].t + timedelta(hours=8),
                   t_start=t0, t_end=first[-1].t)

    # Then the diversion, declared as soon as it happens.
    second = generate_track(v, VoyagePlan(
        start=(first[-1].lat, first[-1].lon),
        start_time=first[-1].t + timedelta(minutes=30),
        initial_course_deg=first[-1].cog_deg,
        initial_sog_kn=first[-1].sog_kn,
        legs=[Leg("transit", target=(14.20, 73.60), speed_kn=11.5),
              Leg("transit", target=(15.10, 73.35), speed_kn=11.5)]), r)
    emit(world, "diverted_honestly", second)
    declare_voyage(world, "diverted_honestly", destination="Mormugao",
                   eta=second[-1].t + timedelta(hours=6),
                   t_start=second[0].t, t_end=second[-1].t)

    world.truth.add(ScenarioTruth(
        scenario_id="F15", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=t0, t_end=second[-1].t,
        expected_detection=False, expected_anomaly_types=[],
        notes=("Declared Mangalore and was genuinely heading there, then was "
               "re-fixed to Mormugao and said so. Two declarations, both "
               "honest at the moment they were made.")))


#: Placed after the identity hulls so the truth ledger reads in F order.
SCENARIOS = (
    f1_to_f5_identity_attestations,
    f6_lawnmower_survey,
    f7_erratic_manoeuvring,
    f8_rotation_not_a_survey,
    f9_moving_in_company,
    f10_shadowing,
    f11_transfer_both_transmitting,
    f12_lane_traffic_decoy,
    f13_declares_one_port_and_steams_for_another,
    f14_declares_an_arrival_no_hull_could_make,
    f15_diverted_honestly,
)
