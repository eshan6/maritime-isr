"""gap_primitive — an absence, with the reason kept out of the data.

A gap is emitted by *not emitting*. That sounds trivial; the discipline is in
what does not happen alongside it.

**The cause never enters the emitted data.** `GapCause` distinguishes an
intentional shutdown from an equipment failure from a receiver shadow, and the
generator needs that distinction to build coherent scenarios — an equipment
failure should be followed by a maintenance port call, a receiver shadow should
only be placed where reception is genuinely poor. But the *rows that land* carry
no reason. They carry an absence, and absences are identical.

If a cause leaked into the emitted rows — a flag, a distinctive interval
pattern, a suspiciously round duration — then every gap scenario would be
graded on a question it had been handed the answer to, and the resulting recall
figure would be worthless. The cause lives in `scenario_truth`, which detection
code is forbidden to read and a test enforces.

**The honesty rule is a property of placement, not of labelling.** CLAUDE.md is
explicit that a silence outside demonstrated coverage is not evidence of
intentional silence. So an intentional-silence scenario is placed *inside*
plausible reception and an out-of-coverage gap is placed well outside it, and
then the system is left to reach its own verdict. `plausible_placement` reports
whether a requested cause is consistent with where it was put, so a scenario
cannot quietly ask for an "intentional" gap 400 nm offshore where no honest
system could ever call it that.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..geography import receiver_coverage
from .ais import Suppression
from .track import TrackPoint, point_at

#: The causes a scenario can ask for. Values are written to `scenario_truth`
#: only — never to a landed row.
INTENTIONAL = "intentional"
EQUIPMENT_FAILURE = "equipment_failure"
RECEIVER_SHADOW = "receiver_shadow"
OUT_OF_COVERAGE = "out_of_coverage"

CAUSES = (INTENTIONAL, EQUIPMENT_FAILURE, RECEIVER_SHADOW, OUT_OF_COVERAGE)

#: Coverage above which a silence is fairly attributable to the ship rather than
#: to us. Deliberately not a hard threshold in the detection path — it is used
#: here only to check that a scenario's request is self-consistent.
COVERAGE_ATTRIBUTABLE = 0.35
#: Coverage below which nothing could reasonably have been heard.
COVERAGE_DEAF = 0.05


@dataclass
class GapSpec:
    """A planned absence. `cause` is truth-only."""
    t0: datetime
    t1: datetime
    cause: str
    lat_off: float
    lon_off: float
    lat_on: float
    lon_on: float
    coverage_at_off: float
    degrade_factor: float | None = None

    @property
    def duration_h(self) -> float:
        return (self.t1 - self.t0).total_seconds() / 3600.0

    def suppression(self) -> Suppression:
        return Suppression(self.t0, self.t1, self.cause,
                           degrade_factor=self.degrade_factor)


def build_gap(points: list[TrackPoint], t0: datetime, t1: datetime, *,
              cause: str, degrade_factor: float | None = None) -> GapSpec:
    """Describe a gap over an existing track between t0 and t1.

    The off/on positions come from the *integrated truth*, so a gap's endpoints
    are where the ship really was — which is what makes the implied-speed
    arithmetic across a gap meaningful. Taking them from the last and first
    emitted reports instead would fold reception noise into a quantity that is
    supposed to describe the vessel.
    """
    if cause not in CAUSES:
        raise ValueError(f"unknown gap cause {cause!r}")
    if t1 <= t0:
        raise ValueError("gap end must follow gap start")
    p_off = point_at(points, t0)
    p_on = point_at(points, t1)
    if p_off is None or p_on is None:
        raise ValueError("gap window does not overlap the track")
    return GapSpec(
        t0=t0, t1=t1, cause=cause,
        lat_off=p_off.lat, lon_off=p_off.lon,
        lat_on=p_on.lat, lon_on=p_on.lon,
        coverage_at_off=receiver_coverage(p_off.lat, p_off.lon),
        degrade_factor=degrade_factor,
    )


def plausible_placement(spec: GapSpec, *,
                        expect_intentional_verdict: bool = False) -> list[str]:
    """Is the scenario's *expectation* consistent with where the gap was placed?

    Returns the reasons it is not. This does not gate the pipeline's verdict —
    the system reaches that on its own — it gates the *scenario author*.

    **`cause` and expected verdict are different things, and separating them is
    the point.** `cause` is the physical truth of why the transponder stopped;
    a vessel really can switch off deliberately 400 nm offshore. What CLAUDE.md
    forbids is *asserting* intentional silence where we have no reception, and
    that is a claim about the verdict, not about the vessel. So a scenario may
    place a genuinely intentional shutdown out of coverage — the canonical dark
    transfer does exactly that — provided it does not then expect the system to
    call it intentional. It only fails authoring review if it wants both.

    That distinction is what makes the offshore deliberate miss a *designed*
    outcome rather than an unexplained recall failure: the vessel did go dark on
    purpose, and we are still right not to say so.
    """
    problems = []
    cov = spec.coverage_at_off
    if expect_intentional_verdict and cov < COVERAGE_ATTRIBUTABLE:
        problems.append(
            f"scenario expects an INTENTIONAL_SILENCE verdict but reception at "
            f"the off-position is {cov:.2f} — outside demonstrated coverage, "
            f"where CLAUDE.md forbids asserting intentional silence")
    if spec.cause == OUT_OF_COVERAGE and cov > COVERAGE_DEAF:
        problems.append(
            f"cause=out_of_coverage but reception is {cov:.2f} — this is "
            f"hearable, so the gap would be attributable after all")
    if spec.cause == RECEIVER_SHADOW and not (COVERAGE_DEAF < cov
                                              < COVERAGE_ATTRIBUTABLE):
        problems.append(
            f"cause=receiver_shadow needs marginal reception; got {cov:.2f}")
    return problems


def implied_speed_across_gap(spec: GapSpec) -> float:
    """Knots needed to get from the off-position to the on-position.

    A gap a vessel could not physically have crossed at a plausible speed is
    itself a tell — the ship was somewhere else, or the identity was. Landed on
    the gap row exactly as GFW lands theirs.
    """
    from ..geography import haversine_m
    d_m = haversine_m(spec.lat_off, spec.lon_off, spec.lat_on, spec.lon_on)
    h = max(spec.duration_h, 1e-6)
    return d_m / 1852.0 / h


def degrade_ramp(t0: datetime, t1: datetime, *, steps: int = 6,
                 start_factor: float = 1.0, end_factor: float = 120.0,
                 cause: str = INTENTIONAL) -> list[Suppression]:
    """A reporting interval that degrades smoothly across a window (A4).

    Partial darkness is more evasive than total silence and much harder to call:
    the vessel is still reporting, just badly, and poor reception looks the same.
    Rather than a step change, the factor ramps geometrically across `steps`
    sub-windows, so the cadence slides from normal to hours-apart the way a
    failing antenna or a deliberately throttled transponder would.
    """
    out = []
    total = (t1 - t0).total_seconds()
    for i in range(steps):
        a = t0 + timedelta(seconds=total * i / steps)
        b = t0 + timedelta(seconds=total * (i + 1) / steps)
        f = start_factor * (end_factor / start_factor) ** (i / max(steps - 1, 1))
        out.append(Suppression(a, b, cause, degrade_factor=f))
    return out
