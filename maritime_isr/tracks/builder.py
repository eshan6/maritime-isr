"""Track builder (roadmap 2.1): segment raw position reports into per-target
tracks and survive the real-world filth.

Mechanism: multi-hypothesis assignment per grouping key, not filtering.
  - Each report joins the live hypothesis it can physically belong to
    (implied speed ≤ HYPOTHESIS_SPEED_GATE_KN against the hypothesis's
    predicted position), else it spawns a new hypothesis.
  - Two hypotheses receiving reports in overlapping time ⇒ DUPLICATE_MMSI
    spoof event. Logged, both tracks kept — the tell is the product.
  - A lone impossible jump becomes a singleton hypothesis; hypotheses that
    never accumulate MIN_REAL_POINTS are demoted to outlier points and
    attached (quality='outlier') to the main track. Nothing is dropped.
  - Silence past the source's reuse guard closes the track; the next report under the
    same key starts a new track_id with `fragmented_from` lineage — the
    MMSI-reuse guard. Identity continuity across breaks is a Phase 4 call,
    not a Phase 2 assumption.

**The grouping key is a parameter now, and the spoof rule is gated on what it
means (ADR-028).** This module was written against AIS and said so in the only
way that matters: it grouped on a column literally named `mmsi` and treated a
collision on it as evidence about a vessel. Coastal radar produces the same
shape of data — position, course, speed, at a cadence — keyed by a station's
track number, which is a *slot in a track table* and not a name. Two radar
tracks sharing a number is a station reusing a slot; calling that a spoofing
tell would manufacture an identity finding out of a housekeeping detail.

So the key comes from a `TrackSource` descriptor, and DUPLICATE_MMSI is emitted
only when `key_is_identity`. Nothing here branches on the source's *name*: the
question asked is what the key means, which stays answerable when the third
sensor arrives.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import HYPOTHESIS_SPEED_GATE_KN, PIPELINE_VERSION
from .. import h3util as tiling
from ..schemas.sources import AIS, TrackSource
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
    #: The sensor's own grouping key as text — MMSI for AIS, station track
    #: number for radar. **Always present.** Anything that needs "is this the
    #: same target?" must use this and not `mmsi`, which is null for a sensor
    #: that observes no identity.
    track_key: str
    #: Which sensor produced this track. Carried so a downstream consumer can
    #: ask the descriptor what the data means rather than guess.
    source: TrackSource
    #: The broadcast identity, when there is one. `None` on radar tracks.
    mmsi: int | None
    hypothesis: int
    points: pd.DataFrame          # smoothed, with sigma_m + quality
    states: list[TrackState]
    median_report_s: float
    n_outliers: int
    fragmented_from: str | None = None

    def __post_init__(self) -> None:
        # Caches. Recomputed nowhere else, because every one of these was being
        # recomputed per gate call: `associate_scene` asks `_gate` for every
        # (contact, track) pair, and a radar correlation run makes millions of
        # those. `tr.points["ts"].max()` is a pandas reduction over the whole
        # frame and `state_at` was rebuilding an array over every state on each
        # call — together they dominated the runtime by two orders of magnitude.
        # Tracks are immutable after construction, so caching is safe; the one
        # place that mutates `points` afterwards (outlier attachment, below)
        # calls `refresh_cache`.
        self.refresh_cache()

    def refresh_cache(self) -> None:
        ts = self.points["ts"]
        self._t_first = float(pd.Timestamp(ts.min()).timestamp())
        self._t_last = float(pd.Timestamp(ts.max()).timestamp())
        self._state_epochs = np.array([s.t for s in self.states], float)
        self._pt_epochs = np.sort(epoch_s(ts))

    @property
    def t_first(self) -> float:
        return self._t_first

    @property
    def t_last(self) -> float:
        return self._t_last

    @property
    def has_identity(self) -> bool:
        """Does this track claim to know who it is? Radar tracks do not."""
        return self.source.key_is_identity and self.mmsi is not None

    def state_at(self, t_epoch: float) -> TrackState:
        """Nearest-preceding smoothed state, predicted forward — the query
        Phase 3 gating will hammer."""
        i = int(np.searchsorted(self._state_epochs, t_epoch, side="right")) - 1
        i = max(0, min(i, len(self.states) - 1))
        return self.states[i].predict(max(t_epoch, self.states[i].t))


def _track_id(key, hid: int, t0: float, source_name: str = AIS.name) -> str:
    """Deterministic track id.

    The source name is folded in so an AIS track and a radar track can never
    collide on a numerically equal key — station `MUM-1` numbering a track `7`
    and MMSI `7` are different things and a shared id would silently merge them.
    **AIS ids are left byte-identical to what they were**: including the source
    unconditionally would have changed every existing track id in the corpus and
    broken determinism comparisons for no benefit.
    """
    prefix = "" if source_name == AIS.name else f"{source_name}|"
    return "trk_" + hashlib.sha1(
        f"{prefix}{key}|{hid}|{t0:.0f}".encode()).hexdigest()[:12]


def build_tracks(df: pd.DataFrame, *, source: TrackSource = AIS
                 ) -> tuple[list[BuiltTrack], list[dict]]:
    """df: conformed position rows for one sensor.

    For AIS: `ais_position` rows (mmsi, lat, lon, sog_kn, cog_deg, ts, ...).
    For radar: `radar_track_report` rows (radar_track_id, lat, lon, sog_kn,
    cog_deg, ts, position_sigma_m, ...).

    Returns (tracks, spoof_events). `spoof_events` is always empty for a source
    whose key is not an identity — see the module docstring.
    """
    tracks: list[BuiltTrack] = []
    spoof_events: list[dict] = []
    key_field = source.key_field
    if key_field not in df.columns:
        raise KeyError(
            f"{source.name} tracks are grouped on {key_field!r}, which is not "
            f"in the frame (columns: {sorted(df.columns)[:12]}...). A source's "
            f"key column is declared in schemas.sources, not guessed here.")
    df = df.copy()
    df["t"] = epoch_s(df["ts"])
    # The reuse guard, in seconds, from the sensor rather than from a constant.
    # See TrackSource.track_break_s for what happens when AIS's seven days are
    # applied to a track number a station recycles every few minutes.
    break_s = source.track_break_s

    for key, g in df.sort_values("t").groupby(key_field):
        # The broadcast identity, when the key IS one. A radar track keeps
        # mmsi=None all the way through, and every consumer is required to cope
        # (schemas.sources explains why that is not an omission).
        mmsi = int(key) if source.key_is_identity else None
        hyps: list[_Hypothesis] = []
        next_hid = 0
        cols = ["t", "lat", "lon", "sog_kn", "cog_deg", "ts"]
        for extra in ("receiver", "position_sigma_m", *source.carry_columns):
            if extra in g.columns and extra not in cols:
                cols.append(extra)
        for row in g[cols].to_dict("records"):
            # close hypotheses silent past the reuse guard
            live = [h for h in hyps
                    if row["t"] - h.t_last < break_s]
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
        #
        # **Only for a key that is an identity.** Two co-living hypotheses under
        # one MMSI means two transmitters claiming to be one ship. Two co-living
        # hypotheses under one radar track number means a station reused a slot,
        # or two stations' numbering happened to meet — a fact about the sensor,
        # not about any vessel. Emitting DUPLICATE_MMSI there would put an
        # identity finding in the alert queue for a housekeeping detail, and the
        # `mmsi` column of the row would have to hold a track number to do it.
        real = [h for h in hyps if len(h.rows) >= MIN_REAL_POINTS]
        if source.key_is_identity:
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
                            track_ids=f"{_track_id(key, a.hid, a.t_first, source.name)},"
                                      f"{_track_id(key, b.hid, b.t_first, source.name)}",
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
                    if main is not None and source.key_is_identity:
                        spoof_events.append(dict(
                            mmsi=int(mmsi), event_type="IMPOSSIBLE_KINEMATICS",
                            t_start=pd.Timestamp(r["t"], unit="s", tz="UTC"),
                            t_end=pd.Timestamp(r["t"], unit="s", tz="UTC"),
                            track_ids=_track_id(key, main.hid, main.t_first,
                                                source.name),
                            max_separation_km=float(_haversine_m(
                                r["lat"], r["lon"], main.lat, main.lon) / 1000),
                            detail="isolated report kinematically incompatible "
                                   "with every live hypothesis"))

        # --- smooth each surviving hypothesis into a BuiltTrack ---
        for h in real:
            rows = sorted(h.rows, key=lambda r: r["t"])
            t = np.array([r["t"] for r in rows])
            # split at reuse-guard breaks inside the hypothesis
            breaks = np.where(np.diff(t) > break_s)[0]
            segs, prev_tid = np.split(np.arange(len(rows)), breaks + 1), None
            for seg in segs:
                if len(seg) < MIN_REAL_POINTS:
                    continue
                srows = [rows[k] for k in seg]
                st = np.array([r["t"] for r in srows])
                lats = np.array([r["lat"] for r in srows])
                lons = np.array([r["lon"] for r in srows])
                # The sensor's own per-report accuracy when it publishes one.
                # A radar plot at 40 km is genuinely four times worse than the
                # same target at 10 km and the smoother should know that.
                sig_in = None
                if "position_sigma_m" in srows[0]:
                    sig_in = np.array([r.get("position_sigma_m") or np.nan
                                       for r in srows], float)
                states, sigma = filter_smooth(st, lats, lons, sigma_m=sig_in)
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
                # Sensor-specific per-report columns, carried through as they
                # came in. Declared on the source rather than sniffed off the
                # frame — see TrackSource.carry_columns.
                for col in source.carry_columns:
                    if col in srows[0]:
                        pts[col] = [r.get(col) for r in srows]
                # Flag noisy: raw-vs-smoothed residual beyond ~3× measurement σ.
                # The threshold follows the sensor rather than sitting at the
                # AIS figure: at 60 m, essentially every radar plot beyond
                # 15 km is "noisy", which is not a finding about the data.
                noisy_m = max(60.0, 3.0 * source.position_sigma_m)
                res = np.array([_haversine_m(lats[k], lons[k],
                                             pts.lat.iloc[k], pts.lon.iloc[k])
                                for k in range(len(srows))])
                pts.loc[res > noisy_m, "quality"] = "noisy"
                tid = _track_id(key, h.hid, st[0], source.name)
                med = float(np.median(np.diff(st))) if len(st) > 1 else 0.0
                trk = BuiltTrack(track_id=tid, track_key=str(key), source=source,
                                 mmsi=mmsi, hypothesis=h.hid,
                                 points=pts, states=states, median_report_s=med,
                                 n_outliers=0, fragmented_from=prev_tid)
                prev_tid = tid
                tracks.append(trk)

        # attach outliers to the main track's point set (kept, flagged)
        if main is not None and outlier_rows:
            mt = next((t_ for t_ in tracks
                       if t_.track_key == str(key)
                       and t_.hypothesis == main.hid), None)
            if mt is not None:
                extra = pd.DataFrame({
                    "ts": [r["ts"] for r in outlier_rows],
                    "receiver": [r.get("receiver", "") for r in outlier_rows],
                    "lat": [r["lat"] for r in outlier_rows],
                    "lon": [r["lon"] for r in outlier_rows],
                    "sog_kn": [r["sog_kn"] for r in outlier_rows],
                    "cog_deg": [r["cog_deg"] for r in outlier_rows],
                    "sigma_m": 1e6, "quality": "outlier"})
                for col in source.carry_columns:
                    if col in outlier_rows[0]:
                        extra[col] = [r.get(col) for r in outlier_rows]
                mt.points = (pd.concat([mt.points, extra])
                             .sort_values("ts").reset_index(drop=True))
                mt.n_outliers = len(outlier_rows)
                mt.refresh_cache()

    # --- lineage across key-reuse breaks --------------------------------
    # A reuse break usually spawns a NEW hypothesis (the stale one was
    # retired by the guard), so within-hypothesis splitting never sees it.
    # Link consecutive non-overlapping tracks of the same key separated by
    # more than the break threshold: identity continuity stays a Phase 4
    # decision; the lineage edge is just recorded evidence. For radar this is
    # exactly the same mechanism doing a different job — a station reusing a
    # track number gets a lineage edge rather than a merged track.
    by_key: dict[str, list[BuiltTrack]] = {}
    for tr in tracks:
        by_key.setdefault(tr.track_key, []).append(tr)
    for _key, trs in by_key.items():
        trs.sort(key=lambda tr: tr.points["ts"].min())
        for prev, nxt in zip(trs, trs[1:]):
            gap_s = (nxt.points["ts"].min()
                     - prev.points["ts"].max()).total_seconds()
            if gap_s > break_s and nxt.fragmented_from is None:
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
