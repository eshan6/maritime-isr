"""The question a zone has to be able to answer.

    "Draw a box anywhere. I'll tell you who was in it."

That sentence is the whole point of making the layer queryable rather than
drawable, and it is one function: `who_was_inside(zone, t0, t1)`. Everything
else here supports it.

**It answers from landed transitions when they exist and from tracks when they
do not**, and it says which. That distinction is not pedantry: transitions are
precomputed over the standing zones and a box the operator drew ninety seconds
ago has none, so the same question has to be answerable two ways or the drawn
box would be a second-class object — which the requirement explicitly forbids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import pandas as pd

from .model import Zone
from .store import ZoneIndex
from .transitions import transitions_for_track

__all__ = ["ZoneQuery", "who_was_inside", "ZoneVisit"]


@dataclass
class ZoneVisit:
    """One vessel's presence in one zone, in an answer to one question."""
    track_id: str
    track_key: Optional[str]
    track_source: str
    mmsi: Optional[int]
    t_enter: pd.Timestamp
    t_exit: Optional[pd.Timestamp]
    dwell_min: float
    entry_lat: Optional[float]
    entry_lon: Optional[float]
    entry_bearing_deg: Optional[float]
    exit_lat: Optional[float]
    exit_lon: Optional[float]
    exit_bearing_deg: Optional[float]
    entry_censored: bool
    exit_censored: bool
    min_sog_kn: float
    mean_sog_kn: float
    n_fixes: int

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["t_enter"] = _iso(self.t_enter)
        d["t_exit"] = _iso(self.t_exit)
        return d


@dataclass
class ZoneQuery:
    """The answer, with its own provenance attached.

    `basis` is `landed` or `computed`, and it is returned to the caller rather
    than kept private, because "nobody was in your box" means something quite
    different when the answer came from a table that may not have been rebuilt
    since the zone was drawn.
    """
    zone: Zone
    window_start: Optional[pd.Timestamp]
    window_end: Optional[pd.Timestamp]
    basis: str
    visits: list[ZoneVisit] = field(default_factory=list)
    note: str = ""

    @property
    def n_vessels(self) -> int:
        keys = {v.mmsi if v.mmsi is not None else v.track_key or v.track_id
                for v in self.visits}
        return len(keys)


def who_was_inside(zone: Zone, tracks: Sequence, *,
                   start=None, end=None,
                   index: Optional[ZoneIndex] = None,
                   landed: Optional[Iterable[dict]] = None) -> ZoneQuery:
    """Who was in this zone between two instants, and how they came and went.

    `landed` is an optional iterable of already-computed `zone_transition`
    rows; when supplied and non-empty for this zone it is used, because it is
    cheaper and because it is what the rest of the system reasoned over. When
    it is absent — the operator's freshly drawn box — the transitions are
    computed here from the tracks, and the answer says so.

    The window test is **overlap, not containment**. A vessel that entered on
    Monday and left on Friday was in the box on Tuesday, and a query for
    Tuesday that missed her would be wrong in the way that matters most.
    """
    t0, t1 = _as_utc(start), _as_utc(end)

    rows: list[dict]
    basis: str
    landed_rows = [r for r in (landed or []) if r.get("zone_id") == zone.zone_id]
    if landed_rows:
        rows, basis = landed_rows, "landed"
    else:
        idx = index or ZoneIndex([zone])
        rows = []
        for tr in tracks:
            rows.extend(transitions_for_track(tr, idx, kinds=[zone.kind]))
        rows = [r for r in rows if r.get("zone_id") == zone.zone_id]
        basis = "computed"

    visits: list[ZoneVisit] = []
    for r in rows:
        enter = pd.Timestamp(r["t_enter"])
        exit_ = pd.Timestamp(r["t_exit"]) if r.get("t_exit") is not None else None
        # Still inside counts as inside for ever after, which is why the
        # open-ended case tests only the lower bound.
        if t1 is not None and enter > t1:
            continue
        if t0 is not None and exit_ is not None and exit_ < t0:
            continue
        visits.append(ZoneVisit(
            track_id=str(r.get("track_id") or ""),
            track_key=(str(r["track_key"]) if r.get("track_key") else None),
            track_source=str(r.get("track_source") or "ais"),
            mmsi=(int(r["mmsi"]) if r.get("mmsi") is not None else None),
            t_enter=enter, t_exit=exit_,
            dwell_min=float(r.get("dwell_min") or 0.0),
            entry_lat=_f(r.get("entry_lat")), entry_lon=_f(r.get("entry_lon")),
            entry_bearing_deg=_f(r.get("entry_bearing_deg")),
            exit_lat=_f(r.get("exit_lat")), exit_lon=_f(r.get("exit_lon")),
            exit_bearing_deg=_f(r.get("exit_bearing_deg")),
            entry_censored=bool(r.get("entry_censored")),
            exit_censored=bool(r.get("exit_censored")),
            min_sog_kn=float(r.get("min_sog_kn") or 0.0),
            mean_sog_kn=float(r.get("mean_sog_kn") or 0.0),
            n_fixes=int(r.get("n_fixes") or 0)))
    visits.sort(key=lambda v: v.t_enter)

    censored = sum(1 for v in visits if v.entry_censored)
    note = ""
    if censored:
        note = (f"{censored} of {len(visits)} were already inside when the "
                f"track began — their entry position is where we picked them "
                f"up, not where they crossed.")
    return ZoneQuery(zone=zone, window_start=t0, window_end=t1, basis=basis,
                     visits=visits, note=note)


def _as_utc(v):
    """A UTC timestamp from whatever the caller passed — see the twin of this
    in `analyses.py`. `pd.Timestamp(x, tz="UTC")` raises on an already-aware
    value, which is the obvious thing for a caller to pass."""
    if v is None:
        return None
    t = pd.Timestamp(v)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _f(v):
    return None if v is None else float(v)


def _iso(t):
    return None if t is None else pd.Timestamp(t).isoformat()
