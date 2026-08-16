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
                      ASSOC_MAX_TRACK_AGE_H, ASSOC_SCORE_FLOOR,
                      ASSOC_SIGMA_REF_M)
from .. import h3util as tiling

SIGMA_MEAS_M = 60.0        # SAR geolocation + smoothing residual — the DEFAULT
LENGTH_REL_SIGMA = 0.25    # SAR length estimate ~18% + registry slop


def _sigma_of(contact: dict, default: float = SIGMA_MEAS_M) -> float:
    """This contact's own 1-σ position error, if it reports one.

    **A module constant was the right shape while SAR was the only contact
    source and is the wrong shape now (ADR-028).** Sentinel-1 geolocation is
    essentially uniform across a scene, so one number covered it. A coastal
    radar plot's accuracy is dominated by cross-range error, which grows
    linearly with range: the same target is good to ~45 m at 10 km and ~220 m at
    50 km from the station. Gating both at 60 m throws away half the radar
    picture at range and gates far too tightly at short range.

    So the observation carries its accuracy and the gate reads it. A contact
    that reports nothing gets the default, which is exactly the previous
    behaviour — no existing SAR call site changes.
    """
    v = contact.get("position_sigma_m")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v > 0.0 and math.isfinite(v) else default


def _hav_m(lat1, lon1, lat2, lon2):
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


KN = 0.514444


def _gate(contact, tracks, t_s: float):
    """Candidate (track, predicted_state, distance, radius, sigma_pos) tuples.

    `sigma_pos` is the **physical** 1-σ on "where is this track right now" —
    the capped uncertainty cone converted from a 95% radius, combined with the
    contact's own measurement error. It is returned separately from the gate
    radius because the two are used for different things and conflating them
    was a defect (see `_score`): the gate says what is worth considering, the
    sigma says how much a given agreement is worth.

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
    sigma = _sigma_of(contact)
    out = []
    for tr in tracks:
        # `t_first`/`t_last` are cached on the track. They used to be
        # `tr.points["ts"].max()` — a pandas reduction over the whole frame,
        # evaluated once per (contact, track) pair. At SAR volumes that is
        # invisible; a radar correlation run makes millions of these calls and
        # it dominated everything else by two orders of magnitude.
        if t_s < tr.t_first - 3600 or t_s - tr.t_last > ASSOC_MAX_TRACK_AGE_H * 3600:
            continue
        k = int(np.searchsorted(tr._pt_epochs, t_s, side="right")) - 1
        dt_anchor = (t_s - tr._pt_epochs[k]) if k >= 0 \
            else (tr._pt_epochs[0] - t_s)
        dt_anchor = max(dt_anchor, 120.0)
        st = tr.state_at(t_s)
        la, lo = st.latlon
        d = _hav_m(contact["lat"], contact["lon"], la, lo)
        v_eff = (2.5 * max(st.sog_kn, 2.0) + 5.0) * KN
        r95 = min(st.uncertainty_radius_m(t_s), v_eff * dt_anchor)
        r = r95 + 3 * sigma + ASSOC_GATE_BUFFER_M
        if d <= r:
            # The error budget for "where is this track now", in quadrature:
            #
            #   * r95/2.4477 — the prediction cone as a 1-σ. A 95% radius for a
            #     2-D Gaussian is 2.4477σ.
            #   * sigma      — the contact's own measurement error.
            #   * BUFFER/2.4477 — everything neither of those models. The gate
            #     has always added `ASSOC_GATE_BUFFER_M` for exactly this
            #     reason: the Kalman covariance on a *smoothed* track is
            #     optimistic, and a vessel does not follow the constant-velocity
            #     model it was fitted with. Omitting it here made the score
            #     demand agreement to ~60 m and rejected a legitimate 700 m
            #     match in the Phase 3 regression suite — the term belongs in
            #     the budget, not only in the gate.
            sigma_pos = math.sqrt((r95 / 2.4477) ** 2 + sigma ** 2
                                  + (ASSOC_GATE_BUFFER_M / 2.4477) ** 2)
            out.append((tr, st, d, r, sigma_pos))
    return out


def _length_rel_sigma(contact: dict, default: float = LENGTH_REL_SIGMA) -> float:
    """How much to trust this contact's length estimate, relatively.

    A SAR contact's length comes from its pixel extent and is good to ~18%. A
    radar contact's comes from radar cross-section, which fluctuates several dB
    look to look — a single plot is worth a size *class*, and a long track's
    median is worth rather more because the fluctuation averages down. The
    sensor is the one that knows which it has, so it says so on the row; the
    gate reads it rather than assuming SAR.
    """
    v = contact.get("length_rel_sigma")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v > 0.0 and math.isfinite(v) else default


def _length_compatible(contact, mmsi: int | None,
                       registry: dict[int, float]) -> bool:
    """HARD length gate: a 60 m contact is not a 200 m merchant, however
    close the positions. Soft penalties don't cut it — measured on the
    synthetic suite, rigs matched passing merchants because a -4 length
    penalty never reached the -8 floor. 2.5σ at 25% relative σ tolerates
    the ~18% SAR length noise with room to spare.

    A track with no identity has no registry length, so this gate cannot fire
    for it and returns True — the same answer it has always given for a vessel
    the registry does not know. That is the correct conservative behaviour: an
    unknown length disqualifies nothing.
    """
    reg_len = registry.get(mmsi) if mmsi is not None else None
    if not reg_len or not contact.get("length_m"):
        return True                      # unknown length can't disqualify
    return (abs(contact["length_m"] - reg_len) / reg_len
            <= 2.5 * _length_rel_sigma(contact))


def _score(contact, tr, st, d: float, sigma_pos: float,
           registry: dict[int, float], t_s: float) -> float:
    """Log-likelihood-ratio of "this contact is this track" against "it isn't".

    **The normalisation term was missing, and its absence was the single
    largest defect this build found (ADR-028).** The score used to be
    `-0.5·(d/σ)²` with `σ` taken from the *gate radius*. That makes the score
    permissive in exact proportion to how little is known: a track whose last
    AIS report is twelve hours old has an uncertainty cone ~900 km wide, so σ
    came out at ~360 km, so a contact **186 km away** scored −0.13 — nowhere
    near the −8 floor, and it matched. Measured on the radar picture: matches
    at 36 km, 61 km, 77 km, 131 km and 187 km, every one of them a real dark
    vessel being explained away by a transmitting ship on the other side of the
    Gulf of Kachchh. The wider the ignorance, the more confident the match.
    That is backwards, and it never showed on the SAR path because the entire
    synthetic SAR corpus is six contacts placed beside fresh AIS tracks.

    A proper 2-D Gaussian log-density is `−ln(2πσ²) − d²/2σ²`. The first term
    is the *volume* normalisation — the price of searching a large area — and
    dropping it makes a large search free. Restoring it, rescaled against a
    reference precision so the numbers stay on the existing floor's scale:

        s = −½(d/σ)² − 2·ln(σ / σ_ref)

    At σ = σ_ref the second term vanishes and the behaviour is exactly what it
    was, which is why the well-constrained SAR case is unchanged. At σ = 360 km
    it is −15, below the −8 floor whatever the distance, so a twelve-hour-stale
    track can no longer explain anything. That is the precision-first answer: a
    hypothesis compatible with half the ocean is not evidence, and declining to
    match is what leaves the contact available to be called dark.
    """
    sigma = max(sigma_pos, _sigma_of(contact))
    s = -0.5 * (d / sigma) ** 2 - 2.0 * math.log(sigma / ASSOC_SIGMA_REF_M)
    # length: only when the registry knows this vessel
    reg_len = registry.get(tr.mmsi) if tr.mmsi is not None else None
    if reg_len and contact.get("length_m"):
        rel = (contact["length_m"] - reg_len) / (
            _length_rel_sigma(contact) * reg_len)
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
        for tr, st, d, r, sig in cs:
            sc = _score(contacts[i], tr, st, d, sig, registry, t_s)
            scores[(i, tr.track_id)] = (sc, tr, st, d)
            cost[i, tix[tr.track_id]] = -sc
        cost[i, n_t + i] = -ASSOC_SCORE_FLOOR          # the "no match" option
    rows, cols = linear_sum_assignment(cost)

    out = []
    for i, j in zip(rows, cols):
        c = contacts[i]
        aid = "asc_" + hashlib.sha1(c["detection_id"].encode()).hexdigest()[:12]
        # **The contact's position travels with the association.** Without it a
        # downstream consumer holding an association row cannot ask a spatial
        # question about it, and one already needed to: `detect_dark_rendezvous`
        # looks for an unmatched contact inside an encounter footprint and its
        # own comment reads "associations don't carry lat/lon; use props if
        # present". They never were, so that branch could not be taken and the
        # rule was silent on 5,880 encounters for want of two columns.
        #
        # `h3_cell` was already here, but a cell is not a position — you cannot
        # compute a 3 km separation from it without inverting the tiling, which
        # is not what a cell is for.
        base = dict(association_id=aid, detection_id=c["detection_id"],
                    scene_id=scene["scene_id"], ts=pd.Timestamp(scene["ts"]),
                    lat=float(c["lat"]), lon=float(c["lon"]),
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
