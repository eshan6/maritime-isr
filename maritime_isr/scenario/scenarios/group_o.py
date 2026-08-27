"""Group O — what the camera sees against what the transponder claims.

Area 5 of the IDEX Challenge 82 brief. *"Mismatch alerting, which is the payoff.
The camera says one thing, the transponder says another. A vessel declaring
itself a fishing vessel that images as a tanker is a strong, legible,
immediately actionable finding."*

**The honest majority is the denominator.** Every hull in this corpus declares
what she is; five do not, and only two of those five are findable. That ratio is
the point. A camera rule measured only against liars reports recall and nothing
about how often it accuses a bulker of being a tanker, and the whole difficulty
of Area 5 is that most merchant hulls look broadly alike from a coastal camera.

The authored lies:

  O1  a 270 m tanker broadcasting the AIS type code of a trawler. The brief's
      own worked example, and the case a camera is unarguably needed for.
  O2  a crane-equipped cargo ship broadcasting product tanker. The subtle one:
      both are merchants of similar length running at similar speeds, so no
      motion feature separates them (ADR-033) and no registry disagrees. The
      deck is the only evidence there is.

The decoy:

  O3  a general cargo ship broadcasting bulk carrier. Both are *cargo* under
      the AIS ship-type standard, the image can tell them apart, and it is
      still not a lie — two sources classifying one hull differently inside a
      family is routine (ADR-034's `class_quibble`). A rule that fires here
      fires on the merchant fleet.

And two capability boundaries, which are the more useful kind of negative
result because the reason is a number rather than a shrug:

  O4  a hull that genuinely misdeclares her type and works 150 km offshore all
      window. No camera can reach her. The cueing ledger names her every slot
      with the range, which is an argument for a surface unit rather than a
      silence.
  O5  the same lie as O2, on a hull whose only camera windows fall at night at
      five kilometres. A thermal image at that range does not carry a deck, and
      the deck is the only thing that would separate the two. The system images
      her, classifies her, and declines to accuse her.

**No hull here is placed by reference to the answer key.** Their tracks are
written against the station network the same way group R's are, so a station
that moves takes its scenarios with it; what each hull *broadcasts* is set in
`cast.DECLARED_CLASS_OVERRIDES` and lands in her ordinary AIS static record.
Nothing in this module reads `scenario_truth`.
"""
from __future__ import annotations

from ..geography import destination
from ..primitives.track import Leg, VoyagePlan, generate_track
from ..radar_network import STATIONS_BY_ID
from ..truth import (DECOY, DELIBERATE_MISS, FAMILY_BOUNDARY, FAMILY_DECOY,
                     FAMILY_IMAGERY, TRUE_ANOMALY, ScenarioTruth)
from ..world import ScenarioWorld, week
from .common import V, emit

__all__ = ["SCENARIOS"]


def _offshore_of(station_id: str, bearing_deg: float, km: float
                 ) -> tuple[float, float]:
    """A position `km` seaward of a named station on a given bearing.

    Same discipline as group R: scenario positions are written against the
    station network rather than as bare coordinates, so a scenario cannot
    silently drift out of the cover it was written to sit in.
    """
    st = STATIONS_BY_ID[station_id]
    return destination(st.lat, st.lon, bearing_deg, km * 1000.0)


#: How close a hull has to pass a station for a *contradiction* to be provable.
#:
#: **Measured, and it is tighter than the range at which a camera can see her.**
#: The useful reach of the head is 20 km; the image quality needed to contradict
#: a declared identity (`anomaly.imagery.MIN_MISMATCH_QUALITY`) is only reached
#: inside about eight kilometres in this coast's monsoon visibility. So the
#: Area 5 hulls are placed on coastal passages that pass close inshore — which
#: is where a coastal surveillance camera earns its keep anyway, and the gap
#: between "can see her" and "can prove something about her" is recorded in
#: ADR-037 rather than tuned away.
_CLOSE_PASS_KM = 6.0


def _coastal_pass(world: ScenarioWorld, key: str, station: str, *,
                  t0, speed_kn: float = 11.0, hours_before: float = 0.0):
    """A passage that closes to `_CLOSE_PASS_KM` off one station and goes on.

    Deliberately a *passage* rather than a loiter: the mismatch rule requires
    corroboration across separate looks, and a hull steaming past a station at
    eleven knots is inside camera range for an hour or so — several slots, at
    changing range and aspect. That is exactly the population the corroboration
    rule was designed against, and authoring a stationary target would have
    handed it identical looks and proved nothing.
    """
    v = V(world, key)
    legs = [
        Leg("transit", target=_offshore_of(station, 250.0, _CLOSE_PASS_KM),
            speed_kn=speed_kn),
        Leg("transit", target=_offshore_of(station, 200.0, _CLOSE_PASS_KM),
            speed_kn=speed_kn),
        Leg("transit", target=_offshore_of(station, 170.0, 14.0),
            speed_kn=speed_kn),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of(station, 285.0, 16.0), start_time=t0, legs=legs),
        world.rng)
    emit(world, key, pts)
    return v, pts


# ==========================================================================
# O1 — the brief's own worked example
# ==========================================================================

def o1_tanker_declaring_fishing(world: ScenarioWorld) -> None:
    """270 m of Suezmax broadcasting the type code of a trawler.

    The plainest possible case and the one Area 5 exists for. Nothing in her
    motion gives her away: a tanker on passage and a large fishing vessel in
    transit are both hulls making eleven knots on a steady course, and her
    registry entry agrees with her declaration because the fraud is upstream of
    the paperwork. Only a photograph closes it.
    """
    v, pts = _coastal_pass(world, "eo_false_class", "SYN-POR",
                           t0=week(2, hours=7), speed_kn=11.5)
    world.truth.add(ScenarioTruth(
        scenario_id="O1", scenario_family=FAMILY_IMAGERY,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=pts[0].t, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["imagery_type_mismatch"],
        notes=("Broadcasts the AIS static type of a fishing vessel while being "
               "a 270 m tanker. Her registry entry agrees with what she "
               "broadcasts, so no paperwork check can find her; her motion is "
               "an ordinary coastal passage, so no behavioural rule can. She "
               "passes six kilometres off Porbandar in daylight, which is the "
               "one condition under which a camera can settle it.")))


# ==========================================================================
# O2 — the distinction motion can never make
# ==========================================================================

def o2_crane_ship_declaring_tanker(world: ScenarioWorld) -> None:
    """A cargo ship with derricks, broadcasting product tanker.

    The valuable case rather than the obvious one. ADR-033 measured that motion
    cannot separate a tanker from a dry-cargo hull and never will, because a
    laden bulker and a laden product tanker at thirteen knots on a great-circle
    course are doing the same thing. A camera separates them on the one feature
    motion has no access to: whether there is anything on the deck.
    """
    v, pts = _coastal_pass(world, "eo_crane_ship", "SYN-VER",
                           t0=week(3, hours=6), speed_kn=12.0)
    world.truth.add(ScenarioTruth(
        scenario_id="O2", scenario_family=FAMILY_IMAGERY,
        truth_class=TRUE_ANOMALY,
        entity_ids=[v.entity_id], t_start=pts[0].t, t_end=pts[-1].t,
        expected_detection=True,
        expected_anomaly_types=["imagery_type_mismatch"],
        notes=("Broadcasts product tanker; is a crane-equipped general cargo "
               "ship. Same length band, same speed, same registry entry — the "
               "deck is the only evidence, and only a daylight image carries "
               "a deck. This is the case that justifies pointing a camera at "
               "an otherwise unremarkable merchant.")))


# ==========================================================================
# O3 — the decoy the whole rule's precision rests on
# ==========================================================================

def o3_family_cousin_decoy(world: ScenarioWorld) -> None:
    """A general cargo ship broadcasting bulk carrier. Not a lie.

    The camera can tell these apart — a bulker's flush hatch covers and a cargo
    ship's derricks are different pictures. That is exactly why the decoy is
    needed: *separable* and *contradictory* are not the same thing. Both are
    cargo under ITU-R M.1371, and ADR-034 already settled the principle for the
    registry check when `class_quibble` was written. A rule that compared the
    image against the declaration at the finest resolution it could resolve
    would fire on a large fraction of the merchant fleet.
    """
    v, pts = _coastal_pass(world, "eo_class_cousin", "SYN-DAH",
                           t0=week(4, hours=8), speed_kn=11.0)
    world.truth.add(ScenarioTruth(
        scenario_id="O3", scenario_family=FAMILY_DECOY, truth_class=DECOY,
        entity_ids=[v.entity_id], t_start=pts[0].t, t_end=pts[-1].t,
        expected_detection=False, expected_anomaly_types=[],
        notes=("Declares bulk carrier, is a general cargo ship, and is "
               "photographed clearly enough for the difference to be visible. "
               "Both are cargo under the AIS ship-type standard and two "
               "sources classifying one hull differently inside a family is "
               "routine. If this fires, the rule is comparing at the wrong "
               "resolution and the merchant fleet is next.")))


# ==========================================================================
# O4 — a real lie, out of reach: the cueing boundary
# ==========================================================================

def o4_beyond_camera_reach(world: ScenarioWorld) -> None:
    """A hull that misdeclares her type and never comes within camera range.

    The value here is in the *ledger* rather than the queue. A camera network
    with a twenty-kilometre useful reach cannot say anything about a vessel
    working a hundred and fifty kilometres out, and the honest product does not
    go quiet about her — it names her, gives the range to the nearest station,
    and says that this is an argument for a surface unit or a satellite tasking
    rather than for waiting. That is the deferral reason `no_camera_in_reach`,
    and it is the half of the automation an operator has to be able to see.
    """
    v = V(world, "eo_beyond_reach")
    t0 = week(5, hours=9)
    legs = [
        Leg("transit", target=_offshore_of("SYN-RAT", 265.0, 155.0),
            speed_kn=12.0),
        Leg("station", duration_h=6.0, radius_m=3000.0),
        Leg("transit", target=_offshore_of("SYN-VEN", 260.0, 150.0),
            speed_kn=12.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-MUM", 245.0, 150.0), start_time=t0,
        legs=legs), world.rng)
    emit(world, "eo_beyond_reach", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="O4", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("A genuine type misdeclaration that this sensor cannot reach."),
        capability_boundary=(
            "A coastal camera on a 30 m tower is useful against a merchant to "
            "about 20 km, and the image is only good enough to contradict a "
            "declared identity inside about 8 km. She works 150 km offshore "
            "for the whole window, so every slot defers her with the range to "
            "the nearest station attached. No threshold change reaches her — "
            "it needs a different sensor, and the system should be able to say "
            "where and how far.")))


# ==========================================================================
# O5 — a real lie the available light cannot settle
# ==========================================================================

def o5_night_only_lie(world: ScenarioWorld) -> None:
    """The same lie as O2, on a hull the camera only ever sees in the dark.

    A thermal head resolves a hull's outline and roughly where her
    accommodation sits. It does not resolve deck fittings, and deck fittings are
    the entire difference between a product tanker and a cargo ship with
    derricks. So the system photographs her, classifies her honestly as a
    merchant, and reports that the image rules out no AIS ship-type family —
    which is a different answer from "her paperwork is fine" and the surface has
    to be able to render both.
    """
    v = V(world, "eo_night_liar")
    # Two night passages, so the finding does not hinge on one visibility draw.
    # 19:00-23:00 UTC at 70°E is roughly midnight to 04:00 local: full dark.
    t0 = week(6, hours=19)
    legs = [
        Leg("transit", target=_offshore_of("SYN-DIU", 240.0, 5.0),
            speed_kn=10.0),
        Leg("transit", target=_offshore_of("SYN-DIU", 200.0, 5.0),
            speed_kn=10.0),
        Leg("station", duration_h=9.0, radius_m=2500.0),
        Leg("transit", target=_offshore_of("SYN-DIU", 215.0, 6.0),
            speed_kn=9.0),
    ]
    pts = generate_track(v, VoyagePlan(
        start=_offshore_of("SYN-DIU", 260.0, 12.0), start_time=t0, legs=legs),
        world.rng)
    emit(world, "eo_night_liar", pts)

    world.truth.add(ScenarioTruth(
        scenario_id="O5", scenario_family=FAMILY_BOUNDARY,
        truth_class=DELIBERATE_MISS,
        entity_ids=[v.entity_id], t_start=t0, t_end=pts[-1].t,
        expected_detection=False,
        notes=("A genuine type misdeclaration the available light cannot "
               "settle."),
        capability_boundary=(
            "She is inside camera range only between midnight and dawn local "
            "time, when the head is on its thermal channel. A thermal image is "
            "a silhouette: it carries length, slenderness and roughly where the "
            "accommodation sits, and it does not carry a deck. The measured "
            "vocabulary at night collapses tanker and dry cargo into one label, "
            "so the image rules out no family and the rule declines. Reporting "
            "a contradiction from it would be asserting the one feature the "
            "image failed to record.")))


SCENARIOS = (
    o1_tanker_declaring_fishing,
    o2_crane_ship_declaring_tanker,
    o3_family_cousin_decoy,
    o4_beyond_camera_reach,
    o5_night_only_lie,
)
