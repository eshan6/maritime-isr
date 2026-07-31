"""ais_emitter — turn integrated motion into position reports someone heard.

Two things happen between a ship moving and a row landing in our store, and
conflating them is how synthetic AIS ends up unrealistic:

**1. The ship transmits on a schedule set by its behaviour.** ITU-R M.1371 fixes
the Class A reporting interval by speed and navigational status — 3 minutes at
anchor, 10 seconds under way below 14 knots, 6 seconds above, and roughly triple
that rate while changing course. Those are *transmit* intervals.

**2. A receiver hears some fraction of those transmissions.** Terrestrial AIS
range is limited and contended; satellite passes are intermittent. What lands in
a corpus is far sparser than what was sent, and the sparsity is a property of the
receiver geometry, not of the ship.

This module models both stages separately, which matters because the two produce
*different* patterns. A ship at anchor genuinely transmits slowly; a ship 400 nm
offshore transmits quickly and is heard rarely. Collapsing both into "a report
every N minutes" would erase the distinction that the coverage model and the gap
classifier exist to make — and would make the receiver-shadow decoy
indistinguishable from a real dark period by construction, which would rig the
very measurement this corpus is built to produce.

**The gap primitive's contract is enforced here.** When emission is suppressed,
nothing about *why* enters the emitted data. An intentional shutdown, an
equipment failure and a receiver shadow all produce the same thing: an absence.
The cause lives only in `scenario_truth`. If the emitted rows carried a reason
the detection code could reach, every gap scenario would be answering a question
it had been handed the answer to.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..geography import destination, receiver_coverage
from .track import (NAV_AT_ANCHOR, NAV_FISHING, NAV_MOORED, TrackPoint)

#: GPS position error, metres, 1-sigma. Modern receivers do better than this in
#: the open, but AIS positions carry the ship's own GNSS solution plus antenna
#: offset and reporting quantisation, and ~15 m is the honest working figure.
POS_NOISE_SIGMA_M = 15.0

#: Fraction of reports that carry a much larger error — multipath, a brief loss
#: of differential correction, a stale fix. Real AIS has these; a corpus with
#: perfectly Gaussian error is separable from real data on the tails alone.
POS_OUTLIER_RATE = 0.004
POS_OUTLIER_SIGMA_M = 220.0

#: Speed and course reporting quantisation, per M.1371 (0.1 kn, 0.1 deg).
SOG_QUANT = 0.1
COG_QUANT = 0.1


@dataclass
class AisReport:
    """One landed position report."""
    t: datetime
    lat: float
    lon: float
    sog_kn: float
    cog_deg: float
    heading_deg: float
    nav_status: int
    receiver: str


def transmit_interval_s(sog_kn: float, nav_status: int,
                        changing_course: bool) -> float:
    """ITU-R M.1371 Class A reporting interval for this state, in seconds."""
    if nav_status in (NAV_AT_ANCHOR, NAV_MOORED) and sog_kn <= 3.0:
        return 180.0
    if sog_kn <= 14.0:
        return 3.33 if changing_course else 10.0
    if sog_kn <= 23.0:
        return 2.0 if changing_course else 6.0
    return 2.0


def landed_interval_s(sog_kn: float, nav_status: int, coverage: float,
                      rng) -> float:
    """How often a report from this state actually lands, in seconds.

    Reception thinning on top of the transmit schedule. Good coverage lands
    something every couple of minutes; marginal coverage stretches to tens of
    minutes and becomes ragged. The multiplier is drawn per report so cadence
    varies the way real reception does rather than sitting on a fixed grid — a
    perfectly periodic report series is one of the easiest synthetic tells.
    """
    base = transmit_interval_s(sog_kn, nav_status, changing_course=False)
    if coverage <= 0.02:
        # Nothing terrestrial hears this. Only a satellite pass would, and in
        # this corpus we do not have one — so nothing lands.
        return float("inf")
    # Thinning factor: at full coverage roughly 12x the transmit interval,
    # degrading sharply as coverage falls.
    thin = 12.0 / max(coverage, 0.02) ** 0.9
    interval = base * thin * rng.uniform(0.7, 1.45)
    return max(20.0, min(interval, 3 * 3600.0))


def _noisy_position(lat: float, lon: float, rng) -> tuple[float, float]:
    sigma = (POS_OUTLIER_SIGMA_M if rng.random() < POS_OUTLIER_RATE
             else POS_NOISE_SIGMA_M)
    # Isotropic 2-D error: random bearing, Rayleigh-distributed magnitude.
    bearing = rng.uniform(0.0, 360.0)
    r = abs(rng.gauss(0.0, sigma)) + abs(rng.gauss(0.0, sigma))
    r *= 0.7071                                  # keep the 1-sigma magnitude right
    return destination(lat, lon, bearing, r)


def _quantise(v: float, step: float) -> float:
    return round(round(v / step) * step, 3)


@dataclass
class Suppression:
    """A window in which nothing is emitted.

    **`cause` is written into `scenario_truth`, never into an emitted row.**
    It is carried here only so the generator can hand it to the truth writer;
    the emitter itself uses nothing but the time bounds.
    """
    t0: datetime
    t1: datetime
    cause: str
    #: Degradation instead of silence: emit, but at this multiple of the normal
    #: interval. A4 (partial darkness) uses it to slide from 3 min to 6 h.
    degrade_factor: float | None = None

    def covers(self, t: datetime) -> bool:
        return self.t0 <= t < self.t1


def emit_ais(vessel, points: list[TrackPoint], rng, *,
             suppressions: list[Suppression] | None = None,
             receiver_label: str = "ter:synthetic",
             coverage_override: float | None = None,
             force_coverage_floor: float = 0.0) -> list[AisReport]:
    """Decimate an integrated track into landed AIS reports.

    `coverage_override` pins reception for scenarios that need a specific
    reception environment (the receiver-shadow decoy, the offshore deliberate
    miss). Left None, reception is computed from the position against the
    receiver-site model in `geography`, so a vessel that sails offshore goes
    quiet because of where it is — which is the honest behaviour and the reason
    the offshore-gap miss resolves to `unknown` rather than to intentional
    silence.
    """
    suppressions = suppressions or []
    out: list[AisReport] = []
    if not points:
        return out

    next_due = points[0].t
    prev_cog = points[0].cog_deg

    for p in points:
        cov = (coverage_override if coverage_override is not None
               else receiver_coverage(p.lat, p.lon))
        cov = max(cov, force_coverage_floor)

        active = next((s for s in suppressions if s.covers(p.t)), None)
        if active is not None and active.degrade_factor is None:
            # Full silence. Push the schedule forward so the first report after
            # the window is not an instant catch-up burst.
            next_due = max(next_due, p.t)
            prev_cog = p.cog_deg
            continue

        if p.t < next_due:
            prev_cog = p.cog_deg
            continue

        interval = landed_interval_s(p.sog_kn, p.nav_status, cov, rng)
        if active is not None and active.degrade_factor:
            interval *= active.degrade_factor
        if not math.isfinite(interval):
            # Out of coverage entirely: nothing lands, and we re-check next step
            # rather than scheduling — coverage changes as the vessel moves.
            prev_cog = p.cog_deg
            continue

        lat, lon = _noisy_position(p.lat, p.lon, rng)
        # Heading and course differ: a vessel making leeway is not pointing
        # exactly where it is going. A few degrees of offset is normal and its
        # absence is another synthetic tell.
        heading = (p.cog_deg + rng.gauss(0.0, 3.0)) % 360.0
        out.append(AisReport(
            t=p.t,
            lat=lat, lon=lon,
            sog_kn=max(0.0, _quantise(p.sog_kn + rng.gauss(0.0, 0.08), SOG_QUANT)),
            cog_deg=_quantise(p.cog_deg % 360.0, COG_QUANT),
            heading_deg=round(heading),
            nav_status=p.nav_status,
            receiver=receiver_label,
        ))
        next_due = p.t + timedelta(seconds=interval)
        prev_cog = p.cog_deg

    return out


def report_intervals_s(reports: list[AisReport]) -> list[float]:
    return [(b.t - a.t).total_seconds() for a, b in zip(reports, reports[1:])]


def median_interval_s(reports: list[AisReport]) -> float:
    iv = sorted(report_intervals_s(reports))
    if not iv:
        return 0.0
    return iv[len(iv) // 2]


def clone_with_mmsi(reports: list[AisReport]) -> list[AisReport]:
    """Shallow copy — identity lives on the row, not the report."""
    return [AisReport(r.t, r.lat, r.lon, r.sog_kn, r.cog_deg, r.heading_deg,
                      r.nav_status, r.receiver) for r in reports]


def inject_kinematic_jump(reports: list[AisReport], rng, *, at_index: int,
                          jump_kn: float, bearing_deg: float | None = None
                          ) -> list[AisReport]:
    """Displace one report so the implied speed to it is impossible (C3).

    Used only by the scenario that declares an impossible-kinematics violation
    in `scenario_truth` and is whitelisted in the physics validator by
    scenario id. Every other track must pass the envelope check unaided.
    """
    if not (0 < at_index < len(reports)):
        return reports
    prev = reports[at_index - 1]
    cur = reports[at_index]
    dt_h = max((cur.t - prev.t).total_seconds() / 3600.0, 1e-6)
    dist_m = jump_kn * dt_h * 1852.0
    brg = bearing_deg if bearing_deg is not None else rng.uniform(0, 360)
    la, lo = destination(prev.lat, prev.lon, brg, dist_m)
    out = list(reports)
    out[at_index] = AisReport(cur.t, la, lo, cur.sog_kn, cur.cog_deg,
                              cur.heading_deg, cur.nav_status, cur.receiver)
    return out
