"""track_generator — motion a hull could actually perform.

The whole value of this module is that it **integrates** rather than
interpolates. A track is produced by stepping a state (position, course, speed)
forward at a fixed timestep under limits the vessel class actually has:

  * speed changes at no more than `accel_kn_per_min` — a loaded VLCC takes the
    better part of an hour to come off service speed, a dhow takes a minute;
  * course changes at no more than `rot_deg_per_s`, so a 300 m tanker sweeps a
    turn instead of hinging;
  * position advances along a **great circle** on the current course.

Interpolating between waypoints instead would produce instantaneous course
changes at every corner and speed discontinuities at every leg boundary. Those
are exactly the artefacts a physics validator flags — and, worse, exactly the
artefacts that would let a classifier separate synthetic tracks from real ones
on something other than behaviour, which would make every precision number
measured against this corpus meaningless.

**Legs are the vocabulary.** A scenario says "transit to Sikka at service speed,
then hold station here for eleven hours, then depart"; the generator turns that
into second-by-second physics. `LegKind` is deliberately small — transit,
station-keep, drift, fishing, moored — because every behaviour in the catalogue
composes from those five.

The internal timestep is 60 s. Finer buys nothing: AIS reports land minutes
apart and position noise dominates below that. Coarser starts to matter for the
turn-rate limit on small fast craft.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..geography import (angular_diff_deg, destination, haversine_m,
                         initial_bearing_deg)

#: Integration timestep.
STEP_S = 60.0

M_PER_NM = 1852.0

# AIS navigational status codes (ITU-R M.1371 table 45).
NAV_UNDERWAY_ENGINE = 0
NAV_AT_ANCHOR = 1
NAV_MOORED = 5
NAV_FISHING = 7


@dataclass
class TrackPoint:
    """One integrated state. Not an AIS report — the emitter makes those."""
    t: datetime
    lat: float
    lon: float
    sog_kn: float
    cog_deg: float
    nav_status: int


@dataclass
class Leg:
    """One instruction in a voyage plan.

    kind:
      transit  — steer for `target` at `speed_kn` (defaults to service speed)
      station  — hold position near `target` for `duration_h` (see below)
      drift    — engine off, carried by current at `speed_kn`, heading wandering
      fishing  — working pattern: slow, frequent course changes, inside a radius
      moored   — alongside, speed zero, nav status MOORED
    """
    kind: str
    target: tuple[float, float] | None = None
    speed_kn: float | None = None
    duration_h: float | None = None
    radius_m: float = 500.0
    #: Arrive at the target no earlier than this. Lets a scenario synchronise a
    #: rendezvous without hand-computing departure times.
    not_before: datetime | None = None


@dataclass
class VoyagePlan:
    start: tuple[float, float]
    start_time: datetime
    legs: list[Leg] = field(default_factory=list)
    initial_course_deg: float | None = None
    #: Speed the vessel is already making when the plan begins.
    #:
    #: **Defaulting this to zero was a real physics error.** A vessel finishing
    #: one leg at 7 knots and beginning the next does not stop dead in between,
    #: but every plan started a fresh integrator at rest — so each leg boundary
    #: showed a speed collapsing to 0.14 kn and a course snapping to the new
    #: initial bearing inside a single 60 s step. That produced turn rates of
    #: 2.75 deg/s on a hull limited to 0.25, and it is exactly the kind of
    #: artefact a classifier would learn instead of behaviour. Any plan that
    #: continues from a previous track must pass the previous point's speed.
    initial_sog_kn: float = 0.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


class _State:
    """Integrator state. Kept as a class so legs can hand off cleanly."""

    def __init__(self, lat, lon, cog, sog, t):
        self.lat, self.lon, self.cog, self.sog, self.t = lat, lon, cog, sog, t

    def step_toward(self, vessel, desired_cog: float, desired_sog: float,
                    dt_s: float = STEP_S) -> None:
        """Advance one timestep under the class's turn and acceleration limits."""
        # --- course: turn at most rot_deg_per_s toward the desired heading ---
        max_turn = vessel.rot_deg_per_s * dt_s
        delta = angular_diff_deg(self.cog, desired_cog)
        self.cog = (self.cog + _clamp(delta, -max_turn, max_turn)) % 360.0

        # --- speed: change at most accel_kn_per_min ---
        max_dv = vessel.accel_kn_per_min * (dt_s / 60.0)
        dv = _clamp(desired_sog - self.sog, -max_dv, max_dv)
        self.sog = max(0.0, min(vessel.max_kn, self.sog + dv))

        # --- position: great-circle advance on the current course ---
        dist_m = self.sog * M_PER_NM * (dt_s / 3600.0)
        if dist_m > 0.0:
            self.lat, self.lon = destination(self.lat, self.lon, self.cog, dist_m)
        self.t = self.t + timedelta(seconds=dt_s)


def generate_track(vessel, plan: VoyagePlan, rng, *,
                   max_hours: float = 24 * 70) -> list[TrackPoint]:
    """Integrate a voyage plan into a second-by-minute track.

    Returns one `TrackPoint` per timestep. The AIS emitter decimates this into
    reports; the SAR/scene side, when a scenario needs one, samples the same
    ground truth. Both therefore describe **one** physical vessel, which is what
    makes a spoof scenario (C2, A3) a genuine contradiction rather than two
    unrelated fabrications.
    """
    st = _State(plan.start[0], plan.start[1],
                plan.initial_course_deg if plan.initial_course_deg is not None
                else 0.0,
                max(0.0, min(plan.initial_sog_kn, vessel.max_kn)),
                plan.start_time)
    out: list[TrackPoint] = []
    budget_steps = int(max_hours * 3600 / STEP_S)

    def record(nav: int) -> None:
        out.append(TrackPoint(st.t, st.lat, st.lon, st.sog, st.cog, nav))

    for leg in plan.legs:
        if len(out) > budget_steps:
            break
        kind = leg.kind

        if kind == "transit":
            if leg.target is None:
                raise ValueError("transit leg needs a target")
            target_speed = leg.speed_kn if leg.speed_kn is not None else vessel.service_kn
            tlat, tlon = leg.target
            # Steer for the target until within one timestep's run of it. The
            # arrival tolerance scales with speed so a fast vessel does not
            # circle a waypoint it overshoots.
            guard = 0
            while guard < budget_steps:
                guard += 1
                d = haversine_m(st.lat, st.lon, tlat, tlon)
                tol = max(200.0, st.sog * M_PER_NM * (STEP_S / 3600.0) * 1.2)
                if d <= tol:
                    break
                brg = initial_bearing_deg(st.lat, st.lon, tlat, tlon)
                # Slow down for arrival: inside the deceleration distance the
                # desired speed tapers, so the hull is not still doing 15 kn
                # when it reaches a rendezvous point.
                decel_dist = (st.sog ** 2 / max(vessel.accel_kn_per_min, 1e-6)) * 12.0
                want = target_speed
                if d < decel_dist:
                    want = max(1.0, target_speed * (d / max(decel_dist, 1.0)))
                st.step_toward(vessel, brg, want)
                record(NAV_UNDERWAY_ENGINE)
                if len(out) > budget_steps:
                    break
            # Honour a synchronisation constraint by holding on station rather
            # than by teleporting: a vessel that arrives early waits.
            if leg.not_before is not None and st.t < leg.not_before:
                hold_h = (leg.not_before - st.t).total_seconds() / 3600.0
                _station_keep(vessel, st, rng, hold_h, leg.radius_m, out,
                              budget_steps)

        elif kind == "station":
            _station_keep(vessel, st, rng, leg.duration_h or 1.0,
                          leg.radius_m, out, budget_steps)

        elif kind == "drift":
            # Engine off. Speed is the current, course wanders slowly and
            # coherently rather than randomly — a drifting hull swings, it does
            # not jitter.
            n = int((leg.duration_h or 1.0) * 3600 / STEP_S)
            set_speed = leg.speed_kn if leg.speed_kn is not None else 0.6
            drift_cog = st.cog
            for _ in range(n):
                drift_cog = (drift_cog + rng.gauss(0, 1.2)) % 360.0
                st.step_toward(vessel, drift_cog, set_speed)
                record(NAV_UNDERWAY_ENGINE)
                if len(out) > budget_steps:
                    break

        elif kind == "fishing":
            # A working pattern: slow, with real course changes every 20-60 min,
            # constrained to stay inside `radius_m` of the ground.
            centre = leg.target or (st.lat, st.lon)
            n = int((leg.duration_h or 1.0) * 3600 / STEP_S)
            work_speed = leg.speed_kn if leg.speed_kn is not None else 3.2
            hdg = st.cog
            since_turn = 0
            for _ in range(n):
                since_turn += 1
                if since_turn * STEP_S > rng.uniform(1200, 3600):
                    hdg = (hdg + rng.uniform(60, 180) * rng.choice((-1, 1))) % 360.0
                    since_turn = 0
                # Herd back toward the ground if we have wandered out.
                if haversine_m(st.lat, st.lon, *centre) > leg.radius_m:
                    hdg = initial_bearing_deg(st.lat, st.lon, *centre)
                    since_turn = 0
                st.step_toward(vessel, hdg, work_speed * rng.uniform(0.85, 1.15))
                record(NAV_FISHING)
                if len(out) > budget_steps:
                    break

        elif kind == "moored":
            n = int((leg.duration_h or 1.0) * 3600 / STEP_S)
            for _ in range(n):
                st.step_toward(vessel, st.cog, 0.0)
                record(NAV_MOORED)
                if len(out) > budget_steps:
                    break

        else:
            raise ValueError(f"unknown leg kind {kind!r}")

    return out


def _station_keep(vessel, st: _State, rng, duration_h: float, radius_m: float,
                  out: list[TrackPoint], budget_steps: int) -> None:
    """Hold position: slow, wandering, never quite still.

    Station-keeping is not zero speed. A hull holding position against wind and
    current works its engine in short bursts and swings around a point, which
    shows up as 0.1-1.2 kn with a heading that rotates through most of the
    compass over a few hours. Generating it as an exactly-stationary point would
    be trivially separable from real loitering, and would also defeat the
    loitering detector, which looks for sustained *low* speed rather than none.
    """
    n = int(duration_h * 3600 / STEP_S)
    anchor = (st.lat, st.lon)
    hdg = st.cog
    for _ in range(n):
        hdg = (hdg + rng.gauss(0, 2.5)) % 360.0
        speed = abs(rng.gauss(0.45, 0.30))
        # Drifted too far off station: come back. This is what keeps the
        # episode inside the radius without pinning it to a point.
        if haversine_m(st.lat, st.lon, *anchor) > radius_m:
            hdg = initial_bearing_deg(st.lat, st.lon, *anchor)
            speed = max(speed, 1.4)
        st.step_toward(vessel, hdg, min(speed, 2.0))
        out.append(TrackPoint(st.t, st.lat, st.lon, st.sog, st.cog,
                              NAV_AT_ANCHOR if radius_m <= 900 else
                              NAV_UNDERWAY_ENGINE))
        if len(out) > budget_steps:
            return


# --------------------------------------------------------------------------
# analysis helpers, shared with the validator
# --------------------------------------------------------------------------

def implied_speed_kn(a: TrackPoint, b: TrackPoint) -> float:
    dt_s = (b.t - a.t).total_seconds()
    if dt_s <= 0:
        return 0.0
    return haversine_m(a.lat, a.lon, b.lat, b.lon) / dt_s * 3600.0 / M_PER_NM


def max_turn_rate_deg_s(points: list[TrackPoint]) -> float:
    worst = 0.0
    for a, b in zip(points, points[1:]):
        dt = (b.t - a.t).total_seconds()
        if dt <= 0:
            continue
        worst = max(worst, abs(angular_diff_deg(a.cog_deg, b.cog_deg)) / dt)
    return worst


def track_bounds(points: list[TrackPoint]) -> tuple[float, float, float, float]:
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    return min(lats), max(lats), min(lons), max(lons)


def point_at(points: list[TrackPoint], t: datetime) -> TrackPoint | None:
    """The integrated state nearest `t`. Used to place SAR contacts on truth."""
    if not points:
        return None
    best, best_dt = None, None
    for p in points:
        dt = abs((p.t - t).total_seconds())
        if best_dt is None or dt < best_dt:
            best, best_dt = p, dt
    return best


def great_circle_waypoints(start: tuple[float, float], end: tuple[float, float],
                           n: int = 3) -> list[tuple[float, float]]:
    """Intermediate waypoints along the great circle — for legs that need shape."""
    from ..geography import interpolate
    return [interpolate(*start, *end, (i + 1) / (n + 1)) for i in range(n)]


def bearing_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    return initial_bearing_deg(*a, *b)


def offset_position(lat: float, lon: float, bearing_deg: float,
                    distance_m: float) -> tuple[float, float]:
    return destination(lat, lon, bearing_deg, distance_m)


def course_to_compass(cog: float) -> str:
    dirs = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return dirs[int((cog % 360) / 22.5 + 0.5) % 16]


def total_distance_nm(points: list[TrackPoint]) -> float:
    return sum(haversine_m(a.lat, a.lon, b.lat, b.lon)
               for a, b in zip(points, points[1:])) / M_PER_NM


def duration_hours(points: list[TrackPoint]) -> float:
    if len(points) < 2:
        return 0.0
    return (points[-1].t - points[0].t).total_seconds() / 3600.0


def mean_speed_kn(points: list[TrackPoint]) -> float:
    if not points:
        return 0.0
    return sum(p.sog_kn for p in points) / len(points)


def sanity_check(vessel, points: list[TrackPoint]) -> list[str]:
    """Problems a track has with its own vessel's physics. Empty list is good."""
    problems: list[str] = []
    if not points:
        return ["track is empty"]
    over = [p for p in points if p.sog_kn > vessel.max_kn + 0.5]
    if over:
        problems.append(
            f"{len(over)} point(s) above class max {vessel.max_kn} kn "
            f"(worst {max(p.sog_kn for p in over):.1f})")
    rot = max_turn_rate_deg_s(points)
    if rot > vessel.rot_deg_per_s * 1.5 + 1e-6:
        problems.append(f"turn rate {rot:.2f} deg/s exceeds class limit "
                        f"{vessel.rot_deg_per_s}")
    for a, b in zip(points, points[1:]):
        if (b.t - a.t).total_seconds() <= 0:
            problems.append("non-monotonic time in track")
            break
    return problems


def haversine_nm(lat1, lon1, lat2, lon2) -> float:
    return haversine_m(lat1, lon1, lat2, lon2) / M_PER_NM


def heading_hold(points: list[TrackPoint], window: int = 30) -> float:
    """Fraction of the track where heading is essentially constant.

    C1's phantom track is detectable partly because a replayed segment holds an
    implausibly exact heading; this is the measurement that makes that claim
    checkable rather than asserted.
    """
    if len(points) < window + 1:
        return 0.0
    held = 0
    for i in range(len(points) - window):
        seg = points[i:i + window]
        spread = max(abs(angular_diff_deg(seg[0].cog_deg, p.cog_deg))
                     for p in seg)
        if spread < 0.5:
            held += 1
    return held / max(1, len(points) - window)


def resample(points: list[TrackPoint], every_s: float) -> list[TrackPoint]:
    """Thin a track to roughly one point per `every_s`, keeping the endpoints."""
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if (p.t - out[-1].t).total_seconds() >= every_s:
            out.append(p)
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def clip_to_window(points: list[TrackPoint], t0: datetime,
                   t1: datetime) -> list[TrackPoint]:
    return [p for p in points if t0 <= p.t <= t1]


def shift(points: list[TrackPoint], delta: timedelta) -> list[TrackPoint]:
    return [TrackPoint(p.t + delta, p.lat, p.lon, p.sog_kn, p.cog_deg,
                       p.nav_status) for p in points]


def displaced(points: list[TrackPoint], bearing_deg: float,
              distance_m: float) -> list[TrackPoint]:
    """Rigidly displace a whole track — the GPS-interference decoy (C4).

    Every vessel in an interference cluster is displaced by the *same* offset,
    because that is what regional interference does. Displacing each vessel
    independently would look like twelve unrelated spoofers, which is precisely
    the wrong conclusion the decoy exists to test for.
    """
    out = []
    for p in points:
        la, lo = destination(p.lat, p.lon, bearing_deg, distance_m)
        out.append(TrackPoint(p.t, la, lo, p.sog_kn, p.cog_deg, p.nav_status))
    return out


def circle_track(vessel, centre: tuple[float, float], radius_m: float,
                 t0: datetime, revolutions: float, speed_kn: float
                 ) -> list[TrackPoint]:
    """A geometrically exact circle — deliberately unnatural, for C1.

    Real vessels do not hold a perfect radius for hours; wind and current see to
    that. This generator is the one place that produces motion no real hull
    would, and it exists so C1 has something a plausibility check can catch.
    The scenario declares it in `scenario_truth`, so it is a labelled artefact
    rather than a bug.
    """
    circumference = 2 * math.pi * radius_m
    total_m = circumference * revolutions
    speed_ms = speed_kn * M_PER_NM / 3600.0
    n = max(2, int(total_m / max(speed_ms * STEP_S, 1.0)))
    out = []
    for i in range(n):
        frac = i / n * revolutions
        ang = (frac * 360.0) % 360.0
        la, lo = destination(centre[0], centre[1], ang, radius_m)
        cog = (ang + 90.0) % 360.0
        out.append(TrackPoint(t0 + timedelta(seconds=i * STEP_S), la, lo,
                              speed_kn, cog, NAV_UNDERWAY_ENGINE))
    return out
