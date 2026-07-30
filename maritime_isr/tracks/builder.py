"""Track builder (roadmap 2.1): segment raw position reports into per-MMSI
tracks and survive the real-world filth.

Mechanism: multi-hypothesis assignment per MMSI, not filtering.
  - Each report joins the live hypothesis it can physically belong to
    (implied speed ≤ HYPOTHESIS_SPEED_GATE_KN against the hypothesis's
    predicted position), else it spawns a new hypothesis.
  - Two hypotheses receiving reports in overlapping time ⇒ DUPLICATE_MMSI
    spoof event. Logged, both tracks kept — the tell is the product.
  - A lone impossible jump becomes a singleton hypothesis; hypotheses that
    never accumulate MIN_REAL_POINTS are demoted to outlier points and
    attached (quality='outlier') to the main track. Nothing is dropped.
  - Silence > TRACK_BREAK_DAYS closes the track; the next report under the
    same MMSI starts a new track_id with `fragmented_from` lineage — the
    MMSI-reuse guard. Identity continuity across breaks is a Phase 4 call,
    not a Phase 2 assumption.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import (HYPOTHESIS_SPEED_GATE_KN, PIPELINE_VERSION,
                      TRACK_BREAK_DAYS)
from .. import h3util as tiling
from .kalman import KN_TO_MS, TrackState, epoch_s, filter_smooth

MIN_REAL_POINTS = 3
OVERLAP_SPOOF_MIN_S = 300  # hypotheses must co-live ≥5 min to call it a spoof


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class _Hypothesis:
    hid: int
    rows: list = field(default_factory=list)   # raw report dicts, time-ordered
    t_first: float = 0.0
    t_last: float = 0.0
    lat: float = 0.0
    lon: float = 0.0

    def implied_speed_kn(self, t: float, lat: float, lon: float) -> float:
        dt = max(t - self.t_last, 1.0)
        return _haversine_m(self.lat, self.lon, lat, lon) / dt / KN_TO_MS

    def add(self, row: dict) -> None:
        self.rows.append(row)
        self.t_last, self.lat, self.lon = row["t"], row["lat"], row["lon"]
        if len(self.rows) == 1:
            self.t_first = row["t"]


@dataclass
class BuiltTrack:
    track_id: str
    mmsi: int
    hypothesis: int
    points: pd.DataFrame          # smoothed, with sigma_m + quality
    states: list[TrackState]
    median_report_s: float
    n_outliers: int
    fragmented_from: str | None = None

    def state_at(self, t_epoch: float) -> TrackState:
        """Nearest-preceding smoothed state, predicted forward — the query
        Phase 3 gating will hammer."""
        ts = np.array([s.t for s in self.states])
        i = int(np.searchsorted(ts, t_epoch, side="right")) - 1
        i = max(0, min(i, len(self.states) - 1))
        return self.states[i].predict(max(t_epoch, self.states[i].t))


def _track_id(mmsi: int, hid: int, t0: float) -> str:
    return "trk_" + hashlib.sha1(f"{mmsi}|{hid}|{t0:.0f}".encode()).hexdigest()[:12]


def build_tracks(df: pd.DataFrame) -> tuple[list[BuiltTrack], list[dict]]:
    """df: conformed ais_position rows (mmsi, lat, lon, sog_kn, cog_deg, ts, ...).
    Returns (tracks, spoof_events)."""
    tracks: list[BuiltTrack] = []
    spoof_events: list[dict] = []
    df = df.copy()
    df["t"] = epoch_s(df["ts"])

    for mmsi, g in df.sort_values("t").groupby("mmsi"):
        hyps: list[_Hypothesis] = []
        next_hid = 0
        cols = ["t", "lat", "lon", "sog_kn", "cog_deg", "ts"]
        if "receiver" in g.columns:
            cols.append("receiver")
        for row in g[cols].to_dict("records"):
            # close hypotheses silent past the reuse guard
            live = [h for h in hyps
                    if row["t"] - h.t_last < TRACK_BREAK_DAYS * 86400]
            best, best_v = None, float("inf")
            for h in live:
                v = h.implied_speed_kn(row["t"], row["lat"], row["lon"])
                if v < best_v:
                    best, best_v = h, v
            if best is not None and best_v <= HYPOTHESIS_SPEED_GATE_KN:
                best.add(row)
            else:
                h = _Hypothesis(next_hid); next_hid += 1
                h.add(row)
                hyps.append(h)

        # --- spoof detection: hypotheses that co-lived ---
        real = [h for h in hyps if len(h.rows) >= MIN_REAL_POINTS]
        for i in range(len(real)):
            for j in range(i + 1, len(real)):
                a, b = real[i], real[j]
                ov0, ov1 = max(a.t_first, b.t_first), min(a.t_last, b.t_last)
                if ov1 - ov0 >= OVERLAP_SPOOF_MIN_S:
                    sep = _haversine_m(a.rows[-1]["lat"], a.rows[-1]["lon"],
                                       b.rows[-1]["lat"], b.rows[-1]["lon"]) / 1000
                    spoof_events.append(dict(
                        mmsi=int(mmsi), event_type="DUPLICATE_MMSI",
                        t_start=pd.Timestamp(ov0, unit="s", tz="UTC"),
                        t_end=pd.Timestamp(ov1, unit="s", tz="UTC"),
                        track_ids=f"{_track_id(mmsi, a.hid, a.t_first)},"
                                  f"{_track_id(mmsi, b.hid, b.t_first)}",
                        max_separation_km=float(sep),
                        detail=f"one MMSI, two kinematically incompatible "
                               f"broadcasters, {sep:.0f} km apart"))

        # --- demote singleton hypotheses to outlier points on the main track ---
        main = max(real, key=lambda h: len(h.rows)) if real else None
        outlier_rows = []
        for h in hyps:
            if len(h.rows) < MIN_REAL_POINTS:
                for r in h.rows:
                    r["quality"] = "outlier"
                    outlier_rows.append(r)
                    if main is not None:
                        spoof_events.append(dict(
                            mmsi=int(mmsi), event_type="IMPOSSIBLE_KINEMATICS",
                            t_start=pd.Timestamp(r["t"], unit="s", tz="UTC"),
                            t_end=pd.Timestamp(r["t"], unit="s", tz="UTC"),
                            track_ids=_track_id(mmsi, main.hid, main.t_first),
                            max_separation_km=float(_haversine_m(
                                r["lat"], r["lon"], main.lat, main.lon) / 1000),
                            detail="isolated report kinematically incompatible "
                                   "with every live hypothesis"))

        # --- smooth each surviving hypothesis into a BuiltTrack ---
        for h in real:
            rows = sorted(h.rows, key=lambda r: r["t"])
            t = np.array([r["t"] for r in rows])
            # split at reuse-guard breaks inside the hypothesis
            breaks = np.where(np.diff(t) > TRACK_BREAK_DAYS * 86400)[0]
            segs, prev_tid = np.split(np.arange(len(rows)), breaks + 1), None
            for seg in segs:
                if len(seg) < MIN_REAL_POINTS:
                    continue
                srows = [rows[k] for k in seg]
                st = np.array([r["t"] for r in srows])
                lats = np.array([r["lat"] for r in srows])
                lons = np.array([r["lon"] for r in srows])
                states, sigma = filter_smooth(st, lats, lons)
                pts = pd.DataFrame({
                    "ts": [r["ts"] for r in srows],
                    "receiver": [r.get("receiver", "") for r in srows],
                    "lat": [s.latlon[0] for s in states],
                    "lon": [s.latlon[1] for s in states],
                    "sog_kn": [s.sog_kn for s in states],
                    "cog_deg": [s.cog_deg for s in states],
                    "sigma_m": sigma,
                    "quality": "ok",
                })
                # flag noisy: raw-vs-smoothed residual beyond 3× measurement σ
                res = np.array([_haversine_m(lats[k], lons[k],
                                             pts.lat.iloc[k], pts.lon.iloc[k])
                                for k in range(len(srows))])
                pts.loc[res > 60.0, "quality"] = "noisy"
                tid = _track_id(mmsi, h.hid, st[0])
                med = float(np.median(np.diff(st))) if len(st) > 1 else 0.0
                trk = BuiltTrack(track_id=tid, mmsi=int(mmsi), hypothesis=h.hid,
                                 points=pts, states=states, median_report_s=med,
                                 n_outliers=0, fragmented_from=prev_tid)
                prev_tid = tid
                tracks.append(trk)

        # attach outliers to the main track's point set (kept, flagged)
        if main is not None and outlier_rows:
            mt = next(t_ for t_ in tracks
                      if t_.mmsi == mmsi and t_.hypothesis == main.hid)
            extra = pd.DataFrame({
                "ts": [r["ts"] for r in outlier_rows],
                "receiver": [r.get("receiver", "") for r in outlier_rows],
                "lat": [r["lat"] for r in outlier_rows],
                "lon": [r["lon"] for r in outlier_rows],
                "sog_kn": [r["sog_kn"] for r in outlier_rows],
                "cog_deg": [r["cog_deg"] for r in outlier_rows],
                "sigma_m": 1e6, "quality": "outlier"})
            mt.points = (pd.concat([mt.points, extra])
                         .sort_values("ts").reset_index(drop=True))
            mt.n_outliers = len(outlier_rows)

    # --- lineage across MMSI-reuse breaks -------------------------------
    # A reuse break usually spawns a NEW hypothesis (the stale one was
    # retired by the guard), so within-hypothesis splitting never sees it.
    # Link consecutive non-overlapping tracks of the same MMSI separated by
    # more than the break threshold: identity continuity stays a Phase 4
    # decision; the lineage edge is just recorded evidence.
    by_mmsi: dict[int, list[BuiltTrack]] = {}
    for tr in tracks:
        by_mmsi.setdefault(tr.mmsi, []).append(tr)
    for mmsi, trs in by_mmsi.items():
        trs.sort(key=lambda tr: tr.points["ts"].min())
        for prev, nxt in zip(trs, trs[1:]):
            gap_s = (nxt.points["ts"].min()
                     - prev.points["ts"].max()).total_seconds()
            if gap_s > TRACK_BREAK_DAYS * 86400 and nxt.fragmented_from is None:
                nxt.fragmented_from = prev.track_id

    # merge pairwise DUPLICATE_MMSI events into episodes per MMSI —
    # 4 co-living hypotheses would otherwise emit C(4,2)=6 rows for one
    # phenomenon. Precision-first policy applies to spoof tells too.
    dups = sorted([s_ for s_ in spoof_events if s_["event_type"] == "DUPLICATE_MMSI"],
                  key=lambda s_: (s_["mmsi"], s_["t_start"]))
    other = [s_ for s_ in spoof_events if s_["event_type"] != "DUPLICATE_MMSI"]
    merged: list[dict] = []
    for ev in dups:
        last = merged[-1] if merged else None
        if last and last["mmsi"] == ev["mmsi"] and ev["t_start"] <= last["t_end"]:
            last["t_end"] = max(last["t_end"], ev["t_end"])
            last["max_separation_km"] = max(last["max_separation_km"],
                                            ev["max_separation_km"])
            ids = set(last["track_ids"].split(",")) | set(ev["track_ids"].split(","))
            last["track_ids"] = ",".join(sorted(ids))
            last["detail"] = (f"{len(ids)} kinematically incompatible broadcasters "
                              f"under one MMSI, up to "
                              f"{last['max_separation_km']:.0f} km apart")
        else:
            merged.append(ev)
    return tracks, other + merged
