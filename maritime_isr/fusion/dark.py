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

**Two things here were tuned to SAR and had to become parameters (ADR-028).**
Both defaults are the SAR values, so no existing call site changes:

  * *the static-object clustering radius.* STATIC_RADIUS_M is 200 m, which is
    comfortably wider than Sentinel-1's ~60 m geolocation error and therefore
    the right size for accumulating repeated SAR looks at a rig. It is
    *narrower* than a coastal radar's own position error at 40 km, so on radar
    the platform's plots scatter past the radius, `build_static_layer` rejects
    the cluster on spread, and every rig in the picture is promoted to a dark
    vessel. The radius has to follow the sensor.
  * *persistence.* A SAR scene is one look: a contact exists or it does not, so
    there was nothing to be persistent about. A radar picture contains sea
    clutter and transient false tracks, and a target seen three times in ten
    minutes and never again is not a ship. `min_looks` is 1 by default — a
    no-op for a single-look sensor — and the suppression is recorded as a
    verdict like every other, because "why is this NOT dark" must stay
    answerable.
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


def build_static_layer(unmatched: list[dict], *,
                       radius_m: float = STATIC_RADIUS_M,
                       res: int = STATIC_RES,
                       min_scenes: int = STATIC_MIN_SCENES) -> list[dict]:
    """Accumulate unmatched detections into fixed objects. Input rows need:
    detection_id, scene_id, ts, lat, lon, length_m.

    `radius_m` is how far apart two looks at the same fixed object may be
    before we stop believing they are the same object, and it has to be wider
    than the sensor's position error or nothing ever accumulates. `res` is the
    H3 resolution the looks are bucketed at and should be the coarser of "small
    enough to separate two nearby platforms" and "large enough that one
    platform's looks land in one cell"; a cell much smaller than `radius_m`
    scatters one object across several cells.

    `min_scenes` is how many independent looks make a recurrence, and it has to
    move with how often the sensor looks. Three is right for Sentinel-1, which
    revisits every six days: three passes over a fortnight at the same 200 m
    spot is a rig. It is badly wrong for a sensor that reports continuously —
    measured on the synthetic radar picture, `min_scenes=3` produced **58**
    static objects, of which four were the real installations and the rest were
    *shipping lanes*: three different ships crossing the same cell on three
    different days over a week is not a fixed object, and at three scenes it is
    indistinguishable from one. What separates them is that an installation is
    there on essentially every day the sensor looks, and a lane is there on the
    days a ship sails it.

    **Spread is measured against the median, not the mean.** With a handful of
    SAR looks the two agree. With thousands of radar plots, one bad plot at
    3 km drags the mean off the platform and then every plot looks like an
    outlier from it — the cluster is rejected for containing a single error.
    """
    cells: dict[str, list[dict]] = defaultdict(list)
    for u in unmatched:
        cells[tiling.cell(u["lat"], u["lon"], res)].append(u)
    objects = []
    for cell, dets in cells.items():
        scenes = {d["scene_id"] for d in dets}
        if len(scenes) < min_scenes:
            continue
        ts = sorted(pd.Timestamp(d["ts"]) for d in dets)
        if (ts[-1] - ts[0]).total_seconds() < STATIC_MIN_SPAN_DAYS * 86400:
            continue
        lat = float(np.median([d["lat"] for d in dets]))
        lon = float(np.median([d["lon"] for d in dets]))
        # 90th percentile rather than the maximum, for the same reason: one
        # stray look must not veto an object seen a thousand times. With the
        # 3-5 looks a SAR run produces, p90 and max coincide.
        spread = float(np.percentile(
            [_hav_m(lat, lon, d["lat"], d["lon"]) for d in dets], 90))
        if spread > radius_m:
            continue
        lengths = [d["length_m"] for d in dets if d.get("length_m") is not None]
        objects.append(dict(
            object_id="sob_" + hashlib.sha1(cell.encode()).hexdigest()[:12],
            lat=lat, lon=lon, n_scenes=len(scenes),
            first_seen=ts[0], last_seen=ts[-1],
            mean_length_m=float(np.median(lengths)) if lengths else None,
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
                 spoof_windows: dict[int, list[tuple[float, float]]] | None = None,
                 *, static_radius_m: float = STATIC_RADIUS_M,
                 min_looks: int = 1,
                 require_excess_contacts: bool = False) -> list[dict]:
    """Unmatched contacts → verdict rows (dark_candidate | suppressed_*).
    `tracks` supplies the isolation term: distance to nearest live track.
    `spoof_windows` {mmsi: [(t0,t1)]}: a contact near a track whose MMSI is
    inside an active DUPLICATE_MMSI episode is spoof EVIDENCE (Phase 5's
    anomaly), not a Phase 3 dark vessel — identity there is chaos, and the
    precision-first posture says don't convict on chaos.

    `min_looks`: how many independent observations a contact must rest on. A
    row may declare `n_looks`; one that does not is assumed to have exactly one,
    which is what a SAR contact is. See the module docstring.

    **`exclude_track_ids` on a row: AIS tracks the assignment already spent on
    other contacts.** The isolation term is a hedge — "a live transmitting track
    is right here, so perhaps our association missed it" — and it is the right
    hedge for a lone SAR contact. It is wrong when the global assignment has
    *already* attached that track to a different contact at the same instant:
    counting it again lets one AIS track explain two contacts, which is exactly
    the double-assignment error the Hungarian solver exists to prevent,
    reappearing one stage later in the scoring.

    Measured on the radar picture: R3's inshore transfer has a dark coaster
    lying 41 metres from a fishing boat with a working transponder. Two hulls
    alongside is the *signature*, and the isolation term read it as evidence
    against darkness — halving the score and suppressing the one contact the
    scenario exists to produce.

    **`require_excess_contacts`: the product's own sentence, enforced by
    counting.** The claim being made is "that contact is on radar and *nothing
    is broadcasting there*". Association alone cannot carry it once AIS is
    sparse: measured on the radar picture, twelve of fifteen false positives
    were ordinary merchants **at anchor**, whose own AIS was arriving from a few
    hundred metres away throughout the supposed dark period. They failed to
    *associate* — an anchored ship lands one AIS report every fifty minutes and
    the prediction cone between receipts opens to kilometres — but something was
    plainly broadcasting there, so the sentence was false.

    The robust test does not depend on getting the assignment right. In a
    neighbourhood, count the contacts and count the distinct transmitters heard.
    If there are more contacts than transmitters, at least one contact is
    unexplained *whichever* way the assignment ran; if there are not, none is.
    The row carries that difference as `excess_contacts` and this gate requires
    it to be positive.

    It is the right answer in both hard cases, which is why it is worth the
    extra column. Eight anchored ships producing eight contacts and eight AIS
    identities: nothing unexplained, all suppressed. A rendezvous — two contacts
    alongside, one transmitter: one unexplained, and the dark party survives,
    which a distance-based isolation rule cannot do because the two hulls are
    forty metres apart.

    Default off, so the SAR path is unchanged: with one look per scene and no
    second contact to count against, the question has no useful answer.
    """
    spoof_windows = spoof_windows or {}
    out = []
    # Only tracks that actually claim an identity can be inside a spoof window,
    # and `spoof_windows` is keyed by MMSI. Filtering here rather than inside
    # the loop keeps `.get(None)` from quietly matching a `None` key if one ever
    # got in.
    id_tracks = [tr for tr in tracks if getattr(tr, "mmsi", None) is not None]
    for u in unmatched:
        t_s = pd.Timestamp(u["ts"]).timestamp()
        cid = "drk_" + hashlib.sha1(u["detection_id"].encode()).hexdigest()[:12]
        h = hearable(model, u["lat"], u["lon"], t_s)
        near_static = any(
            _hav_m(u["lat"], u["lon"], s["lat"], s["lon"]) <= static_radius_m
            for s in statics)
        # AIS tracks the assignment already spent on other contacts at this
        # time — excluded from the isolation term. See the docstring.
        spent = u.get("exclude_track_ids") or ()
        d_track = min((_hav_m(u["lat"], u["lon"], *tr.state_at(t_s).latlon)
                       for tr in tracks
                       if tr.track_id not in spent
                       and (abs(tr.t_last - t_s) < 48 * 3600
                            or tr.t_first <= t_s <= tr.t_last)),
                      default=float("inf"))

        near_spoof = False
        for tr in id_tracks:
            wins = spoof_windows.get(tr.mmsi)
            if not wins or not any(w0 <= t_s <= w1 for w0, w1 in wins):
                continue
            if _hav_m(u["lat"], u["lon"], *tr.state_at(t_s).latlon) \
                    <= SPOOF_AMBIGUITY_RADIUS_M:
                near_spoof = True
                break

        n_looks = int(u.get("n_looks") or 1)
        length = u.get("length_m")
        # static check FIRST: a rig is a rig regardless of AIS coverage,
        # and the analyst-facing suppression reason should say so
        if near_spoof:
            status, score = "suppressed_spoof_ambiguity", 0.0
        elif near_static:
            status, score = "suppressed_static", 0.0
        elif require_excess_contacts and int(u.get("excess_contacts") or 0) <= 0:
            # More transmitters heard here than contacts seen: nothing is
            # unexplained, whichever way the assignment ran.
            status, score = "suppressed_not_isolated", 0.0
        elif n_looks < min_looks:
            # Not enough observations to believe anything is there. A separate
            # verdict from suppressed_size on purpose: "we saw it and it was too
            # small to call" and "we barely saw it at all" are different answers
            # to "why is this not dark", and an analyst wants the right one.
            status, score = "suppressed_transient", 0.0
        elif h < 0.5:
            status, score = "suppressed_coverage", 0.0
        elif length is None:
            # A sensor that gives no size at all cannot clear a size floor.
            # Saying so beats crashing on the comparison, and beats waving it
            # through — the floor exists because below it we cannot tell a boat
            # from clutter, and "no estimate" is not evidence of being above it.
            status, score = "suppressed_size", 0.0
        elif length < DARK_MIN_LENGTH_M:
            status, score = "suppressed_size", 0.0
        else:
            size_f = min(1.0, (length - DARK_MIN_LENGTH_M) / 20.0 + 0.5)
            iso_f = min(1.0, d_track / 5000.0 + 0.5) if math.isfinite(d_track) else 1.0
            score = float(u.get("score", 0.8)) * h * size_f * iso_f
            status = "dark_candidate" if score >= DARK_SCORE_THRESHOLD \
                else "suppressed_score"

        out.append(dict(
            candidate_id=cid, detection_id=u["detection_id"],
            scene_id=u["scene_id"], ts=pd.Timestamp(u["ts"]),
            lat=u["lat"], lon=u["lon"], length_m=length,
            status=status, dark_score=round(score, 4),
            hearable_conf=round(h, 4),
            nearest_track_m=(round(d_track, 1) if math.isfinite(d_track)
                             else float("nan")),
            h3_cell=tiling.cell(u["lat"], u["lon"])))
    return out
