"""encounter_primitive — two hulls meet, work, and part.

**One function builds every rendezvous in the catalogue.** An illicit
ship-to-ship transfer in the deep basin and a legitimate bunkering at a
designated anchorage come out of `build_rendezvous` with the same code, the same
integrator and the same noise model. Only the parameters differ: where, how
long, how close, and who.

That is not a convenience, it is the measurement's foundation. If illicit
transfers were generated carefully and legitimate ones sloppily, any detector
would separate them on the sloppiness — and the precision figure that came out
would be a measurement of the generator, not of the system. The
decoy-separability test exists to keep this honest, and it can only pass because
this module has no idea which kind of meeting it is building.

**How the geometry is guaranteed coherent.** The receiving vessel is not
integrated independently and hoped to arrive in the right place. Vessel A is
integrated normally; during the transfer window vessel B is placed *alongside*
A — offset perpendicular to A's course by the working separation — and inherits
A's course and speed. Two hulls made fast to each other move as one body, which
is what alongside means, so B's motion is A's motion translated and is therefore
exactly as physical as A's. B's own approach and departure are integrated
normally.

The alternative — integrating both and letting them converge — produces
separations that miss by hundreds of metres and transfer windows that drift out
of sync, which no amount of parameter tuning fixes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..geography import destination, haversine_m, initial_bearing_deg
from ..searoute import seaward_point
from .track import (NAV_UNDERWAY_ENGINE, STEP_S, Leg, TrackPoint, VoyagePlan,
                    generate_track, shift)


@dataclass
class RendezvousSpec:
    """What actually happened, geometrically. Feeds the landed encounter row.

    These are measurements taken from the generated tracks, not the parameters
    asked for — so if the geometry came out different from the request, the
    landed row says what happened rather than what was intended.
    """
    lat: float
    lon: float
    t_start: datetime
    t_end: datetime
    duration_h: float
    min_separation_m: float
    mean_separation_m: float
    mean_sog_kn: float
    max_closing_speed_kn: float


def build_rendezvous(vessel_a, vessel_b, *, meet_point: tuple[float, float],
                     t_meet: datetime, duration_h: float,
                     separation_m: float, rng,
                     approach_from_a: float = 250.0,
                     approach_from_b: float = 70.0,
                     approach_nm: float = 55.0,
                     depart_to_a: float | None = None,
                     depart_to_b: float | None = None,
                     depart_nm: float = 55.0,
                     working_speed_kn: float = 0.8,
                     underway: bool = False,
                     ) -> tuple[list[TrackPoint], list[TrackPoint], RendezvousSpec]:
    """Build both sides of a meeting.

    `approach_from_a` / `approach_from_b` are the bearings the two vessels come
    *from*, so they arrive on different headings — two hulls approaching on the
    same bearing would be a following pair, not a rendezvous.

    `underway` selects a transfer conducted while making way (4-6 kn, common for
    larger parcels and rougher water) rather than drifting. Both are real
    practice; having both in the corpus stops "slow" from becoming a synonym for
    "transfer".
    """
    sep = max(separation_m, vessel_a.min_separation_m(),
              vessel_b.min_separation_m())

    # ---- A: approach, work, depart -------------------------------------
    start_a = seaward_point(meet_point, approach_from_a,
                            approach_nm * 1852.0)
    transfer_speed = (rng.uniform(4.0, 5.5) if underway
                      else max(0.2, working_speed_kn))

    plan_a = VoyagePlan(
        start=start_a, start_time=t_meet - timedelta(
            hours=approach_nm / max(vessel_a.service_kn, 1.0) * 1.35),
        initial_course_deg=initial_bearing_deg(*start_a, *meet_point),
        legs=[
            Leg("transit", target=meet_point, speed_kn=vessel_a.service_kn,
                not_before=t_meet),
        ],
    )
    pts_a = generate_track(vessel_a, plan_a, rng)

    # The working phase, integrated from wherever the approach actually ended.
    if not pts_a:
        raise ValueError("approach produced no track for vessel A")
    last = pts_a[-1]
    work_plan = VoyagePlan(
        start=(last.lat, last.lon), start_time=last.t,
        initial_course_deg=last.cog_deg, initial_sog_kn=last.sog_kn,
        legs=[Leg("drift", duration_h=duration_h, speed_kn=transfer_speed)]
        if not underway else
        [Leg("transit",
             target=destination(last.lat, last.lon, last.cog_deg,
                                transfer_speed * 1852.0 * duration_h),
             speed_kn=transfer_speed)],
    )
    work_a = generate_track(vessel_a, work_plan, rng)
    pts_a += work_a

    if work_a:
        end = work_a[-1]
        depart_brg = (depart_to_a if depart_to_a is not None
                      else (approach_from_a + 180.0) % 360.0)
        dep_plan = VoyagePlan(
            start=(end.lat, end.lon), start_time=end.t,
            initial_course_deg=end.cog_deg, initial_sog_kn=end.sog_kn,
            # Steam out on the departure heading as far as there is sea,
            # rather than dead-reckoning the full distance onto whatever the
            # bearing happens to point at — from an anchorage inside the Gulf of
            # Kutch the reciprocal of the approach points at the Kachchh shore.
            legs=[Leg("transit",
                      target=seaward_point((end.lat, end.lon), depart_brg,
                                           depart_nm * 1852.0),
                      speed_kn=vessel_a.service_kn)],
        )
        pts_a += generate_track(vessel_a, dep_plan, rng)

    if not work_a:
        raise ValueError("transfer window produced no track")

    t0, t1 = work_a[0].t, work_a[-1].t

    # ---- B: approach to the alongside position, shadow A, depart --------
    # Where B must be when the transfer starts: alongside A, offset
    # perpendicular to A's course.
    side = rng.choice((90.0, -90.0))
    first = work_a[0]
    b_start_pos = destination(first.lat, first.lon,
                              (first.cog_deg + side) % 360.0, sep)
    start_b = seaward_point(b_start_pos, approach_from_b,
                            approach_nm * 1852.0)
    plan_b = VoyagePlan(
        start=start_b, start_time=t0 - timedelta(
            hours=approach_nm / max(vessel_b.service_kn, 1.0) * 1.35),
        initial_course_deg=initial_bearing_deg(*start_b, *b_start_pos),
        legs=[Leg("transit", target=b_start_pos, speed_kn=vessel_b.service_kn)],
    )
    pts_b = generate_track(vessel_b, plan_b, rng)

    # **Put B's approach on A's clock.** The two vessels' plans start at
    # different fractional offsets — the offsets are derived from their own
    # service speeds — so their 60 s grids do not line up. The joint between
    # B's last approach point and her first alongside point (which inherits A's
    # timestamp) then fell half a second apart, and a normal course change
    # across half a second reads as 334 deg/s.
    #
    # Shifting the whole approach is physically neutral: same motion, same
    # speeds, same geometry, different clock. It is not a fudge for a bad
    # trajectory, it is the correct way to synchronise two independently
    # integrated tracks that have to meet.
    if pts_b:
        pts_b = shift(pts_b, (t0 - timedelta(seconds=STEP_S)) - pts_b[-1].t)

    # Alongside: B is A translated. Separation breathes a little — hulls work
    # against their fenders — but **never closes below `sep`**, which is already
    # the larger of the two hulls' beam-derived floors. Fenders compress; hulls
    # do not interpenetrate. Letting the noise wander below the floor produced
    # exactly that on the smallest hulls in the cast (two 22 m dhows at 14 m
    # centre-to-centre), so the breathing is one-sided by construction rather
    # than caught later by a tolerance.
    shadow: list[TrackPoint] = []
    cur_sep = sep
    # B's *reported* course has to turn into line with A's rather than snap to
    # it. Coming alongside, a vessel matches course over a minute or two; a
    # generator that simply copied A's heading produced a single step of up to
    # 343 deg/s at the joint — physically impossible, and a free giveaway to
    # any classifier. Position still comes from A (that is the geometric
    # guarantee); only the heading is rate-limited, using B's own turn rate.
    cur_cog = pts_b[-1].cog_deg if pts_b else work_a[0].cog_deg
    max_turn_per_step = vessel_b.rot_deg_per_s * 60.0
    for p in work_a:
        cur_sep = max(sep, min(sep * 1.30, cur_sep + rng.gauss(0.0, 1.2)))
        delta = (p.cog_deg - cur_cog + 540.0) % 360.0 - 180.0
        cur_cog = (cur_cog + max(-max_turn_per_step,
                                 min(max_turn_per_step, delta))) % 360.0
        la, lo = destination(p.lat, p.lon, (p.cog_deg + side) % 360.0, cur_sep)
        shadow.append(TrackPoint(p.t, la, lo, p.sog_kn, cur_cog,
                                 NAV_UNDERWAY_ENGINE))
    pts_b += shadow

    if shadow:
        end_b = shadow[-1]
        depart_brg_b = (depart_to_b if depart_to_b is not None
                        else (approach_from_b + 180.0) % 360.0)
        dep_plan_b = VoyagePlan(
            start=(end_b.lat, end_b.lon), start_time=end_b.t,
            initial_course_deg=end_b.cog_deg, initial_sog_kn=end_b.sog_kn,
            legs=[Leg("transit",
                      target=seaward_point((end_b.lat, end_b.lon), depart_brg_b,
                                           depart_nm * 1852.0),
                      speed_kn=vessel_b.service_kn)],
        )
        pts_b += generate_track(vessel_b, dep_plan_b, rng)

    spec = measure_rendezvous(pts_a, pts_b, t0, t1)
    return pts_a, pts_b, spec


def measure_rendezvous(pts_a: list[TrackPoint], pts_b: list[TrackPoint],
                       t0: datetime, t1: datetime) -> RendezvousSpec:
    """Measure the geometry that actually resulted. Never trusts the request."""
    by_t_b = {p.t: p for p in pts_b}
    seps: list[float] = []
    sogs: list[float] = []
    lats: list[float] = []
    lons: list[float] = []
    for a in pts_a:
        if not (t0 <= a.t <= t1):
            continue
        b = by_t_b.get(a.t)
        if b is None:
            continue
        seps.append(haversine_m(a.lat, a.lon, b.lat, b.lon))
        sogs.append((a.sog_kn + b.sog_kn) / 2.0)
        lats.append((a.lat + b.lat) / 2.0)
        lons.append((a.lon + b.lon) / 2.0)

    if not seps:
        raise ValueError("rendezvous produced no overlapping samples — the two "
                         "tracks are not co-timed")

    # Closing speed: how fast the separation changed, in knots. During a proper
    # alongside period this is near zero; a large value means the "transfer"
    # was actually a crossing.
    closing = 0.0
    for i in range(1, len(seps)):
        d_sep = abs(seps[i] - seps[i - 1])
        closing = max(closing, d_sep / 60.0 * 3600.0 / 1852.0)

    return RendezvousSpec(
        lat=sum(lats) / len(lats), lon=sum(lons) / len(lons),
        t_start=t0, t_end=t1,
        duration_h=(t1 - t0).total_seconds() / 3600.0,
        min_separation_m=min(seps),
        mean_separation_m=sum(seps) / len(seps),
        mean_sog_kn=sum(sogs) / len(sogs),
        max_closing_speed_kn=closing,
    )


def coherent(spec: RendezvousSpec, vessel_a, vessel_b, *,
             max_mean_sep_m: float = 400.0,
             max_closing_kn: float = 3.0) -> list[str]:
    """Is this geometry physically sensible? Empty list means yes.

    Checked by the validator over every generated rendezvous, decoys included.
    A meeting whose hulls overlap, or whose separation swings by hundreds of
    metres a minute, is not a transfer — and shipping a corpus containing one
    would put a physically impossible event in the same table as our findings.
    """
    problems = []
    floor = max(vessel_a.min_separation_m(), vessel_b.min_separation_m())
    if spec.min_separation_m < floor * 0.9:
        problems.append(
            f"hulls closer than their beams allow: "
            f"{spec.min_separation_m:.0f} m < {floor:.0f} m")
    if spec.mean_separation_m > max_mean_sep_m:
        problems.append(
            f"mean separation {spec.mean_separation_m:.0f} m is too wide to be "
            f"a transfer (limit {max_mean_sep_m:.0f} m)")
    if spec.max_closing_speed_kn > max_closing_kn:
        problems.append(
            f"separation changing at {spec.max_closing_speed_kn:.1f} kn — a "
            f"crossing, not a rendezvous")
    if spec.duration_h <= 0:
        problems.append("non-positive duration")
    return problems


def near_miss(vessel_a, vessel_b, *, cross_point: tuple[float, float],
              t_cross: datetime, rng, speed_kn: float = 12.0,
              closest_m: float = 380.0
              ) -> tuple[list[TrackPoint], list[TrackPoint]]:
    """Two vessels pass close at speed without meeting.

    The negative control for the encounter detector: inside the 500 m radius,
    but crossing at 12 knots and never slowing. A detector that fires on this is
    measuring proximity rather than rendezvous.
    """
    leg_nm = 30.0
    a_from = destination(*cross_point, 270.0, leg_nm * 1852.0)
    b_from = destination(*cross_point, 180.0, leg_nm * 1852.0)
    # Offset B's crossing point so the tracks miss by `closest_m`.
    b_target = destination(*cross_point, 0.0, closest_m)

    t_start = t_cross - timedelta(hours=leg_nm / speed_kn)
    pa = generate_track(vessel_a, VoyagePlan(
        start=a_from, start_time=t_start,
        initial_course_deg=initial_bearing_deg(*a_from, *cross_point),
        legs=[Leg("transit", target=destination(*cross_point, 90.0,
                                                leg_nm * 1852.0),
                  speed_kn=speed_kn)]), rng)
    pb = generate_track(vessel_b, VoyagePlan(
        start=b_from, start_time=t_start,
        initial_course_deg=initial_bearing_deg(*b_from, *b_target),
        legs=[Leg("transit", target=destination(*b_target, 0.0,
                                                leg_nm * 1852.0),
                  speed_kn=speed_kn)]), rng)
    return pa, pb
