"""How improbable is what she is doing, given what traffic does here? — a
novelty score, offered as the successor to the departure residual ADR-042
measured at chance.

The question this asks, and why it is a different one
-----------------------------------------------------
:mod:`tracks.projection` asks *"is she where we predicted?"* and ADR-042
measured the answer: precision 0.09–0.33 against a base rate of 0.15, at or
below chance. The mechanism it named is worth restating because this module is
built to avoid it:

    **Nearly every vessel departs from her own dead-reckoned path, because
    nearly every vessel alters course at every waypoint.** A residual is large
    at a corner whether or not the corner is odd, so the residual does not
    *select*. Improving the predictor moved the median and left the tail alone,
    and a detector lives on the tail.

So this module asks a different question of the same flow field:

    **Given that traffic arriving in this cell on this heading normally leaves
    on that bearing at that speed, how surprising is what she actually did?**

A waypoint turn is a *large residual* and a *small surprise*, because the flow
field knows traffic turns there and how sharply. That is the whole difference,
and it is why the two signals can disagree.

What is scored, per fix
-----------------------
For each of her fixes where she is making way, the flow field is asked what
traffic arriving in this cell on this heading does next. Where it answers, two
surprises are computed:

* **Course surprise** — how far her actual bearing made good over the following
  few minutes sits from the lane's, measured **in units of the lane's own
  circular spread**. A tightly channelled leg has a spread of a few degrees and
  tolerates little; a junction has a spread of tens and tolerates much. Dividing
  by the spread is what stops a busy junction manufacturing surprise.
* **Speed surprise** — the log ratio of her speed made good to the lane's.
  Signed, because "far slower than this lane runs" and "far faster" are
  different tells and folding them together loses that.

Where the field cannot answer, the fix is **not checkable** and contributes to
neither surprise. That is the three-valued discipline the rest of this system
holds to (:mod:`anomaly.identity`, :mod:`baselines`, ``route_support``): a fix
we could not check is not a fix that was fine.

Off-road presence, and the coverage rule that keeps it honest
--------------------------------------------------------------
A fix the field cannot answer for is interesting in one specific case and
uninteresting in every other: when she is in water the fleet **does** use, but
in a cell of it that the fleet does not. That is "off the road in a watched
area". A fix in water nobody watches is not off the road — we simply cannot
hear that road — and calling it one is exactly the false positive by
construction CLAUDE.md forbids for offshore AIS gaps, one domain along.

So :meth:`TrafficField.road_status` is three-valued — ``on_road``, ``off_road``
and ``unwatched`` — and ``unwatched`` never counts against a hull.

What this module does NOT do
----------------------------
* **It never reads ``scenario_truth``.** It reads motion, and the flow field
  fitted from other hulls' motion. Nothing else.
* **It does not decide.** :class:`HullMotionProfile` reports numbers, the way
  :mod:`baselines` reports distributions. The operating point and the flag live
  in :func:`flag`, are stated as constants, and were fitted on a **dev** split
  that the reported result was not measured on.
* **It reads motion only**, so a radar track with no identity gets the same
  answer as an AIS track — the ADR-032/033 rule that keeps ``fusion/``
  source-agnostic.

Every number derived from this module on the scenario corpus is **synthetic**
and optimistic by construction: the corpus routes its vessels through one
deterministic generator, so a flow field fitted to it recovers the generator's
own waypoints. See :func:`caveat` and HANDOFF_PRECISION.md.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from .. import h3util as tiling
from ..config import PIPELINE_VERSION
from . import route_prior as rp
from .kalman import epoch_s

__all__ = [
    "TrafficField", "FixSurprise", "HullMotionProfile", "Window", "Rule",
    "Verdict", "fit_traffic_field", "fix_surprises", "profile_hull",
    "profile_tracks", "windows_of", "hull_windows", "flag",
    "MIN_SPREAD_DEG", "MIN_LANE_KN", "UNDERWAY_KN", "OFFSHORE_KM",
    "NEIGHBOURHOOD_RING", "MIN_NEIGHBOURHOOD_OBS", "ON_ROAD", "OFF_ROAD",
    "UNWATCHED", "WINDOW_HOURS", "MIN_WINDOW_CHECKABLE", "FLAGGED", "QUIET",
    "NOT_CHECKABLE", "caveat",
]

# --------------------------------------------------------------------------
# constants — argued, and fixed before any threshold was swept
# --------------------------------------------------------------------------

#: A floor under the lane's circular spread when it is used as a denominator.
#:
#: A lane fitted from tightly-clustered traffic can report a spread of a
#: fraction of a degree. Dividing by that turns AIS quantisation — course over
#: ground is broadcast to a tenth of a degree and the position it is derived
#: from is metres-noisy — into a surprise of hundreds of sigma. Five degrees is
#: about the honest floor on how well a bearing made good over six minutes can
#: be known at all, and it is a *floor*, not a replacement: a genuinely broad
#: junction keeps its own broader spread.
MIN_SPREAD_DEG = 5.0

#: A floor under the lane's speed made good when it is used as a denominator,
#: for the same reason: a lane whose fitted speed is 0.2 knots makes every
#: passing vessel a hundredfold surprise.
MIN_LANE_KN = 1.0

#: Below this she is not making way and her course over ground is noise about a
#: swinging circle. The same 2 knots :mod:`baselines`, the loitering rule and
#: :data:`route_prior.MIN_UNDERWAY_KN` already use.
UNDERWAY_KN = rp.MIN_UNDERWAY_KN

#: Far enough from land that stopping is not "she is alongside, or waiting off a
#: port". Ports, anchorages and their waiting areas are where a fleet stops for
#: entirely ordinary reasons, and a stop-detector that does not exclude them
#: measures port calls. 15 km is outside the anchorage radii the activity layer
#: already works with.
OFFSHORE_KM = 15.0

#: How far around a cell to look before calling a fix "off the road" rather
#: than "somewhere we do not watch". Ring 2 at res 6 is a neighbourhood roughly
#: 30 km across.
NEIGHBOURHOOD_RING = 2

#: How much fitted traffic that neighbourhood needs before it counts as watched.
#: Same argument as :data:`route_prior.MIN_KEY_OBSERVATIONS` one scale up: below
#: this the neighbourhood is one vessel's transit and "off the road" would mean
#: "off *her* road".
MIN_NEIGHBOURHOOD_OBS = 50

ON_ROAD = "on_road"
OFF_ROAD = "off_road"
UNWATCHED = "unwatched"

#: The unit a hull is scored in, in hours — **not** the whole track.
#:
#: This is the single most important constant in the module, and it is here
#: because of a measurement failure rather than a modelling preference. Scored
#: per hull over her whole track, this corpus separates anomalous hulls from the
#: rest with an AUC of 0.88 **on track length alone**: the scripted cast is
#: generated for the duration of its scenario (median 15 h) and the background
#: fleet runs the whole 51-day window (median 148 h). Every feature correlates
#: with observation length, so every feature looks excellent, and what is being
#: measured is which vessels the generator scripted.
#:
#: A fixed window removes it by construction — every unit of evidence is the
#: same length — and it reverses the residual bias in the safe direction: a hull
#: watched for 500 hours contributes forty windows and therefore forty chances
#: to look odd, where one watched for twelve contributes one. A signal that
#: still separates under that is separating on behaviour.
#:
#: Twelve hours is also the operational unit: an operator watches a picture, not
#: a completed voyage, and a finding that needs the whole voyage to exist is a
#: finding that arrives after she has sailed.
WINDOW_HOURS = 12.0

#: Fewest checkable fixes a window needs before the flow field may be said to
#: have an opinion about it. Below this the window is ``not_checkable`` — never
#: "quiet". A fraction computed over three fixes is 0, 1/3, 2/3 or 1, and a
#: threshold on that is a threshold on nothing.
MIN_WINDOW_CHECKABLE = 20

FLAGGED = "flagged"
QUIET = "quiet"
NOT_CHECKABLE = "not_checkable"


def caveat() -> str:
    return (
        "Fitted on the synthetic scenario corpus, whose vessels are routed by "
        "one deterministic generator (scenario/searoute.py). A flow field "
        "fitted to that traffic recovers the generator's own waypoints, so a "
        "novelty score built on it is optimistic by construction. Real coastal "
        "traffic is far more dispersed and these figures must be re-measured "
        "on the deploy host before any of them is stated externally "
        "(CLAUDE.md §4.6).")


# --------------------------------------------------------------------------
# the field
# --------------------------------------------------------------------------

@dataclass
class TrafficField:
    """A fitted :class:`route_prior.RoutePrior` plus where the fleet went at
    all, which is what makes "off the road" separable from "not watched".

    The prior alone cannot make that distinction: it holds only the cells that
    cleared its support floors, so an absent cell there could equally be open
    ocean nobody transits or open ocean nobody *hears*. The occupancy map holds
    every cell any fitted hull was seen in, at any support level, and the ring
    around a cell is what decides which of the two an absence is.
    """
    prior: rp.RoutePrior
    #: ``{cell: observations}`` over the fitted hulls, before any support floor.
    occupancy: dict[str, int] = field(default_factory=dict)
    #: ``{cell: distinct fitted hulls seen there}``.
    occupancy_hulls: dict[str, int] = field(default_factory=dict)
    ring: int = NEIGHBOURHOOD_RING
    min_neighbourhood_obs: int = MIN_NEIGHBOURHOOD_OBS
    n_fit_hulls: int = 0
    pipeline_version: str = PIPELINE_VERSION
    #: Memo for :meth:`_neighbourhood_obs`. The ring sum is the same answer
    #: every time it is asked for a cell and a hull crosses a cell many times;
    #: recomputing it per fix makes the cost of scoring depend on report rate.
    _ring_memo: dict = field(default_factory=dict, repr=False)

    @property
    def res(self) -> int:
        return self.prior.res

    def _neighbourhood_obs(self, cell: str) -> int:
        hit = self._ring_memo.get(cell)
        if hit is not None:
            return hit
        try:
            ring = tiling.disk(cell, self.ring)
        except Exception:                                # pragma: no cover
            ring = [cell]
        total = sum(self.occupancy.get(c, 0) for c in ring)
        self._ring_memo[cell] = total
        return total

    def road_status(self, lat: float, lon: float, cog_deg: float) -> str:
        """``on_road`` / ``off_road`` / ``unwatched`` for one fix.

        ``unwatched`` is returned whenever the *neighbourhood* carries too
        little fitted traffic to have an opinion, and it is deliberately the
        answer that costs a hull nothing. Asserting "she is off the customary
        route" in water where we have not established what the customary route
        is would be the same error as calling an out-of-coverage AIS gap dark.
        """
        if self.prior.lookup(lat, lon, cog_deg) is not None:
            return ON_ROAD
        cell = tiling.cell(lat, lon, self.res)
        if self._neighbourhood_obs(cell) < self.min_neighbourhood_obs:
            return UNWATCHED
        return OFF_ROAD

    def report(self) -> dict:
        return {
            "prior": self.prior.report(),
            "occupied_cells": len(self.occupancy),
            "supported_cells": self.prior.n_cells,
            "n_fit_hulls": self.n_fit_hulls,
            "ring": self.ring,
            "min_neighbourhood_obs": self.min_neighbourhood_obs,
            "caveat": caveat(),
        }


def fit_traffic_field(tracks: Iterable, *, hull_of=None,
                      res: int = rp.FLOW_RES, **prior_kw) -> TrafficField:
    """Fit the flow field and the occupancy map from the same tracks, once.

    ``tracks`` must be the **fit** split and nothing else. A field that has seen
    a hull it will later score has memorised her routing, and on a corpus whose
    vessels follow one deterministic generator it would memorise it very
    flatteringly.
    """
    tracks = list(tracks)
    if hull_of is None:
        def hull_of(t):                                   # noqa: E306
            return str(getattr(t, "track_key", None)
                       or getattr(t, "track_id", ""))

    prior = rp.fit_route_prior(tracks, hull_of=hull_of, res=res, **prior_kw)

    occ: dict[str, int] = defaultdict(int)
    who: dict[str, set] = defaultdict(set)
    hulls: set[str] = set()
    for tr in tracks:
        hull = str(hull_of(tr))
        hulls.add(hull)
        arr = rp._track_arrays(tr)
        if arr is None:
            continue
        _, lat, lon, sog, _ = arr
        for k in range(len(lat)):
            if not (np.isfinite(sog[k]) and sog[k] >= UNDERWAY_KN):
                continue
            c = tiling.cell(float(lat[k]), float(lon[k]), res)
            occ[c] += 1
            who[c].add(hull)
    return TrafficField(prior=prior, occupancy=dict(occ),
                        occupancy_hulls={c: len(v) for c, v in who.items()},
                        n_fit_hulls=len(hulls))


# --------------------------------------------------------------------------
# per-fix surprise
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FixSurprise:
    """What she did at one fix, against what traffic does there.

    ``course_sigma`` and ``speed_log_ratio`` are ``None`` when the field had no
    answer — never 0.0. Zero means "exactly what the lane does", which is the
    opposite claim.
    """
    t: float
    lat: float
    lon: float
    cog_deg: float
    sog_made_good_kn: float
    out_bearing_deg: float
    road: str
    course_sigma: Optional[float] = None
    speed_log_ratio: Optional[float] = None
    lane_course_deg: Optional[float] = None
    lane_spread_deg: Optional[float] = None
    lane_sog_kn: Optional[float] = None

    @property
    def checkable(self) -> bool:
        return self.course_sigma is not None


def fix_surprises(track, field: TrafficField, *,
                  lookahead_minutes: float = rp.LOOKAHEAD_MINUTES,
                  underway_kn: float = UNDERWAY_KN
                  ) -> list[FixSurprise]:
    """Score every fix of one track against the field.

    The *outgoing* bearing is measured the same way the field learned it — to
    where she actually was ``lookahead_minutes`` later — so the comparison is
    between two quantities of the same kind. Measuring her surprise from her
    broadcast course over ground while the field holds bearings made good would
    compare a heading with a displacement and report the difference as anomaly.
    """
    arr = rp._track_arrays(track)
    if arr is None:
        return []
    t, lat, lon, sog, cog = arr
    n = len(t)
    look_s = lookahead_minutes * 60.0
    out: list[FixSurprise] = []
    for i in range(n - 1):
        if not (np.isfinite(sog[i]) and sog[i] >= underway_kn
                and np.isfinite(cog[i])):
            continue
        j = int(np.searchsorted(t, t[i] + look_s, side="left"))
        if j >= n:
            break
        dt = t[j] - t[i]
        # A bearing measured across a silence is a bearing over that silence,
        # not over six minutes. The field skipped those when it learned; the
        # scorer skips them for the same reason.
        if dt <= 0 or dt > 3.0 * look_s:
            continue
        d = rp._hav_m(lat[i], lon[i], lat[j], lon[j])
        if d < 50.0:
            continue
        b = rp._bearing_deg(lat[i], lon[i], lat[j], lon[j])
        v = d / dt / 0.514444
        mode = field.prior.lookup(float(lat[i]), float(lon[i]), float(cog[i]))
        if mode is None:
            road = field.road_status(float(lat[i]), float(lon[i]),
                                     float(cog[i]))
            out.append(FixSurprise(
                t=float(t[i]), lat=float(lat[i]), lon=float(lon[i]),
                cog_deg=float(cog[i]), sog_made_good_kn=float(v),
                out_bearing_deg=float(b), road=road))
            continue
        dev = abs(rp._signed_delta(mode.course_deg, b))
        sigma = dev / max(mode.spread_deg, MIN_SPREAD_DEG)
        ratio = math.log(max(v, 0.05)
                         / max(mode.sog_made_good_kn, MIN_LANE_KN))
        out.append(FixSurprise(
            t=float(t[i]), lat=float(lat[i]), lon=float(lon[i]),
            cog_deg=float(cog[i]), sog_made_good_kn=float(v),
            out_bearing_deg=float(b), road=ON_ROAD,
            course_sigma=float(sigma), speed_log_ratio=float(ratio),
            lane_course_deg=float(mode.course_deg),
            lane_spread_deg=float(mode.spread_deg),
            lane_sog_kn=float(mode.sog_made_good_kn)))
    return out


# --------------------------------------------------------------------------
# the per-hull profile
# --------------------------------------------------------------------------

def _q(a: Sequence[float], p: float) -> Optional[float]:
    return float(np.percentile(np.asarray(a, dtype=float), p)) if len(a) else None


@dataclass
class HullMotionProfile:
    """Everything this module measured about one hull's motion, and nothing it
    decided.

    Reported rather than thresholded, for the reason :mod:`baselines` gives:
    a layer that both derives a distribution and chooses the operating point
    from it has no separation between the measurement and the claim, and the
    next person cannot tell which of the two moved.
    """
    hull: str
    n_tracks: int = 0
    n_fixes: int = 0
    span_hours: float = 0.0

    # -- coverage of the check itself -------------------------------------
    n_scored: int = 0            # fixes where she was making way and scoreable
    n_checkable: int = 0         # …of which the field had an answer for
    n_off_road: int = 0
    n_unwatched: int = 0

    # -- flow novelty ------------------------------------------------------
    course_sigma_p50: Optional[float] = None
    course_sigma_p90: Optional[float] = None
    frac_course_sigma_gt2: Optional[float] = None
    frac_course_sigma_gt4: Optional[float] = None
    speed_log_p10: Optional[float] = None
    speed_log_p90: Optional[float] = None
    frac_speed_slow: Optional[float] = None    # < half the lane's speed
    frac_speed_fast: Optional[float] = None    # > twice the lane's speed

    # -- kinematics, source-blind -----------------------------------------
    sog_median: Optional[float] = None
    straightness: Optional[float] = None
    offshore_km_median: Optional[float] = None
    stopped_offshore_hours: float = 0.0
    longest_offshore_stop_hours: float = 0.0
    longest_gap_hours: float = 0.0

    @property
    def check_coverage(self) -> Optional[float]:
        """What fraction of her scoreable fixes the field could speak for.

        The honesty gate on every number above it: a hull scored on 3% of her
        track has a course-surprise figure computed from a handful of fixes, and
        restricting a claim to hulls the model has evidence for is the difference
        between an honest operating point and a number quoted everywhere.
        """
        return (self.n_checkable / self.n_scored) if self.n_scored else None

    @property
    def off_road_fraction(self) -> Optional[float]:
        """Of the fixes in **watched** water, how many were off the road.

        ``unwatched`` fixes are excluded from the denominator, not counted as
        on-road: they are neither.
        """
        d = self.n_checkable + self.n_off_road
        return (self.n_off_road / d) if d else None

    def as_dict(self) -> dict:
        d = {"hull": self.hull, "n_tracks": self.n_tracks,
             "n_fixes": self.n_fixes,
             "span_hours": round(self.span_hours, 2),
             "n_scored": self.n_scored, "n_checkable": self.n_checkable,
             "n_off_road": self.n_off_road, "n_unwatched": self.n_unwatched,
             "check_coverage": (None if self.check_coverage is None
                                else round(self.check_coverage, 3)),
             "off_road_fraction": (None if self.off_road_fraction is None
                                   else round(self.off_road_fraction, 3))}
        for k in ("course_sigma_p50", "course_sigma_p90",
                  "frac_course_sigma_gt2", "frac_course_sigma_gt4",
                  "speed_log_p10", "speed_log_p90",
                  "frac_speed_slow", "frac_speed_fast",
                  "sog_median", "straightness", "offshore_km_median",
                  "stopped_offshore_hours", "longest_offshore_stop_hours",
                  "longest_gap_hours"):
            v = getattr(self, k)
            d[k] = None if v is None else round(float(v), 4)
        return d


def _offshore_km(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    from .. import coastline
    try:
        return np.asarray(coastline.distance_to_shore_km(lat, lon),
                          dtype=float)
    except Exception:                                    # pragma: no cover
        return np.full(len(lat), np.nan)


def profile_hull(tracks: Sequence, field: TrafficField, *, hull: str
                 ) -> HullMotionProfile:
    """Aggregate one hull's tracks into one profile.

    Aggregated at the **hull**, not the track, because that is the unit an
    operator acts on and the unit the split is made on. A hull with four tracks
    scored four times would be four chances to clear a threshold.
    """
    p = HullMotionProfile(hull=str(hull), n_tracks=len(tracks))
    sigmas: list[float] = []
    speeds: list[float] = []
    sogs: list[float] = []
    offshore: list[float] = []
    travelled = net = 0.0
    t_lo = t_hi = None

    for tr in tracks:
        arr = rp._track_arrays(tr)
        if arr is None:
            continue
        t, lat, lon, sog, _ = arr
        p.n_fixes += len(t)
        t_lo = float(t[0]) if t_lo is None else min(t_lo, float(t[0]))
        t_hi = float(t[-1]) if t_hi is None else max(t_hi, float(t[-1]))

        for k in range(len(t) - 1):
            travelled += rp._hav_m(lat[k], lon[k], lat[k + 1], lon[k + 1])
            gap_h = (t[k + 1] - t[k]) / 3600.0
            p.longest_gap_hours = max(p.longest_gap_hours, gap_h)
        net += rp._hav_m(lat[0], lon[0], lat[-1], lon[-1])
        sogs.extend(float(s) for s in sog if np.isfinite(s))

        dist = _offshore_km(lat, lon)
        offshore.extend(float(x) for x in dist if np.isfinite(x))
        # Stopped, and far enough out that stopping is not a port call. Summed
        # over the interval each fix represents rather than counted, so a hull
        # reporting every two minutes does not out-score one reporting every
        # ten for the same behaviour.
        run_h = 0.0
        for k in range(len(t) - 1):
            dt_h = (t[k + 1] - t[k]) / 3600.0
            if dt_h <= 0 or dt_h > 2.0:
                run_h = 0.0
                continue
            if (np.isfinite(sog[k]) and sog[k] < 1.0
                    and np.isfinite(dist[k]) and dist[k] >= OFFSHORE_KM):
                p.stopped_offshore_hours += dt_h
                run_h += dt_h
                p.longest_offshore_stop_hours = max(
                    p.longest_offshore_stop_hours, run_h)
            else:
                run_h = 0.0

        for fx in fix_surprises(tr, field):
            p.n_scored += 1
            if fx.checkable:
                p.n_checkable += 1
                sigmas.append(fx.course_sigma)
                speeds.append(fx.speed_log_ratio)
            elif fx.road == OFF_ROAD:
                p.n_off_road += 1
            else:
                p.n_unwatched += 1

    if t_lo is not None:
        p.span_hours = (t_hi - t_lo) / 3600.0
    if sigmas:
        p.course_sigma_p50 = _q(sigmas, 50)
        p.course_sigma_p90 = _q(sigmas, 90)
        p.frac_course_sigma_gt2 = float(np.mean(np.asarray(sigmas) > 2.0))
        p.frac_course_sigma_gt4 = float(np.mean(np.asarray(sigmas) > 4.0))
    if speeds:
        a = np.asarray(speeds, dtype=float)
        p.speed_log_p10 = _q(speeds, 10)
        p.speed_log_p90 = _q(speeds, 90)
        p.frac_speed_slow = float(np.mean(a < math.log(0.5)))
        p.frac_speed_fast = float(np.mean(a > math.log(2.0)))
    if sogs:
        p.sog_median = float(np.median(sogs))
    if offshore:
        p.offshore_km_median = float(np.median(offshore))
    if travelled > 1.0:
        p.straightness = float(net / travelled)
    return p


def profile_tracks(tracks: Sequence, field: TrafficField, *, hull_of=None
                   ) -> dict[str, HullMotionProfile]:
    """Group tracks by hull and profile each hull."""
    if hull_of is None:
        def hull_of(t):                                   # noqa: E306
            m = getattr(t, "mmsi", None)
            if m is not None:
                return str(m)
            return str(getattr(t, "track_key", None)
                       or getattr(t, "track_id", ""))
    by_hull: dict[str, list] = defaultdict(list)
    for tr in tracks:
        by_hull[str(hull_of(tr))].append(tr)
    return {h: profile_hull(v, field, hull=h) for h, v in by_hull.items()}
