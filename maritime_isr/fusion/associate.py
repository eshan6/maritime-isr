"""SAR↔AIS association engine (roadmap 3.1) — the fusion core.

Per scene: probabilistic matching of each SAR contact against the AIS track
picture at scene-acquisition time.

  GATE    a track is a candidate for a contact iff the contact lies inside
          the track's uncertainty cone at scene time (Phase 2's
          `TrackState.uncertainty_radius_m` — built for this call) plus a
          measurement buffer, and the track reported recently enough.
  SCORE   log-likelihood: position term against the cone, length term
          against the registry (when the registry knows the vessel),
          historical-presence bonus (the vessel has been in this cell
          before). Heading consistency is deferred: Sentinel-1-class
          detections don't carry reliable heading at prototype fidelity.
  ASSIGN  global optimum over the whole scene via the Hungarian/JV solver
          (scipy linear_sum_assignment), with a per-contact "no match"
          dummy at the score floor. NEVER greedy per-contact — greedy
          double-assigns one track to two contacts and manufactures a
          phantom dark vessel out of the loser.
  GRADE   assigned pairs whose top-2 margin is thin are AMBIGUOUS (top-k
          reported, confidence discounted); contacts whose best option is
          the floor are UNMATCHED — the dark-vessel candidates.

Scale note: candidate discovery below is a direct n_tracks × n_contacts
distance pass — right answer at prototype scale (tens × tens). At real AOI
scale it becomes an H3 join: index predicted track positions at scene time
into cells, expand each contact by its gate radius in rings. The tiling
module was built for that; the seam is `_gate`.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from ..config import (ASSOC_AMBIGUITY_MARGIN, ASSOC_GATE_BUFFER_M,
                      ASSOC_MAX_TRACK_AGE_H, ASSOC_SCORE_FLOOR)
from .. import tiling

SIGMA_MEAS_M = 60.0        # SAR geolocation + smoothing residual
LENGTH_REL_SIGMA = 0.25    # SAR length estimate ~18% + registry slop


def _hav_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


KN = 0.514444


def _gate(contact, tracks, t_s: float):
    """Candidate (track, predicted_state, distance, radius) tuples.

    Gate radius = min(Kalman 95% radius, effective-speed cone) + buffer,
    where the effective-speed cone is 2.5× the vessel's recent smoothed
    speed + 5 kn over the ANCHOR staleness — the time since the last report
    *before* scene time. Two lessons encoded here, both bought with bugs:
      - staleness vs the track's final report goes NEGATIVE for scene times
        mid-track (the normal batch case), collapsing the cone to zero and
        manufacturing phantom darks out of well-tracked merchants;
      - the raw 60 kn physical cone on an hours-stale track gates half the
        ocean and manufactures ambiguity. A merchant doing 12 kn does not
        teleport at 60; if it truly sprinted, the unmatched contact is the
        safer error under the precision-first posture."""
    out = []
    for tr in tracks:
        t_last = tr.points["ts"].max().timestamp()
        t_first = tr.points["ts"].min().timestamp()
        if t_s < t_first - 3600 or t_s - t_last > ASSOC_MAX_TRACK_AGE_H * 3600:
            continue
        if not hasattr(tr, "_pt_epochs"):
            tr._pt_epochs = np.sort(
                tr.points["ts"].map(pd.Timestamp.timestamp).to_numpy())
        k = int(np.searchsorted(tr._pt_epochs, t_s, side="right")) - 1
        dt_anchor = (t_s - tr._pt_epochs[k]) if k >= 0 \
            else (tr._pt_epochs[0] - t_s)
        dt_anchor = max(dt_anchor, 120.0)
        st = tr.state_at(t_s)
        la, lo = st.latlon
        d = _hav_m(contact["lat"], contact["lon"], la, lo)
        v_eff = (2.5 * max(st.sog_kn, 2.0) + 5.0) * KN
        r = min(st.uncertainty_radius_m(t_s), v_eff * dt_anchor) \
            + 3 * SIGMA_MEAS_M + ASSOC_GATE_BUFFER_M
        if d <= r:
            out.append((tr, st, d, r))
    return out


def _length_compatible(contact, mmsi: int, registry: dict[int, float]) -> bool:
    """HARD length gate: a 60 m contact is not a 200 m merchant, however
    close the positions. Soft penalties don't cut it — measured on the
    synthetic suite, rigs matched passing merchants because a -4 length
    penalty never reached the -8 floor. 2.5σ at 25% relative σ tolerates
    the ~18% SAR length noise with room to spare."""
    reg_len = registry.get(mmsi)
    if not reg_len or not contact.get("length_m"):
        return True                      # unknown length can't disqualify
    return abs(contact["length_m"] - reg_len) / reg_len <= 2.5 * LENGTH_REL_SIGMA


def _score(contact, tr, st, d: float, r_gate: float,
           registry: dict[int, float], t_s: float) -> float:
    # position: gaussian against the effective gate cone (σ = r/2.45, floored)
    sigma = max(r_gate / 2.4477, 150.0)
    s = -0.5 * (d / sigma) ** 2
    # length: only when the registry knows this vessel
    reg_len = registry.get(tr.mmsi)
    if reg_len and contact.get("length_m"):
        rel = (contact["length_m"] - reg_len) / (LENGTH_REL_SIGMA * reg_len)
        s += -0.5 * rel ** 2
    # historical presence: this vessel has been in this cell before
    cell = tiling.cell(contact["lat"], contact["lon"])
    if cell in getattr(tr, "_visited_cells", set()):
        s += 0.5
    return s


def associate_scene(scene: dict, tracks: list, registry: dict[int, float],
                    gaps_by_track: dict[str, list[dict]] | None = None
                    ) -> list[dict]:
    """One scene → one ASSOCIATION row per contact. `gaps_by_track` (from
    the Phase 2 gap classifier) lets a match be flagged as a SAR-confirmed
    dark period: the contact matches a track whose AIS is inside a
    classified gap at scene time — physical confirmation of the silent
    ship. That flag, not unmatched-ness, is the correct dark signal for a
    vessel we CAN still associate."""
    gaps_by_track = gaps_by_track or {}
    t_s = pd.Timestamp(scene["ts"]).timestamp()
    contacts = scene["detections"]
    # precompute visited cells per track once
    for tr in tracks:
        if not hasattr(tr, "_visited_cells"):
            tr._visited_cells = set(
                tiling.cell(r.lat, r.lon) for r in
                tr.points[tr.points.quality != "outlier"].iloc[::10].itertuples())

    cand: list[list] = [
        [g for g in _gate(c, tracks, t_s)
         if _length_compatible(c, g[0].mmsi, registry)]
        for c in contacts]

    n_c = len(contacts)
    track_ids = sorted({t[0].track_id for cs in cand for t in cs})
    tix = {tid: j for j, tid in enumerate(track_ids)}
    n_t = len(track_ids)
    # cost matrix: contacts × (tracks + per-contact dummy)
    BIG = 1e6
    cost = np.full((n_c, n_t + n_c), BIG)
    scores = {}
    for i, cs in enumerate(cand):
        for tr, st, d, r in cs:
            sc = _score(contacts[i], tr, st, d, r, registry, t_s)
            scores[(i, tr.track_id)] = (sc, tr, st, d)
            cost[i, tix[tr.track_id]] = -sc
        cost[i, n_t + i] = -ASSOC_SCORE_FLOOR          # the "no match" option
    rows, cols = linear_sum_assignment(cost)

    out = []
    for i, j in zip(rows, cols):
        c = contacts[i]
        aid = "asc_" + hashlib.sha1(c["detection_id"].encode()).hexdigest()[:12]
        base = dict(association_id=aid, detection_id=c["detection_id"],
                    scene_id=scene["scene_id"], ts=pd.Timestamp(scene["ts"]),
                    h3_cell=tiling.cell(c["lat"], c["lon"]))
        alts = sorted(((sc, tid) for (ci, tid), (sc, *_ ) in scores.items()
                       if ci == i), reverse=True)
        if j >= n_t:                                    # floor won → unmatched
            out.append(dict(**base, status="unmatched", track_id=None,
                            mmsi=None, confidence=0.0,
                            position_error_m=float("nan"),
                            length_error_rel=float("nan"),
                            in_ais_gap=False, gap_type=None,
                            top_k=json.dumps([(t, round(s, 2)) for s, t in alts[:3]])))
            continue
        tid = track_ids[j]
        sc, tr, st, d = scores[(i, tid)]
        # softmax confidence over {assigned, alternatives, floor}
        pool = [s for s, t in alts] + [ASSOC_SCORE_FLOOR]
        z = np.exp(np.array(pool) - max(pool))
        conf = float(z[[t for s, t in alts].index(tid)] / z.sum())
        margin = sc - (alts[1][0] if len(alts) > 1 else ASSOC_SCORE_FLOOR)
        status = "ambiguous" if margin < ASSOC_AMBIGUITY_MARGIN else "matched"
        reg_len = registry.get(tr.mmsi)
        in_gap, gtype = False, None
        for g in gaps_by_track.get(tid, []):
            if g["t_start"].timestamp() <= t_s <= g["t_end"].timestamp():
                in_gap, gtype = True, g["gap_type"]
                break
        out.append(dict(
            **base, status=status, track_id=tid, mmsi=tr.mmsi,
            in_ais_gap=in_gap, gap_type=gtype,
            confidence=round(conf, 4), position_error_m=round(d, 1),
            length_error_rel=(round((c["length_m"] - reg_len) / reg_len, 3)
                              if reg_len and c.get("length_m") else float("nan")),
            top_k=json.dumps([(scores[(i, t)][1].mmsi, round(s, 2))
                              for s, t in alts[:3]])))
    return out
