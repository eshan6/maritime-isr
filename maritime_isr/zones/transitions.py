"""Zone entry and exit as first-class events.

**The requirement is not "was she inside" but "when did she come in, from
where, and where did she leave to".** That distinction decides the shape of
this module: a transition is an interval with two boundary crossings on it, not
a flag on a position, and the crossings carry bearings so the direction of
travel across the boundary survives into the graph and into the rules.

Three things here are easy to get wrong and are handled explicitly:

* **A track that starts inside a zone was not seen entering it.** Reporting the
  first fix as the entry point would put a boundary crossing in the middle of
  the EEZ and let a rule conclude a vessel "entered from" open water when in
  fact we simply started watching. `entry_censored` says so, and anything
  reasoning about direction must check it.
* **A crossing is between two fixes, not at one.** The entry position is
  interpolated onto the boundary between the last outside fix and the first
  inside one, so a vessel reporting every ten minutes at fifteen knots is not
  recorded as entering four kilometres inside the line.
* **Brief re-entries are not two visits.** A vessel weaving along a boundary
  produces a burst of crossings that mean nothing; consecutive visits separated
  by less than `MIN_GAP_MIN` outside are merged, and the merge is recorded in
  the fix count rather than hidden.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from .geometry import bearing_deg, contains
from .model import ZONE_TRANSITION
from .store import ZoneIndex

__all__ = ["ZONE_TRANSITION_TABLE", "transitions_for_track",
           "transitions_for_tracks", "land_transitions", "MIN_GAP_MIN",
           "MIN_DWELL_MIN"]

ZONE_TRANSITION_TABLE = "zone_transition"

#: Two visits to the same zone closer together than this are one visit. A
#: vessel steaming along a boundary crosses it repeatedly on position noise
#: alone; at AIS accuracy that is a few hundred metres of wobble against a
#: boundary we know to a few kilometres, so splitting on it would manufacture
#: events rather than record them.
MIN_GAP_MIN = 30.0

#: A visit shorter than this is not reported. Same reasoning from the other
#: side: clipping a corner of a zone for four minutes is not a visit anybody
#: needs an event for, and the four large statutory zones would otherwise emit
#: one every time a track's noise crossed a line.
MIN_DWELL_MIN = 15.0


def transitions_for_track(track, index: ZoneIndex, *,
                          kinds: Optional[Iterable[str]] = None,
                          min_dwell_min: float = MIN_DWELL_MIN,
                          min_gap_min: float = MIN_GAP_MIN) -> list[dict]:
    """Every zone this track entered and left, as interval rows.

    Walks the track once and asks the index which zones each fix is in — one
    cell lookup plus a containment test on a handful of candidates. The cost is
    linear in fixes rather than in fixes × zones, which matters: the corpus is
    two hundred thousand positions and the layer is a few hundred zones.
    """
    pts = track.points
    if hasattr(pts, "quality"):
        pts = pts[pts.quality != "outlier"]
    if len(pts) < 2:
        return []

    lat = pts["lat"].to_numpy(dtype=float)
    lon = pts["lon"].to_numpy(dtype=float)
    ts = pd.to_datetime(pts["ts"], utc=True)
    epochs = ts.map(lambda x: x.timestamp()).to_numpy(dtype=float)
    sog = (pts["sog_kn"].to_numpy(dtype=float) if "sog_kn" in pts.columns
           else np.zeros(len(pts)))

    member: list[frozenset[str]] = [
        index.ids_at(float(la), float(lo), kinds)
        for la, lo in zip(lat, lon)]

    #: zone_id -> list of open/closed visits, each a dict under construction
    visits: dict[str, list[dict]] = {}
    open_now: dict[str, dict] = {}

    for i in range(len(lat)):
        here = member[i]

        for zid in here - set(open_now):
            v = _open_visit(index, zid, i, lat, lon, epochs)
            # Re-entry inside the merge window continues the previous visit
            # rather than starting a new one: a vessel steaming along a
            # boundary crosses it repeatedly on position noise alone, and
            # splitting on that would manufacture events rather than record any.
            prior = visits.get(zid, [])
            if (prior and prior[-1].get("t_exit") is not None
                    and (v["t_enter"] - prior[-1]["t_exit"])
                    < min_gap_min * 60.0):
                resumed = prior.pop()
                resumed["t_exit"] = None
                resumed["exit_censored"] = False
                open_now[zid] = resumed
            else:
                open_now[zid] = v
                visits.setdefault(zid, [])

        for zid in list(open_now):
            if zid in here:
                v = open_now[zid]
                v["n_fixes"] += 1
                v["sog"].append(float(sog[i]))
                v["last_i"] = i
            else:
                v = open_now.pop(zid)
                _close_visit(index, zid, v, i, lat, lon, epochs)
                visits.setdefault(zid, []).append(v)

    for zid, v in open_now.items():
        # Still inside at the last fix: the exit is censored, not absent.
        v["t_exit"] = None
        v["exit_censored"] = True
        v["exit_lat"] = v["exit_lon"] = v["exit_bearing_deg"] = None
        visits.setdefault(zid, []).append(v)

    rows: list[dict] = []
    for zid, vs in visits.items():
        z = index.get(zid)
        if z is None:
            continue
        for v in vs:
            t_end = v["t_exit"] if v["t_exit"] is not None else epochs[v["last_i"]]
            dwell = (t_end - v["t_enter"]) / 60.0
            if dwell < min_dwell_min:
                continue
            rows.append(_row(track, z, v, dwell))
    rows.sort(key=lambda r: (r["t_enter"], r["zone_id"]))
    return rows


def _open_visit(index: ZoneIndex, zid: str, i: int, lat, lon, epochs) -> dict:
    """Start a visit, interpolating the crossing onto the boundary."""
    if i == 0:
        return dict(t_enter=float(epochs[0]), entry_lat=float(lat[0]),
                    entry_lon=float(lon[0]), entry_bearing_deg=None,
                    entry_censored=True, t_exit=None, exit_censored=False,
                    exit_lat=None, exit_lon=None, exit_bearing_deg=None,
                    n_fixes=1, sog=[], last_i=i)
    cla, clo, ct = _crossing(index, zid, lat[i - 1], lon[i - 1], epochs[i - 1],
                             lat[i], lon[i], epochs[i], want_inside=True)
    return dict(t_enter=ct, entry_lat=cla, entry_lon=clo,
                entry_bearing_deg=bearing_deg(lat[i - 1], lon[i - 1],
                                              lat[i], lon[i]),
                entry_censored=False, t_exit=None, exit_censored=False,
                exit_lat=None, exit_lon=None, exit_bearing_deg=None,
                n_fixes=1, sog=[], last_i=i)


def _close_visit(index: ZoneIndex, zid: str, v: dict, i: int, lat, lon,
                 epochs) -> None:
    cla, clo, ct = _crossing(index, zid, lat[i - 1], lon[i - 1], epochs[i - 1],
                             lat[i], lon[i], epochs[i], want_inside=False)
    v["t_exit"] = ct
    v["exit_lat"], v["exit_lon"] = cla, clo
    v["exit_bearing_deg"] = bearing_deg(lat[i - 1], lon[i - 1], lat[i], lon[i])
    v["exit_censored"] = False


def _crossing(index: ZoneIndex, zid: str, la0, lo0, t0, la1, lo1, t1, *,
              want_inside: bool, iters: int = 12):
    """Where between two fixes the boundary was crossed.

    Bisection on the straight segment between them. Twelve halvings of a
    ten-minute leg locate the crossing to under a second and to a few metres —
    far inside the accuracy of the boundary itself, which is the point: the
    interpolation exists so the recorded crossing is on the line rather than at
    whichever fix happened to be first inside, not to claim metre precision.
    """
    geom = index.geometry(zid)
    lo_f, hi_f = 0.0, 1.0
    for _ in range(iters):
        mid = 0.5 * (lo_f + hi_f)
        mla = la0 + (la1 - la0) * mid
        mlo = lo0 + (lo1 - lo0) * mid
        inside = contains(geom, mla, mlo)
        if inside == want_inside:
            hi_f = mid
        else:
            lo_f = mid
    f = hi_f
    return (float(la0 + (la1 - la0) * f), float(lo0 + (lo1 - lo0) * f),
            float(t0 + (t1 - t0) * f))


def _row(track, zone, v: dict, dwell_min: float) -> dict:
    sogs = v["sog"] or [0.0]
    tid = getattr(track, "track_id", "")
    raw = f"{tid}|{zone.zone_id}|{v['t_enter']:.0f}"
    return dict(
        transition_id="ztr_" + hashlib.sha1(raw.encode()).hexdigest()[:12],
        zone_id=zone.zone_id, zone_kind=zone.kind, zone_name=zone.name,
        track_id=tid,
        track_key=getattr(track, "track_key", None),
        track_source=getattr(getattr(track, "source", None), "name", "ais"),
        mmsi=(int(track.mmsi) if getattr(track, "mmsi", None) is not None
              else None),
        t_enter=pd.Timestamp(v["t_enter"], unit="s", tz="UTC"),
        t_exit=(pd.Timestamp(v["t_exit"], unit="s", tz="UTC")
                if v["t_exit"] is not None else None),
        dwell_min=round(dwell_min, 2),
        entry_lat=_r(v["entry_lat"]), entry_lon=_r(v["entry_lon"]),
        entry_bearing_deg=_r(v["entry_bearing_deg"], 1),
        exit_lat=_r(v["exit_lat"]), exit_lon=_r(v["exit_lon"]),
        exit_bearing_deg=_r(v["exit_bearing_deg"], 1),
        min_sog_kn=round(float(min(sogs)), 2),
        mean_sog_kn=round(float(sum(sogs) / len(sogs)), 2),
        n_fixes=int(v["n_fixes"]),
        entry_censored=bool(v["entry_censored"]),
        exit_censored=bool(v["exit_censored"]),
        lat=v["entry_lat"], lon=v["entry_lon"],
    )


def _r(x, nd: int = 5):
    return None if x is None else round(float(x), nd)


def transitions_for_tracks(tracks: Sequence, index: ZoneIndex, **kw
                           ) -> list[dict]:
    out: list[dict] = []
    for tr in tracks:
        out.extend(transitions_for_track(tr, index, **kw))
    return out


def land_transitions(rows: Sequence[dict], *, source_id: str,
                     is_synthetic: bool,
                     source_ref: str = "zone-transitions") -> dict[str, int]:
    """Land transitions through the shared layer, like any other event."""
    from ..ingest.landing import land_table, stamp_envelope, stamp_h3
    if not rows:
        return {}
    out = []
    for r in rows:
        row = dict(r)
        stamp_envelope(row, source_id=source_id, source_ref=source_ref,
                       acquired_at=pd.Timestamp(row["t_enter"]).to_pydatetime(),
                       confidence=None, is_synthetic=is_synthetic)
        row = stamp_h3(row)
        row.pop("lat", None)
        row.pop("lon", None)
        out.append(row)
    w = land_table(out, table=ZONE_TRANSITION_TABLE,
                   key_fields=("transition_id",), day_field="t_enter")
    return {ZONE_TRANSITION_TABLE: sum(w.values())}
