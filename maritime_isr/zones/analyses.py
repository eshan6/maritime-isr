"""The four analyses the zone layer unlocks.

Each returns candidate findings with a score and an evidence chain, in the
shape `anomaly.library._emit` expects, and each is **precision-gated like every
other detector** — a zone layer that turned four unbuildable analyses into four
noisy ones would be a net loss.

The four, and the honest state of each:

1. **Area visit** — who was in this area during this window. Not an anomaly at
   all; it is a *query*, and it is here because the requirement names it
   alongside the others. It emits no alerts and is scored on completeness
   rather than precision.
2. **Maiden visit** — a vessel with no prior presence in this zone across the
   retained history. Real signal, and the one most sensitive to how much
   history there is: on eight weeks of corpus almost everything is a maiden
   visit, so the rule requires the vessel to have been *seen elsewhere* first.
   Without that it fires on every vessel's first appearance and means nothing.
3. **Lane deviation** — a vessel on a coastal passage well outside every
   established corridor. Measured on synthetic data this figure is optimistic
   by construction and the docstring says why.
4. **Anchoring outside port limits** — stopped, for hours, in water that is
   inside the territorial sea but outside any declared port limit or
   anchorage. The one of the four with the clearest operational meaning.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from ..config import (LANE_DEVIATION_MIN_KM, LANE_DEVIATION_MIN_MINUTES,
                      LANE_DEVIATION_MIN_SOG_KN, MAIDEN_MIN_NOVELTY_KM,
                      MAIDEN_MIN_PRIOR_ZONES, OUTSIDE_LIMITS_MIN_HOURS,
                      OUTSIDE_LIMITS_MAX_SOG_KN)
from .geometry import distance_to_m, haversine_m
from .store import ZoneIndex

__all__ = ["detect_area_visits", "detect_maiden_visit",
           "detect_lane_deviation", "detect_anchored_outside_port_limits",
           "anchoring_analysis_status"]


def anchoring_analysis_status(index: ZoneIndex) -> tuple[bool, str]:
    """Can the anchoring analysis run, and if not, why not — in one sentence.

    **An analysis that cannot run must say so, not return an empty list.** This
    project has found the same defect five times now under different names: a
    stage that computes nothing and looks healthy is indistinguishable from a
    stage that computed nothing because there was nothing to find. "Anchored
    outside port limits" needs a territorial sea to be *inside*, and this
    project declines to derive one (see `zones/derive.py`), so on a checkout
    where nobody has run the connector this rule is idle by construction.

    Returned as a pair rather than logged, so the pipeline prints it, the API
    returns it, and the UI can grey the analysis out with the reason attached.
    """
    if index.of_kind("territorial_sea"):
        return True, "territorial sea present"
    return False, (
        "IDLE — no territorial_sea zone is loaded. This analysis asks whether a "
        "vessel is stopped INSIDE territorial waters and OUTSIDE every "
        "facility, and this project will not derive a territorial sea from a "
        "coastline (see zones/derive.py). Load a real one with "
        "`maritime-isr ingest zones --path <file.geojson> --kind "
        "territorial_sea` and it runs unchanged.")


# --------------------------------------------------------------------------
# 1. area visit — a query, not an anomaly
# --------------------------------------------------------------------------

def detect_area_visits(transitions: Sequence[dict], *,
                       zone_ids: Optional[Iterable[str]] = None,
                       start=None, end=None) -> list[dict]:
    """Presence rows for the named zones in the named window.

    Deliberately not an alert-producing detector. "Which vessels visited this
    area" is a question an operator asks, not a judgement the system makes, and
    dressing it as an anomaly would put a hundred lawful port calls in an alert
    queue that ADR-004 spends its whole budget keeping short.
    """
    want = set(zone_ids) if zone_ids else None
    t0, t1 = _as_utc(start), _as_utc(end)
    out = []
    for r in transitions:
        if want is not None and r.get("zone_id") not in want:
            continue
        enter = pd.Timestamp(r["t_enter"])
        exit_ = pd.Timestamp(r["t_exit"]) if r.get("t_exit") is not None else None
        if t1 is not None and enter > t1:
            continue
        if t0 is not None and exit_ is not None and exit_ < t0:
            continue
        out.append(dict(r))
    return out




def _as_utc(v):
    """A UTC timestamp from whatever the caller passed.

    `pd.Timestamp(x, tz="UTC")` RAISES when `x` is already tz-aware, so a caller
    who does the obvious correct thing — hand in an aware timestamp — got a
    ValueError out of a window filter. Localise the naive case, convert the
    aware one.
    """
    if v is None:
        return None
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# --------------------------------------------------------------------------
# 2. maiden visit
# --------------------------------------------------------------------------

def detect_maiden_visit(store, transitions: Sequence[dict], *,
                        source_ref: str, index: Optional[ZoneIndex] = None,
                        kinds: Sequence[str] = ("port_limit", "anchorage",
                                                "oil_terminal",
                                                "sensitive_area", "geofence"),
                        min_prior_zones: int = MAIDEN_MIN_PRIOR_ZONES,
                        min_novelty_km: float = MAIDEN_MIN_NOVELTY_KM
                        ) -> list[str]:
    """A vessel's first ever presence in a zone — but only if she is not new.

    **The qualifier is the whole rule.** Over a retained history of eight
    weeks, the first time a vessel is seen anywhere is also the first time she
    is seen in every zone she passes through, so an unqualified "no prior
    presence" test fires on every vessel's debut and carries no information at
    all. Measured on the scenario corpus before the qualifier: 168 alerts, one
    per hull, which is a list of the fleet rather than a finding.

    Requiring `min_prior_zones` distinct zones already visited means the
    subject is a vessel we have watched working this coast, now turning up
    somewhere she has never been. That is the signal the requirement is asking
    for, and it is why the rule reads history rather than a single visit.

    **And the new place has to be somewhere genuinely else.** The prior-zones
    qualifier alone still fired 643 times, because a vessel adding the next
    berth along to her rotation satisfies it every time. `min_novelty_km` asks
    how far the new zone is from the nearest zone she has already worked; see
    `config.MAIDEN_MIN_NOVELTY_KM` for why 300 km is a structural break in the
    data rather than a fitted number.

    Without a `index` the novelty term cannot be computed and is skipped, with
    the prior-zones qualifier alone — which is weaker, and the caller gets what
    it asked for rather than a silent no-op.

    The large statutory limits are excluded by default: a first entry into the
    EEZ is what every arriving vessel does.
    """
    from ..anomaly.library import _emit
    from ..graph.identity import resolve_mmsi

    rows = [r for r in transitions if r.get("zone_kind") in set(kinds)]
    rows.sort(key=lambda r: pd.Timestamp(r["t_enter"]))

    cent: dict[str, tuple[float, float]] = {}
    if index is not None:
        for z in index.zones:
            g = index.geometry(z.zone_id)
            if g is not None and not g.is_empty:
                cent[z.zone_id] = (g.centroid.y, g.centroid.x)

    seen_zones: dict[str, set[str]] = defaultdict(set)
    out: list[str] = []
    for r in rows:
        subject_key = _subject_key(r)
        if subject_key is None:
            continue
        prior = seen_zones[subject_key]
        zid = str(r["zone_id"])
        first_here = zid not in prior
        n_prior = len(prior)
        prior.add(zid)
        if not first_here or n_prior < min_prior_zones:
            continue
        novelty_km = None
        if cent and zid in cent:
            near = [haversine_m(*cent[zid], *cent[p]) / 1000.0
                    for p in prior if p in cent and p != zid]
            if near:
                novelty_km = min(near)
                if novelty_km < min_novelty_km:
                    continue
        t = pd.Timestamp(r["t_enter"]).timestamp()
        mmsi = r.get("mmsi")
        if mmsi is None:
            # A radar contact has no identity, so "she has never been here
            # before" cannot be asserted about a hull — only about a track
            # number a station recycles. The claim is not available.
            continue
        vid = resolve_mmsi(store, int(mmsi), at=t)
        score = min(1.0, 0.55 + 0.05 * min(n_prior, 6))
        ev = [dict(edge="entered-zone", src=vid, dst=zid,
                   confidence=round(score, 3), source="zone_layer",
                   source_ref=source_ref,
                   props=dict(zone_name=r.get("zone_name"),
                              zone_kind=r.get("zone_kind"),
                              prior_zones=n_prior,
                              novelty_km=(round(novelty_km, 1)
                                          if novelty_km is not None else None),
                              dwell_min=r.get("dwell_min"),
                              entry_bearing_deg=r.get("entry_bearing_deg")))]
        _emit(out, store, "maiden_zone_visit", vid, t, score, ev,
              props=dict(zone_id=zid, zone_name=r.get("zone_name"),
                         zone_kind=r.get("zone_kind"),
                         prior_zones=n_prior,
                         novelty_km=(round(novelty_km, 1)
                                     if novelty_km is not None else None),
                         lat=r.get("entry_lat"), lon=r.get("entry_lon")))
    return out


def _subject_key(r: dict) -> Optional[str]:
    if r.get("mmsi") is not None:
        return f"mmsi:{int(r['mmsi'])}"
    k = r.get("track_key")
    return f"track:{k}" if k else None


# --------------------------------------------------------------------------
# 3. lane deviation
# --------------------------------------------------------------------------

def detect_lane_deviation(store, tracks: Sequence, index: ZoneIndex, *,
                          source_ref: str,
                          min_km: float = LANE_DEVIATION_MIN_KM,
                          min_minutes: float = LANE_DEVIATION_MIN_MINUTES,
                          min_sog_kn: float = LANE_DEVIATION_MIN_SOG_KN
                          ) -> list[str]:
    """A vessel under way, well outside every established corridor, for hours.

    **Measured on synthetic data this number is optimistic by construction, and
    that has to be said before the figure is.** The scenario generator routes
    its vessels with the same land-avoiding router the lane centrelines were
    drawn from, so generated traffic sits on the lanes almost by definition and
    the only vessels that deviate are the ones a scenario deliberately sends
    off-route. On real traffic the corridors would be wrong in ways this corpus
    cannot show — which is exactly why the corridors carry `confidence` 0.35
    and are labelled customary rather than adopted.

    Three conditions together, because any one alone is noise: far from every
    lane, **making way** (a drifting vessel is a different finding, and one the
    loitering rule already owns), and *sustained* (a single fix outside a
    corridor is position noise or a legitimate diversion round weather).
    """
    from ..anomaly.library import _emit
    from ..graph.identity import track_subject_id

    lanes = index.of_kind("shipping_lane")
    if not lanes:
        return []
    geoms = [index.geometry(z.zone_id) for z in lanes]

    out: list[str] = []
    for tr in tracks:
        pts = tr.points
        if hasattr(pts, "quality"):
            pts = pts[pts.quality != "outlier"]
        if len(pts) < 3:
            continue
        lat = pts["lat"].to_numpy(dtype=float)
        lon = pts["lon"].to_numpy(dtype=float)
        sog = (pts["sog_kn"].to_numpy(dtype=float)
               if "sog_kn" in pts.columns else np.zeros(len(pts)))
        ts = pd.to_datetime(pts["ts"], utc=True).map(
            lambda x: x.timestamp()).to_numpy(dtype=float)

        run_start: Optional[int] = None
        best: Optional[tuple] = None
        for i in range(len(lat)):
            if sog[i] < min_sog_kn:
                run_start = None
                continue
            d_km = min(distance_to_m(g, float(lat[i]), float(lon[i]))
                       for g in geoms) / 1000.0
            if d_km < min_km:
                run_start = None
                continue
            if run_start is None:
                run_start = i
            dur_min = (ts[i] - ts[run_start]) / 60.0
            if dur_min >= min_minutes:
                mid = (run_start + i) // 2
                if best is None or dur_min > best[0]:
                    best = (dur_min, run_start, i, mid, d_km)
        if best is None:
            continue
        dur_min, i0, i1, mid, d_km = best
        t = float(ts[i0])
        vid = track_subject_id(store, tr, at=t)
        score = min(1.0, 0.5 + 0.15 * min(d_km / min_km, 2.0)
                    + 0.1 * min(dur_min / (2 * min_minutes), 1.0))
        ev = [dict(edge="deviated-from-lane", src=vid,
                   dst=lanes[0].zone_id if len(lanes) == 1 else "lane:any",
                   confidence=round(score, 3), source="zone_layer",
                   source_ref=source_ref,
                   props=dict(distance_km=round(d_km, 1),
                              duration_min=round(dur_min, 1),
                              sensor=getattr(getattr(tr, "source", None),
                                             "name", "ais"),
                              note=("corridors are customary routes, not IMO "
                                    "routeing measures")))]
        _emit(out, store, "lane_deviation", vid, t, score, ev,
              props=dict(distance_km=round(d_km, 1),
                         duration_min=round(dur_min, 1),
                         lat=float(lat[mid]), lon=float(lon[mid]),
                         track_id=getattr(tr, "track_id", None)))
    return out


# --------------------------------------------------------------------------
# 4. anchored outside port limits
# --------------------------------------------------------------------------

def detect_anchored_outside_port_limits(store, tracks: Sequence,
                                        index: ZoneIndex, *, source_ref: str,
                                        min_hours: float = OUTSIDE_LIMITS_MIN_HOURS,
                                        max_sog_kn: float = OUTSIDE_LIMITS_MAX_SOG_KN
                                        ) -> list[str]:
    """Stopped for hours inside territorial waters and outside every facility.

    The shape of a vessel doing something it should have declared: lying
    stopped in a state's territorial sea, not at a berth, not in a designated
    anchorage, not on a terminal. Ship-to-ship transfers, waiting to be told
    where to go, and smuggling all look like this from a track.

    **The three exclusions are what make it usable rather than a list of every
    ship waiting for a tide.** Without the anchorage layer the rule fires on
    every vessel queueing at Kandla — the same defect the loitering rule hit
    (STATE.md, 2026-08-01), reached from a different direction, which is the
    argument for these areas being *data* rather than four constants.

    Radar-sourced tracks are eligible: this rule needs a position history and a
    speed, not an identity, so a contact nobody can name that sits stopped
    inside territorial waters for six hours is exactly as interesting.
    """
    from ..anomaly.library import _emit
    from ..graph.identity import track_subject_id

    ts_zones = index.of_kind("territorial_sea")
    if not ts_zones:
        # Idle, not clean. `anchoring_analysis_status` is the sentence the
        # caller is expected to print; returning [] silently here is the exact
        # failure mode this codebase keeps rediscovering.
        return []
    ts_geoms = [index.geometry(z.zone_id) for z in ts_zones]
    exempt = ("port_limit", "anchorage", "oil_terminal")

    out: list[str] = []
    for tr in tracks:
        pts = tr.points
        if hasattr(pts, "quality"):
            pts = pts[pts.quality != "outlier"]
        if len(pts) < 3:
            continue
        lat = pts["lat"].to_numpy(dtype=float)
        lon = pts["lon"].to_numpy(dtype=float)
        sog = (pts["sog_kn"].to_numpy(dtype=float)
               if "sog_kn" in pts.columns else np.zeros(len(pts)))
        ts = pd.to_datetime(pts["ts"], utc=True).map(
            lambda x: x.timestamp()).to_numpy(dtype=float)

        run_start: Optional[int] = None
        best: Optional[tuple] = None
        for i in range(len(lat)):
            la, lo = float(lat[i]), float(lon[i])
            stopped = sog[i] <= max_sog_kn
            if not stopped:
                run_start = None
                continue
            if index.zones_at(la, lo, exempt):
                run_start = None
                continue
            if not any(_covers(g, la, lo) for g in ts_geoms):
                run_start = None
                continue
            if run_start is None:
                run_start = i
            dur_h = (ts[i] - ts[run_start]) / 3600.0
            if dur_h >= min_hours and (best is None or dur_h > best[0]):
                best = (dur_h, run_start, i)
        if best is None:
            continue
        dur_h, i0, i1 = best
        mid = (i0 + i1) // 2
        t = float(ts[i0])
        vid = track_subject_id(store, tr, at=t)
        near = _nearest_facility(index, float(lat[mid]), float(lon[mid]))
        score = min(1.0, 0.55 + 0.1 * min(dur_h / min_hours, 2.0)
                    + (0.1 if near and near[1] < 40.0 else 0.0))
        ev = [dict(edge="anchored-outside-limits", src=vid,
                   dst=ts_zones[0].zone_id,
                   confidence=round(score, 3), source="zone_layer",
                   source_ref=source_ref,
                   props=dict(hours=round(dur_h, 1),
                              nearest_facility=(near[0] if near else None),
                              nearest_km=(round(near[1], 1) if near else None),
                              sensor=getattr(getattr(tr, "source", None),
                                             "name", "ais")))]
        _emit(out, store, "anchored_outside_limits", vid, t, score, ev,
              props=dict(hours=round(dur_h, 1),
                         lat=float(lat[mid]), lon=float(lon[mid]),
                         nearest_facility=(near[0] if near else None),
                         track_id=getattr(tr, "track_id", None)))
    return out


def _covers(geom, lat: float, lon: float) -> bool:
    from .geometry import contains
    return contains(geom, lat, lon)


def _nearest_facility(index: ZoneIndex, lat: float, lon: float):
    """Which facility she is lying off, and how far. Evidence, not a test."""
    best = None
    for kind in ("port_limit", "anchorage", "oil_terminal"):
        for z in index.of_kind(kind):
            g = index.geometry(z.zone_id)
            la, lo = g.centroid.y, g.centroid.x
            d = haversine_m(lat, lon, la, lo) / 1000.0
            if best is None or d < best[1]:
                best = (z.facility or z.name, d)
    return best
