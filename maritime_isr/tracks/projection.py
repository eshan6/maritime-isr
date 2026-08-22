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
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..config import MAX_FEASIBLE_SPEED_KN, PIPELINE_VERSION
from .kalman import epoch_s

__all__ = ["Projection", "Departure", "project", "project_from",
           "check_departure", "departures_along"]

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


def project(track, *, at: float, made_from: Optional[float] = None
            ) -> Optional[Projection]:
    """Project a built track forward to a moment.

    ``made_from`` selects which fix the projection is made from; the default is
    the last fix at or before ``at``, which is the honest one — projecting from
    a fix that has not happened yet would be using the answer to predict itself.
    Returns None when there is no such fix.
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
    return project_from(
        lat=float(row["lat"]), lon=float(row["lon"]),
        sog_kn=float(row["sog_kn"]), cog_deg=float(row["cog_deg"]),
        made_at=float(t[idx]), valid_for=float(at),
        position_sigma_m=sigma,
        track_id=getattr(track, "track_id", None),
        track_source=getattr(getattr(track, "source", None), "name", None))


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
                     require_steady_leg: bool = True) -> list[Departure]:
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
        p = project(track, at=float(t[j]), made_from=float(t[i]))
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
