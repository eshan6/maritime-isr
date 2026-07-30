"""Dark-vessel product logic (roadmap 3.2/3.3).

An unmatched SAR contact must survive the filter cascade before it earns
the name. Every suppression is a recorded verdict, not a deletion — the
analyst question "why is this NOT dark" must be answerable from the store.

  1. COVERAGE   the AIS absence must not be explainable: had a vessel been
                transmitting here, we'd have heard it (Phase 2 coverage
                model + pass schedule). A contact in a receiver shadow is
                unexplained, not dark.
  2. STATIC     not a known fixed installation. The static-object layer
                SELF-BUILDS: unmatched detections recurring at the same
                spot across >= STATIC_MIN_SCENES scenes spanning >=
                STATIC_MIN_SPAN_DAYS accumulate into objects. Matched
                detections never accumulate (a berthed ship isn't a rig).
                Known gap: a never-transmitting vessel loitering one spot
                for weeks would eventually staticize — accepted v1 cost,
                revisited when commercial tasking gives us look-again.
  3. SIZE       length above DARK_MIN_LENGTH_M — margin over the 15-25 m
                Sentinel-1 physics floor. Below it we cannot distinguish
                small craft from clutter and say so rather than alert.
  4. SCORE      survivors get dark_score = detection quality × hearability
                × size margin × isolation; only scores above
                DARK_SCORE_THRESHOLD alert. Launch posture per roadmap 3.3:
                thresholds precision-gated — of 10 alerts >= 7 must survive
                review, recall grows only as measured precision holds.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from ..config import (DARK_MIN_LENGTH_M, DARK_SCORE_THRESHOLD,
                      STATIC_MIN_SCENES, STATIC_MIN_SPAN_DAYS, STATIC_RADIUS_M)
from .. import h3util as tiling
from ..tracks.coverage import CoverageModel

STATIC_RES = 8   # ~460 m cells for static clustering


def _hav_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_static_layer(unmatched: list[dict]) -> list[dict]:
    """Accumulate unmatched detections into fixed objects. Input rows need:
    detection_id, scene_id, ts, lat, lon, length_m."""
    cells: dict[str, list[dict]] = defaultdict(list)
    for u in unmatched:
        cells[tiling.cell(u["lat"], u["lon"], STATIC_RES)].append(u)
    objects = []
    for cell, dets in cells.items():
        scenes = {d["scene_id"] for d in dets}
        if len(scenes) < STATIC_MIN_SCENES:
            continue
        ts = sorted(pd.Timestamp(d["ts"]) for d in dets)
        if (ts[-1] - ts[0]).total_seconds() < STATIC_MIN_SPAN_DAYS * 86400:
            continue
        lat = float(np.mean([d["lat"] for d in dets]))
        lon = float(np.mean([d["lon"] for d in dets]))
        spread = max(_hav_m(lat, lon, d["lat"], d["lon"]) for d in dets)
        if spread > STATIC_RADIUS_M:
            continue
        objects.append(dict(
            object_id="sob_" + hashlib.sha1(cell.encode()).hexdigest()[:12],
            lat=lat, lon=lon, n_scenes=len(scenes),
            first_seen=ts[0], last_seen=ts[-1],
            mean_length_m=float(np.mean([d["length_m"] for d in dets])),
            h3_cell=cell))
    return objects


def hearable(model: CoverageModel, lat: float, lon: float, t_s: float
             ) -> float:
    """P(a transmitting vessel here would have been heard recently).

    Terrestrial: the local ratio model. Satellite: coverage is REGIONAL —
    a pass hears everything in view — so the empirical question offshore is
    feed health at regional scale ("did sat receipts land within ~250 km,
    ±6 h"), not receipt density in this exact cell. Cell-local sat evidence
    would suppress the paradigm case: a lone dark vessel in empty ocean.
    Both legs still require >= 1 full pass in the 2 h before scene time."""
    p_ter, _, _ = model.p_heard(lat, lon, t_s)
    recent_pass = model.sat.passes_within(t_s - 2 * 3600, t_s) >= 1
    p_sat_reg = model.sat_feed_health(lat, lon, t_s) if recent_pass else 0.0
    return float(max(p_ter, p_sat_reg))


SPOOF_AMBIGUITY_RADIUS_M = 25_000.0


def dark_cascade(unmatched: list[dict], model: CoverageModel,
                 statics: list[dict], tracks: list,
                 spoof_windows: dict[int, list[tuple[float, float]]] | None = None
                 ) -> list[dict]:
    """Unmatched contacts → verdict rows (dark_candidate | suppressed_*).
    `tracks` supplies the isolation term: distance to nearest live track.
    `spoof_windows` {mmsi: [(t0,t1)]}: a contact near a track whose MMSI is
    inside an active DUPLICATE_MMSI episode is spoof EVIDENCE (Phase 5's
    anomaly), not a Phase 3 dark vessel — identity there is chaos, and the
    precision-first posture says don't convict on chaos."""
    spoof_windows = spoof_windows or {}
    out = []
    for u in unmatched:
        t_s = pd.Timestamp(u["ts"]).timestamp()
        cid = "drk_" + hashlib.sha1(u["detection_id"].encode()).hexdigest()[:12]
        h = hearable(model, u["lat"], u["lon"], t_s)
        near_static = any(
            _hav_m(u["lat"], u["lon"], s["lat"], s["lon"]) <= STATIC_RADIUS_M
            for s in statics)
        d_track = min((_hav_m(u["lat"], u["lon"], *tr.state_at(t_s).latlon)
                       for tr in tracks
                       if abs(tr.points["ts"].max().timestamp() - t_s) < 48 * 3600
                       or tr.points["ts"].min().timestamp() <= t_s
                       <= tr.points["ts"].max().timestamp()),
                      default=float("inf"))

        near_spoof = False
        for tr in tracks:
            wins = spoof_windows.get(tr.mmsi)
            if not wins or not any(w0 <= t_s <= w1 for w0, w1 in wins):
                continue
            if _hav_m(u["lat"], u["lon"], *tr.state_at(t_s).latlon) \
                    <= SPOOF_AMBIGUITY_RADIUS_M:
                near_spoof = True
                break

        # static check FIRST: a rig is a rig regardless of AIS coverage,
        # and the analyst-facing suppression reason should say so
        if near_spoof:
            status, score = "suppressed_spoof_ambiguity", 0.0
        elif near_static:
            status, score = "suppressed_static", 0.0
        elif h < 0.5:
            status, score = "suppressed_coverage", 0.0
        elif u["length_m"] < DARK_MIN_LENGTH_M:
            status, score = "suppressed_size", 0.0
        else:
            size_f = min(1.0, (u["length_m"] - DARK_MIN_LENGTH_M) / 20.0 + 0.5)
            iso_f = min(1.0, d_track / 5000.0 + 0.5) if math.isfinite(d_track) else 1.0
            score = float(u.get("score", 0.8)) * h * size_f * iso_f
            status = "dark_candidate" if score >= DARK_SCORE_THRESHOLD \
                else "suppressed_score"

        out.append(dict(
            candidate_id=cid, detection_id=u["detection_id"],
            scene_id=u["scene_id"], ts=pd.Timestamp(u["ts"]),
            lat=u["lat"], lon=u["lon"], length_m=u["length_m"],
            status=status, dark_score=round(score, 4),
            hearable_conf=round(h, 4),
            nearest_track_m=(round(d_track, 1) if math.isfinite(d_track)
                             else float("nan")),
            h3_cell=tiling.cell(u["lat"], u["lon"])))
    return out
