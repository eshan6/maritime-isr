"""Where does traffic actually *go* from here? — route priors, and prediction
that follows the road instead of the bonnet ornament.

ADR-032 measured forward projection and refused to promote it. The refusal was
not a tuning complaint; it named a physical cause and the fix it declined to
build:

    *"Dead reckoning is a good predictor along a leg and a useless one across a
    turn, and a coastal voyage is mostly turns. … What would make it
    discriminate is stated so it is not rediscovered: prediction has to be
    route-aware, and the zone layer's customary lanes (ADR-030) are what a
    corridor model would be fitted to."*

This module is that corridor model, plus the two other conditioners the
measurement pointed at — what kind of ship she is, and whether she has run this
leg before.

The shape of the model, and the wrong shape it started as
---------------------------------------------------------
The obvious flow field stores, per cell, *the courses traffic steers there*.
It was built that way first, and measured, and it was **worse than dead
reckoning**. The reason is worth keeping, because it is the whole design:

    A cell containing a waypoint holds both the inbound course and the outbound
    course. Asked "which of these is hers", an unconditioned field answers with
    the one nearest her current heading — the inbound one — and so it steers
    her *straight through the corner*, which is exactly what dead reckoning
    already did. The one place a route model must earn its keep is the one
    place that field could not.

Measured on held-out hulls of the synthetic corpus, median position error in
nautical miles (p90 in brackets):

===========  ===============  ===============  ===================
lead (h)     dead reckoning   unconditioned    heading-conditioned
===========  ===============  ===============  ===================
1.0          0.82  (5.86)     1.45  (6.86)     0.85  (5.84)
3.0          4.70 (23.17)     5.20 (24.07)     2.91 (21.26)
6.0          16.56 (59.51)    12.30 (57.64)    9.92 (48.06)
===========  ===============  ===============  ===================

The unconditioned field is behind the bonnet ornament at one hour and at three,
which is the failure stated above with a number on it. So the field is
**conditioned on where she is going, and stores where traffic went next**. The
key is (cell, incoming course octant); the value is the bearing traffic
actually made good over the following few minutes, and the speed it made good
at. At a waypoint the inbound octant maps to an outbound bearing that has
already begun to bend, and stepping composes the bend into a turn. That is a
transition model rather than a histogram, and it is the difference between a
route prior and a decoration.

**None of that made it a detector** — see :mod:`tracks.projection` and
HANDOFF_PREDICTION.md. It made it a better assertion, which is a different and
smaller claim.

The four things a prediction is conditioned on
----------------------------------------------
**1. The route prior — where traffic goes next from this cell on this
heading.** Learned from landed positions, held as (cell, course octant) →
outgoing bearing and speed made good.

**2. Vessel type, inferred from motion.** A trawler's next hour looks nothing
like a VLCC's, and it is not a small difference. The fitted advance factor —
how far along the modelled path she is predicted to have got — comes out at
**1.0 for a merchant at every lead measured** and at **0.9 at one hour falling
to 0.1 at three** for a vessel working a ground, because a trawler goes back
over her own water and the distance she travels is not the distance she makes
good. Predicting her 45 nm along her heading because she is doing 15 knots is
not a cone problem; it is a wrong prediction with a cone drawn round it. The
type comes from
:mod:`tracks.vessel_type`, which infers it **from motion only** and therefore
answers for a radar contact as well as an AIS track; this module never
re-derives it.

**3. The vessel's own history — has she run this leg before?** A hull that has
transited this cell on a previous voyage tells us more about her next hour than
the fleet does, so her own passages take precedence. **Strictly causal:** only
passages that *ended* before the moment the projection was made are eligible
(:meth:`OwnRouteHistory.lookup`). Her passage through a cell she has not yet
reached lies in the future of the prediction, and using it would be predicting
the answer from the answer — the mistake this codebase already guards against
in ``_on_steady_leg`` and in the hull-grouped split of
:mod:`tracks.vessel_type`.

*Measured, and it is the weakest of the four.* On held-out hulls of this corpus
her own history was available for about one projection in sixteen, and removing
it entirely moved median error by under 0.15 nm at every lead. It is kept
because it is right and because a corpus of eight weeks is the wrong corpus to
judge it on — a hull's second call at a port she has visited once is the case
it exists for, and eight weeks holds few of those — but it is **not** carrying
the result and this module does not claim it is.

**4. Per-area baselines.** :mod:`maritime_isr.baselines` already learns what
normal looks like per res-5 cell, and its ``is_unusual`` is deliberately
three-valued: True, False and **None for "we have not watched here enough to
have an opinion"**. That third value is honoured rather than collapsed — see
``projection._cone_modulation``.

The speed profile, and what it is worth
---------------------------------------
Dead reckoning holds her *current* speed for the whole lead, and that is not a
heading problem: a vessel two hours off Kandla is going to slow down, and the
flow field knows it because every hull that went before her slowed there too.
So the walk carries her speed forward **scaled by the lane's own speed
profile** — anchored on the lane speed where she is now, so a hull running fast
for the lane stays fast for the lane. She is predicted to do what traffic does
here, at her own pace relative to it.

Ablated on held-out hulls, median error without the profile against with it:
0.94 / 0.85 nm at one hour, 3.59 / 2.91 at three, 10.06 / 9.81 at six. Real,
worth keeping, and second to the heading conditioning rather than equal to it.

Three-valued, like every other rule in this system
--------------------------------------------------
A route-aware projection reports ``route_support`` as one of

  ``own_history``   — steered by this hull's own previous passages,
  ``fleet_prior``   — steered by the fleet flow field,
  ``not_checkable`` — no cell on the path had enough support, so this is dead
                      reckoning with a calibrated cone and it says so.

"We could not check" is an answer here exactly as it is in
:mod:`anomaly.identity` and :mod:`baselines`. A projection that quietly fell
back to dead reckoning and presented itself as route-aware would be the worst
of the three, because a consumer would trust it more than it deserves.

What is preserved, on purpose
-----------------------------
The cone still grows with lead time and is still capped by
``MAX_FEASIBLE_SPEED_KN``. Phase 3 association gating reads
``TrackState.uncertainty_radius_m`` and the projection cone on the same
contract; a route-aware prediction that changed that cap in one place would
change gating behaviour as a side effect of a prediction change, which is the
coupling CLAUDE.md §4.5 exists to prevent. Nothing here touches
:mod:`tracks.kalman`.

Every number in this module is measured on the **synthetic corpus** and is
optimistic by construction — see :meth:`RoutePrior.caveat`. The scenario
generator routes its vessels through one deterministic coastal corridor
(``scenario/searoute.py``), so a flow field fitted to generated traffic
recovers the generator's own waypoints. Real coastal traffic is not that tidy.
No figure from this module may be stated as a live one (CLAUDE.md §4.6).
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np

from .. import h3util as tiling
from ..config import MAX_FEASIBLE_SPEED_KN, PIPELINE_VERSION
from .kalman import epoch_s

__all__ = [
    "FlowMode", "RoutePrior", "OwnRouteHistory", "Passage", "SteppedPath",
    "TypeCalibration", "PredictionModel",
    "fit_route_prior", "fit_own_history", "calibrate", "step_along_prior",
    "truncate_path", "physics_cap_m", "motion_class", "WORKING_TYPES",
    "FLOW_RES", "COURSE_OCTANTS", "MIN_UNDERWAY_KN", "MIN_KEY_OBSERVATIONS",
    "MIN_KEY_VESSELS", "MAX_COURSE_DELTA_DEG", "STEP_MINUTES",
    "CONE_PERCENTILE", "MIN_PRIOR_COVERAGE", "MIN_CONE_M", "ROUTE_TABLE",
]

#: Where a learned route prior lands, if it is landed. A conformed table like
#: any other, so the flow field is an inspectable artifact rather than a pickle
#: — the same argument :mod:`baselines` makes for itself (ADR-032 (d)).
ROUTE_TABLE = "route_prior"

#: H3 resolution the flow field is built at.
#:
#: **Res 6 (~6 km across), and the choice is the model's spatial resolution.**
#: Res 7 (~1.2 km) is the canonical join key but would split one lane into a
#: ribbon of cells each holding a handful of fixes, and a transition fitted on
#: a handful of fixes is one ship's routing. Res 5 (~8-10 km, what `baselines`
#: uses) is coarse enough that one cell can hold an approach channel and the
#: open water outside it. Res 6 with the course conditioning below recovers the
#: sub-cell structure that matters without starving the cells.
#:
#: Taken from `h3util` rather than written as a literal (ADR-015).
FLOW_RES = tiling.R6

#: How finely the *incoming* course is binned. Eight octants of 45°.
#:
#: This is the conditioning that makes the field a transition model rather than
#: a histogram (see the module docstring). Finer bins would separate the two
#: arms of a waypoint more sharply and would also divide the support by the
#: same factor; eight is where a res-6 cell on this corpus still carries enough
#: observations per octant to speak.
COURSE_OCTANTS = 8

#: Fixes below this speed contribute nothing to the flow field.
#:
#: A vessel at anchor still reports a course over ground and it is noise: GPS
#: jitter about a swinging circle is uniform over 360°. An anchorage cell
#: fitted from those has no mode and, worse, has *enough observations to look
#: like it does*. Two knots is the threshold `baselines` and the loitering rule
#: already treat as "making way".
MIN_UNDERWAY_KN = 2.0

#: How far ahead the field looks to learn where traffic went next, in minutes.
#: Matched to :data:`STEP_MINUTES` so that one learned transition is exactly one
#: prediction step — a field learned at one horizon and stepped at another
#: would compose turns at the wrong rate.
LOOKAHEAD_MINUTES = 6.0

#: Fewest observations one (cell, incoming octant) key needs before the field
#: will speak for it.
#:
#: Argued rather than tuned, on `baselines.MIN_OBSERVATIONS`' argument one
#: domain along: below this a transition is one or two passages, and a
#: prediction steered by one passage is steered by one master's preference. The
#: floor is deliberately generous — the cost of an absent prior is a fallback
#: to dead reckoning, which is where the system already was.
MIN_KEY_OBSERVATIONS = 20

#: …and from at least this many distinct hulls. Observations alone are not
#: enough: one vessel reporting every two minutes lays thirty fixes in a cell
#: on her own, clears the observation floor by herself, and turns her private
#: routing into "what traffic does here". Same hazard as splitting a dataset by
#: chip rather than by scene.
MIN_KEY_VESSELS = 3

#: How far an incoming course may sit from an octant's centre before the
#: neighbouring octant is consulted as well. Used only for the fallback widen —
#: the exact octant is always preferred.
MAX_COURSE_DELTA_DEG = 60.0

#: How often the prediction re-asks the flow field which way the road goes.
#: Six minutes at 14 knots is 1.4 nm, comfortably inside a res-6 cell, so no
#: cell on the path is stepped over unasked.
STEP_MINUTES = 6.0

#: Fastest a prediction may turn the modelled hull, degrees per minute.
#:
#: A merchant altering 90° at a waypoint takes several minutes about it.
#: Without this clamp the predictor snaps onto the outgoing bearing the instant
#: it enters a cell, which cuts every corner on the inside and puts the
#: predicted position where no hull could have been. 6°/min is a firm but
#: ordinary alteration and is deliberately generous rather than tight:
#: predicting a slower turn than the hull makes is an error the cone absorbs,
#: predicting a faster one is a position she was never at.
TURN_RATE_MAX_DEG_MIN = 6.0

#: Bounds on how far the lane's speed profile may scale her own speed. A single
#: badly-supported cell must not be able to stop a vessel dead or double her.
SPEED_SCALE_MIN, SPEED_SCALE_MAX = 0.25, 2.0

#: What fraction of the stepped path must have had a supported route prior
#: before the projection may call itself route-aware. Below it the answer is
#: ``not_checkable`` and the basis says dead reckoning.
MIN_PRIOR_COVERAGE = 0.5

#: The percentile of *training* prediction error the cone is sized to.
#:
#: **Chosen before the sweep was run, and stated here so it cannot be
#: back-fitted.** A departure has to mean something an operator can hold: "she
#: is outside the ninetieth percentile of what this class of vessel does over
#: this lead". One check in ten lands outside by construction, and the
#: persistence gate is what has to turn that into a finding. Picking this
#: number after seeing which value made the fleet-percentage table look best is
#: exactly the failure ADR-032 caught once already.
CONE_PERCENTILE = 90.0

#: Lead times, in hours, the calibration is fitted at. The cone is linear in
#: lead by contract, so these are the anchors the single per-hour rate is fitted
#: through.
CALIBRATION_LEADS_H = (0.5, 1.0, 3.0, 6.0)

#: Candidate advance factors the calibration searches over. The factor is how
#: far along the modelled path she is predicted to have got, as a fraction of
#: the path the model walked — see :class:`TypeCalibration`.
#:
#: **Stops at 1.0, and the ceiling is real rather than tidy.**
#: :meth:`SteppedPath.at_fraction` clamps at the end of the walked path, so a
#: candidate above 1.0 is the same prediction as 1.0 wearing a different
#: number — it would appear in a landed calibration as a claim the model does
#: not make. The walk already carries her the whole lead at her own speed; if
#: she covers more ground than that, the thing that was wrong is the speed
#: profile, and lengthening the factor would hide it rather than fix it.
ADVANCE_GRID = tuple(round(0.10 + 0.05 * i, 2) for i in range(19))   # 0.10..1.00

#: A cone never tighter than this, whatever a calibration says. Position error
#: on a smoothed AIS track is tens of metres; a cone of that order would report
#: a departure on rounding error.
MIN_CONE_M = 500.0


# ---------------------------------------------------------------------------
# small geodesy — the same formulae `projection` uses, kept local so this
# module can be read on its own
# ---------------------------------------------------------------------------

_R_M = 6_371_000.0


def _hav_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * _R_M * math.asin(math.sqrt(a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _advance(lat: float, lon: float, bearing_deg: float, dist_m: float
             ) -> tuple[float, float]:
    d = dist_m / _R_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def _signed_delta(a: float, b: float) -> float:
    """Smallest signed turn from bearing `a` to bearing `b`, in (-180, 180]."""
    return (float(b) - float(a) + 180.0) % 360.0 - 180.0


def _octant(course_deg: float) -> int:
    return int((float(course_deg) % 360.0) // (360.0 / COURSE_OCTANTS))


def _circ_mean_deg(deg: Sequence[float], w: Optional[Sequence[float]] = None
                   ) -> tuple[float, float]:
    """Circular mean and circular standard deviation of bearings, in degrees.

    An arithmetic mean of 350° and 10° is 180° — the exact reciprocal of the
    right answer, and a flow field that made that mistake would steer vessels
    backwards through every cell straddling north.
    """
    r = np.radians(np.asarray(deg, dtype=float))
    ww = np.ones_like(r) if w is None else np.asarray(w, dtype=float)
    s = float(np.sum(ww * np.sin(r)))
    c = float(np.sum(ww * np.cos(r)))
    n = float(np.sum(ww)) or 1.0
    mean = math.degrees(math.atan2(s, c)) % 360.0
    rbar = min(1.0, math.hypot(s, c) / n)
    std = math.degrees(math.sqrt(-2.0 * math.log(rbar))) if rbar > 1e-9 else 180.0
    return mean, min(180.0, std)


# ---------------------------------------------------------------------------
# the flow field
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FlowMode:
    """Where traffic went next from one cell on one heading, and what it made
    good doing it.

    ``course_deg`` is the *outgoing* bearing over the following
    :data:`LOOKAHEAD_MINUTES` — not the course being steered on arrival. The
    distinction is the whole model: on arrival at a waypoint the two differ,
    and it is the outgoing one that predicts the turn.

    ``sog_made_good_kn`` is displacement over elapsed time, not reported speed
    over ground. A vessel milling in an approach reports twelve knots and makes
    good two, and it is the two that says where she will be.
    """
    course_deg: float
    #: Circular standard deviation of the outgoing bearings behind this mode. A
    #: tightly channelled leg is a few degrees; a junction is tens. Carried
    #: because it is the honest input to how wide the cone should be *here*.
    spread_deg: float
    sog_made_good_kn: float
    n_obs: int
    n_vessels: int

    def as_dict(self) -> dict:
        return {"course_deg": round(self.course_deg, 1),
                "spread_deg": round(self.spread_deg, 1),
                "sog_made_good_kn": round(self.sog_made_good_kn, 2),
                "n_obs": int(self.n_obs), "n_vessels": int(self.n_vessels)}


@dataclass
class RoutePrior:
    """The learned flow field: what traffic does next, cell by cell, heading by
    heading.

    An artifact, not a constant — it carries the provenance envelope every
    other record in this system carries (CLAUDE.md §4.1) and :meth:`as_rows`
    lands it, so an operator can be *shown* why a vessel was predicted round a
    corner rather than asked to trust that she was.
    """
    res: int
    #: ``{cell: {incoming octant: FlowMode}}``
    cells: dict[str, dict[int, FlowMode]] = field(default_factory=dict)
    min_observations: int = MIN_KEY_OBSERVATIONS
    min_vessels: int = MIN_KEY_VESSELS
    octants: int = COURSE_OCTANTS
    lookahead_minutes: float = LOOKAHEAD_MINUTES
    n_positions: int = 0
    n_hulls: int = 0
    #: The hulls this prior was fitted on. Provenance, and what makes a held-out
    #: measurement checkable rather than asserted: if an evaluation hull appears
    #: here, the number measured is a number about memorisation.
    fitted_hulls: tuple[str, ...] = ()
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    source_id: str = "maritime-isr:route_prior"
    source_ref: str = "fit_route_prior"
    pipeline_version: str = PIPELINE_VERSION
    is_synthetic: bool = True

    # -- lookup ---------------------------------------------------------
    def cell_of(self, lat: float, lon: float) -> str:
        return tiling.cell(lat, lon, self.res)

    def modes(self, lat: float, lon: float) -> list[FlowMode]:
        return list(self.cells.get(self.cell_of(lat, lon), {}).values())

    def support(self, lat: float, lon: float) -> Optional[int]:
        """Observations behind this cell, or **None** for "no opinion here".

        None and 0 are different answers, and the distinction is the one
        `baselines.is_unusual` protects: a cell we have never watched is not a
        cell where nothing happens.
        """
        ms = self.modes(lat, lon)
        return sum(m.n_obs for m in ms) if ms else None

    def lookup(self, lat: float, lon: float, course_deg: float
               ) -> Optional[FlowMode]:
        """Which way does *her* road go from here — or None if we cannot say.

        Keyed on her incoming heading, which is the point: the same cell
        answers differently for a vessel arriving from the north and one
        arriving from the west, because they are on different legs of the same
        waypoint. The exact octant is preferred; a neighbouring octant is
        consulted only when the exact one has no support, since 45° is coarse
        enough that a vessel two degrees from a boundary should not fall off
        the map.
        """
        per_octant = self.cells.get(self.cell_of(lat, lon))
        if not per_octant:
            return None
        o = _octant(course_deg)
        m = per_octant.get(o)
        if m is not None:
            return m
        for d in (1, -1):
            m = per_octant.get((o + d) % self.octants)
            if m is not None and abs(_signed_delta(course_deg, m.course_deg)) \
                    <= MAX_COURSE_DELTA_DEG + 45.0:
                return m
        return None

    # -- reporting ------------------------------------------------------
    @property
    def n_cells(self) -> int:
        return len(self.cells)

    @property
    def n_modes(self) -> int:
        return sum(len(v) for v in self.cells.values())

    def as_rows(self) -> list[dict]:
        """Flat rows, one per (cell, octant), for landing and for inspection."""
        rows = []
        for cell, per_octant in sorted(self.cells.items()):
            lat, lon = tiling.cell_center(cell)
            for o, m in sorted(per_octant.items()):
                rows.append({
                    "h3_cell": cell, "res": self.res, "in_octant": o,
                    "in_course_center_deg": o * (360.0 / self.octants)
                    + (180.0 / self.octants),
                    "lat": lat, "lon": lon, **m.as_dict(),
                    "source_id": self.source_id, "source_ref": self.source_ref,
                    "pipeline_version": self.pipeline_version,
                    "confidence": min(1.0, m.n_obs / 500.0),
                    "is_synthetic": self.is_synthetic,
                })
        return rows

    def report(self) -> dict:
        per_cell = [len(v) for v in self.cells.values()]
        spreads = [m.spread_deg for v in self.cells.values() for m in v.values()]
        return {
            "res": self.res, "octants": self.octants,
            "lookahead_minutes": self.lookahead_minutes,
            "n_cells": self.n_cells, "n_modes": self.n_modes,
            "median_modes_per_cell": (float(np.median(per_cell))
                                      if per_cell else 0.0),
            "median_mode_spread_deg": (float(np.median(spreads))
                                       if spreads else None),
            "n_positions_seen": self.n_positions,
            "n_hulls_fitted_on": self.n_hulls,
            "min_observations": self.min_observations,
            "min_vessels": self.min_vessels,
            "window": {"start": self.window_start, "end": self.window_end},
            "caveat": self.caveat(),
            "pipeline_version": self.pipeline_version,
        }

    @staticmethod
    def caveat() -> str:
        return (
            "Fitted on the synthetic scenario corpus, whose vessels are routed "
            "by one deterministic coastal corridor (scenario/searoute.py). A "
            "flow field fitted to that traffic recovers the generator's own "
            "waypoints, so every accuracy figure derived from it is optimistic "
            "by construction. Real coastal traffic is far more dispersed, and "
            "these numbers must be re-measured on the deploy host before any "
            "of them is stated externally (CLAUDE.md §4.6).")


def _track_arrays(track):
    pts = track.points
    if hasattr(pts, "quality"):
        pts = pts[pts.quality != "outlier"]
    if len(pts) < 2:
        return None
    return (epoch_s(pts["ts"]),
            pts["lat"].to_numpy(dtype=float),
            pts["lon"].to_numpy(dtype=float),
            pts["sog_kn"].to_numpy(dtype=float),
            pts["cog_deg"].to_numpy(dtype=float))


def _transitions(track, *, res: int, lookahead_s: float,
                 min_underway_kn: float):
    """Yield (cell, incoming octant, outgoing bearing, speed made good).

    The outgoing bearing is measured to where she actually was ``lookahead_s``
    later, so a turn in progress is already in the number. Samples where the
    far end of the window is missing — a silence — are skipped rather than
    stretched: a bearing measured over a four-hour gap is a bearing over four
    hours, not over six minutes, and averaging the two would teach the field
    that traffic here goes somewhere nobody went.
    """
    arr = _track_arrays(track)
    if arr is None:
        return
    t, lat, lon, sog, cog = arr
    n = len(t)
    for i in range(n - 1):
        if not (np.isfinite(sog[i]) and sog[i] >= min_underway_kn
                and np.isfinite(cog[i])):
            continue
        target = t[i] + lookahead_s
        j = int(np.searchsorted(t, target, side="left"))
        if j >= n:
            break
        dt = t[j] - t[i]
        if dt <= 0 or dt > 3.0 * lookahead_s:
            continue
        d = _hav_m(lat[i], lon[i], lat[j], lon[j])
        if d < 50.0:                    # she has not moved; no bearing to read
            continue
        yield (tiling.cell(float(lat[i]), float(lon[i]), res),
               _octant(cog[i]),
               _bearing_deg(lat[i], lon[i], lat[j], lon[j]),
               d / dt / 0.514444)       # m/s → knots made good


def fit_route_prior(tracks: Iterable, *, hull_of=None, res: int = FLOW_RES,
                    min_observations: int = MIN_KEY_OBSERVATIONS,
                    min_vessels: int = MIN_KEY_VESSELS,
                    min_underway_kn: float = MIN_UNDERWAY_KN,
                    lookahead_minutes: float = LOOKAHEAD_MINUTES) -> RoutePrior:
    """Learn the flow field from built tracks.

    ``hull_of(track) -> str`` names the hull a track belongs to; the default
    uses the track's own grouping key. Hull identity matters twice — a key
    needs several *hulls* before it speaks (``min_vessels``), and the set fitted
    on is recorded so a held-out measurement can be checked rather than
    asserted.
    """
    if hull_of is None:
        def hull_of(t):                                   # noqa: E306
            return str(getattr(t, "track_key", None) or getattr(t, "track_id", ""))

    look_s = lookahead_minutes * 60.0
    bear: dict[tuple[str, int], list[float]] = defaultdict(list)
    speed: dict[tuple[str, int], list[float]] = defaultdict(list)
    who: dict[tuple[str, int], set] = defaultdict(set)
    hulls: set[str] = set()
    n_pos = 0
    t_lo = t_hi = None

    for tr in tracks:
        hull = str(hull_of(tr))
        hulls.add(hull)
        arr = _track_arrays(tr)
        if arr is not None:
            t_lo = float(arr[0][0]) if t_lo is None else min(t_lo, float(arr[0][0]))
            t_hi = float(arr[0][-1]) if t_hi is None else max(t_hi, float(arr[0][-1]))
        for cell, o, b, v in _transitions(tr, res=res, lookahead_s=look_s,
                                          min_underway_kn=min_underway_kn):
            bear[(cell, o)].append(b)
            speed[(cell, o)].append(v)
            who[(cell, o)].add(hull)
            n_pos += 1

    cells: dict[str, dict[int, FlowMode]] = defaultdict(dict)
    for key, bs in bear.items():
        if len(bs) < min_observations or len(who[key]) < min_vessels:
            continue
        mean, std = _circ_mean_deg(bs)
        cells[key[0]][key[1]] = FlowMode(
            course_deg=mean, spread_deg=std,
            sog_made_good_kn=float(np.median(speed[key])),
            n_obs=len(bs), n_vessels=len(who[key]))

    import pandas as pd
    return RoutePrior(
        res=res, cells=dict(cells), min_observations=min_observations,
        min_vessels=min_vessels, lookahead_minutes=lookahead_minutes,
        n_positions=n_pos, n_hulls=len(hulls), fitted_hulls=tuple(sorted(hulls)),
        window_start=(None if t_lo is None
                      else pd.Timestamp(t_lo, unit="s", tz="UTC").isoformat()),
        window_end=(None if t_hi is None
                    else pd.Timestamp(t_hi, unit="s", tz="UTC").isoformat()))


# ---------------------------------------------------------------------------
# the vessel's own history
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Passage:
    """One hull's transit of one cell on one heading, with when it ended.

    ``t_end`` is the field that makes causality checkable: a passage may only
    inform a projection made *after* the passage finished.
    """
    t_end: float
    in_octant: int
    out_course_deg: float
    spread_deg: float
    sog_made_good_kn: float
    n_obs: int


@dataclass
class OwnRouteHistory:
    """Each hull's own previous passages, cell by cell and heading by heading.

    Held separately from the fleet prior rather than folded into it, because
    the two answer different questions and one of them has to be asked with a
    clock in hand. "What does traffic do here" is timeless enough to fit once;
    "what did *she* do here, **before now**" is not, and a structure that could
    not express the "before now" would leak her future into her own prediction
    by construction.
    """
    res: int
    #: ``{(hull, cell): [Passage, …]}``, each list sorted by ``t_end``.
    passages: dict[tuple[str, str], list[Passage]] = field(default_factory=dict)
    octants: int = COURSE_OCTANTS

    def lookup(self, hull: str, lat: float, lon: float, course_deg: float, *,
               before: float) -> Optional[FlowMode]:
        """What did this hull do here, on this heading, before now — or None.

        **Only passages that ended strictly before ``before``.** Her transit of
        a cell she has not yet reached lies in the future of this prediction,
        and steering with it would be reading the answer key.
        """
        ps = self.passages.get((str(hull), tiling.cell(lat, lon, self.res)))
        if not ps:
            return None
        cut = bisect_left([p.t_end for p in ps], float(before))
        o = _octant(course_deg)
        past = [p for p in ps[:cut] if p.in_octant == o]
        if not past:
            return None
        mean, std = _circ_mean_deg([p.out_course_deg for p in past],
                                   [p.n_obs for p in past])
        return FlowMode(
            course_deg=mean,
            spread_deg=max(std, min(p.spread_deg for p in past)),
            sog_made_good_kn=float(np.median([p.sog_made_good_kn for p in past])),
            n_obs=int(sum(p.n_obs for p in past)), n_vessels=1)


def fit_own_history(tracks: Iterable, *, hull_of=None, res: int = FLOW_RES,
                    min_underway_kn: float = MIN_UNDERWAY_KN,
                    lookahead_minutes: float = LOOKAHEAD_MINUTES,
                    min_passage_obs: int = 3) -> OwnRouteHistory:
    """Break every track into per-cell, per-heading passages, keyed by hull.

    A *passage* is a contiguous run of her fixes inside one cell on one
    incoming octant. Contiguity and heading both matter: a hull that passes
    through a cell northbound on Monday and southbound on Friday has two
    passages going opposite ways, and averaging them would predict her up the
    middle of the two.
    """
    if hull_of is None:
        def hull_of(t):                                   # noqa: E306
            return str(getattr(t, "track_key", None) or getattr(t, "track_id", ""))

    look_s = lookahead_minutes * 60.0
    out: dict[tuple[str, str], list[Passage]] = defaultdict(list)

    for tr in tracks:
        hull = str(hull_of(tr))
        arr = _track_arrays(tr)
        if arr is None:
            continue
        t, lat, lon, sog, cog = arr
        n = len(t)
        # One entry per *contiguous* run, in the order they happened. Keyed by
        # position in this list rather than by (cell, octant), because a hull
        # that leaves a cell and comes back a week later has made two passages
        # and they must not be averaged into one: a dict keyed on the cell
        # merges them, and then reports the merger once for each visit.
        runs: list[tuple[tuple[str, int], list[tuple[float, float, float]]]] = []
        prev_key = None
        for i in range(n - 1):
            if not (np.isfinite(sog[i]) and sog[i] >= min_underway_kn
                    and np.isfinite(cog[i])):
                continue
            target = t[i] + look_s
            j = int(np.searchsorted(t, target, side="left"))
            if j >= n:
                break
            dt = t[j] - t[i]
            if dt <= 0 or dt > 3.0 * look_s:
                continue
            d = _hav_m(lat[i], lon[i], lat[j], lon[j])
            if d < 50.0:
                continue
            key = (tiling.cell(float(lat[i]), float(lon[i]), res),
                   _octant(cog[i]))
            if key != prev_key:
                runs.append((key, []))
                prev_key = key
            runs[-1][1].append((float(t[i]),
                                _bearing_deg(lat[i], lon[i], lat[j], lon[j]),
                                d / dt / 0.514444))
        # One passage per contiguous run, so a re-entry is a second passage
        # with its own end time — which is what makes the causality cut in
        # `lookup` mean anything: her Monday transit may inform a Friday
        # prediction and her Friday transit may not inform the Monday one.
        for key, rows in runs:
            if len(rows) < min_passage_obs:
                continue
            mean, std = _circ_mean_deg([r[1] for r in rows])
            out[(hull, key[0])].append(Passage(
                t_end=max(r[0] for r in rows), in_octant=key[1],
                out_course_deg=mean, spread_deg=std,
                sog_made_good_kn=float(np.median([r[2] for r in rows])),
                n_obs=len(rows)))

    for k in out:
        out[k].sort(key=lambda p: p.t_end)
    return OwnRouteHistory(res=res, passages=dict(out))


# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------

@dataclass
class SteppedPath:
    """The result of walking a route prior forward.

    Carries the whole polyline and the cumulative distance along it, so a
    calibration can ask "where would she be at 0.7 of this path" without
    walking it again — and so the UI can draw the curve an operator is being
    asked to believe.
    """
    path: list[tuple[float, float]] = field(default_factory=list)
    cum_m: list[float] = field(default_factory=list)
    final_course_deg: float = 0.0
    #: Fraction of steps that had a supported prior to follow.
    coverage: float = 0.0
    #: ``own_history`` | ``fleet_prior`` | ``not_checkable``
    support: str = "not_checkable"
    #: Mean circular spread of the modes followed, degrees, or None.
    mean_spread_deg: Optional[float] = None

    @property
    def length_m(self) -> float:
        return self.cum_m[-1] if self.cum_m else 0.0

    def at_fraction(self, f: float) -> tuple[float, float]:
        """The point a fraction ``f`` of the way along the walked path.

        Linear in *distance along the path*, not in step index, so a walk that
        slowed through an approach still reports the right point.
        """
        if not self.path:
            raise ValueError("empty path")
        if len(self.path) == 1 or self.length_m <= 0:
            return self.path[0]
        target = max(0.0, f) * self.length_m
        if target >= self.length_m:
            return self.path[-1]
        k = bisect_right(self.cum_m, target)
        k = min(max(k, 1), len(self.path) - 1)
        lo, hi = self.cum_m[k - 1], self.cum_m[k]
        w = 0.0 if hi <= lo else (target - lo) / (hi - lo)
        (a_lat, a_lon), (b_lat, b_lon) = self.path[k - 1], self.path[k]
        seg = _hav_m(a_lat, a_lon, b_lat, b_lon)
        if seg <= 0:
            return self.path[k]
        return _advance(a_lat, a_lon,
                        _bearing_deg(a_lat, a_lon, b_lat, b_lon), w * seg)


def step_along_prior(*, lat: float, lon: float, cog_deg: float, sog_kn: float,
                     lead_h: float, model: "PredictionModel",
                     hull: Optional[str] = None,
                     made_at: Optional[float] = None,
                     step_minutes: float = STEP_MINUTES,
                     turn_rate_max_deg_min: float = TURN_RATE_MAX_DEG_MIN
                     ) -> SteppedPath:
    """Walk ``lead_h`` hours forward, turning towards the road and taking its
    speed profile.

    Her own history is asked first and the fleet flow field second. That order
    is the design: the fleet says what traffic does, she says what *she* does,
    and where the two disagree the hull that has run this leg before is the
    better witness.

    The turn is rate-limited (:data:`TURN_RATE_MAX_DEG_MIN`) and the speed is
    her own, scaled by how the lane's speed here compares with the lane's speed
    where she started (:data:`SPEED_SCALE_MIN`/``MAX``). A hull running fast for
    the lane stays fast for the lane; the profile says *when* traffic slows,
    not how fast this hull is.
    """
    steps = max(1, min(400, int(math.ceil(lead_h * 60.0 / step_minutes))))
    dt_h = lead_h / steps
    max_turn = turn_rate_max_deg_min * (dt_h * 60.0)

    course = float(cog_deg)
    ref_lane_kn: Optional[float] = None
    n_supported = 0
    used_own = False
    spreads: list[float] = []
    path: list[tuple[float, float]] = [(lat, lon)]
    cum: list[float] = [0.0]

    for _ in range(steps):
        mode = None
        if model.own is not None and hull and made_at is not None:
            mode = model.own.lookup(hull, lat, lon, course, before=made_at)
            if mode is not None:
                used_own = True
        if mode is None and model.prior is not None:
            mode = model.prior.lookup(lat, lon, course)

        speed = float(sog_kn)
        if mode is not None:
            n_supported += 1
            spreads.append(mode.spread_deg)
            d = _signed_delta(course, mode.course_deg)
            course = (course + max(-max_turn, min(max_turn, d))) % 360.0
            if ref_lane_kn is None:
                ref_lane_kn = max(0.5, mode.sog_made_good_kn)
            scale = mode.sog_made_good_kn / ref_lane_kn
            speed = float(sog_kn) * max(SPEED_SCALE_MIN,
                                        min(SPEED_SCALE_MAX, scale))

        seg = speed * 1852.0 * dt_h
        lat, lon = _advance(lat, lon, course, seg)
        path.append((lat, lon))
        cum.append(cum[-1] + seg)

    coverage = n_supported / steps
    if coverage < MIN_PRIOR_COVERAGE:
        support = "not_checkable"
    elif used_own:
        support = "own_history"
    else:
        support = "fleet_prior"
    return SteppedPath(path=path, cum_m=cum, final_course_deg=course,
                       coverage=coverage, support=support,
                       mean_spread_deg=(float(np.mean(spreads))
                                        if spreads else None))


def truncate_path(stepped: SteppedPath, f: float) -> list[tuple[float, float]]:
    """The walked path clipped at a fraction ``f`` of its length.

    The predicted position is :meth:`SteppedPath.at_fraction`; the curve an
    operator is shown has to *end there*. Handing the UI the whole walk while
    marking the position part-way along it would draw a road continuing past
    the prediction, which reads as "and then she does this too" — a claim the
    model is not making.
    """
    if not stepped.path:
        return []
    end = stepped.at_fraction(f)
    target = max(0.0, f) * stepped.length_m
    out = [p for p, c in zip(stepped.path, stepped.cum_m) if c <= target]
    if not out:
        out = [stepped.path[0]]
    if out[-1] != end:
        out.append(end)
    return out


def physics_cap_m(lead_h: float) -> float:
    """The widest any cone may be at this lead — the Phase 3 contract.

    Restated in one place so both predictors use the identical cap and a
    prediction change cannot alter association gating as a side effect.
    """
    return MAX_FEASIBLE_SPEED_KN * 1852.0 * max(0.0, lead_h)


# ---------------------------------------------------------------------------
# per-type calibration
# ---------------------------------------------------------------------------

#: Motion-inferred types that move like a vessel working a ground rather than a
#: vessel going somewhere. Everything else that is a claim folds to
#: ``merchant``; ``unclassified`` stays itself.
WORKING_TYPES = frozenset({"fishing", "trawler", "survey", "tug", "dredger"})


def motion_class(vessel_type: Optional[str]) -> str:
    """Fold a motion-inferred type into the class the *calibration* is fitted per.

    This is **not** a second vessel-type vocabulary and it does not re-decide
    anything :mod:`tracks.vessel_type` decided — it takes that module's answer
    and buckets it, for one reason that is about sample counts rather than
    about ships: the corpus carries five VLCC tracks, and an advance factor and
    a cone growth rate fitted on five tracks are fitted on five masters.

    The fold is the one distinction that is large in the *kinematics*, measured
    rather than assumed: over three hours a merchant makes good close to the
    whole of speed × time because she is going somewhere, and a vessel working
    a ground makes good a fraction of it because she goes back over her own
    water. Splitting Aframax from Suezmax buys nothing here — they predict
    identically — while splitting merchant from fishing changes the advance
    factor from 1.0 to 0.1.

    ``unclassified`` is preserved as its own class and never folded into
    ``merchant``: "we could not say what she is" is a third answer everywhere
    else in this system, and giving it the merchant calibration would quietly
    assert the commonest class about every hull the classifier declined.
    """
    if not vessel_type or vessel_type == "unclassified":
        return "unclassified"
    return "fishing" if str(vessel_type).lower() in WORKING_TYPES else "merchant"


@dataclass
class TypeCalibration:
    """How this class of vessel actually moves, measured rather than assumed.

    ``advance_factor`` is **how far along the modelled path she is predicted to
    have got**, as a fraction of the path the model walked. It is not a fudge
    factor: a merchant on passage comes out near 1.0 because she goes where the
    road goes at the speed the road goes, while a trawler comes out far below
    it because she works a ground and the distance she *travels* is not the
    distance she *makes good*. Predicting a trawler 45 nm along her heading
    because she is doing 15 knots is the single largest error dead reckoning
    makes on this corpus, and it is not a cone problem — it is a wrong
    prediction with a cone drawn round it.

    ``cone_growth_m_per_hour`` is the :data:`CONE_PERCENTILE` of *training*
    prediction error per hour of lead. It is a calibration, not a threshold:
    the percentile was fixed before the sweep ran.
    """
    vessel_type: str
    advance_factor: dict[float, float] = field(default_factory=dict)
    cone_growth_m_per_hour: float = 1852.0
    n_samples: int = 0
    #: Median and 90th-percentile error, per lead, on the fitting set, in
    #: nautical miles. Reported so a reader sees the spread the cone was sized
    #: against and not only the cone.
    fit_error_nm: dict[float, dict] = field(default_factory=dict)

    def factor_at(self, lead_h: float) -> float:
        """Advance factor at an arbitrary lead, interpolated between anchors."""
        if not self.advance_factor:
            return 1.0
        leads = sorted(self.advance_factor)
        if lead_h <= leads[0]:
            return self.advance_factor[leads[0]]
        if lead_h >= leads[-1]:
            return self.advance_factor[leads[-1]]
        k = bisect_left(leads, lead_h)
        a, b = leads[k - 1], leads[k]
        fa, fb = self.advance_factor[a], self.advance_factor[b]
        return fa + ((lead_h - a) / (b - a)) * (fb - fa)

    def as_dict(self) -> dict:
        return {"vessel_type": self.vessel_type,
                "advance_factor": {str(k): round(v, 3)
                                   for k, v in sorted(self.advance_factor.items())},
                "cone_growth_m_per_hour": round(self.cone_growth_m_per_hour, 1),
                "cone_growth_nm_per_hour": round(
                    self.cone_growth_m_per_hour / 1852.0, 2),
                "fit_error_nm": {str(k): v
                                 for k, v in sorted(self.fit_error_nm.items())},
                "n_samples": self.n_samples,
                "cone_percentile": CONE_PERCENTILE}


@dataclass
class PredictionModel:
    """Everything a route-aware projection needs, in one object.

    Assembled once and passed in, never rebuilt per call: fitting a flow field
    inside a projection would make the cost of one prediction depend on the size
    of the corpus, and would make it impossible to say which hulls a prediction
    was informed by.
    """
    prior: Optional[RoutePrior] = None
    own: Optional[OwnRouteHistory] = None
    calibration: dict[str, TypeCalibration] = field(default_factory=dict)
    #: Used for a hull whose type the motion classifier declined to name.
    #: ``unclassified`` is a first-class answer here as everywhere else, and it
    #: gets its own calibration — never the merchant one.
    fallback: Optional[TypeCalibration] = None
    baselines: object = None       # a `baselines.BaselineIndex`, or None

    def calibration_for(self, vessel_type: Optional[str]) -> TypeCalibration:
        if vessel_type and vessel_type in self.calibration:
            return self.calibration[vessel_type]
        folded = motion_class(vessel_type)
        if folded in self.calibration:
            return self.calibration[folded]
        if self.fallback is not None:
            return self.fallback
        return TypeCalibration(vessel_type or "unknown")

    def report(self) -> dict:
        return {
            "prior": (self.prior.report() if self.prior else None),
            "own_history_hulls": (len({h for h, _ in self.own.passages})
                                  if self.own else 0),
            "calibration": {k: v.as_dict()
                            for k, v in sorted(self.calibration.items())},
            "fallback": (self.fallback.as_dict() if self.fallback else None),
            "has_baselines": self.baselines is not None,
        }


def _sample_stride(n: int, max_samples: int) -> int:
    return max(1, n // max(1, max_samples))


def _walk_samples(track, lead_h: float, max_samples: int,
                  model: "PredictionModel", vtype: Optional[str], hull: str):
    """Yield (SteppedPath-or-None, straight-line origin, observed lat/lon).

    One walk per sample, reused across every candidate advance factor — which
    is what makes a grid search over the factor affordable.
    """
    arr = _track_arrays(track)
    if arr is None:
        return
    t, lat, lon, sog, cog = arr
    if len(t) < 3:
        return
    lead_s = lead_h * 3600.0
    for i in range(0, len(t) - 1, _sample_stride(len(t), max_samples)):
        target = t[i] + lead_s
        if target > t[-1]:
            break
        j = int(np.searchsorted(t, target, side="left"))
        j = min(j, len(t) - 1)
        if abs(t[j] - target) > lead_s * 0.5:
            continue
        if not np.isfinite(sog[i]) or sog[i] * lead_h < 0.05:
            continue
        sp = None
        if model.prior is not None or model.own is not None:
            sp = step_along_prior(
                lat=float(lat[i]), lon=float(lon[i]), cog_deg=float(cog[i]),
                sog_kn=float(sog[i]), lead_h=lead_h, model=model, hull=hull,
                made_at=float(t[i]))
        if sp is None or sp.support == "not_checkable":
            # The dead-reckoning arm — and also the route arm's own fallback,
            # built here rather than left to the caller so that the calibration
            # is fitted on **exactly** the path `projection.project_route_aware`
            # will walk. A factor fitted against a part-bent path and then
            # applied to a straight one is a number borrowed from a model that
            # is not the one running.
            d = float(sog[i]) * 1852.0 * lead_h
            end = _advance(float(lat[i]), float(lon[i]), float(cog[i]), d)
            sp = SteppedPath(path=[(float(lat[i]), float(lon[i])), end],
                             cum_m=[0.0, d], final_course_deg=float(cog[i]),
                             coverage=(sp.coverage if sp is not None else 0.0),
                             support="not_checkable")
        yield sp, float(lat[j]), float(lon[j])


def calibrate(labelled: Sequence[tuple[str, object]], *,
              model: Optional["PredictionModel"] = None,
              hull_of=None,
              leads: Sequence[float] = CALIBRATION_LEADS_H,
              max_samples: int = 40,
              percentile: float = CONE_PERCENTILE
              ) -> dict[str, TypeCalibration]:
    """Measure, per vessel type, how far along the road she gets and how wrong
    the finished predictor is.

    ``labelled`` is ``(vessel_type, track)`` — the type as **inferred from
    motion** by :mod:`tracks.vessel_type`, not as declared, because the whole
    point is that a radar contact with no identity gets the same treatment.

    Two quantities, fitted in one pass over the same walks:

    * the **advance factor**, chosen from :data:`ADVANCE_GRID` as the fraction
      of the modelled path that minimises median error. Fitted against the
      model that will actually run, so the dead-reckoning arm and the
      route-aware arm are each given their own best point estimate and the
      comparison between them is not a comparison between a tuned model and an
      untuned one.
    * the **cone**, sized to the :data:`CONE_PERCENTILE` of the error that
      remains once that factor is applied. Sizing a cone against an error the
      predictor no longer makes would leave a cone nothing can depart from.

    Pass ``model=None`` to calibrate plain dead reckoning, which is the control
    arm the comparison needs.
    """
    staged = PredictionModel(
        prior=(model.prior if model else None),
        own=(model.own if model else None),
        baselines=(model.baselines if model else None))

    # {type: {lead: [(SteppedPath, obs_lat, obs_lon), …]}}
    walks: dict[str, dict[float, list]] = defaultdict(lambda: defaultdict(list))
    for vtype, track in labelled:
        hull = (str(hull_of(track)) if hull_of else
                str(getattr(track, "track_key", None) or ""))
        for lead in leads:
            for sp, olat, olon in _walk_samples(track, lead, max_samples,
                                                staged, vtype, hull):
                walks[vtype][lead].append((sp, olat, olon))

    calib: dict[str, TypeCalibration] = {}
    for vtype, per_lead in walks.items():
        factors: dict[float, float] = {}
        errors: dict[float, dict] = {}
        rates: list[float] = []
        n = 0
        for lead, rows in per_lead.items():
            if len(rows) < 20:
                continue
            n += len(rows)
            best_f, best_med, best_errs = 1.0, None, None
            for f in ADVANCE_GRID:
                errs = []
                for sp, olat, olon in rows:
                    plat, plon = sp.at_fraction(f)
                    errs.append(_hav_m(plat, plon, olat, olon))
                med = float(np.median(errs))
                if best_med is None or med < best_med:
                    best_f, best_med, best_errs = f, med, errs
            factors[lead] = best_f
            errors[lead] = {"p50": round(best_med / 1852.0, 2),
                            "p90": round(float(np.percentile(best_errs, 90))
                                         / 1852.0, 2),
                            "n": len(best_errs)}
            rates.append(float(np.percentile(best_errs, percentile)) / lead)
        calib[vtype] = TypeCalibration(
            vessel_type=vtype, advance_factor=factors,
            cone_growth_m_per_hour=(float(np.median(rates)) if rates
                                    else 1852.0),
            n_samples=n, fit_error_nm=errors)
    return calib
