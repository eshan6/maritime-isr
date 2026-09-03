"""The measurement that decides whether forward projection is a suspicion
factor — run again, on the route-aware predictor, exactly as ADR-032 ran it.

ADR-032 measured dead-reckoned projection over the corpus and refused to
promote it, and the refusal is the point of this module. A better-sounding
predictor is worth nothing without the same sweep, run the same way, so that
the two tables can be read side by side and the comparison is between two
predictors rather than between two experiments.

Everything here therefore holds to four rules.

**One code path, two predictors.** Both arms call
:func:`tracks.projection.departures_along` with identical gates, identical
persistence, identical stride. The only difference is whether ``model`` is
None. If the route-aware arm needed its own walk, its own gate or its own
sampling, the numbers would not be comparable and the whole exercise would be
theatre.

**The severity threshold is a post-filter, not a knob.** Departures are
computed once per (lead, persistence) at ``margin=1.0`` and then filtered by
how many cone radii outside they landed. This is how ADR-032's grid was swept
and it is what makes a *plateau* visible: if the fleet percentage falls
smoothly across the severity axis there is a threshold worth having, and if it
falls off a cliff there is not.

**The prior is fitted on hulls the measurement never scores.**
:func:`hull_split` divides by hull, never by track — the same rule
:mod:`tracks.vessel_type` splits on and the same rule CLAUDE.md states for
chips and scenes. A flow field fitted on a hull and then scored on that hull's
own tracks measures memorisation, and on a corpus whose vessels follow one
deterministic corridor it would measure it very flatteringly.

**On-lane and off-lane are decided by the predictor, never by truth.** A
sample is *on lane* when the flow field had a supported mode for her cell and
her heading at the moment the projection was made — a fact the predictor holds
in its hand before it predicts. Nothing here reads ``scenario_truth``.

Every figure this module produces is **on the synthetic corpus**, whose
vessels are routed through one deterministic coastal corridor by
``scenario/searoute.py``. A flow field fitted to that traffic recovers the
generator's own waypoints, so the route-aware arm is flattered by construction
and the gap between the arms is an upper bound on the real one. No number from
here may be stated as a live one (CLAUDE.md §4.6, §5).
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from . import projection as proj
from . import route_prior as rp

__all__ = [
    "hull_split", "fit_model", "sweep", "position_errors", "SweepRow",
    "ErrorRow", "format_sweep", "format_errors", "SWEEP_COMBOS",
    "SEVERITY_GRID", "ERROR_LEADS_H", "CAVEAT",
]

#: The five (lead hours, persistence gate, severity) points ADR-032 published.
#: Reproduced verbatim so the baseline arm can be checked against the ADR
#: rather than trusted.
SWEEP_COMBOS: tuple[tuple[float, int, float], ...] = (
    (0.5, 2, 1.0), (0.5, 3, 5.0), (1.0, 3, 5.0), (3.0, 2, 5.0), (3.0, 2, 20.0),
)

#: The severity axis swept in search of a plateau. Denser than ADR-032's five
#: points on purpose: the ADR's finding was "98% or 10%, nothing between", and
#: the only way to confirm or refute that is to look between.
SEVERITY_GRID: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0,
                                    30.0)

#: Leads the position-error table is measured at.
ERROR_LEADS_H: tuple[float, ...] = (0.5, 1.0, 3.0, 6.0)

CAVEAT = (
    "Measured on the synthetic scenario corpus. Its vessels are routed through "
    "one deterministic coastal corridor (scenario/searoute.py), so a flow "
    "field fitted to them recovers the generator's own waypoints and the "
    "route-aware arm is flattered by construction. Real coastal traffic is far "
    "more dispersed; every figure here must be re-measured on the deploy host "
    "before any of it is stated externally.")


def _hull_of(track) -> str:
    """The hull a track belongs to.

    MMSI where there is one, the track key otherwise — a radar track has no
    identity and must still be groupable, which is the same accommodation
    :mod:`schemas.sources` makes everywhere else.
    """
    m = getattr(track, "mmsi", None)
    if m is not None:
        return str(m)
    return str(getattr(track, "track_key", None)
               or getattr(track, "track_id", ""))


# ---------------------------------------------------------------------------
# the split
# ---------------------------------------------------------------------------

@dataclass
class Split:
    """Which hulls the model may learn from, and which it is scored on."""
    fit_hulls: frozenset
    score_hulls: frozenset
    fit_tracks: list
    score_tracks: list
    seed: int

    def report(self) -> dict:
        overlap = self.fit_hulls & self.score_hulls
        return {"n_fit_hulls": len(self.fit_hulls),
                "n_score_hulls": len(self.score_hulls),
                "n_fit_tracks": len(self.fit_tracks),
                "n_score_tracks": len(self.score_tracks),
                "hull_overlap": len(overlap),
                "seed": self.seed,
                "rule": ("split by hull, never by track — a hull on both sides "
                         "lets the flow field memorise her routing and report "
                         "it as knowledge of the lane")}


def hull_split(tracks: Sequence, *, fit_fraction: float = 0.6, seed: int = 7,
               hull_of: Optional[Callable] = None) -> Split:
    """Divide the corpus by hull for fitting and for scoring.

    ``fit_fraction`` of hulls train the flow field and the calibration; the
    rest are scored and never seen. The split is on the **hull**, so every
    track a hull produced lands on the same side.
    """
    hull_of = hull_of or _hull_of
    hulls = sorted({hull_of(t) for t in tracks})
    rng = random.Random(seed)
    rng.shuffle(hulls)
    cut = max(1, int(len(hulls) * fit_fraction))
    fit, score = frozenset(hulls[:cut]), frozenset(hulls[cut:])
    return Split(fit_hulls=fit, score_hulls=score,
                 fit_tracks=[t for t in tracks if hull_of(t) in fit],
                 score_tracks=[t for t in tracks if hull_of(t) in score],
                 seed=seed)


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------

def fit_model(split: Split, *, types: Optional[dict] = None,
              all_tracks: Optional[Sequence] = None,
              hull_of: Optional[Callable] = None,
              baselines=None,
              max_samples: int = 25) -> tuple[rp.PredictionModel,
                                              rp.PredictionModel]:
    """Fit the route-aware model and its dead-reckoning control.

    Returns ``(route_model, dr_model)``. Both carry a calibration fitted on the
    **fit** hulls only, and each calibration is fitted against the predictor it
    will run with — otherwise the comparison would be between a tuned model and
    an untuned one, which is a way of winning an argument rather than settling
    one.

    ``types`` maps ``track_id`` to a motion-inferred vessel type (from
    :mod:`tracks.vessel_type`). Absent, every hull is ``unclassified``, which
    is a real answer and gets its own calibration.

    ``all_tracks`` is the whole corpus for the **own-history** structure only.
    That is not a leak: :meth:`OwnRouteHistory.lookup` admits only passages
    that ended before the moment a projection was made, so a scored hull can
    see her own past and never her own future. Holding it out instead would
    measure a capability the deployed system would have and the measurement
    did not.
    """
    hull_of = hull_of or _hull_of
    types = types or {}

    def klass(track) -> str:
        return rp.motion_class(types.get(getattr(track, "track_id", None)))

    prior = rp.fit_route_prior(split.fit_tracks, hull_of=hull_of)
    own = rp.fit_own_history(list(all_tracks if all_tracks is not None
                                  else split.fit_tracks), hull_of=hull_of)

    labelled = [(klass(t), t) for t in split.fit_tracks]
    staged = rp.PredictionModel(prior=prior, own=own, baselines=baselines)
    route_cal = rp.calibrate(labelled, model=staged, hull_of=hull_of,
                             max_samples=max_samples)
    dr_cal = rp.calibrate(labelled, model=None, hull_of=hull_of,
                          max_samples=max_samples)

    route = rp.PredictionModel(prior=prior, own=own, calibration=route_cal,
                               fallback=route_cal.get("unclassified"),
                               baselines=baselines)
    # The control arm is a PredictionModel with no road in it. It exists so the
    # baseline gets the *same* per-type advance factor and the same measured
    # cone the route arm gets — otherwise the route arm would be beating a
    # predictor that was never calibrated, and the improvement reported would
    # be partly the calibration's.
    dr = rp.PredictionModel(prior=None, own=None, calibration=dr_cal,
                            fallback=dr_cal.get("unclassified"),
                            baselines=baselines)
    return route, dr


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SweepRow:
    lead_h: float
    min_run: int
    severity: float
    n_departures: int
    n_hulls_flagged: int
    n_hulls: int

    @property
    def pct_fleet(self) -> float:
        return 100.0 * self.n_hulls_flagged / max(1, self.n_hulls)

    def as_dict(self) -> dict:
        return {"lead_h": self.lead_h, "min_run": self.min_run,
                "severity_radii": self.severity,
                "departures": self.n_departures,
                "hulls_flagged": self.n_hulls_flagged,
                "n_hulls": self.n_hulls,
                "pct_fleet": round(self.pct_fleet, 1)}


def sweep(tracks: Sequence, *, model=None, types: Optional[dict] = None,
          combos: Optional[Sequence[tuple]] = None,
          severities: Sequence[float] = SEVERITY_GRID,
          max_checks: int = 200,
          hull_of: Optional[Callable] = None,
          progress: Optional[Callable] = None) -> list[SweepRow]:
    """Sweep lead × persistence × severity and report how much of the fleet is
    flagged.

    ``combos`` may be the ADR's five (lead, min_run, severity) points, in which
    case exactly those are returned; pass None to sweep every (lead, min_run)
    in the ADR's set against the whole :data:`SEVERITY_GRID`, which is what a
    plateau would show up in.

    Departures for one (lead, min_run) are computed **once** and filtered by
    severity afterwards, because ``radii_outside`` is a property of each
    departure and re-walking the corpus per threshold would be the same
    arithmetic done nine times.
    """
    hull_of = hull_of or _hull_of
    types = types or {}
    pairs = (sorted({(c[0], c[1]) for c in combos}) if combos
             else sorted({(c[0], c[1]) for c in SWEEP_COMBOS}))
    wanted = set(combos) if combos else None
    n_hulls = len({hull_of(t) for t in tracks})

    rows: list[SweepRow] = []
    for lead, min_run in pairs:
        found: list[tuple[str, float]] = []
        for tr in tracks:
            hull = hull_of(tr)
            vt = types.get(getattr(tr, "track_id", None))
            for d in proj.departures_along(
                    tr, lead_hours=lead, min_run=min_run,
                    max_checks=max_checks, model=model,
                    vessel_type=vt, hull=hull):
                found.append((hull, d.radii_outside))
        if progress:
            progress(f"lead={lead} min_run={min_run}: {len(found)} raw")
        for sev in severities:
            keep = [f for f in found if f[1] >= sev]
            if wanted is not None and (lead, min_run, sev) not in wanted:
                continue
            rows.append(SweepRow(lead_h=lead, min_run=min_run, severity=sev,
                                 n_departures=len(keep),
                                 n_hulls_flagged=len({h for h, _ in keep}),
                                 n_hulls=n_hulls))
    return rows


def format_sweep(rows: Sequence[SweepRow], *, title: str = "") -> str:
    """The sweep as the table ADR-032 published, so the two can be laid side
    by side without re-typing either."""
    out = [title] if title else []
    out.append("| lead (h) | min run | radii >= | departures | % of fleet |")
    out.append("|---|---|---|---|---|")
    for r in rows:
        out.append(f"| {r.lead_h} | {r.min_run} | {r.severity:g} | "
                   f"{r.n_departures:,} | {r.pct_fleet:.0f}% |")
    return "\n".join(out)


def plateau_verdict(rows: Sequence[SweepRow], *, lead_h: float, min_run: int,
                    low: float = 5.0, high: float = 60.0) -> dict:
    """Is there a severity threshold that flags a meaningful minority?

    A *plateau* is a run of severity thresholds where the flagged fraction sits
    between ``low`` and ``high`` percent **and moves slowly** — the fleet
    percentage changing by less than a factor of two from one grid step to the
    next. A single threshold that happens to land in the band while the
    neighbours are at 90% and 2% is a cliff, and a threshold on a cliff is
    fitted to this corpus rather than to the phenomenon. That is the exact
    failure ADR-032 caught, so it is checked rather than eyeballed.
    """
    band = sorted([r for r in rows
                   if r.lead_h == lead_h and r.min_run == min_run],
                  key=lambda r: r.severity)
    inside = [r for r in band if low <= r.pct_fleet <= high]
    runs: list[list[SweepRow]] = []
    cur: list[SweepRow] = []
    for r in band:
        if low <= r.pct_fleet <= high:
            if cur and cur[-1].pct_fleet > 0 and \
                    r.pct_fleet < cur[-1].pct_fleet / 2.0:
                runs.append(cur)
                cur = [r]
            else:
                cur.append(r)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    best = max(runs, key=len, default=[])
    return {
        "lead_h": lead_h, "min_run": min_run,
        "in_band": [r.as_dict() for r in inside],
        "longest_stable_run": [r.as_dict() for r in best],
        "has_plateau": len(best) >= 2,
        "band": [low, high],
        "note": ("a plateau is two or more adjacent severity steps inside the "
                 "band whose flagged fraction does not halve between them; one "
                 "isolated step in the band is a cliff, not a plateau"),
    }


# ---------------------------------------------------------------------------
# position error
# ---------------------------------------------------------------------------

@dataclass
class ErrorRow:
    """Position error for one arm, one lead, one slice — spread, not a median."""
    arm: str
    lead_h: float
    slice_name: str
    n: int
    p10: float
    p50: float
    p90: float
    mean: float

    def as_dict(self) -> dict:
        return {"arm": self.arm, "lead_h": self.lead_h,
                "slice": self.slice_name, "n": self.n,
                "p10_nm": round(self.p10, 2), "p50_nm": round(self.p50, 2),
                "p90_nm": round(self.p90, 2), "mean_nm": round(self.mean, 2)}


def _summary(arm: str, lead: float, name: str, errs: Sequence[float]
             ) -> Optional[ErrorRow]:
    if len(errs) < 20:
        return None
    a = np.asarray(errs, dtype=float)
    return ErrorRow(arm=arm, lead_h=lead, slice_name=name, n=len(a),
                    p10=float(np.percentile(a, 10)),
                    p50=float(np.median(a)),
                    p90=float(np.percentile(a, 90)),
                    mean=float(np.mean(a)))


def position_errors(tracks: Sequence, *, route: rp.PredictionModel,
                    dr: rp.PredictionModel,
                    types: Optional[dict] = None,
                    leads: Sequence[float] = ERROR_LEADS_H,
                    max_samples: int = 30,
                    hull_of: Optional[Callable] = None) -> list[ErrorRow]:
    """How far from her actual position each arm put her, in nautical miles.

    Both arms are scored on **the same samples** — the same origin fixes, the
    same target fixes, the same silence rule — so a difference between them is
    a difference between predictors and not between two samplings of the
    corpus.

    Sliced three ways, because the aggregate hides the interesting part:

    * ``all``
    * by **calibration class** (merchant / fishing / unclassified), which is
      where the advance factor differs by a factor of ten,
    * by **on lane / off lane**, decided by whether the flow field had a
      supported mode for her cell and heading *at the moment of prediction*.
      Off-lane is where the route arm has nothing to offer and must be no worse
      than dead reckoning; if it is worse there, the model is doing harm in the
      places it does not know.
    """
    hull_of = hull_of or _hull_of
    types = types or {}
    buckets: dict[tuple[str, float, str], list[float]] = defaultdict(list)

    for tr in tracks:
        hull = hull_of(tr)
        vt = types.get(getattr(tr, "track_id", None))
        cls = rp.motion_class(vt)
        arr = rp._track_arrays(tr)
        if arr is None:
            continue
        t, lat, lon, sog, cog = arr
        if len(t) < 3:
            continue
        for lead in leads:
            lead_s = lead * 3600.0
            stride = max(1, len(t) // max(1, max_samples))
            for i in range(0, len(t) - 1, stride):
                target = t[i] + lead_s
                if target > t[-1]:
                    break
                j = min(int(np.searchsorted(t, target, side="left")),
                        len(t) - 1)
                # A silence across the whole window leaves nothing to score
                # against, and an "error" measured across it would be a
                # statement about a gap. Same rule `departures_along` applies.
                if abs(t[j] - target) > lead_s * 0.5:
                    continue
                if not np.isfinite(sog[i]) or sog[i] * lead < 0.05:
                    continue
                on_lane = (route.prior is not None
                           and route.prior.lookup(float(lat[i]), float(lon[i]),
                                                  float(cog[i])) is not None)
                lane = "on lane" if on_lane else "off lane"
                common = dict(lat=float(lat[i]), lon=float(lon[i]),
                              sog_kn=float(sog[i]), cog_deg=float(cog[i]),
                              made_at=float(t[i]), valid_for=float(t[j]),
                              vessel_type=cls, hull=hull)
                for arm, model in (("route", route), ("dead reckoning", dr)):
                    p = proj.project_route_aware(model=model, **common)
                    e = rp._hav_m(p.lat, p.lon, float(lat[j]),
                                  float(lon[j])) / 1852.0
                    for name in ("all", cls, lane):
                        buckets[(arm, lead, name)].append(e)

    rows: list[ErrorRow] = []
    for (arm, lead, name), errs in sorted(buckets.items()):
        r = _summary(arm, lead, name, errs)
        if r is not None:
            rows.append(r)
    return rows


def format_errors(rows: Sequence[ErrorRow], *, slice_name: str = "all",
                  title: str = "") -> str:
    """Both arms at every lead, for one slice, as a markdown table."""
    out = [title] if title else []
    out.append("| lead (h) | arm | n | p10 nm | p50 nm | p90 nm | mean nm |")
    out.append("|---|---|---|---|---|---|---|")
    sel = [r for r in rows if r.slice_name == slice_name]
    for r in sorted(sel, key=lambda r: (r.lead_h, r.arm)):
        out.append(f"| {r.lead_h} | {r.arm} | {r.n:,} | {r.p10:.2f} | "
                   f"{r.p50:.2f} | {r.p90:.2f} | {r.mean:.2f} |")
    return "\n".join(out)


def improvement_table(rows: Sequence[ErrorRow]) -> str:
    """Route arm against dead reckoning, per slice, as a percentage change.

    Signed and reported for both the median and the ninetieth percentile,
    because they say different things: the median is how good the prediction
    usually is, and the p90 is the tail a *detector* would have to live on. A
    model that improves the median and leaves the tail alone is a better
    assertion and not a better detector, and only one table shows that.
    """
    idx = {(r.arm, r.lead_h, r.slice_name): r for r in rows}
    slices = sorted({r.slice_name for r in rows})
    out = ["| slice | lead (h) | p50 route/DR | Δ p50 | p90 route/DR | Δ p90 |",
           "|---|---|---|---|---|---|"]
    for s in slices:
        for lead in sorted({r.lead_h for r in rows}):
            a = idx.get(("route", lead, s))
            b = idx.get(("dead reckoning", lead, s))
            if a is None or b is None:
                continue
            d50 = 100.0 * (a.p50 - b.p50) / b.p50 if b.p50 else float("nan")
            d90 = 100.0 * (a.p90 - b.p90) / b.p90 if b.p90 else float("nan")
            out.append(f"| {s} | {lead} | {a.p50:.2f} / {b.p50:.2f} | "
                       f"{d50:+.0f}% | {a.p90:.2f} / {b.p90:.2f} | "
                       f"{d90:+.0f}% |")
    return "\n".join(out)
