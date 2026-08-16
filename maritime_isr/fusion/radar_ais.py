"""Radar↔AIS correlation, and the dark contacts that fall out of it. ADR-028.

**The matching problem is the one already solved.** A radar plot is a position
at a time with a size estimate and no identity, which is structurally what a SAR
contact is. So this module does not implement matching: it slices the radar
picture into *epochs*, hands each epoch to `associate_scene` — the same
Hungarian global assignment, the same uncertainty-cone gate, the same score
floor — and then aggregates the per-epoch verdicts back onto whole radar tracks.

Why that shape rather than matching whole tracks to whole tracks:

  * **Global assignment must happen across the picture, not per contact.** That
    is the one banned pattern in this codebase (CLAUDE.md §6): matching each
    contact to its nearest track double-assigns and manufactures phantom dark
    vessels. An epoch is exactly the unit the Hungarian solver needs — every
    contact visible at one instant, competing for every live AIS track at once.
  * **A radar track is not one answer.** The interesting case is a track that
    correlates and then stops, because that is a transponder being switched off
    with a witness. Track-to-track matching returns one verdict per pair and
    cannot express it. Epoch-by-epoch, the verdict is a *time series*, and the
    transition in it is the product.

The aggregation is deliberately blunt: a radar track is explained by the AIS
track that wins a clear majority of its gated epochs, and the run of epochs
after that support ends is the dark period. Blunt survives fragmentation, which
the picture is full of — a coastal network hands a hull from station to station
and every handover is a new track number.

**Everything unexplained then goes through the existing dark cascade**, not a
radar-specific one. That is the architectural claim being tested: the coverage
check, the static-object layer, the size floor and the scoring are the same code
the SAR path uses, called with the sensor's own parameters.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from ..config import (RADAR_CORRELATION_EPOCH_S, RADAR_DARK_MIN_EPOCHS,
                      RADAR_DARK_MIN_MINUTES, RADAR_MIN_LOOKS,
                      RADAR_CENSUS_WINDOW_EPOCHS,
                      RADAR_NEIGHBOURHOOD_RES, RADAR_STATIC_MIN_SCENES,
                      RADAR_STATIC_RADIUS_M,
                      RADAR_STATIC_RES, RADAR_SUPPORT_AMBIGUOUS,
                      RADAR_SUPPORT_CORRELATED)
from .. import h3util as tiling
from ..tracks.coverage import CoverageModel
from .associate import associate_scene
from .dark import build_static_layer, dark_cascade

__all__ = ["correlate_radar", "RadarCorrelationResult"]

#: Radar length comes from radar cross-section, which fluctuates several dB per
#: look. The relative uncertainty on a single plot's length estimate is
#: therefore large; averaged over a long track it is much smaller. This is the
#: single-look figure, handed to the association gate on the row so the length
#: term is weighted for what it actually is.
RADAR_LENGTH_REL_SIGMA = 0.55


def _epoch_key(ts, t0: float) -> int:
    return int((pd.Timestamp(ts).timestamp() - t0) // RADAR_CORRELATION_EPOCH_S)


class RadarCorrelationResult(dict):
    """`correlate_radar`'s return value. A dict, with names for the parts."""

    @property
    def correlations(self) -> list[dict]:
        return self["correlations"]

    @property
    def verdicts(self) -> list[dict]:
        return self["verdicts"]

    @property
    def statics(self) -> list[dict]:
        return self["statics"]


def correlate_radar(radar_tracks: list, ais_tracks: list,
                    model: CoverageModel, registry: dict[int, float],
                    *, spoof_events: list[dict] | None = None,
                    ais_gaps: list[dict] | None = None,
                    ) -> RadarCorrelationResult:
    """Explain every radar track by an AIS track, or fail to and say so.

    `radar_tracks` / `ais_tracks` are `BuiltTrack`s from the shared track
    engine. `model` must be the coverage model fitted on **AIS**: the question
    the dark cascade asks is "would we have heard a transmitter here", and a
    model fitted on radar answers "does our radar reach here", which is a
    different question with the same shape.

    **`ais_gaps` are Phase 2's classified gap rows, and passing them is not
    optional in any deployment that wants precision.** A radar track that
    correlates and then stops correlating has two possible explanations, and
    only one of them is a finding:

      * her transponder stopped — the dark vessel; or
      * she is still transmitting and we simply stopped *hearing* her.

    The second is the common case and it looked exactly like the first.
    Measured on the synthetic picture: twelve of fifteen false positives were
    vessels **at anchor**. A ship at anchor transmits every three minutes under
    M.1371 and a shore receiver lands roughly one of those every fifty minutes;
    the radar sees her every five. So the prediction cone opens to kilometres
    between AIS receipts, the association declines to claim the match — rightly,
    a contact inside a 5 km cone in a crowded anchorage is not diagnostic — and
    a perfectly ordinary anchored merchant is announced as having gone dark.

    The gap classifier already answers this question and the radar path was not
    asking it. A correlated track's dark run is only a shutdown if the AIS side
    carries an `INTENTIONAL_SILENCE` gap across it; a `COVERAGE_GAP`, a
    `SAT_PASS_GAP`, or no gap at all means the silence is ours, not hers.
    Without `ais_gaps` this check cannot run and every correlated dropout is
    treated as a shutdown, which is the permissive behaviour and is stated here
    rather than left to be discovered.
    """
    if not radar_tracks:
        return RadarCorrelationResult(correlations=[], verdicts=[], statics=[],
                                      epochs=0, plots=0)

    by_track = {tr.track_id: tr for tr in radar_tracks}
    t0 = min(tr.t_first for tr in radar_tracks)
    gaps_by_ais: dict[str, list[dict]] = defaultdict(list)
    for g in (ais_gaps or []):
        gaps_by_ais[g["track_id"]].append(g)

    # ---- 1. slice the picture into epochs ---------------------------------
    # One representative plot per radar track per epoch: the plot nearest the
    # epoch centre. A track reporting every five minutes contributes three plots
    # to a fifteen-minute epoch and they say the same thing; associating all
    # three would triple the solver's work and let one track compete with itself
    # for the same AIS candidate.
    epochs: dict[int, list[dict]] = defaultdict(list)
    contact_pos: dict[tuple[str, int], tuple[float, float]] = {}
    n_plots = 0
    for tr in radar_tracks:
        pts = tr.points[tr.points.quality != "outlier"]
        n_plots += len(pts)
        chosen: dict[int, tuple[float, dict]] = {}
        for r in pts.itertuples():
            ts = pd.Timestamp(r.ts).timestamp()
            k = _epoch_key(r.ts, t0)
            centre = t0 + (k + 0.5) * RADAR_CORRELATION_EPOCH_S
            d = abs(ts - centre)
            if k in chosen and chosen[k][0] <= d:
                continue
            chosen[k] = (d, dict(
                detection_id=f"{tr.track_id}@{k}",
                track_id=tr.track_id,
                lat=float(r.lat), lon=float(r.lon), ts=r.ts,
                length_m=(float(r.length_est_m)
                          if getattr(r, "length_est_m", None) is not None
                          and np.isfinite(r.length_est_m) else None),
                # The sensor's own accuracy for this observation, travelling to
                # the gate on the row. See fusion.associate._sigma_of.
                position_sigma_m=(float(r.sigma_m)
                                  if np.isfinite(r.sigma_m) else None),
                length_rel_sigma=RADAR_LENGTH_REL_SIGMA,
                score=0.85))
        for k, (_d, c) in chosen.items():
            epochs[k].append(c)
            # Where THIS track was in THIS epoch. The census below has to be
            # taken at the contact's own position epoch by epoch: evaluating it
            # at the dark run's midpoint instead was a real defect — a vessel
            # under way covers 70 nm in a long dark run, so for most of it the
            # midpoint is tens of kilometres from where she actually was, and
            # the census counted whatever traffic happened to be at the
            # midpoint instead. It suppressed four of the eight findable
            # episodes, including the narrative spine's.
            contact_pos[(tr.track_id, k)] = (c["lat"], c["lon"])

    # ---- 2. associate each epoch, globally --------------------------------
    # `associate_scene` is called unmodified. The "scene" is a radar sweep and
    # the "contacts" are the tracks visible in it; nothing about the function
    # knows or needs to know which sensor produced them.
    per_track: dict[str, list[tuple[int, float, str | None, int | None, float]]] = \
        defaultdict(list)
    #: epoch -> AIS track ids the assignment spent in that epoch. Handed to the
    #: dark cascade so its isolation term cannot re-use a track that already
    #: explains a different contact. See `dark_cascade`.
    spent_by_epoch: dict[int, set[str]] = defaultdict(set)
    for k in sorted(epochs):
        contacts = epochs[k]
        ts = min(pd.Timestamp(c["ts"]) for c in contacts)
        scene = dict(scene_id=f"radar-epoch-{k}", ts=ts, detections=contacts)
        for a in associate_scene(scene, ais_tracks, registry):
            rtid = a["detection_id"].rsplit("@", 1)[0]
            if a["status"] != "unmatched" and a.get("track_id"):
                spent_by_epoch[k].add(a["track_id"])
            per_track[rtid].append((
                k,
                pd.Timestamp(a["ts"]).timestamp(),
                a["track_id"] if a["status"] != "unmatched" else None,
                a["mmsi"] if a["status"] != "unmatched" else None,
                a["position_error_m"] if a["status"] != "unmatched"
                else float("nan")))

    # ---- 2b. the neighbourhood census -------------------------------------
    # Per (H3 res-6 cell, epoch): how many radar contacts were seen, and how
    # many distinct AIS identities were actually HEARD. Both are hash joins on
    # the shared grid — the join CLAUDE.md §3 says this architecture exists to
    # make cheap — rather than a distance sweep over every pair.
    #
    # AIS *receipts*, not predicted track positions: the claim being tested is
    # "nothing is broadcasting there", and only a receipt is evidence of a
    # broadcast. A predicted position is evidence about our filter.
    contacts_in: dict[tuple[str, int], set[str]] = defaultdict(set)
    for k, cs in epochs.items():
        for c in cs:
            contacts_in[(tiling.cell(c["lat"], c["lon"],
                                     RADAR_NEIGHBOURHOOD_RES), k)].add(
                c["track_id"])
    heard_in: dict[tuple[str, int], set[str]] = defaultdict(set)
    for tr in ais_tracks:
        pts = tr.points[tr.points.quality != "outlier"]
        for r in pts.itertuples():
            heard_in[(tiling.cell(r.lat, r.lon, RADAR_NEIGHBOURHOOD_RES),
                      _epoch_key(r.ts, t0))].add(tr.track_id)

    def _census(lat: float, lon: float, k: int) -> tuple[int, int]:
        """(contacts, broadcasters) in this cell and its ring, this epoch."""
        cell = tiling.cell(lat, lon, RADAR_NEIGHBOURHOOD_RES)
        cells = [cell, *tiling.neighbors(cell, 1)]
        seen: set[str] = set()
        heard: set[str] = set()
        w = RADAR_CENSUS_WINDOW_EPOCHS
        for c in cells:
            seen |= contacts_in.get((c, k), set())
            # Broadcasters over a WIDER time window than contacts — see
            # RADAR_CENSUS_WINDOW_EPOCHS. An anchored ship is heard once an
            # hour and seen every five minutes.
            for kk in range(k - w, k + w + 1):
                heard |= heard_in.get((c, kk), set())
        return len(seen), len(heard)

    # ---- 3. aggregate onto whole radar tracks -----------------------------
    correlations: list[dict] = []
    dark_rows: list[dict] = []
    unmatched_plots: list[dict] = []

    for tid, tr in sorted(by_track.items()):
        seq = sorted(per_track.get(tid, []))
        n_epochs = len(seq)
        votes = Counter(m for _k, _t, m, _mm, _e in seq if m)
        best_ais, support = None, 0.0
        if votes and n_epochs:
            best_ais, n_best = votes.most_common(1)[0]
            support = n_best / n_epochs
        mmsi = next((mm for _k, _t, m, mm, _e in seq if m == best_ais), None)
        errs = [e for _k, _t, m, _mm, e in seq
                if m == best_ais and e is not None and math.isfinite(e)]

        # Every epoch nothing on AIS explained. These feed the static-object
        # layer — the SAR path feeds it every unmatched contact, and the
        # analogue here is every unmatched epoch, not only the ones inside a
        # declared dark period. A mooring buoy near a working berth is
        # intermittently gated against passing ships and comes out `ambiguous`;
        # if only dark-run plots were fed, the layer would never see it and
        # every one of its unmatched hours would reach the queue.
        unmatched_keys = {k for k, _t, m, _mm, _e in seq if m is None}

        # The dark run: the LONGEST run of consecutive unmatched epochs.
        #
        # **Not the trailing run, which is what this did first and which lost
        # real detections.** A tail is the right shape for the headline story —
        # she was explained, then she was not, and she stayed unexplained — but
        # it is not the only shape. A track that is unexplained for four hours
        # in the middle and picks up an AIS match again at the end produced an
        # empty tail, therefore no dark row, therefore nothing, while its own
        # status said `dark`. Measured: two of the eight findable episodes
        # vanished exactly that way, including one of the narrative spine's.
        #
        # The longest run covers both shapes. Which shape it was is recorded
        # separately, in `went_dark_at`, and only claimed when the track really
        # was correlated first.
        runs: list[list[float]] = []
        cur: list[float] = []
        for _k, t_e, m, _mm, _err in seq:
            if m is None:
                cur.append(t_e)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        dark_run = max(runs, key=len) if runs else []
        is_tail = bool(dark_run) and dark_run[-1] == seq[-1][1]

        length_est = _median_length(tr)
        station_ids = _stations_of(tr)

        status = _status_of(support, dark_run, n_epochs)

        # A correlated track's dark run only counts as a shutdown if the AIS
        # side agrees it was one. See the function docstring.
        ais_gap_type = None
        if status == "correlated_then_dark":
            ais_gap_type = _gap_type_over(
                gaps_by_ais.get(best_ais, []), dark_run[0], dark_run[-1])
            # **Only an explicit non-intentional label suppresses.** `None`
            # means the AIS gap classifier had nothing to say about this
            # interval — most often because her AIS track simply *ended*, which
            # is what a transponder being switched off looks like from the AIS
            # side and is the case we most want to keep.
            #
            # The distinction is load-bearing right now for an unhappy reason:
            # on this corpus the classifier emits 26,778 gaps and **every one
            # of them is COVERAGE_GAP**. `INTENTIONAL_SILENCE` requires two
            # completed satellite passes across the gap and there is no
            # satellite-AIS schedule in this corpus at all, so that branch is
            # unreachable — a defect that predates this work and is recorded in
            # STATE.md. Treating `None` as suppressing would therefore have made
            # this check a pure suppressor, capable of hiding a real shutdown
            # and incapable of ever confirming one.
            if ais_gap_type is not None \
                    and ais_gap_type != "INTENTIONAL_SILENCE":
                status = "correlated_gap_explained"

        went_dark_at = went_dark_lat = went_dark_lon = None
        if dark_run and best_ais is not None and status == "correlated_then_dark":
            # The last epoch that WAS explained before the dark run began: this
            # is where the transponder stopped. Claimed **only** when the track
            # really was correlated first — otherwise "went dark at" would be
            # asserted about a contact nothing ever explained, which is a
            # sentence the evidence does not support.
            before = [t_e for _k, t_e, m, _mm, _e in seq
                      if m is not None and t_e < dark_run[0]]
            if before:
                last_ok = max(before)
                went_dark_at = pd.Timestamp(last_ok, unit="s", tz="UTC")
                st = tr.state_at(last_ok)
                went_dark_lat, went_dark_lon = st.latlon

        dark_from = dark_to = None
        rep_lat = rep_lon = None
        dark_minutes = 0.0
        if dark_run:
            dark_from = pd.Timestamp(dark_run[0], unit="s", tz="UTC")
            dark_to = pd.Timestamp(dark_run[-1], unit="s", tz="UTC")
            dark_minutes = (dark_run[-1] - dark_run[0]) / 60.0
            mid_t = dark_run[len(dark_run) // 2]
            rep_lat, rep_lon = tr.state_at(mid_t).latlon

        cid = "rcx_" + hashlib.sha1(tid.encode()).hexdigest()[:12]
        correlations.append(dict(
            correlation_id=cid,
            radar_track_id=tr.track_key,
            track_id=tid,
            station_ids=station_ids,
            t_start=pd.Timestamp(tr.t_first, unit="s", tz="UTC"),
            t_end=pd.Timestamp(tr.t_last, unit="s", tz="UTC"),
            n_epochs=n_epochs,
            n_matched=sum(1 for _k, _t, m, _mm, _e in seq if m),
            status=status,
            ais_track_id=best_ais, mmsi=mmsi,
            support=round(support, 4),
            mean_position_error_m=(round(float(np.mean(errs)), 1) if errs
                                   else float("nan")),
            length_est_m=(round(length_est, 1) if length_est is not None
                          else None),
            went_dark_at=went_dark_at,
            went_dark_lat=(round(went_dark_lat, 5)
                           if went_dark_lat is not None else None),
            went_dark_lon=(round(went_dark_lon, 5)
                           if went_dark_lon is not None else None),
            dark_from=dark_from, dark_to=dark_to,
            lat=(round(rep_lat, 5) if rep_lat is not None else None),
            lon=(round(rep_lon, 5) if rep_lon is not None else None),
            dark_is_tail=is_tail,
            h3_cell=(tiling.cell(rep_lat, rep_lon)
                     if rep_lat is not None else None),
            n_plots=int(len(tr.points)),
            dark_minutes=round(dark_minutes, 1),
            # What the AIS side says about the same interval. Carried onto the
            # row because "we stopped hearing her, and her own gap classifier
            # calls that a coverage hole" is the evidence for NOT raising a
            # contact, and a suppression an analyst cannot see is a suppression
            # they cannot trust.
            ais_gap_type=ais_gap_type,
        ))

        # This is the static layer's first real input in the project's history:
        # a mooring buoy reported every quarter of an hour for eight weeks is
        # exactly the recurrence it was built to accumulate, and the SAR corpus
        # holds six contacts in total.
        if unmatched_keys:
            for r in tr.points[tr.points.quality != "outlier"].itertuples():
                t_p = pd.Timestamp(r.ts).timestamp()
                if _epoch_key(r.ts, t0) not in unmatched_keys:
                    continue
                unmatched_plots.append(dict(
                    detection_id=f"{tid}#{int(t_p)}",
                    # The "scene" for a continuously-scanning sensor is a day.
                    # A fixed object appears on essentially every one of them;
                    # a ship on a lane appears on the days it sails, which is
                    # what `RADAR_STATIC_MIN_SCENES` separates.
                    scene_id=f"radar-day-{pd.Timestamp(r.ts):%Y%m%d}",
                    ts=r.ts, lat=float(r.lat), lon=float(r.lon),
                    length_m=length_est))

        if status not in ("dark", "correlated_then_dark"):
            continue
        if not dark_run:
            continue
        dark_keys = [k for k, _t, m, _mm, _e in seq
                     if m is None and dark_run[0] <= _t <= dark_run[-1]]
        spent: set[str] = set()
        for k in dark_keys:
            spent |= spent_by_epoch.get(k, set())

        # The census over the dark run, taken at the median epoch so a single
        # odd moment cannot decide it.
        census = []
        for k in dark_keys:
            pos = contact_pos.get((tid, k))
            if pos is None:
                continue
            n_c, n_h = _census(pos[0], pos[1], k)
            census.append(n_c - n_h)
        excess = int(np.median(census)) if census else 0
        dark_rows.append(dict(
            detection_id=cid,
            scene_id=f"radar:{station_ids}",
            ts=pd.Timestamp(dark_run[len(dark_run) // 2], unit="s", tz="UTC"),
            lat=rep_lat, lon=rep_lon,
            length_m=length_est,
            exclude_track_ids=spent,
            excess_contacts=excess,
            # Persistence, in the cascade's terms: how many independent looks
            # this contact rests on. Sea clutter rests on two or three.
            n_looks=int(len(dark_run)),
            score=0.85,
            _correlation_id=cid, _dark_minutes=dark_minutes))

    # ---- 4. the existing cascade, with the sensor's parameters ------------
    statics = build_static_layer(unmatched_plots,
                                 radius_m=RADAR_STATIC_RADIUS_M,
                                 res=RADAR_STATIC_RES,
                                 min_scenes=RADAR_STATIC_MIN_SCENES)
    spoof_windows: dict[int, list[tuple[float, float]]] = {}
    for ev in (spoof_events or []):
        if ev.get("event_type") == "DUPLICATE_MMSI":
            spoof_windows.setdefault(ev["mmsi"], []).append(
                (ev["t_start"].timestamp(), ev["t_end"].timestamp()))

    # Two pre-cascade filters, both expressed as `n_looks` so the cascade
    # records them as `suppressed_transient` rather than dropping them silently:
    # a contact must rest on enough looks AND span enough time. A fast-moving
    # clutter return can rack up looks in four minutes; a ship cannot be dark
    # for four minutes in any sense an analyst cares about.
    for row in dark_rows:
        if row.pop("_dark_minutes") < RADAR_DARK_MIN_MINUTES:
            row["n_looks"] = 0

    verdicts = dark_cascade(dark_rows, model, statics, ais_tracks,
                            spoof_windows,
                            static_radius_m=RADAR_STATIC_RADIUS_M,
                            min_looks=max(RADAR_MIN_LOOKS,
                                          RADAR_DARK_MIN_EPOCHS),
                            require_excess_contacts=True)

    # Carry the correlation id onto the verdict so a dark contact can be traced
    # back to the radar track that produced it — the evidence chain is the
    # product, and a verdict that cannot name its own track is not evidence.
    by_det = {r["detection_id"]: r for r in dark_rows}
    for v in verdicts:
        src = by_det.get(v["detection_id"])
        if src:
            v["correlation_id"] = src["_correlation_id"]

    return RadarCorrelationResult(
        correlations=correlations, verdicts=verdicts, statics=statics,
        epochs=len(epochs), plots=n_plots)


def _gap_type_over(gaps: list[dict], t0: float, t1: float) -> str | None:
    """How the AIS gap classifier labelled the interval this radar track was
    unexplained over. `None` when the AIS track has no gap there at all — which
    means she was reporting normally and our correlation, not her transponder,
    is what stopped.

    Where several gaps overlap, `INTENTIONAL_SILENCE` wins: a shutdown that
    happens to straddle a coverage hole is still a shutdown.
    """
    best = None
    for g in gaps:
        g0 = pd.Timestamp(g["t_start"]).timestamp()
        g1 = pd.Timestamp(g["t_end"]).timestamp()
        if g0 > t1 or g1 < t0:
            continue
        if g.get("gap_type") == "INTENTIONAL_SILENCE":
            return "INTENTIONAL_SILENCE"
        best = best or g.get("gap_type")
    return best


def _status_of(support: float, dark_run: list, n_epochs: int) -> str:
    """One word for what happened to this radar track.

    **A dark tail must be long enough to mean something.** Measured on the
    synthetic picture before this rule existed: 28 tracks came back
    `correlated_then_dark`, and 21 of them had dark runs of 0-30 minutes — one
    or two epochs at the very end of the track. That is not a transponder being
    switched off, it is a track *ending*: the target leaves the station's cover
    and the last epoch or two fall outside the association gate as the AIS
    track's own last report ages. Every ragged track ending was being announced
    as a vessel going dark, which would have put twenty-eight of them in front
    of an analyst on the strength of the sensor losing interest.
    """
    if n_epochs == 0:
        return "transient"
    real_dark = len(dark_run) >= RADAR_DARK_MIN_EPOCHS
    if support >= RADAR_SUPPORT_CORRELATED:
        return "correlated_then_dark" if real_dark else "correlated"
    if support >= RADAR_SUPPORT_AMBIGUOUS:
        # Something on AIS is nearby often enough to be a candidate and not
        # often enough to be the answer. Precision-first says do not convict:
        # an ambiguous track is neither correlated nor dark, and it is reported
        # as its own state so an analyst can look rather than being told.
        return "ambiguous"
    return "dark"


def _median_length(tr) -> float | None:
    """The track's size estimate: median over its plots.

    **The median is doing real work here.** A single plot's length comes from a
    cross-section that fluctuates several dB, which is a factor of 1.5 or so in
    length — worth a size class and no more. Over eighty looks the fluctuation
    averages down and the estimate becomes good enough to put against a 20 m
    floor. Persistence buys size accuracy, which is the one thing radar has
    that a single SAR look does not.
    """
    if "length_est_m" not in tr.points.columns:
        return None
    v = pd.to_numeric(tr.points["length_est_m"], errors="coerce").dropna()
    return float(v.median()) if len(v) else None


def _stations_of(tr) -> str:
    if "station_id" not in tr.points.columns:
        return ""
    return ",".join(sorted({str(s) for s in tr.points["station_id"].dropna()
                            if str(s)}))


def format_correlation(res: RadarCorrelationResult) -> str:
    counts = Counter(c["status"] for c in res.correlations)
    lines = ["radar ↔ AIS correlation (SYNTHETIC)"]
    lines.append(f"  radar tracks     : {len(res.correlations):,}")
    lines.append(f"  epochs           : {res['epochs']:,} "
                 f"({RADAR_CORRELATION_EPOCH_S / 60:.0f} min each)")
    lines.append(f"  plots            : {res['plots']:,}")
    for k in ("correlated", "correlated_then_dark", "correlated_gap_explained",
              "ambiguous", "dark", "transient"):
        lines.append(f"    {k:<22}{counts.get(k, 0):>8,}")
    lines.append(f"  static objects   : {len(res.statics):,}")
    vc = Counter(v["status"] for v in res.verdicts)
    lines.append(f"  dark verdicts    : {len(res.verdicts):,}  {dict(vc)}")
    return "\n".join(lines)
