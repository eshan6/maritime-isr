"""D1 — Global Fishing Watch Events connector (encounters, loitering, port
visits, AIS gaps).

**What these events are, in plain English:**

- **Encounter** — two vessels close together and moving slowly for a sustained
  period. That is what a transfer at sea looks like from orbit. Not proof of
  anything by itself; a strong prompt to look.
- **Loitering** — one vessel going slowly in open water for a sustained period,
  away from port. Waiting for someone, or working.
- **Port visit** — a vessel entering and later leaving a port's area.
- **AIS gap** — the vessel's position broadcast stopped for a while and later
  resumed. This is the closest free signal we have to "went dark."

**The honesty constraint on gaps (CLAUDE.md §6).** A gap is *not* by itself
evidence of intentional silence. If nobody had a receiver listening where the
ship was, the silence is ours, not the ship's. GFW computes these events with
their own coverage model and attaches their own confidence; we carry that
confidence through as `confidence` on the provenance envelope and never
re-assert a gap as our own dark-vessel finding. Turning a gap into a dark-vessel
claim is a Phase 3 fusion decision made against demonstrated coverage, not an
ingest decision.

These land as **connector outputs in canonical form**. No fusion or graph code
is modified — a later session adapts them onto the fusion schemas.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..config import AOI_V1
from . import gfw_client as gc
from .checks import landed, report_landed
from .landing import land_raw_json, land_table, stamp_envelope, stamp_h3

SOURCE_ID = "gfw-events"

# Dataset ids and the event-type filter each one takes. Read from the official
# client's EventDataset / EventType enums — see DATA_SOURCES.md.
EVENT_SPECS: dict[str, dict] = {
    "encounters": {
        "dataset": "public-global-encounters-events:latest",
        "types": ["ENCOUNTER"],
        "table": "gfw_encounters",
    },
    "loitering": {
        "dataset": "public-global-loitering-events:latest",
        "types": ["LOITERING"],
        "table": "gfw_loitering",
    },
    "port_visits": {
        "dataset": "public-global-port-visits-events:latest",
        "types": ["PORT_VISIT"],
        "table": "gfw_port_visits",
    },
    "gaps": {
        "dataset": "public-global-gaps-events:latest",
        "types": ["GAP"],
        "table": "gfw_ais_gaps",
    },
}

# GFW confidence levels are "2"/"3"/"4"; map to a 0-1 confidence for the envelope.
_CONFIDENCE_MAP = {"2": 0.3, "3": 0.6, "4": 0.9, 2: 0.3, 3: 0.6, 4: 0.9}


def _parse_ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _position(ev: dict) -> tuple[float | None, float | None]:
    """Pull a lat/lon out of an event, whichever shape GFW used for it."""
    pos = ev.get("position") or {}
    lat = pos.get("lat", ev.get("lat"))
    lon = pos.get("lon", ev.get("lon"))
    if lat is None or lon is None:
        start = ev.get("start") or {}
        if isinstance(start, dict):
            lat, lon = start.get("lat", lat), start.get("lon", lon)
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return (None, None)


def _primary_vessel(ev: dict) -> dict:
    v = ev.get("vessel") or {}
    if not v:
        vessels = ev.get("vessels") or []
        if vessels and isinstance(vessels[0], dict):
            v = vessels[0]
    return v if isinstance(v, dict) else {}


def _counterpart_vessel(ev: dict) -> dict:
    """For an encounter, the *other* ship. This is the whole point of an encounter."""
    vessels = ev.get("vessels") or []
    if isinstance(vessels, list) and len(vessels) > 1 and isinstance(vessels[1], dict):
        return vessels[1]
    enc = ev.get("encounter") or {}
    if isinstance(enc, dict):
        v = enc.get("vessel") or {}
        if isinstance(v, dict):
            return v
    return {}


def _f(v) -> float | None:
    """Coerce to float. GFW returns some numerics as strings, e.g. distanceKm."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _confidence_raw(ev: dict):
    """Find GFW's confidence, which is NOT where the docs imply.

    Measured on live payloads 2026-07-29: encounters, loitering and gaps carry
    **no** event-level `confidence` at all. Only port visits do, nested at
    `port_visit.confidence`. Reading `ev["confidence"]` therefore wrote null on
    ~99% of rows while the envelope looked populated — worse than an obvious
    gap, because it reads as "GFW had no opinion" rather than "we looked in the
    wrong place."
    """
    if ev.get("confidence") is not None:
        return ev["confidence"]
    pv = ev.get("port_visit") or ev.get("portVisit")
    if isinstance(pv, dict) and pv.get("confidence") is not None:
        return pv["confidence"]
    enc = ev.get("encounter")
    if isinstance(enc, dict) and enc.get("confidence") is not None:
        return enc["confidence"]
    return None


def _anchorage_fields(anch, prefix: str) -> dict:
    """Flatten one GFW anchorage record under `prefix`."""
    if not isinstance(anch, dict):
        return {f"{prefix}_{k}": None
                for k in ("id", "name", "flag", "lat", "lon", "at_dock",
                          "distance_from_shore_km")}
    return {
        f"{prefix}_id": anch.get("id"),
        f"{prefix}_name": anch.get("name"),
        f"{prefix}_flag": anch.get("flag"),
        f"{prefix}_lat": _f(anch.get("lat")),
        f"{prefix}_lon": _f(anch.get("lon")),
        f"{prefix}_at_dock": anch.get("atDock"),
        f"{prefix}_distance_from_shore_km": _f(anch.get("distanceFromShoreKm")),
    }


def map_event(ev: dict, kind: str) -> dict | None:
    """Map one GFW event into a canonical row. Returns None if unusable."""
    event_id = ev.get("id") or ev.get("eventId")
    start = _parse_ts(ev.get("start") if not isinstance(ev.get("start"), dict) else None) \
        or _parse_ts(ev.get("startDate")) or _parse_ts((ev.get("start") or {}).get("time")
                                                       if isinstance(ev.get("start"), dict) else None)
    end = _parse_ts(ev.get("end") if not isinstance(ev.get("end"), dict) else None) \
        or _parse_ts(ev.get("endDate")) or _parse_ts((ev.get("end") or {}).get("time")
                                                     if isinstance(ev.get("end"), dict) else None)
    if event_id is None or start is None:
        return None

    lat, lon = _position(ev)
    if lat is not None and not AOI_V1.contains(lat, lon):
        return None  # AOI-scoped: GFW's polygon filter is inclusive at the edges

    vessel = _primary_vessel(ev)
    other = _counterpart_vessel(ev)
    conf_raw = _confidence_raw(ev)
    confidence = _CONFIDENCE_MAP.get(conf_raw)
    dist = ev.get("distances") or {}
    gap = ev.get("gap") if isinstance(ev.get("gap"), dict) else {}
    pv = ev.get("port_visit") or ev.get("portVisit") or {}
    if not isinstance(pv, dict):
        pv = {}

    duration_h = ev.get("durationHours")
    if duration_h is None and start and end:
        duration_h = (end - start).total_seconds() / 3600.0

    row = {
        "event_id": str(event_id),
        "event_kind": kind,
        "event_type": ev.get("type"),
        "start_time": start,
        "end_time": end,
        "duration_hours": float(duration_h) if duration_h is not None else None,
        "lat": lat,
        "lon": lon,
        # primary vessel identity as GFW reported it at event time
        "vessel_id": vessel.get("id") or vessel.get("vesselId"),
        "mmsi": vessel.get("ssvid") or vessel.get("mmsi"),
        "imo": vessel.get("imo"),
        "ship_name": vessel.get("name") or vessel.get("shipname"),
        "flag": vessel.get("flag"),
        "vessel_type": vessel.get("type") or vessel.get("vesselType"),
        # counterpart (encounters only, null elsewhere)
        "counterpart_vessel_id": other.get("id") or other.get("vesselId"),
        "counterpart_mmsi": other.get("ssvid") or other.get("mmsi"),
        "counterpart_name": other.get("name") or other.get("shipname"),
        "counterpart_flag": other.get("flag"),
        "encounter_type": (ev.get("encounter") or {}).get("type") if isinstance(
            ev.get("encounter"), dict) else None,

        # ---- distance context, present on EVERY event type ------------------
        # This is what makes "loitering" interpretable. Slow 2 km off Mumbai is
        # an anchorage queue; slow 400 km offshore is worth looking at. GFW
        # pre-computes both, which removes our dependency on the World Port
        # Index for this purpose entirely (ADR-016).
        "start_distance_from_port_km": _f(dist.get("startDistanceFromPortKm")),
        "end_distance_from_port_km": _f(dist.get("endDistanceFromPortKm")),
        "start_distance_from_shore_km": _f(dist.get("startDistanceFromShoreKm")),
        "end_distance_from_shore_km": _f(dist.get("endDistanceFromShoreKm")),

        # ---- port visits: full anchorage records ----------------------------
        # Each carries lat/lon, name and flag, so 3,000 port visits are also a
        # port gazetteer for the AOI — better targeted than WPI, because these
        # are the anchorages actually in use here.
        "port_visit_id": pv.get("visitId"),
        **_anchorage_fields(pv.get("startAnchorage"), "start_anchorage"),
        **_anchorage_fields(pv.get("intermediateAnchorage"), "anchorage"),
        **_anchorage_fields(pv.get("endAnchorage"), "end_anchorage"),
        # kept for backwards compatibility with rows landed before 2026-07-29
        "port_id": (pv.get("intermediateAnchorage") or {}).get("id")
        if isinstance(pv.get("intermediateAnchorage"), dict) else None,
        "port_name": (pv.get("intermediateAnchorage") or {}).get("name")
        if isinstance(pv.get("intermediateAnchorage"), dict) else None,

        # ---- gaps ----------------------------------------------------------
        "gap_distance_km": _f(gap.get("distanceKm")),
        "gap_implied_speed_kn": _f(gap.get("impliedSpeedKnots")),
        "gap_duration_hours": _f(gap.get("durationHours")),
        # GFW'S OWN dark-vessel judgement. Arguably the single most valuable
        # field in the entire pull, and it was being dropped on the floor.
        # Recorded as GFW's assertion, never re-asserted as ours: per
        # CLAUDE.md §6 a gap outside demonstrated coverage is not evidence of
        # intentional silence, and GFW's coverage model is not ours.
        "gfw_intentional_disabling": gap.get("intentionalDisabling"),
        # GFW's coverage evidence for that judgement — how much they could hear.
        "gap_positions_12h_before_sat": _f(gap.get("positions12HoursBeforeSat")),
        "gap_positions_per_day_sat": _f(gap.get("positionsPerDaySatReception")),
        "gap_off_lat": (gap.get("offPosition") or {}).get("lat")
        if isinstance(gap.get("offPosition"), dict) else None,
        "gap_off_lon": (gap.get("offPosition") or {}).get("lon")
        if isinstance(gap.get("offPosition"), dict) else None,
        "gap_on_lat": (gap.get("onPosition") or {}).get("lat")
        if isinstance(gap.get("onPosition"), dict) else None,
        "gap_on_lon": (gap.get("onPosition") or {}).get("lon")
        if isinstance(gap.get("onPosition"), dict) else None,

        "gfw_confidence_raw": str(conf_raw) if conf_raw is not None else None,
    }

    stamp_h3(row)
    stamp_envelope(
        row,
        source_id=SOURCE_ID,
        source_ref=f"{kind}:{event_id}",
        acquired_at=start,
        confidence=confidence,
    )
    return row


def fetch_kind(kind: str, start: date, end: date) -> list[dict]:
    """Fetch every event of one kind over the AOI and window."""
    spec = EVENT_SPECS[kind]
    body = {
        "datasets": [spec["dataset"]],
        "types": spec["types"],
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "geometry": gc.aoi_geojson(AOI_V1),
    }
    raw = list(gc.post_paginated("events", body))
    land_raw_json(SOURCE_ID, f"{kind}_{start:%Y%m%d}_{end:%Y%m%d}.json", raw)
    return raw


def run(kind: str | None = None, weeks: int = 8) -> int:
    """Download and land GFW events for the AOI.

    `kind` limits to one of encounters/loitering/port_visits/gaps; default is all
    four. `weeks` is how far back to go.
    """
    end = datetime.now(timezone.utc).date() + timedelta(days=1)  # endDate is exclusive
    start = end - timedelta(weeks=weeks)
    kinds = [kind] if kind else list(EVENT_SPECS)

    total = 0
    for k in kinds:
        spec = EVENT_SPECS[k]
        print(f"[gfw-events] {k}: {start} .. {end}  AOI={AOI_V1.name}")
        try:
            raw = fetch_kind(k, start, end)
        except gc.GFWUnavailable as e:
            print(f"[gfw-events] {k}: UNAVAILABLE — {e}")
            continue

        rows, skipped = [], 0
        for ev in raw:
            r = map_event(ev, k)
            if r is None:
                skipped += 1
            else:
                rows.append(r)

        if rows:
            written = land_table(
                rows, table=spec["table"], key_fields=("event_id",), day_field="start_time",
            )
            n = landed(written)
            report_landed(f"gfw-events {k}", spec["table"], written, len(rows))
            total += n
        else:
            print(f"[gfw-events] {k}: no events in window")
        if skipped:
            print(f"[gfw-events] {k}: skipped {skipped} unusable/out-of-AOI records")

    print(f"[gfw-events] done — {total:,} event rows landed")
    return 0
