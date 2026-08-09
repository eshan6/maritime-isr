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

from ..config import (ENCOUNTER_MAX_SOG_KN, ENCOUNTER_MIN_MINUTES,
                      ENCOUNTER_RADIUS_M, LOITER_MAX_SOG_KN, LOITER_MIN_HOURS,
                      PORT_RADIUS_KM)
from .. import h3util as tiling
from .kalman import epoch_s

RESAMPLE_S = 300
ENC_RES = 7  # ~5 km² cells; 500 m radius always inside cell ∪ 1-ring

# Minimal AOI port layer (WPI fold-in replaces this on the deploy host).
#: Ports whose approaches suppress a loitering signal — a vessel waiting for a
#: berth is queueing, not loitering (PORT_RADIUS_KM).
#:
#: **Sikka, Vadinar and Gwadar were missing, and the omission was load-bearing.**
#: Sikka and Vadinar are the Gulf of Kutch crude terminals and take a large share
#: of this AOI's tanker traffic — and they sit inside the "Kandla pipeline
#: corridor" sensitive zone in `anomaly/library.py`. With no entry here, every
#: vessel waiting at either anchorage produced an unsuppressed loiter episode
#: inside a sensitive geofence, which is an alert. Measured on a corpus with
#: realistic anchorage traffic: 30 alerts on ordinary merchant vessels, against
#: 4 on the whole scenario cast. The gap was recorded in STATE.md ("three
#: separate port gazetteers ... no Sikka or Vadinar, where most scenario tanker
#: traffic goes") before it had anything to bite on.
AOI_PORTS = {
    "Mumbai": (18.95, 72.84), "JNPT": (18.95, 72.95), "Kandla": (23.02, 70.22),
    "Mundra": (22.74, 69.70), "Porbandar": (21.63, 69.60),
    "Karachi": (24.79, 66.98), "Kochi": (9.97, 76.24), "Mangalore": (12.92, 74.80),
    "Sikka": (22.43, 69.84), "Vadinar": (22.28, 69.73), "Gwadar": (24.88, 62.32),
}


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

    # loitering: sustained low speed outside port radius
    episodes, i = [], 0
    near_port = np.array([min(_hav_m(la, lo, pla, plo)
                              for pla, plo in AOI_PORTS.values()) < PORT_RADIUS_KM * 1000
                          for la, lo in zip(pts.lat, pts.lon)])
    slow = (sog < LOITER_MAX_SOG_KN) & ~near_port
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
        port = None
        for name, (pla, plo) in AOI_PORTS.items():
            if _hav_m(pts.lat.iloc[k], pts.lon.iloc[k], pla, plo) < PORT_RADIUS_KM * 1000:
                port = name
                break
        if port != cur:
            if port:
                calls.append(port)
            cur = port
    feats["port_calls"] = calls
    return feats
