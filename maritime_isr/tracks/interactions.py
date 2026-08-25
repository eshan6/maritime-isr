"""What are these two doing *with each other*? — Area 3.

*"Interactions between vessels. Two or more tracks behaving in relation to one
another: converging and holding station, moving in company, one shadowing
another, a transfer pattern. Interactions are a named part of the requirement
and they are where radar earns its keep, because an interaction between a radar
track and a silent contact is exactly the event nobody else can see."* — the
IDEX Challenge 82 brief, Area 3.

What this adds over the encounter primitive
-------------------------------------------
``tracks.features.detect_encounters`` already finds *sustained proximity at low
speed* — two hulls within 500 m at under 2 knots. That is one interaction, and
it is the only one the system could see. It cannot describe two ships steaming
in company two miles apart, or one sitting astern of another for six hours,
because neither is slow and neither is that close.

So this module asks a different question. It takes pairs of tracks that are
**near each other over time** and classifies their *relative* motion: how the
separation behaves, whether the courses agree, whether one holds a constant
bearing from the other. Those are the four behaviours the requirement names.

Three things it refuses to do
-----------------------------
**It does not call a port an interaction.** Every hull in an anchorage is within
a few miles of every other hull, on similar courses, for days. That is what an
anchorage is. ADR-031 recorded the cost of forgetting this: 42 of 43
dark-rendezvous alerts fired inside berths before the shared ``at_waiting_area``
helper was applied to them. The same helper is applied here, at the pair level,
and `test_an_anchorage_is_not_an_interaction` fails if it is removed.

**It does not call a shipping lane an interaction.** Two merchants on the same
customary route at the same speed are "in company" by any geometric test and by
no useful one. Company requires the pair to hold a separation that is *stable* —
a constant distance is a formation; a slow overtake that happens to pass through
two miles is traffic.

**It never claims a pair it did not watch.** Every interaction states how long
it observed the behaviour, and a relationship seen for ten minutes is not
reported at all — the same persistence discipline the radar dark cascade applies
(``RADAR_DARK_MIN_EPOCHS``).

Source-agnostic, like everything in ``tracks/``: it reads position, speed and
course over time and never asks which sensor produced the track. An
AIS-to-radar interaction — a named hull holding station on an unnamed contact —
falls out for free, and it is the single most operationally interesting thing
this module can produce.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Sequence

import h3
import numpy as np

from .. import h3util as tiling
from ..config import (ANCHORAGE_RADIUS_KM, PIPELINE_VERSION, PORT_RADIUS_KM)
from ..ports import at_waiting_area
from .features import RESAMPLE_S, resample_track

__all__ = ["Interaction", "INTERACTIONS", "detect_interactions"]


#: The interaction vocabulary, with what each rule is written from.
INTERACTIONS: dict[str, str] = {
    "moving_in_company": "two vessels holding a steady separation on the same "
                         "course for a sustained period — a formation, not "
                         "two ships that happen to share a lane",
    "shadowing": "one vessel holding station astern of another, matching her "
                 "course and speed, at a distance that does not close",
    "converging_and_holding": "two vessels closing on each other and then "
                              "stopping together in open water",
    "transfer_pattern": "close alongside at near-zero speed for long enough to "
                        "pass cargo — the ship-to-ship signature",
}

#: The H3 resolution pair candidates are bucketed at. Same hash-join discipline
#: the whole architecture turns on (CLAUDE.md §3) — no O(n²) distance sweep over
#: 1,300 tracks.
#:
#: **The ring size is computed, not assumed, and the first version assumed.** It
#: bucketed at res 6, took the one-ring, and the comment said that was "roughly
#: 9 km, which comfortably contains the widest interaction this module will
#: claim". It is not. A res-6 hexagon has an edge of about 3.7 km, so two points
#: 9.26 km apart can sit near opposite far corners of cells nearly three rings
#: apart, and every such pair was dropped before it was ever measured. The
#: failure is invisible from the outside: it produces *fewer* findings, never
#: wrong ones, so the module reported "no interactions in this corpus" with
#: total confidence. See `_ring_for`.
PAIR_RES = tiling.R6

#: Widest separation at which a pair is still a **candidate**. Two ships five
#: miles apart are in visual range of each other in good weather; ten miles
#: apart they are simply both at sea. This bounds the search, not the claim.
MAX_SEPARATION_M = 9260.0                      # 5 nautical miles

#: Widest separation at which **company or shadowing** will be claimed.
#:
#: **This is the gate that actually separates a formation from traffic, and
#: persistence never was.** Measured over two independent corpus samples (the
#: RNG stream shifts when the cast changes, so the background pairs are a fresh
#: draw each time), mean separation of every pair the rule reported:
#:
#:     coincidental pairs, sample 1   6555  8227  7397  6409  8052
#:     coincidental pairs, sample 2   5745  5337  6100  6651  7764  7814
#:     authored relationships          589  1191  4245
#:
#: Eleven coincidental pairs across two samples, none closer than 5,337 m; the
#: three authored relationships all inside 4,245 m. The populations do not
#: overlap and the gap is wide.
#:
#: Duration does not separate them at all, which is why the first attempt to
#: gate on it failed twice. In sample 1 the longest coincidental pair ran 4.7
#: hours and a 6-hour floor looked decisive; in sample 2 a pair of fishing-fleet
#: hulls steaming to the same ground held station for **11.7 hours**, longer
#: than two of the three authored relationships. A fishing fleet transiting
#: together is a formation by every geometric test and by no useful one, and no
#: persistence floor will ever exclude it.
#:
#: Visual range is not station-keeping range. Vessels deliberately working
#: together stay close enough to manoeuvre and talk — a mile or two. At four
#: miles on the same heading they are on the same route.
#:
#: The floor rests on the coincidental population, which nobody authored. The
#: authored positives establish only that 2.5 nm does not exclude a real
#: relationship; the margin above the closest coincidence is 9%, which is
#: thinner than this project would like and is stated rather than rounded away.
COMPANY_MAX_SEPARATION_M = 4630.0              # 2.5 nautical miles

#: Closest approach at which a pair is "alongside" rather than "in company".
ALONGSIDE_M = 500.0

#: How long a behaviour must persist before it is reported.
#:
#: **Re-derived after the candidate search was fixed, because the first sweep
#: was run through a broken one.** The earlier value of 120 minutes came from a
#: sweep that saw 8 pairs at 60 minutes and 0 at 120, and concluded the corpus
#: contained no formations. Roughly half of all cross-cell pairs were being
#: discarded before any test ran (see `PAIR_RES` and `consider`), and the corpus
#: contained no *authored* formations either — so the sweep was measuring the
#: bug and the gap at once, and neither was visible in the output.
#:
#: Swept again on the fixed search, over a corpus that now contains three
#: authored relationships (group F) and the same untouched background fleet:
#:
#:     min_minutes   found   authored   background
#:     60               11          3            8
#:     120               8          3            5
#:     240               6          3            3
#:     300               3          3            0
#:     360               3          3            0
#:     480               3          3            0
#:     600               2          2            0
#:
#: In that sample the longest coincidental pair ran 4.7 hours, and a six-hour
#: floor looked decisive. **A second sample falsified that, and the correction
#: matters more than the number.** Changing the cast shifts the generator's RNG
#: stream, so the background pairs are a fresh draw; in the next corpus a pair
#: of fishing-fleet hulls steaming to the same ground held course and station
#: for **11.7 hours** — longer than two of the three authored relationships.
#:
#: A fishing fleet transiting together is a formation by every geometric test
#: and by no useful one, and **no persistence floor will ever exclude it.**
#: Duration is not the discriminator here and the first sweep only appeared to
#: show that it was because it had one sample and a broken candidate search.
#: What does separate the populations is how close they hold: see
#: `COMPANY_MAX_SEPARATION_M`, which is the gate that does the work.
#:
#: Six hours is kept as a persistence floor because a relationship worth an
#: operator's time does persist, and because it costs nothing now that the
#: separation gate carries the discrimination. It is not doing the work and is
#: no longer described as though it were.
MIN_MINUTES = 360.0

#: Courses this close count as the same course.
SAME_COURSE_DEG = 20.0

#: Both must be making way for company or shadowing to mean anything —
#: otherwise it describes two ships stopped near each other, which is the
#: converging case or an anchorage.
UNDERWAY_MIN_KN = 4.0

#: A formation holds its separation. Expressed as the standard deviation of the
#: separation over the episode, relative to its mean: a pair whose distance
#: wanders by more than this fraction of its own size is overtaking, not formed
#: up. Measured in the same sweep: tightening it from 0.33 to 0.15 removed two
#: of the eight background pairs at 60 minutes and nothing at 120, so it is a
#: secondary gate — persistence is what actually separates the populations.
STABLE_SEPARATION_CV = 0.25

#: Shadowing is company with a *bearing* constraint: the follower sits within
#: this many degrees of dead astern of the leader, consistently.
ASTERN_TOLERANCE_DEG = 50.0

#: And the transfer case: alongside, both essentially stopped.
TRANSFER_MAX_SOG_KN = 2.0


#: Cells covered by a port or a designated anchorage, computed once.
#:
#: **The anchorage exclusion has to happen before bucketing, not after.**
#: Rejecting an anchorage pair at classification time is correct and far too
#: late: every hull in an anchorage is near every other hull for days, so the
#: candidate set is dominated by exactly the pairs that will be thrown away.
#: Measured on the corpus, the pair-sample guard tripped at 200,000 before this
#: filter existed.
#:
#: Testing `at_waiting_area` per resampled point is the obvious fix and is also
#: too slow — 3.1 million points against 35 gazetteer entries is 100 million
#: haversines. Precomputing the *cells* turns it into a set lookup, which is the
#: same hash-join move the rest of the architecture is built on (CLAUDE.md §3).
_WAITING_CELLS: Optional[frozenset] = None


def _waiting_area_cells() -> frozenset:
    global _WAITING_CELLS
    if _WAITING_CELLS is not None:
        return _WAITING_CELLS
    from ..ports import ANCHORAGES, PORTS

    # A res-6 cell is ~3.2 km across, so k rings reach roughly 3.2*k km. Round
    # up so the covering is never smaller than the radius it stands for — an
    # under-covered anchorage is one whose edge still leaks pairs.
    cells: set[str] = set()
    for gaz, radius_km in ((PORTS, PORT_RADIUS_KM),
                           (ANCHORAGES, ANCHORAGE_RADIUS_KM)):
        k = max(1, int(math.ceil(radius_km / 3.2)))
        for lat, lon in gaz.values():
            cells.update(tiling.disk(tiling.cell(lat, lon, PAIR_RES), k))
    _WAITING_CELLS = frozenset(cells)
    return _WAITING_CELLS


def _hav_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def _bearing_deg(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _ang_diff(a: float, b: float) -> float:
    return abs((float(a) - float(b) + 180.0) % 360.0 - 180.0)


@dataclass
class Interaction:
    """One relationship between two tracks, over one span."""
    kind: str
    track_id_a: str
    track_id_b: str
    t_start: float
    t_end: float
    lat: float
    lon: float
    confidence: float
    #: Which track is the follower, for `shadowing`. None otherwise.
    follower: Optional[str] = None
    mean_separation_m: float = 0.0
    min_separation_m: float = 0.0
    reason: str = ""
    detail: dict = field(default_factory=dict)
    source_a: Optional[str] = None
    source_b: Optional[str] = None
    #: True when the two tracks came from different sensors — the case the
    #: requirement most wants, because a named hull interacting with an unnamed
    #: contact is the event no single sensor can see.
    cross_sensor: bool = False
    pipeline_version: str = PIPELINE_VERSION

    @property
    def duration_hours(self) -> float:
        return max(0.0, self.t_end - self.t_start) / 3600.0

    @property
    def interaction_id(self) -> str:
        a, b = sorted((self.track_id_a, self.track_id_b))
        return "itx_" + hashlib.sha1(
            f"{self.kind}|{a}|{b}|{self.t_start:.0f}".encode()).hexdigest()[:12]

    def as_dict(self) -> dict:
        return {
            "interaction_id": self.interaction_id, "kind": self.kind,
            "track_id_a": self.track_id_a, "track_id_b": self.track_id_b,
            "source_a": self.source_a, "source_b": self.source_b,
            "cross_sensor": self.cross_sensor,
            "t_start": self.t_start, "t_end": self.t_end,
            "duration_hours": round(self.duration_hours, 2),
            "lat": round(float(self.lat), 5), "lon": round(float(self.lon), 5),
            "confidence": round(float(self.confidence), 3),
            "follower": self.follower,
            "mean_separation_m": round(float(self.mean_separation_m), 1),
            "min_separation_m": round(float(self.min_separation_m), 1),
            "reason": self.reason, "detail": self.detail,
            "description": INTERACTIONS.get(self.kind, ""),
            "pipeline_version": self.pipeline_version,
        }


def _runs(samples: list[tuple], gap_s: float) -> list[list[tuple]]:
    """Split time-ordered samples into runs of consecutive grid steps."""
    if not samples:
        return []
    samples.sort()
    out, run = [], [samples[0]]
    for s in samples[1:]:
        if s[0] - run[-1][0] <= gap_s:
            run.append(s)
        else:
            out.append(run)
            run = [s]
    out.append(run)
    return out


def _ring_for(max_separation_m: float, res: int = PAIR_RES) -> int:
    """How many H3 rings must be searched to reach `max_separation_m`.

    Two points at most D apart sit in cells whose centres are at most
    ``D + 2·R`` apart, where R is the furthest a point can be from its own
    cell's centre — the hexagon's circumradius, which for H3 is its edge
    length. Adjacent cell centres are ``edge·√3`` apart, so the ring needed is
    ``ceil((D + 2·edge) / (edge·√3))``.

    Stated as arithmetic rather than as a constant because the constant was
    wrong and looked right: a one-ring search at res 6 reaches about 6 km, and
    this module claims 9.26 km. Every pair in the 6-9 km band was dropped
    before it was measured, and nothing in the output said so.
    """
    edge_m = h3.average_hexagon_edge_length(res, unit="km") * 1000.0
    return max(1, math.ceil((max_separation_m + 2.0 * edge_m)
                            / (edge_m * math.sqrt(3.0))))


def detect_interactions(tracks: Sequence, *,
                        max_separation_m: float = MAX_SEPARATION_M,
                        min_minutes: float = MIN_MINUTES,
                        max_pairs: int = 200_000) -> list[Interaction]:
    """Classify the relative motion of every pair of tracks that stays near.

    Candidate pairs come from an H3 bucket join at :data:`PAIR_RES` plus its
    one-ring, which bounds the work: on a picture of 1,300 tracks a distance
    sweep is 850,000 pairs per epoch and the hash join is a few hundred.

    ``max_pairs`` is a guard, not a tuning knob. A pathological picture — every
    vessel in one anchorage — could still generate a very large candidate set,
    and silently taking minutes is worse than saying so.
    """
    ring = _ring_for(max_separation_m)
    rs = {tr.track_id: resample_track(tr) for tr in tracks}
    by_id = {tr.track_id: tr for tr in tracks}

    skip = _waiting_area_cells()
    buckets: dict[tuple[float, str], list] = defaultdict(list)
    n_in_port = 0
    for tid, df in rs.items():
        if df is None or len(df) == 0:
            continue
        for r in df.itertuples():
            cell = tiling.cell(r.lat, r.lon, PAIR_RES)
            if cell in skip:
                # Inside a berth or a designated anchorage. Not an interaction
                # by definition, and dropping it here rather than at
                # classification time is what keeps the candidate set finite.
                n_in_port += 1
                continue
            buckets[(r.t, cell)].append((tid, r.lat, r.lon, r.sog_kn))

    # (a, b) -> [(t, sep_m, bearing_ab, sog_a, sog_b, cog_a, cog_b, lat, lon)]
    pairs: dict[tuple[str, str], list] = defaultdict(list)
    n_pairs = 0

    def consider(t, a, b) -> None:
        """One candidate pair-sample, keyed on the pair in a fixed order.

        **Ordering the key rather than dropping one direction is the fix to the
        second half of the same bug.** The first version skipped the sample
        whenever `a`'s track id sorted after `b`'s — a guard meant to count each
        unordered pair once. It works for two hulls in the *same* cell, where
        both orderings are generated. Across cells only one ordering is ever
        generated (the lower cell contributes `members`, the higher contributes
        candidates), so the guard was not deduplicating: it was discarding,
        on a coin flip between two unrelated orderings, roughly half of every
        cross-cell pair in the picture.
        """
        nonlocal n_pairs
        if a[0] == b[0]:
            return
        # Two hypotheses of the same target are not two vessels — the same
        # guard `detect_encounters` needed (ADR-028), keyed on `track_key`
        # because a radar track has no MMSI and `None == None` would discard
        # every radar pair.
        if by_id[a[0]].track_key == by_id[b[0]].track_key:
            return
        sep = _hav_m(a[1], a[2], b[1], b[2])
        if sep > max_separation_m:
            return
        n_pairs += 1
        if n_pairs > max_pairs:
            raise RuntimeError(
                f"interaction search exceeded {max_pairs:,} candidate "
                f"pair-samples ({n_in_port:,} in-port samples were "
                f"already excluded). The picture is denser than this "
                f"module was sized for; narrow the window or raise "
                f"max_pairs deliberately rather than by accident.")
        if a[0] > b[0]:
            a, b = b, a
        pairs[(a[0], b[0])].append(
            (a[0], b[0], t, sep, a[1], a[2], b[1], b[2], a[3], b[3]))

    for (t, cell), members in buckets.items():
        # Same cell: each unordered pair once, by index.
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                consider(t, members[i], members[j])
        # Neighbouring cells: each unordered *cell* pair once, then every
        # cross-cell member pair within it.
        for nc in tiling.neighbors(cell, ring):
            if nc <= cell:
                continue
            for a in members:
                for b in buckets.get((t, nc), ()):
                    consider(t, a, b)

    need = max(2, int(min_minutes * 60 / RESAMPLE_S))
    out: list[Interaction] = []
    for (ta, tb), samples in pairs.items():
        for run in _runs([(s[2], s) for s in samples], RESAMPLE_S * 1.5):
            if len(run) < need:
                continue
            itx = _classify_run(by_id[ta], by_id[tb], [r[1] for r in run])
            if itx is not None:
                out.append(itx)
    return out


def _course_at(track, t: float) -> Optional[float]:
    """The track's own course nearest a moment, from its raw fixes."""
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) == 0:
        return None
    from .kalman import epoch_s
    ts = epoch_s(pts["ts"])
    i = int(np.searchsorted(ts, t))
    i = min(max(i, 0), len(ts) - 1)
    return float(pts["cog_deg"].iloc[i])


def _classify_run(track_a, track_b, run: list) -> Optional[Interaction]:
    """Name the relationship in one sustained run of proximity, or None."""
    t0, t1 = run[0][2], run[-1][2]
    seps = np.array([r[3] for r in run], dtype=float)
    sog_a = np.array([r[8] for r in run], dtype=float)
    sog_b = np.array([r[9] for r in run], dtype=float)
    lat = float(np.mean([r[4] for r in run] + [r[6] for r in run]))
    lon = float(np.mean([r[5] for r in run] + [r[7] for r in run]))

    # **A port is not an interaction.** Every hull in an anchorage is near every
    # other hull for days. The shared helper, at the pair's own position.
    if at_waiting_area(lat, lon, port_radius_km=PORT_RADIUS_KM,
                       anchorage_radius_km=ANCHORAGE_RADIUS_KM):
        return None

    mean_sep = float(np.mean(seps))
    min_sep = float(np.min(seps))
    cv = float(np.std(seps) / mean_sep) if mean_sep > 1.0 else 1.0
    hours = (t1 - t0) / 3600.0
    both_underway = bool(np.median(sog_a) >= UNDERWAY_MIN_KN
                         and np.median(sog_b) >= UNDERWAY_MIN_KN)
    both_stopped = bool(np.median(sog_a) <= TRANSFER_MAX_SOG_KN
                        and np.median(sog_b) <= TRANSFER_MAX_SOG_KN)

    src_a = getattr(getattr(track_a, "source", None), "name", None)
    src_b = getattr(getattr(track_b, "source", None), "name", None)
    common = dict(track_id_a=track_a.track_id, track_id_b=track_b.track_id,
                  t_start=t0, t_end=t1, lat=lat, lon=lon,
                  mean_separation_m=mean_sep, min_separation_m=min_sep,
                  source_a=src_a, source_b=src_b,
                  cross_sensor=bool(src_a and src_b and src_a != src_b))

    # How much of the run we saw, capped — a six-hour relationship is more
    # believable than a one-hour one, and a two-day one is not more believable
    # than a six-hour one.
    span_conf = 0.5 + 0.4 * min(1.0, hours / 6.0)

    # 1. Transfer — alongside and both stopped, in open water.
    if min_sep <= ALONGSIDE_M and both_stopped:
        return Interaction(
            kind="transfer_pattern", confidence=min(0.95, span_conf),
            reason=(f"Closed to {min_sep:.0f} m and both held under "
                    f"{TRANSFER_MAX_SOG_KN:.0f} kn for {hours:.1f} hours, "
                    f"clear of any berth or anchorage. That is long enough and "
                    f"close enough to pass cargo."),
            detail=dict(hours=round(hours, 2), mean_sep_m=round(mean_sep, 1)),
            **common)

    if not both_underway:
        # 2. Converging and holding — they came together and stopped, but not
        #    alongside. Weaker than a transfer and worth saying separately.
        if both_stopped and mean_sep <= max(ALONGSIDE_M * 4, 2000.0):
            return Interaction(
                kind="converging_and_holding", confidence=min(0.8, span_conf),
                reason=(f"Both stopped within {mean_sep:.0f} m of each other "
                        f"for {hours:.1f} hours in open water, without closing "
                        f"alongside."),
                detail=dict(hours=round(hours, 2)), **common)
        return None

    # Both making way. Do their courses agree, and does the gap hold?
    cog_a = _course_at(track_a, (t0 + t1) / 2)
    cog_b = _course_at(track_b, (t0 + t1) / 2)
    if cog_a is None or cog_b is None:
        return None
    course_diff = _ang_diff(cog_a, cog_b)
    if course_diff > SAME_COURSE_DEG or cv > STABLE_SEPARATION_CV:
        # Different courses, or a separation that wanders: this is traffic
        # passing, an overtake, or a crossing. Not a relationship.
        return None
    if mean_sep > COMPANY_MAX_SEPARATION_M:
        # Same course, steady gap, and four miles apart: two ships on a route.
        # This is the gate that separates a formation from traffic — see
        # `COMPANY_MAX_SEPARATION_M`, and note that persistence does not: a
        # fishing fleet steaming to the same ground held station for 11.7 hours.
        return None

    # 3. Shadowing — one consistently astern of the other. Measured by the
    #    bearing from the leader to the follower against the leader's course.
    astern_a = astern_b = 0
    for r in run:
        brg_ab = _bearing_deg(r[4], r[5], r[6], r[7])
        if _ang_diff(brg_ab, (cog_a + 180.0) % 360.0) <= ASTERN_TOLERANCE_DEG:
            astern_b += 1        # b sits astern of a  => a leads
        if _ang_diff((brg_ab + 180.0) % 360.0,
                     (cog_b + 180.0) % 360.0) <= ASTERN_TOLERANCE_DEG:
            astern_a += 1        # a sits astern of b  => b leads
    n = len(run)
    if astern_b / n >= 0.8 or astern_a / n >= 0.8:
        follower = (track_b.track_id if astern_b >= astern_a
                    else track_a.track_id)
        leader = (track_a.track_id if follower == track_b.track_id
                  else track_b.track_id)
        return Interaction(
            kind="shadowing", confidence=min(0.85, span_conf),
            follower=follower,
            reason=(f"{follower} held station astern of {leader} at "
                    f"{mean_sep:.0f} m for {hours:.1f} hours, matching course "
                    f"to within {course_diff:.0f}° while the gap stayed "
                    f"steady. That is following, not sharing a lane."),
            detail=dict(hours=round(hours, 2), course_diff_deg=round(course_diff, 1),
                        separation_cv=round(cv, 3), leader=leader),
            **common)

    # 4. Moving in company — same course, steady gap, neither astern.
    return Interaction(
        kind="moving_in_company", confidence=min(0.75, span_conf * 0.9),
        reason=(f"Held {mean_sep:.0f} m apart on courses within "
                f"{course_diff:.0f}° of each other for {hours:.1f} hours, with "
                f"the gap steady to {cv:.0%}. Two vessels keeping formation "
                f"rather than two vessels in the same lane."),
        detail=dict(hours=round(hours, 2), course_diff_deg=round(course_diff, 1),
                    separation_cv=round(cv, 3)),
        **common)


def summarise(interactions: Sequence[Interaction]) -> dict:
    """Counts by kind, and how many cross sensors — the interesting number."""
    kinds: dict[str, int] = {}
    for i in interactions:
        kinds[i.kind] = kinds.get(i.kind, 0) + 1
    return {
        "total": len(interactions),
        "by_kind": kinds,
        "cross_sensor": sum(1 for i in interactions if i.cross_sensor),
        "note": ("A cross-sensor interaction is a named hull behaving in "
                 "relation to an unnamed contact — the event no single sensor "
                 "can see, and the reason this analysis is worth running."),
    }
