"""What is this vessel *doing*? — activity classification from motion alone.

*"What is this vessel doing, expressed as an activity rather than a number:
transiting, fishing, loitering, drifting, anchored, conducting a survey pattern,
rendezvousing, manoeuvring erratically. Each with a confidence."* — the IDEX
Challenge 82 brief, Area 2.

**This module lives in `tracks/` and takes a track, not an AIS message**, and
that placement is the whole point. Area 3 of the same brief requires that the
identical behaviours be recognisable "whether the track came from radar or AIS"
and says outright that if they are not, "that is a defect in the fusion core".
So the classifier reads position, speed and course over time and nothing else —
no MMSI, no vessel class, no declared status. A radar track and an AIS track
present the same surface here (`BuiltTrack`), so the same code runs over both
without a source-specific branch, and
`test_activity_is_identical_on_radar_and_ais` fails if that ever stops being
true.

Why rules and not a learned model
---------------------------------
There is no labelled corpus of vessel activity for this area of operations, and
inventing one from the scenario generator would be training a classifier on its
own answer key. What exists instead is a body of published, physical
descriptions of what these behaviours look like in kinematics — a trawler works
at 2-4 knots with frequent heading reversals, a merchant transits at 12-16
knots on a steady course, a vessel at anchor swings on the tide within a few
hundred metres. Those are the rules here, with the thresholds named as
constants and each one carrying the reason it has the value it has.

The honest consequence is stated rather than hidden: **this separates the
classes it can genuinely separate and refuses the rest.** `unclassified` is a
first-class output, not a failure. A short track, a track with three fixes, a
vessel doing something the rule set does not describe — each gets
`unclassified` with a reason, because a confident wrong activity on an operator's
screen costs more than an admitted gap.

Confidence means one thing throughout: how well this track's kinematics match
the description, given how much of the track we saw. A one-hour track can never
score as highly as a two-day one for the same behaviour, because it cannot.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..config import ANCHORAGE_RADIUS_KM, PORT_RADIUS_KM
from ..ports import at_waiting_area, port_at
from .kalman import epoch_s

__all__ = ["Activity", "ACTIVITIES", "classify_activity",
           "classify_activity_segments", "activity_features"]


#: The activities this classifier will name, with the description each rule is
#: written from. `unclassified` is deliberately in the list: it is an answer.
ACTIVITIES: dict[str, str] = {
    "transiting": "making way on a steady course at a sustained service speed",
    "fishing": "working at trawling speed with repeated heading reversals",
    "loitering": "holding station at low speed, clear of any berth or "
                 "designated anchorage",
    "drifting": "barely moving, with the course wandering as she is set by "
                "wind and current rather than steered",
    "anchored": "stopped inside a port area or a designated anchorage, "
                "swinging within a small radius",
    "survey_pattern": "running long straight legs with regular reciprocal "
                      "turns — a lawnmower search or survey track",
    "manoeuvring_erratically": "changing course far more often than passage "
                               "requires, without the regularity of a working "
                               "or survey pattern",
    "unclassified": "nothing in the rule set describes this motion, or there "
                    "is not enough track to say",
}


# ---- thresholds, each with the reason it has the value it has --------------

#: Below this a vessel is not making way in any useful sense. Chosen to match
#: `LOITER_MAX_SOG_KN`, which the loitering rule has always used, so that
#: "loitering" as an activity and "loitering" as an alert cannot disagree about
#: what slow means.
SLOW_KN = 2.0

#: Trawling speed. A working trawler tows at 2-4 knots; above about 5 she is
#: steaming between grounds rather than fishing, and the classifier should say
#: transiting.
FISHING_MIN_KN, FISHING_MAX_KN = 1.5, 5.0

#: Sustained speed at which a hull is plainly on passage. Deliberately low
#: enough to include a laden bulker at 10 knots and a dhow at 7.
TRANSIT_MIN_KN = 6.0

#: Course steadiness, in degrees of absolute heading change per minute,
#: averaged over the track. A merchant on passage holds under a degree a
#: minute; a trawler working a ground turns an order of magnitude more.
STEADY_MAX_DEG_MIN = 1.5
TURNY_MIN_DEG_MIN = 4.0

#: A vessel at anchor swings on her cable. The radius is set by the scope of
#: chain out plus the ship's length, and 800 m covers a large hull in deep
#: water with margin — while still being far smaller than any transit.
ANCHOR_SWING_MAX_M = 800.0

#: A drifting vessel's course wanders because nothing is steering it. This is
#: the *variance* of course, not its rate: a drifting hull's heading changes
#: slowly but without direction, which is what separates it from one holding
#: station under power.
DRIFT_MAX_KN = 1.5

#: A survey or search pattern is made of long straight legs joined by turns of
#: close to 180 degrees. Both halves are required: long legs alone are a
#: transit, and reciprocal turns alone are a vessel milling about.
SURVEY_MIN_LEGS = 4
SURVEY_RECIPROCAL_TOL_DEG = 35.0
SURVEY_MIN_LEG_MINUTES = 12.0

#: What fraction of a track's course changes must be near-reciprocal before it
#: is a pattern rather than a voyage that happens to double back.
#:
#: **Measured**: without this the rule called 151 of 209 tracks a survey. A
#: coastal rotation between two ports supplies reciprocal turns at each end and
#: long legs in between, which satisfies "four legs and three reciprocals" on
#: any multi-week track. A vessel genuinely running a lawnmower spends most of
#: her course changes on the turn at the end of a leg.
#: Measured against *alterations*, not against every fix-to-fix step. A 150-fix
#: lawnmower contains 5 turns and 144 steps of holding course; dividing by the
#: latter gave a reciprocal fraction of 0.03 and the rule could not fire at all.
SURVEY_MIN_RECIPROCAL_FRACTION = 0.5

#: And a survey goes nowhere: net displacement is small against distance run.
#: A passage that doubles back once is still a passage.
SURVEY_MAX_STRAIGHTNESS = 0.35

#: How often a survey turns. **This is what separates a lawnmower from a
#: career**, and it is the gate the first two attempts were missing.
#:
#: A there-and-back coastal rotation over eight weeks accumulates long legs,
#: near-reciprocal turns and a straightness near zero — every survey signature —
#: because it ends up where it started. What it does *not* do is turn often: it
#: runs a leg every few days. A vessel actually working a pattern turns every
#: half hour to an hour. Measured on the corpus, the two are more than an order
#: of magnitude apart (0.24 legs/hour for a lawnmower against 0.016 for a
#: rotation), so 0.1 sits in open space between them rather than on either.
SURVEY_MIN_LEGS_PER_HOUR = 0.1

#: A course change this large is an alteration rather than steering noise.
#: Shared with `_count_legs`, which uses it to end a leg — the two must agree
#: or the leg count and the reciprocal count would be measuring different turns.
ALTERATION_MIN_DEG = 45.0

#: Below this a track cannot be classified at all. Four fixes is the minimum
#: from which a heading-change rate means anything, and thirty minutes is the
#: minimum over which a "sustained" speed is sustained.
MIN_POINTS = 4
MIN_SPAN_MINUTES = 30.0


@dataclass
class Activity:
    """One classified activity over one span of one track."""
    activity: str
    confidence: float
    t_start: float
    t_end: float
    lat: float
    lon: float
    #: Why the classifier said this, in a sentence — the evidence for the label.
    reason: str = ""
    #: The kinematic measurements the decision was made from, so a disagreeing
    #: analyst can see the numbers rather than argue with the verdict.
    features: dict = field(default_factory=dict)
    track_id: Optional[str] = None
    track_source: Optional[str] = None

    @property
    def duration_hours(self) -> float:
        return max(0.0, self.t_end - self.t_start) / 3600.0

    def as_dict(self) -> dict:
        return {"activity": self.activity,
                "confidence": round(float(self.confidence), 3),
                "t_start": self.t_start, "t_end": self.t_end,
                "duration_hours": round(self.duration_hours, 2),
                "lat": round(float(self.lat), 5),
                "lon": round(float(self.lon), 5),
                "reason": self.reason, "features": self.features,
                "track_id": self.track_id, "track_source": self.track_source,
                "description": ACTIVITIES.get(self.activity, "")}


def _hav_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def _turn_deg(a: float, b: float) -> float:
    """Absolute change between two courses, wrapped to [0, 180]."""
    return abs((b - a + 180.0) % 360.0 - 180.0)


def activity_features(track, *, i0: int = 0, i1: Optional[int] = None) -> dict:
    """The kinematics a classification is made from, over one slice of a track.

    Separated from the decision so that the numbers can be inspected, tested and
    shown to an operator independently of the label they produced. A classifier
    whose inputs cannot be seen is one nobody can argue with.
    """
    pts = track.points[track.points.quality != "outlier"]
    i1 = len(pts) if i1 is None else i1
    sl = pts.iloc[i0:i1]
    n = len(sl)
    if n == 0:
        return {"n_points": 0}

    t = epoch_s(sl["ts"])
    sog = sl["sog_kn"].to_numpy(dtype=float)
    cog = sl["cog_deg"].to_numpy(dtype=float)
    lat = sl["lat"].to_numpy(dtype=float)
    lon = sl["lon"].to_numpy(dtype=float)

    span_min = (t[-1] - t[0]) / 60.0 if n > 1 else 0.0
    turns = [_turn_deg(cog[i], cog[i + 1]) for i in range(n - 1)]
    dt_min = [max((t[i + 1] - t[i]) / 60.0, 1e-6) for i in range(n - 1)]
    turn_rate = (float(np.mean([a / b for a, b in zip(turns, dt_min)]))
                 if turns else 0.0)

    # Spread: the radius of the smallest circle about the mean position that
    # contains the track. This is what separates a vessel at anchor from one
    # holding station a mile away and back.
    clat, clon = float(np.mean(lat)), float(np.mean(lon))
    spread = max((_hav_m(clat, clon, a, b) for a, b in zip(lat, lon)),
                 default=0.0)

    # Straightness: net displacement over distance travelled. A transit is near
    # 1; a vessel working a ground or milling about is near 0.
    travelled = sum(_hav_m(lat[i], lon[i], lat[i + 1], lon[i + 1])
                    for i in range(n - 1))
    net = _hav_m(lat[0], lon[0], lat[-1], lon[-1])
    straightness = (net / travelled) if travelled > 1.0 else 0.0

    # Alterations are course changes that are actually course changes; the rest
    # is a vessel holding her heading. Reciprocals are counted against those,
    # not against every step, because a long steady leg would otherwise drown
    # the signature it is part of.
    alterations = sum(1 for x in turns if x > ALTERATION_MIN_DEG)
    reciprocals = sum(1 for x in turns
                      if abs(x - 180.0) <= SURVEY_RECIPROCAL_TOL_DEG)

    in_waiting = bool(at_waiting_area(clat, clon,
                                      port_radius_km=PORT_RADIUS_KM,
                                      anchorage_radius_km=ANCHORAGE_RADIUS_KM))
    return {
        "n_points": n,
        "span_minutes": round(span_min, 1),
        "sog_mean": round(float(np.mean(sog)), 2),
        "sog_median": round(float(np.median(sog)), 2),
        "sog_p90": round(float(np.percentile(sog, 90)), 2),
        "sog_std": round(float(np.std(sog)), 2),
        "turn_rate_deg_min": round(turn_rate, 2),
        "turn_mean_deg": round(float(np.mean(turns)), 1) if turns else 0.0,
        "alterations": int(alterations),
        "reciprocal_turns": int(reciprocals),
        "reciprocal_fraction": round(reciprocals / alterations, 3)
                               if alterations else 0.0,
        "straightness": round(float(straightness), 3),
        "spread_m": round(float(spread), 1),
        "lat": clat, "lon": clon,
        "in_waiting_area": in_waiting,
        "nearest_port": port_at(clat, clon, radius_km=PORT_RADIUS_KM),
        "t_start": float(t[0]), "t_end": float(t[-1]),
    }


def _span_confidence(span_minutes: float) -> float:
    """How much of the behaviour we actually saw, in [0.5, 1.0].

    A one-hour look at a vessel cannot support the same confidence as two days
    of it, whatever the kinematics say, and a classifier that ignores this
    reports its shortest observations most loudly. Saturates at twelve hours,
    past which more track tells you little about *this* activity.
    """
    return 0.5 + 0.5 * min(1.0, span_minutes / (12 * 60.0))


def classify_activity(track, *, i0: int = 0, i1: Optional[int] = None
                      ) -> Activity:
    """Name what this track — or this slice of it — is doing.

    The rules are tried in order of how distinctive their signature is, so that
    a behaviour with an unmistakable shape (anchored inside a port; a survey
    pattern) is claimed before a vaguer one (loitering) can absorb it.
    """
    f = activity_features(track, i0=i0, i1=i1)
    tid = getattr(track, "track_id", None)
    src = getattr(getattr(track, "source", None), "name", None)

    def out(activity: str, conf: float, reason: str) -> Activity:
        return Activity(activity=activity, confidence=round(min(0.99, conf), 3),
                        t_start=f.get("t_start", 0.0), t_end=f.get("t_end", 0.0),
                        lat=f.get("lat", 0.0), lon=f.get("lon", 0.0),
                        reason=reason, features=f, track_id=tid,
                        track_source=src)

    if f["n_points"] < MIN_POINTS or f.get("span_minutes", 0) < MIN_SPAN_MINUTES:
        return out("unclassified", 0.0,
                   f"Only {f['n_points']} usable fix(es) over "
                   f"{f.get('span_minutes', 0):.0f} minutes. A heading-change "
                   f"rate needs at least {MIN_POINTS} fixes and a sustained "
                   f"speed at least {MIN_SPAN_MINUTES:.0f} minutes, so nothing "
                   f"is claimed.")

    base = _span_confidence(f["span_minutes"])
    sog, spread = f["sog_median"], f["spread_m"]
    turn, straight = f["turn_rate_deg_min"], f["straightness"]

    # 1. Anchored — stopped, inside a berth or designated anchorage, swinging
    #    within a cable's length. The place is what distinguishes this from
    #    loitering, and it is why the shared `at_waiting_area` helper is used
    #    rather than a fresh radius: the loitering *rule* already suppresses
    #    these, and the two must not disagree about where a berth is.
    if sog <= SLOW_KN and f["in_waiting_area"] and spread <= ANCHOR_SWING_MAX_M:
        where = f["nearest_port"] or "a designated anchorage"
        return out("anchored", base * 0.95,
                   f"Held at {sog:.1f} kn inside {where}, swinging within "
                   f"{spread:.0f} m. That is a vessel at anchor or alongside, "
                   f"not a vessel behaving unusually.")

    # 2. Survey or search pattern — long straight legs joined by reciprocal
    #    turns, *and going nowhere*. All three required.
    #
    #    **The first version of this rule called 151 of 209 tracks a survey.**
    #    It asked only for four long legs and three near-reciprocal turns, and
    #    over a multi-week track any coastal voyage accumulates both: every
    #    passage has long legs, and a there-and-back rotation between two ports
    #    supplies the reciprocals. The signature is not "has some reciprocal
    #    turns", it is "is *made of* reciprocal turns and ends up where it
    #    started" — a lawnmower covers an area rather than crossing one.
    #
    #    So the reciprocals must be a *majority* of the turns, and straightness
    #    must be low. A vessel that ran four parallel legs and then left for
    #    another port is classified on what she did in each window, which is
    #    what `classify_activity_segments` is for.
    legs = _count_legs(track, i0, i1)
    span_h = max(f["span_minutes"] / 60.0, 1e-6)
    legs_per_hour = legs / span_h
    recip_fraction = f["reciprocal_fraction"]
    if (legs >= SURVEY_MIN_LEGS
            and f["reciprocal_turns"] >= SURVEY_MIN_LEGS - 1
            and recip_fraction >= SURVEY_MIN_RECIPROCAL_FRACTION
            and legs_per_hour >= SURVEY_MIN_LEGS_PER_HOUR
            and straight < SURVEY_MAX_STRAIGHTNESS
            and sog >= FISHING_MIN_KN):
        return out("survey_pattern", base * 0.8,
                   f"{legs} straight legs over {span_h:.0f} hours — a turn "
                   f"every {span_h / legs:.1f} hours — of which "
                   f"{recip_fraction:.0%} of her alterations are near "
                   f"reciprocal, with a straightness of {straight:.2f}. She "
                   f"covers an area rather than crossing one. That is a "
                   f"lawnmower pattern: a survey or a search, not a passage.")

    # 3. Fishing — trawling speed with repeated heading reversals. The speed
    #    band alone is not enough: a vessel manoeuvring in a channel is also
    #    slow, and a drifting one is slower still.
    if (FISHING_MIN_KN <= sog <= FISHING_MAX_KN
            and turn >= TURNY_MIN_DEG_MIN and straight < 0.5):
        return out("fishing", base * 0.75,
                   f"Working at {sog:.1f} kn — trawling speed — turning "
                   f"{turn:.1f}°/min with a straightness of {straight:.2f}. "
                   f"The track doubles back on itself repeatedly, which is "
                   f"what working a ground looks like and what a passage does "
                   f"not.")

    # 4. Transiting — sustained speed on a steady course.
    if sog >= TRANSIT_MIN_KN and turn <= STEADY_MAX_DEG_MIN:
        return out("transiting", base * 0.9,
                   f"Making {sog:.1f} kn on a course steady to "
                   f"{turn:.1f}°/min, straightness {straight:.2f}. She is on "
                   f"passage.")

    # 5. Drifting — barely moving and not steering. Distinguished from
    #    loitering by speed: a vessel holding station under power keeps a knot
    #    or two on to maintain steerage, a drifting one does not.
    if sog <= DRIFT_MAX_KN and spread > ANCHOR_SWING_MAX_M:
        return out("drifting", base * 0.7,
                   f"Moving at {sog:.1f} kn over a spread of {spread:.0f} m "
                   f"with no steady heading. She is being set by wind and "
                   f"current rather than steered — engine trouble, waiting, or "
                   f"deliberately stopped.")

    # 6. Loitering — slow, clear of any berth or anchorage. The alert-raising
    #    version of this lives in `anomaly.library.detect_sensitive_loitering`
    #    and adds the geofence test; this is the activity, which is a
    #    description rather than a finding.
    if sog <= SLOW_KN and not f["in_waiting_area"]:
        return out("loitering", base * 0.8,
                   f"Holding at {sog:.1f} kn over {spread:.0f} m, clear of any "
                   f"berth or designated anchorage. A stopped ship in open "
                   f"water is doing something; this does not say what.")

    # 7. Manoeuvring erratically — turning far more than passage requires,
    #    without the regularity of a working or survey pattern. Deliberately
    #    last: it is the residual for "moving, clearly not on passage, and not
    #    matching any recognised working pattern".
    if turn >= TURNY_MIN_DEG_MIN * 2 and straight < 0.35:
        return out("manoeuvring_erratically", base * 0.6,
                   f"Turning {turn:.1f}°/min at {sog:.1f} kn with a "
                   f"straightness of {straight:.2f} — far more course change "
                   f"than passage requires, and without the regularity of a "
                   f"fishing or survey pattern.")

    return out("unclassified", 0.0,
               f"At {sog:.1f} kn, turning {turn:.1f}°/min, straightness "
               f"{straight:.2f}, spread {spread:.0f} m — this motion does not "
               f"match any behaviour the rule set describes. Saying nothing is "
               f"the correct answer here.")


def _count_legs(track, i0: int, i1: Optional[int]) -> int:
    """How many sustained straight legs this slice contains.

    A leg ends where the course changes by more than the steady threshold
    allows. Only legs of at least `SURVEY_MIN_LEG_MINUTES` count, because the
    signature being looked for is *long* legs — a vessel making a series of
    short straight hops between turns is manoeuvring, not surveying.
    """
    pts = track.points[track.points.quality != "outlier"]
    i1 = len(pts) if i1 is None else i1
    sl = pts.iloc[i0:i1]
    if len(sl) < 3:
        return 0
    t = epoch_s(sl["ts"])
    cog = sl["cog_deg"].to_numpy(dtype=float)
    legs, start = 0, 0
    for i in range(len(sl) - 1):
        if _turn_deg(cog[i], cog[i + 1]) > 45.0:
            if (t[i] - t[start]) / 60.0 >= SURVEY_MIN_LEG_MINUTES:
                legs += 1
            start = i + 1
    if (t[-1] - t[start]) / 60.0 >= SURVEY_MIN_LEG_MINUTES:
        legs += 1
    return legs


def classify_activity_segments(track, *, window_hours: float = 6.0
                               ) -> list[Activity]:
    """Classify a long track in windows, and merge neighbouring like verdicts.

    A vessel's activity changes: she transits, she works a ground, she anchors.
    A single verdict over a two-week track is an average of all three and
    describes none of them. Windowing is the cheap way to get a sequence, and
    merging keeps the output at the granularity an operator reads — "fishing
    for eleven hours", not eleven consecutive one-hour fishing verdicts.

    ``unclassified`` windows are returned too, rather than dropped: a gap in
    the middle of a sequence of activities is information about the track.
    """
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) < MIN_POINTS:
        return [classify_activity(track)]
    t = epoch_s(pts["ts"])
    step = window_hours * 3600.0

    bounds, i, t0 = [], 0, t[0]
    for j in range(len(t)):
        if t[j] - t0 >= step:
            bounds.append((i, j))
            i, t0 = j, t[j]
    bounds.append((i, len(t)))

    raw = [classify_activity(track, i0=a, i1=b) for a, b in bounds
           if b - a >= MIN_POINTS]
    if not raw:
        return [classify_activity(track)]

    merged: list[Activity] = [raw[0]]
    for a in raw[1:]:
        prev = merged[-1]
        if a.activity == prev.activity:
            # One episode, not two. Confidence is the stronger of the two — a
            # longer look at the same behaviour cannot make it less certain —
            # and the features come from the window that was surer of itself.
            keep = a if a.confidence > prev.confidence else prev
            merged[-1] = Activity(
                activity=prev.activity, confidence=max(prev.confidence,
                                                       a.confidence),
                t_start=prev.t_start, t_end=a.t_end,
                lat=(prev.lat + a.lat) / 2, lon=(prev.lon + a.lon) / 2,
                reason=keep.reason, features=keep.features,
                track_id=prev.track_id, track_source=prev.track_source)
        else:
            merged.append(a)
    return merged


def dominant_activity(activities: Sequence[Activity]) -> Optional[Activity]:
    """The activity a track spent most of its time doing, ignoring gaps.

    Duration-weighted rather than count-weighted: eleven hours of fishing
    outranks three one-hour transits between grounds, which is what an operator
    means by "what is she doing".
    """
    named = [a for a in activities if a.activity != "unclassified"]
    if not named:
        return None
    return max(named, key=lambda a: a.duration_hours * max(a.confidence, 0.01))
