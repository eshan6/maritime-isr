"""Coverage model + gap classification (roadmap 2.2).

The coverage model is EMPIRICAL: built from the received feed itself, per
(H3 cell, hour, receiver-class). The question it answers — "had a vessel
been transmitting here at this time, would we have heard it?" — is answered
by whether we heard *other* vessels in the same cell-neighborhood window.
This is the honest map of where silence is meaningful, and it is the
product asset the roadmap says nobody else will have for Indian waters.

Gap taxonomy (every gap gets exactly one label + confidence):
  COVERAGE_GAP        no receiver could plausibly have heard it — expected
  SAT_PASS_GAP        only satellite covers the area and the gap sits
                      between passes — expected, schedulable
  INTENTIONAL_SILENCE demonstrated coverage, still silent — the dark period
"""
from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import GAP_MIN_MINUTES, GAP_NOMINAL_MULT
from .. import tiling
from .kalman import epoch_s

COVER_RES = 4          # ~1,770 km² cells: coverage is a regional property
HOUR = 3600.0
# P(heard | transmitting) saturation: 3 distinct other vessels heard in the
# neighborhood-hour ⇒ ~0.95 confidence the cell was covered.
_SAT_K = 1.0


@dataclass
class SatPassSchedule:
    """Pass windows for a satellite AIS feed, [(t0_epoch, t1_epoch), ...].
    Populated by the sat-AIS connector (real: from provider pass predictions;
    synthetic: written by the feed generator)."""
    windows: list[tuple[float, float]] = field(default_factory=list)

    def in_pass(self, t: float) -> bool:
        i = bisect_right([w[0] for w in self.windows], t) - 1
        return i >= 0 and self.windows[i][0] <= t <= self.windows[i][1]

    def covered_fraction(self, t0: float, t1: float) -> float:
        if t1 <= t0:
            return 0.0
        cov = sum(max(0.0, min(t1, w1) - max(t0, w0))
                  for w0, w1 in self.windows)
        return cov / (t1 - t0)

    def passes_within(self, t0: float, t1: float) -> int:
        """Full passes inside the interval — silence through N of these is
        N missed hear-opportunities, the intentionality evidence."""
        return sum(1 for w0, w1 in self.windows if w0 >= t0 and w1 <= t1)

    def nominal_period_s(self) -> float:
        """Median spacing between pass starts; 0.0 if no schedule."""
        if len(self.windows) < 2:
            return 0.0
        starts = [w[0] for w in self.windows]
        diffs = sorted(b - a for a, b in zip(starts, starts[1:]))
        return diffs[len(diffs) // 2]


class CoverageModel:
    """counts[(cell, hour_idx, rx_class)] = set of MMSIs heard."""

    def __init__(self, t0_epoch: float, sat_schedule: SatPassSchedule | None = None):
        self.t0 = t0_epoch
        self.sat = sat_schedule or SatPassSchedule()
        self._heard: dict[tuple[str, int, str], set[int]] = {}

    @staticmethod
    def _rx_classes(receiver: str) -> set[str]:
        """Phase 0 dedup pipe-joins multi-receiver receipts ('ter:x|sat:y');
        every class that heard the report is coverage evidence."""
        return {"sat" if part.startswith("sat") else "ter"
                for part in str(receiver).split("|") if part}

    def fit(self, df: pd.DataFrame) -> "CoverageModel":
        t = epoch_s(df["ts"])
        hours = ((t - self.t0) // HOUR).astype(int)
        cells = [tiling.cell(la, lo, COVER_RES)
                 for la, lo in zip(df["lat"], df["lon"])]
        for c, h, rxs, m in zip(cells, hours, df["receiver"], df["mmsi"]):
            for r in self._rx_classes(rxs):
                self._heard.setdefault((c, int(h), r), set()).add(int(m))
        return self

    def _sets(self, cell: str, hour: int, rx: str,
              exclude_mmsi: int = -1) -> set[int]:
        s: set[int] = set()
        for c in [cell, *tiling.neighbors(cell, 1)]:
            for h in (hour - 1, hour, hour + 1):
                s |= self._heard.get((c, h, rx), set())
        s.discard(exclude_mmsi)
        return s

    def p_sat_area(self, lat: float, lon: float, t: float,
                   exclude_mmsi: int = -1) -> float:
        """Does the satellite feed demonstrably cover this AREA around this
        time — independent of pass timing. Wider ±3 h window because
        satellite evidence arrives in pass bursts."""
        cell = tiling.cell(lat, lon, COVER_RES)
        hour = int((t - self.t0) // HOUR)
        n = 0
        for c in [cell, *tiling.neighbors(cell, 1)]:
            for h in range(hour - 3, hour + 4):
                n += len(self._heard.get((c, h, "sat"), set()) - {exclude_mmsi})
        return 1.0 - math.exp(-_SAT_K * n)

    def sat_feed_health(self, lat: float, lon: float, t: float) -> float:
        """Satellite feed health: did sat receipts land ANYWHERE in the AOI
        within ±3 h of t? Satellite AIS is a global system — if the feed
        delivered receipts around this time, a pass over this cell would
        have heard a transmitter. Empty local neighborhood is NOT deafness;
        demanding local receipt density would suppress the paradigm case
        (a lone dark vessel in empty ocean). Feed outages (no receipts
        anywhere) correctly zero this. lat/lon kept in the signature for a
        future regional detection-probability model (message collisions in
        dense zones lower p in BUSY areas, never in empty ones)."""
        hour = int((t - self.t0) // HOUR)
        if not hasattr(self, "_sat_by_hour"):
            agg: dict[int, int] = {}
            for (c, h, r), mm in self._heard.items():
                if r == "sat":
                    agg[h] = agg.get(h, 0) + len(mm)
            self._sat_by_hour = agg
        n = sum(self._sat_by_hour.get(h, 0) for h in range(hour - 3, hour + 4))
        return 1.0 - math.exp(-0.05 * n)

    def p_heard(self, lat: float, lon: float, t: float,
                exclude_mmsi: int = -1) -> tuple[float, float, float]:
        """(p_terrestrial, p_satellite_now, p_any) at a point-time.

        Terrestrial coverage is a RATIO, not a count: of every vessel heard
        at all in this neighborhood-window, what fraction was heard
        terrestrially? The satellite feed acts as the probe fleet supplying
        negative evidence — at a receiver-ring boundary, sat-only receipts
        from the outside drag the ratio down, which a count-based model
        can't see. This is what stops coverage smear from promoting
        boundary-hover silences to INTENTIONAL_SILENCE."""
        cell = tiling.cell(lat, lon, COVER_RES)
        hour = int((t - self.t0) // HOUR)
        ter = self._sets(cell, hour, "ter", exclude_mmsi)
        sat = self._sets(cell, hour, "sat", exclude_mmsi)
        heard_any = ter | sat
        if heard_any:
            p_ter = (len(ter) / len(heard_any)) * (1.0 - math.exp(-_SAT_K * len(heard_any)))
        else:
            p_ter = 0.0
        p_sat_area = 1.0 - math.exp(-_SAT_K * len(sat))
        p_sat_now = p_sat_area if self.sat.in_pass(t) else 0.0
        return p_ter, p_sat_now, 1.0 - (1.0 - p_ter) * (1.0 - p_sat_now)


def classify_gaps(track, model: CoverageModel,
                  spoof_windows: list[tuple[float, float]] | None = None
                  ) -> list[dict]:
    """Emit one labeled row per gap in one BuiltTrack. Interpolates the
    silent path from the smoothed endpoints and integrates coverage along it.

    `spoof_windows`: [(t0,t1)] epochs of active DUPLICATE_MMSI episodes for
    this MMSI — while identity is compromised, silence attribution is
    meaningless, so INTENTIONAL_SILENCE is never assigned inside them
    (precision-first policy)."""
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) < 2:
        return []
    t = epoch_s(pts["ts"])
    has_rx = "receiver" in pts.columns
    thresh = max(GAP_MIN_MINUTES * 60.0,
                 GAP_NOMINAL_MULT * max(track.median_report_s, 1.0))
    spoof_windows = spoof_windows or []
    out = []
    for i in np.where(np.diff(t) > thresh)[0]:
        t0, t1 = t[i], t[i + 1]
        la0, lo0 = pts.lat.iloc[i], pts.lon.iloc[i]
        la1, lo1 = pts.lat.iloc[i + 1], pts.lon.iloc[i + 1]
        n = max(4, min(24, int((t1 - t0) / 900)))
        fs = np.linspace(0, 1, n)
        p_ter, p_any = [], []
        for f in fs:
            ts_ = t0 + f * (t1 - t0)
            # own pre/post-gap receipts ARE coverage evidence (no
            # circularity: a silent ship has no receipts inside the gap)
            pt, ps, pa = model.p_heard(la0 + f * (la1 - la0),
                                       lo0 + f * (lo1 - lo0), ts_)
            p_ter.append(pt); p_any.append(pa)
        mean_ter = float(np.mean(p_ter))
        mean_any = float(np.mean(p_any))
        # satellite AREA coverage along the silent path, pass-independent
        sat_area = float(np.mean([
            model.p_sat_area(la0 + f * (la1 - la0), lo0 + f * (lo1 - lo0),
                             t0 + f * (t1 - t0))
            for f in fs]))
        # endpoint evidence: the vessel itself was sat-heard at this gap's
        # own endpoints — direct proof its transponder-to-satellite path
        # works in this region, stronger than neighborhood statistics
        if has_rx:
            e0 = "sat" in str(pts.receiver.iloc[i])
            e1 = "sat" in str(pts.receiver.iloc[i + 1])
            endpoint_sat = 0.85 if (e0 and e1) else 0.6 if (e0 or e1) else 0.0
        else:
            endpoint_sat = 0.0
        sat_cov = max(sat_area, endpoint_sat)
        n_passes = model.sat.passes_within(t0, t1)
        period = model.sat.nominal_period_s()
        in_spoof = any(w0 < t1 and w1 > t0 for w0, w1 in spoof_windows)
        covered = sat_cov >= 0.5 or mean_ter >= 0.6

        # Decision tree — precision-first (roadmap 3.3 posture, applied here):
        # an INTENTIONAL_SILENCE conviction requires the vessel to have
        # missed ≥2 independent hear opportunities. Full satellite passes
        # are the reliable opportunity clock; terrestrial evidence raises
        # confidence but cannot convict alone, because terrestrial coverage
        # boundaries are empirically soft at cell resolution (measured on
        # the synthetic suite: every terrestrial-only conviction was a
        # ring-edge false positive). Cost: sub-2-pass dark periods deep
        # inside terrestrial rings are missed — stated recall sacrifice,
        # recoverable by threshold config as measured precision holds.
        if covered and n_passes >= 2 and not in_spoof:
            gap_type = "INTENTIONAL_SILENCE"
            conf = max(mean_ter, sat_cov) * (1.0 - math.exp(-0.6 * n_passes))
        elif n_passes == 0 and period and (t1 - t0) > 2 * period:
            # zero scheduled passes across ≥2 nominal periods — the
            # schedule itself has a hole (feed outage), not the vessel
            gap_type, conf = "COVERAGE_GAP", 1.0 - mean_any
        elif sat_cov >= 0.5:
            # covered area, gap fits between passes — schedulable silence
            gap_type, conf = "SAT_PASS_GAP", 1.0 - model.sat.covered_fraction(t0, t1)
        else:
            gap_type, conf = "COVERAGE_GAP", 1.0 - mean_any

        gid = "gap_" + hashlib.sha1(
            f"{track.track_id}|{t0:.0f}".encode()).hexdigest()[:12]
        out.append(dict(
            gap_id=gid, track_id=track.track_id, mmsi=track.mmsi,
            t_start=pd.Timestamp(t0, unit="s", tz="UTC"),
            t_end=pd.Timestamp(t1, unit="s", tz="UTC"),
            duration_min=(t1 - t0) / 60.0,
            gap_type=gap_type, confidence=float(min(conf, 1.0)),
            coverage_along_path=mean_any,
            lat_start=float(la0), lon_start=float(lo0),
            lat_end=float(la1), lon_end=float(lo1),
            h3_cell=tiling.cell((la0 + la1) / 2, (lo0 + lo1) / 2)))
    return out
