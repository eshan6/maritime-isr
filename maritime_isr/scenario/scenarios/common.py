"""Helpers every scenario module shares.

Layer 2's job is composition and meaning: pick primitives, place them in time and
space, and write the truth row. These helpers keep that job free of bookkeeping —
event ids, emission, event-row construction — so a scenario module reads as a
description of what happens rather than as plumbing.

**Event rows are built here, in the shape the GFW connectors land.** The fusion
core and the graph populator consume landed behaviour events; that is the code
path real data takes, so it is the code path scenario data must take too. A
scenario that only produced AIS positions would exercise the track engine and
stop short of the graph, which is where the interesting failure modes are.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from ..cast import entity_id
from ..geography import haversine_m, receiver_coverage
from ..primitives.ais import Suppression, emit_ais
from ..primitives.gap import GapSpec, implied_speed_across_gap
from ..primitives.encounter import RendezvousSpec
from ..primitives.port_call import PortCallSpec
from ..primitives.track import TrackPoint
from ..world import LandedEvent, ScenarioWorld


def eid(*parts) -> str:
    """Deterministic event id. Same seed, same run, same ids."""
    return "syn_" + hashlib.sha1("|".join(str(p) for p in parts).encode()
                                 ).hexdigest()[:16]


def V(world: ScenarioWorld, key: str):
    return world.vessel(entity_id(key))


def emit(world: ScenarioWorld, key: str, points: list[TrackPoint], *,
         suppressions: list[Suppression] | None = None,
         coverage_override: float | None = None,
         force_coverage_floor: float = 0.0) -> None:
    """Integrate-to-emit for one vessel, registering both truth and reports.

    A vessel whose AIS is legitimately off (the naval decoy) still gets its
    integrated track recorded — it moved, we just did not hear it. Keeping the
    motion in truth while emitting nothing is what lets the corpus contain a
    vessel that is genuinely invisible without pretending it was not there.
    """
    v = V(world, key)
    world.add_track(v.entity_id, points)
    if not v.ais_expected:
        return
    reports = emit_ais(v, points, world.rng, suppressions=suppressions,
                       coverage_override=coverage_override,
                       force_coverage_floor=force_coverage_floor)
    world.add_ais(v.entity_id, reports)


def add_encounter(world: ScenarioWorld, scenario_id: str, a_key: str,
                  b_key: str, spec: RendezvousSpec, *,
                  encounter_type: str = "transfer") -> LandedEvent:
    """A met-with event, in GFW's landed shape.

    `encounter_type` is descriptive only and carries no verdict — GFW's own
    encounter rows say what kind of meeting the geometry looked like, not
    whether it was licit. Landing a verdict here would hand the answer to the
    detector.
    """
    a, b = V(world, a_key), V(world, b_key)
    return world.add_event(LandedEvent(
        kind="encounters",
        event_id=eid(scenario_id, "enc", a.entity_id, b.entity_id,
                     spec.t_start.isoformat()),
        entity_id=a.entity_id, counterpart_entity_id=b.entity_id,
        t_start=spec.t_start, t_end=spec.t_end,
        lat=spec.lat, lon=spec.lon,
        props=dict(
            duration_hours=round(spec.duration_h, 3),
            encounter_type=encounter_type,
            median_speed_knots=round(spec.mean_sog_kn, 2),
            min_separation_m=round(spec.min_separation_m, 1),
            mean_separation_m=round(spec.mean_separation_m, 1),
            start_distance_from_shore_km=round(
                _shore_km(spec.lat, spec.lon), 1),
            start_distance_from_port_km=round(
                _port_km(spec.lat, spec.lon), 1),
        )))


def add_loiter(world: ScenarioWorld, scenario_id: str, key: str,
               t_start: datetime, t_end: datetime, lat: float, lon: float, *,
               mean_sog_kn: float = 0.7) -> LandedEvent:
    v = V(world, key)
    return world.add_event(LandedEvent(
        kind="loitering",
        event_id=eid(scenario_id, "loi", v.entity_id, t_start.isoformat()),
        entity_id=v.entity_id, t_start=t_start, t_end=t_end,
        lat=lat, lon=lon,
        props=dict(
            duration_hours=round((t_end - t_start).total_seconds() / 3600.0, 3),
            median_speed_knots=round(mean_sog_kn, 2),
            start_distance_from_shore_km=round(_shore_km(lat, lon), 1),
            start_distance_from_port_km=round(_port_km(lat, lon), 1),
        )))


def add_port_visit(world: ScenarioWorld, scenario_id: str, key: str,
                   spec: PortCallSpec) -> LandedEvent:
    v = V(world, key)
    return world.add_event(LandedEvent(
        kind="port_visits",
        event_id=eid(scenario_id, "pv", v.entity_id, spec.t_arrive.isoformat()),
        entity_id=v.entity_id, t_start=spec.t_arrive, t_end=spec.t_depart,
        lat=spec.lat, lon=spec.lon,
        props=dict(
            duration_hours=round(spec.duration_h, 3),
            port_id=f"anch:{spec.port.lower()}",
            port_name=spec.port,
            anchorage_hours=round(spec.anchorage_hours, 2),
            berth_hours=round(spec.berth_hours, 2),
            start_distance_from_shore_km=round(_shore_km(spec.lat, spec.lon), 1),
            start_distance_from_port_km=0.0,
        )))


def add_gap_event(world: ScenarioWorld, scenario_id: str, key: str,
                  gap: GapSpec) -> LandedEvent:
    """An AIS gap, landed the way GFW lands theirs — including their verdict field.

    **`gfw_intentional_disabling` is left None on every synthetic gap.** GFW did
    not assess these, and inventing their verdict would be putting words in
    another organisation's mouth in a column an analyst reads as theirs. It
    would also hand the answer to any detector that consulted it. Our own
    verdict is the thing being measured, so the column stays empty and the
    system has to reach one.

    ⚠ **The second half of that justification is now refuted, and this is a
    known separability hole — see STATE.md OPEN QUESTION #9.** This docstring
    used to add: "the real corpus has exactly zero gaps flagged intentional, so
    the combined column stays honest." That was measured against a null column
    caused by a mapper bug. The host run on 2026-07-31 (ADR-020 work) found
    **5 of 5 real gaps flagged `intentionalDisabling=true`**. So on a combined
    corpus the real gaps are 100% flagged and the synthetic ones 100% null,
    which makes `gfw_intentional_disabling IS NULL` a single-filter synthetic-row
    detector — the exact defect class `scenario/nulls.py` exists to close
    (ADR-019).

    It is **left as-is deliberately** rather than fixed in passing: simulating a
    third party's assessment is a decision about what the scenario corpus is
    allowed to claim, not a bug fix, and CLAUDE.md §9 says to ask rather than
    invent. The consequence to know meanwhile is that the findings screen's
    dark-gap section is empty on any sandbox corpus and populated only where the
    real rows live.
    """
    v = V(world, key)
    return world.add_event(LandedEvent(
        kind="gaps",
        event_id=eid(scenario_id, "gap", v.entity_id, gap.t0.isoformat()),
        entity_id=v.entity_id, t_start=gap.t0, t_end=gap.t1,
        lat=gap.lat_off, lon=gap.lon_off,
        props=dict(
            duration_hours=round(gap.duration_h, 3),
            gap_duration_hours=round(gap.duration_h, 3),
            gap_off_lat=gap.lat_off, gap_off_lon=gap.lon_off,
            gap_on_lat=gap.lat_on, gap_on_lon=gap.lon_on,
            gap_distance_km=round(haversine_m(gap.lat_off, gap.lon_off,
                                              gap.lat_on, gap.lon_on) / 1000.0, 2),
            gap_implied_speed_kn=round(implied_speed_across_gap(gap), 2),
            gfw_intentional_disabling=None,
            # Our own reception estimate at the off-position, which is what an
            # honest gap classifier needs and what makes the offshore
            # deliberate miss explicable rather than merely absent.
            reception_at_off=round(gap.coverage_at_off, 3),
            start_distance_from_shore_km=round(
                _shore_km(gap.lat_off, gap.lon_off), 1),
        )))


def _shore_km(lat: float, lon: float) -> float:
    """Rough distance to the nearest modelled coastal reference, kilometres.

    Approximated from the receiver-site list, which sits on the coast. This is a
    scenario-side convenience for populating the distance columns the real
    events carry; it is not a coastline model and nothing downstream treats it
    as one.
    """
    from ..geography import RECEIVER_SITES
    return min(haversine_m(lat, lon, s[0], s[1]) for s in RECEIVER_SITES) / 1000.0


def _port_km(lat: float, lon: float) -> float:
    from ..geography import PORTS
    return min(haversine_m(lat, lon, p[0], p[1])
               for p in PORTS.values()) / 1000.0


def coverage_at(lat: float, lon: float) -> float:
    return receiver_coverage(lat, lon)


def schedule_after(world: ScenarioWorld, keys: list[str], planned: datetime, *,
                   buffer_h: float = 6.0) -> datetime:
    """The earliest time at or after `planned` when every named vessel is free.

    Vessels are shared across scenarios deliberately — that is what gives the
    graph structure worth traversing — but a shared vessel has a shared
    calendar, and two scenarios that both move her on the same afternoon
    produce a hull that teleports between them. `world.add_track` refuses that
    outright; this is how a scenario avoids asking for it.

    Deferring a scenario by a few hours costs nothing here: what these
    scenarios assert is a *pattern* — a repeated position, a crossing that never
    slows — not a specific date. Hard-coding times that happen to miss today's
    catalogue would work until the next scenario borrowed the same hull.
    """
    t = planned
    for key in keys:
        busy_until = world.free_from(V(world, key).entity_id)
        if busy_until is not None:
            t = max(t, busy_until + timedelta(hours=buffer_h))
    return t


def schedule_arrival(world: ScenarioWorld, key: str,
                     start_pos: tuple[float, float], planned: datetime, *,
                     buffer_h: float = 4.0) -> datetime:
    """Earliest start for a leg beginning at `start_pos`, allowing for passage.

    `schedule_after` only guarantees the vessel is not doing something else.
    This also gives her time to *sail there* from wherever the previous
    scenario left her, at 85% of service speed plus a buffer — because a
    scenario that begins at a fixed position is otherwise asking her to
    teleport, and `world.add_track` now refuses that outright.
    """
    v = V(world, key)
    busy_until = world.free_from(v.entity_id)
    if busy_until is None:
        return planned
    track = world.track_of(v.entity_id)
    if not track:
        return max(planned, busy_until + timedelta(hours=buffer_h))
    last = track[-1]
    dist_nm = haversine_m(last.lat, last.lon, *start_pos) / 1852.0
    travel_h = dist_nm / max(v.service_kn * 0.85, 1.0) + buffer_h
    return max(planned, busy_until + timedelta(hours=travel_h))


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def days(n: float) -> timedelta:
    return timedelta(days=n)
