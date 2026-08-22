"""What counts as normal *here* — per-area behavioural baselines.

*"The requirement states twice that the system should learn from local
historical data and deliver area-specific intelligence. This means anomaly
thresholds derived per area rather than set globally — what counts as unusual
speed, unusual loitering duration, or unusual traffic density in one approach
channel is ordinary in another. Build the baseline as a maintained, inspectable
artifact, not a constant in a configuration file. This is an explicit ask and it
is currently absent from the system entirely."* — the IDEX Challenge 82 brief,
Area 2.

The last sentence was accurate. Every threshold in this system is global:
``LOITER_MAX_SOG_KN`` is 2 knots off Mumbai and 2 knots in the middle of the
Arabian Sea, and ``LOITER_MIN_HOURS`` is two hours in a working anchorage and
two hours over a cable route. That is why the loitering rule was once a Kandla
anchorage detector (STATE.md, 2026-08-09) and why the rendezvous rule was a
Mangalore berth detector until ADR-031.

Three things this module is, and one it is not
----------------------------------------------
**It is an artifact, not a constant.** A baseline is derived from landed
positions, written to a table with the provenance envelope every other record
carries, and re-derivable from raw plus a git SHA (CLAUDE.md §4.2). It can be
listed, queried and shown to an operator — "here is what we think normal looks
like in this cell, and here are the 412 observations we think it from" — which
is what "maintained, inspectable artifact" means and what a number in
``config.py`` can never be.

**It is per H3 cell, at res 5.** The grid is the one the whole architecture
already turns on (CLAUDE.md §3), so a baseline lookup is a hash join rather than
a geometry problem. Res 5 cells are ~250 km² — about 8 km across. That is the
scale at which "here" means here for this purpose: an approach channel, an
anchorage and the open water outside them fall in different cells, while a cell
is still large enough to accumulate enough observations to say anything. Res 7,
which the association layer uses, would give ~1.2 km cells and a corpus this
size would leave most of them with three observations and a meaningless
distribution.

**It refuses to speak when it has not seen enough.** A cell with eleven
observations has no distribution, and a baseline derived from one is worse than
no baseline: it looks authoritative and is noise. :data:`MIN_OBSERVATIONS` is
the floor, cells below it are recorded as ``insufficient`` rather than dropped,
and :func:`is_unusual` returns ``None`` — "cannot say" — rather than False. A
caller that cannot tell "normal" from "unknown" will report every unmonitored
patch of ocean as clean.

**It is not a learned model, and it does not decide anything.** It reports
percentiles of what has actually been observed. Whether a vessel above the 95th
percentile of local speed is *suspicious* is a rule's judgement, not this
module's, and the separation is deliberate: a baseline that also alerted would
be a detector whose calibration lived in its own training data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

import numpy as np

from . import h3util as tiling
from .config import PIPELINE_VERSION

__all__ = ["AreaBaseline", "BASELINE_TABLE", "BASELINE_RES",
           "MIN_OBSERVATIONS", "derive_baselines", "land_baselines",
           "load_baselines", "BaselineIndex", "is_unusual"]

#: Where derived baselines land. A conformed table like any other.
BASELINE_TABLE = "area_baseline"

#: H3 resolution the baseline grid is built at. See the module docstring —
#: this is a judgement about the scale at which "here" means here, and it is
#: named rather than passed so that a baseline landed today and one landed next
#: month cannot silently be built at different resolutions and joined.
#:
#: Taken from `h3util`, not written as a literal: that module's standing rule is
#: that no integer resolution is hard-coded outside it (ADR-015), because a
#: resolution spelled in two places is how the H3 joins go silently wrong.
BASELINE_RES = tiling.R5

#: Fewest observations a cell needs before its distribution is reported.
#:
#: **Not tuned — argued.** Below about a hundred samples a 95th percentile is
#: an order statistic over a handful of points and moves by knots when one
#: vessel passes. A cell that carries a hundred position reports over an
#: eight-week corpus has seen real traffic; one that carries twelve has seen a
#: single passage, and the "normal speed" it would report is that one ship's
#: speed. The floor is generous on purpose: the cost of an absent baseline is a
#: rule falling back to its global threshold, which is exactly where the system
#: already was.
MIN_OBSERVATIONS = 100

#: Percentiles reported for every metric. The 50th is what normal looks like;
#: the 95th is the edge of ordinary; the 99th is where a rule might reasonably
#: start paying attention. Kept as a triple rather than a single "threshold"
#: because a rule choosing its own operating point from a distribution is the
#: separation this module exists to preserve.
PERCENTILES = (50, 95, 99)


@dataclass
class AreaBaseline:
    """What normal looks like in one cell, and how much we saw to say it."""
    h3_cell: str
    res: int
    lat: float
    lon: float
    n_observations: int
    n_vessels: int
    status: str                              # "derived" | "insufficient"
    #: {metric: {p50, p95, p99, mean, std}}
    metrics: dict = field(default_factory=dict)
    #: Vessels per day seen in this cell — the traffic-density half of the ask.
    vessels_per_day: Optional[float] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    source_id: str = "maritime-isr:baseline"
    source_ref: str = "derive_baselines"
    pipeline_version: str = PIPELINE_VERSION
    is_synthetic: bool = False

    @property
    def usable(self) -> bool:
        return self.status == "derived"

    def percentile(self, metric: str, p: int) -> Optional[float]:
        m = self.metrics.get(metric)
        if not m or not self.usable:
            return None
        return m.get(f"p{p}")

    def as_row(self) -> dict:
        """Flat, for landing. Nested dicts do not survive a Parquet schema well
        and a column per metric-percentile is what a DuckDB query wants."""
        row = {
            "h3_cell": self.h3_cell, "res": self.res,
            "lat": self.lat, "lon": self.lon,
            "n_observations": self.n_observations,
            "n_vessels": self.n_vessels,
            "status": self.status,
            "vessels_per_day": self.vessels_per_day,
            "window_start": self.window_start, "window_end": self.window_end,
            "source_id": self.source_id, "source_ref": self.source_ref,
            "pipeline_version": self.pipeline_version,
            "confidence": (min(1.0, self.n_observations / 1000.0)
                           if self.usable else 0.0),
            "is_synthetic": self.is_synthetic,
        }
        for metric, stats in self.metrics.items():
            for k, v in stats.items():
                row[f"{metric}_{k}"] = v
        return row

    def as_dict(self) -> dict:
        return {
            "h3_cell": self.h3_cell, "res": self.res,
            "lat": round(self.lat, 5), "lon": round(self.lon, 5),
            "n_observations": self.n_observations,
            "n_vessels": self.n_vessels, "status": self.status,
            "usable": self.usable, "metrics": self.metrics,
            "vessels_per_day": self.vessels_per_day,
            "window": {"start": self.window_start, "end": self.window_end},
            "min_observations": MIN_OBSERVATIONS,
            "note": (None if self.usable else
                     f"Only {self.n_observations} observation(s) in this cell, "
                     f"below the {MIN_OBSERVATIONS} needed for a distribution. "
                     f"No baseline is reported — a percentile over a handful of "
                     f"points is noise wearing an authoritative face."),
            "is_synthetic": self.is_synthetic,
        }


def _stats(values: np.ndarray) -> dict:
    return {
        **{f"p{p}": round(float(np.percentile(values, p)), 3)
           for p in PERCENTILES},
        "mean": round(float(np.mean(values)), 3),
        "std": round(float(np.std(values)), 3),
        "min": round(float(np.min(values)), 3),
        "max": round(float(np.max(values)), 3),
    }


def derive_baselines(positions, *, res: int = BASELINE_RES,
                     min_observations: int = MIN_OBSERVATIONS
                     ) -> list[AreaBaseline]:
    """Derive a baseline per cell from landed positions.

    ``positions`` is a DataFrame with ``lat``, ``lon``, ``sog_kn``, ``ts`` and
    ideally ``vessel_id`` (or ``mmsi``) and ``is_synthetic``.

    **The cell is recomputed here rather than read from the row's own
    ``h3_r5``.** The landed rows carry res 4, 6, 7, 8 and 9 — not 5 — so there
    is nothing to read, and computing it through the shared helper is the
    contract anyway (CLAUDE.md §3: one helper, one version, everywhere).
    Hand-rolling ``latlng_to_cell`` here is precisely how the joins go silently
    wrong.
    """
    import pandas as pd

    if positions is None or len(positions) == 0:
        return []
    df = positions
    need = {"lat", "lon", "sog_kn"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(
            f"cannot derive baselines: positions are missing {sorted(missing)}. "
            f"A baseline over rows without a speed would report a distribution "
            f"of nothing.")

    df = df.dropna(subset=["lat", "lon", "sog_kn"]).copy()
    if len(df) == 0:
        return []
    df["_cell"] = [tiling.cell(la, lo, res)
                   for la, lo in zip(df["lat"], df["lon"])]

    key = "vessel_id" if "vessel_id" in df.columns else (
        "mmsi" if "mmsi" in df.columns else None)

    w_start = w_end = None
    if "ts" in df.columns:
        ts = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df["_day"] = ts.dt.date
        w_start = ts.min()
        w_end = ts.max()

    out: list[AreaBaseline] = []
    for cell, grp in df.groupby("_cell", sort=True):
        n = len(grp)
        lat, lon = tiling.cell_center(cell)
        n_vessels = int(grp[key].nunique()) if key else 0
        synthetic = bool(grp["is_synthetic"].all()) \
            if "is_synthetic" in grp.columns else False

        if n < min_observations:
            out.append(AreaBaseline(
                h3_cell=cell, res=res, lat=lat, lon=lon, n_observations=n,
                n_vessels=n_vessels, status="insufficient",
                window_start=_iso(w_start), window_end=_iso(w_end),
                is_synthetic=synthetic))
            continue

        metrics = {"sog_kn": _stats(grp["sog_kn"].to_numpy(dtype=float))}
        if "cog_deg" in grp.columns:
            # Course is circular, so a percentile of it is meaningless — the
            # mean of 350° and 10° is not 180°. What IS meaningful is how
            # *concentrated* the traffic's heading is: a shipping lane has two
            # tight modes, open water has none. Reported as the resultant
            # length of the unit vectors, in [0,1].
            ang = np.radians(grp["cog_deg"].to_numpy(dtype=float))
            r = math.hypot(float(np.mean(np.cos(ang))),
                           float(np.mean(np.sin(ang))))
            metrics["course_concentration"] = {"r": round(r, 3)}

        vpd = None
        if "_day" in grp.columns and key:
            days = grp["_day"].nunique()
            if days:
                vpd = round(float(grp.groupby("_day")[key].nunique().mean()), 2)

        out.append(AreaBaseline(
            h3_cell=cell, res=res, lat=lat, lon=lon, n_observations=n,
            n_vessels=n_vessels, status="derived", metrics=metrics,
            vessels_per_day=vpd, window_start=_iso(w_start),
            window_end=_iso(w_end), is_synthetic=synthetic))
    return out


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return v.isoformat()
    except AttributeError:
        return str(v)


class BaselineIndex:
    """Baselines keyed by cell, with the lookup a rule actually makes.

    A rule holds a position, not a cell, so the index computes the cell through
    the shared helper on the caller's behalf — the one place that conversion
    should happen for this layer.
    """

    def __init__(self, baselines: Iterable[AreaBaseline],
                 res: int = BASELINE_RES):
        self.res = res
        self._by_cell = {b.h3_cell: b for b in baselines}

    def __len__(self) -> int:
        return len(self._by_cell)

    def at(self, lat: float, lon: float) -> Optional[AreaBaseline]:
        return self._by_cell.get(tiling.cell(lat, lon, self.res))

    def usable(self) -> list[AreaBaseline]:
        return [b for b in self._by_cell.values() if b.usable]

    def coverage(self) -> dict:
        """How much of this index can actually answer a question.

        Returned wherever the index is shown, because a baseline layer with 6
        usable cells out of 900 is a layer that will fall back to the global
        threshold almost everywhere — and an operator told "area baselines are
        in use" would reasonably assume otherwise.
        """
        usable = sum(1 for b in self._by_cell.values() if b.usable)
        return {
            "cells": len(self._by_cell),
            "usable": usable,
            "insufficient": len(self._by_cell) - usable,
            "fraction_usable": (round(usable / len(self._by_cell), 3)
                                if self._by_cell else 0.0),
            "res": self.res,
            "min_observations": MIN_OBSERVATIONS,
        }


def is_unusual(index: BaselineIndex, *, lat: float, lon: float,
               metric: str, value: float, percentile: int = 95
               ) -> Optional[dict]:
    """Is this value unusual *for this place*? None means "cannot say".

    **The three-valued return is the whole point.** True, False and None are
    genuinely different answers — "unusual here", "ordinary here", and "we have
    not watched here enough to have an opinion" — and a boolean would collapse
    the third into the second. Every rule that consumes this must handle None
    by falling back to its global threshold and saying so, not by treating the
    behaviour as normal.
    """
    b = index.at(lat, lon)
    if b is None:
        return None
    if not b.usable:
        return None
    threshold = b.percentile(metric, percentile)
    if threshold is None:
        return None
    return {
        "unusual": bool(value > threshold),
        "value": round(float(value), 3),
        "threshold": threshold,
        "percentile": percentile,
        "metric": metric,
        "h3_cell": b.h3_cell,
        "n_observations": b.n_observations,
        "n_vessels": b.n_vessels,
        "statement": (
            f"{value:.1f} against a local {percentile}th percentile of "
            f"{threshold:.1f} for {metric.replace('_', ' ')}, from "
            f"{b.n_observations:,} observations of {b.n_vessels} vessel(s) in "
            f"this cell."),
    }


# --------------------------------------------------------------------------
# landing and loading
# --------------------------------------------------------------------------

def land_baselines(baselines: Sequence[AreaBaseline], *,
                   source_id: str = "maritime-isr:baseline",
                   is_synthetic: bool | None = None) -> int:
    """Write derived baselines to the conformed store. Returns rows written.

    Landed rather than held in memory because the requirement is a *maintained,
    inspectable* artifact: an operator has to be able to ask what the system
    thinks normal is, and a value that exists only inside a running process
    cannot be asked. Insufficient cells land too — "we watched here and saw too
    little" is a fact worth keeping, and dropping them would make the coverage
    figure unmeasurable.
    """
    from .ingest.landing import SYNTHETIC_SOURCE_ID, land_table, stamp_envelope

    if not baselines:
        return 0
    now = datetime.now(timezone.utc)
    rows = []
    for b in baselines:
        row = b.as_row()
        syn = b.is_synthetic if is_synthetic is None else is_synthetic
        # `source_id` on a synthetic row must be *exactly* the reserved token —
        # the landing layer refuses anything else, because the flag and the
        # envelope are two independent markers of the same fact and are only
        # safer than one if they can never drift (ADR-019). What this artifact
        # is goes in `source_ref`, which is free-form and is where a
        # distinguishing name belongs.
        stamp_envelope(row,
                       source_id=SYNTHETIC_SOURCE_ID if syn else source_id,
                       source_ref=(f"{source_id}:{BASELINE_TABLE}" if syn
                                   else BASELINE_TABLE),
                       acquired_at=now,
                       confidence=row.get("confidence"), is_synthetic=syn)
        rows.append(row)
    # Keyed on the cell and partitioned by the day of derivation. Re-deriving
    # on the same day converges on one row per cell (the landing layer's
    # idempotence); re-deriving next month lands a *new* snapshot beside the
    # old one, which is deliberate — "what did we think normal was when that
    # alert was raised" has to stay answerable (CLAUDE.md §4.2), and
    # `load_baselines` resolves latest-per-cell on read.
    written = land_table(rows, table=BASELINE_TABLE,
                         key_fields=("h3_cell",), day_field="acquired_at")
    return sum(written.values())


def load_baselines() -> list[AreaBaseline]:
    """Read the most recent landed baseline snapshot, or an empty list."""
    from .ingest.landing import read_table

    try:
        rows = read_table(BASELINE_TABLE)
    except Exception:                                            # noqa: BLE001
        return []
    if not rows:
        return []
    # Latest snapshot per cell. Snapshots from different days coexist by
    # design, so a naive read would blend this week's distribution with last
    # month's and report a baseline that was never true at any moment.
    latest: dict[str, dict] = {}
    for r in rows:
        cell = r.get("h3_cell")
        if not cell:
            continue
        prev = latest.get(cell)
        if prev is None or str(r.get("acquired_at")) > str(prev.get("acquired_at")):
            latest[cell] = r
    return [_from_row(r) for r in latest.values()]


def _from_row(r: dict) -> AreaBaseline:
    metrics: dict = {}
    for k, v in r.items():
        for metric in ("sog_kn", "course_concentration"):
            prefix = metric + "_"
            if k.startswith(prefix) and v is not None:
                metrics.setdefault(metric, {})[k[len(prefix):]] = v
    return AreaBaseline(
        h3_cell=r["h3_cell"], res=int(r.get("res") or BASELINE_RES),
        lat=float(r.get("lat") or 0.0), lon=float(r.get("lon") or 0.0),
        n_observations=int(r.get("n_observations") or 0),
        n_vessels=int(r.get("n_vessels") or 0),
        status=r.get("status") or "insufficient", metrics=metrics,
        vessels_per_day=r.get("vessels_per_day"),
        window_start=_iso(r.get("window_start")),
        window_end=_iso(r.get("window_end")),
        source_id=r.get("source_id") or "maritime-isr:baseline",
        source_ref=r.get("source_ref") or "derive_baselines",
        pipeline_version=r.get("pipeline_version") or PIPELINE_VERSION,
        is_synthetic=bool(r.get("is_synthetic")))


def load_index() -> BaselineIndex:
    return BaselineIndex(load_baselines())
