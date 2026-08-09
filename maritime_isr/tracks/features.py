"""Behavioral feature extraction + the rendezvous primitive (roadmap 2.3).

These features are cheap now and become the vessel's behavioral fingerprint
in Phase 4 — the thing a one-time spoof can't fake. The encounter detector
is the primitive Phase 5's dark-rendezvous anomaly is built on.

Encounter mechanics: smoothed tracks are resampled to a common 5-minute
grid, bucketed on H3 res-7 cells (+1-ring), candidate pairs verified on
exact distance and speed. Same H3-join discipline the tiling module was
built for — no O(n²) geometry at query time.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from ..config import (ANCHORAGE_RADIUS_KM, ENCOUNTER_MAX_SOG_KN,
                      ENCOUNTER_MIN_MINUTES, ENCOUNTER_RADIUS_M,
                      LOITER_MAX_SOG_KN, LOITER_MIN_HOURS, PORT_RADIUS_KM)
from .. import h3util as tiling
from .kalman import epoch_s

RESAMPLE_S = 300
ENC_RES = 7  # ~5 km² cells; 500 m radius always inside cell ∪ 1-ring

# The one shared gazetteer (ADR-023). These used to be local literals here,
# duplicating `scenario.geography.PORTS` and disagreeing with it — 8 ports with
# **no Sikka and no Vadinar**, the two Gujarat crude terminals most tanker
# traffic in this AOI calls at, so a full laden voyage into Vadinar produced an
# empty `port_calls` and every port-based rule saw nothing. Re-exported under
# the old names because callers and tests import them from here.
from ..ports import ANCHORAGES as AOI_ANCHORAGES   # noqa: E402
from ..ports import PORTS as AOI_PORTS             # noqa: E402
from ..ports import at_waiting_area, port_at       # noqa: E402


def _hav_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def resample_track(track, step_s: int = RESAMPLE_S) -> pd.DataFrame:
    """Linear resample of smoothed points to a common grid (no extrapolation
    across gaps > 2× step — a silent ship has no resampled presence)."""
    pts = track.points[track.points.quality != "outlier"]
    if len(pts) < 2:
        return pd.DataFrame(columns=["t", "lat", "lon", "sog_kn"])
    t = epoch_s(pts["ts"])
    grid = np.arange(math.ceil(t[0] / step_s) * step_s, t[-1] + 1, step_s)
    if len(grid) == 0:
        return pd.DataFrame(columns=["t", "lat", "lon", "sog_kn"])
    lat = np.interp(grid, t, pts.lat.to_numpy())
    lon = np.interp(grid, t, pts.lon.to_numpy())
    sog = np.interp(grid, t, pts.sog_kn.to_numpy())
    # kill grid points whose bracketing raw interval is a gap
    idx = np.searchsorted(t, grid, side="right") - 1
    idx = np.clip(idx, 0, len(t) - 2)
    ok = (t[idx + 1] - t[idx]) <= 2 * max(step_s, track.median_report_s * 3)
    return pd.DataFrame({"t": grid[ok], "lat": lat[ok], "lon": lon[ok],
                         "sog_kn": sog[ok]})


def detect_encounters(tracks: list) -> list[dict]:
    """Rendezvous candidates: two tracks < ENCOUNTER_RADIUS_M apart at
    < ENCOUNTER_MAX_SOG_KN, sustained ≥ ENCOUNTER_MIN_MINUTES."""
    rs = {tr.track_id: resample_track(tr) for tr in tracks}
    by_track = {tr.track_id: tr for tr in tracks}
    # bucket: (t, cell) -> [(track_id, lat, lon, sog)]
    buckets: dict[tuple[float, str], list] = defaultdict(list)
    for tid, df in rs.items():
        for r in df.itertuples():
            buckets[(r.t, tiling.cell(r.lat, r.lon, ENC_RES))].append(
                (tid, r.lat, r.lon, r.sog_kn))

    hits: dict[tuple[str, str], list] = defaultdict(list)
    for (t, cell), members in buckets.items():
        cand = list(members)
        for nc in tiling.neighbors(cell, 1):
            if nc > cell:  # visit each unordered cell pair once
                cand += buckets.get((t, nc), [])
        for i in range(len(members)):
            for j in range(len(cand)):
                a, b = members[i], cand[j]
                if a[0] >= b[0]:
                    continue
                if by_track[a[0]].mmsi == by_track[b[0]].mmsi:
                    continue  # duplicate-MMSI pairs are spoof events, not meetings
                d = _hav_m(a[1], a[2], b[1], b[2])
                if (d <= ENCOUNTER_RADIUS_M
                        and a[3] <= ENCOUNTER_MAX_SOG_KN
                        and b[3] <= ENCOUNTER_MAX_SOG_KN):
                    hits[(a[0], b[0])].append((t, d, (a[3] + b[3]) / 2,
                                               (a[1] + b[1]) / 2, (a[2] + b[2]) / 2))

    out = []
    need = int(ENCOUNTER_MIN_MINUTES * 60 / RESAMPLE_S)
    for (ta, tb), samples in hits.items():
        samples.sort()
        # split into runs of consecutive grid steps
        run = [samples[0]]
        runs = []
        for s in samples[1:]:
            if s[0] - run[-1][0] <= RESAMPLE_S * 1.5:
                run.append(s)
            else:
                runs.append(run); run = [s]
        runs.append(run)
        for run in runs:
            if len(run) < max(2, need):
                continue
            t0, t1 = run[0][0], run[-1][0]
            dmin = min(s[1] for s in run)
            sog = float(np.mean([s[2] for s in run]))
            lat = float(np.mean([s[3] for s in run]))
            lon = float(np.mean([s[4] for s in run]))
            conf = min(1.0, (len(run) / need) * (1 - dmin / (2 * ENCOUNTER_RADIUS_M)))
            eid = "enc_" + hashlib.sha1(f"{ta}|{tb}|{t0:.0f}".encode()).hexdigest()[:12]
            out.append(dict(
                encounter_id=eid, track_id_a=ta, track_id_b=tb,
                mmsi_a=by_track[ta].mmsi, mmsi_b=by_track[tb].mmsi,
                t_start=pd.Timestamp(t0, unit="s", tz="UTC"),
                t_end=pd.Timestamp(t1, unit="s", tz="UTC"),
                duration_min=(t1 - t0) / 60.0, min_distance_m=float(dmin),
                mean_sog_kn=sog, lat=lat, lon=lon,
                confidence=float(conf), h3_cell=tiling.cell(lat, lon)))
    return out


def extract_features(track) -> dict:
    """Per-track behavioral fingerprint seed (consumed by Phase 4.2)."""
    pts = track.points[track.points.quality != "outlier"]
    t = epoch_s(pts["ts"])
    sog = pts["sog_kn"].to_numpy()
    cog = pts["cog_deg"].to_numpy()
    feats = dict(track_id=track.track_id, mmsi=track.mmsi,
                 sog_mean=float(np.mean(sog)), sog_p90=float(np.percentile(sog, 90)),
                 heading_change_rate_deg_min=float(np.mean(
                     np.abs((np.diff(cog) + 180) % 360 - 180)
                     / np.maximum(np.diff(t) / 60, 1e-6))) if len(t) > 1 else 0.0)

    # Loitering: sustained low speed that is not simply waiting for a berth.
    # Both layers are needed — a berth radius does not reach the anchorage that
    # serves it, which is where a queueing vessel actually stops.
    episodes, i = [], 0
    waiting = np.array([
        at_waiting_area(la, lo, port_radius_km=PORT_RADIUS_KM,
                        anchorage_radius_km=ANCHORAGE_RADIUS_KM)
        for la, lo in zip(pts.lat, pts.lon)])
    slow = (sog < LOITER_MAX_SOG_KN) & ~waiting
    while i < len(slow):
        if slow[i]:
            j = i
            while j + 1 < len(slow) and slow[j + 1]:
                j += 1
            if t[j] - t[i] >= LOITER_MIN_HOURS * 3600:
                episodes.append(dict(t_start=float(t[i]), t_end=float(t[j]),
                                     lat=float(pts.lat.iloc[i:j + 1].mean()),
                                     lon=float(pts.lon.iloc[i:j + 1].mean())))
            i = j + 1
        else:
            i += 1
    feats["n_loiter_episodes"] = len(episodes)
    feats["loiter_episodes"] = episodes

    # port-call sequence
    calls, cur = [], None
    for k in range(len(pts)):
        # Nearest port wins. This used to break on the first dictionary hit, so
        # at Mumbai and JNPT — 11 km apart, both inside the radius — the answer
        # depended on iteration order rather than on distance.
        port = port_at(pts.lat.iloc[k], pts.lon.iloc[k],
                       radius_km=PORT_RADIUS_KM)
        if port != cur:
            if port:
                calls.append(port)
            cur = port
    feats["port_calls"] = calls
    return feats
