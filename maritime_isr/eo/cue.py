"""The cueing scheduler — which track a camera is pointed at, and when.

*"Cueing logic, which is the real product here. Given a picture containing far
more tracks than there are cameras, which track does the system point a camera
at, and when. This is a scheduling and prioritisation problem driven by
suspicion, geometry and opportunity ... Making that decision automatically, and
explaining it, is precisely the workload the requirement wants removed."*

Everything else in Area 5 is downstream of this file. It needs no camera and no
imagery to build, and it is the part of the area that is genuinely this
project's: image classification is a commodity, and a hundred cameras that a
watchkeeper must slew by hand is the pain the requirement actually names.

Why this is an assignment problem and not a ranked list
-------------------------------------------------------
The obvious implementation is: sort the tracks by suspicion, walk the list, give
each track its best camera. That is **greedy per-target matching**, and it is
the same mistake CLAUDE.md §6 bans in the association core one domain along —
it double-books. The most suspicious three contacts are frequently in one
station's arc, so a greedy pass hands that station's camera to all three,
resolves the collision arbitrarily, and leaves fifteen cameras idle while two of
the three go unimaged. So each slot is solved as a **global assignment** over
cameras x candidates (Jonker-Volgenant, via `scipy.optimize.linear_sum_assignment`)
— the same tool `fusion/associate.py` uses, for the same reason.

What goes into the decision
---------------------------
The brief lists the inputs by name; each becomes a term with a stated weight.

``suspicion``           the highest-scoring alert this system holds about her.
                        Dominant, because the requirement's whole framing is
                        that cueing should follow the ranked list.
``information_gain``    **what an image would actually tell us.** A contact
                        nobody can name gains everything from a photograph; a
                        hull already imaged this week, whose declared identity
                        the image confirmed, gains almost nothing. Without this
                        term a scheduler spends its cameras re-photographing the
                        most suspicious ship in the picture every half hour and
                        never looks at the other fifty.
``staleness``           time since she was last imaged. A tie-break, not a
                        reason, and weighted accordingly.
``image quality``       range, aspect, light and visibility, from
                        :mod:`.camera`. Multiplies rather than adds: a target
                        worth imaging that cannot be imaged well is not an
                        opportunity, it is a target to image later.
``closing window``      whether she is about to become unobservable. Enters as
                        an *urgency multiplier on the cost*, not as priority:
                        being about to leave does not make a ship more
                        suspicious, it makes deferring her more expensive. That
                        distinction is what makes this a schedule rather than a
                        sorted list.

And the honesty half
--------------------
Every tasking carries the decomposition that produced it and a sentence a
watchkeeper can read. Every candidate that was *not* tasked and was worth
tasking carries the reason it lost — out of reach, too dark, outranked and by
whom. That is the suppression discipline ADR-028 established for the radar
cascade and ADR-031 extended to the ranked list: a queue that silently drops
things cannot be calibrated against, and an automation an operator cannot
interrogate is one they will switch off.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from .camera import EOCamera, CameraView, best_view, view

__all__ = ["CueCandidate", "Tasking", "Deferral", "CuePlan", "plan_cueing",
           "W_SUSPICION", "W_INFORMATION", "W_STALENESS", "PRIORITY_FLOOR",
           "URGENCY_WEIGHT", "FRESH_HOURS", "STALE_HOURS"]


#: How the three priority terms trade off. They sum to 1 so that `priority` is
#: readable as a fraction and a change to one is visibly a change to the others.
#:
#: Suspicion dominates because that is the requirement's own framing.
#: information gain is a close second and deliberately large: it is the term
#: that stops the network spending itself on one ship. Staleness is small
#: because "we have not looked at her lately" is not by itself a reason to look.
W_SUSPICION = 0.55
W_INFORMATION = 0.30
W_STALENESS = 0.15

#: A candidate below this priority does not get a camera, even if one is free.
#:
#: Set as arithmetic rather than tuned, the same way `assistant.build.MIN_SCORE`
#: is: it is the priority of an ordinary hull with no suspicion at all whose
#: declared identity has never been checked against an image and who has never
#: been imaged — ``0.30 x 0.55 + 0.15 x 1.0 = 0.315``. So the rule reads "a hull
#: nobody suspects is worth exactly one look, and after that she has to earn the
#: next one". Below it are hulls already imaged and confirmed, which is the
#: population a camera should not be spent on.
PRIORITY_FLOOR = 0.30

#: How much a closing observation window is allowed to move the assignment.
#: At 0.6 a last-chance look outranks an equally valuable one that can be taken
#: in any of the next four slots, and does not outrank one worth two-thirds
#: more. Urgency should reorder a queue, never rewrite it.
URGENCY_WEIGHT = 0.6

#: Staleness ramp. Below `FRESH_HOURS` a second image tells us nothing new;
#: above `STALE_HOURS` the last look is old enough that she may be a different
#: ship doing a different thing.
FRESH_HOURS = 6.0
STALE_HOURS = 96.0

#: How far ahead the scheduler looks when working out whether deferring a target
#: costs anything. Four slots: far enough to distinguish "she is leaving cover"
#: from "she will be there all afternoon", short enough that the dead-reckoned
#: position it relies on is still worth something (ADR-032 measured how quickly
#: a projection stops discriminating; this uses it for opportunity, which is
#: what that ADR said the projection is genuinely good for).
LOOKAHEAD_SLOTS = 4

#: Information gain by what an image would resolve. Stated as a table because
#: it is a judgement and an operator should be able to argue with it.
INFO_UNIDENTIFIED = 1.00     # nothing broadcasting: an image is the only lead
INFO_UNVERIFIED = 0.55       # she declares an identity nothing has checked
INFO_CONFIRMED = 0.10        # an image already agreed with her declaration
INFO_CONTRADICTED = 0.85     # an image already disagreed: look again, harder

#: A very large cost, standing in for "this pairing is impossible". Finite
#: rather than infinite because the solver requires a finite matrix; pairings at
#: this cost are struck out after the solve rather than trusted to lose.
_IMPOSSIBLE = 1e9


@dataclass
class CueCandidate:
    """One track the network could point a camera at, and what is known of her.

    Deliberately a plain value object with no store behind it: the scheduler is
    handed a picture and returns a plan, so it can be exercised on four fixtures
    in a unit test and on two thousand tracks in the pipeline without changing.
    """
    subject_id: str
    track_id: str
    lat: float
    lon: float
    sog_kn: float = 0.0
    cog_deg: float = 0.0
    length_m: Optional[float] = None
    #: Highest confidence this system holds about her, in [0,1]. 0 for a track
    #: nothing has ever flagged, which is most of the picture.
    suspicion: float = 0.0
    #: What the strongest signal about her was, for the explanation.
    suspicion_reason: str = ""
    #: False for a contact with no broadcast identity at all.
    identity_known: bool = False
    #: What she says she is, where she says anything.
    declared_type: Optional[str] = None
    track_source: str = "radar"
    is_synthetic: bool = False

    def information_gain(self, *, imaged_before: int, verdict_state: str
                         ) -> float:
        """What a photograph of her would resolve that is not already resolved."""
        if not self.identity_known:
            # An unnamed contact stays worth imaging even after one look: the
            # first image gives a type, and a second, later look gives a
            # movement history for a hull the system still cannot name.
            return INFO_UNIDENTIFIED if not imaged_before else 0.65
        if verdict_state == "contradicted":
            return INFO_CONTRADICTED
        if verdict_state == "confirmed":
            return INFO_CONFIRMED
        return INFO_UNVERIFIED


@dataclass
class Tasking:
    """One camera pointed at one target for one slot, with its reasoning."""
    tasking_id: str
    camera_id: str
    station_id: str
    station: str
    subject_id: str
    track_id: str
    at: datetime
    slot_index: int
    lat: float
    lon: float
    range_km: float
    bearing_deg: float
    aspect_deg: Optional[float]
    band: str
    expected_quality: float
    length_m: Optional[float]
    priority: float
    value: float
    urgency: float
    #: The decomposition, term by term. The tasking order is only automation an
    #: operator will accept if the arithmetic is on the page.
    why: dict = field(default_factory=dict)
    sentence: str = ""
    is_synthetic: bool = False

    def as_dict(self) -> dict:
        return {"tasking_id": self.tasking_id, "camera_id": self.camera_id,
                "station_id": self.station_id, "station": self.station,
                "subject_id": self.subject_id, "track_id": self.track_id,
                "at": self.at.isoformat(), "slot_index": self.slot_index,
                "lat": self.lat, "lon": self.lon,
                "range_km": round(self.range_km, 2),
                "bearing_deg": round(self.bearing_deg, 1),
                "aspect_deg": (None if self.aspect_deg is None
                               else round(self.aspect_deg, 1)),
                "band": self.band,
                "expected_quality": round(self.expected_quality, 3),
                "length_m": self.length_m,
                "priority": round(self.priority, 3),
                "value": round(self.value, 4),
                "urgency": round(self.urgency, 3),
                "why": self.why, "sentence": self.sentence,
                "is_synthetic": self.is_synthetic}


@dataclass
class Deferral:
    """A target the scheduler considered and did not task, and why not."""
    subject_id: str
    slot_index: int
    at: datetime
    reason: str
    explanation: str
    priority: float
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"subject_id": self.subject_id, "slot_index": self.slot_index,
                "at": self.at.isoformat(), "reason": self.reason,
                "explanation": self.explanation,
                "priority": round(self.priority, 3), "detail": self.detail}


@dataclass
class CuePlan:
    """The tasking order, the deferral ledger, and what the plan cost."""
    taskings: list[Tasking] = field(default_factory=list)
    deferrals: list[Deferral] = field(default_factory=list)
    slots: int = 0
    slot_seconds: float = 0.0
    t0: Optional[datetime] = None
    n_cameras: int = 0
    #: Counts the ledger deliberately does not enumerate — see `plan_cueing`.
    counters: dict = field(default_factory=dict)

    @property
    def camera_slots(self) -> int:
        return self.slots * self.n_cameras

    def utilisation(self) -> float:
        return (len(self.taskings) / self.camera_slots) if self.camera_slots else 0.0

    def subjects(self) -> set[str]:
        return {t.subject_id for t in self.taskings}

    def window(self, start: datetime, end: datetime) -> list[Tasking]:
        return [t for t in self.taskings if start <= t.at < end]

    def busiest_hour(self) -> tuple[Optional[datetime], list[Tasking]]:
        """The hour with the most taskings — the picture worth printing."""
        if not self.taskings:
            return None, []
        by_hour: dict[datetime, list[Tasking]] = {}
        for t in self.taskings:
            h = t.at.replace(minute=0, second=0, microsecond=0)
            by_hour.setdefault(h, []).append(t)
        h = max(by_hour, key=lambda k: (len(by_hour[k]), k))
        return h, sorted(by_hour[h], key=lambda t: (t.slot_index, -t.value))

    def as_dict(self, *, limit: int = 200) -> dict:
        return {"slots": self.slots, "slot_seconds": self.slot_seconds,
                "t0": self.t0.isoformat() if self.t0 else None,
                "n_cameras": self.n_cameras,
                "n_taskings": len(self.taskings),
                "n_deferrals": len(self.deferrals),
                "camera_slots": self.camera_slots,
                "utilisation": round(self.utilisation(), 4),
                "counters": dict(self.counters),
                "taskings": [t.as_dict() for t in self.taskings[:limit]],
                "deferrals": [d.as_dict() for d in self.deferrals[:limit]]}

    def format(self, *, limit: int = 20) -> str:
        lines = ["EO tasking order — the busiest hour in the plan"]
        hour, taskings = self.busiest_hour()
        if hour is None:
            lines.append("  (nothing was worth imaging in this window)")
            return "\n".join(lines)
        lines.append(f"  hour {hour:%Y-%m-%d %H:%M} UTC   "
                     f"{len(taskings)} tasking(s) across {self.n_cameras} camera(s)")
        lines.append(f"  {'#':<3}{'camera':<10}{'target':<34}"
                     f"{'rng':>7}{'brg':>6}{'qual':>7}{'prio':>7}  why")
        for i, t in enumerate(taskings[:limit], 1):
            lines.append(f"  {i:<3}{t.station_id:<10}{t.subject_id[:33]:<34}"
                         f"{t.range_km:>6.1f}k{t.bearing_deg:>6.0f}"
                         f"{t.expected_quality:>7.2f}{t.priority:>7.2f}  "
                         f"{t.sentence}")
        held = [d for d in self.deferrals
                if hour <= d.at < hour + timedelta(hours=1)]
        if held:
            lines.append(f"  deferred this hour: {len(held)}")
            for d in sorted(held, key=lambda d: -d.priority)[:6]:
                lines.append(f"    {d.subject_id[:40]:<42}{d.priority:>5.2f}  "
                             f"{d.reason}: {d.explanation}")
        return "\n".join(lines)


def _advance(lat: float, lon: float, bearing_deg: float, dist_m: float
             ) -> tuple[float, float]:
    """Dead-reckon a position along a bearing. Great-circle, small distances."""
    r = 6371000.0
    d = dist_m / r
    br = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def _project(c: CueCandidate, seconds: float) -> tuple[float, float]:
    """Where she will be in `seconds`, at her present course and speed.

    Dead reckoning, which ADR-032 measured and found *useless* as a suspicion
    signal — every vessel departs from its own projection, because every voyage
    is mostly turns. That ADR also said what the projection is genuinely good
    for, and this is it: an opportunity horizon. "Will she still be inside
    Mumbai's camera arc in twenty minutes" tolerates the error that "did she
    deviate from her predicted track" does not.
    """
    if seconds <= 0 or not c.sog_kn:
        return c.lat, c.lon
    metres = float(c.sog_kn) * 1852.0 / 3600.0 * seconds
    return _advance(c.lat, c.lon, float(c.cog_deg or 0.0), metres)


def _staleness(now: float, last_imaged_at: Optional[float]) -> float:
    if last_imaged_at is None:
        return 1.0
    hours = max(0.0, (now - last_imaged_at) / 3600.0)
    if hours <= FRESH_HOURS:
        return 0.0
    if hours >= STALE_HOURS:
        return 1.0
    return (hours - FRESH_HOURS) / (STALE_HOURS - FRESH_HOURS)


def _sentence(c: CueCandidate, v: CameraView, terms: dict, urgency: float
              ) -> str:
    """The one line a watchkeeper reads instead of slewing the camera herself."""
    aspect = ""
    if v.aspect_deg is not None:
        aspect = (", near broadside" if 55.0 <= v.aspect_deg <= 125.0
                  else ", fine on the bow" if v.aspect_deg < 25.0
                  or v.aspect_deg > 155.0 else "")
    bits = []
    if terms["suspicion"] > 0:
        bits.append(f"she is carrying {c.suspicion_reason or 'a flagged signal'}"
                    f" at {c.suspicion:.2f}")
    if not c.identity_known:
        bits.append("nothing is broadcasting there, so an image is the only lead")
    elif terms["information_gain"] >= INFO_UNVERIFIED:
        bits.append("no image has ever checked what she declares she is")
    if terms["staleness"] >= 0.9 and c.identity_known:
        bits.append("she has not been imaged")
    if urgency >= 0.9:
        bits.append("this is the last slot in which any camera can see her")
    elif urgency >= 0.5:
        bits.append("her window is closing")
    why = "; ".join(bits) or "nothing better is in reach"
    return (f"{v.camera.name} at {v.range_km:.1f} km, "
            f"{v.bearing_deg:.0f}°{aspect}, {v.illumination.label}, "
            f"expected image {v.quality:.2f} — {why}.")


def plan_cueing(feed: Callable[[datetime], Sequence[CueCandidate]],
                cameras: Sequence[EOCamera], *,
                t0: datetime, slots: int, slot_seconds: float = 1800.0,
                imaged_at: Optional[dict[str, float]] = None,
                verdict_state: Optional[dict[str, str]] = None,
                max_deferrals_per_slot: int = 12,
                priority_floor: float = PRIORITY_FLOOR) -> CuePlan:
    """Produce a tasking order over a window, and the reasons for what it left.

    ``feed(t)`` returns the picture as at ``t``: every track the network is
    holding, with whatever the system knows about each. It is a callable rather
    than a table so the scheduler can be unit-tested on four fixtures and driven
    in the pipeline from two thousand resampled tracks without knowing the
    difference.

    ``imaged_at`` and ``verdict_state`` seed the loop's memory — when she was
    last photographed and whether that photograph agreed with what she declares.
    Both are carried forward across slots inside the plan, which is what makes
    this a control loop rather than a repeated ranking: a camera spent in slot 3
    changes what slot 4 is worth spending on.
    """
    from scipy.optimize import linear_sum_assignment

    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    cameras = list(cameras)
    plan = CuePlan(slots=slots, slot_seconds=slot_seconds, t0=t0,
                   n_cameras=len(cameras))
    imaged: dict[str, float] = dict(imaged_at or {})
    n_imaged: dict[str, int] = {}
    states: dict[str, str] = dict(verdict_state or {})
    counters = {"candidates_seen": 0, "below_priority_floor": 0,
                "no_camera_in_reach": 0, "outranked": 0,
                "slew_too_far": 0, "idle_camera_slots": 0}
    # Where each camera was left pointing, so the slew to the next target can be
    # charged against the slot. At half-hour slots this never binds; at
    # two-minute slots it is the difference between a plan and a wish.
    pointing: dict[str, float] = {}

    for k in range(slots):
        t = t0 + timedelta(seconds=slot_seconds * (k + 0.5))
        cands = list(feed(t))
        counters["candidates_seen"] += len(cands)
        if not cands or not cameras:
            counters["idle_camera_slots"] += len(cameras)
            continue

        now = t.timestamp()
        scored: list[tuple[CueCandidate, dict, float]] = []
        for c in cands:
            terms = {
                "suspicion": max(0.0, min(1.0, float(c.suspicion or 0.0))),
                "information_gain": c.information_gain(
                    imaged_before=n_imaged.get(c.subject_id, 0),
                    verdict_state=states.get(c.subject_id, "unknown")),
                "staleness": _staleness(now, imaged.get(c.subject_id)),
            }
            priority = (W_SUSPICION * terms["suspicion"]
                        + W_INFORMATION * terms["information_gain"]
                        + W_STALENESS * terms["staleness"])
            terms["priority"] = priority
            scored.append((c, terms, priority))

        eligible = [(c, terms, p) for c, terms, p in scored
                    if p >= priority_floor]
        counters["below_priority_floor"] += len(scored) - len(eligible)
        if not eligible:
            counters["idle_camera_slots"] += len(cameras)
            continue

        # ---- views, one per (camera, candidate) --------------------------
        views: dict[tuple[int, int], CameraView] = {}
        reachable: list[bool] = []
        for j, (c, _terms, _p) in enumerate(eligible):
            any_ok = False
            for i, cam in enumerate(cameras):
                span = (cam.day_range_km / 100.0) + 0.05
                if abs(c.lat - cam.lat) > span or abs(c.lon - cam.lon) > span:
                    continue
                v = view(cam, lat=c.lat, lon=c.lon, when=t,
                         length_m=c.length_m, heading_deg=c.cog_deg)
                if not v.observable:
                    continue
                # Can the head get there and settle inside the slot? A camera
                # holding a bearing 170° away needs 14 seconds of slew at 12°/s
                # plus its dwell; at short slots that genuinely excludes pairs.
                was = pointing.get(cam.camera_id)
                if was is not None:
                    swing = abs((v.bearing_deg - was + 180.0) % 360.0 - 180.0)
                    need = swing / max(cam.slew_rate_deg_s, 0.1) + cam.min_dwell_s
                    if need > slot_seconds:
                        counters["slew_too_far"] += 1
                        continue
                views[(i, j)] = v
                any_ok = True
            reachable.append(any_ok)

        # ---- how much deferring each candidate costs ----------------------
        urgency: list[float] = []
        for j, (c, _terms, _p) in enumerate(eligible):
            future = 0
            for ahead in range(1, LOOKAHEAD_SLOTS + 1):
                if k + ahead >= slots:
                    break
                t_ahead = t + timedelta(seconds=slot_seconds * ahead)
                la, lo = _project(c, slot_seconds * ahead)
                if best_view(cameras, lat=la, lon=lo, when=t_ahead,
                             length_m=c.length_m,
                             heading_deg=c.cog_deg) is not None:
                    future += 1
            urgency.append(1.0 / (1.0 + future))

        # ---- the assignment ----------------------------------------------
        n_rows, n_cols = len(cameras), len(eligible)
        cost = [[_IMPOSSIBLE] * n_cols for _ in range(n_rows)]
        for (i, j), v in views.items():
            value = eligible[j][2] * v.quality
            cost[i][j] = -(value * (1.0 + URGENCY_WEIGHT * urgency[j]))
        rows, cols = linear_sum_assignment(cost)

        taken: set[int] = set()
        winners: dict[int, tuple[int, CameraView]] = {}
        for i, j in zip(rows, cols):
            if cost[i][j] >= _IMPOSSIBLE / 2:
                continue
            v = views[(i, j)]
            c, terms, priority = eligible[j]
            value = priority * v.quality
            sentence = _sentence(c, v, terms, urgency[j])
            plan.taskings.append(Tasking(
                tasking_id=f"eot-{k:05d}-{cameras[i].station_id}",
                camera_id=cameras[i].camera_id,
                station_id=cameras[i].station_id, station=cameras[i].name,
                subject_id=c.subject_id, track_id=c.track_id,
                at=t, slot_index=k, lat=c.lat, lon=c.lon,
                range_km=v.range_km, bearing_deg=v.bearing_deg,
                aspect_deg=v.aspect_deg, band=v.band,
                expected_quality=v.quality, length_m=c.length_m,
                priority=priority, value=value, urgency=urgency[j],
                why={**{k2: round(v2, 4) for k2, v2 in terms.items()},
                     "image_quality": round(v.quality, 4),
                     "value": round(value, 4),
                     "closing_window": round(urgency[j], 3),
                     "weights": {"suspicion": W_SUSPICION,
                                 "information_gain": W_INFORMATION,
                                 "staleness": W_STALENESS},
                     "view": v.as_dict()},
                sentence=sentence,
                is_synthetic=bool(c.is_synthetic or cameras[i].is_synthetic)))
            taken.add(j)
            winners[j] = (i, v)
            pointing[cameras[i].camera_id] = v.bearing_deg
            imaged[c.subject_id] = now
            n_imaged[c.subject_id] = n_imaged.get(c.subject_id, 0) + 1
        counters["idle_camera_slots"] += len(cameras) - len(taken)

        # ---- the ledger of what was left, and why -------------------------
        #
        # Bounded per slot on purpose. Over a corpus-length plan an unbounded
        # ledger is larger than the corpus and nobody reads it; the counters
        # above carry the totals and the ledger carries the cases an operator
        # would actually ask about — the highest-priority targets that did not
        # get a camera.
        held = [(j, eligible[j]) for j in range(n_cols) if j not in taken]
        held.sort(key=lambda pair: -pair[1][2])
        for rank, (j, (c, terms, priority)) in enumerate(held):
            if not reachable[j]:
                counters["no_camera_in_reach"] += 1
            else:
                counters["outranked"] += 1
            if rank >= max_deferrals_per_slot:
                continue
            if reachable[j]:
                best_cam = max((ij for ij in views if ij[1] == j),
                               key=lambda ij: views[ij].quality)
                rival = next((eligible[jj][0].subject_id
                              for jj, (ii, _v) in winners.items()
                              if ii == best_cam[0]), None)
                plan.deferrals.append(Deferral(
                    subject_id=c.subject_id, slot_index=k, at=t,
                    reason="outranked",
                    explanation=(
                        f"{cameras[best_cam[0]].name} could see her at "
                        f"{views[best_cam].range_km:.1f} km but was tasked onto "
                        f"{rival or 'a higher-value target'} this slot"),
                    priority=priority,
                    detail={"camera_id": cameras[best_cam[0]].camera_id,
                            "quality": round(views[best_cam].quality, 3),
                            "taken_by": rival}))
            else:
                nearest, why = _nearest_reason(cameras, c, t)
                plan.deferrals.append(Deferral(
                    subject_id=c.subject_id, slot_index=k, at=t,
                    reason="no_camera_in_reach",
                    explanation=why, priority=priority,
                    detail={"nearest_station": nearest}))
    plan.counters = counters
    return plan


def _nearest_reason(cameras: Sequence[EOCamera], c: CueCandidate,
                    t: datetime) -> tuple[Optional[str], str]:
    """Which camera came closest to being able to see her, and what stopped it.

    "Nothing ashore can see her" is an answer; "Mumbai is 41 km away and her
    useful range is 20" is an answer an operator can act on, because it is an
    argument for a surface unit or a satellite tasking rather than for waiting.
    """
    best = None
    for cam in cameras:
        v = view(cam, lat=c.lat, lon=c.lon, when=t, length_m=c.length_m,
                 heading_deg=c.cog_deg)
        if best is None or v.range_km < best.range_km:
            best = v
    if best is None:
        return None, "there are no cameras in this network"
    return best.camera.name, (f"nearest camera is {best.camera.name} — "
                              f"{best.reason or 'no view'}")
