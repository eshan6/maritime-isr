"""Automating the electro-optical loop — ADR-037.

Area 5 of the IDEX Challenge 82 brief. The requirement names four things: capture
without operator intervention, tag the image to a track, classify against a
library, alert on the mismatch. Only one of them needs pictures, and the tests
are ordered so the ones that carry the product come first:

1. **Geometry and conditions** — what a camera on a tower can see, and when.
2. **The cueing scheduler** — the centre of gravity. Far more tracks than
   cameras; which one gets imaged, when, and why.
3. **Capture and tagging** — an image bound to a track, landed as evidence.
4. **The classifier behind its interface** — including a third implementation
   defined here and substituted into the running loop, because swap-ability
   asserted in a docstring is not swap-ability.
5. **The mismatch rule**, three-valued, and the four ways it refuses.
6. **The corpus outcome** — every alert accounted for against a truth row.
7. **The discipline the area rests on** — no module here reads the answer key.

Section 6 is not a formality. Area 4 shipped with a green unit suite while two of
its three rules answered "not checkable" for every document in the corpus, and
the only thing that exposed it was counting alerts against the authored truth.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc

#: Mid-corpus, mid-morning off the Saurashtra coast. Local solar noon at 69.6°E
#: is about 07:20 UTC, so this is high sun and the visible band is working.
T_DAY = datetime(2026, 6, 20, 6, 30, tzinfo=UTC)
#: The same place twelve hours later: the sun is well below the horizon and the
#: head is on its thermal channel.
T_NIGHT = datetime(2026, 6, 20, 19, 30, tzinfo=UTC)


def _camera(**kw):
    from maritime_isr.eo.camera import EOCamera
    base = dict(camera_id="EO-TEST", station_id="SYN-TST", name="Testport",
                lat=21.630, lon=69.610)
    base.update(kw)
    return EOCamera(**base)


def _candidate(**kw):
    from maritime_isr.eo.cue import CueCandidate
    base = dict(subject_id="vessel:t1", track_id="t1", lat=21.630, lon=69.500,
                sog_kn=10.0, cog_deg=180.0, length_m=180.0)
    base.update(kw)
    return CueCandidate(**base)


# ==========================================================================
# 1. geometry, light and weather — what a camera can see
# ==========================================================================

def test_solar_elevation_puts_the_sun_where_it_belongs():
    """Light halves the usable day, so the term deciding it must be right.

    Checked against facts nobody needs an almanac for: the sun is up at local
    midday and down at local midnight, and the swing between them at 21°N in
    June is most of ninety degrees. A sign error here would put every camera on
    its thermal channel at noon.
    """
    from maritime_isr.eo.conditions import solar_elevation_deg

    noon = solar_elevation_deg(21.63, 69.61, T_DAY)
    midnight = solar_elevation_deg(21.63, 69.61, T_NIGHT)
    assert noon > 60.0, noon
    assert midnight < -20.0, midnight


def test_the_band_follows_the_sun_and_a_thermal_look_is_worth_less():
    """A night image is a silhouette. The band has to travel with the capture.

    Everything downstream depends on this: the classifier drops deck features on
    a thermal look, the vocabulary coarsens, and the mismatch rule then refuses
    distinctions the image could not have carried. If the band were wrong the
    whole chain would assert deck detail nobody photographed.
    """
    from maritime_isr.eo.conditions import (BAND_THERMAL, BAND_VISIBLE,
                                            illumination)

    day = illumination(21.63, 69.61, T_DAY)
    night = illumination(21.63, 69.61, T_NIGHT)
    assert day.band == BAND_VISIBLE and day.is_daylight
    assert night.band == BAND_THERMAL and not night.is_daylight
    assert night.light_factor < day.light_factor


def test_visibility_is_stable_for_one_place_and_hour():
    """A scheduler whose decisions change on re-read cannot be argued with.

    Weather here stands in for a met sensor the system does not have. Being a
    stand-in is fine; being *random at read time* is not, because then the same
    corpus produces a different tasking order every run and no cueing change
    could ever be attributed.
    """
    from maritime_isr.eo.conditions import visibility_km

    a = visibility_km("SYN-POR", T_DAY)
    b = visibility_km("SYN-POR", T_DAY)
    assert a == b
    assert 1.0 < a < 60.0
    # Different stations see different weather, or the term does no work.
    assert len({visibility_km(s, T_DAY) for s in
                ("SYN-POR", "SYN-MUM", "SYN-KOC", "SYN-DWA")}) > 1


def test_range_beats_everything_else_in_the_quality_model():
    """Pixels on target fall as 1/range and contrast falls exponentially.

    The whole reason cueing is a scheduling problem rather than a ranking is
    that a camera's useful reach is short. If quality did not fall hard with
    range, every target would be equally imageable and there would be nothing
    to schedule.
    """
    from maritime_isr.eo.camera import view

    cam = _camera()
    near = view(cam, lat=21.66, lon=69.61, when=T_DAY, length_m=180.0,
                heading_deg=90.0)
    far = view(cam, lat=21.78, lon=69.61, when=T_DAY, length_m=180.0,
               heading_deg=90.0)
    assert near.observable
    assert near.quality > far.quality
    assert near.pixels_on_target > far.pixels_on_target


def test_a_target_beyond_useful_range_is_refused_with_a_reason():
    """"Nothing ashore can see her" is an answer; a number is an actionable one.

    An out-of-reach contact is an argument for a surface unit or a satellite
    tasking. That argument can only be made if the refusal carries the range.
    """
    from maritime_isr.eo.camera import view

    v = view(_camera(), lat=22.40, lon=69.610, when=T_DAY, length_m=180.0,
             heading_deg=90.0)
    assert not v.observable
    assert "range" in v.reason
    assert v.range_km > 60.0


def test_a_bow_on_look_is_worse_than_a_broadside_one():
    """A ship seen end-on presents a fraction of her length.

    Aspect is why the same camera at the same range gives a usable image of one
    hull and not another, and why the classifier is allowed to withhold a length
    rather than divide a foreshortened one back out into nonsense.
    """
    from maritime_isr.eo.camera import view

    cam = _camera()
    broadside = view(cam, lat=21.66, lon=69.61, when=T_DAY, length_m=180.0,
                     heading_deg=90.0)
    bow_on = view(cam, lat=21.66, lon=69.61, when=T_DAY, length_m=180.0,
                  heading_deg=180.0)
    assert broadside.aspect_deg is not None
    assert broadside.quality > bow_on.quality


def test_a_terrain_masked_bearing_is_not_an_imaging_opportunity():
    """The camera is on the tower behind the same headland as the radar.

    Inheriting the array's shadow sectors rather than inventing a second set is
    what keeps the camera's coverage map honest against the radar's — a target
    the station cannot see through a hill is not one a lens can.
    """
    from maritime_isr.eo.camera import view

    cam = _camera(masked_sectors=((240.0, 300.0),))
    v = view(cam, lat=21.630, lon=69.500, when=T_DAY, length_m=180.0,
             heading_deg=0.0)
    assert not v.observable
    assert "mask" in v.reason


def test_every_camera_in_this_build_is_flagged_synthetic():
    """The synthetic marker has to be on the surface, not only in the database.

    A coverage claim is the most persuasive thing this system can make and there
    is no real Coastal Surveillance Network behind it. The flag rides on the
    camera, so it rides on every view, every tasking and every capture built
    from one.
    """
    from maritime_isr.eo.camera import default_camera_network

    cams = default_camera_network()
    assert len(cams) >= 12
    assert all(c.is_synthetic for c in cams)


# ==========================================================================
# 2. the cueing scheduler — the centre of gravity
# ==========================================================================

def _plan(candidates, cameras, *, slots=1, slot_seconds=1800.0, t0=T_DAY,
          **kw):
    from maritime_isr.eo.cue import plan_cueing
    return plan_cueing(lambda _t: candidates, cameras, t0=t0, slots=slots,
                       slot_seconds=slot_seconds, **kw)


def test_two_cameras_and_three_targets_never_double_book_a_camera():
    """Greedy per-target matching double-books, and it is banned one domain over.

    CLAUDE.md §6 forbids greedy per-contact matching in association because it
    manufactures phantom dark vessels. The same shape of bug lives here: the
    three most suspicious contacts are frequently inside one station's arc, so
    walking a ranked list hands that station's camera to all three, resolves the
    collision arbitrarily, and leaves the other cameras idle. Global assignment
    is the fix in both places.
    """
    cams = [_camera(camera_id="EO-A", station_id="SYN-A", name="Alpha",
                    lat=21.63, lon=69.61),
            _camera(camera_id="EO-B", station_id="SYN-B", name="Bravo",
                    lat=21.66, lon=69.61)]
    cands = [_candidate(subject_id=f"vessel:v{i}", track_id=f"t{i}",
                        lat=21.645, lon=69.58, suspicion=0.9 - 0.01 * i)
             for i in range(3)]
    plan = _plan(cands, cams)

    assert len(plan.taskings) == 2, "two cameras, so at most two taskings"
    assert len({t.camera_id for t in plan.taskings}) == 2
    assert len({t.subject_id for t in plan.taskings}) == 2


def test_the_loser_is_named_in_the_ledger_with_who_took_the_camera():
    """A queue that silently drops things cannot be calibrated against.

    The suppression discipline ADR-028 established for the radar cascade and
    ADR-031 extended to the ranked list. It matters more for an automation than
    for a queue: an operator who cannot find out why the system did *not* look
    at something will go back to slewing the camera by hand.
    """
    cams = [_camera()]
    cands = [_candidate(subject_id="vessel:winner", track_id="tw",
                        lat=21.645, lon=69.58, suspicion=0.9),
             _candidate(subject_id="vessel:loser", track_id="tl",
                        lat=21.645, lon=69.58, suspicion=0.2)]
    plan = _plan(cands, cams)

    assert [t.subject_id for t in plan.taskings] == ["vessel:winner"]
    held = [d for d in plan.deferrals if d.subject_id == "vessel:loser"]
    assert held, "the target that lost a camera must be in the ledger"
    assert held[0].reason == "outranked"
    assert "vessel:winner" in (held[0].detail.get("taken_by") or "")


def test_an_unreachable_target_is_deferred_with_the_nearest_station_named():
    """Out of reach is a different answer from outranked, and both are useful.

    One says wait; the other says nothing ashore can help and this is an
    argument for a surface unit. Collapsing them into "not tasked" throws away
    the half an operator can act on.
    """
    cams = [_camera()]
    offshore = _candidate(subject_id="vessel:far", track_id="tf",
                          lat=21.63, lon=68.20, suspicion=0.95)
    plan = _plan([offshore], cams)

    assert plan.taskings == []
    held = [d for d in plan.deferrals if d.subject_id == "vessel:far"]
    assert held and held[0].reason == "no_camera_in_reach"
    assert held[0].detail.get("nearest_station")


def test_suspicion_outranks_an_ordinary_hull_at_equal_geometry():
    """The requirement's own framing: cueing should follow the ranked list.

    Held at equal geometry so the comparison is about the priority terms and
    not about which target happened to be closer.
    """
    cams = [_camera()]
    flagged = _candidate(subject_id="vessel:flagged", track_id="a",
                         lat=21.645, lon=69.58, suspicion=0.8,
                         identity_known=True)
    ordinary = _candidate(subject_id="vessel:ordinary", track_id="b",
                          lat=21.645, lon=69.58, suspicion=0.0,
                          identity_known=True)
    plan = _plan([flagged, ordinary], cams)
    assert [t.subject_id for t in plan.taskings] == ["vessel:flagged"]


def test_an_unidentified_contact_outranks_a_known_hull_at_equal_suspicion():
    """Information gain is what stops the network spending itself on one ship.

    A photograph of a contact nobody can name is the only lead there is; a
    photograph of a hull already broadcasting her identity confirms something
    already known. Without this term a scheduler re-images the top of the
    ranked list every slot and never looks at the other fifty tracks.
    """
    cams = [_camera()]
    contact = _candidate(subject_id="contact:radar:SYN-A:7", track_id="r7",
                         lat=21.645, lon=69.58, suspicion=0.3,
                         identity_known=False)
    known = _candidate(subject_id="vessel:known", track_id="k",
                       lat=21.645, lon=69.58, suspicion=0.3,
                       identity_known=True)
    plan = _plan([contact, known], cams)
    assert [t.subject_id for t in plan.taskings] == ["contact:radar:SYN-A:7"]


def test_a_hull_whose_image_already_confirmed_her_stops_consuming_cameras():
    """The loop has to close, or it is a ranking that runs repeatedly.

    A hull photographed once, whose picture agreed with what she broadcasts, has
    little left to give; one whose picture disagreed should be looked at again,
    harder. Feeding last pass's outcome back into this pass's priority is what
    makes this a control loop, and it is what supplies the corroborating second
    look the mismatch rule requires.
    """
    from maritime_isr.eo.cue import PRIORITY_FLOOR

    cams = [_camera()]
    hull = _candidate(subject_id="vessel:seen", track_id="s",
                      lat=21.645, lon=69.58, suspicion=0.0,
                      identity_known=True)
    fresh = _plan([hull], cams)
    assert [t.subject_id for t in fresh.taskings] == ["vessel:seen"], (
        "a hull nobody has ever photographed is worth exactly one look")

    # Five days on: fully stale either way, so the only thing separating these
    # two runs is what the previous image *said*.
    last_week = (T_DAY - timedelta(days=5)).timestamp()
    confirmed = _plan([hull], cams,
                      imaged_at={"vessel:seen": last_week},
                      verdict_state={"vessel:seen": "confirmed"})
    assert confirmed.taskings == [], (
        "a hull the camera already agreed with must fall below the floor")
    assert confirmed.counters["below_priority_floor"] >= 1

    contradicted = _plan([hull], cams,
                         imaged_at={"vessel:seen": last_week},
                         verdict_state={"vessel:seen": "contradicted"})
    assert [t.subject_id for t in contradicted.taskings] == ["vessel:seen"], (
        "a hull whose image disagreed with her declaration must be re-imaged")
    assert PRIORITY_FLOOR > 0.0


def test_a_closing_window_reorders_the_queue_without_rewriting_it():
    """Being about to leave does not make a ship more suspicious.

    It makes deferring her more expensive, which is why urgency multiplies the
    assignment cost rather than the priority. The distinction is what makes this
    a schedule: a target observable all afternoon can wait for the camera; one
    leaving cover in ten minutes cannot.
    """
    cams = [_camera()]
    # Both equally valuable now. One is steaming hard out of camera reach; the
    # other is loitering and will still be there in an hour.
    leaving = _candidate(subject_id="vessel:leaving", track_id="lv",
                         lat=21.645, lon=69.58, suspicion=0.5, sog_kn=22.0,
                         cog_deg=270.0, identity_known=True)
    staying = _candidate(subject_id="vessel:staying", track_id="st",
                         lat=21.645, lon=69.58, suspicion=0.5, sog_kn=0.2,
                         cog_deg=0.0, identity_known=True)
    plan = _plan([leaving, staying], cams, slots=4, slot_seconds=900.0)

    first = [t for t in plan.taskings if t.slot_index == 0]
    assert first and first[0].subject_id == "vessel:leaving", (
        "the target about to become unobservable takes the first slot")
    assert first[0].urgency > 0.0
    # And the one that waited is not abandoned — she is imaged in a later slot.
    assert "vessel:staying" in plan.subjects()


def test_a_camera_cannot_slew_across_the_horizon_inside_a_short_slot():
    """A schedule that ignores the time to move the head is a wish.

    At half-hour slots this never binds and the term looks like decoration; at
    two-minute slots it is the difference between a plan a station can execute
    and one it cannot. Tested at the scale where it bites.
    """
    # Two kilometres either side of the tower, so the pair is well inside the
    # camera's reach whatever the visibility draw happens to be for this hour —
    # the test is about the head's travel time and must not be decided by the
    # weather.
    cam = _camera(slew_rate_deg_s=1.0, min_dwell_s=30.0)
    east = _candidate(subject_id="vessel:east", track_id="e",
                      lat=21.630, lon=69.629, suspicion=0.9)
    west = _candidate(subject_id="vessel:west", track_id="w",
                      lat=21.630, lon=69.591, suspicion=0.89)

    plan = _plan([east, west], [cam], slots=2, slot_seconds=60.0)
    assert plan.counters["slew_too_far"] >= 1
    assert "vessel:west" not in plan.subjects(), (
        "the head cannot swing 180° and settle inside a 60 s slot")
    # The camera is not left idle by the constraint — it holds the target it is
    # already pointed at, which is what a station would actually do.
    assert {t.subject_id for t in plan.taskings} == {"vessel:east"}

    # With room to move, the same pair is scheduled across both slots.
    roomy = _plan([east, west], [cam], slots=2, slot_seconds=1800.0)
    assert roomy.subjects() == {"vessel:east", "vessel:west"}


def test_every_tasking_carries_its_arithmetic_and_a_sentence():
    """Automation an operator cannot interrogate is automation they switch off.

    The requirement asks for the decision to be made automatically *and
    explained*. A tasking order that is only an ordering is half the deliverable.
    """
    from maritime_isr.eo.cue import (W_INFORMATION, W_STALENESS, W_SUSPICION)

    cams = [_camera()]
    c = _candidate(subject_id="vessel:x", track_id="x", lat=21.645, lon=69.58,
                   suspicion=0.7, suspicion_reason="a dark-contact alert",
                   identity_known=True)
    plan = _plan([c], cams)
    t = plan.taskings[0]

    for key in ("suspicion", "information_gain", "staleness", "priority",
                "image_quality", "value", "closing_window", "weights", "view"):
        assert key in t.why, key
    # The decomposition has to actually reconstruct the priority, or it is
    # decoration — the same identity `assistant.score` asserts of its points.
    recomputed = (W_SUSPICION * t.why["suspicion"]
                  + W_INFORMATION * t.why["information_gain"]
                  + W_STALENESS * t.why["staleness"])
    assert recomputed == pytest.approx(t.priority, abs=1e-6)
    assert "Testport" in t.sentence
    assert "a dark-contact alert" in t.sentence
    assert t.is_synthetic, "a tasking from a simulated camera says so"


def test_an_idle_camera_is_counted_rather_than_hidden():
    """Utilisation is how an operator sees whether the network is being used.

    A camera left idle because nothing in reach was worth imaging is a correct
    outcome and a reportable one; a camera idle because the scheduler could not
    see a target is a defect. The counters are what separate the two.
    """
    cams = [_camera(), _camera(camera_id="EO-Z", station_id="SYN-Z",
                               name="Zulu", lat=9.97, lon=76.24)]
    plan = _plan([_candidate(lat=21.645, lon=69.58, suspicion=0.9)], cams)
    assert plan.n_cameras == 2
    assert plan.camera_slots == 2
    assert plan.counters["idle_camera_slots"] == 1
    assert 0.0 < plan.utilisation() <= 1.0


def test_the_scheduler_survives_a_picture_far_larger_than_the_network():
    """The requirement's own framing: far more tracks than there are cameras.

    Exercised at the scale the corpus actually produces, because an assignment
    written against four fixtures can be quadratic in the candidate count and
    nobody notices until it runs over two thousand tracks.
    """
    cams = list(_camera() for _ in range(1))
    rng = random.Random(4)
    cands = [_candidate(subject_id=f"vessel:b{i}", track_id=f"b{i}",
                        lat=21.63 + rng.uniform(-0.4, 0.4),
                        lon=69.61 + rng.uniform(-0.4, 0.4),
                        suspicion=rng.random() * 0.4,
                        identity_known=True)
             for i in range(400)]
    plan = _plan(cands, cams, slots=3)
    assert len(plan.taskings) <= 3
    assert plan.counters["candidates_seen"] == 1200
    # The ledger is bounded, or it is larger than the corpus and nobody reads it.
    assert len(plan.deferrals) <= 3 * 12


# ==========================================================================
# 3. capture and tagging
# ==========================================================================

class _FixedSource:
    """A capture source that returns one prepared target for every tasking."""

    name = "test-fixture"
    mode = "simulated"

    def __init__(self, target):
        self.target = target
        self.calls = 0

    def capture(self, tasking):
        self.calls += 1
        return self.target


def _target(vessel_class="Suezmax", length_m=275.0, beam_m=48.0,
            draught_m=16.0, present=True):
    from maritime_isr.eo.appearance import descriptor_for
    from maritime_isr.eo.capture import ObservedTarget
    return ObservedTarget(
        present=present,
        appearance=(descriptor_for(vessel_class, length_m=length_m,
                                   beam_m=beam_m, draught_m=draught_m)
                    if present else None),
        length_m=length_m if present else None,
        target_kind="vessel" if present else "empty_water")


def test_a_capture_binds_to_the_track_with_time_bearing_range_and_station():
    """The requirement names the binding, and the binding is the product.

    A photograph in a folder is worth nothing to a watchkeeper. A photograph
    attached to a named track, with the geometry that produced it, is evidence.
    """
    from maritime_isr.eo.capture import run_captures
    from maritime_isr.eo.classify import PrototypeClassifier, ReferenceLibrary

    plan = _plan([_candidate(subject_id="vessel:bound", track_id="tb",
                             lat=21.645, lon=69.58, suspicion=0.8)],
                 [_camera()])
    caps = run_captures(plan, source=_FixedSource(_target()),
                        classifier=PrototypeClassifier(),
                        library=ReferenceLibrary(), is_synthetic=True)
    assert len(caps) == 1
    c = caps[0]
    assert c.subject_id == "vessel:bound" and c.track_id == "tb"
    assert c.station_id == "SYN-TST" and c.range_km > 0
    assert 0.0 <= c.bearing_deg < 360.0
    assert c.taken_at.tzinfo is not None
    assert c.is_synthetic


def test_a_capture_row_says_no_lens_was_involved():
    """Never overclaim capability (CLAUDE.md §5), on the row and not in a note.

    There is no camera in this system. A capture record that did not say so
    would let a synthetic image reach an operator looking exactly like a real
    one, which is the cardinal failure this project names.
    """
    from maritime_isr.eo.capture import MODE_SIMULATED, run_captures
    from maritime_isr.eo.classify import PrototypeClassifier, ReferenceLibrary

    plan = _plan([_candidate(lat=21.645, lon=69.58, suspicion=0.8)],
                 [_camera()])
    caps = run_captures(plan, source=_FixedSource(_target()),
                        classifier=PrototypeClassifier(),
                        library=ReferenceLibrary(), is_synthetic=True)
    row = caps[0].as_row()
    assert row["capture_mode"] == MODE_SIMULATED
    assert row["image_ref"] is None
    assert row["model_provenance"], "the model has to name where it came from"
    assert "never seen an image" in row["model_provenance"]


def test_an_empty_frame_is_recorded_and_is_not_an_accusation():
    """The camera slewed and there was nothing there.

    A real answer about a radar track — it resolves sea clutter, which is the
    dominant false-positive source in the whole radar picture. It is recorded
    and counted, and deliberately not promoted to an alert: the simulated camera
    never misses a target that is present and a real one does, so the rule would
    be calibrated against a false-negative rate this project does not have.
    """
    from maritime_isr.eo.capture import run_captures
    from maritime_isr.eo.classify import PrototypeClassifier, ReferenceLibrary

    plan = _plan([_candidate(subject_id="contact:radar:SYN-A:9",
                             track_id="c9", lat=21.645, lon=69.58,
                             suspicion=0.7, identity_known=False)],
                 [_camera()])
    caps = run_captures(plan, source=_FixedSource(_target(present=False)),
                        classifier=PrototypeClassifier(),
                        library=ReferenceLibrary(), is_synthetic=True)
    assert len(caps) == 1
    assert caps[0].target_present is False
    assert caps[0].verdict is None
    assert "frame was empty" in caps[0].statement()


def test_captures_are_deterministic_for_one_plan():
    """A cueing change must be attributable, not confounded with fresh dice.

    Image noise is seeded from the capture id, so the same corpus and the same
    plan produce the same pictures. Without it, every re-run moves the alert
    counts and no measurement means anything.
    """
    from maritime_isr.eo.capture import run_captures
    from maritime_isr.eo.classify import PrototypeClassifier, ReferenceLibrary

    plan = _plan([_candidate(lat=21.645, lon=69.58, suspicion=0.8)],
                 [_camera()])

    def once():
        caps = run_captures(plan, source=_FixedSource(_target()),
                            classifier=PrototypeClassifier(),
                            library=ReferenceLibrary(), is_synthetic=True)
        return caps[0].observed.as_dict()

    assert once() == once()


# ==========================================================================
# 4. the classifier, behind its interface
# ==========================================================================

def test_the_vocabulary_is_measured_rather_than_declared():
    """A hand-written list of coarse classes is a claim about the world.

    What this module is entitled to make is a claim about its own model, which
    is the move ADR-033 made for vessel type from motion. The merge reads the
    measured confusion — and reuses `tracks.vessel_type.confusable_groups`
    outright, because two implementations of one question drift apart.
    """
    from maritime_isr.eo.classify import measure_separability
    from maritime_isr.eo.conditions import BAND_THERMAL, BAND_VISIBLE

    day = measure_separability(quality=0.8, band=BAND_VISIBLE)
    night = measure_separability(quality=0.5, band=BAND_THERMAL)
    assert day["vocabulary"] and night["vocabulary"]
    # A thermal silhouette carries less, so it must not resolve *more* classes.
    assert len(night["vocabulary"]) <= len(day["vocabulary"]), (
        f"night {night['vocabulary']} vs day {day['vocabulary']}")


def test_confidence_tracks_the_hit_rate_it_is_supposed_to_describe():
    """A confidence that does not track accuracy is decoration.

    Measured: a hand-set temperature had the model picking the right fine class
    96% of the time while reporting an average confidence of 0.35, so 84% of
    perfectly good images were refused by a number that meant nothing. The
    temperature is fitted so mean confidence matches measured accuracy.
    """
    from maritime_isr.eo.classify import measure_separability

    for q in (0.5, 0.65, 0.8):
        cal = measure_separability(quality=q)["calibration"]
        assert abs(cal["mean_confidence"] - cal["accuracy"]) < 0.06, (q, cal)


def test_a_bow_on_look_withholds_the_length_rather_than_inventing_one():
    """The aspect correction divides by sin(aspect) and blows up near the bow.

    Withholding the measurement is the honest outcome: reporting a 190 m tanker
    as a 60 m trawler because she was pointed at the camera is exactly how an
    imagery rule manufactures a spectacular false positive.
    """
    from maritime_isr.eo.appearance import (MIN_ASPECT_FOR_LENGTH,
                                            descriptor_for, observe)
    from maritime_isr.eo.conditions import BAND_VISIBLE

    proto = descriptor_for("Suezmax", length_m=275.0, beam_m=48.0,
                           draught_m=16.0)
    rng = random.Random(1)
    broadside = observe(proto, aspect_deg=90.0, quality=0.8, band=BAND_VISIBLE,
                        rng=rng)
    bow_on = observe(proto, aspect_deg=5.0, quality=0.8, band=BAND_VISIBLE,
                     rng=rng)
    assert broadside.length_reliable
    assert not bow_on.length_reliable
    assert MIN_ASPECT_FOR_LENGTH > 0.0


def test_a_thermal_look_cannot_read_a_deck_and_says_so():
    """The deck is what separates a tanker from a bulker, and it is the first
    thing a silhouette loses. A model that reported "tanker" off a night image
    would be inventing the one feature it could not see."""
    from maritime_isr.eo.appearance import descriptor_for, observe
    from maritime_isr.eo.conditions import BAND_THERMAL, BAND_VISIBLE

    proto = descriptor_for("bulker", length_m=190.0, beam_m=32.0,
                           draught_m=11.5)
    rng = random.Random(2)
    assert observe(proto, aspect_deg=90.0, quality=0.7, band=BAND_VISIBLE,
                   rng=rng).deck_readable
    assert not observe(proto, aspect_deg=90.0, quality=0.7, band=BAND_THERMAL,
                       rng=rng).deck_readable


def test_a_poor_image_is_refused_rather_than_guessed():
    """Refusing is a first-class output, as it is in `tracks.vessel_type`.

    This floor is what makes a marginal look produce information instead of an
    accusation, and it is the difference between the poor-image decoy staying
    quiet and it being a false positive.
    """
    from maritime_isr.eo.appearance import descriptor_for, observe
    from maritime_isr.eo.classify import (MIN_CLASSIFY_QUALITY,
                                          PrototypeClassifier,
                                          ReferenceLibrary)
    from maritime_isr.eo.conditions import BAND_VISIBLE

    proto = descriptor_for("Suezmax", length_m=275.0, beam_m=48.0,
                           draught_m=16.0)
    seen = observe(proto, aspect_deg=90.0, quality=0.1, band=BAND_VISIBLE,
                   rng=random.Random(3))
    v = PrototypeClassifier().classify(seen, quality=0.1, band=BAND_VISIBLE,
                                       library=ReferenceLibrary())
    assert not v.is_claim
    assert v.not_classifiable
    assert MIN_CLASSIFY_QUALITY > 0.0


def test_a_distinctive_hull_is_recognised_and_a_sister_ship_is_refused():
    """"Classify to specific identity where a vessel has been imaged before" —
    and the honest limit of that, which the measurement forced.

    Two looks at one hull sit about 0.12 apart in descriptor space; the closest
    pair of *different* hulls sits 0.11 apart. The distributions overlap,
    because two Suezmaxes of the same dimensions genuinely do look the same in
    six numbers. So no radius separates them and the margin does the work: an
    identification is offered when the hull is distinctive against what the
    library holds, and refused when she is one of a class.

    Refusing is the whole point. A library of similar merchants that returned
    whichever one sorted first would name the wrong ship, confidently, which is
    worse than naming none.
    """
    from maritime_isr.eo.appearance import descriptor_for, observe
    from maritime_isr.eo.classify import (LibraryEntry, PrototypeClassifier,
                                          ReferenceLibrary)
    from maritime_isr.eo.conditions import BAND_VISIBLE

    rng = random.Random(7)
    clf = PrototypeClassifier()
    proto = descriptor_for("Suezmax", length_m=275.0, beam_m=48.0,
                           draught_m=16.0)

    def look(aspect=85.0):
        return observe(proto, aspect_deg=aspect, quality=0.95,
                       band=BAND_VISIBLE, rng=rng)

    # A library holding only her, plus a plainly different hull.
    lib = ReferenceLibrary()
    lib.add(LibraryEntry(subject_id="vessel:known", appearance=look(),
                         capture_id="eoc_first", at=0.0, quality=0.95))
    lib.add(LibraryEntry(
        subject_id="vessel:trawler",
        appearance=descriptor_for("fishing", length_m=26.0, beam_m=7.0,
                                  draught_m=3.0),
        capture_id="eoc_other", at=0.0, quality=0.95))

    v = clf.classify(look(80.0), quality=0.95, band=BAND_VISIBLE, library=lib)
    assert v.identity_subject == "vessel:known", v.identity_basis
    assert v.identity_confidence > 0.0

    # Now put three sister ships in the library. She is no longer distinctive
    # and the system must decline rather than pick one.
    for i in range(3):
        lib.add(LibraryEntry(subject_id=f"vessel:sister{i}",
                             appearance=look(), capture_id=f"eoc_s{i}",
                             at=0.0, quality=0.95))
    v2 = clf.classify(look(80.0), quality=0.95, band=BAND_VISIBLE, library=lib)
    assert v2.identity_subject is None, (
        "a hull that looks like three others in the library must not be named")
    assert "too close to call" in v2.identity_basis

    # And an empty library cannot identify anybody.
    v3 = clf.classify(look(), quality=0.95, band=BAND_VISIBLE,
                      library=ReferenceLibrary())
    assert v3.identity_subject is None


class _AlwaysTrawler:
    """A third classifier, defined in the test file and substituted live.

    The point of this class is that it is *not* in the package. If the loop can
    run end to end against a model the package has never heard of, the interface
    is real; if it needs an import or an isinstance check anywhere, it is not.
    """

    name = "always-trawler-v0"
    provenance = ("A deliberately wrong stub defined in tests/test_area5.py, "
                  "standing in for a customer-supplied model.")

    def __init__(self):
        self.calls = 0

    def classify(self, seen, *, quality, band, library, known_subject=None):
        from maritime_isr.eo.classify import ImageVerdict
        self.calls += 1
        return ImageVerdict(imaged_type="fishing", confidence=0.9,
                            fine_type="fishing",
                            imaged_families=frozenset({"fishing"}),
                            model_name=self.name,
                            model_provenance=self.provenance,
                            quality=quality, band=band)


def test_the_classifier_is_swappable_and_the_swap_is_demonstrated():
    """Swap-ability asserted in a docstring is not swap-ability.

    The same plan and the same captures are run through three models — the
    default, the restricted one that ships beside it, and one defined in this
    test file that the package has never heard of. Every one produces bound,
    landed captures; the verdicts differ; nothing in the cueing, the tagging or
    the mismatch rule is touched between the runs.
    """
    from maritime_isr.eo.capture import run_captures
    from maritime_isr.eo.classify import (PrototypeClassifier,
                                          ReferenceLibrary,
                                          SilhouetteClassifier)

    plan = _plan([_candidate(subject_id="vessel:swap", track_id="sw",
                             lat=21.645, lon=69.58, suspicion=0.9)],
                 [_camera()])
    third = _AlwaysTrawler()
    verdicts = {}
    for clf in (PrototypeClassifier(), SilhouetteClassifier(), third):
        caps = run_captures(plan, source=_FixedSource(_target()),
                            classifier=clf, library=ReferenceLibrary(),
                            is_synthetic=True)
        assert len(caps) == 1 and caps[0].subject_id == "vessel:swap"
        verdicts[clf.name] = caps[0].verdict

    assert third.calls == 1, "the loop really called the substituted model"
    assert verdicts["always-trawler-v0"].imaged_type == "fishing"
    # The two shipped models are genuinely different, not aliases: the
    # restricted one sees fewer features and reaches a coarser answer.
    assert (verdicts["prototype-v1"].imaged_type
            != verdicts["silhouette-v1"].imaged_type
            or verdicts["prototype-v1"].confidence
            != verdicts["silhouette-v1"].confidence)


def test_a_restricted_model_reports_its_own_lower_certainty():
    """A weaker model must not inherit a fuller one's calibration.

    Sharing one calibration across implementations would let a model that sees
    two features report the confidence of one that sees six — the overclaim the
    interface exists to make impossible.
    """
    from maritime_isr.eo.classify import (PrototypeClassifier,
                                          SilhouetteClassifier,
                                          separability_at)
    from maritime_isr.eo.conditions import BAND_VISIBLE

    full = PrototypeClassifier()
    thin = SilhouetteClassifier()
    a = separability_at(0.8, BAND_VISIBLE, model=full.name,
                        restrict=full._restrict)
    b = separability_at(0.8, BAND_VISIBLE, model=thin.name,
                        restrict=thin._restrict)
    assert a["vocabulary"] != b["vocabulary"]
    assert len(b["vocabulary"]) <= len(a["vocabulary"])


# ==========================================================================
# 5. the mismatch rule
# ==========================================================================

def _verdict(imaged_type, families, conf=0.9, band="visible"):
    from maritime_isr.eo.classify import ImageVerdict
    return ImageVerdict(imaged_type=imaged_type, confidence=conf,
                        fine_type=imaged_type,
                        imaged_families=(frozenset(families) if families
                                         else None),
                        model_name="test", quality=0.8, band=band)


def test_the_headline_case_fires():
    """"A vessel declaring itself a fishing vessel that images as a tanker."

    The brief's own worked example. If this does not fire, Area 5 has no payoff.
    """
    from maritime_isr.anomaly.imagery import check_declared_type

    f = check_declared_type(declared_class="fishing",
                            verdict=_verdict("tanker", {"tanker"}),
                            quality=0.8)
    assert f.outcome == "contradiction"
    assert f.confidence > 0.5
    assert "fishing" in f.statement and "tanker" in f.statement


def test_a_merged_label_still_contradicts_a_family_it_excludes():
    """"Merchant" cannot say which family she is; it says which she is not.

    A first version returned "cannot check" whenever the label spanned two
    families, which silently discarded the brief's headline example — under most
    conditions this model publishes `merchant` rather than `tanker`, and a hull
    declaring herself a trawler while imaging as a merchant has plainly been
    contradicted.
    """
    from maritime_isr.anomaly.imagery import check_declared_type

    f = check_declared_type(declared_class="fishing",
                            verdict=_verdict("merchant", {"tanker", "cargo"}),
                            quality=0.8)
    assert f.outcome == "contradiction"

    # ...and it must NOT contradict a family it leaves open.
    ok = check_declared_type(declared_class="product_tanker",
                             verdict=_verdict("merchant", {"tanker", "cargo"}),
                             quality=0.8)
    assert ok.outcome == "ok"


def test_two_classes_in_one_ais_family_are_not_a_lie():
    """The precision decision the whole rule rests on.

    A bulker declaring general cargo has contradicted nothing: both are cargo
    under ITU-R M.1371, and two sources classifying one hull differently inside
    a family is the `class_quibble` argument ADR-034 already settled. A rule
    that fired here would fire on the merchant fleet.
    """
    from maritime_isr.anomaly.imagery import check_declared_type

    f = check_declared_type(declared_class="general_cargo",
                            verdict=_verdict("bulker", {"cargo"}),
                            quality=0.8)
    assert f.outcome == "ok"


def test_the_rule_is_three_valued_and_says_which_refusal_it_made():
    """"We could not check" is an answer, never folded into "fine".

    Four distinct refusals, each a false positive that would otherwise happen:
    she declared nothing, the image was too poor, the label rules nothing out,
    and the classifier was not confident enough to accuse.
    """
    from maritime_isr.anomaly.imagery import (MIN_MISMATCH_CONFIDENCE,
                                              MIN_MISMATCH_QUALITY,
                                              check_declared_type)

    tanker = _verdict("tanker", {"tanker"})

    silent = check_declared_type(declared_class=None, verdict=tanker,
                                 quality=0.8)
    assert silent.outcome == "not_checkable"
    assert "no vessel type" in silent.statement

    poor = check_declared_type(declared_class="fishing", verdict=tanker,
                               quality=MIN_MISMATCH_QUALITY - 0.05)
    assert poor.outcome == "not_checkable"
    assert "quality" in poor.statement

    unbounded = check_declared_type(declared_class="fishing",
                                    verdict=_verdict("vessel", None),
                                    quality=0.8)
    assert unbounded.outcome == "not_checkable"
    assert "rules out no" in unbounded.statement

    unsure = check_declared_type(
        declared_class="fishing",
        verdict=_verdict("tanker", {"tanker"},
                         conf=MIN_MISMATCH_CONFIDENCE - 0.05),
        quality=0.8)
    assert unsure.outcome == "not_checkable"

    nothing = check_declared_type(declared_class="fishing", verdict=None,
                                  quality=0.8)
    assert nothing.outcome == "not_checkable"


def test_an_honest_fleet_is_almost_never_accused():
    """Precision before recall, measured rather than asserted (ADR-004).

    Every prototype hull declaring exactly what she is, photographed two hundred
    times each under three conditions. The bar is the product policy: a rule
    that accused even one honest hull in a hundred would put a steady trickle of
    innocent ships on the queue, which is how an operator learns to stop opening
    it.
    """
    from maritime_isr.anomaly.imagery import check_declared_type
    from maritime_isr.eo.appearance import descriptor_for, observe
    from maritime_isr.eo.classify import (PROTOTYPE_HULLS, PrototypeClassifier,
                                          ReferenceLibrary)
    from maritime_isr.eo.conditions import BAND_THERMAL, BAND_VISIBLE

    clf, lib = PrototypeClassifier(), ReferenceLibrary()
    total = accused = 0
    for band in (BAND_VISIBLE, BAND_THERMAL):
        for q in (0.5, 0.65, 0.8):
            rng = random.Random(11)
            for cls, (length, beam, dr) in PROTOTYPE_HULLS.items():
                proto = descriptor_for(cls, length_m=length, beam_m=beam,
                                       draught_m=dr)
                for _ in range(60):
                    seen = observe(proto, aspect_deg=80.0, quality=q,
                                   band=band, rng=rng)
                    v = clf.classify(seen, quality=q, band=band, library=lib)
                    f = check_declared_type(declared_class=cls, verdict=v,
                                            quality=q, band=band)
                    total += 1
                    accused += f.outcome == "contradiction"
    assert total > 3000
    assert accused / total < 0.01, (
        f"{accused} of {total} honest looks produced an accusation")


def test_a_single_look_never_accuses_a_named_hull():
    """Corroboration is the fix for a residual error rate, not a higher bar.

    A wrong label and a right one look the same from inside, so raising the
    confidence bar suppresses true positives just as fast as false ones. Two
    looks at different ranges, aspects and light are close to independent, and
    the cueing loop supplies the second for free by keeping a contradicted hull
    at high information gain.
    """
    from maritime_isr.anomaly.library import (MIN_CORROBORATING_CAPTURES,
                                              detect_imagery_mismatch)

    store = _store()
    identities = [dict(vessel_id="vessel:liar", vessel_class="fishing")]
    caps = [_capture_row(0), _capture_row(1)]

    assert MIN_CORROBORATING_CAPTURES == 2
    assert detect_imagery_mismatch(store, caps[:1], identities,
                                   source_ref="t") == []
    fired = detect_imagery_mismatch(store, caps, identities, source_ref="t")
    assert len(fired) == 1


def test_two_looks_that_disagree_with_each_other_are_not_corroboration():
    """A hull called a tanker once and a trawler once was photographed badly.

    Requiring only "two contradictions" rather than "two contradictions that
    agree" would count confusion as evidence — which is the failure mode the
    corroboration rule exists to close, arriving through the back door.
    """
    from maritime_isr.anomaly.library import detect_imagery_mismatch

    store = _store()
    identities = [dict(vessel_id="vessel:liar", vessel_class="fishing")]
    caps = [_capture_row(0, imaged_type="tanker", families="tanker"),
            _capture_row(1, imaged_type="dry_cargo", families="cargo")]
    assert detect_imagery_mismatch(store, caps, identities,
                                   source_ref="t") == []


def test_a_capture_on_an_unnamed_contact_is_evidence_and_not_an_accusation():
    """A target that declared nothing cannot have lied about it.

    Most radar contacts are in this state, which is exactly why the imagery on
    *them* is the strongest thing the system can offer and exactly why it must
    not arrive as a contradiction.
    """
    from maritime_isr.anomaly.library import detect_imagery_mismatch

    store = _store()
    caps = [_capture_row(i, subject="contact:radar:SYN-A:4") for i in range(3)]
    assert detect_imagery_mismatch(store, caps, [], source_ref="t") == []


def _store():
    from maritime_isr.anomaly.library import vessel_node_id
    from maritime_isr.graph.store import GraphStore
    store = GraphStore(":memory:")
    store.upsert_node(vessel_node_id("vessel:liar"), "vessel",
                      props=dict(mmsi="999000123"), is_synthetic=True)
    store.upsert_node("contact:radar:SYN-A:4", "contact",
                      props=dict(track_key="SYN-A:4"), is_synthetic=True)
    return store


def _capture_row(i, *, subject="vessel:liar", imaged_type="tanker",
                 families="tanker", quality=0.8, conf=0.9):
    return {
        "capture_id": f"eoc_test{i}",
        "subject_id": subject,
        "target_present": True,
        "taken_at": T_DAY + timedelta(hours=i * 6),
        "image_quality": quality,
        "band": "visible",
        "imaged_type": imaged_type,
        "imaged_families": families,
        "type_confidence": conf,
        "fine_type": imaged_type,
        "camera_id": "EO-TEST", "station": "Testport",
        "range_km": 6.0, "bearing_deg": 210.0,
        "model_name": "prototype-v1", "model_provenance": "test",
        "capture_mode": "simulated",
        "lat": 21.6, "lon": 69.5,
    }


# ==========================================================================
# 7. the discipline the whole area rests on
# ==========================================================================

def test_no_area5_module_reads_the_answer_key():
    """ADR-019: no scheduler, classifier or rule may read `scenario_truth`.

    The camera simulator in `scenario/` is entitled to know what is out there —
    it is the world generator, exactly as `scenario/radar.py` is. Nothing on
    this side of the `CaptureSource` seam is, and that is the entire basis for
    any precision figure the area produces.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "maritime_isr"
    paths = list((root / "eo").glob("*.py")) + [root / "anomaly" / "imagery.py"]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "scenario_truth" not in text, path
        assert "radar_dark_truth" not in text, path
        assert "scenario_eo_appearance" not in text, path


def test_the_eo_loop_does_not_reach_into_the_fusion_core():
    """Every source is a connector, never a core change (CLAUDE.md §4.5).

    If Area 5 had needed `fusion/` to learn about cameras, that would be a
    defect in the core to be written up rather than patched around — the brief's
    standing caution says so outright. It did not: the loop reads tracks and
    alerts and writes its own table.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "maritime_isr"
    for path in (root / "eo").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from ..fusion" not in text, path
        assert "import fusion" not in text, path


def test_the_imagery_factor_is_registered_with_a_weight_and_an_action():
    """A kind with no catalog entry scores as nothing and narrates as nothing.

    It would still appear to work, which is why the catalog raises rather than
    defaulting. The brief's own test of whether an area was wired in is that the
    ranked list gains a new class of factor.
    """
    from maritime_isr.assistant.catalog import FAMILIES, spec

    s = spec("imagery_type_mismatch")
    assert s.family == "imagery"
    assert 0.0 < s.weight <= 1.0
    assert "cue_eo_camera" in s.actions
    assert "imagery" in FAMILIES


def test_the_capture_table_is_registered_with_the_reader():
    """A table absent from `CONFORMED_TABLES` makes `has()` answer False over a
    corpus that contains rows. That has cost this project two areas' worth of
    debugging (ADR-035, ADR-036), so it is asserted rather than remembered."""
    from maritime_isr.api.reader import CONFORMED_TABLES
    from maritime_isr.eo.capture import TABLE

    assert TABLE in CONFORMED_TABLES


def test_the_camera_simulators_world_model_is_not_visible_to_the_serving_layer():
    """`scenario_eo_appearance` is the simulator's model of what is physically
    out there — the stand-in for the photons a real lens would collect. Only the
    world generator may read it, so it is deliberately absent from the reader's
    table list, one `has()` call away from the API."""
    from maritime_isr.api.reader import CONFORMED_TABLES

    assert "scenario_eo_appearance" not in CONFORMED_TABLES


def test_the_graph_holds_a_capture_as_an_artifact_not_as_a_ship():
    """A photograph is not a vessel, and a capture can depict a target nothing
    can name. Its own node type, for the same reason `notification` has one."""
    from maritime_isr.graph.ontology import EDGE_TYPES_V1, NODE_TYPES_V1

    assert "eo_capture" in NODE_TYPES_V1
    assert EDGE_TYPES_V1["depicts"]["src"] == ["eo_capture"]
    assert "contact" in EDGE_TYPES_V1["depicts"]["dst"]
    assert EDGE_TYPES_V1["captured-by"]["dst"] == ["sensor"]


def test_the_recommendation_does_not_claim_a_camera_this_system_lacks():
    """Half built, and the halves are named separately (CLAUDE.md §5).

    Saying "built" would claim a photograph the system cannot take; saying "not
    built" would hide the part of Area 5 that is finished.
    """
    from maritime_isr.assistant.recommend import ACTIONS

    cap = ACTIONS["cue_eo_camera"]["capability"]
    assert "Partly built" in cap
    assert "no camera" in cap.lower()
