"""Where should she be by now? — forward projection as a first-class assertion.

*"Note the word in the title: predictive. Detection of what already happened is
not what was asked for. Project the track forward and hold the projection as a
first-class assertion with growing uncertainty, so that a vessel which departs
from its own predicted path is detectable as such."* — the IDEX Challenge 82
brief, Area 2.

Two words in that sentence decide the design.

**"First-class assertion."** A projection is not a number computed inside a
comparison and thrown away. It is a record with a time it was made, a state it
was made from, an uncertainty that grows with how far ahead it reaches, and the
provenance of the track it came from — the same envelope every other record in
this system carries (CLAUDE.md §4.1). That is what makes a departure
*attributable*: an operator can ask "what did you expect, when did you expect
it, and how sure were you", and the answer is a stored row rather than a
re-derivation that may no longer reproduce.

**"Growing uncertainty."** The cone is not decoration. Its growth is what stops
this rule firing on every vessel that alters course: a projection made an hour
ago is tight and a departure from it means something; one made two days ago is
so wide that nothing can depart from it, and the rule must say so rather than
claim a hit. The cone is capped by physics — no vessel outruns
``MAX_FEASIBLE_SPEED_KN`` — which is the same cap Phase 3's association gating
depends on, imported rather than restated.

What this deliberately is *not*
-------------------------------
It is **dead reckoning with an honest error model**, not a learned trajectory
predictor. A hull holding course and speed goes on holding them; that is the
null hypothesis, and a departure from it is the signal.

**And the signal does not discriminate. Measured, and reported rather than
buried.**
------------------------------------------------------------------------
The brief asks that "a vessel which departs from its own predicted path is
detectable as such". It is detectable. It is also *ubiquitous*, and a swept
measurement over the whole corpus says so plainly — 209 AIS tracks, every
combination of lead time, persistence gate and severity threshold:

===========  =========  ============  ============  =========
lead (h)     min run    radii >=      departures    % of fleet
===========  =========  ============  ============  =========
0.5          2          1             1,770         98%
0.5          3          5               377         73%
1.0          3          5               775         92%
3.0          2          5             1,177         97%
3.0          2          20               22         10%
===========  =========  ============  ============  =========

There is no plateau. The rule either flags almost every hull in the picture or,
past a cliff, almost none — and a threshold sitting on a cliff is fitted to this
corpus rather than to the phenomenon. The reason is physical and not fixable by
tuning: at three hours a merchant runs 45 nm, a cone tight enough to notice an
alteration is about ±4° of heading, and **every vessel alters course at every
waypoint**. Dead reckoning is a good predictor along a leg and a useless one
across a turn, and a coastal voyage is mostly turns.

So, under ADR-004 — precision before recall for anything analyst-facing —
**departure from a dead-reckoned track is not promoted to a suspicion factor.**
The Vessel of Interest list does not carry it, and `assistant/catalog.py` has no
entry for it. Shipping a rule that flags 98% of the fleet would bury the
findings that matter, which is the failure that policy exists to prevent.

What the projection *is* used for, and it is worth having:

* **An assertion an operator can see.** "Where did you expect her, and how
  sure were you" is answerable, with a cone whose growth is stated.
* **Bridging a gap.** Where a vessel could have been during her silence is the
  same arithmetic, and it is what the imaging-opportunity layer already needs
  (ADR-026).
* **A comparison an analyst can call up** on a subject already under suspicion
  for another reason, which is a different and far safer use than a detector.

What would make it discriminate is stated so it is not rediscovered: prediction
has to be **route-aware**. "She is on the Kandla-Colombo track and will alter at
the waypoint" is the null hypothesis that a coastal voyage actually follows, and
this system's zone layer (ADR-030) already holds customary lanes that a corridor
model could be fitted to. That is real work and it is not this session's.

That work was then done — and the answer was still no
-----------------------------------------------------
:mod:`tracks.route_prior` builds the route-aware predictor ADR-032 asked for:
a learned flow field over H3 cells keyed on *(cell, incoming heading)*, a
per-motion-class calibration, the hull's own previous passages, and a cone
sized to measured error rather than to a constant.
:func:`project_route_aware` is the entry point, and as a **predictor** it is a
real improvement — median position error on held-out hulls of the synthetic
corpus falls from 3.33 nm to 2.46 nm at three hours and from 9.75 nm to 5.59 nm
at six.

**It is still not a suspicion factor**, and the reason is a different one from
ADR-032's, which is why it is stated here rather than assumed.

*One.* The improvement is in the **median and not in the tail**. At three hours
the median falls 26% and the ninetieth percentile falls 6%; at six hours, 43%
and 1%. A detector lives on the tail — the cone is sized at the ninetieth
percentile — so a model that predicts the ordinary vessel much better and the
awkward one no better is a better **assertion** and not a better detector.

*Two.* Once the cone is honestly calibrated, **the fraction of the fleet
outside it is set by the percentile, not by the vessels.** ADR-032's 98% was
measured against a cone growing at a stated-but-unfitted 1 nm per hour; the
measured rate is six to seven. Calibrate either arm honestly and the severity
axis grades smoothly instead of falling off a cliff — which is genuinely new —
but it grades in *both* arms, so the grading is a property of calibration
rather than of route-awareness.

*Three, and decisive.* Against the scenario's own answer key, on held-out
hulls, precision runs **0.09 to 0.33 across every operating point, against a
base rate of 0.15** — at or barely above chance, and nowhere near the ≥0.7
ADR-004 requires. So ``assistant/catalog.py`` still has no entry for departure
and ``test_projection_is_not_a_registered_suspicion_factor`` still stands. See
HANDOFF_PREDICTION.md and the proposed ADR-042 for all four tables.

What route-awareness *is* now good for is the honest use ADR-032 already named,
made measurably better: an assertion an operator can see (with the curve
attached, not just the endpoint), a gap bridged along the road rather than
along a heading, and an expected-versus-actual comparison called up on a
subject already suspicious for another reason.

Every figure above is **on the synthetic corpus**, whose vessels are routed
through one deterministic coastal corridor; a flow field fitted to them
recovers the generator's own waypoints, so the route arm is flattered by
construction and the gap between the arms is an upper bound on the real one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..config import MAX_FEASIBLE_SPEED_KN, PIPELINE_VERSION
from .kalman import epoch_s

__all__ = ["Projection", "Departure", "project", "project_from",
           "project_route_aware", "check_departure", "departures_along",
           "REFERENCE_LANE_SPREAD_DEG", "REFERENCE_SPEED_SPREAD"]

#: How fast the cone opens, in metres of radius per hour of lead time, on top
#: of the position uncertainty the track already carries.
#:
#: **A statement about how well a vessel holds a course, not a fitted number.**
#: A merchant on passage holds her track to well under a mile an hour of
#: cross-track error in ordinary conditions; a knot of unmodelled set from
#: current or a two-degree steering bias accumulates at roughly this rate. It is
#: deliberately generous — the cost of a cone that is too tight is a false
#: departure on every vessel that alters for weather, and ADR-004 spends its
#: whole budget keeping this queue short.
CONE_GROWTH_M_PER_HOUR = 1852.0

#: Below this lead time a projection is not worth making: the track's own
#: position uncertainty dominates and any "departure" is measurement noise.
MIN_LEAD_MINUTES = 20.0

#: Above this the cone is wider than anything useful and the rule refuses.
#: At 24 hours the radius is already ~44 km before the physics cap, which is
#: most of a day's steaming across a strait; claiming a departure from that is
#: claiming almost nothing.
MAX_LEAD_HOURS = 24.0

#: How far outside the cone a fix has to sit before it is called a departure,
#: as a multiple of the cone radius. One radius is the cone; 1.0 means "outside
#: it at all". Kept as a named constant because it is the precision knob.
DEPARTURE_MARGIN = 1.0


def _hav_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * 6_371_000.0 * math.asin(math.sqrt(a))


def _advance(lat: float, lon: float, bearing_deg: float, dist_m: float
             ) -> tuple[float, float]:
    """Great-circle destination from a point, bearing and distance."""
    r = 6_371_000.0
    d = dist_m / r
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


@dataclass
class Projection:
    """Where a vessel was expected to be, asserted at a stated moment.

    Carries its own provenance envelope because it is a record, not an
    intermediate: ``made_at`` is when the assertion was made, ``valid_for`` is
    the moment it describes, and the two together are what let a departure be
    attributed to a specific prediction rather than to "the model".
    """
    track_id: Optional[str]
    track_source: Optional[str]
    #: When the projection was made — the last fix it was based on.
    made_at: float
    #: The moment it describes.
    valid_for: float
    lat: float
    lon: float
    #: Radius of the uncertainty cone at ``valid_for``, metres.
    radius_m: float
    #: The state it was projected from.
    from_lat: float
    from_lon: float
    from_sog_kn: float
    from_cog_deg: float
    #: Confidence that the vessel is inside the cone. Falls with lead time —
    #: a projection is less believable the further ahead it reaches, and a
    #: number that did not say so would be lying by omission.
    confidence: float = 0.0
    basis: str = "dead reckoning from the last fix, cone capped by physics"
    pipeline_version: str = PIPELINE_VERSION

    # -- route-aware fields (ADR-042). Defaulted so that every existing caller
    #    of `project_from` keeps working unchanged, and so that a dead-reckoned
    #    projection reports `not_checkable` rather than silently claiming to
    #    have followed a road it never asked about.
    #: ``own_history`` | ``fleet_prior`` | ``not_checkable``.
    route_support: str = "not_checkable"
    #: Fraction of the stepped path that had a supported route prior.
    prior_coverage: float = 0.0
    #: How much the calibrated cone was widened or narrowed for local
    #: conditions — lane tightness and the per-area baseline. 1.0 means
    #: "nothing local was known", which is the honest default.
    cone_modulation: float = 1.0
    #: The motion-inferred vessel type the calibration was taken from, or None.
    vessel_type: Optional[str] = None
    #: The predicted path, as (lat, lon) waypoints. Empty for dead reckoning,
    #: where the path is the straight line the endpoints already describe. An
    #: operator asked to believe a curved prediction is entitled to see the
    #: curve, and the UI draws this.
    path: list = field(default_factory=list)

    @property
    def lead_hours(self) -> float:
        return max(0.0, self.valid_for - self.made_at) / 3600.0

    def contains(self, lat: float, lon: float,
                 margin: float = DEPARTURE_MARGIN) -> bool:
        return _hav_m(self.lat, self.lon, lat, lon) <= self.radius_m * margin

    def as_dict(self) -> dict:
        return {
            "track_id": self.track_id, "track_source": self.track_source,
            "made_at": self.made_at, "valid_for": self.valid_for,
            "lead_hours": round(self.lead_hours, 2),
            "lat": round(self.lat, 5), "lon": round(self.lon, 5),
            "radius_m": round(self.radius_m, 1),
            "radius_nm": round(self.radius_m / 1852.0, 2),
            "from": {"lat": round(self.from_lat, 5),
                     "lon": round(self.from_lon, 5),
                     "sog_kn": round(self.from_sog_kn, 2),
                     "cog_deg": round(self.from_cog_deg, 1)},
            "confidence": round(self.confidence, 3),
            "basis": self.basis,
            "route_support": self.route_support,
            "prior_coverage": round(self.prior_coverage, 3),
            "cone_modulation": round(self.cone_modulation, 3),
            "vessel_type": self.vessel_type,
            "path": [[round(a, 5), round(b, 5)] for a, b in self.path],
            "pipeline_version": self.pipeline_version,
        }


@dataclass
class Departure:
    """A vessel found outside her own projected cone."""
    projection: Projection
    observed_lat: float
    observed_lon: float
    observed_at: float
    distance_m: float
    #: How many cone radii outside. 1.0 is on the edge; 3.0 is unambiguous.
    radii_outside: float
    confidence: float
    statement: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "projection": self.projection.as_dict(),
            "observed": {"lat": round(self.observed_lat, 5),
                         "lon": round(self.observed_lon, 5),
                         "at": self.observed_at},
            "distance_m": round(self.distance_m, 1),
            "distance_nm": round(self.distance_m / 1852.0, 2),
            "radii_outside": round(self.radii_outside, 2),
            "confidence": round(self.confidence, 3),
            "statement": self.statement,
            "detail": self.detail,
        }


def project_from(*, lat: float, lon: float, sog_kn: float, cog_deg: float,
                 made_at: float, valid_for: float,
                 position_sigma_m: float = 0.0,
                 track_id: Optional[str] = None,
                 track_source: Optional[str] = None) -> Projection:
    """Project one state forward to one moment.

    The cone radius is the track's own position uncertainty, plus growth with
    lead time, **capped by what a hull could physically do**: nothing may be
    more uncertain than "somewhere within the distance she could have covered at
    the feasible-speed ceiling". Without that cap a long lead produces a cone
    larger than the ocean, and a rule that fires on nothing is indistinguishable
    from one that is broken.
    """
    lead_h = max(0.0, valid_for - made_at) / 3600.0
    lat2, lon2 = _advance(lat, lon, cog_deg, sog_kn * 1852.0 * lead_h)

    grown = float(position_sigma_m) + CONE_GROWTH_M_PER_HOUR * lead_h
    physics_cap = MAX_FEASIBLE_SPEED_KN * 1852.0 * lead_h
    radius = min(grown, physics_cap) if lead_h > 0 else float(position_sigma_m)

    # Confidence decays with lead time on the same shape as the cone grows: at
    # one hour a dead-reckoned position is worth believing, at twelve it is a
    # guess with a large circle around it.
    conf = max(0.05, 1.0 / (1.0 + lead_h / 3.0))
    return Projection(
        track_id=track_id, track_source=track_source,
        made_at=made_at, valid_for=valid_for, lat=lat2, lon=lon2,
        radius_m=max(radius, 1.0), from_lat=lat, from_lon=lon,
        from_sog_kn=float(sog_kn), from_cog_deg=float(cog_deg),
        confidence=conf)


#: The lane tightness at which the calibrated cone is used unmodified.
#:
#: The flow field reports, per cell, the circular spread of the courses traffic
#: actually steers there. Twenty degrees is the middle of that distribution on
#: this corpus — a coastal corridor runs tighter, an open approach or a
#: junction runs wider — so it is the pivot, not a target. A cell tighter than
#: this earns a narrower cone because the road genuinely constrains where she
#: can be; a cell wider than this earns a broader one for the same reason
#: inverted.
REFERENCE_LANE_SPREAD_DEG = 20.0

#: The local speed variability at which the baseline leaves the cone alone.
#: Read as (p95 − p50) / p50 of speed in the cell: 0.5 means the fast tail runs
#: half again the local median, which is ordinary mixed coastal traffic.
REFERENCE_SPEED_SPREAD = 0.5

#: Bounds on the total modulation. A modulation that could run to zero would
#: produce a cone nothing can be inside; one that could run unbounded would
#: produce a cone nothing can be outside. Both are ways of not making a claim.
MODULATION_MIN, MODULATION_MAX = 0.6, 2.0


def _cone_modulation(spread_deg: Optional[float], baselines, lat: float,
                     lon: float, sog_kn: float) -> tuple[float, str]:
    """How much wider or narrower than the calibrated cone, here, and why.

    Two local facts, each three-valued, each defaulting to "no change" when it
    cannot be established:

    * **How tightly the road is channelled.** From the flow field's own course
      spread. ``None`` when no cell on the path had a supported prior.
    * **How variable local speed is.** From the per-area baseline
      (:mod:`maritime_isr.baselines`), whose ``is_unusual`` is deliberately
      three-valued. A cell below ``MIN_OBSERVATIONS`` returns no opinion, and
      **"no opinion" must not tighten the cone** — a cone quietly narrowed over
      an unmonitored patch of ocean manufactures a departure out of ignorance,
      which is the coverage-versus-silence confusion this project keeps
      finding in new clothes.
    """
    reasons: list[str] = []
    factor = 1.0

    if spread_deg is not None:
        lane = spread_deg / REFERENCE_LANE_SPREAD_DEG
        lane = max(0.6, min(1.8, lane))
        factor *= lane
        reasons.append(f"lane spread {spread_deg:.0f}° (x{lane:.2f})")
    else:
        reasons.append("lane spread not checkable (x1.00)")

    b = None
    if baselines is not None:
        try:
            b = baselines.at(lat, lon)
        except Exception:                                        # noqa: BLE001
            b = None
    if b is not None and getattr(b, "usable", False):
        p50 = b.percentile("sog_kn", 50)
        p95 = b.percentile("sog_kn", 95)
        if p50 is not None and p95 is not None and p50 > 0.5:
            spread = (p95 - p50) / p50
            loc = max(0.75, min(1.5, spread / REFERENCE_SPEED_SPREAD))
            factor *= loc
            reasons.append(
                f"local speed spread {spread:.2f} over {b.n_observations:,} "
                f"observations (x{loc:.2f})")
        else:
            reasons.append("local speed baseline not checkable (x1.00)")
    else:
        reasons.append("no usable area baseline here (x1.00)")

    return (max(MODULATION_MIN, min(MODULATION_MAX, factor)),
            "; ".join(reasons))


def project_route_aware(*, lat: float, lon: float, sog_kn: float,
                        cog_deg: float, made_at: float, valid_for: float,
                        model, vessel_type: Optional[str] = None,
                        hull: Optional[str] = None,
                        position_sigma_m: float = 0.0,
                        track_id: Optional[str] = None,
                        track_source: Optional[str] = None) -> Projection:
    """Project one state forward **along the road**, not along the heading.

    The route-aware twin of :func:`project_from`, and deliberately a separate
    function rather than a flag on that one: the dead-reckoned projection is
    what Phase 3 association and the imaging-opportunity layer already call,
    and a route prior silently changing their answers would be a behaviour
    change smuggled in under a refactor.

    Four conditioners, all from :mod:`tracks.route_prior`:

    * the **flow field** steers the path (``model.prior``),
    * the hull's **own previous passages** override it where she has any, and
      only ones that ended before ``made_at`` (``model.own``),
    * her **motion-inferred type** sets how far she makes good over the lead
      and how fast the cone opens (``model.calibration``),
    * the **per-area baseline** and the lane's own tightness modulate the cone
      (``model.baselines``), each three-valued and each leaving it alone when
      it has no opinion.

    The cone still grows with lead time and is still capped by
    ``MAX_FEASIBLE_SPEED_KN``, because Phase 3 gating reads the same contract.
    """
    from . import route_prior as rp

    lead_h = max(0.0, valid_for - made_at) / 3600.0
    calib = model.calibration_for(vessel_type)
    # How far along the modelled path she is predicted to have got. Applied the
    # same way in both arms — as a fraction of the walk, not as a shortened
    # heading — because that is exactly how `route_prior.calibrate` fitted it,
    # and a factor applied differently from how it was measured is a number
    # borrowed from a different experiment.
    factor = calib.factor_at(lead_h)
    reach_m = float(sog_kn) * 1852.0 * lead_h

    stepped = None
    path: list = []
    if lead_h > 0 and reach_m > 1.0 and (model.prior is not None
                                         or model.own is not None):
        stepped = rp.step_along_prior(
            lat=lat, lon=lon, cog_deg=cog_deg, sog_kn=float(sog_kn),
            lead_h=lead_h, model=model, hull=hull, made_at=made_at)

    # **A walk that came out `not_checkable` is dead-reckoned, not part-bent.**
    # Below `MIN_PRIOR_COVERAGE` most of the path had no road under it, and the
    # few cells that did had every right to bend her — a hull crossing a
    # corridor gets swung onto it by the two cells she clipped. Measured on the
    # synthetic corpus, keeping the part-bent path made the route arm *worse
    # than dead reckoning* off-lane (three-hour median 4.72 nm against 3.01),
    # which is a predictor doing harm in exactly the water it does not know.
    # The module already promised this fallback in its basis string; it now
    # does it. `not_checkable` means dead reckoning, all the way down.
    if stepped is not None and stepped.support != "not_checkable":
        lat2, lon2 = stepped.at_fraction(factor)
        path = rp.truncate_path(stepped, factor)
    else:
        lat2, lon2 = _advance(lat, lon, cog_deg, reach_m * factor)

    support = stepped.support if stepped is not None else "not_checkable"
    coverage = stepped.coverage if stepped is not None else 0.0
    # The lane spread modulates the cone only where the lane was followed. On
    # the fallback the walk was discarded, and sizing the cone off the
    # tightness of a road we did not take would narrow it on the strength of
    # evidence we just declined to use — the same one-sided error as tightening
    # a cone over an unwatched cell.
    spread = (stepped.mean_spread_deg
              if stepped is not None and support != "not_checkable" else None)
    modulation, why = _cone_modulation(spread, model.baselines, lat2, lon2,
                                       float(sog_kn))

    grown = (float(position_sigma_m)
             + calib.cone_growth_m_per_hour * lead_h * modulation)
    radius = (min(grown, rp.physics_cap_m(lead_h)) if lead_h > 0
              else float(position_sigma_m))
    radius = max(radius, rp.MIN_CONE_M if lead_h > 0 else 1.0)

    conf = max(0.05, 1.0 / (1.0 + lead_h / 3.0))
    # A route-aware projection with a real prior behind it deserves more
    # confidence than the same reach dead-reckoned, and one that fell back
    # deserves no more. Bounded well below 1.0: this is still a prediction.
    if support in ("fleet_prior", "own_history"):
        conf = min(0.95, conf * (1.0 + 0.25 * coverage))

    basis = {
        "own_history": (
            f"stepped along this hull's own previous passages where she had "
            f"them and the fleet flow field elsewhere ({coverage:.0%} of the "
            f"path had a prior), cone calibrated for "
            f"{vessel_type or 'unclassified'} and capped by physics"),
        "fleet_prior": (
            f"stepped along the learned fleet flow field ({coverage:.0%} of "
            f"the path had a prior), cone calibrated for "
            f"{vessel_type or 'unclassified'} and capped by physics"),
        "not_checkable": (
            f"no route prior for this water ({coverage:.0%} of the path had "
            f"one, under the {rp.MIN_PRIOR_COVERAGE:.0%} floor) — dead "
            f"reckoning with a cone calibrated for "
            f"{vessel_type or 'unclassified'}, capped by physics"),
    }[support] + f". Cone modulation: {why}."

    return Projection(
        track_id=track_id, track_source=track_source,
        made_at=made_at, valid_for=valid_for, lat=lat2, lon=lon2,
        radius_m=radius, from_lat=lat, from_lon=lon,
        from_sog_kn=float(sog_kn), from_cog_deg=float(cog_deg),
        confidence=conf, basis=basis, route_support=support,
        prior_coverage=coverage, cone_modulation=modulation,
        vessel_type=vessel_type, path=path)


def project(track, *, at: float, made_from: Optional[float] = None,
            model=None, vessel_type: Optional[str] = None,
            hull: Optional[str] = None) -> Optional[Projection]:
    """Project a built track forward to a moment.

    ``made_from`` selects which fix the projection is made from; the default is
    the last fix at or before ``at``, which is the honest one — projecting from
    a fix that has not happened yet would be using the answer to predict itself.
    Returns None when there is no such fix.

    ``model`` is an optional :class:`tracks.route_prior.PredictionModel`. With
    it the projection is route-aware; **without it the behaviour is exactly
    what it has always been**, which is what lets Phase 3 and the imaging
    layer keep calling this unchanged.
    """
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) == 0:
        return None
    t = epoch_s(pts["ts"])
    cutoff = at if made_from is None else made_from
    idx = int(np.searchsorted(t, cutoff, side="right")) - 1
    if idx < 0:
        return None

    row = pts.iloc[idx]
    sigma = float(row["sigma_m"]) if "sigma_m" in pts.columns else 0.0
    if not math.isfinite(sigma):
        sigma = 0.0
    common = dict(
        lat=float(row["lat"]), lon=float(row["lon"]),
        sog_kn=float(row["sog_kn"]), cog_deg=float(row["cog_deg"]),
        made_at=float(t[idx]), valid_for=float(at),
        position_sigma_m=sigma,
        track_id=getattr(track, "track_id", None),
        track_source=getattr(getattr(track, "source", None), "name", None))
    if model is None:
        return project_from(**common)
    return project_route_aware(
        model=model, vessel_type=vessel_type,
        hull=(hull if hull is not None
              else str(getattr(track, "track_key", None) or "")),
        **common)


def check_departure(projection: Projection, *, lat: float, lon: float,
                    at: float, margin: float = DEPARTURE_MARGIN
                    ) -> Optional[Departure]:
    """Is this observed position outside the cone that predicted it?

    Returns None when the vessel is where she was expected — which is the
    overwhelmingly common case and must stay cheap.
    """
    d = _hav_m(projection.lat, projection.lon, lat, lon)
    radii = d / max(projection.radius_m, 1.0)
    if radii <= margin:
        return None

    lead = projection.lead_hours
    # Confidence rises with how far outside she is and falls with how long the
    # projection had to be right for. Three radii outside a one-hour cone is
    # near-certain; the same three radii outside a twelve-hour cone is a lead.
    conf = min(0.95, (1.0 - 1.0 / radii) * projection.confidence * 1.6)
    return Departure(
        projection=projection, observed_lat=lat, observed_lon=lon,
        observed_at=at, distance_m=d, radii_outside=radii,
        confidence=max(0.0, conf),
        statement=(
            f"Projected {lead:.1f} h ahead from {projection.from_sog_kn:.1f} kn "
            f"on {projection.from_cog_deg:.0f}°, she should have been within "
            f"{projection.radius_m / 1852.0:.1f} nm of "
            f"{projection.lat:.2f}, {projection.lon:.2f}. She was "
            f"{d / 1852.0:.1f} nm away — {radii:.1f} times the cone. She "
            f"departed from her own predicted track."),
        detail={"lead_hours": round(lead, 2),
                "cone_radius_nm": round(projection.radius_m / 1852.0, 2),
                "observed_distance_nm": round(d / 1852.0, 2),
                "radii_outside": round(radii, 2)})


def departures_along(track, *, lead_hours: float = 3.0,
                     margin: float = DEPARTURE_MARGIN,
                     max_checks: int = 200,
                     min_run: int = 2,
                     require_steady_leg: bool = True,
                     model=None, vessel_type: Optional[str] = None,
                     hull: Optional[str] = None) -> list[Departure]:
    """Walk a track, projecting ahead and checking where she actually was.

    At each fix, project ``lead_hours`` forward and compare against the real
    position nearest that moment. This is the self-consistency test the brief
    asks for: the vessel is measured against **her own** predicted path, not
    against a fleet average, so it needs no baseline and works on a hull the
    system has never seen before.

    Two gates, and without them this rule is a false-positive machine.
    **Measured before they existed: 4,004 departures across 60 tracks** — one
    for essentially every course change in the corpus, because a merchant
    altering at a waypoint is 39 nm from where three hours of dead reckoning
    said she would be, every single time. A queue like that is the alert-fatigue
    failure ADR-004 exists to prevent.

    * ``require_steady_leg`` only projects from a fix where the vessel has been
      holding a course. Dead reckoning is the null hypothesis "she goes on doing
      what she is doing", and that hypothesis is *only* meaningful while she is
      doing something steady. Projecting from the middle of a turn predicts a
      position nobody expected, including the master.
    * ``min_run`` requires the vessel to be outside her cone on consecutive
      checks. One fix outside is an alteration; a sustained run outside is a
      change of plan. This is the same persistence discipline the radar cascade
      applies (``RADAR_DARK_MIN_EPOCHS``) and for the same reason.

    Both are honest about what they cost: a genuine one-off diversion that ends
    within one check window is no longer reported, and a departure that begins
    during a turn is missed. That is the precision-first trade, stated rather
    than absorbed.

    ``max_checks`` bounds the walk. A dense two-week track holds thousands of
    fixes and every one of them would otherwise be projected; the stride keeps
    the cost flat and the sampling even.

    ``model`` switches the walk to the route-aware predictor (ADR-042). The
    gates and the persistence rule are **identical** either way, on purpose:
    the two arms of the measurement have to differ in the predictor and in
    nothing else, or the comparison is between two experiments rather than two
    predictors.
    """
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) < 3:
        return []
    t = epoch_s(pts["ts"])
    lat = pts["lat"].to_numpy(dtype=float)
    lon = pts["lon"].to_numpy(dtype=float)
    cog = pts["cog_deg"].to_numpy(dtype=float)

    if lead_hours * 3600.0 < MIN_LEAD_MINUTES * 60.0:
        raise ValueError(
            f"lead_hours={lead_hours} is under the {MIN_LEAD_MINUTES:.0f}-minute "
            f"floor: below it the track's own position error dominates and any "
            f"'departure' is measurement noise, not behaviour.")
    if lead_hours > MAX_LEAD_HOURS:
        raise ValueError(
            f"lead_hours={lead_hours} exceeds the {MAX_LEAD_HOURS:.0f}-hour "
            f"ceiling: the cone is wider than anything a departure could be "
            f"outside of, so the answer would be 'no departure' by "
            f"construction rather than by evidence.")

    step = max(1, len(pts) // max_checks)
    lead_s = lead_hours * 3600.0
    checks: list[Optional[Departure]] = []
    for i in range(0, len(pts) - 1, step):
        target = t[i] + lead_s
        if target > t[-1]:
            break
        j = int(np.searchsorted(t, target, side="left"))
        j = min(j, len(t) - 1)
        # The nearest real fix to the projected moment. If the track is silent
        # across the whole lead window there is nothing to compare against, and
        # "she was not where predicted" would be a claim about a gap rather
        # than about a movement — which is the loitering-versus-silence
        # confusion this project keeps guarding against.
        if abs(t[j] - target) > lead_s * 0.5:
            checks.append(None)
            continue
        if require_steady_leg and not _on_steady_leg(cog, i):
            checks.append(None)
            continue
        p = project(track, at=float(t[j]), made_from=float(t[i]),
                    model=model, vessel_type=vessel_type, hull=hull)
        if p is None:
            checks.append(None)
            continue
        checks.append(check_departure(p, lat=float(lat[j]), lon=float(lon[j]),
                                      at=float(t[j]), margin=margin))

    # Keep only departures that persist across `min_run` consecutive checks.
    # The strongest of each run is reported, because an operator wants one
    # finding per divergence, not one per sample of it.
    out: list[Departure] = []
    run: list[Departure] = []
    for d in checks + [None]:
        if d is None:
            if len(run) >= min_run:
                out.append(max(run, key=lambda x: x.radii_outside))
            run = []
        else:
            run.append(d)
    return out


#: How steady a course must have been, over the fixes leading up to a
#: projection, for dead reckoning to be a meaningful null hypothesis.
STEADY_LEG_MAX_TURN_DEG = 20.0
STEADY_LEG_LOOKBACK = 3


def _on_steady_leg(cog: "np.ndarray", i: int) -> bool:
    """Was the vessel holding a course at fix `i`?

    Looks back rather than forward on purpose: looking forward would use the
    alteration we are trying to detect in order to decide whether to look for
    it, which is the answer-key mistake in miniature.
    """
    lo = max(0, i - STEADY_LEG_LOOKBACK)
    if i - lo < 2:
        return False
    return all(_turn_deg(cog[k], cog[k + 1]) <= STEADY_LEG_MAX_TURN_DEG
               for k in range(lo, i))


def _turn_deg(a: float, b: float) -> float:
    return abs((float(b) - float(a) + 180.0) % 360.0 - 180.0)


def strongest(departures: Sequence[Departure]) -> Optional[Departure]:
    """The departure most worth showing: furthest outside its own cone."""
    return max(departures, key=lambda d: d.radii_outside, default=None)
